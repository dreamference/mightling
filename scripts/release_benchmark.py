r"""A release's published benchmark entry (``docs/releases/<version>.json``) and the Benchmarks page.

Only numbers tied to a release are published; nightly results stay internal. The release step
records a release's entry from the night report that qualified it, and the night is named
explicitly by id (``--night``), never guessed. The page ``docs/benchmarks.md`` is generated from the
entries, so it never holds a number the entries do not.

Usage::

    python3 scripts/release_benchmark.py record --version 1.8.0 --night 21 --tasks 500 \\
        --solved 330 --previous-version 1.7.2 --previous-solved 322 --wall-hours 11.5 [--p-value 0.31]
    python3 scripts/release_benchmark.py render          # rewrite docs/benchmarks.md
    python3 scripts/release_benchmark.py check [--version 1.8.0]   # entries valid, page in sync

Standard library only. Exit 0 on success; 1 with one line on standard error otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
RELEASES_DIR: Final[str] = "docs/releases"
PAGE: Final[str] = "docs/benchmarks.md"
VERSION_RE: Final[re.Pattern] = re.compile(r"^\d+\.\d+\.\d+([-.][0-9A-Za-z.]+)?$")
FIELDS: Final[tuple] = (
    "version",
    "benchmark",
    "night",
    "tasks",
    "solved",
    "previous_version",
    "previous_solved",
    "p_value",
    "wall_hours",
)
CAVEAT: Final[str] = (
    "Measured on our own GB10 with our harness; it compares configurations, it is not a leaderboard score."
)
# What must never reach a published page: a path, an address, an account or a host name.
FORBIDDEN: Final[re.Pattern] = re.compile(
    r"/home/|/Users/|@|\b\d{1,3}(\.\d{1,3}){3}\b|\.local\b|puffin|gx10", re.IGNORECASE
)
HEADER: Final[str] = """# Benchmarks

Each release's result on SWE-bench Verified, against the default of the release before it: the
tasks solved out of the tasks run, the p-value of the paired comparison where there is one, and the
wall time of the run. Only numbers tied to a release are published.

!!! note "How to read these numbers"
    {caveat}

"""


class BenchmarkError(Exception):
    """An entry is missing a field, inconsistent, or carries something that must not be published."""


def entries(root: Optional[Path] = None) -> list[dict]:
    """Every release's entry, newest version first.

    Args:
        root: The repository root.

    Returns:
        List[dict]: The validated entries.
    """
    root = root or ROOT
    found = [
        validate(json.loads(p.read_text(encoding="utf-8")), p.stem)
        for p in (root / RELEASES_DIR).glob("*.json")
    ]
    return sorted(
        found,
        key=lambda e: [int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", e["version"])],
        reverse=True,
    )


def validate(entry: dict, stem: str | None = None) -> dict:
    """Checks one entry.

    Args:
        entry: The parsed JSON.
        stem: The file's name without ``.json``, which must be the entry's version.

    Returns:
        dict: The entry.

    Raises:
        BenchmarkError: Naming what is wrong.
    """
    missing = [f for f in FIELDS if f not in entry]
    extra = [f for f in entry if f not in FIELDS]
    if missing or extra:
        raise BenchmarkError(
            f"entry {stem or entry.get('version')}: missing {missing}, unknown {extra}"
        )
    if not VERSION_RE.match(str(entry["version"])) or (stem and stem != entry["version"]):
        raise BenchmarkError(
            f"entry {stem}: the version {entry['version']!r} does not match the file name"
        )
    if entry["night"] in (None, "") or not re.fullmatch(r"[0-9A-Za-z._-]+", str(entry["night"])):
        raise BenchmarkError(f"entry {entry['version']}: the qualifying night must be given by id")
    tasks = entry["tasks"]
    for key in ("solved", "previous_solved"):
        if not (
            isinstance(entry[key], int) and isinstance(tasks, int) and 0 <= entry[key] <= tasks
        ):
            raise BenchmarkError(
                f"entry {entry['version']}: {key} must be an integer from 0 to tasks"
            )
    p_value = entry["p_value"]
    if p_value is not None and not (isinstance(p_value, (int, float)) and 0 <= p_value <= 1):
        raise BenchmarkError(f"entry {entry['version']}: p_value is null or a number from 0 to 1")
    if not (isinstance(entry["wall_hours"], (int, float)) and entry["wall_hours"] > 0):
        raise BenchmarkError(f"entry {entry['version']}: wall_hours must be positive")
    if not VERSION_RE.match(str(entry["previous_version"])):
        raise BenchmarkError(f"entry {entry['version']}: previous_version is not a version")
    if FORBIDDEN.search(json.dumps(entry)):
        raise BenchmarkError(
            f"entry {entry['version']}: carries a path, address, account or host name"
        )
    return entry


def render(items: list[dict]) -> str:
    """The Benchmarks page for the entries.

    Args:
        items: The validated entries, newest first.

    Returns:
        str: The page's Markdown.
    """
    text = HEADER.format(caveat=CAVEAT)
    if not items:
        return text + "No release has a published result yet.\n"
    text += "| Release | Benchmark | Solved | Previous default | Difference | p-value | Wall time |\n| --- | --- | --- | --- | --- | --- | --- |\n"
    for e in items:
        p = "not available" if e["p_value"] is None else f"{e['p_value']:.3f}"
        text += (
            f"| {e['version']} | {e['benchmark']} | {e['solved']} of {e['tasks']} | "
            f"{e['previous_version']}: {e['previous_solved']} of {e['tasks']} | "
            f"{e['solved'] - e['previous_solved']:+d} | {p} | {e['wall_hours']:g} h |\n"
        )
    return text


def main(argv: list[str] | None = None) -> int:
    """Runs the command line.

    Args:
        argv: The arguments; ``sys.argv`` by default.

    Returns:
        int: The exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--version", required=True)
    rec.add_argument("--night", required=True, help="the qualifying night's id, given explicitly")
    rec.add_argument("--benchmark", default="SWE-bench Verified")
    rec.add_argument("--tasks", type=int, required=True)
    rec.add_argument("--solved", type=int, required=True)
    rec.add_argument("--previous-version", required=True)
    rec.add_argument("--previous-solved", type=int, required=True)
    rec.add_argument("--p-value", type=float)
    rec.add_argument("--wall-hours", type=float, required=True)
    sub.add_parser("render")
    chk = sub.add_parser("check")
    chk.add_argument("--version")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "record":
            entry = validate(
                {
                    "version": args.version,
                    "benchmark": args.benchmark,
                    "night": args.night,
                    "tasks": args.tasks,
                    "solved": args.solved,
                    "previous_version": args.previous_version,
                    "previous_solved": args.previous_solved,
                    "p_value": args.p_value,
                    "wall_hours": args.wall_hours,
                },
                args.version,
            )
            (ROOT / RELEASES_DIR).mkdir(exist_ok=True)
            (ROOT / RELEASES_DIR / f"{args.version}.json").write_text(
                json.dumps(entry, indent=2) + "\n", encoding="utf-8"
            )
            (ROOT / PAGE).write_text(render(entries()), encoding="utf-8")
        elif args.cmd == "render":
            (ROOT / PAGE).write_text(render(entries()), encoding="utf-8")
        else:
            items = entries()
            if (ROOT / PAGE).read_text(encoding="utf-8") != render(items):
                raise BenchmarkError(f"{PAGE} is not what the entries render to; run `render`")
            if args.version and not any(e["version"] == args.version for e in items):
                raise BenchmarkError(f"no benchmark entry for {args.version}")
    except (BenchmarkError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
