"""`ling-admin adapter {list,install,remove,verify}` (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §3).

Adapters are a node's: they are installed where the model is served, and a client sees them
through the node's `/v1/models`. Only `install` reaches the network; `list`, `list --available`,
`verify` and `remove` read this machine alone.
"""

import argparse
import json
from typing import Final

from dreamference.config import DreamferenceConfig
from dreamference.hardware.adapter_catalog import AdapterCatalog
from dreamference.hardware.adapter_installer import AdapterInstaller
from dreamference.hardware.adapter_manifest import AdapterManifest
from dreamference.hardware.adapter_store import TRUST_THIRD_PARTY, AdapterStore
from dreamference.hardware.adapter_verifier import AdapterVerifier

ADAPTER_COMMAND: Final[str] = "adapter"

THIRD_PARTY_WARNING: Final[str] = """⚠️  This adapter is not signed by Dreamference.
    An adapter changes what the model writes: it can make the model suggest
    insecure code, ignore your instructions or leave out what it was asked to keep.
    Mightling will check that its files match its manifest and that it was made
    for the model this machine serves, and nothing else."""

RESTART_HINT: Final[str] = (
    "💡 The model server loads installed adapters at its next start: `ling-admin server start`. "
    "Then choose one per session with `ling --adapter {name}` or `/adapter {name}`."
)


class AdapterCommand:
    """Parses and runs the `adapter` group."""

    @classmethod
    def add_parser(
        cls, subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]"
    ) -> argparse.ArgumentParser:
        """Adds `adapter` and its subcommands.

        Args:
            subparsers: The top-level parser's subcommands.

        Returns:
            argparse.ArgumentParser: The group's parser.
        """
        parser = subparsers.add_parser(
            ADAPTER_COMMAND, help="Install and manage LoRA adapters for the served model"
        )
        commands = parser.add_subparsers(dest="adapter_command")
        listing = commands.add_parser("list", help="Show the installed adapters (no network)")
        listing.add_argument(
            "--available",
            action="store_true",
            help="Also list the adapters this release catalogues",
        )
        listing.add_argument("--json", action="store_true", help="Print JSON")
        install = commands.add_parser(
            "install",
            help="Download, verify and install an adapter (the only adapter step that uses the network)",
        )
        install.add_argument(
            "name",
            nargs="?",
            default=None,
            help="A catalogued adapter, as <name> or <name>@<version>",
        )
        install.add_argument(
            "--repo",
            default=None,
            help="An adapter published after this release: <org>/<repo>@<commit>",
        )
        install.add_argument(
            "--third-party",
            action="store_true",
            help="Install an adapter that is not signed by Dreamference (asks you to type its name)",
        )
        install.add_argument(
            "--hf-token", default=None, help="A Hugging Face token, for a private repository"
        )
        remove = commands.add_parser("remove", help="Remove an installed adapter and its files")
        remove.add_argument("name", help="The installed adapter")
        verify = commands.add_parser(
            "verify", help="Check installed adapters again: signature, files and the served base"
        )
        verify.add_argument("name", nargs="?", default=None, help="One adapter (default: all)")
        return parser

    @classmethod
    def run(cls, args: argparse.Namespace, config: DreamferenceConfig) -> int:
        """Runs one subcommand.

        Args:
            args: The parsed arguments.
            config: The configuration (the model server's address, the configured model).

        Returns:
            int: The exit code.
        """
        from dreamference.node.node_identity import NodeIdentity

        if NodeIdentity.read() is None:
            print(
                f"💡 Adapters are installed on the node that serves the model ({config.vllm_host}): "
                f"run `ling-admin adapter {args.adapter_command}` there. This machine sees the node's "
                "adapters in its /v1/models and needs nothing installed."
            )
            return 1
        base = AdapterVerifier.served_base(config.vllm_host, config.model)
        handlers = {
            "list": cls._list,
            "install": cls._install,
            "remove": cls._remove,
            "verify": cls._verify,
        }
        return handlers[args.adapter_command](args, base)

    @classmethod
    def _list(cls, args: argparse.Namespace, base: dict[str, str] | None) -> int:
        """`adapter list [--available] [--json]`."""
        installed = AdapterStore.entries()
        rows = [
            {
                **{
                    key: entry.get(key)
                    for key in (
                        "name",
                        "version",
                        "display_name",
                        "repo",
                        "commit",
                        "rank",
                        "trust",
                    )
                },
                "base": entry.get("base_repo"),
                "base_matches": base is not None
                and (entry.get("base_repo"), entry.get("base_revision"))
                == (base["repo"], base["revision"]),
            }
            for _, entry in sorted(installed.items())
        ]
        available = AdapterCatalog.available() if args.available else []
        if args.json:
            print(
                json.dumps(
                    {"installed": rows, "available": available, "base": base}, indent=2, default=str
                )
            )
            return 0
        served = f"{base['key']} ({base['source']})" if base else "no model of the matrix"
        print(f"Served base: {served}")
        if not rows:
            print("No adapters installed.")
        for row in rows:
            flag = " ⚠️  third-party" if row["trust"] == TRUST_THIRD_PARTY else ""
            match = "" if row["base_matches"] else "  base mismatch"
            print(
                f"  {row['name']} {row['version']}  {row['display_name']}  rank {row['rank']}  "
                f"{row['repo']}@{str(row['commit'])[:12]}{flag}{match}"
            )
        if args.available:
            print(
                "Catalogued in this release:"
                if available
                else "This release catalogues no adapters yet."
            )
            for entry in available:
                mark = "installed" if entry["name"] in installed else "not installed"
                print(
                    f"  {entry['name']} {entry.get('version')}  {entry.get('display_name')}  "
                    f"for {entry.get('base_alias')}  ({mark})"
                )
        return 0

    @classmethod
    def _install(cls, args: argparse.Namespace, base: dict[str, str] | None) -> int:
        """`adapter install <name>[@<version>] | --repo <org>/<repo>@<commit> [--third-party]`."""
        if (args.name is None) == (args.repo is None):
            print("❌ Name a catalogued adapter or give --repo <org>/<repo>@<commit>, not both.")
            return 2
        if args.third_party and args.repo is None:
            print(
                "❌ --third-party needs --repo <org>/<repo>@<commit>: the catalogue lists Dreamference's adapters only."
            )
            return 2
        confirm = cls._confirm if args.third_party else None
        try:
            entry = AdapterInstaller.install(
                args.name, args.repo, base, confirm=confirm, token=args.hf_token
            )
        except ValueError as error:
            print(f"❌ {error}. Nothing was installed.")
            return 1
        flag = (
            " (third-party)" if entry["trust"] == TRUST_THIRD_PARTY else ", signed by Dreamference"
        )
        print(
            f"✅ Installed {entry['display_name']} {entry['version']} as `{entry['name']}`{flag}."
        )
        print(RESTART_HINT.format(name=entry["name"]))
        return 0

    @classmethod
    def _confirm(cls, manifest: AdapterManifest) -> bool:
        """The typed confirmation of a third-party adapter: its name, at a terminal, no `--yes`."""
        print(THIRD_PARTY_WARNING)
        print(
            f"    It is {manifest.display_name} ({manifest.name} {manifest.version}): {manifest.description}"
        )
        try:
            typed = input("    Type the adapter's name to install it: ")
        except EOFError:
            return False
        return typed.strip() == manifest.name

    @classmethod
    def _remove(cls, args: argparse.Namespace, base: dict[str, str] | None) -> int:
        """`adapter remove <name>`."""
        if not AdapterInstaller.remove(args.name):
            print(f"❌ No adapter named {args.name} is installed (`ling-admin adapter list`).")
            return 1
        print(
            f"✅ Removed {args.name}. A session using it is refused at its next request; "
            "`/adapter off` or a new session returns to the base model."
        )
        return 0

    @classmethod
    def _verify(cls, args: argparse.Namespace, base: dict[str, str] | None) -> int:
        """`adapter verify [<name>]`."""
        entries = AdapterStore.entries()
        if args.name is not None:
            if args.name not in entries:
                print(f"❌ No adapter named {args.name} is installed.")
                return 1
            entries = {args.name: entries[args.name]}
        if not entries:
            print("No adapters installed.")
            return 0
        failed = 0
        for name, entry in sorted(entries.items()):
            problem = AdapterInstaller.verify(entry, base)
            if problem:
                failed += 1
                print(f"❌ {name}: {problem}")
            else:
                print(f"✅ {name}: signature, files and base check out")
        return 1 if failed else 0
