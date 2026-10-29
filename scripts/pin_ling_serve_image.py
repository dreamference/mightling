#!/usr/bin/env python3
"""Moves Mightling's pin of ling-serve's image: the engine commit, the image digest and the features.

    scripts/pin_ling_serve_image.py COMMIT DIGEST [--features kv-pages,sparse-head,paging-switch,fair-pairing] [--check]

COMMIT is the full ling-engine commit `scripts/build_ling_serve_image.sh` built the image from; DIGEST
is the `sha256:...` digest GHCR gave `docker push`. The script rewrites QWEN38_LING_ENGINE_COMMIT,
QWEN38_LING_IMAGE and QWEN38_LING_ENGINE_FEATURES in dreamference/hardware/model_matrix_registry.py
and nothing else, so the pin moves in one reviewable diff; the prose around the constants and the
tests that name the old commit (tests/test_ling_serve_engine.py) are updated by hand, as the checklist
in docs/dev/release-1.8.0.md says. `--check` validates the arguments and prints the diff without
writing. Never runs Docker or the network.
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

REGISTRY = (
    Path(__file__).resolve().parent.parent
    / "dreamference"
    / "hardware"
    / "model_matrix_registry.py"
)
KNOWN_FEATURES = ("kv-pages", "sparse-head", "paging-switch", "fair-pairing")


def repin(text: str, commit: str, digest: str, features: list[str]) -> str:
    """Returns the registry's text with the three pin constants replaced.

    Args:
        commit: The full 40-character ling-engine commit.
        digest: The image digest, `sha256:` and 64 hex characters.
        features: The engine features that commit has (a subset of KNOWN_FEATURES).

    Raises:
        ValueError: When an argument is malformed or a constant is not found exactly once.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("COMMIT must be the full 40-character lower-case commit")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("DIGEST must be sha256: and 64 lower-case hex characters")
    unknown = [f for f in features if f not in KNOWN_FEATURES]
    if unknown:
        raise ValueError(f"unknown feature(s) {unknown}; known: {', '.join(KNOWN_FEATURES)}")
    tuple_text = (
        "(" + ", ".join(f'"{f}"' for f in features) + ("," if len(features) == 1 else "") + ")"
    )
    edits = (
        (r'(QWEN38_LING_ENGINE_COMMIT: Final\[str\] = ")[0-9a-f]{40}(")', commit),
        (r'(ghcr\.io/dreamference/ling-serve@)sha256:[0-9a-f]{64}(")', digest),
        (r"(QWEN38_LING_ENGINE_FEATURES: Final\[tuple\[str, \.\.\.\]\] = )\([^)]*\)()", tuple_text),
    )
    for pattern, value in edits:
        text, n = re.subn(pattern, lambda m, v=value: m.group(1) + v + m.group(2), text)
        if n != 1:
            raise ValueError(f"expected one match of {pattern!r}, found {n}")
    return text


def main(argv: list[str]) -> int:
    """Runs the script; returns the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("commit")
    parser.add_argument("digest")
    parser.add_argument(
        "--features", default="", help="comma-separated engine features of that commit"
    )
    parser.add_argument("--check", action="store_true", help="print the diff, write nothing")
    parser.add_argument("--registry", type=Path, default=REGISTRY, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    old = args.registry.read_text()
    try:
        new = repin(old, args.commit, args.digest, [f for f in args.features.split(",") if f])
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    sys.stdout.writelines(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), "old", "new", n=0)
    )
    if not args.check:
        args.registry.write_text(new)
        print(
            "✅ pinned; now update the prose and the test constants (docs/dev/release-1.8.0.md, step 3)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
