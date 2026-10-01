"""Codex's own tests run on Puffin's patched export, never the submodule, minus a reasoned skip list."""

import glob
import os
import re
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from dreamference.runner import codex_test_runner as runner_module
from dreamference.runner.codex_branded_builder import CODEX_SUBMODULE_DIR
from dreamference.runner.codex_test_runner import CodexTestRunner

SUBMODULE_PRESENT = os.path.isdir(os.path.join(CODEX_SUBMODULE_DIR, "codex-rs"))


def _workspace_packages() -> dict:
    """Maps each Codex workspace package name to its directory, read from the submodule's manifests."""
    packages = {}
    for manifest in glob.glob(os.path.join(CODEX_SUBMODULE_DIR, "codex-rs", "**", "Cargo.toml"), recursive=True):
        with open(manifest) as handle:
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
        assert os.path.isfile(os.path.join(packages[entry["package"]], "tests", f"{entry['test']}.rs"))


def test_a_skip_without_a_reason_is_refused(tmp_path):
    path = tmp_path / "skips.toml"
    path.write_text('[[test]]\nfilter = "test(=x)"\n')
    with pytest.raises(ValueError, match="reason"):
        CodexTestRunner.load_skips(str(path))


def test_the_filterset_narrows_to_the_callers_filter_and_leaves_the_skips_out():
    skips = {"test": [{"filter": "test(=a)", "reason": "r"}, {"filter": "package(b)", "reason": "r"}]}
    assert CodexTestRunner.filterset(skips) == "not ((test(=a)) | (package(b)))"
    assert CodexTestRunner.filterset(skips, "package(c)") == "(package(c)) & not ((test(=a)) | (package(b)))"
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
    assert ["--exclude", "codex-voice-host"] == build[build.index("--exclude"):build.index("--exclude") + 2]
    assert "codex-code-mode-host" in workspace and "codex-voice-host" in workspace
    assert package[package.index("-p") + 1] == "codex-code-mode-host"
    assert "stdio" not in package and ["--test", "grpc"] == package[-4:-2]
    # The launcher is built beside it (its vendored OpenSSL), but only that package's tests run.
    assert ["-p", "puffin-launcher"] == package[package.index("-p") + 2:package.index("-p") + 4]
    assert package[package.index("-E") + 1] == "(package(codex-code-mode-host))"


def test_the_tests_see_an_empty_home_and_no_model_server(tmp_path):
    with patch.dict(os.environ, {"RUST_BACKTRACE": "1", "CODEX_HOME": "/home/x/.puffin",
                                 "DREAMFERENCE_CONFIG_PATH": "/home/x/dreamference.toml"}):
        environment = CodexTestRunner.environment({"RUSTY_V8_ARCHIVE": "a"}, "/tools", 6, str(tmp_path))
    assert environment["HOME"] == runner_module.TEST_HOME_DIR
    assert environment["TMPDIR"] == str(tmp_path)
    # The launcher steps aside, and a test that looked for a model server anyway finds a closed port.
    assert environment["PUFFIN_UPSTREAM_TESTS"] == "1"
    assert environment["DREAMFERENCE_VLLM_HOST"] == "http://127.0.0.1:9"
    assert "CODEX_HOME" not in environment and "DREAMFERENCE_CONFIG_PATH" not in environment
    assert "RUST_BACKTRACE" not in environment and environment["RUST_LIB_BACKTRACE"] == "0"
    assert environment["PATH"].startswith("/tools")
    assert environment["RUSTY_V8_ARCHIVE"] == "a"


def test_the_export_is_put_back_at_the_version_upstreams_tests_expect(tmp_path):
    manifest = tmp_path / "codex-rs" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text('[workspace.package]\nversion = "0.158.0"\n\n[dependencies]\nx = { version = "1.2.3" }\n')
    CodexTestRunner.reset_workspace_version(str(tmp_path))
    text = manifest.read_text()
    assert '[workspace.package]\nversion = "0.0.0"' in text and 'version = "1.2.3"' in text


def test_the_run_tests_the_export_and_never_the_submodule(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "TEST_SOURCE_DIR", str(tmp_path / "test-src"))
    monkeypatch.setattr(runner_module, "TEST_HOME_DIR", str(tmp_path / "test-home"))
    monkeypatch.setattr(runner_module, "BUILD_CACHE_DIR", str(tmp_path))
    (tmp_path / "test-src" / "codex-rs").mkdir(parents=True)
    (tmp_path / "test-src" / "codex-rs" / "Cargo.toml").write_text('[workspace.package]\nversion = "1"\n')
    calls = []

    def fake_call(command, cwd=None, env=None):
        calls.append((command, cwd, env))
        return 100 if "nextest" in command else 0

    with patch("dreamference.runner.codex_test_runner.DesktopInstaller.install_rust", return_value=True), \
            patch.object(runner_module.CodexBrandedBuilder, "prepare_source", return_value=True), \
            patch.object(runner_module.CodexBrandedBuilder, "fetch_rusty_v8", return_value={}), \
            patch.object(CodexTestRunner, "ensure_nextest", return_value="/tools"), \
            patch.object(CodexTestRunner, "test_targets", return_value=["grpc"]), \
            patch.object(CodexTestRunner, "has_disk_space", return_value=True), \
            patch.object(CodexTestRunner, "apply_test_overlay", return_value=True) as overlay, \
            patch("subprocess.call", side_effect=fake_call):
        assert CodexTestRunner.run() == 100
    overlay.assert_called_once_with(str(tmp_path / "test-src"))
    assert len(calls) == 3
    for command, cwd, env in calls:
        assert cwd == str(tmp_path / "test-src" / "codex-rs")
        assert not cwd.startswith(CODEX_SUBMODULE_DIR)
        assert env["PUFFIN_UPSTREAM_TESTS"] == "1"
    # The private temporary directory is removed afterwards.
    assert not os.path.exists(calls[0][2]["TMPDIR"])


def test_a_run_without_room_for_the_build_does_not_start(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "BUILD_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(runner_module, "TEST_TARGET_DIR", str(tmp_path / "test-target"))
    with patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(0, 0, 5 * 2**30)):
        assert not CodexTestRunner.has_disk_space()
    # An earlier build counts toward what is needed: Cargo reuses or replaces it.
    (tmp_path / "test-target").mkdir()
    with open(tmp_path / "test-target" / "big", "wb") as handle:
        handle.truncate(38 * 2**30)
    with patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(0, 0, 5 * 2**30)):
        assert CodexTestRunner.has_disk_space()
    with patch("dreamference.runner.codex_test_runner.DesktopInstaller.install_rust", return_value=True), \
            patch.object(CodexTestRunner, "has_disk_space", return_value=False), \
            patch("subprocess.call") as call:
        assert CodexTestRunner.run() == 1
    call.assert_not_called()


def test_the_temporary_directory_name_has_no_path_separators_the_tests_decode(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    path = CodexTestRunner.make_temp_dir()
    assert os.path.dirname(path) == str(tmp_path)
    assert re.fullmatch(r"pct[a-z0-9]{11}", os.path.basename(path))
    assert os.stat(path).st_mode & 0o777 == 0o700


def test_a_nextest_download_that_fails_its_checksum_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "TOOLS_DIR", str(tmp_path))

    def fake_retrieve(url, path):
        with open(path, "wb") as handle:
            handle.write(b"not the release")

    with patch.object(runner_module.CodexBrandedBuilder, "host_target", return_value="aarch64-unknown-linux-gnu"), \
            patch("urllib.request.urlretrieve", side_effect=fake_retrieve):
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
    puffin = (BOXED_UPSTREAM.replace("OpenAI Codex (v0.0.0)      ", "Puffin (v0.0.0)            ")
              .replace("Ask Codex", "Ask Puffin"))
    assert CodexTestRunner.differs_only_by_name(BOXED_UPSTREAM, puffin)


def test_a_snapshot_with_any_other_change_is_refused():
    renamed = BOXED_UPSTREAM.replace("OpenAI Codex", "Puffin").replace("Ask Codex", "Ask Puffin")
    # A layout regression: a row of content gone, a word changed, a symbol changed.
    assert not CodexTestRunner.differs_only_by_name(BOXED_UPSTREAM, renamed.replace(
        "│ model:     gpt-5   /model to change   │\n", ""))
    assert not CodexTestRunner.differs_only_by_name(BOXED_UPSTREAM, renamed.replace("gpt-5", "gpt-6"))
    assert not CodexTestRunner.differs_only_by_name(BOXED_UPSTREAM, renamed.replace("›", ">"))


def test_the_overlay_replaces_upstream_snapshots_and_applies_the_test_patches(tmp_path, monkeypatch):
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
    (overlay / "tui" / "src" / "snapshots" / "header.snap").write_text("puffin's\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001-tests.patch").write_text(
        "--- a/codex-rs/tui/tests/suite.rs\n+++ b/codex-rs/tui/tests/suite.rs\n@@ -1 +1 @@\n"
        '-wait_for_screen("Ask Codex to do anything");\n+wait_for_screen("Ask Puffin to do anything");\n'
    )
    monkeypatch.setattr(runner_module, "SNAPSHOT_OVERLAY_DIR", str(overlay))
    monkeypatch.setattr(runner_module, "TEST_PATCHES_DIR", str(patches))
    assert CodexTestRunner.apply_test_overlay(str(export))
    assert snap.read_text() == "puffin's\n"
    assert "Ask Puffin" in test_file.read_text()


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
        with open(os.path.join(runner_module.TEST_PATCHES_DIR, name)) as handle:
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
        os.rmdir(path)
