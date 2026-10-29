"""Handing the forge of record to another node: `forge move` and `forge promote`.

`forge move` is planned and `forge promote` is not (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md
§6.1, §6.2).

`move <node>` runs on the forge of record, which must be paired with the target:

1. freeze: the push hook refuses every push with a message to retry;
2. back up: a fresh set, sent to the target and to one more paired node;
3. copy and check: the target restores the set into its instance (which was a mirror), compares
   every ref with the set's manifest and takes `role=record`; anything different and the move
   stops, the old record unfrozen;
4. flip: this node's data folder and secrets are moved aside, never deleted, and it starts again
   as a mirror of the new record; the mirror plan is handed to every paired node.

`move --back` within the 30 days the old data is kept is the same move towards the previous
record. `promote <node>` restores the newest set that node holds and makes it the record: pushes
after that set are lost from the forge, not from the agents' worktrees, which push again.
"""

import calendar
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Final

from dreamference.forge.forge_backup import SECRET_FILES, SET_NAME, ForgeBackup
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_settings import ROLE_MIRROR, ROLE_RECORD, UNIT, ForgeSettings

# A node id is letters, digits and this separator.
NODE_ID_SEPARATOR: Final[str] = "-"
# What `forge move` accepts for "this node" on a mirror that pulls the role over.
HERE_NAMES: Final[tuple[str, ...]] = ("here",)
HAND_OVER_TIMEOUT_S: Final[int] = 7200
# Besides letters and digits, what a node id or host name may hold.
NAME_PUNCTUATION: Final[str] = "-."


class ForgeMove:
    """Moves and promotes the forge of record."""

    @classmethod
    def frozen_flag(cls) -> Path:
        """Return the file whose presence makes the push hook refuse every push.

        Returns:
            Path: The file whose presence makes the push hook refuse every push.
        """
        return ForgeSettings.data_dir() / "mightling" / "frozen"

    @classmethod
    def freeze(cls, frozen: bool) -> None:
        """Freeze or unfreeze pushes while the role moves.

        Args:
            frozen: Whether pushes are refused.
        """
        flag = cls.frozen_flag()
        if frozen:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(str(int(time.time())))
        else:
            flag.unlink(missing_ok=True)

    @classmethod
    def restore_here(cls, folder: Path, previous: str | None) -> dict[str, Any]:
        """Make this node the forge of record from a set.

        This is the target's half of `move`, and `promote`.

        Args:
            folder: The set.
            previous: The node id of the record the set came from, kept for `move --back`.

        Returns:
            dict[str, Any]: `ok`, `differences` and `error`.
        """
        from dreamference.forge.forge_repositories import ForgeRepositories
        from dreamference.forge.forge_service import ForgeService

        ForgeService.systemctl("stop", UNIT)
        manifest, aside = ForgeBackup.install_data(folder)
        if manifest is None:
            ForgeService.systemctl("start", UNIT)
            return {"ok": False, "error": "the set did not restore"}
        cls.freeze(False)
        ForgeSettings.update_state(
            role=ROLE_RECORD,
            record_node=None,
            record_name=None,
            previous_record=previous,
            record_address_at_start=None,
            became_record_at=int(time.time()),
            replaced_data=str(aside) if aside else None,
        )
        ForgeService.install_units()
        ForgeService.systemctl("enable", UNIT)
        if ForgeService.systemctl(
            "restart", UNIT
        ).returncode != 0 or not ForgeContainer.wait_until_answering(180):
            return {"ok": False, "error": "the restored forge did not start"}
        ForgeContainer.forgejo(["admin", "regenerate", "hooks"], timeout=600)
        try:
            ForgeRepositories.install_hooks(mirror_trigger=True)
        except RuntimeError as error:
            return {"ok": False, "error": f"hooks: {error}"}
        differences = ForgeBackup.compare(
            manifest, ForgeSettings.data_dir() / "git" / "repositories"
        )
        ForgeService.advertise(ROLE_RECORD)
        return {
            "ok": not differences,
            "differences": differences[:50],
            "error": f"{len(differences)} refs differ" if differences else "",
        }

    @classmethod
    def serve_restore(cls, name: str, previous: str | None) -> int:
        """Restore a received set here (the forced command's `forge-restore <set> [<previous>]`).

        Args:
            name: A set this node received.
            previous: The sending record's node id.

        Returns:
            int: 0 when this node is now the record with every ref, 1 otherwise, 2 when refused.
        """
        folder = ForgeBackup.received_dir() / name
        if not SET_NAME.fullmatch(name) or not folder.is_dir():
            print(json.dumps({"ok": False, "error": "no such backup set here"}))
            return 2
        if previous is not None and not all(
            c.isalnum() or c == NODE_ID_SEPARATOR for c in previous
        ):
            print(json.dumps({"ok": False, "error": "not a node id"}))
            return 2
        result = cls.restore_here(folder, previous)
        print(json.dumps(result))
        return 0 if result["ok"] else 1

    @classmethod
    def become_mirror(cls, record_node: str, record_name: str) -> None:
        """Do this node's half of the flip: move data and secrets aside, start a fresh mirror.

        Args:
            record_node: The new record's node id.
            record_name: Its name.
        """
        from dreamference.forge.forge_service import ForgeService

        ForgeService.systemctl("stop", UNIT)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        data = ForgeSettings.data_dir()
        if data.exists():
            data.rename(data.with_name(f"data.record-{stamp}"))
        for key in SECRET_FILES:
            path = ForgeSettings.secret_path(key)
            if path.exists():
                path.rename(path.with_name(f"{key}.record-{stamp}"))
        ForgeSettings.update_state(
            role=ROLE_MIRROR,
            record_node=record_node,
            record_name=record_name,
            previous_data=str(data.with_name(f"data.record-{stamp}")),
            mirrors_synced_at=None,
        )
        ForgeService.setup(show_password=False)

    @classmethod
    def move(cls, name: str | None, back: bool = False) -> int:
        """Move the forge of record (`forge move <node>` and `forge move --back`).

        Args:
            name: The target node (paired), or None with `back`.
            back: Move to the node the role came from.

        Returns:
            int: 0 when the target is the record with every ref, 1 otherwise.
        """
        from dreamference.forge.forge_mirrors import ForgeMirrors
        from dreamference.forge.forge_service import ForgeService

        if ForgeSettings.role() == ROLE_MIRROR and (
            back or name in HERE_NAMES or name == ForgeService.host_name()
        ):
            return cls.pull_here()
        if ForgeSettings.role() != ROLE_RECORD:
            print(
                "❌ This node is not the forge of record: run `forge move <node>` there, or `forge move here` "
                "(or `--back`) on the node that takes the role."
            )
            return 1
        target = cls.move_target(name, back)
        if target is None:
            return 1
        target_name = target.get("name") or target["node"]
        print(f"🧊 Freezing pushes and backing up for the move to {target_name}…")
        cls.freeze(True)
        folder = cls.send_set(target, target_name)
        if folder is None:
            return 1
        answer = cls.restore_on(target, folder)
        if not answer.get("ok"):
            cls.freeze(False)
            print(
                f"❌ The move stopped: {answer.get('error')}. This node is still the forge of record, unfrozen."
            )
            return 1
        cls.become_mirror(target["node"], target_name)
        ForgeMirrors.sync()
        print(
            f"✅ {target_name} is the forge of record; this node is a mirror of it. Its old data is kept beside "
            "the new (30 days for `forge move --back`)."
        )
        return 0

    @classmethod
    def move_target(cls, name: str | None, back: bool) -> dict[str, Any] | None:
        """Find the paired node a move goes to, saying why when there is none.

        Args:
            name: The target node, or None with `back`.
            back: Move to the node the role came from.

        Returns:
            dict[str, Any] | None: The target's pairing record, or None.
        """
        from dreamference.node.node_pairing import NodePairing

        if back:
            name = ForgeSettings.state().get("previous_record")
            if not name:
                print(
                    "❌ The role never moved here from another node; `forge move <node>` names one."
                )
                return None
        target = NodePairing.find(name or "")
        if target is None:
            print(f"❌ {name} is not a paired node (`ling-admin node add {name}` first).")
        return target

    @classmethod
    def send_set(cls, target: dict[str, Any], target_name: str) -> Path | None:
        """Take a fresh set and send it to the target and one more paired node.

        Pushes are unfrozen again when the set does not reach the target.

        Args:
            target: The target's pairing record.
            target_name: The target's name.

        Returns:
            Path | None: The set, or None when it was not taken or did not reach the target.
        """
        from dreamference.node.node_pairing import NodePairing

        folder = ForgeBackup.take()
        if folder is None:
            cls.freeze(False)
            return None
        others = [record for record in NodePairing.paired() if record["node"] != target["node"]]
        if not ForgeBackup.send(folder, target):
            cls.freeze(False)
            print(f"❌ The set did not reach {target_name}; nothing changed.")
            return None
        kept = [target_name] + [
            r.get("name") or r["node"] for r in others[:1] if ForgeBackup.send(folder, r)
        ]
        print(f"📦 {folder.name} is on {', '.join(kept)}. {target_name} restores it…")
        return folder

    @classmethod
    def restore_on(cls, target: dict[str, Any], folder: Path) -> dict[str, Any]:
        """Have the target restore the set and take the role.

        Args:
            target: The target's pairing record.
            folder: The set, already sent to the target.

        Returns:
            dict[str, Any]: The target's answer: `ok`, and `error` when it failed.
        """
        from dreamference.node.node_pairing import NodePairing

        result = NodePairing.run(target, f"forge-restore {folder.name} {ForgeBackup.node_id()}")
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {
                "ok": False,
                "error": (result.stderr or result.stdout).strip()[-300:] or "no answer",
            }

    @classmethod
    def pull_here(cls) -> int:
        """Pull the role from the forge of record to this mirror (`forge move here` / `--back`).

        For a node the record is not paired with but which is paired with the record: the record
        freezes and streams a fresh set back (`forge-hand-over`), this node restores it and checks
        every ref, then the record becomes a mirror of this node (`forge-become-mirror`). Anything
        wrong and the record is unfrozen (`forge-unfreeze`) and keeps the role.

        Returns:
            int: 0 when this node is the record with every ref, 1 otherwise.
        """
        from dreamference.forge.forge_mirrors import ForgeMirrors
        from dreamference.forge.forge_service import ForgeService
        from dreamference.node.node_pairing import NodePairing

        state = ForgeSettings.state()
        record = NodePairing.find(state.get("record_node") or "")
        if record is None:
            print(
                "❌ This node is not paired with the forge of record; run `forge move <this node>` there."
            )
            return 1
        record_name = record.get("name") or record["node"]
        print(f"🧊 {record_name} freezes pushes and hands a fresh backup over…")
        folder = cls.receive_hand_over(record)
        if folder is None:
            NodePairing.run(record, "forge-unfreeze")
            return 1
        result = cls.restore_here(folder, record["node"])
        if not result.get("ok"):
            print(
                f"❌ The move stopped: {result.get('error')}. {record_name} keeps the role, unfrozen."
            )
            NodePairing.run(record, "forge-unfreeze")
            cls.become_mirror(record["node"], record_name)
            return 1
        answer = NodePairing.run(
            record, f"forge-become-mirror {ForgeBackup.node_id()} {ForgeService.host_name()}"
        )
        if answer.returncode != 0:
            print(
                f"⚠️  {record_name} did not confirm it is a mirror now: {(answer.stderr or '').strip()[-300:]}"
            )
        ForgeMirrors.sync()
        print(f"✅ This node is the forge of record again; {record_name} is a mirror of it.")
        return 0

    @classmethod
    def receive_hand_over(cls, record: dict[str, Any]) -> Path | None:
        """Ask the record for a fresh set and store it with the received ones.

        Args:
            record: The record's pairing record.

        Returns:
            Path | None: The set, or None when none arrived.
        """
        import subprocess
        import tarfile
        import tempfile

        from dreamference.node.node_pairing import NodePairing

        try:
            command = NodePairing.ssh_command(record, "forge-hand-over")
        except LookupError as error:
            print(f"❌ {error}")
            return None
        root = ForgeBackup.received_dir()
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root) as spool:
            try:
                result = subprocess.run(
                    command,
                    stdout=spool,
                    stderr=subprocess.PIPE,
                    timeout=HAND_OVER_TIMEOUT_S,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError) as error:
                print(f"❌ The record did not hand a set over: {error}")
                return None
            if result.returncode != 0:
                print(
                    f"❌ The record did not hand a set over: {result.stderr.decode(errors='replace')[-300:]}"
                )
                return None
            spool.seek(0)
            try:
                with tarfile.open(fileobj=spool, mode="r") as archive:
                    name = archive.getnames()[0].split("/", 1)[0]
            except (tarfile.TarError, IndexError):
                print("❌ The record's answer was not a backup set.")
                return None
            spool.seek(0)
            if ForgeBackup.receive(name, spool) != 0:
                return None
        return root / name

    @classmethod
    def serve_hand_over(cls) -> int:
        """Freeze, take a set and stream it to standard output (the forced command's `forge-hand-over`).

        Returns:
            int: 0 when the set was written, 1 otherwise (and pushes unfrozen).
        """
        import subprocess

        if ForgeSettings.role() != ROLE_RECORD:
            sys.stderr.write("this node is not the forge of record\n")
            return 1
        cls.freeze(True)
        folder = ForgeBackup.take()
        if folder is None:
            cls.freeze(False)
            return 1
        sys.stdout.flush()
        packed = subprocess.run(
            ["tar", "-C", str(folder.parent), "-cf", "-", folder.name],
            stdout=sys.stdout.buffer,
            check=False,
        )
        return 0 if packed.returncode == 0 else 1

    @classmethod
    def serve_become_mirror(cls, node_id: str, name: str) -> int:
        """Become a mirror of the node that took the role (the forced command's `forge-become-mirror`).

        Args:
            node_id: The new record's node id.
            name: Its name.

        Returns:
            int: 0, or 2 when refused.
        """
        if not all(c.isalnum() or c in NAME_PUNCTUATION for c in node_id + name):
            sys.stderr.write("not a node id and name\n")
            return 2
        if ForgeSettings.role() != ROLE_RECORD or not cls.frozen_flag().exists():
            sys.stderr.write("refused: this node is not a frozen forge of record\n")
            return 2
        cls.become_mirror(node_id, name)
        return 0

    @classmethod
    def promote(cls, name: str | None) -> int:
        """Make the newest set a node holds the forge of record there (`forge promote <node>`).

        Args:
            name: A paired node, or None (or this machine's name) for this node.

        Returns:
            int: 0 when the node is the record with every ref of the set.
        """
        from dreamference.forge.forge_mirrors import ForgeMirrors
        from dreamference.forge.forge_service import ForgeService
        from dreamference.node.node_pairing import NodePairing

        if name in (None, "", "here", ForgeService.host_name()):
            folder = ForgeBackup.latest()
            if folder is None:
                print("❌ This node holds no backup set to promote from.")
                return 1
            print(f"⬆️  Promoting this node from {folder.name}…")
            result = cls.restore_here(folder, None)
        else:
            target = NodePairing.find(name or "")
            if target is None:
                print(f"❌ {name} is not a paired node.")
                return 1
            answer = NodePairing.run(target, "forge-promote")
            try:
                result = json.loads(answer.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                result = {
                    "ok": False,
                    "error": (answer.stderr or answer.stdout).strip()[-300:] or "no answer",
                }
        if not result.get("ok"):
            print(f"❌ Promotion failed: {result.get('error')}")
            return 1
        ForgeMirrors.sync()
        print(
            "✅ Promoted. Pushes made after that backup must be pushed again from the worktrees that made them."
        )
        return 0

    @classmethod
    def serve_promote(cls) -> int:
        """Promote this node from its newest set (the forced command's `forge-promote`).

        Returns:
            int: 0 when this node is now the record.
        """
        folder = ForgeBackup.latest()
        if folder is None:
            print(json.dumps({"ok": False, "error": "no backup set on this node"}))
            return 1
        result = cls.restore_here(folder, None)
        print(json.dumps(result))
        sys.stdout.flush()
        return 0 if result["ok"] else 1

    @classmethod
    def old_data(cls) -> list[Path]:
        """List the data folders a move or a restore set aside.

        Returns:
            list[Path]: Data folders a move or a restore set aside.
        """
        parent = ForgeSettings.data_dir().parent
        return sorted(p for p in parent.glob("data.*") if p.is_dir())

    @classmethod
    def prune_old_data(cls, days: int = 30) -> list[str]:
        """Remove set-aside data folders older than the `--back` window.

        Args:
            days: The window.

        Returns:
            list[str]: The folders removed.
        """
        removed = []
        for folder in cls.old_data():
            # A rename keeps the folder's own time, so the stamp in its name says when it went aside.
            stamp = re.search(r"(\d{8}-\d{6})$", folder.name)
            try:
                aside = calendar.timegm(time.strptime(stamp[1], "%Y%m%d-%H%M%S")) if stamp else None
            except ValueError:
                aside = None
            if time.time() - (aside or folder.stat().st_mtime) > days * 86400:
                shutil.rmtree(folder, ignore_errors=True)
                removed.append(folder.name)
        return removed
