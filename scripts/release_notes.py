"""The GitHub release description of a version, taken from its notes spec.

``specs/DREAMFERENCE_RELEASE_<version>.md`` holds a status header, then the description between its
first two lines that are ``---`` alone (outside a code fence), then anything after (a checklist, for
example). The release workflow (``.github/workflows/release.yml``) publishes that text as the
release's description, so this script:

- extracts it;
- unwraps hard-wrapped paragraphs and list items into single lines, because GitHub renders every
  newline in a release body as a line break. Headings, tables, code fences, HTML comments and blank
  lines are kept as they are, and a list item's nesting (its indentation) is kept;
- refuses a description that is not finished: one that still contains ``{{TBD`` or a ``VARIANT``
  marker. The header before the first rule is not checked, since it describes those markers.

Usage::

    python3 scripts/release_notes.py specs/DREAMFERENCE_RELEASE_1.6.0.md
    python3 scripts/release_notes.py --version 1.6.0 [--out body.md]

Exit status 0 with the description on standard output (or in ``--out``); 1 with one line on
standard error naming what is wrong. Standard library only: the workflow runs it with the runner's
own ``python3``, before anything is installed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final, List, Optional

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
RULE: Final[str] = "---"
UNFINISHED_MARKERS: Final[tuple] = ("{{TBD", "VARIANT")
FENCE: Final[re.Pattern] = re.compile(r"^\s*(```|~~~)")
LIST_ITEM: Final[re.Pattern] = re.compile(r"^\s*([-*+]|(\d+)[.)])\s+")
STANDALONE: Final[re.Pattern] = re.compile(r"^\s*(#{1,6}\s|\||<!--|-->|\*\*\*\s*$|___\s*$)")


class ReleaseNotesError(Exception):
    """The notes spec is missing, has no description, or the description is not finished."""


def spec_path(version: str, root: Optional[Path] = None) -> Path:
    """The notes spec of a version.

    Args:
        version: The release version without the leading ``v`` (``1.6.0``, ``1.6.0-rc.1``).
        root: The repository root; this checkout by default.

    Returns:
        ``<root>/specs/DREAMFERENCE_RELEASE_<version>.md``; a pre-release names its own file.
    """
    return (root or ROOT) / "specs" / f"DREAMFERENCE_RELEASE_{version}.md"


def extract(text: str) -> List[str]:
    """The lines between the first two ``---`` rules outside a code fence.

    Args:
        text: The whole notes spec.

    Returns:
        The description's lines, without leading and trailing blank lines.

    Raises:
        ReleaseNotesError: Fewer than two rules, or nothing between them.
    """
    rules: List[int] = []
    in_fence = False
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and line.rstrip() == RULE:
            rules.append(number)
            if len(rules) == 2:
                break
    if len(rules) < 2:
        raise ReleaseNotesError(
            f"found {len(rules)} line(s) consisting of '---' alone; the description is the text "
            "between the first two"
        )
    body = lines[rules[0] + 1:rules[1]]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        raise ReleaseNotesError("the text between the first two '---' lines is empty")
    return body


def starts_item(line: str, block: Optional[str]) -> bool:
    """Whether a line starts a list item rather than continuing the block above it.

    Args:
        line: The line.
        block: The block it would continue, or None.

    Returns:
        True for a bullet; for a numbered item, when there is no block, the block is a list item
        or an indented paragraph inside one (the item is its sibling), or the number is 1.
    """
    match = LIST_ITEM.match(line)
    if not match:
        return False
    number = match.group(2)
    in_list = block is None or bool(LIST_ITEM.match(block)) or block[:1].isspace()
    return number is None or in_list or int(number) == 1


def unwrap(lines: List[str]) -> List[str]:
    """Join hard-wrapped paragraphs and list items into one line each.

    A line continues the block above it unless it is blank, starts a list item, is a heading, a
    table row, an HTML comment or a thematic break, or the line above ends in a Markdown hard break
    (two spaces or a backslash). Code fences are copied verbatim. As in CommonMark, a numbered item
    other than ``1.`` does not interrupt a paragraph, so a wrapped sentence whose next line begins
    with a figure (``100. That…``) stays one line.

    Args:
        lines: The description's lines.

    Returns:
        The unwrapped lines.
    """
    out: List[str] = []
    block: Optional[str] = None
    in_fence = False
    hard_break = False

    def flush() -> None:
        nonlocal block
        if block is not None:
            out.append(block)
            block = None

    for raw in lines:
        line = raw.rstrip()
        if in_fence or FENCE.match(line):
            flush()
            out.append(raw)
            if FENCE.match(line):
                in_fence = not in_fence
            hard_break = False
            continue
        if not line:
            flush()
            out.append("")
            hard_break = False
            continue
        if STANDALONE.match(line):
            flush()
            out.append(line)
            hard_break = False
            continue
        if block is None or hard_break or starts_item(line, block):
            flush()
            block = line
        else:
            block = f"{block} {line.strip()}"
        hard_break = raw.endswith("  ") or line.endswith("\\")
    flush()
    return out


def unfinished(lines: List[str]) -> Optional[str]:
    """The first marker of an unfinished description, if any.

    Args:
        lines: The description's lines.

    Returns:
        A message naming the marker and its line within the description, or None.
    """
    for number, line in enumerate(lines, start=1):
        for marker in UNFINISHED_MARKERS:
            if marker in line:
                return f"the description still contains '{marker}' (line {number} after the first '---'): fill or remove it"
    return None


def render(path: Path) -> str:
    """The release description of a notes spec, ready for ``gh release create --notes-file``.

    Args:
        path: The notes spec.

    Returns:
        The unwrapped description, ending in one newline.

    Raises:
        ReleaseNotesError: The spec is missing, has no description, or the description is unfinished.
    """
    if not path.is_file():
        raise ReleaseNotesError(f"{path} does not exist; write the version's release notes first")
    body = extract(path.read_text(encoding="utf-8"))
    problem = unfinished(body)
    if problem:
        raise ReleaseNotesError(f"{path}: {problem}")
    return "\n".join(unwrap(body)) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    """Print or write a version's release description.

    Args:
        argv: The command line without the program name.

    Returns:
        0 when the description was written, 1 when it is missing or unfinished.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("spec", nargs="?", type=Path, help="the notes spec")
    source.add_argument("--version", help="the version; reads specs/DREAMFERENCE_RELEASE_<version>.md")
    parser.add_argument("--out", type=Path, help="write the description here instead of standard output")
    args = parser.parse_args(argv)
    path = args.spec if args.spec else spec_path(args.version)
    try:
        notes = render(path)
    except ReleaseNotesError as error:
        print(f"release notes: {error}", file=sys.stderr)
        return 1
    if args.out:
        args.out.write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
