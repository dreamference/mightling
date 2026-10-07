"""
Applies the host settings `VLLMServerManager.check_host_safety()` demands
(`puffin-admin host check|setup`).

It also checks, and fixes, one thing `check_host_safety()` does not look at, because it is the
agent's prerequisite and not the model's: whether bubblewrap can create a sandbox from an ordinary
login. `SandboxPrerequisite` asks about the same fix on every `puffin-admin` run.

`check_host_safety()` refuses to load a model on a machine without sysstat, an armed OOM handler,
64 GB of swap and two raised sysctls, and until 2026-10-02 it only printed the `sudo` lines: a
fresh GB10 could not load a model until its owner had copied five commands by hand. This class
runs them. It decides nothing itself: every reading comes from the same helpers the check uses, so
the two cannot disagree about what "safe" means, and the check stays the gate.

Everything here changes the machine outside the user's home, so each command is printed before it
runs and sudo prompts on the terminal. Where sudo cannot prompt, the commands are only printed.
"""

import getpass
try:
    import grp
except ImportError:  # Windows, where only the release builder imports this package
    grp = None
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, Final, List, Optional

from dreamference.vllm_server.vllm_server_manager import (
    EARLYOOM_ARGS,
    MIN_FREE_KBYTES,
    MIN_SWAP_GB,
    WATERMARK_SCALE_FACTOR,
    VLLMServerManager,
)

SWAP_FILE: Final[str] = "/swap.img"
SYSCTL_FILE: Final[str] = "/etc/sysctl.d/99-dreamference.conf"
EARLYOOM_DEFAULTS: Final[str] = "/etc/default/earlyoom"
FSTAB: Final[str] = "/etc/fstab"
DOCKER_SOCKET: Final[str] = "/var/run/docker.sock"
# Where NVIDIA documents installing the two things DGX OS ships and a plain Ubuntu does not. Every
# GB10 machine ships DGX OS (HP and Lenovo also document Ubuntu 24.04), so these matter only to a
# machine reinstalled with Ubuntu; none has been seen here (specs/DREAMFERENCE_SETUP.md §3.5).
DOCKER_INSTALL_URL: Final[str] = "https://docs.docker.com/engine/install/ubuntu/"
TOOLKIT_INSTALL_URL: Final[str] = (
    "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html")
# Any one on PATH means the NVIDIA Container Toolkit is installed: `nvidia-ctk` since 1.12, the
# hook and the CLI in every version.
TOOLKIT_COMMANDS: Final[tuple] = ("nvidia-ctk", "nvidia-container-runtime-hook", "nvidia-container-cli")

# Disk left free after the swap file is made: a root filesystem filled to the last block by its
# own swap file is a worse machine than one with too little swap.
SWAP_DISK_MARGIN_GB: Final[float] = 20.0


class HostSafetySetup:
    """Finds what `check_host_safety()` would refuse over, and applies the fixes."""

    @classmethod
    def steps(cls) -> List[Dict[str, Any]]:
        """
        Lists what has to change before a model may load.

        Returns:
            List[Dict[str, Any]]: One entry per failing check, in the order they are applied:
            `name`, `why` (one line), `commands` (argv lists to run as root, without `sudo`), and
            `manual` (set instead of `commands` when the fix is not safe to automate, saying what
            to do). Empty when the host already passes.
        """
        found: List[Dict[str, Any]] = []
        for step in (cls._sysstat_step(), cls._oom_step(), cls._swap_step(), *cls._sysctl_steps(),
                     cls._container_step(), cls._sandbox_step()):
            if step is not None:
                found.append(step)
        return found

    @classmethod
    def check(cls) -> bool:
        """
        Prints what `setup` would do, changing nothing.

        Returns:
            bool: True if the host already passes every check.
        """
        steps = cls.steps()
        if not steps:
            print("✅ Host setup: nothing to do. This machine has what a model load and puffin's sandbox need.")
            return True
        print(f"⚠️  Host setup: {len(steps)} thing(s) to fix on this machine.\n")
        for number, step in enumerate(steps, 1):
            cls._describe(number, step)
        if any("commands" in step for step in steps):
            print("💡 `puffin-admin host setup` runs the commands listed (sudo asks for your password).")
        return False

    @classmethod
    def setup(cls) -> bool:
        """
        Applies every fix that is safe to automate, then reads the host again.

        Returns:
            bool: True if the host passes every check afterwards.
        """
        steps = cls.steps()
        if not steps:
            print("✅ Host setup: nothing to do. This machine has what a model load and puffin's sandbox need.")
            return True
        can_prompt = sys.stdin.isatty() and shutil.which("sudo") is not None
        if not can_prompt:
            print("⚠️  sudo cannot ask for a password here (no terminal), so nothing was changed.\n"
                  "   Run `puffin-admin host setup` in a terminal, or run these yourself:\n")
        for number, step in enumerate(steps, 1):
            cls._describe(number, step)
            if not can_prompt or step.get("manual"):
                continue
            for command in step["commands"]:
                if not cls._run_as_root(command):
                    print(f"❌ That command failed; the rest of \"{step['name']}\" was skipped.")
                    break
        remaining = cls.steps()
        if not any(step["name"] == "let bubblewrap create its sandbox" for step in remaining):
            # A "turn it off" answer given to `SandboxPrerequisite` no longer applies.
            from dreamference.vllm_server.sandbox_prerequisite import SandboxPrerequisite
            if SandboxPrerequisite.turned_off():
                SandboxPrerequisite._forget()
                print("💡 Night Shift was turned off for the sandbox; `puffin-admin night enable` puts the timer back.")
        if not remaining:
            print("✅ Host setup: this machine now has what a model load and puffin's sandbox need.")
            return True
        print(f"⚠️  {len(remaining)} thing(s) still to fix: "
              + "; ".join(step["name"] for step in remaining) + ".")
        return False

    # -- the checks, each with its fix -----------------------------------------------------------

    @classmethod
    def _sysstat_step(cls) -> Optional[Dict[str, Any]]:
        if shutil.which("sar") is not None:
            return None
        return {
            "name": "install sysstat",
            "why": "its sar/sadc history is the only record of a freeze that survives a hard reset",
            "commands": [
                ["apt-get", "install", "-y", "sysstat"],
                ["sed", "-i", 's/^ENABLED=.*/ENABLED="true"/', "/etc/default/sysstat"],
                ["systemctl", "enable", "--now", "sysstat"],
            ],
        }

    @classmethod
    def _oom_step(cls) -> Optional[Dict[str, Any]]:
        if VLLMServerManager._oom_handler_problem() is None:
            return None
        # The line is written whole rather than edited: the stock file's arguments are the fault.
        write_args = ["sh", "-c", f"printf '%s\\n' 'EARLYOOM_ARGS=\"{EARLYOOM_ARGS}\"' > {EARLYOOM_DEFAULTS}"]
        if VLLMServerManager._process_argv("earlyoom") is not None:
            return {
                "name": "arm earlyoom",
                "why": "it is running with thresholds that can never fire, or that kill healthy loads, on this hardware",
                "commands": [write_args, ["systemctl", "restart", "earlyoom"]],
            }
        return {
            "name": "install and arm earlyoom",
            "why": "without a memory-pressure handler a model load can freeze the whole machine",
            "commands": [
                ["apt-get", "install", "-y", "earlyoom"],
                write_args,
                ["systemctl", "enable", "earlyoom"],
                ["systemctl", "restart", "earlyoom"],
            ],
        }

    @classmethod
    def _swap_step(cls) -> Optional[Dict[str, Any]]:
        swap_gb = VLLMServerManager._swap_total_gb()
        if swap_gb >= MIN_SWAP_GB * 0.99:
            return None
        step: Dict[str, Any] = {
            "name": f"raise swap to {MIN_SWAP_GB:.0f} GB",
            "why": f"swap is {swap_gb:.1f} GB; with too little, the kernel stalls instead of shedding cold pages",
        }
        # zram is not counted (see `_swap_total_gb`), and it is no reason not to resize the swap
        # file beside it: it stays as it is.
        areas = [area for area in cls.swap_areas() if not os.path.basename(area["name"]).startswith("zram")]
        target = f"{MIN_SWAP_GB:.0f}G"
        make = [
            ["fallocate", "-l", target, SWAP_FILE],
            ["chmod", "600", SWAP_FILE],
            ["mkswap", SWAP_FILE],
            ["swapon", SWAP_FILE],
            # Ubuntu's installer lists /swap.img in fstab; a machine without the line would lose
            # the swap, and with it this check, at its next boot.
            ["sh", "-c", f"grep -q '^{SWAP_FILE}[[:space:]]' {FSTAB} || "
                         f"echo '{SWAP_FILE} none swap sw 0 0' >> {FSTAB}"],
        ]
        only_the_swap_file = len(areas) == 1 and areas[0]["name"] == SWAP_FILE and areas[0]["type"] == "file"
        if areas and not only_the_swap_file:
            listed = ", ".join(f"{area['name']} ({area['type']})" for area in areas)
            step["manual"] = (f"This machine's swap is {listed}, not the single {SWAP_FILE} Ubuntu sets up, so "
                              f"it is not resized automatically. Bring the total to {MIN_SWAP_GB:.0f} GB "
                              f"yourself, for example by adding a swap file beside what is there.")
            return step
        if not areas and os.path.exists(SWAP_FILE):
            step["manual"] = (f"{SWAP_FILE} exists but is not in use as swap. Check what it is, then "
                              f"`sudo swapon {SWAP_FILE}` or remove it and run this again.")
            return step
        existing_gb = areas[0]["size_gb"] if areas else 0.0
        free_gb = cls._disk_free_gb("/")
        if free_gb + existing_gb < MIN_SWAP_GB + SWAP_DISK_MARGIN_GB:
            step["manual"] = (f"The root filesystem has {free_gb:.0f} GB free; a {MIN_SWAP_GB:.0f} GB swap "
                              f"file needs that and {SWAP_DISK_MARGIN_GB:.0f} GB to spare. Free some space first.")
            return step
        if areas:
            # `swapoff` moves everything swapped out back into memory, so it must fit.
            used_gb = areas[0]["used_gb"]
            if used_gb > cls._mem_available_gb() - 4.0:
                step["manual"] = (f"{used_gb:.1f} GB is swapped out now and would not fit back into memory, "
                                  f"so {SWAP_FILE} cannot be resized while this much is running. Stop the "
                                  f"model server (`puffin-admin server stop`) and run this again.")
                return step
            make.insert(0, ["swapoff", SWAP_FILE])
        step["commands"] = make
        return step

    @classmethod
    def _sysctl_steps(cls) -> List[Dict[str, Any]]:
        steps = []
        for name, wanted, why in (
            ("vm.min_free_kbytes", MIN_FREE_KBYTES,
             "a larger emergency pool keeps allocations from stalling on reclaim"),
            ("vm.watermark_scale_factor", WATERMARK_SCALE_FACTOR,
             "background reclaim starts earlier and stays ahead of the loader"),
        ):
            current = VLLMServerManager._sysctl_int(name)
            if current is None or current >= wanted:
                continue
            line = f"{name}={wanted}"
            steps.append({
                "name": f"set {line}",
                "why": f"it is {current}; {why}",
                "commands": [
                    ["sysctl", "-w", line],
                    # Kept across reboots, and written once however often this runs.
                    ["sh", "-c", f"grep -qx '{line}' {SYSCTL_FILE} 2>/dev/null || echo '{line}' >> {SYSCTL_FILE}"],
                ],
            })
        return steps

    @classmethod
    def _container_step(cls) -> Optional[Dict[str, Any]]:
        """
        The model server runs in Docker with `--gpus all`, which needs Docker, the NVIDIA Container
        Toolkit, and a user who may talk to the daemon.

        DGX OS ships all three, which is why `check_host_safety()` never had to ask; a GB10 machine
        reinstalled with plain Ubuntu (HP and Lenovo document it) has none of them, and the first
        sign used to be a `docker run` error from `server start`. Installing Docker and the toolkit
        means adding a vendor's apt repository, so those two are explained, not done. Joining the
        `docker` group is one command, and it is done.
        """
        if shutil.which("docker") is None:
            return {
                "name": "install Docker and the NVIDIA Container Toolkit",
                "why": "the model server runs in a Docker container; DGX OS ships both, plain Ubuntu neither",
                "manual": (f"Install Docker Engine ({DOCKER_INSTALL_URL}) and the NVIDIA Container "
                           f"Toolkit ({TOOLKIT_INSTALL_URL}), then run this again."),
            }
        if not any(shutil.which(command) for command in TOOLKIT_COMMANDS):
            return {
                "name": "install the NVIDIA Container Toolkit",
                "why": "without it `docker run --gpus all` fails, so no model server can start",
                "manual": (f"Install it from NVIDIA's apt repository ({TOOLKIT_INSTALL_URL}), then "
                           f"`sudo nvidia-ctk runtime configure --runtime=docker` and "
                           f"`sudo systemctl restart docker` (which restarts every running container)."),
            }
        if cls._may_use_docker():
            return None
        user = cls._user()
        return {
            "name": f"let {user} use Docker",
            "why": f"{user} is not in the docker group, so every docker command Puffin runs is refused; "
                   f"it takes effect at your next login",
            "commands": [["usermod", "-aG", "docker", user]],
        }

    @classmethod
    def _sandbox_step(cls) -> Optional[Dict[str, Any]]:
        """
        The one prerequisite here that is `puffin`'s, not the model server's: its command sandbox.

        Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`, under which bubblewrap
        (and so Codex's sandbox, Night Shift's tasks and the code indexers) fails unless an
        AppArmor profile allows it. On the machine this was written on it had only ever worked
        because every shell was a child of a snap-confined IDE; from a plain terminal, an SSH
        login or a systemd timer every sandboxed command failed (measured 2026-10-02).
        """
        from dreamference.vllm_server.sandbox_prerequisite import SandboxPrerequisite
        if shutil.which("bwrap") is None:
            # GNOME pulls bubblewrap in (WebKit and the desktop portal depend on it), so DGX OS has
            # it; a machine installed as Ubuntu Server does not. Codex falls back to a bundled copy
            # Puffin does not install, and Night Shift, node jobs and the code indexers call the
            # system one, so it is installed, with the profile at the same time where it is needed.
            return {
                "name": "install bubblewrap",
                "why": "`puffin`'s command sandbox, Night Shift tasks, node jobs and the code indexers all run in it",
                "commands": [["apt-get", "install", "-y", "bubblewrap"], *(SandboxPrerequisite.fix_commands() or [])],
            }
        if cls.sandbox_works() is not False:
            return None
        step: Dict[str, Any] = {
            "name": "let bubblewrap create its sandbox",
            "why": "from an ordinary login `bwrap` is refused a user namespace, so every command "
                   "`puffin` runs in its sandbox fails, as do Night Shift tasks and the code indexers",
        }
        commands = SandboxPrerequisite.fix_commands()
        if commands is None:
            step["manual"] = ("bubblewrap is refused a user namespace, but not by Ubuntu's AppArmor "
                              "restriction (kernel.apparmor_restrict_unprivileged_userns is not 1 here), "
                              "so an AppArmor profile would not help. Check user.max_user_namespaces and, "
                              "on Debian kernels, kernel.unprivileged_userns_clone.")
            return step
        # An AppArmor profile for bubblewrap alone, the shape of Ubuntu's own for sandboxing
        # programs; see sandbox_prerequisite.py for why not the sysctl.
        step["commands"] = commands
        return step

    # -- readings --------------------------------------------------------------------------------

    @classmethod
    def sandbox_works(cls) -> Optional[bool]:
        """
        Tries bubblewrap from a process with no inherited AppArmor label: a transient unit of the
        user's systemd, which is how a login shell, an SSH session and the Night Shift timer run.
        Trying it from this process would only test whatever happens to confine the caller.

        Returns:
            Optional[bool]: True or False, or None where it cannot be tried (no bubblewrap, no
            user systemd), in which case nothing is reported.
        """
        if shutil.which("bwrap") is None or shutil.which("systemd-run") is None:
            return None
        try:
            result = subprocess.run(
                ["systemd-run", "--user", "--wait", "--pipe", "--quiet", "--collect", "--",
                 "bwrap", "--ro-bind", "/", "/", "--unshare-net", "true"],
                capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        output = result.stdout + result.stderr
        if "bwrap:" in output:
            return False
        return True if result.returncode == 0 else None

    @classmethod
    def _may_use_docker(cls) -> bool:
        """
        Returns:
            bool: True if this user can reach the Docker daemon: the socket is writable to this
            process (root, an ACL, a rootless daemon elsewhere), or the user is listed in the
            `docker` group, which counts before the next login makes it true for processes too.
        """
        if not os.path.exists(DOCKER_SOCKET) or os.access(DOCKER_SOCKET, os.W_OK):
            return True
        if grp is None:
            return True
        try:
            return cls._user() in grp.getgrnam("docker").gr_mem
        except KeyError:
            return True           # no docker group to join: the socket is someone else's design
        except OSError:
            return True

    @classmethod
    def _user(cls) -> str:
        return os.environ.get("SUDO_USER") or getpass.getuser()

    @classmethod
    def swap_areas(cls) -> List[Dict[str, Any]]:
        """
        Reads the swap areas in use from `/proc/swaps`.

        Returns:
            List[Dict[str, Any]]: `name`, `type` (`file` or `partition`), `size_gb` and `used_gb`
            for each; empty when there is no swap or the file cannot be read.
        """
        areas: List[Dict[str, Any]] = []
        try:
            with open("/proc/swaps") as handle:
                lines = handle.read().splitlines()[1:]
        except OSError:
            return areas
        for line in lines:
            fields = line.split()
            if len(fields) < 4:
                continue
            try:
                areas.append({"name": fields[0], "type": fields[1],
                              "size_gb": int(fields[2]) / (1024 ** 2),
                              "used_gb": int(fields[3]) / (1024 ** 2)})
            except ValueError:
                continue
        return areas

    @classmethod
    def _disk_free_gb(cls, path: str) -> float:
        try:
            return shutil.disk_usage(path).free / (1024 ** 3)
        except OSError:
            return 0.0

    @classmethod
    def _mem_available_gb(cls) -> float:
        try:
            with open("/proc/meminfo") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / (1024 ** 2)
        except (OSError, ValueError, IndexError):
            pass
        return 0.0

    # -- output and the one place sudo runs -------------------------------------------------------

    @classmethod
    def _describe(cls, number: int, step: Dict[str, Any]) -> None:
        print(f"{number}. {step['name']}: {step['why']}.")
        if step.get("manual"):
            print(f"   {step['manual']}\n")
            return
        for command in step["commands"]:
            print(f"     {cls.command_line(command)}")
        print()

    @classmethod
    def command_line(cls, command: List[str]) -> str:
        """
        Renders a command as the line a user would type.

        Args:
            command: The argv, without `sudo`.

        Returns:
            str: `sudo …`, with arguments quoted where a shell needs it.
        """
        import shlex
        return "sudo " + " ".join(shlex.quote(part) for part in command)

    @classmethod
    def _run_as_root(cls, command: List[str]) -> bool:
        print(f"🔑 {cls.command_line(command)}")
        try:
            return subprocess.run(["sudo", *command], check=False).returncode == 0
        except OSError as error:
            print(f"❌ {error}")
            return False
