"""Codex's own tests run on Mightling's patched export, never the submodule, minus a reasoned skip list."""

import glob
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from dreamference.runner import codex_test_runner as runner_module
from dreamference.runner.codex_branded_builder import CODEX_SUBMODULE_DIR
from dreamference.runner.codex_test_runner import CodexTestRunner

SUBMODULE_PRESENT = pathlib.Path(os.path.join(CODEX_SUBMODULE_DIR, "codex-rs")).is_dir()


def _workspace_packages() -> dict:
    """Maps each Codex workspace package name to its directory, read from the submodule's manifests."""
    packages = {}
    for manifest in glob.glob(
        os.path.join(CODEX_SUBMODULE_DIR, "codex-rs", "**", "Cargo.toml"), recursive=True
    ):
        with pathlib.Path(manifest).open() as handle:
            match = re.search(r'^\[package\]\s*\nname = "([^"]+)"', handle.read(), re.M)
        if match:
            packages[match.group(1)] = os.path.dirname(manifest)
    return packages


def test_every_skip_carries_a_reason():
    skips = CodexTestRunner.load_skips()
    entries = [entry for kind in skips.values() for entry in kind]
    assert entries
    assert all(len(entry["reason"].strip()) > 20 for entry in entries)


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_every_skipped_package_and_target_exists_in_the_pinned_codex():
    # A skip that names nothing would silently stop applying after a rename upstream.
    packages = _workspace_packages()
    skips = CodexTestRunner.load_skips()
    for entry in skips["package"]:
        assert entry["name"] in packages
    for entry in skips["target"]:
        assert pathlib.Path(os.path.join(packages[entry["package"]], "tests", f"{entry['test']}.rs")).is_file()


def test_a_skip_without_a_reason_is_refused(tmp_path):
    path = tmp_path / "skips.toml"
    path.write_text('[[test]]\nfilter = "test(=x)"\n')
    with pytest.raises(ValueError, match="reason"):
        CodexTestRunner.load_skips(str(path))


def test_the_filterset_narrows_to_the_callers_filter_and_leaves_the_skips_out():
    skips = {
        "test": [{"filter": "test(=a)", "reason": "r"}, {"filter": "package(b)", "reason": "r"}]
    }
    assert CodexTestRunner.filterset(skips) == "not ((test(=a)) | (package(b)))"
    assert (
        CodexTestRunner.filterset(skips, "package(c)")
        == "(package(c)) & not ((test(=a)) | (package(b)))"
    )
    assert CodexTestRunner.filterset({"test": []}) is None


def test_binaries_are_built_first_and_a_package_with_a_skipped_target_runs_on_its_own():
    skips = {
        "package": [{"name": "codex-voice-host", "reason": "r"}],
        "target": [{"package": "codex-code-mode-host", "test": "stdio", "reason": "r"}],
        "test": [],
    }
    with patch.object(CodexTestRunner, "test_targets", return_value=["grpc", "stdio", "grpc_tcp"]):
        commands = CodexTestRunner.commands(skips, "/x/codex-rs", {}, None, 8)
    build, workspace, package = commands
    assert build[:2] == ["cargo", "build"] and "--bins" in build
    assert build[
        build.index("--exclude") : build.index("--exclude") + 2
    ] == ["--exclude", "codex-voice-host"]
    assert "codex-code-mode-host" in workspace and "codex-voice-host" in workspace
    assert package[package.index("-p") + 1] == "codex-code-mode-host"
    assert "stdio" not in package and package[-4:-2] == ["--test", "grpc"]
    # The launcher is built beside it (its vendored OpenSSL), but only that package's tests run.
    assert package[package.index("-p") + 2 : package.index("-p") + 4] == ["-p", "ling-launcher"]
    assert package[package.index("-E") + 1] == "(package(codex-code-mode-host))"


def test_the_tests_see_an_empty_home_and_no_model_server(tmp_path):
    with patch.dict(
        os.environ,
        {
            "RUST_BACKTRACE": "1",
            "CODEX_HOME": "/home/x/.mightling",
            "DREAMFERENCE_CONFIG_PATH": "/home/x/dreamference.toml",
        },
    ):
        environment = CodexTestRunner.environment(
            {"RUSTY_V8_ARCHIVE": "a"}, "/tools", 6, str(tmp_path)
        )
    assert environment["HOME"] == runner_module.TEST_HOME_DIR
    assert environment["TMPDIR"] == str(tmp_path)
    # The launcher steps aside, and a test that looked for a model server anyway finds a closed port.
    assert environment["MIGHTLING_UPSTREAM_TESTS"] == "1"
    assert environment["DREAMFERENCE_VLLM_HOST"] == "http://127.0.0.1:9"
    assert "CODEX_HOME" not in environment and "DREAMFERENCE_CONFIG_PATH" not in environment
    assert environment["DREAMFERENCE_MIGHTLING_DOCS"] == "0"
    assert "RUST_BACKTRACE" not in environment and environment["RUST_LIB_BACKTRACE"] == "0"
    assert environment["PATH"].startswith("/tools")
    assert environment["RUSTY_V8_ARCHIVE"] == "a"


def test_the_tests_start_on_the_base_whatever_the_users_default_adapter(tmp_path):
    # specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §10.4: set, and empty, next to the model host.
    with patch.dict(os.environ, {"DREAMFERENCE_MIGHTLING_ADAPTER": "rust"}):
        environment = CodexTestRunner.environment({}, "/tools", 6, str(tmp_path))
    assert environment["DREAMFERENCE_MIGHTLING_ADAPTER"] == ""


def test_the_export_is_put_back_at_the_version_upstreams_tests_expect(tmp_path):
    manifest = tmp_path / "codex-rs" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text(
        '[workspace.package]\nversion = "0.158.0"\n\n[dependencies]\nx = { version = "1.2.3" }\n'
    )
    CodexTestRunner.reset_workspace_version(str(tmp_path))
    text = manifest.read_text()
    assert '[workspace.package]\nversion = "0.0.0"' in text and 'version = "1.2.3"' in text


def test_the_run_tests_the_export_and_never_the_submodule(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "TEST_SOURCE_DIR", str(tmp_path / "test-src"))
    monkeypatch.setattr(runner_module, "TEST_HOME_DIR", str(tmp_path / "test-home"))
    monkeypatch.setattr(runner_module, "BUILD_CACHE_DIR", str(tmp_path))
    (tmp_path / "test-src" / "codex-rs").mkdir(parents=True)
    (tmp_path / "test-src" / "codex-rs" / "Cargo.toml").write_text(
        '[workspace.package]\nversion = "1"\n'
    )
    calls = []

    def fake_call(command, cwd=None, env=None):
        calls.append((command, cwd, env))
        return 100 if "nextest" in command else 0

    with (
        patch(
            "dreamference.runner.codex_test_runner.DesktopInstaller.install_rust", return_value=True
        ),
        patch.object(runner_module.CodexBrandedBuilder, "prepare_source", return_value=True),
        patch.object(runner_module.CodexBrandedBuilder, "fetch_rusty_v8", return_value={}),
        patch.object(CodexTestRunner, "ensure_nextest", return_value="/tools"),
        patch.object(CodexTestRunner, "test_targets", return_value=["grpc"]),
        patch.object(CodexTestRunner, "has_disk_space", return_value=True),
        patch.object(CodexTestRunner, "apply_test_overlay", return_value=True) as overlay,
        patch("subprocess.call", side_effect=fake_call),
    ):
        assert CodexTestRunner.run() == 100
    overlay.assert_called_once_with(str(tmp_path / "test-src"))
    assert len(calls) == 3
    for command, cwd, env in calls:
        assert cwd == str(tmp_path / "test-src" / "codex-rs")
        assert not cwd.startswith(CODEX_SUBMODULE_DIR)
        assert env["MIGHTLING_UPSTREAM_TESTS"] == "1"
    # The private temporary directory is removed afterwards.
    assert not pathlib.Path(calls[0][2]["TMPDIR"]).exists()


def test_a_run_without_room_for_the_build_does_not_start(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "BUILD_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(runner_module, "TEST_TARGET_DIR", str(tmp_path / "test-target"))
    with patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(0, 0, 5 * 2**30)):
        assert not CodexTestRunner.has_disk_space()
    # An earlier build counts toward what is needed: Cargo reuses or replaces it.
    (tmp_path / "test-target").mkdir()
    with pathlib.Path(tmp_path / "test-target" / "big").open("wb") as handle:
        handle.truncate(38 * 2**30)
    with patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(0, 0, 5 * 2**30)):
        assert CodexTestRunner.has_disk_space()
    with (
        patch(
            "dreamference.runner.codex_test_runner.DesktopInstaller.install_rust", return_value=True
        ),
        patch.object(CodexTestRunner, "has_disk_space", return_value=False),
        patch("subprocess.call") as call,
    ):
        assert CodexTestRunner.run() == 1
    call.assert_not_called()


def test_the_temporary_directory_name_has_no_path_separators_the_tests_decode(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    path = CodexTestRunner.make_temp_dir()
    assert os.path.dirname(path) == str(tmp_path)
    assert re.fullmatch(r"pct[a-z0-9]{11}", os.path.basename(path))
    assert os.stat(path).st_mode & 0o777 == 0o700


def test_a_nextest_download_that_fails_its_checksum_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "TOOLS_DIR", str(tmp_path))

    def fake_retrieve(url, path):
        with pathlib.Path(path).open("wb") as handle:
            handle.write(b"not the release")

    with (
        patch.object(
            runner_module.CodexBrandedBuilder,
            "host_target",
            return_value="aarch64-unknown-linux-gnu",
        ),
        patch.object(runner_module.CodexBrandedBuilder, "download", side_effect=fake_retrieve),
    ):
        assert CodexTestRunner.ensure_nextest() is None
    assert not (tmp_path / "cargo-nextest").exists()


BOXED_UPSTREAM = """---
source: tui/src/history_cell/tests.rs
expression: rendered
---
╭───────────────────────────────────────╮
│ >_ OpenAI Codex (v0.0.0)              │
│ model:     gpt-5   /model to change   │
╰───────────────────────────────────────╯
› Ask Codex to do anything
"""


def test_a_snapshot_that_only_renames_codex_is_accepted():
    ling = BOXED_UPSTREAM.replace(
        "OpenAI Codex (v0.0.0)      ", "Mightling (v0.0.0)            "
    ).replace("Ask Codex", "Ask Mightling")
    assert CodexTestRunner.differs_only_by_name(BOXED_UPSTREAM, ling)


def test_a_snapshot_with_any_other_change_is_refused():
    renamed = BOXED_UPSTREAM.replace("OpenAI Codex", "Mightling").replace(
        "Ask Codex", "Ask Mightling"
    )
    # A layout regression: a row of content gone, a word changed, a symbol changed.
    assert not CodexTestRunner.differs_only_by_name(
        BOXED_UPSTREAM, renamed.replace("│ model:     gpt-5   /model to change   │\n", "")
    )
    assert not CodexTestRunner.differs_only_by_name(
        BOXED_UPSTREAM, renamed.replace("gpt-5", "gpt-6")
    )
    assert not CodexTestRunner.differs_only_by_name(BOXED_UPSTREAM, renamed.replace("›", ">"))


def test_the_overlay_replaces_upstream_snapshots_and_applies_the_test_patches(
    tmp_path, monkeypatch
):
    export = tmp_path / "export"
    snap = export / "codex-rs" / "tui" / "src" / "snapshots" / "header.snap"
    snap.parent.mkdir(parents=True)
    snap.write_text(BOXED_UPSTREAM)
    test_file = export / "codex-rs" / "tui" / "tests" / "suite.rs"
    test_file.parent.mkdir(parents=True)
    test_file.write_text('wait_for_screen("Ask Codex to do anything");\n')
    subprocess.run(["git", "init", "-q"], cwd=export, check=True)

    overlay = tmp_path / "snapshots"
    (overlay / "tui" / "src" / "snapshots").mkdir(parents=True)
    (overlay / "tui" / "src" / "snapshots" / "header.snap").write_text("ling's\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001-tests.patch").write_text(
        "--- a/codex-rs/tui/tests/suite.rs\n+++ b/codex-rs/tui/tests/suite.rs\n@@ -1 +1 @@\n"
        '-wait_for_screen("Ask Codex to do anything");\n+wait_for_screen("Ask Mightling to do anything");\n'
    )
    monkeypatch.setattr(runner_module, "SNAPSHOT_OVERLAY_DIR", str(overlay))
    monkeypatch.setattr(runner_module, "TEST_PATCHES_DIR", str(patches))
    assert CodexTestRunner.apply_test_overlay(str(export))
    assert snap.read_text() == "ling's\n"
    assert "Ask Mightling" in test_file.read_text()


def _cells(text: str) -> int:
    """How many terminal cells a line takes: two for a wide character, one for any other."""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _box_widths(lines: list[str]) -> list[list[int]]:
    """The widths of each ╭…╰ box's rows, up to and including its right border."""
    boxes = []
    for index, line in enumerate(lines):
        if "╭" not in line:
            continue
        start, widths = line.index("╭"), []
        for row in lines[index:]:
            if len(row) <= start or row[start] not in "╭│╰":
                break
            segment = row[start:]
            end = max(segment.rfind("╮"), segment.rfind("│"), segment.rfind("╯"))
            widths.append(_cells(segment[: end + 1]))
            if row[start] == "╰":
                break
        boxes.append(widths)
    return boxes


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_the_overlay_snapshots_keep_upstreams_widths():
    # The Puffin -> Mightling rename replaced the name in place and left Puffin's padding: 68
    # snapshots had a row three cells wider than the terminal or a box with one row sticking out,
    # and `slash_side_requests_forked_side_question_while_task_running` failed on it (1.7.0 tests).
    wrong = []
    for path in glob.glob(
        os.path.join(runner_module.SNAPSHOT_OVERLAY_DIR, "**", "*.snap"), recursive=True
    ):
        relative = os.path.relpath(path, runner_module.SNAPSHOT_OVERLAY_DIR)
        with pathlib.Path(path).open(encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        with pathlib.Path(os.path.join(CODEX_SUBMODULE_DIR, "codex-rs", relative)).open(encoding="utf-8") as handle:
            upstream = handle.read().split("\n")
        rows = {_cells(line) for line in lines if len(line) > 1 and line[0] == line[-1] == '"'}
        upstream_rows = {
            _cells(line) for line in upstream if len(line) > 1 and line[0] == line[-1] == '"'
        }
        if rows != upstream_rows:
            wrong.append(f"{relative}: rows {sorted(rows)}, upstream {sorted(upstream_rows)}")
        wrong += [
            f"{relative}: box rows {widths}"
            for widths in _box_widths(lines)
            if len(set(widths)) > 1
        ]
    assert wrong == []


def test_an_overlay_snapshot_with_no_upstream_original_stops_the_run(tmp_path, monkeypatch):
    (tmp_path / "export" / "codex-rs").mkdir(parents=True)
    overlay = tmp_path / "snapshots"
    (overlay / "tui").mkdir(parents=True)
    (overlay / "tui" / "removed_upstream.snap").write_text("stale\n")
    monkeypatch.setattr(runner_module, "SNAPSHOT_OVERLAY_DIR", str(overlay))
    monkeypatch.setattr(runner_module, "TEST_PATCHES_DIR", str(tmp_path / "none"))
    assert not CodexTestRunner.apply_test_overlay(str(tmp_path / "export"))


def test_insta_never_rewrites_snapshots_in_an_ordinary_run(monkeypatch):
    monkeypatch.setenv("INSTA_UPDATE", "always")
    monkeypatch.setenv("INSTA_FORCE_PASS", "1")
    environment = CodexTestRunner.environment({}, "/tools", 1, "/tmp/x")
    assert environment["INSTA_UPDATE"] == "no"
    assert "INSTA_FORCE_PASS" not in environment
    accepting = CodexTestRunner.environment({}, "/tools", 1, "/tmp/x", accept_snapshots=True)
    assert accepting["INSTA_UPDATE"] == "always" and accepting["INSTA_FORCE_PASS"] == "1"


def test_the_shipped_test_patches_touch_only_test_files():
    for name in os.listdir(runner_module.TEST_PATCHES_DIR):
        with pathlib.Path(os.path.join(runner_module.TEST_PATCHES_DIR, name)).open() as handle:
            paths = re.findall(r"^\+\+\+ b/(\S+)", handle.read(), flags=re.M)
        assert paths, name
        for path in paths:
            assert "/tests/" in path or path.endswith("tests.rs"), path


def test_project_markers_in_the_temporary_root_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    assert CodexTestRunner.stray_project_markers() == []
    (tmp_path / ".git").mkdir()
    assert CodexTestRunner.stray_project_markers() == [str(tmp_path / ".git")]


def test_the_temporary_directory_keeps_png_off_a_base64_boundary():
    import base64

    # The pet-image test's path: <TMPDIR>/.tmpXXXXXX/frame.png, encoded whole.
    path = CodexTestRunner.make_temp_dir()
    try:
        # As the runner makes it, under /tmp (this test's own root may be elsewhere).
        root = f"/tmp/{os.path.basename(path)}"
        encoded = base64.b64encode(f"{root}/.tmpABCDEF/frame.png".encode()).decode()
        assert "cG5n" not in encoded
    finally:
        pathlib.Path(path).rmdir()


def test_a_command_the_filter_leaves_empty_does_not_fail_the_run():
    skips = {
        "package": [],
        "target": [{"package": "codex-code-mode-host", "test": "stdio", "reason": "r"}],
        "test": [],
    }
    with patch.object(CodexTestRunner, "test_targets", return_value=["stdio", "other"]):
        commands = CodexTestRunner.commands(skips, "/ws", {}, "package(codex-tui)", 8)
    for command in commands[1:]:
        assert command[command.index("--no-tests") + 1] == "pass"


def test_the_nextest_download_gives_up_on_a_server_that_stops_sending(tmp_path, monkeypatch):
    # urlretrieve sets no timeout, so a stalled download hung `ling-admin codex test` for good.
    import urllib.request

    timeouts = []

    def stalled(url, *args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        raise TimeoutError("timed out")

    monkeypatch.setattr(runner_module, "TOOLS_DIR", str(tmp_path / "tools"))
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder,
        "host_target",
        classmethod(lambda cls: "aarch64-unknown-linux-gnu"),
    )
    monkeypatch.setattr(urllib.request, "urlopen", stalled)
    assert CodexTestRunner.ensure_nextest() is None
    assert timeouts and timeouts[0] is not None


def test_ensure_nextest_handles_stamp_unsupported_target_network_error_and_valid_archive(
    tmp_path, monkeypatch
):
    import hashlib
    import io
    import tarfile
    from pathlib import Path

    tools = tmp_path / "tools"
    monkeypatch.setattr(runner_module, "TOOLS_DIR", str(tools))

    # Unsupported target returns None.
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder,
        "host_target",
        classmethod(lambda cls: "riscv64-unknown-linux-gnu"),
    )
    assert CodexTestRunner.ensure_nextest() is None

    target = "aarch64-unknown-linux-gnu"
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder, "host_target", classmethod(lambda cls: target)
    )

    # Download failure returns None.
    def fail_download(url, dest):
        raise OSError("network unreachable")

    monkeypatch.setattr(runner_module.CodexBrandedBuilder, "download", staticmethod(fail_download))
    assert CodexTestRunner.ensure_nextest() is None

    # Valid archive extracts binary, writes stamp, and subsequent call returns from stamp.
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
        payload = b"#!/bin/sh\necho nextest\n"
        info = tarfile.TarInfo("cargo-nextest")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    raw_archive = tar_bytes.getvalue()
    digest = hashlib.sha256(raw_archive).hexdigest()
    monkeypatch.setitem(runner_module.NEXTEST_SHA256, target, digest)
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder,
        "download",
        staticmethod(lambda url, dest: Path(dest).write_bytes(raw_archive)),
    )
    assert CodexTestRunner.ensure_nextest() == str(tools)
    assert (tools / "cargo-nextest").read_bytes() == b"#!/bin/sh\necho nextest\n"
    assert (tools / "cargo-nextest.version").read_text().strip() == runner_module.NEXTEST_VERSION

    # Stamp hit skips download.
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder,
        "download",
        staticmethod(lambda url, dest: pytest.fail("should use cached cargo-nextest")),
    )
    assert CodexTestRunner.ensure_nextest() == str(tools)


def test_apply_test_overlay_refuses_broken_patch(tmp_path, monkeypatch):
    export = tmp_path / "export"
    (export / "codex-rs").mkdir(parents=True)
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001-bad.patch").write_text("not a valid git patch\n")
    monkeypatch.setattr(runner_module, "SNAPSHOT_OVERLAY_DIR", str(tmp_path / "no-snaps"))
    monkeypatch.setattr(runner_module, "TEST_PATCHES_DIR", str(patches))
    assert not CodexTestRunner.apply_test_overlay(str(export))


def test_patches_apply_to_the_export_when_the_home_is_a_git_work_tree(tmp_path, monkeypatch):
    # The exports live under ~/.cache. In a dotfiles home git found the home's repository above
    # the export, read every `diff --git` path against its top, skipped them all ("Skipped
    # patch") and still exited 0: `ling` was built from unpatched upstream Codex.
    home = tmp_path / "home"
    export = home / ".cache" / "export"
    (export / "codex-rs").mkdir(parents=True)
    (export / "codex-rs" / "f.rs").write_text("upstream\n")
    subprocess.run(["git", "init", "-q", str(home)], check=True)
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001-tests.patch").write_text(
        "diff --git a/codex-rs/f.rs b/codex-rs/f.rs\n--- a/codex-rs/f.rs\n+++ b/codex-rs/f.rs\n"
        "@@ -1 +1 @@\n-upstream\n+patched\n"
    )
    monkeypatch.setattr(runner_module, "SNAPSHOT_OVERLAY_DIR", str(tmp_path / "no-snaps"))
    monkeypatch.setattr(runner_module, "TEST_PATCHES_DIR", str(patches))
    assert CodexTestRunner.apply_test_overlay(str(export))
    assert (export / "codex-rs" / "f.rs").read_text() == "patched\n"


def test_snapshot_hashes_and_accept_snapshots_classify_name_only_refused_and_pending(
    tmp_path, monkeypatch
):
    export = tmp_path / "export"
    ws = export / "codex-rs"
    (ws / "tui" / "snapshots").mkdir(parents=True)
    (ws / "target" / "debug").mkdir(parents=True)
    (ws / "target" / "debug" / "ignored.snap").write_text("ignored\n")
    good_snap = ws / "tui" / "snapshots" / "ok.snap"
    bad_snap = ws / "tui" / "snapshots" / "bad.snap"
    good_snap.write_text("---\nsource: tui\n---\nOpenAI Codex\n")
    bad_snap.write_text("---\nsource: tui\n---\nOpenAI Codex\n")

    before = CodexTestRunner.snapshot_hashes(str(ws))
    assert set(before) == {"tui/snapshots/ok.snap", "tui/snapshots/bad.snap"}

    # Rewrite both snapshots: ok.snap changes only the name, bad.snap changes content.
    good_snap.write_text("---\nsource: tui\n---\nMightling\n")
    bad_snap.write_text("---\nsource: tui\n---\nMightling something else\n")
    (ws / "tui" / "snapshots" / "inline.pending-snap").write_text("pending\n")

    overlay = tmp_path / "overlay"
    monkeypatch.setattr(runner_module, "TEST_SOURCE_DIR", str(export))
    monkeypatch.setattr(runner_module, "SNAPSHOT_OVERLAY_DIR", str(overlay))
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder, "source_commit", classmethod(lambda cls: "deadbeef")
    )
    monkeypatch.setattr(
        runner_module.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "---\nsource: tui\n---\nOpenAI Codex\n", ""
        ),
    )
    assert CodexTestRunner.accept_snapshots(before) == 1
    assert (overlay / "tui" / "snapshots" / "ok.snap").read_text().endswith("Mightling\n")
    assert not (overlay / "tui" / "snapshots" / "bad.snap").exists()

    # Without refused or pending snapshots, accept_snapshots returns 0.
    (ws / "tui" / "snapshots" / "inline.pending-snap").unlink()
    before_clean = dict(before)
    before_clean["tui/snapshots/bad.snap"] = CodexTestRunner.snapshot_hashes(str(ws))[
        "tui/snapshots/bad.snap"
    ]
    assert CodexTestRunner.accept_snapshots(before_clean) == 0


def test_lld_dir_test_targets_scoped_and_temp_dir_collision(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    target = "aarch64-unknown-linux-gnu"
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder, "host_target", classmethod(lambda cls: target)
    )
    monkeypatch.setenv("RUSTUP_HOME", str(tmp_path / "missing-rustup"))
    assert CodexTestRunner.lld_dir() is None

    rustup = tmp_path / "rustup"
    (rustup / "toolchains" / f"1.80.0-{target}").mkdir(parents=True)
    monkeypatch.setenv("RUSTUP_HOME", str(rustup))
    assert CodexTestRunner.lld_dir() is None

    gcc_ld = (
        rustup / "toolchains" / f"1.95.0-{target}" / "lib" / "rustlib" / target / "bin" / "gcc-ld"
    )
    gcc_ld.mkdir(parents=True)
    (gcc_ld / "ld.lld").write_text("")
    assert CodexTestRunner.lld_dir() == str(gcc_ld)
    env = CodexTestRunner.environment({}, "/tools", 2, "/tmp/pct12345678901")
    assert f"-B{gcc_ld}" in env["RUSTFLAGS"]

    # test_targets parses cargo metadata and raises ValueError for unknown package.
    meta = {
        "packages": [
            {
                "name": "codex-core",
                "targets": [
                    {"name": "lib", "kind": ["lib"]},
                    {"name": "suite", "kind": ["test"]},
                ],
            }
        ]
    }
    monkeypatch.setattr(
        runner_module.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(meta), ""),
    )
    assert CodexTestRunner.test_targets("/ws", "codex-core", {}) == ["suite"]
    with pytest.raises(ValueError, match="no package missing"):
        CodexTestRunner.test_targets("/ws", "missing", {})

    # scoped() returns command unchanged when systemd-run is absent.
    monkeypatch.setattr(runner_module.shutil, "which", lambda name: None)
    assert CodexTestRunner.scoped(["cargo", "test"], "22G") == ["cargo", "test"]

    # make_temp_dir retries on FileExistsError.
    attempts = []
    real_mkdir = os.mkdir

    def flaky_mkdir(path, mode=0o777):
        attempts.append(path)
        if len(attempts) == 1:
            raise FileExistsError(path)
        return real_mkdir(path, mode)

    monkeypatch.setattr(runner_module.os, "mkdir", flaky_mkdir)
    created = CodexTestRunner.make_temp_dir()
    try:
        assert len(attempts) == 2
    finally:
        Path(created).rmdir()


def test_run_guard_failures_and_build_abort(tmp_path, monkeypatch):
    import fcntl
    from collections import namedtuple

    monkeypatch.setattr(runner_module, "BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(runner_module, "TEST_SOURCE_DIR", str(tmp_path / "src"))
    monkeypatch.setattr(runner_module, "TEST_TARGET_DIR", str(tmp_path / "target"))
    monkeypatch.setattr(runner_module, "TEST_HOME_DIR", str(tmp_path / "home"))

    # 1. Rust missing.
    monkeypatch.setattr(
        runner_module.DesktopInstaller, "install_rust", classmethod(lambda cls: False)
    )
    assert CodexTestRunner.run() == 1
    monkeypatch.setattr(
        runner_module.DesktopInstaller, "install_rust", classmethod(lambda cls: True)
    )

    # 2. Disk space check handles OSError on lstat and returns False when free < needed.
    real_lstat = runner_module.os.lstat
    (tmp_path / "target").mkdir(parents=True)
    (tmp_path / "target" / "blob").write_bytes(b"x")
    monkeypatch.setattr(
        runner_module.os, "lstat", lambda path, **kw: (_ for _ in ()).throw(OSError("gone"))
    )
    Usage = namedtuple("Usage", ["total", "used", "free"])
    monkeypatch.setattr(runner_module.shutil, "disk_usage", lambda path: Usage(100, 99, 1 * 2**30))
    assert CodexTestRunner.run() == 1
    # shutil.rmtree calls os.lstat too; give it the real one back before anything else runs.
    monkeypatch.setattr(runner_module.os, "lstat", real_lstat)

    monkeypatch.setattr(
        runner_module.shutil, "disk_usage", lambda path: Usage(100 * 2**30, 0, 100 * 2**30)
    )

    # 3. Lock contention returns 1.
    monkeypatch.setattr(
        fcntl,
        "flock",
        lambda fd, op: (_ for _ in ()).throw(BlockingIOError("locked")),
    )
    assert CodexTestRunner.run() == 1
    monkeypatch.setattr(fcntl, "flock", lambda fd, op: None)

    # 4. prepare_source failure returns 1.
    monkeypatch.setattr(
        CodexTestRunner, "stray_project_markers", classmethod(lambda cls: ["/tmp/.git"])
    )
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder,
        "prepare_source",
        classmethod(lambda cls, d: False),
    )
    assert CodexTestRunner.run() == 1

    # 5. apply_test_overlay failure returns 1.
    (tmp_path / "src" / "codex-rs").mkdir(parents=True)
    (tmp_path / "src" / "codex-rs" / "Cargo.toml").write_text(
        '[workspace.package]\nversion = "0.158.0"\n'
    )
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder,
        "prepare_source",
        classmethod(lambda cls, d: True),
    )
    monkeypatch.setattr(CodexTestRunner, "apply_test_overlay", classmethod(lambda cls, d: False))
    assert CodexTestRunner.run() == 1

    # 6. Missing rusty_v8 or nextest returns 1.
    monkeypatch.setattr(CodexTestRunner, "apply_test_overlay", classmethod(lambda cls, d: True))
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder,
        "fetch_rusty_v8",
        classmethod(lambda cls, d: None),
    )
    monkeypatch.setattr(CodexTestRunner, "ensure_nextest", classmethod(lambda cls: None))
    assert CodexTestRunner.run() == 1

    # 7. _run_commands stops immediately when `cargo build` fails, and run() reports new stray markers.
    monkeypatch.setattr(
        runner_module.CodexBrandedBuilder,
        "fetch_rusty_v8",
        classmethod(lambda cls, d: {"RUSTY_V8_ARCHIVE": "/v8.a"}),
    )
    monkeypatch.setattr(CodexTestRunner, "ensure_nextest", classmethod(lambda cls: "/tools"))
    markers = [["/tmp/.git"], ["/tmp/.git", "/tmp/AGENTS.md"]]
    monkeypatch.setattr(
        CodexTestRunner, "stray_project_markers", classmethod(lambda cls: markers.pop(0))
    )
    monkeypatch.setattr(
        CodexTestRunner,
        "commands",
        classmethod(lambda cls, *a: [["cargo", "build"], ["cargo", "nextest", "run"]]),
    )
    called = []
    monkeypatch.setattr(
        runner_module.subprocess,
        "call",
        lambda cmd, **kw: called.append(cmd) or 2,
    )
    monkeypatch.setattr(CodexTestRunner, "accept_snapshots", classmethod(lambda cls, before: 0))
    assert CodexTestRunner.run(accept_snapshots=True) == 2
    assert len(called) == 1


def test_the_run_is_capped_in_memory_without_swap_and_is_the_first_the_kernel_kills(monkeypatch):
    # specs/DREAMFERENCE_MIGHTLING_CODEX.md §7: a 24 GiB cap, and choom -n 1000 so an
    # out-of-memory kill takes the tests and not the desktop session.
    monkeypatch.setattr(runner_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    command = CodexTestRunner.scoped(["cargo", "nextest", "run"], runner_module.DEFAULT_MEMORY_MAX)
    assert command[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
    assert "MemoryMax=24G" in command and "MemorySwapMax=0" in command
    assert command[command.index("choom") :] == [
        "choom",
        "-n",
        "1000",
        "--",
        "cargo",
        "nextest",
        "run",
    ]
    # Without choom the cap still holds.
    monkeypatch.setattr(
        runner_module.shutil, "which", lambda name: None if name == "choom" else name
    )
    assert "choom" not in CodexTestRunner.scoped(["cargo"], "24G")


def test_the_run_defaults_to_eight_tests_at_a_time_under_24g():
    assert runner_module.DEFAULT_TEST_THREADS == 8
    assert runner_module.DEFAULT_MEMORY_MAX == "24G"
    assert runner_module.TEST_BUILD_DISK_GIB == 40


def test_the_test_build_keeps_no_debug_info_in_its_own_target_directory(tmp_path):
    environment = CodexTestRunner.environment({}, "/tools", 6, str(tmp_path))
    assert environment["CARGO_TARGET_DIR"] == runner_module.TEST_TARGET_DIR
    assert environment["CARGO_PROFILE_CI_TEST_DEBUG"] == "false"
    assert environment["CARGO_PROFILE_CI_TEST_STRIP"] == "debuginfo"
