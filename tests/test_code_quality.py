"""The Python code standard may only get stricter: a ratchet over ruff's findings (PYTHON_QUALITY §4).

`ruff check` runs over `dreamference/` and `tests/` with the rules in `pyproject.toml`; its findings,
plus the project conventions no linter knows (§3.9, the `PQ` codes below), are counted per file and
per rule and compared with `tests/quality_baseline.json`. A count may fall, never rise, and a file
missing from the baseline (a new file) must have none at all.

Lowering the baseline after a cleanup:
    .venv/bin/python tests/test_code_quality.py --update-baseline
It refuses to raise any count; `--allow-raise` exists for the rare reviewed exception and the commit
that uses it must say why.
"""

import ast
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
BASELINE_PATH: Final[Path] = REPO_ROOT / "tests" / "quality_baseline.json"
SCOPE: Final[tuple[str, ...]] = ("dreamference", "tests")
# The pin in setup.py's "dev" extra. Rule sets change between ruff releases, so a baseline is only
# comparable with the version that recorded it.
RUFF_VERSION: Final[str] = "0.16.10"
INSTALL_HINT: Final[str] = "install the pinned tools with: .venv/bin/pip install -e .[dev]"
UPDATE_HINT: Final[str] = ".venv/bin/python tests/test_code_quality.py --update-baseline"

# §3.9's exemption from one-class-per-file: modules staged into a container as a single file and
# run there as `__main__`, where a second module would have to be staged and kept in step.
SINGLE_FILE_CONTAINER_SCRIPTS: Final[frozenset[str]] = frozenset({
    "dreamference/chat/image_search_service.py",  # dreamference-image-search, python:3-slim
    "dreamference/chat/gmail_search_service.py",  # dreamference-gmail, stdlib only
    "dreamference/vllm_server/diffusion_openai_service.py",  # bind-mounted entrypoint
})


def ruff_binary() -> str | None:
    """Return the ruff beside this interpreter, else the one on PATH, else None."""
    beside = Path(sys.executable).parent / "ruff"
    return str(beside) if beside.is_file() else shutil.which("ruff")


def ruff_counts(ruff: str) -> Counter:
    """Return ruff's findings counted by (relative path, rule code)."""
    result = subprocess.run(
        [ruff, "check", "--no-cache", "--output-format", "json", "--exit-zero", *SCOPE],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    counts: Counter = Counter()
    for finding in json.loads(result.stdout):
        path = Path(finding["filename"]).resolve().relative_to(REPO_ROOT).as_posix()
        counts[(path, finding["code"] or "syntax-error")] += 1
    return counts


def _is_main_guard_or_type_checking(node: ast.If) -> bool:
    """Return True for `if __name__ == "__main__":` and `if TYPE_CHECKING:` blocks."""
    test = ast.unparse(node.test)
    return test in ("__name__ == '__main__'", "TYPE_CHECKING", "typing.TYPE_CHECKING")


def _is_module_logic(node: ast.stmt) -> bool:
    """Return True for a top-level statement that does work at import time (§3.9).

    Imports, constants, definitions, the module docstring, `__all__`, an import guarded by
    `try`, and the `__main__`/`TYPE_CHECKING` blocks are not logic; a call, a loop, a `with`
    or any other `if` at module level is.
    """
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef,
               ast.AsyncFunctionDef, ast.ClassDef)
    if isinstance(node, allowed):
        return False
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return False  # a docstring or a bare string
    if isinstance(node, ast.If):
        return not _is_main_guard_or_type_checking(node)
    if isinstance(node, ast.Try):
        return any(_is_module_logic(child) for child in node.body)
    return True


def convention_counts() -> Counter:
    """Return §3.9's findings in `dreamference/`, counted by (relative path, PQ code).

    PQ001: a second (third, ...) class in one file. PQ002: an `__init__.py` without `__all__`.
    PQ003: a top-level statement that does work at import time.
    """
    counts: Counter = Counter()
    for path in sorted((REPO_ROOT / "dreamference").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        classes = sum(isinstance(node, ast.ClassDef) for node in tree.body)
        if classes > 1 and rel not in SINGLE_FILE_CONTAINER_SCRIPTS:
            counts[(rel, "PQ001")] += classes - 1
        if path.name == "__init__.py":
            names = {target.id for node in tree.body if isinstance(node, ast.Assign)
                     for target in node.targets if isinstance(target, ast.Name)}
            if "__all__" not in names:
                counts[(rel, "PQ002")] += 1
        counts.update((rel, "PQ003") for node in tree.body if _is_module_logic(node))
    return counts


def current_counts(ruff: str) -> Counter:
    """Return every finding the standard counts, by (relative path, code)."""
    return ruff_counts(ruff) + convention_counts()


def load_baseline() -> dict[str, dict[str, int]]:
    """Return the committed baseline: {path: {code: count}}."""
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["counts"]


def rises(counts: Counter, baseline: dict[str, dict[str, int]]) -> list[str]:
    """Return one line per (file, rule) whose count exceeds the baseline (0 for a new file)."""
    return [
        f"{path}: {code} {baseline.get(path, {}).get(code, 0)} -> {count}"
        for (path, code), count in sorted(counts.items())
        if count > baseline.get(path, {}).get(code, 0)
    ]


def falls(counts: Counter, baseline: dict[str, dict[str, int]]) -> int:
    """Return how many findings the baseline still allows that the tree no longer has."""
    return sum(max(0, allowed - counts.get((path, code), 0))
               for path, codes in baseline.items() for code, allowed in codes.items())


def _require_ruff() -> str:
    ruff = ruff_binary()
    if ruff is None:
        pytest.fail(f"ruff is not installed; {INSTALL_HINT}")
    version = subprocess.run([ruff, "--version"], capture_output=True, text=True).stdout.split()[-1]
    if version != RUFF_VERSION:
        pytest.fail(f"ruff {version} is not the pinned {RUFF_VERSION}; {INSTALL_HINT}")
    return ruff


def test_no_finding_count_rises_above_the_baseline():
    baseline = load_baseline()
    counts = current_counts(_require_ruff())
    risen = rises(counts, baseline)
    assert not risen, (
        "New findings against the code standard (specs/DREAMFERENCE_PYTHON_QUALITY.md). "
        "Fix them; a new file must have none. `ruff check <file>` shows each one.\n" + "\n".join(risen)
    )
    lowered = falls(counts, baseline)
    if lowered:
        print(f"{lowered} findings fixed since the baseline; lock that in with: {UPDATE_HINT}")


def test_the_ratchet_counts_a_new_file_from_zero():
    counts = Counter({("dreamference/new_module.py", "D103"): 1, ("dreamference/old.py", "E501"): 2})
    baseline = {"dreamference/old.py": {"E501": 3}}
    assert rises(counts, baseline) == ["dreamference/new_module.py: D103 0 -> 1"]
    assert falls(counts, baseline) == 1


def test_module_logic_is_told_apart_from_constants_and_guards():
    source = (
        '"""Doc."""\nimport os\nX: int = int(os.environ.get("X", "1"))\n'
        "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
        'if __name__ == "__main__":\n    print(X)\n'
        "print(X)\nif X:\n    pass\n"
    )
    flagged = [type(node).__name__ for node in ast.parse(source).body if _is_module_logic(node)]
    assert flagged == ["Expr", "If"]


def update_baseline(allow_raise: bool) -> int:
    """Write the current counts as the baseline; refuse to raise one unless allowed."""
    ruff = ruff_binary()
    if ruff is None:
        print(f"ruff is not installed; {INSTALL_HINT}")
        return 1
    counts = current_counts(ruff)
    if BASELINE_PATH.exists() and not allow_raise:
        risen = rises(counts, load_baseline())
        if risen:
            print("Refusing to raise the baseline (fix these, or pass --allow-raise with a reason "
                  "in the commit):\n" + "\n".join(risen))
            return 1
    nested: dict[str, dict[str, int]] = {}
    for (path, code), count in sorted(counts.items()):
        nested.setdefault(path, {})[code] = count
    payload = {"ruff": RUFF_VERSION, "total": sum(counts.values()), "counts": nested}
    BASELINE_PATH.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Baseline written: {payload['total']} findings in {len(nested)} files.")
    return 0


if __name__ == "__main__":
    if "--update-baseline" in sys.argv:
        sys.exit(update_baseline(allow_raise="--allow-raise" in sys.argv))
    print(__doc__)
