"""`ling-admin remote …` and `ling-admin node remote …`: the console side (REMOTE_ACCESS §3, §4).

The work is in `dreamference/remote/`, which returns its lines; this class prints them and asks
the one question `setup` and `remove` ask.
"""

import argparse
from collections.abc import Callable

from dreamference.remote import RemoteAccess, RemotePeers

EXIT_USAGE = 2
REMOTE_USAGE = "usage: ling-admin remote {setup,status,remove}"
NODE_REMOTE_USAGE = "usage: ling-admin node remote {code,peers,revoke}"


class RemoteCommand:
    """Parses and dispatches the remote-access subcommands."""

    @classmethod
    def add_remote_parser(cls, subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
        """Adds `ling-admin remote`.

        Args:
            subparsers: `ling-admin`'s subcommands.

        Returns:
            argparse.ArgumentParser: The group's parser.
        """
        parser = subparsers.add_parser(
            "remote",
            help="Reach this node from outside the LAN, by name, over a NetBird overlay it runs itself",
        )
        commands = parser.add_subparsers(dest="remote_command")
        setup = commands.add_parser(
            "setup", help="Set up (or reconcile) the control plane here and the relay box"
        )
        setup.add_argument("dns_name", help="The box's DNS name: the control plane's public name")
        setup.add_argument(
            "--relay-name", default="", help="The relay's DNS name (default: relay.<dns-name>)"
        )
        setup.add_argument("--yes", action="store_true", help="Do not ask before changing anything")
        setup.add_argument(
            "--dry-run", action="store_true", help="Print the plan and change nothing"
        )
        commands.add_parser(
            "status",
            help="Show the names, the certificates, the control plane, the tunnel and the peers",
        )
        remove = commands.add_parser(
            "remove", help="Take the tunnel and the box's half away; keep the CA and the peers"
        )
        remove.add_argument(
            "--purge", action="store_true", help="Also delete the CA and every peer"
        )
        remove.add_argument("--yes", action="store_true", help="Do not ask")
        return parser

    @classmethod
    def add_node_remote_parser(
        cls, node_subparsers: argparse._SubParsersAction
    ) -> argparse.ArgumentParser:
        """Adds `ling-admin node remote`.

        Args:
            node_subparsers: `ling-admin node`'s subcommands.

        Returns:
            argparse.ArgumentParser: The group's parser.
        """
        parser = node_subparsers.add_parser(
            "remote", help="Enrol clients on the overlay, list them, revoke one"
        )
        commands = parser.add_subparsers(dest="node_remote_command")
        commands.add_parser(
            "code",
            help="Print a one-time code for `ling node remote join` and wait for it, 10 minutes",
        )
        commands.add_parser(
            "peers", help="List the overlay's peers and when each was last connected"
        )
        revoke = commands.add_parser("revoke", help="Delete a peer: its key is forgotten at once")
        revoke.add_argument("peer", help="The peer's name, overlay label or id")
        return parser

    @classmethod
    def confirm(cls, question: str) -> bool:
        """Asks a yes/no question on the terminal.

        Args:
            question: The question.

        Returns:
            bool: True for an answer starting with y.
        """
        try:
            return input(f"{question} [y/N] ").strip().lower().startswith("y")
        except EOFError:
            return False

    @classmethod
    def dispatch_remote(cls, args: argparse.Namespace) -> int:
        """Runs `ling-admin remote …`.

        Args:
            args: The parsed arguments.

        Returns:
            int: The exit code.
        """
        actions: dict[str, Callable[[], int]] = {
            "setup": lambda: RemoteAccess.setup(
                args.dns_name, args.relay_name, args.yes, args.dry_run, print, cls.confirm
            ),
            "status": lambda: RemoteAccess.status(print),
            "remove": lambda: RemoteAccess.remove(args.purge, args.yes, print, cls.confirm),
        }
        action = actions.get(getattr(args, "remote_command", None) or "")
        if action is None:
            print(REMOTE_USAGE)
            return EXIT_USAGE
        return action()

    @classmethod
    def dispatch_node_remote(cls, args: argparse.Namespace) -> int:
        """Runs `ling-admin node remote …`.

        Args:
            args: The parsed arguments.

        Returns:
            int: The exit code.
        """
        actions: dict[str, Callable[[], int]] = {
            "code": lambda: RemotePeers.code(print),
            "peers": lambda: RemotePeers.peers(print),
            "revoke": lambda: RemotePeers.revoke(args.peer, print),
        }
        action = actions.get(getattr(args, "node_remote_command", None) or "")
        if action is None:
            print(NODE_REMOTE_USAGE)
            return EXIT_USAGE
        return action()
