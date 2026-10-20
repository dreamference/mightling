"""The Python code standard may only get stricter: a ratchet over ruff's findings (PYTHON_QUALITY §4).

`ruff check` runs over `dreamference/` and `tests/` with the rules in `pyproject.toml`; its findings,
`ruff format --check` (one `ruff-format` finding per file the formatter would change) and the
project conventions no linter knows (§3.9, the `PQ` codes below) are counted per file and per rule
and compared with `tests/quality_baseline.json`. A count may fall, never rise, and a file missing
from the baseline (a new file) must have none at all, so a new file is formatted and meets every
rule from its first commit.

Most findings in new code are fixed by the tools themselves:
    .venv/bin/ruff format <file> && .venv/bin/ruff check --fix <file>

Lowering the baseline after a cleanup:
    .venv/bin/python tests/test_code_quality.py --update-baseline
It refuses to raise any count; `--allow-raise` exists for the rare reviewed exception and the commit
that uses it must say why. mypy has the same ratchet in `tests/test_typing.py`, which uses the
generic functions here.
"""

import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
BASELINE_PATH: Final[Path] = REPO_ROOT / "tests" / "quality_baseline.json"
SCOPE: Final[tuple[str, ...]] = ("dreamference", "tests")
# The pins in setup.py's "dev" extra. Rule sets change between releases, so a baseline is only
# comparable with the version that recorded it.
RUFF_VERSION: Final[str] = "0.16.10"
INSTALL_HINT: Final[str] = "install the pinned tools with: .venv/bin/pip install -e .[dev]"
UPDATE_HINT: Final[str] = ".venv/bin/python tests/test_code_quality.py --update-baseline"
FIX_HINT: Final[str] = (
    "`.venv/bin/ruff format <file> && .venv/bin/ruff check --fix <file>` fixes most of them "
    "(formatting, imports, `Optional`/`Dict`, the docstring summary line); "
    "`.venv/bin/ruff check <file>` shows the rest"
)
# The pseudo rule for a file `ruff format` would change. It is ratcheted per file like a rule
# because Phase 1 landed without formatting the tree (spec §11): files unformatted then are in the
# baseline, every other file must stay formatted.
FORMAT_CODE: Final[str] = "ruff-format"

# §3.9's exemption from one-class-per-file: modules staged into a container as a single file and
# run there as `__main__`, where a second module would have to be staged and kept in step.
SINGLE_FILE_CONTAINER_SCRIPTS: Final[frozenset[str]] = frozenset(
    {
        "dreamference/chat/image_search_service.py",  # dreamference-image-search, python:3-slim
        "dreamference/chat/gmail_search_service.py",  # dreamference-gmail, stdlib only
        "dreamference/vllm_server/diffusion_openai_service.py",  # bind-mounted entrypoint
    }
)


def tool_binary(name: str) -> str | None:
    """Return the tool beside this interpreter, else the one on PATH, else None."""
    beside = Path(sys.executable).parent / name
    return str(beside) if beside.is_file() else shutil.which(name)


def tool_version(binary: str) -> str:
    """Return the version a `<tool> --version` prints (`ruff 0.16.10`, `mypy 2.4.0 (compiled…)`)."""
    output = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.split()
    return output[1] if len(output) > 1 else ""


def pinned_tool_problem(name: str, pinned: str) -> tuple[str | None, str | None]:
    """Return (binary, None) for the pinned tool, or (None, why) when it is missing or another version."""
    binary = tool_binary(name)
    if binary is None:
        return None, f"{name} is not installed; {INSTALL_HINT}"
    version = tool_version(binary)
    if version != pinned:
        return None, f"{name} {version} is not the pinned {pinned}; {INSTALL_HINT}"
    return binary, None


def require_tool(name: str, pinned: str) -> str:
    """Return the pinned tool's path, or fail the test (never skip: §4) saying how to install it."""
    binary, problem = pinned_tool_problem(name, pinned)
    if binary is None:
        pytest.fail(problem)
    return binary


def _relative(filename: str) -> str:
    """Return a tool's (absolute or relative) file name relative to the checkout, POSIX style."""
    return (REPO_ROOT / filename).resolve().relative_to(REPO_ROOT).as_posix()


def ruff_counts(ruff: str) -> Counter:
    """Return ruff's findings counted by (relative path, rule code)."""
    result = subprocess.run(
        [ruff, "check", "--no-cache", "--output-format", "json", "--exit-zero", *SCOPE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    counts: Counter = Counter()
    for finding in json.loads(result.stdout):
        counts[(_relative(finding["filename"]), finding["code"] or "syntax-error")] += 1
    return counts


def format_counts(ruff: str) -> Counter:
    """Return one `ruff-format` finding per file the formatter would change."""
    result = subprocess.run(
        [ruff, "format", "--check", "--no-cache", "--output-format", "json", *SCOPE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    unformatted = {_relative(finding["filename"]) for finding in json.loads(result.stdout or "[]")}
    return Counter({(path, FORMAT_CODE): 1 for path in unformatted})


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
    allowed = (
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
    )
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
            names = {
                target.id
                for node in tree.body
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if "__all__" not in names:
                counts[(rel, "PQ002")] += 1
        counts.update((rel, "PQ003") for node in tree.body if _is_module_logic(node))
    return counts


def current_counts(ruff: str) -> Counter:
    """Return every finding the standard counts, by (relative path, code)."""
    return ruff_counts(ruff) + format_counts(ruff) + convention_counts()


def load_baseline(path: Path) -> dict[str, dict[str, int]]:
    """Return a committed baseline: {file: {code: count}}."""
    return json.loads(path.read_text(encoding="utf-8"))["counts"]


def rises(counts: Counter, baseline: dict[str, dict[str, int]]) -> list[str]:
    """Return one line per (file, rule) whose count exceeds the baseline (0 for a new file)."""
    return [
        f"{path}: {code} {baseline.get(path, {}).get(code, 0)} -> {count}"
        for (path, code), count in sorted(counts.items())
        if count > baseline.get(path, {}).get(code, 0)
    ]


def falls(counts: Counter, baseline: dict[str, dict[str, int]]) -> int:
    """Return how many findings the baseline still allows that the tree no longer has."""
    return sum(
        max(0, allowed - counts.get((path, code), 0))
        for path, codes in baseline.items()
        for code, allowed in codes.items()
    )


def check_ratchet(counts: Counter, baseline_path: Path, what: str, fix: str, update: str) -> None:
    """Fail on any count above the baseline; warn, with the update command, when counts fell.

    Args:
        counts: The tree's findings by (relative path, code).
        baseline_path: The committed baseline they are compared with.
        what: The checker's name in the messages ("the code standard", "mypy").
        fix: How to see or fix a risen finding.
        update: The command that lowers the baseline.
    """
    baseline = load_baseline(baseline_path)
    risen = rises(counts, baseline)
    assert not risen, (
        f"New findings against {what} (specs/DREAMFERENCE_PYTHON_QUALITY.md); a new file must "
        f"have none. {fix}.\n" + "\n".join(risen)
    )
    lowered = falls(counts, baseline)
    if lowered:
        # A warning, not a print: pytest -q shows warnings of passing tests, never their output.
        warnings.warn(
            f"{lowered} {what} findings fixed since the baseline; lock that in with: {update}",
            stacklevel=2,
        )


def write_baseline(
    counts: Counter, baseline_path: Path, tool: dict[str, str], allow_raise: bool
) -> int:
    """Write the counts as the baseline; refuse to raise one unless allowed.

    Args:
        counts: The tree's findings by (relative path, code).
        baseline_path: Where the baseline is written.
        tool: The pinned tool versions recorded beside the counts.
        allow_raise: Write even when a count is above the old baseline.

    Returns:
        The process exit status: 0 when written, 1 when refused.
    """
    if baseline_path.exists() and not allow_raise:
        risen = rises(counts, load_baseline(baseline_path))
        if risen:
            print(
                "Refusing to raise the baseline (fix these, or pass --allow-raise with a reason "
                "in the commit):\n" + "\n".join(risen)
            )
            return 1
    nested: dict[str, dict[str, int]] = {}
    for (path, code), count in sorted(counts.items()):
        nested.setdefault(path, {})[code] = count
    payload = {**tool, "total": sum(counts.values()), "counts": nested}
    baseline_path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"Baseline written to {baseline_path.name}: {payload['total']} findings "
        f"in {len(nested)} files."
    )
    return 0


def test_no_finding_count_rises_above_the_baseline():
    counts = current_counts(require_tool("ruff", RUFF_VERSION))
    check_ratchet(counts, BASELINE_PATH, "the code standard", FIX_HINT, UPDATE_HINT)


def test_the_ratchet_counts_a_new_file_from_zero():
    counts = Counter(
        {("dreamference/new_module.py", "D103"): 1, ("dreamference/old.py", "E501"): 2}
    )
    baseline = {"dreamference/old.py": {"E501": 3}}
    assert rises(counts, baseline) == ["dreamference/new_module.py: D103 0 -> 1"]
    assert falls(counts, baseline) == 1


def test_a_fall_warns_with_the_update_command_and_a_rise_fails(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"counts": {"dreamference/old.py": {"E501": 3}}}))
    with pytest.warns(UserWarning, match="--update-baseline"):
        check_ratchet(
            Counter({("dreamference/old.py", "E501"): 2}), baseline_path, "x", "fix", UPDATE_HINT
        )
    with pytest.raises(AssertionError, match=re.escape("dreamference/old.py: E501 3 -> 4")):
        check_ratchet(
            Counter({("dreamference/old.py", "E501"): 4}), baseline_path, "x", "fix", UPDATE_HINT
        )


def test_writing_the_baseline_refuses_a_raise_unless_allowed(tmp_path, capsys):
    baseline_path = tmp_path / "baseline.json"
    tool = {"ruff": RUFF_VERSION}
    assert write_baseline(Counter({("a.py", "E501"): 1}), baseline_path, tool, False) == 0
    assert write_baseline(Counter({("a.py", "E501"): 2}), baseline_path, tool, False) == 1
    assert "a.py: E501 1 -> 2" in capsys.readouterr().out
    assert write_baseline(Counter({("a.py", "E501"): 2}), baseline_path, tool, True) == 0
    assert load_baseline(baseline_path) == {"a.py": {"E501": 2}}


def test_module_logic_is_told_apart_from_constants_and_guards():
    source = (
        '"""Doc."""\nimport os\nX: int = int(os.environ.get("X", "1"))\n'
        "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
        'if __name__ == "__main__":\n    print(X)\n'
        "print(X)\nif X:\n    pass\n"
    )
    flagged = [type(node).__name__ for node in ast.parse(source).body if _is_module_logic(node)]
    assert flagged == ["Expr", "If"]


@pytest.mark.parametrize(
    "package",
    [
        "cli",
        "config",
        "context_engine",
        "hardware",
        "mcp_server",
        "runner",
        "vllm_server",
    ],
)
def test_each_subsystem_name_imports_its_package_not_a_same_named_module(package):
    # Phase 1 deleted the seven `dreamference/<package>.py` shims (`from … import *`), which the
    # packages of the same name shadowed; nothing may bring a module of that name back.
    # find_spec locates the module without running it (context_engine's imports take seconds).
    spec = importlib.util.find_spec(f"dreamference.{package}")
    assert spec is not None
    assert spec.origin == str(REPO_ROOT / "dreamference" / package / "__init__.py")
    assert not (REPO_ROOT / "dreamference" / f"{package}.py").exists()


def main(argv: list[str]) -> int:
    """Run `--update-baseline [--allow-raise]`; anything else prints this module's doc."""
    if "--update-baseline" not in argv:
        print(__doc__)
        return 0
    ruff, problem = pinned_tool_problem("ruff", RUFF_VERSION)
    if ruff is None:
        print(problem)
        return 1
    return write_baseline(
        current_counts(ruff),
        BASELINE_PATH,
        {"ruff": RUFF_VERSION},
        allow_raise="--allow-raise" in argv,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
