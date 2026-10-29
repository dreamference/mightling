"""What the dependency audit of 2026-10-09 fixed or relied on stays true.

specs/DREAMFERENCE_MIGHTLING_DEPENDENCY_AUDIT.md raised the Python floors past their advisories
(§5.1), cleared the development virtualenv's leftovers as never imported (§5.3), left the Electron
build tooling's findings because none of it ships in the app (§6), and found every action pinned
(§7). A floor can be lowered, a runtime dependency added or a local action changed without any
other test noticing; these do.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SETUP_PY = ROOT / "setup.py"
WORKFLOWS = ROOT / ".github" / "workflows"
ELECTRON = ROOT / "desktop" / "electron"
MSVC_ACTION = "./codex/.github/actions/setup-msvc-env"

# The first release of each without a published advisory (§5.1).
AUDITED_FLOORS = {
    "requests": "2.33.0",
    "sentence-transformers": "5.6.0",
    "sqlite-vec": "0.1.3",
    "Pillow": "12.3.0",
}

# Packages of the development virtualenv with advisories that nothing may import (§5.3), by
# import name: gitpython is `git`, pyjwt is `jwt`.
LEFTOVER_IMPORTS = {"litellm", "git", "aiohttp", "django", "starlette", "jwt"}


def setup_requirements() -> list[str]:
    """Every string in setup.py's install_requires and extras, read without running setup.py."""
    tree = ast.parse(SETUP_PY.read_text())
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setup"
    )
    keywords = {k.arg: k.value for k in call.keywords}
    requirements = list(ast.literal_eval(keywords["install_requires"]))
    for extra in ast.literal_eval(keywords["extras_require"]).values():
        requirements.extend(extra)
    return requirements


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def test_no_audited_floor_falls_below_its_first_release_without_an_advisory() -> None:
    floors = {}
    for requirement in setup_requirements():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?>=([0-9.]+)", requirement)
        if match:
            floors[match.group(1)] = match.group(2)
    for package, audited in AUDITED_FLOORS.items():
        assert package in floors, f"{package} has no >= floor in setup.py"
        assert version_tuple(floors[package]) >= version_tuple(audited), package


def test_the_code_standard_tools_stay_pinned_exactly() -> None:
    pinned = [r for r in setup_requirements() if r.split("==")[0] in ("ruff", "mypy")]
    assert len(pinned) == 2
    assert all(re.fullmatch(r"[a-z]+==[0-9.]+", r) for r in pinned)


@pytest.mark.parametrize("folder", ["dreamference", "tests"])
def test_nothing_imports_the_virtualenvs_vulnerable_leftovers(folder: str) -> None:
    found = []
    for path in sorted((ROOT / folder).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            found += [
                f"{path.relative_to(ROOT)}:{node.lineno}: {name}"
                for name in names
                if name.split(".")[0] in LEFTOVER_IMPORTS
            ]
    assert found == []


def test_the_electron_app_ships_no_npm_package() -> None:
    # Vite bundles the app; Forge copies only `dependencies` into the package, so with none every
    # npm package, the build tooling's advisories among them, stays on the build machine.
    manifest = json.loads((ELECTRON / "package.json").read_text())
    assert not manifest.get("dependencies")
    packages = json.loads((ELECTRON / "package-lock.json").read_text())["packages"]
    shipped = [name for name, entry in packages.items() if name and not entry.get("dev")]
    assert shipped == []


def local_uses() -> list[str]:
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text().splitlines():
            match = re.match(r"\s*(?:-\s+)?uses:\s*(\./\S+)", line)
            if match:
                found.append(match.group(1))
    return found


def test_local_uses_are_the_repositorys_own_workflows_and_the_msvc_action_only() -> None:
    others = []
    for target in local_uses():
        workflow = re.fullmatch(r"\./\.github/workflows/([^/]+\.yml)", target)
        if workflow:
            assert (WORKFLOWS / workflow.group(1)).is_file(), target
        elif target != MSVC_ACTION:
            others.append(target)
    assert others == []
    assert MSVC_ACTION in local_uses()


@pytest.mark.skipif(
    not (ROOT / "codex" / "codex-rs").is_dir(), reason="codex submodule not checked out"
)
def test_the_msvc_action_runs_a_script_and_calls_no_other_action() -> None:
    action = yaml.safe_load((ROOT / MSVC_ACTION / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    steps = action["runs"]["steps"]
    assert steps and all("uses" not in step and "run" in step for step in steps)
