"""The forge on this node: setup, the user units, on and off, and the ten-minute tick.

Setup runs at `node enable` (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §2, §5.2, §7).

Units, all user units (the node's user lingers):

- `mightling-forge.service` runs the container in the foreground (`ling-admin forge run`), so
  the bind addresses and a mirror's record address are resolved at every start;
- `mightling-forge-tick.timer`, every ten minutes: the storage budget, the hooks, the GitHub
  mirror's sweep and, hourly, the mirror plan on the record; a mirror restarts its container
  when the forge of record's address changed;
- `mightling-forge-backup.timer`, 06:30 local: the nightly backup (the record only);
- `mightling-forge-check.timer`, monthly: `restore --check` of the newest set this node holds;
- `mightling-forge-mirror.path`: runs the GitHub mirror seconds after a push (the record only).

The role is decided at the first setup: a node that finds a forge of record on the network
(another node's `_mightling-forge._tcp` with `role=record`) is a mirror, else it is the record.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_anonymity import ForgeAnonymity
from dreamference.forge.forge_app_ini import ForgeAppIni
from dreamference.forge.forge_bind import ForgeBind
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_settings import (
    HTTP_PORT,
    MEMORY_CAP_GIB,
    ROLE_MIRROR,
    ROLE_RECORD,
    ROLES,
    SERVICE_TYPE,
    UNIT,
    WEB_PATH,
    WEB_PORT,
    ForgeSettings,
)

TIMERS: Final[tuple] = (
    "mightling-forge-tick.timer",
    "mightling-forge-backup.timer",
    "mightling-forge-check.timer",
    "mightling-forge-mirror.path",
)
GIB_IN_GB: Final[float] = 1024**3 / 1000**3
# The forge of record hands the mirror plan to its paired nodes at most this often.
MIRROR_SYNC_INTERVAL_S: Final[int] = 3600


class ForgeService:
    """Sets the forge up and keeps it running."""

    @classmethod
    def unit_dir(cls) -> Path:
        """Return the folder of the user's systemd units.

        Returns:
            Path: `~/.config/systemd/user`.
        """
        return Path("~/.config/systemd/user").expanduser()

    @classmethod
    def admin_executable(cls) -> str:
        """Return the path of this node's `ling-admin`.

        Returns:
            str: This node's `ling-admin`, by path (units run with a minimal PATH).
        """
        from dreamference.node.node_serve import NodeServe

        return NodeServe.admin_executable()

    @classmethod
    def units(cls) -> dict[str, str]:
        """Render the forge's unit files.

        Returns:
            dict[str, str]: Each unit file's name and text.
        """
        admin = cls.admin_executable()
        docker = shutil.which("docker") or "/usr/bin/docker"
        trigger = ForgeSettings.data_dir() / "mightling" / "pushed"

        def oneshot(description: str, command: str) -> str:
            return (
                f"[Unit]\nDescription={description}\n\n[Service]\nType=oneshot\n"
                f"ExecStart={admin} forge {command}\nNice=10\n"
            )

        return {
            UNIT: (
                "[Unit]\nDescription=Mightling forge (Forgejo)\nAfter=network-online.target\n\n"
                f"[Service]\nExecStartPre=-{docker} rm -f {ForgeContainer.name}\nExecStart={admin} forge run\n"
                f"ExecStop={docker} stop -t 30 {ForgeContainer.name}\nRestart=on-failure\nRestartSec=30\n\n"
                "[Install]\nWantedBy=default.target\n"
            ),
            "mightling-forge-tick.service": oneshot(
                "Mightling forge: budget, hooks, mirrors", "tick"
            ),
            "mightling-forge-tick.timer": (
                "[Unit]\nDescription=Mightling forge tick\n\n[Timer]\nOnBootSec=5min\n"
                "OnUnitActiveSec=10min\n\n[Install]\nWantedBy=timers.target\n"
            ),
            "mightling-forge-backup.service": oneshot("Mightling forge: nightly backup", "backup"),
            "mightling-forge-backup.timer": (
                "[Unit]\nDescription=Mightling forge nightly backup\n\n[Timer]\n"
                "OnCalendar=*-*-* 06:30:00\nPersistent=true\n\n"
                "[Install]\nWantedBy=timers.target\n"
            ),
            "mightling-forge-check.service": oneshot(
                "Mightling forge: restore check", "restore --check"
            ),
            "mightling-forge-check.timer": (
                "[Unit]\nDescription=Mightling forge monthly restore check\n\n[Timer]\n"
                "OnCalendar=monthly\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n"
            ),
            "mightling-forge-mirror.service": oneshot(
                "Mightling forge: GitHub mirror", "github-mirror --run"
            ),
            "mightling-forge-mirror.path": (
                f"[Unit]\nDescription=Mightling forge: a push arrived\n\n[Path]\n"
                f"PathModified={trigger}\n\n[Install]\nWantedBy=default.target\n"
            ),
        }

    @classmethod
    def systemctl(cls, *arguments: str) -> subprocess.CompletedProcess:
        """Run `systemctl --user`.

        Args:
            *arguments: `systemctl --user`'s arguments.

        Returns:
            subprocess.CompletedProcess: Its result.
        """
        try:
            return subprocess.run(
                ["systemctl", "--user", *arguments],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 1, "", str(error))

    @classmethod
    def install_units(cls) -> None:
        """Write the unit files and reload systemd."""
        folder = cls.unit_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (ForgeSettings.data_dir() / "mightling").mkdir(parents=True, exist_ok=True)
        for name, text in cls.units().items():
            (folder / name).write_text(text)
        cls.systemctl("daemon-reload")

    @classmethod
    def host_name(cls) -> str:
        """Return this machine's name without a domain.

        Returns:
            str: This machine's name without a domain.
        """
        return socket.gethostname().split(".")[0]

    @classmethod
    def find_record(cls) -> dict[str, str] | None:
        """Look on the network for another node's forge of record.

        Returns:
            dict[str, str] | None: Another node's `_mightling-forge._tcp` answer with
            `role=record`, or None.
        """
        from dreamference.node.node_browser import NodeBrowser
        from dreamference.node.node_identity import NodeIdentity

        mine = NodeIdentity.read()
        for entry in NodeBrowser.browse_service(SERVICE_TYPE):
            if entry.get("role") == ROLE_RECORD and entry.get("node") and entry.get("node") != mine:
                return entry
        return None

    @classmethod
    def decide_role(cls) -> str:
        """Decide the role once (the first node set up is the record) and keep it.

        Returns:
            str: `record` or `mirror`.
        """
        role = ForgeSettings.role()
        if role:
            return role
        record = cls.find_record()
        if record:
            ForgeSettings.update_state(
                role=ROLE_MIRROR, record_node=record["node"], record_name=record.get("name", "")
            )
            return ROLE_MIRROR
        ForgeSettings.update_state(role=ROLE_RECORD)
        return ROLE_RECORD

    @classmethod
    def record_address(cls) -> str | None:
        """Resolve the forge of record's address now, by its node id.

        Returns:
            str | None: The forge of record's address now, resolved by its node id, or None.
        """
        from dreamference.node.node_browser import NodeBrowser

        wanted = ForgeSettings.state().get("record_node")
        if not wanted:
            return None
        for entry in NodeBrowser.browse_service(SERVICE_TYPE) + NodeBrowser.browse():
            if entry.get("node") == wanted and entry.get("address"):
                return entry["address"].split("%")[0]
        return None

    @classmethod
    def run_arguments(cls) -> list[str]:
        """Write the forge's files and build the container's `docker run` command, resolved now.

        Returns:
            list[str]: The container's `docker run` command, resolved now.
        """
        data = ForgeSettings.data_dir()
        role = ForgeSettings.role() or ROLE_RECORD
        ForgeAppIni.write(data, cls.host_name(), role)
        ForgeAnonymity.write_patterns(data)
        record_address = cls.record_address() if role == ROLE_MIRROR else None
        if role == ROLE_MIRROR:
            ForgeSettings.update_state(record_address_at_start=record_address or "")
        return ForgeContainer.run_arguments(data, ForgeBind.lan_addresses(), record_address)

    @classmethod
    def run(cls) -> int:
        """Replace this process with the container (the unit's `ExecStart`).

        Returns:
            int: Only when docker could not be started.
        """
        if not ForgeContainer.ensure_network() or not ForgeContainer.ensure_image():
            print("❌ Docker could not prepare the forge's network or image.", file=sys.stderr)
            return 1
        argv = cls.run_arguments()
        sys.stdout.flush()
        os.execvp(argv[0], argv)
        return 1  # pragma: no cover - execvp does not return

    @classmethod
    def restart(cls) -> bool:
        """Restart the forge's unit.

        Returns:
            bool: Whether the unit restarted.
        """
        return cls.systemctl("restart", UNIT).returncode == 0

    @classmethod
    def advertise(cls, role: str | None) -> None:
        """Add the forge's record to the node's advertisement, or remove it.

        Args:
            role: `record`, `mirror`, or None to stop advertising the forge.
        """
        from dreamference.node.node_service_file import NodeServiceFile

        NodeServiceFile.update(forge=(role, HTTP_PORT) if role else None)

    @classmethod
    def setup(cls, show_password: bool = True) -> bool:
        """Set the forge up and start it (what `node enable` runs, and `forge on`).

        Args:
            show_password: Show the owner's password when it is generated now.

        Returns:
            bool: Whether the forge runs.
        """
        if not ForgeSettings.enabled():
            print(
                "💡 The forge is off on this node ([forge] enabled = false or DREAMFERENCE_FORGE=0)."
            )
            return True
        if not ForgeContainer.ensure_image() or not ForgeContainer.ensure_network():
            print(
                "❌ The forge's image or network could not be prepared; the node works without the forge."
            )
            return False
        role = cls.decide_role()
        cls.install_units()
        cls.systemctl("enable", UNIT, *TIMERS)
        started = cls.systemctl("restart", UNIT)
        if started.returncode != 0 or not ForgeContainer.wait_until_answering(180):
            print(f"❌ The forge did not start: `journalctl --user -u {UNIT}` says why.")
            return False
        ready, password = ForgeAccounts.ensure()
        if not ready:
            return False
        if ForgeAccounts.owner_is_admin():
            print("⚠️  The forge's owner account is an administrator; it should not be.")
        cls.systemctl("start", *TIMERS)
        cls.advertise(role)
        addresses = ForgeBind.lan_addresses()
        where = (
            ", ".join(f"http://{address}:{HTTP_PORT}/" for address in addresses) or "loopback only"
        )
        if role == ROLE_RECORD:
            from dreamference.forge.forge_repositories import ForgeRepositories

            ForgeRepositories.install_hooks(mirror_trigger=True)
            cls.ensure_phone()
            print(
                f"✅ The forge of record runs: in a browser at http://{cls.host_name()}.local:{WEB_PORT}{WEB_PATH} "
                f"through `ling web`, signed in on paired devices; git and the API on {where}."
            )
        else:
            name = ForgeSettings.state().get("record_name") or "the forge of record"
            print(
                f"✅ This node's forge runs as a read-only mirror of {name} ({where}). Its repositories arrive "
                "with `ling-admin forge mirrors sync`, run on a node paired with this one (the record's "
                "timer runs it hourly)."
            )
        if password and show_password:
            ForgeAccounts.show_password_once(password)
        return True

    @classmethod
    def off(cls) -> int:
        """Stop the units and remove the advertisement, keeping the data (`forge off`).

        Returns:
            int: 0, or 1 when systemd did not stop them.
        """
        ForgeSettings.set_enabled(False)
        stopped = cls.systemctl("disable", "--now", UNIT, *TIMERS)
        cls.advertise(None)
        if stopped.returncode != 0:
            print(f"❌ systemd did not stop the forge: {(stopped.stderr or '').strip()[-300:]}")
            return 1
        print(
            f"✅ The forge is off; its data stays in {ForgeSettings.share_dir()}. "
            "`ling-admin forge on` brings it back."
        )
        return 0

    @classmethod
    def on(cls) -> int:
        """Turn the forge on and set it up (`forge on`).

        Returns:
            int: 0 when the forge runs.
        """
        ForgeSettings.set_enabled(True)
        return 0 if cls.setup() else 1

    @classmethod
    def memory_allowance_gb(cls) -> float:
        """Return what the model server's pre-load check sets aside for the forge (§2.6).

        Returns:
            float: The cap in GB while the forge's container runs, else 0.
        """
        # A node whose forge was never set up (no role in its state file) asks docker nothing.
        if ForgeSettings.role() is None:
            return 0.0
        try:
            return MEMORY_CAP_GIB * GIB_IN_GB if ForgeContainer.running() else 0.0
        except (OSError, subprocess.SubprocessError):
            return 0.0

    @classmethod
    def housekeeping(cls) -> None:
        """Keep the storage budget and remove data folders a move set aside more than 30 days ago."""
        from dreamference.forge.forge_move import ForgeMove
        from dreamference.forge.forge_storage import ForgeStorage

        for line in ForgeStorage.prune():
            print(line)
        for name in ForgeMove.prune_old_data():
            print(f"🧹 Removed {name}, set aside by a move more than 30 days ago.")

    @classmethod
    def ensure_phone(cls) -> None:
        """Keep the phone account, its token and the default branches' protection in line (§2.7)."""
        from dreamference.forge.forge_phone import ForgePhone

        try:
            for line in ForgePhone.ensure():
                print(line)
        except RuntimeError as error:
            print(f"⚠️  The phone account was not checked: {error}")

    @classmethod
    def tick(cls) -> int:
        """Do the ten-minute timer's work.

        Returns:
            int: 0.
        """
        cls.housekeeping()
        role = ForgeSettings.role()
        if role in ROLES and ForgeContainer.running():
            from dreamference.forge.forge_repositories import ForgeRepositories

            try:
                for line in ForgeRepositories.remove_push_mirrors():
                    print(line)
            except RuntimeError as error:
                print(f"⚠️  Push mirrors not checked: {error}")
        if role == ROLE_MIRROR:
            address = cls.record_address()
            if address and address != ForgeSettings.state().get("record_address_at_start"):
                print(
                    "💡 The forge of record's address changed; restarting this mirror's container."
                )
                cls.restart()
            return 0
        if role != ROLE_RECORD or not ForgeContainer.running():
            return 0
        from dreamference.forge.forge_github_mirror import ForgeGithubMirror
        from dreamference.forge.forge_mirrors import ForgeMirrors
        from dreamference.forge.forge_repositories import ForgeRepositories

        try:
            ForgeRepositories.install_hooks(mirror_trigger=True)
        except RuntimeError as error:
            print(f"⚠️  Hooks not refreshed: {error}")
        ForgeGithubMirror.run()
        cls.ensure_phone()
        last = ForgeSettings.state().get("mirrors_synced_at", 0)
        if time.time() - float(last) > MIRROR_SYNC_INTERVAL_S:
            ForgeMirrors.sync(quiet=True)
            ForgeSettings.update_state(mirrors_synced_at=int(time.time()))
        return 0
