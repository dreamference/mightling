"""Tests for scripts/rename_ragon.py: false friends, protected names, idempotence, the overlay guard."""

import hashlib
import importlib.util
import json
import subprocess
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
