"""
Generates docs/admin.md, the `puffin-admin` command reference on the product site.

The page is built from the real argparse parser, not typed by hand, so it cannot drift from the
CLI: rerun this after adding or changing a subcommand and commit the result.

    .venv/bin/python scripts/gen_admin_reference.py
"""

import argparse
import os
import sys
from typing import Final, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController  # noqa: E402

OUTPUT: Final[str] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "admin.md")

HEADER: Final[str] = """# `puffin-admin` reference

`puffin-admin` runs everything around the agent: the model server, the web chat, the desktop app,
models, and the mail commands the agent calls (`gmail`). The agent itself is `puffin`, and its web
commands are programs of their own, `puffin-search` and `puffin-fetch`; see [Terminal agent](puffin.md).

!!! note "Generated from the CLI"
    This page is generated from `puffin-admin`'s own argument parser by
    `scripts/gen_admin_reference.py`. Run `puffin-admin <command> --help` for the same text locally.

"""


def subparsers_of(parser: argparse.ArgumentParser) -> List[argparse._SubParsersAction]:
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


def option_rows(parser: argparse.ArgumentParser) -> List[str]:
    rows = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        name = ", ".join(f"`{s}`" for s in action.option_strings) or f"`{action.dest}`"
        text = (action.help or "").replace("|", "\\|").replace("\n", " ").strip()
        if text and not text.endswith((".", ")")):
            text += "."
        if action.choices and not isinstance(action.choices, dict):
            text += (" " if text else "") + "One of: " + ", ".join(f"`{c}`" for c in action.choices) + "."
        rows.append(f"| {name} | {text} |")
    return rows


def render(parser: argparse.ArgumentParser, path: List[str], depth: int, out: List[str]) -> None:
    for group in subparsers_of(parser):
        seen = set()
        helps = {a.dest: a.help for a in group._choices_actions}
        for name, sub in group.choices.items():
            if id(sub) in seen:  # aliases (e.g. `onyx` for `puffin`) point at the same parser
                continue
            seen.add(id(sub))
            aliases = [n for n, p in group.choices.items() if p is sub and n != name]
            full = path + [name]
            out.append(f"{'#' * min(depth, 4)} `puffin-admin {' '.join(full)}`\n")
            if helps.get(name):
                out.append(f"{helps[name].strip()}.\n" if not helps[name].strip().endswith(".") else f"{helps[name].strip()}\n")
            if aliases:
                out.append(f"Alias: {', '.join(f'`{a}`' for a in aliases)}.\n")
            rows = option_rows(sub)
            if rows:
                out.append("| Option | Description |\n|---|---|")
                out.extend(rows)
                out.append("")
            render(sub, full, depth + 1, out)


def generate() -> str:
    """Returns the reference page for the CLI as it is now."""
    parser = DreamferenceCLIController.build_parser()
    out: List[str] = [HEADER, "## Global options\n", "| Option | Description |\n|---|---|"]
    out.extend(option_rows(parser))
    out.append("\n## Commands\n")
    render(parser, [], 3, out)
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    with open(OUTPUT, "w") as handle:
        handle.write(generate())
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
