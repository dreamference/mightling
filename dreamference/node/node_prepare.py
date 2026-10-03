"""
`sudo puffin-admin node prepare`: every root step of a node install, and nothing else
(specs/DREAMFERENCE_PUFFIN_FLEET.md §7.3).

It is the one command a person types once on a new GB10, and the one `node provision` runs there
with `sudo`. It acts for the user who invoked sudo (`SUDO_USER`), reads each setting before it
changes it, prints every command before running it, and changes only what is missing:

- the host settings a model load is refused without (`HostSafetySetup.steps()`, unchanged);
- the user in the `docker` group, which NVIDIA's first-boot wizard does not do;
- lingering, which jobs and Night Shift need;
- the Avahi service file, created empty and owned by the user, so `node enable` (run as the user)
  fills it with no root;
- the AppArmor profile that lets bubblewrap sandbox (`SandboxPrerequisite.fix_commands()`);
- NVIDIA's telemetry service, disabled always (decided 2026-10-03), as Puffin disabled vLLM's
  and Onyx's.

What it never touches: sshd, netplan or NetworkManager, the firewall, users and passwords, APT
sources, kernel parameters other than the host settings' two sysctls, and NVIDIA's services other
than the telemetry one. A step that can lock someone out of a machine is not automated.
"""

import grp
import os
import pwd
import subprocess
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

TELEMETRY_UNIT: Final[str] = "nvidia-dgx-telemetry"
LINGER_DIR: Final[str] = "/var/lib/systemd/linger"
AVAHI_SERVICES_DIR: Final[str] = "/etc/avahi/services"

# The words a prepare command may never contain: the things provisioning promises not to touch.
FORBIDDEN: Final[tuple] = (
    "sshd",
    "ssh.service",
    "ssh.socket",
    "netplan",
    "NetworkManager",
    "nmcli",
    "ufw",
    "iptables",
    "nft",
    "passwd",
    "chpasswd",
    "useradd",
    "userdel",
    "sources.list",
    "visudo",
    "sudoers",
)


class NodePrepare:
    """The root half of setting up a node, for `SUDO_USER`."""

    @classmethod
    def user(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: The account the steps are for: the user who ran sudo.
        """
        name = os.environ.get("SUDO_USER")
        return name if name and name != "root" else None

    @classmethod
    def steps(cls, user: str) -> List[Dict[str, Any]]:
        """
        Lists what has to change on this machine for `user`.

        Args:
            user: The account the node runs as.

        Returns:
            List[Dict[str, Any]]: In order, each with `name`, `why`, and `commands` (argv lists,
            run as root) or `manual` (what to do by hand). Empty when the machine is ready.
        """
        found: List[Dict[str, Any]] = []
        for step in (
            *cls._host_steps(),
            cls._docker_step(user),
            cls._linger_step(user),
            cls._avahi_step(user),
            cls._sandbox_step(),
            cls._telemetry_step(),
        ):
            if step is not None:
                found.append(step)
        return found

    @classmethod
    def run(cls) -> bool:
        """
        Applies every missing step as root, printing each command first.

        Returns:
            bool: True when nothing is left to do afterwards.
        """
        if os.geteuid() != 0:
            print(
                "❌ node prepare changes the machine outside your home folder; run it as "
                "`sudo puffin-admin node prepare`."
            )
            return False
        user = cls.user()
        if user is None:
            print(
                "❌ node prepare acts for the user who ran sudo, and SUDO_USER is not set "
                "(run it with sudo from your own account, not from a root shell)."
            )
            return False
        steps = cls.steps(user)
        if not steps:
            print(f"✅ node prepare: nothing to do; this machine is ready for {user}.")
            return True
        for number, step in enumerate(steps, 1):
            print(f"{number}. {step['name']}: {step['why']}.")
            if step.get("manual"):
                print(f"   {step['manual']}")
                continue
            for command in step["commands"]:
                if not cls._execute(command):
                    print(f'❌ That command failed; the rest of "{step["name"]}" was skipped.')
                    break
        remaining = [step["name"] for step in cls.steps(user)]
        if remaining:
            print(f"⚠️  Still to do: {'; '.join(remaining)}.")
            return False
        print(f"✅ node prepare: this machine is ready for {user}.")
        return True

    # -- the steps ------------------------------------------------------------------------------

    @classmethod
    def _host_steps(cls) -> List[Dict[str, Any]]:
        from dreamference.vllm_server.host_safety_setup import HostSafetySetup

        # The sandbox check of `HostSafetySetup` runs bubblewrap from the invoking user's systemd,
        # which root does not have; this class checks the profile itself (`_sandbox_step`).
        return [
            step
            for step in HostSafetySetup.steps()
            if step["name"] != "let bubblewrap create its sandbox"
        ]

    @classmethod
    def _docker_step(cls, user: str) -> Optional[Dict[str, Any]]:
        try:
            members = grp.getgrnam("docker").gr_mem
        except KeyError:
            return {
                "name": "Docker without sudo",
                "why": "there is no `docker` group on this machine",
                "manual": "install Docker (NVIDIA's DGX OS ships it), then run node prepare again",
            }
        if user in members or cls._primary_group(user) == "docker":
            return None
        return {
            "name": "Docker without sudo",
            "why": f"{user} is not in the docker group, which the first-boot wizard does not add",
            "commands": [["usermod", "-aG", "docker", user]],
        }

    @classmethod
    def _linger_step(cls, user: str) -> Optional[Dict[str, Any]]:
        if Path(LINGER_DIR, user).exists():
            return None
        return {
            "name": "lingering",
            "why": "a job or a model load started over SSH stops with the connection without it",
            "commands": [["loginctl", "enable-linger", user]],
        }

    @classmethod
    def _avahi_step(cls, user: str) -> Optional[Dict[str, Any]]:
        from dreamference.node.node_service_file import NodeServiceFile

        path = NodeServiceFile.service_path
        if not Path(AVAHI_SERVICES_DIR).is_dir() and path.parent == Path(AVAHI_SERVICES_DIR):
            return {
                "name": "advertising",
                "why": "Avahi is not installed (no /etc/avahi/services)",
                "manual": "install avahi-daemon, which DGX OS ships, then run node prepare again",
            }
        try:
            if path.stat().st_uid == pwd.getpwnam(user).pw_uid:
                return None
        except (OSError, KeyError):
            pass
        group = cls._primary_group(user) or user
        return {
            "name": "advertising",
            "why": f"the service file must be {user}'s, so `node enable` and `server start` "
            f"rewrite it with no root",
            "commands": [["install", "-m", "644", "-o", user, "-g", group, "/dev/null", str(path)]],
        }

    @classmethod
    def _sandbox_step(cls) -> Optional[Dict[str, Any]]:
        from dreamference.vllm_server.sandbox_prerequisite import PROFILE_PATH, SandboxPrerequisite

        if Path(PROFILE_PATH).is_file() and cls._profile_loaded():
            return None
        commands = SandboxPrerequisite.fix_commands()
        if commands is None:
            return None
        return {
            "name": "let bubblewrap create its sandbox",
            "why": "Ubuntu refuses user namespaces to programs without an AppArmor profile, so "
            "`puffin`'s command sandbox, Night Shift and the code indexers would fail",
            "commands": commands,
        }

    @classmethod
    def _telemetry_step(cls) -> Optional[Dict[str, Any]]:
        enabled = cls._systemctl("is-enabled", TELEMETRY_UNIT)
        active = cls._systemctl("is-active", TELEMETRY_UNIT)
        if enabled not in ("enabled", "static", "enabled-runtime") and active != "active":
            return None
        return {
            "name": "NVIDIA's telemetry",
            "why": f"{TELEMETRY_UNIT} reports to NVIDIA; Puffin turns telemetry off, as it did "
            f"for vLLM and the web UI (NVIDIA's service: `systemctl enable --now "
            f"{TELEMETRY_UNIT}` puts it back)",
            "commands": [["systemctl", "disable", "--now", TELEMETRY_UNIT]],
        }

    # -- readings and the one place commands run --------------------------------------------------

    @classmethod
    def _primary_group(cls, user: str) -> Optional[str]:
        try:
            return grp.getgrgid(pwd.getpwnam(user).pw_gid).gr_name
        except KeyError:
            return None

    @classmethod
    def _profile_loaded(cls) -> bool:
        from dreamference.vllm_server.sandbox_prerequisite import PROFILE_NAME

        try:
            return any(
                line.split(" ")[0] == PROFILE_NAME
                for line in Path("/sys/kernel/security/apparmor/profiles").read_text().splitlines()
            )
        except OSError:
            return False

    @classmethod
    def _systemctl(cls, verb: str, unit: str) -> str:
        try:
            result = subprocess.run(
                ["systemctl", verb, unit], capture_output=True, text=True, timeout=15, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip()

    @classmethod
    def _execute(cls, command: List[str]) -> bool:
        if any(word in part for part in command for word in FORBIDDEN):
            print(f"❌ Refused, node prepare never runs this: {' '.join(command)}")
            return False
        print(f"🔑 {' '.join(command)}")
        try:
            return subprocess.run(command, check=False).returncode == 0
        except OSError as error:
            print(f"❌ {error}")
            return False
