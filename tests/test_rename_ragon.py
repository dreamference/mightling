"""Tests for scripts/rename_ragon.py: false friends, protected names, idempotence, the overlay guard."""

import hashlib
import importlib.util
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rename_ragon.py"
spec = importlib.util.spec_from_file_location("rename_ragon", SCRIPT)
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)


@pytest.mark.parametrize(
    "word",
    [
        "lingering",
        "linger",
        "enable-linger",
        "cross-lingual",
        "sampling_rate",
        "ceiling_value",
        "handling",
        "Kling",
        "english",
        "Bowling",
        "Ling-3.0",
        "mightling-release-key",
        "mightling-release",
        "mightling-adapter",
        ".ling-gpu",
        ".ling-build",
        "mightling-relay",
        "mightling-<id>.netbird.selfhosted",
        "puffin_version",
        "/puffin-images/",
    ],
)
def test_false_friends_and_protected_names_stay(word):
    assert rr.transform(f"x {word} y") == f"x {word} y"


@pytest.mark.parametrize(
    "old,new",
    [
        ("ling-admin codex build", "ragon-admin codex build"),
        ("ling-rs/src/lib.rs", "ragon-rs/src/lib.rs"),
        ("~/.mightling/config.toml", "~/.ragon/config.toml"),
        ("Mightling", "Ragon"),
        ("MIGHTLING_HOME", "RAGON_HOME"),
        ("DREAMFERENCE_MIGHTLING_LING_CLAUDE", "DREAMFERENCE_RAGON_CLAUDE"),
        ("DREAMFERENCE_LING_ENGINE_FOO", "DREAMFERENCE_RAGON_ENGINE_FOO"),
        ("LING_HOME", "RAGON_HOME"),
        ("ling_code", "ragon_code"),
        ("lingDocs", "ragonDocs"),
        ("LingServeLaunchBuilder", "RagonServeLaunchBuilder"),
        ("ling-serve", "ragon-serve"),
        (".lingignore", ".ragonignore"),
        ("puffin-codex", "ragon-codex"),
        ("PUFFIN_X", "RAGON_X"),
        ("run `ling` now", "run `ragon` now"),
    ],
)
def test_rules(old, new):
    assert rr.transform(old) == new


def test_links_in_excluded_files_follow_the_renamed_specs():
    text = "see DREAMFERENCE_MIGHTLING_LING_CLAUDE.md and DREAMFERENCE_LING_ENGINE_API.md and MIGHTLING.md"
    assert (
        rr.transform_links(text)
        == "see DREAMFERENCE_RAGON_CLAUDE.md and DREAMFERENCE_RAGON_ENGINE_API.md and MIGHTLING.md"
    )


def test_history_lines_in_specs_are_kept():
    text = "# T\nOn 2026-10-07 Mightling shipped.\nMightling does x.\n"
    out = rr.transform_text("specs/DREAMFERENCE_X.md", text)
    assert "On 2026-10-07 Mightling shipped." in out
    assert "Ragon does x." in out
    assert out.splitlines()[2] == rr.RENAME_NOTE


def test_code_lines_are_never_history():
    assert (
        rr.transform_text("a.py", "# legacy: ling-admin 2026-01-01\n")
        == "# legacy: ragon-admin 2026-01-01\n"
    )


def test_path_keep_and_rename():
    assert rr.new_path("ling-rs/src/lib.rs") == "ragon-rs/src/lib.rs"
    assert (
        rr.new_path("specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md")
        == "specs/DREAMFERENCE_RAGON_CLAUDE.md"
    )
    assert rr.new_path("scripts/rename_mightling.py") == "scripts/rename_mightling.py"
    assert (
        rr.new_path("specs/DREAMFERENCE_RENAME_MIGHTLING.md")
        == "specs/DREAMFERENCE_RENAME_MIGHTLING.md"
    )
    assert (
        rr.new_path("specs/DREAMFERENCE_RELEASE_1.8.0.md") == "specs/DREAMFERENCE_RELEASE_1.8.0.md"
    )


def git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def tree(tmp_path):
    files = {
        "ling-rs/src/lib.rs": '// ling-admin and Mightling, ling_code, lingering\nlet k = "mightling-release";\n',
        "specs/DREAMFERENCE_MIGHTLING_X.md": (
            "# X\nMightling on 2026-10-01.\nSee DREAMFERENCE_MIGHTLING_LING_CLAUDE.md\n"
        ),
        "specs/DREAMFERENCE_RENAME_RAGON.md": "Mightling stays here\n",
        "codex/a.rs": "ling Mightling\n",
        "README.md": "run `ling` (Mightling)\n",
    }
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_run_renames_and_is_idempotent(tree):
    assert rr.main(["--root", str(tree)]) == 0
    assert (tree / "ragon-rs/src/lib.rs").read_text() == (
        '// ragon-admin and Ragon, ragon_code, lingering\nlet k = "mightling-release";\n'
    )
    spec_new = (tree / "specs/DREAMFERENCE_RAGON_X.md").read_text()
    assert "Mightling on 2026-10-01." in spec_new and rr.RENAME_NOTE in spec_new
    assert "DREAMFERENCE_RAGON_CLAUDE.md" in spec_new
    assert (tree / "codex/a.rs").read_text() == "ling Mightling\n"
    assert (tree / "specs/DREAMFERENCE_RENAME_RAGON.md").read_text() == "Mightling stays here\n"
    assert rr.leftovers(str(tree)) == []
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "renamed")
    changed, renames = rr.run(str(tree), rr.tracked(str(tree)), True, None)
    assert changed == [] and renames == []


def test_dry_run_touches_nothing(tree):
    before = (tree / "README.md").read_text()
    assert rr.main(["--root", str(tree), "--dry"]) == 0
    assert (tree / "README.md").read_text() == before
    assert (tree / "ling-rs").exists()


def test_leftover_check_finds_old_names(tree):
    hits = rr.leftovers(str(tree))
    assert any("README.md" in h for h in hits)
    assert not any("codex/" in h for h in hits)
    assert not any("mightling-release" in h for h in hits)


def test_collision_aborts(tree):
    (tree / "ragon-rs/src").mkdir(parents=True)
    (tree / "ragon-rs/src/lib.rs").write_text("x")
    git(tree, "add", "-A")
    with pytest.raises(SystemExit):
        rr.run(str(tree), rr.tracked(str(tree)), False, None)


def test_overlay_hash_guard(tree):
    old = "README.md"
    overlay = tree / rr.OVERLAY_DIR
    (overlay / "docs").mkdir(parents=True)
    (overlay / "docs/new.md").write_text("new\n")
    digest = hashlib.sha256((tree / old).read_bytes()).hexdigest()
    manifest = {"docs/new.md": {"replaces": old, "sha256": digest}}
    (overlay / "manifest.json").write_text(json.dumps(manifest))
    assert rr.apply_overlay(str(tree), True) == ["docs/new.md"]
    assert rr.apply_overlay(str(tree), False) == ["docs/new.md"]
    assert (tree / "docs/new.md").read_text() == "new\n"
    assert not (tree / old).exists()
    # main changed the replaced file after the overlay was built
    (tree / old).write_text("changed on main\n")
    with pytest.raises(SystemExit):
        rr.apply_overlay(str(tree), False)
    assert Path(tree / "docs/new.md").exists()


def test_predecessor_files_and_the_webview_id():
    for path in (
        "desktop/electron/predecessor.json",
        "desktop/electron/windows/predecessor.nsh",
        "docs/dev/ragon-release/notes.md",
    ):
        assert rr.content_excluded(path)
    assert rr.transform('"dev.dreamference.mightling"') == '"dev.dreamference.ragon"'


@pytest.mark.parametrize(
    "old,new",
    [
        ("ling-code def foo", "ragon-code def foo"),
        ("run `ling-docs search`", "run `ragon-docs search`"),
        ("a\\nling-search", "a\\nragon-search"),
    ],
)
def test_the_tool_names_after_an_escape_or_a_backtick_are_renamed(old, new):
    assert rr.transform(old) == new


@pytest.mark.parametrize(
    "peer",
    [
        "mightling-1a2b3c4d-1111-2222-3333-444455556666",
        "mightling-ab.netbird.selfhosted",
    ],
)
def test_enrolled_peer_names_keep_the_old_prefix(peer):
    assert rr.transform(f"peer {peer} joined") == f"peer {peer} joined"
    assert rr.LEFTOVER.search(rr.masked(peer)) is None


def test_the_protected_words_do_not_count_as_leftovers():
    for word in (
        "mightling-release-key",
        "mightling-adapter",
        "still runs Mightling: `ling update` there",
    ):
        assert rr.LEFTOVER.search(rr.masked(word)) is None


def test_a_compat_edit_with_a_missing_anchor_aborts(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("nothing to anchor on\n")
    monkeypatch.setattr(rr, "COMPAT_EDITS", [("a.txt", "absent text", "x", 1)])
    with pytest.raises(SystemExit):
        rr.apply_compat(tmp_path, False)


def test_every_compat_edit_names_an_anchor_that_occurs_a_fixed_number_of_times():
    for entry in rr.COMPAT_EDITS:
        path, old, new, count = entry
        assert path and old and new != old and count >= 1


def test_a_compat_edit_is_applied_once_and_a_second_run_does_nothing(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("keep\nanchor\n")
    monkeypatch.setattr(rr, "COMPAT_EDITS", [("a.txt", "anchor\n", "anchor\nextra\n", 1)])
    assert rr.apply_compat(tmp_path, False) == 1
    assert (tmp_path / "a.txt").read_text() == "keep\nanchor\nextra\n"
    assert rr.apply_compat(tmp_path, False) == 0


def test_home_compat_edit_keeps_using_unmoved_home(tmp_path):
    home_rs = tmp_path / "ragon-rs/src/home.rs"
    home_rs.parent.mkdir(parents=True)
    home_rs.write_text(
        f"pub fn use_ragon_home() {{\n{rr.HOME_ANCHOR}        return;\n    }};\n}}\n"
    )
    assert rr.apply_compat(tmp_path, False) == 1
    assert "crate::rename::unmoved_home(&home)" in home_rs.read_text()
    assert rr.apply_compat(tmp_path, False) == 0


def test_a_compat_edit_matches_reformatted_rust_across_lines(tmp_path, monkeypatch):
    old = "    let x = f(a) else { return None; };\n"
    new = "    let x = g(a);\n"
    reformatted = (
        "        let x =\n            f(a)\n        else {\n            return None;\n        };\n"
    )
    (tmp_path / "a.rs").write_text(f"fn demo() {{\n{reformatted}}}\n")
    monkeypatch.setattr(rr, "COMPAT_EDITS", [("a.rs", old, new, 1)])
    assert rr.apply_compat(tmp_path, False) == 1
    assert (tmp_path / "a.rs").read_text() == f"fn demo() {{\n{new}}}\n"
    assert rr.apply_compat(tmp_path, False) == 0


def test_a_compat_edit_does_not_relax_layout_for_non_formatter_files(tmp_path, monkeypatch):
    old = "    let x = f(a) else { return None; };\n"
    reformatted = (
        "        let x =\n            f(a)\n        else {\n            return None;\n        };\n"
    )
    (tmp_path / "a.yml").write_text(reformatted)
    monkeypatch.setattr(rr, "COMPAT_EDITS", [("a.yml", old, "    let x = g(a);\n", 1)])
    with pytest.raises(SystemExit):
        rr.apply_compat(tmp_path, False)


def test_a_tolerant_insert_before_anchor_keeps_the_files_own_layout(tmp_path, monkeypatch):
    old = "    let x = f(a) else { return None; };\n"
    prefix = "    prep();\n"
    reformatted = (
        "        let x =\n            f(a)\n        else {\n            return None;\n        };\n"
    )
    (tmp_path / "a.rs").write_text(reformatted)
    monkeypatch.setattr(rr, "COMPAT_EDITS", [("a.rs", old, prefix + old, 1)])
    assert rr.apply_compat(tmp_path, False) == 1
    assert (tmp_path / "a.rs").read_text() == prefix + reformatted


def test_compat_abort_message_names_constant_or_index(tmp_path, monkeypatch):
    (tmp_path / "a.rs").write_text("present\n")
    monkeypatch.setattr(rr, "SLICE_ANCHOR", "absent\n")
    monkeypatch.setattr(rr, "COMPAT_EDITS", [("a.rs", rr.SLICE_ANCHOR, "x\n", 1)])
    with pytest.raises(SystemExit) as excinfo:
        rr.apply_compat(tmp_path, False)
    assert "SLICE_ANCHOR" in str(excinfo.value)

    monkeypatch.setattr(rr, "COMPAT_EDITS", [("a.rs", "inline absent\n", "x\n", 1)])
    with pytest.raises(SystemExit) as excinfo:
        rr.apply_compat(tmp_path, False)
    assert "COMPAT_EDITS[0]" in str(excinfo.value)


def test_real_compat_anchors_match_their_own_exact_layout():
    checked = 0
    for path, old, _new, _count in rr.COMPAT_EDITS:
        if path.endswith((".rs", ".py")):
            assert len(rr.anchor_pattern(old).findall(old)) == 1, path
            checked += 1
    assert checked > 0


# F9: the renamed sandbox check and host step replace the profile an older release installed.
REPO = SCRIPT.parent.parent
SANDBOX = "dreamference/vllm_server/sandbox_prerequisite.py"
HOST_SETUP = "dreamference/vllm_server/host_safety_setup.py"
ALL_COMPAT_EDITS = list(rr.COMPAT_EDITS)


def renamed_module(tmp_path, monkeypatch, relative, name):
    """The renamed copy of a real source file, compat edits applied, imported from tmp_path."""
    source = (REPO / relative).read_text(encoding="utf-8")
    if "LEGACY_PROFILE_NAMES" in (REPO / SANDBOX).read_text(encoding="utf-8"):
        pytest.skip("this tree is the renamed copy already; the script's input is the old tree")
    target = tmp_path / "tree" / rr.new_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rr.transform_text(relative, source), encoding="utf-8")
    monkeypatch.setattr(
        rr, "COMPAT_EDITS", [edit for edit in ALL_COMPAT_EDITS if edit[0] == relative]
    )
    rr.apply_compat(tmp_path / "tree", False)
    module_spec = importlib.util.spec_from_file_location(name, target)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


@pytest.fixture
def renamed_sandbox(tmp_path, monkeypatch):
    """The renamed SandboxPrerequisite whose AppArmor folder is tmp_path/apparmor and whose bwrap is fixed."""
    module = renamed_module(tmp_path, monkeypatch, SANDBOX, "ragon_sandbox_prerequisite")
    apparmor = tmp_path / "apparmor"
    apparmor.mkdir()
    bwrap = tmp_path / "bin" / "bwrap"
    bwrap.parent.mkdir()
    bwrap.write_text("")
    monkeypatch.setattr(module, "APPARMOR_DIR", str(apparmor))
    monkeypatch.setattr(module.shutil, "which", lambda name: str(bwrap))
    monkeypatch.setattr(
        module.SandboxPrerequisite, "_userns_restricted", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(
        module.SandboxPrerequisite,
        "staged_profile",
        classmethod(lambda cls, binary: tmp_path / "staged"),
    )
    module.bwrap = str(bwrap.resolve())
    module.apparmor = apparmor
    return module


def write_profile(module, name):
    path = module.apparmor / name
    path.write_text(f"profile {name} {module.bwrap} flags=(unconfined) {{}}\n")
    return str(path)


def test_the_renamed_sandbox_check_replaces_the_old_profile(renamed_sandbox):
    old = write_profile(renamed_sandbox, "puffin-bwrap")
    assert renamed_sandbox.PROFILE_NAME == "ragon-bwrap"
    assert renamed_sandbox.SandboxPrerequisite.fix_commands() == [
        ["apparmor_parser", "-R", old],
        ["rm", "-f", old],
        [
            "install",
            "-m",
            "644",
            "-o",
            "root",
            "-g",
            "root",
            str(renamed_sandbox.apparmor.parent / "staged"),
            "/etc/apparmor.d/ragon-bwrap",
        ],
        ["apparmor_parser", "-r", "/etc/apparmor.d/ragon-bwrap"],
    ]


def test_the_old_profile_is_not_a_third_party_profile_and_a_real_one_is_reloaded(renamed_sandbox):
    old = write_profile(renamed_sandbox, "puffin-bwrap")
    other = write_profile(renamed_sandbox, "other-bwrap")
    assert renamed_sandbox.SandboxPrerequisite.fix_commands() == [
        ["apparmor_parser", "-R", old],
        ["rm", "-f", old],
        ["apparmor_parser", "-r", other],
    ]


def test_without_an_old_profile_only_the_two_install_lines_remain(renamed_sandbox):
    commands = renamed_sandbox.SandboxPrerequisite.fix_commands()
    assert [command[0] for command in commands] == ["install", "apparmor_parser"]
    assert commands[1] == ["apparmor_parser", "-r", "/etc/apparmor.d/ragon-bwrap"]


@pytest.fixture
def renamed_host_setup(tmp_path, monkeypatch, renamed_sandbox):
    monkeypatch.setitem(
        __import__("sys").modules, "dreamference.vllm_server.sandbox_prerequisite", renamed_sandbox
    )
    module = renamed_module(tmp_path, monkeypatch, HOST_SETUP, "ragon_host_safety_setup")
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/bwrap")
    monkeypatch.setattr(module.HostSafetySetup, "sandbox_works", classmethod(lambda cls: True))
    return module


def test_the_host_step_replaces_a_profile_that_still_works(renamed_host_setup, renamed_sandbox):
    old = write_profile(renamed_sandbox, "puffin-bwrap")
    step = renamed_host_setup.HostSafetySetup._sandbox_step()
    assert step["commands"][:2] == [["apparmor_parser", "-R", old], ["rm", "-f", old]]
    assert step["commands"][2][0] == "install"
    assert step["commands"][-1] == ["apparmor_parser", "-r", "/etc/apparmor.d/ragon-bwrap"]


def test_the_host_step_has_nothing_to_do_without_an_old_profile(renamed_host_setup):
    assert renamed_host_setup.HostSafetySetup._sandbox_step() is None


def test_the_host_step_only_removes_when_userns_is_not_restricted(
    renamed_host_setup, renamed_sandbox, monkeypatch
):
    old = write_profile(renamed_sandbox, "puffin-bwrap")
    monkeypatch.setattr(
        renamed_sandbox.SandboxPrerequisite, "_userns_restricted", classmethod(lambda cls: False)
    )
    step = renamed_host_setup.HostSafetySetup._sandbox_step()
    assert step["commands"] == [["apparmor_parser", "-R", old], ["rm", "-f", old]]


# F11: the older asset names a 1.9.0 release adds, and a build that no longer adds them.
LINUX_TARGETS = ("aarch64-unknown-linux-gnu", "x86_64-unknown-linux-gnu")
OTHER_TARGETS = ("aarch64-apple-darwin", "x86_64-pc-windows-msvc")
SUMS_PROGRAMS = (
    "ragon",
    "ragon-search",
    "ragon-fetch",
    "ragon-code",
    "ragon-docs",
    "ragon-app",
    "ragon-signal",
)


def old_assets_script():
    """The shell body of release.yml's "Add the older asset names" step, dedented."""
    body = rr.OLD_ASSETS_STEP.split("        run: |\n", 1)[1]
    return textwrap.dedent(body)


def fake_artifacts(root):
    artifacts = root / "artifacts"
    artifacts.mkdir()
    for target in (*LINUX_TARGETS, *OTHER_TARGETS):
        (artifacts / f"ragon-{target}.gz").write_text(f"bytes {target}\n")
        lines = [f"{'a' * 64}  {program}-{target}.gz\n" for program in SUMS_PROGRAMS]
        lines.append(f"{'b' * 64}  codex-code-mode-host-{target}.gz\n")
        lines.append(f"{'c' * 64}  ragon-delegate-{target}.gz\n")
        (artifacts / f"ragon-{target}.sha256sums").write_text("".join(lines))
    for program in ("search", "fetch", "code", "docs"):
        (artifacts / f"ragon-{program}-{LINUX_TARGETS[0]}.gz").write_text(f"bytes {program}\n")
    return artifacts


def test_the_older_asset_step_lists_only_what_1_4_asks_for(tmp_path):
    artifacts = fake_artifacts(tmp_path)
    result = subprocess.run(
        ["bash", "-c", old_assets_script()], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    five = {"puffin", "puffin-search", "puffin-fetch", "puffin-code", "codex-code-mode-host"}
    for target in LINUX_TARGETS:
        listed = (artifacts / f"puffin-{target}.sha256sums").read_text().splitlines()
        assert {line.split("  ")[1].removesuffix(f"-{target}.gz") for line in listed} == five
    assert not list(artifacts.glob("puffin-*windows*")) and not list(
        artifacts.glob("puffin-*apple*")
    )
    assert (
        artifacts / f"puffin-{LINUX_TARGETS[0]}.gz"
    ).read_text() == f"bytes {LINUX_TARGETS[0]}\n"
    assert not (artifacts / f"puffin-docs-{LINUX_TARGETS[0]}.gz").exists()


def test_the_older_asset_step_leaves_the_ling_copies_whole(tmp_path):
    artifacts = fake_artifacts(tmp_path)
    subprocess.run(
        ["bash", "-c", old_assets_script()], cwd=tmp_path, check=True, capture_output=True
    )
    for target in (*LINUX_TARGETS, *OTHER_TARGETS):
        ragon = (artifacts / f"ragon-{target}.sha256sums").read_text()
        assert (artifacts / f"ling-{target}.sha256sums").read_text() == ragon.replace(
            "ragon", "ling"
        )
        assert (artifacts / f"ling-{target}.gz").read_bytes() == (
            artifacts / f"ragon-{target}.gz"
        ).read_bytes()


def test_the_renamed_build_workflow_no_longer_adds_puffin_assets(tmp_path, monkeypatch):
    relative = ".github/workflows/release-build.yml"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text(
        rr.transform_text(relative, (REPO / relative).read_text(encoding="utf-8")), encoding="utf-8"
    )
    monkeypatch.setattr(
        rr, "COMPAT_EDITS", [edit for edit in ALL_COMPAT_EDITS if edit[0] == relative]
    )
    rr.apply_compat(tmp_path, False)
    text = target.read_text(encoding="utf-8")
    assert "dist/puffin" not in text and "puffin-$target" not in text
    assert "release.yml adds the older asset names" in text


def test_format_clean_reads_which_files_ruff_would_reformat(tmp_path):
    # ruff 0.9 stopped printing `Would reformat: path`; reading only that, every file looked
    # clean and the rename reformatted files that were not clean before it.
    (tmp_path / "dreamference").mkdir()
    (tmp_path / "dreamference" / "clean.py").write_text("x = 1\n")
    (tmp_path / "dreamference" / "dirty.py").write_text("x=1\n")
    paths = ["dreamference/clean.py", "dreamference/dirty.py", "notes.md"]
    assert rr.format_clean(str(tmp_path), paths) == {"dreamference/clean.py"}


def test_tidy_sorts_the_imports_only_where_the_rename_unsorted_them(tmp_path):
    # A renamed name can sort before its neighbour; a block that was unsorted before the rename
    # is left alone, as the ratchet counts it.
    (tmp_path / "dreamference").mkdir()
    unsorted = "import os\nimport json\n\nprint(os, json)\n"
    (tmp_path / "dreamference" / "renamed.py").write_text(unsorted)
    (tmp_path / "dreamference" / "before.py").write_text(unsorted)
    clean = {"dreamference/renamed.py", "dreamference/before.py"}
    rr.tidy(str(tmp_path), clean, {"dreamference/before.py": 1})
    assert (
        (tmp_path / "dreamference" / "renamed.py")
        .read_text()
        .startswith("import json\nimport os\n")
    )
    assert (tmp_path / "dreamference" / "before.py").read_text() == unsorted
