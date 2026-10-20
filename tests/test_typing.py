"""mypy's errors in `dreamference/` may only fall: the typing ratchet (PYTHON_QUALITY §3.8, §4).

mypy runs with the settings in `pyproject.toml` (`[tool.mypy]`, default strictness until Phase 4
raises one package at a time to `strict`); its errors, notes excluded, are counted per file and per
error code and compared with `tests/typing_baseline.json`, exactly as `tests/test_code_quality.py`
does for ruff. A count may fall, never rise, and a new file must have no error at all.

mypy keeps its incremental cache in `.mypy_cache/` at the checkout's root: the first run in a fresh
checkout takes about 15 s, every later one under a second.

Lowering the baseline after a fix:
    .venv/bin/python tests/test_typing.py --update-baseline
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Final

from test_code_quality import (
    REPO_ROOT,
    check_ratchet,
    pinned_tool_problem,
    require_tool,
    write_baseline,
)

BASELINE_PATH: Final[Path] = REPO_ROOT / "tests" / "typing_baseline.json"
# The pin in setup.py's "dev" extra; error codes and inference change between mypy releases.
MYPY_VERSION: Final[str] = "2.4.0"
UPDATE_HINT: Final[str] = ".venv/bin/python tests/test_typing.py --update-baseline"
FIX_HINT: Final[str] = "`.venv/bin/mypy` (from the checkout's root) shows each error with its line"


def mypy_counts(mypy: str) -> Counter:
    """Return mypy's errors counted by (relative path, error code)."""
    result = subprocess.run(
        [mypy, "--output", "json", "--no-error-summary"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    counts: Counter = Counter()
    for line in result.stdout.splitlines():
        if not line.startswith("{"):
            continue
        error = json.loads(line)
        if error["severity"] == "error":
            counts[(Path(error["file"]).as_posix(), error["code"] or "misc")] += 1
    if not counts and result.returncode != 0:
        raise RuntimeError(f"mypy failed without reporting an error:\n{result.stderr}")
    return counts


def test_no_mypy_error_count_rises_above_the_baseline():
    counts = mypy_counts(require_tool("mypy", MYPY_VERSION))
    check_ratchet(counts, BASELINE_PATH, "mypy", FIX_HINT, UPDATE_HINT)


def main(argv: list[str]) -> int:
    """Run `--update-baseline [--allow-raise]`; anything else prints this module's doc."""
    if "--update-baseline" not in argv:
        print(__doc__)
        return 0
    mypy, problem = pinned_tool_problem("mypy", MYPY_VERSION)
    if mypy is None:
        print(problem)
        return 1
    return write_baseline(
        mypy_counts(mypy),
        BASELINE_PATH,
        {"mypy": MYPY_VERSION},
        allow_raise="--allow-raise" in argv,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
