"""The Puffin-branded Codex is built from a patched *copy* of the submodule, and is the only Codex the runner uses."""

import os
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from dreamference.runner import codex_branded_builder as builder_module
from dreamference.runner.codex_branded_builder import (
    CODEX_SUBMODULE_DIR,
    CodexBrandedBuilder,
)
from dreamference.runner.codex_installer import CodexInstaller

SUBMODULE_PRESENT = os.path.isdir(os.path.join(CODEX_SUBMODULE_DIR, "codex-rs"))


def test_the_patch_series_is_ordered_and_non_empty():
    names = [os.path.basename(p) for p in CodexBrandedBuilder.patches()]
    assert names == sorted(names)
    assert any("brand" in name for name in names)


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_every_patch_applies_to_the_pinned_submodule_and_leaves_it_untouched(tmp_path):
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    session = (tmp_path / "src" / "codex-rs" / "tui" / "src" / "history_cell" / "session.rs").read_text()
    assert '"OpenAI Codex"' not in session and '"Puffin"' in session
    status = subprocess.run(
        ["git", "-C", CODEX_SUBMODULE_DIR, "status", "--porcelain"], capture_output=True, text=True
    )
    assert status.stdout == ""


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_the_embedded_prompt_names_puffin_not_codex(tmp_path):
    # The runner reads its system prompt out of the built binary's models.json, so the identity
    # line the model sees comes from this file.
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    models = (tmp_path / "src" / "codex-rs" / "models-manager" / "models.json").read_text()
    assert "You are Puffin, a coding agent." in models
    assert "You are Codex" not in models


def test_the_build_key_changes_with_the_patches(tmp_path):
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "0001-a.patch").write_text("one")
    with patch.object(builder_module, "CODEX_PATCH_DIR", str(patch_dir)), \
            patch.object(CodexBrandedBuilder, "source_commit", return_value="a" * 40):
        first = CodexBrandedBuilder.build_key()
        (patch_dir / "0001-a.patch").write_text("two")
        assert CodexBrandedBuilder.build_key() != first


def test_the_build_compiles_the_exported_copy_not_the_submodule(tmp_path):
    calls = []

    def fake_call(command, cwd=None, env=None):
        calls.append((command, cwd, env))
        return 1

    with patch.object(builder_module, "BUILD_CACHE_DIR", str(tmp_path)), \
            patch.object(CodexBrandedBuilder, "is_current", return_value=False), \
            patch.object(CodexBrandedBuilder, "build_key", return_value="k"), \
            patch.object(CodexBrandedBuilder, "prepare_source", return_value=True), \
            patch.object(CodexBrandedBuilder, "fetch_rusty_v8", return_value={"RUSTY_V8_ARCHIVE": "a", "RUSTY_V8_SRC_BINDING_PATH": "b"}), \
            patch.object(builder_module.DesktopInstaller, "install_rust", return_value=True), \
            patch.object(builder_module.subprocess, "call", side_effect=fake_call):
        assert CodexBrandedBuilder.build() is False

    (command, cwd, env), = calls
    assert cwd == os.path.join(str(tmp_path), "src", "codex-rs")
    assert not cwd.startswith(CODEX_SUBMODULE_DIR)
    assert env["RUSTY_V8_ARCHIVE"] == "a"
    assert env["CARGO_TARGET_DIR"] == os.path.join(str(tmp_path), "target")
    # Debug info is dropped at compile time, not stripped after: upstream's profile keeps it.
    assert env["CARGO_PROFILE_RELEASE_DEBUG"] == "none"
    assert env["CARGO_PROFILE_RELEASE_STRIP"] == "debuginfo"
    assert command[:3] == ["cargo", "build", "--release"]


def test_the_runner_never_falls_back_to_an_upstream_codex(tmp_path):
    upstream = tmp_path / "codex"
    upstream.write_text("#!/bin/sh\n")
    upstream.chmod(0o755)
    with patch.object(builder_module, "INSTALL_DIR", str(tmp_path / "not-built")), \
            patch.dict(os.environ, {"PATH": str(tmp_path)}):
        assert CodexInstaller.get_codex_executable() is None


def test_the_runner_resolves_the_branded_executable(tmp_path):
    binary = tmp_path / "bin" / "puffin-codex"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    with patch.object(builder_module, "INSTALL_DIR", str(tmp_path)):
        assert CodexInstaller.get_codex_executable() == str(binary)
