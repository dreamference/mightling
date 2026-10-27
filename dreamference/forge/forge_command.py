"""`ling-admin forge` (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §7)."""

import argparse
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from dreamference.forge.forge_settings import (
    HTTP_PORT,
    OWNER,
    ROLE_MIRROR,
    ROLE_RECORD,
    ForgeSettings,
)

# `forge status` gives an age in seconds below two minutes, in minutes below two hours.
SECONDS_SHOWN_BELOW: Final[int] = 120
MINUTES_SHOWN_BELOW: Final[int] = 7200
SECONDS_PER_MINUTE: Final[int] = 60
SECONDS_PER_HOUR: Final[int] = 3600
# The exit code of a command line that names no known subcommand.
USAGE_EXIT: Final[int] = 2
# `forge key`'s only subcommand.
KEY_ADD: Final[str] = "add"
USAGE: Final[str] = (
    "usage: ling-admin forge {status,on,off,move,promote,backup,restore,key,github-mirror,password,"
    "import,archive,mirrors}"
)


class ForgeCommand:
    """The parser and the dispatch of `ling-admin forge`."""

    # The top-level command names this class parses and dispatches.
    COMMANDS: tuple[str, ...] = ("forge",)

    @classmethod
    def add_parser(cls, subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
        """Add the `forge` command and its subcommands to `ling-admin`'s parser.

        Args:
            subparsers: `ling-admin`'s subparsers.
        """
        parser = subparsers.add_parser(
            "forge",
            help="The node's Forgejo forge: status, on/off, move, promote, "
            "backups, keys, the GitHub mirror",
        )
        commands = parser.add_subparsers(dest="forge_command")
        commands.add_parser(
            "status",
            help="Role, repositories, mirror freshness, last backup, GitHub mirror, storage",
        )
        commands.add_parser("on", help="Turn the forge on (and set it up)")
        commands.add_parser("off", help="Stop the forge and its advertisement; the data is kept")
        move = commands.add_parser("move", help="Hand the forge of record to another (paired) node")
        move.add_argument(
            "node", nargs="?", default=None, help="The paired node that becomes the record"
        )
        move.add_argument(
            "--back", action="store_true", help="Move the role back to the node it came from"
        )
        promote = commands.add_parser(
            "promote",
            help="Make a node the forge of record from its newest backup (when the record is lost)",
        )
        promote.add_argument(
            "node", nargs="?", default=None, help="A paired node, or none for this one"
        )
        commands.add_parser("backup", help="Take a backup now and send it to two other nodes")
        restore = commands.add_parser(
            "restore", help="Check that the newest backup restores, every ref"
        )
        restore.add_argument(
            "--check",
            action="store_true",
            required=True,
            help="Restore into a scratch folder and compare refs (nothing running is touched)",
        )
        key = commands.add_parser("key", help="SSH keys of the owner account")
        key_commands = key.add_subparsers(dest="key_command")
        key_add = key_commands.add_parser("add", help="Add a public key to the owner account")
        key_add.add_argument("file", help="The public key file")
        github = commands.add_parser(
            "github-mirror", help="This fleet's mirror of every branch to GitHub"
        )
        github_group = github.add_mutually_exclusive_group(required=True)
        github_group.add_argument(
            "--all-branches",
            action="store_true",
            help="Mirror every branch and tag, never deleting on GitHub",
        )
        github_group.add_argument("--off", action="store_true", help="Stop mirroring")
        github_group.add_argument("--run", action="store_true", help=argparse.SUPPRESS)
        github.add_argument("--account", default=None, help="The `gh` account whose login pushes")
        password = commands.add_parser(
            "password", help="Print the owner's password again (this node's terminal only)"
        )
        password.add_argument("--show", action="store_true", required=True)
        importer = commands.add_parser(
            "import", help="Import a GitHub repository's branches and tags"
        )
        importer.add_argument("source", help="The GitHub repository's URL")
        importer.add_argument(
            "name", help="owner/name on the forge (an organisation is created when missing)"
        )
        importer.add_argument(
            "--anonymity",
            choices=("full", "denylist", "off"),
            default="off",
            help="The push hook's check (default off)",
        )
        importer.add_argument(
            "--exempt", action="append", default=[], help="A path the check skips (repeatable)"
        )
        importer.add_argument(
            "--mirror", action="store_true", help="Mirror the forge's branches back to the source"
        )
        archive = commands.add_parser(
            "archive",
            help="Import git bundles into a private, archived repository that is never mirrored",
        )
        archive.add_argument("name", help="owner/name on the forge")
        archive.add_argument("bundles", nargs="+", help="Bundle files")
        mirrors = commands.add_parser("mirrors", help="Mirror nodes")
        mirrors.add_argument(
            "mirrors_command",
            choices=("sync",),
            help="sync: hand the record's plan to every paired node",
        )
        commands.add_parser("run", help="(The forge's unit) run the container in the foreground")
        commands.add_parser(
            "tick", help="(The forge's timer) storage budget, hooks, GitHub and node mirrors"
        )

    @classmethod
    def handlers(cls) -> dict[str, Callable[[argparse.Namespace], int]]:
        """Map each subcommand to the classmethod that runs it.

        Returns:
            dict[str, Callable[[argparse.Namespace], int]]: The handler of each subcommand.
        """
        return {
            "status": cls.run_status,
            "on": cls.run_on,
            "off": cls.run_off,
            "run": cls.run_run,
            "tick": cls.run_tick,
            "move": cls.run_move,
            "promote": cls.run_promote,
            "backup": cls.run_backup,
            "restore": cls.run_restore,
            "key": cls.run_key,
            "github-mirror": cls.run_github_mirror,
            "password": cls.run_password,
            "import": cls.run_import,
            "archive": cls.run_archive,
            "mirrors": cls.run_mirrors,
        }

    @classmethod
    def dispatch(cls, args: argparse.Namespace) -> int:
        """Run the subcommand the arguments name.

        Args:
            args: The parsed arguments.

        Returns:
            int: The exit code.
        """
        handler = cls.handlers().get(args.forge_command)
        if handler is None:
            return cls.usage()
        return handler(args)

    @classmethod
    def usage(cls) -> int:
        """Print the subcommands `ling-admin forge` knows.

        Returns:
            int: The exit code of a command line without a known subcommand.
        """
        print(USAGE)
        return USAGE_EXIT

    @classmethod
    def run_status(cls, args: argparse.Namespace) -> int:
        """Print `forge status`'s report.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code, always 0.
        """
        print("\n".join(cls.status_lines()))
        return 0

    @classmethod
    def run_on(cls, args: argparse.Namespace) -> int:
        """Turn the forge on and set it up.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_service import ForgeService

        return ForgeService.on()

    @classmethod
    def run_off(cls, args: argparse.Namespace) -> int:
        """Stop the forge and its advertisement, keeping the data.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_service import ForgeService

        return ForgeService.off()

    @classmethod
    def run_run(cls, args: argparse.Namespace) -> int:
        """Run the container in the foreground, as the forge's unit does.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_service import ForgeService

        return ForgeService.run()

    @classmethod
    def run_tick(cls, args: argparse.Namespace) -> int:
        """Run the forge timer's periodic work.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_service import ForgeService

        return ForgeService.tick()

    @classmethod
    def run_move(cls, args: argparse.Namespace) -> int:
        """Hand the forge of record to another node, or back to the one it came from.

        Args:
            args: The parsed arguments: `node` or `back`, exactly one of them.

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_move import ForgeMove

        if bool(args.node) == bool(args.back):
            print("usage: ling-admin forge move <node> | --back")
            return USAGE_EXIT
        return ForgeMove.move(args.node, back=args.back)

    @classmethod
    def run_promote(cls, args: argparse.Namespace) -> int:
        """Make a node the forge of record from its newest backup.

        Args:
            args: The parsed arguments: `node`, or None for this node.

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_move import ForgeMove

        return ForgeMove.promote(args.node)

    @classmethod
    def run_backup(cls, args: argparse.Namespace) -> int:
        """Take a backup now and send it to other nodes.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_backup import ForgeBackup

        return ForgeBackup.run()

    @classmethod
    def run_restore(cls, args: argparse.Namespace) -> int:
        """Check that the newest backup restores every ref.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_backup import ForgeBackup

        return ForgeBackup.check()

    @classmethod
    def run_key(cls, args: argparse.Namespace) -> int:
        """Add a public key file to the owner account (`forge key add`).

        Args:
            args: The parsed arguments: `key_command` and `file`.

        Returns:
            int: The exit code.
        """
        if getattr(args, "key_command", None) != KEY_ADD:
            return cls.usage()
        from dreamference.forge.forge_accounts import ForgeAccounts

        try:
            text = Path(args.file).expanduser().read_text()
        except OSError as error:
            print(f"❌ {error}")
            return 1
        return 0 if ForgeAccounts.add_key(text, title=Path(args.file).name) else 1

    @classmethod
    def run_github_mirror(cls, args: argparse.Namespace) -> int:
        """Run the GitHub mirror, or turn it on or off.

        Args:
            args: The parsed arguments: `run`, `off` or `all_branches`, and `account`.

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_github_mirror import ForgeGithubMirror

        if args.run:
            return ForgeGithubMirror.run()
        if args.off:
            ForgeGithubMirror.save_settings(False, ForgeGithubMirror.settings()["account"])
            print("✅ The GitHub mirror is off; nothing on GitHub was changed.")
            return 0
        ForgeGithubMirror.save_settings(
            True, args.account or ForgeGithubMirror.settings()["account"]
        )
        print(
            "✅ Every branch and tag of the repositories with a GitHub destination is mirrored, never deleted "
            "there. Destinations: " + (", ".join(ForgeGithubMirror.destinations()) or "none yet")
        )
        return 0

    @classmethod
    def run_password(cls, args: argparse.Namespace) -> int:
        """Print the owner's password, on this node's own terminal only.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code.
        """
        if (
            not sys.stdin.isatty()
            or not sys.stdout.isatty()
            or os.environ.get("SSH_ORIGINAL_COMMAND")
        ):
            print("❌ The password is printed on this node's own terminal only.")
            return 1
        password = ForgeSettings.read_secret("owner-password")
        print(password or "❌ No owner password on this node (the forge was never set up here).")
        return 0 if password else 1

    @classmethod
    def run_import(cls, args: argparse.Namespace) -> int:
        """Import a GitHub repository's branches and tags.

        Args:
            args: The parsed arguments: `source`, `name`, `anonymity`, `exempt` and `mirror`.

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_repositories import ForgeRepositories

        imported = ForgeRepositories.import_github(
            args.source, args.name, args.anonymity, args.exempt, args.mirror
        )
        return 0 if imported else 1

    @classmethod
    def run_archive(cls, args: argparse.Namespace) -> int:
        """Import git bundles into a private, archived repository.

        Args:
            args: The parsed arguments: `name` and `bundles`.

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_repositories import ForgeRepositories

        imported = ForgeRepositories.import_bundles(
            [Path(b).expanduser() for b in args.bundles], args.name
        )
        return 0 if imported else 1

    @classmethod
    def run_mirrors(cls, args: argparse.Namespace) -> int:
        """Hand the record's plan to every paired node.

        Args:
            args: The parsed arguments (unused).

        Returns:
            int: The exit code.
        """
        from dreamference.forge.forge_mirrors import ForgeMirrors

        return ForgeMirrors.sync()

    @classmethod
    def age(cls, at: float | str | None) -> str:
        """Say how long ago a moment was, in words.

        Args:
            at: A UNIX time.

        Returns:
            str: How long ago, in words, or `never` when there is no time.
        """
        if at is None:
            return "never"
        try:
            seconds = time.time() - float(at)
        except (TypeError, ValueError):
            return "never"
        if seconds < SECONDS_SHOWN_BELOW:
            return f"{int(seconds)} s ago"
        if seconds < MINUTES_SHOWN_BELOW:
            return f"{int(seconds / SECONDS_PER_MINUTE)} min ago"
        return f"{seconds / SECONDS_PER_HOUR:.1f} h ago"

    @classmethod
    def status_lines(cls) -> list[str]:
        """Build `forge status`'s report.

        Returns:
            list[str]: The report's lines.
        """
        from dreamference.forge.forge_backup import ForgeBackup
        from dreamference.forge.forge_container import ForgeContainer

        state = ForgeSettings.state()
        role = state.get("role") or "not set up"
        running = ForgeContainer.running()
        lines = cls.header_lines(state, role, running)
        if running:
            lines += cls.repository_lines()
        if role == ROLE_RECORD:
            lines += cls.record_lines()
        received = ForgeBackup.latest()
        if received is not None and role != ROLE_RECORD:
            lines.append(f"Newest backup held here: {received.name}")
        lines += cls.storage_lines()
        if role == ROLE_RECORD and ForgeSettings.read_secret("owner-password"):
            lines.append(
                f"Owner account: `{OWNER}` (`ling-admin forge password --show` on this node)"
            )
        return lines

    @classmethod
    def header_lines(cls, state: dict[str, Any], role: str, running: bool) -> list[str]:
        """Build the status report's first lines: switch, role, address and freeze.

        Args:
            state: The forge's state file.
            role: This node's role, or `not set up`.
            running: Whether the container runs.

        Returns:
            list[str]: The lines.
        """
        from dreamference.forge.forge_move import ForgeMove
        from dreamference.forge.forge_service import ForgeService

        lines = [
            f"Forge: {'on' if ForgeSettings.enabled() else 'off'}, {role}, "
            f"{'running' if running else 'not running'} on http://{ForgeService.host_name()}.local:{HTTP_PORT}/"
        ]
        if role == ROLE_MIRROR:
            lines.append(
                f"Forge of record: {state.get('record_name') or state.get('record_node') or 'unknown'}"
            )
        if ForgeMove.frozen_flag().exists():
            lines.append("⚠️  Frozen: pushes are refused while the role moves.")
        return lines

    @classmethod
    def repository_lines(cls) -> list[str]:
        """Build one status line per repository on the running forge.

        Returns:
            list[str]: The lines, led by an error line when the repositories cannot be read.
        """
        from dreamference.forge.forge_repositories import ForgeRepositories

        lines = []
        try:
            repositories = ForgeRepositories.all()
        except RuntimeError as error:
            lines.append(f"Repositories: unreadable ({error})")
            repositories = []
        for repo in sorted(repositories, key=lambda r: r["full_name"]):
            kind = "private" if ForgeRepositories.never_mirrored(repo) else "shared"
            extra = ""
            if repo.get("mirror"):
                extra = f", pulled {cls.age(cls.parse_time(repo.get('mirror_updated')))}"
            settings = ForgeRepositories.settings(repo["full_name"])
            lines.append(
                f"  {repo['full_name']}: {kind}{', archived' if repo.get('archived') else ''}, "
                f"anonymity {settings['scope']}{', → ' + settings['github'] if settings['github'] else ''}"
                f"{extra}"
            )
        return lines

    @classmethod
    def record_lines(cls) -> list[str]:
        """Build the status lines only the forge of record has: last backup and GitHub mirror.

        Returns:
            list[str]: The lines.
        """
        from dreamference.forge.forge_backup import ForgeBackup
        from dreamference.forge.forge_github_mirror import ForgeGithubMirror

        backup = ForgeBackup.status()
        lines = [
            "Last backup: "
            + (
                f"{backup.get('set')} {cls.age(backup.get('at'))}, sent to "
                f"{', '.join(backup.get('sent_to', [])) or 'no node'}"
                f"{' ⚠️ ' + backup['error'] if backup.get('error') else ''}"
                if backup
                else "never"
            )
        ]
        if ForgeGithubMirror.settings()["enabled"]:
            summary = cls.github_summary(ForgeGithubMirror.status())
            lines.append(f"GitHub mirror: every branch, {summary}")
        else:
            lines.append("GitHub mirror: off")
        return lines

    @classmethod
    def github_summary(cls, last: dict[str, Any] | None) -> str:
        """Summarise the GitHub mirror's last run.

        Args:
            last: The last run's status, or None when it never ran.

        Returns:
            str: The summary: never run, stopped, or which repositories had problems.
        """
        if not last:
            return "last run never"
        if last.get("stopped"):
            return f"STOPPED {cls.age(last.get('at'))}: {last['stopped']}"
        failed = [n for n, r in last.get("repositories", {}).items() if not r.get("ok")]
        return f"last run {cls.age(last.get('at'))}" + (
            f", problems in {', '.join(failed)}" if failed else ", all mirrored"
        )

    @classmethod
    def storage_lines(cls) -> list[str]:
        """Build the status lines on the forge's storage and its warnings.

        Returns:
            list[str]: The lines.
        """
        from dreamference.forge.forge_storage import GB, ForgeStorage

        usage = ForgeStorage.usage()
        lines = [
            f"Storage: {usage['total'] / GB:.2f} GB (repositories {usage['repositories'] / GB:.2f}, "
            f"logs and artefacts {usage['logs_artefacts'] / GB:.2f}, dumps {usage['dumps'] / GB:.2f}); "
            f"{ForgeStorage.free_gb():.0f} GB free on the disk"
        ]
        lines += [f"⚠️  {warning}" for warning in ForgeStorage.warnings(usage)]
        return lines

    @classmethod
    def parse_time(cls, text: object) -> float | None:
        """Convert an API time to a UNIX time.

        Args:
            text: An RFC 3339 time from the API.

        Returns:
            float | None: The UNIX time, or None when the text is not a time.
        """
        from datetime import datetime

        try:
            return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
