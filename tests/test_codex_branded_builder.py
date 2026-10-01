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
    # The launcher crate is copied in from puffin-rs/ and reached through one dependency line.
    assert (tmp_path / "src" / "codex-rs" / "puffin" / "src" / "lib.rs").is_file()
    cli_manifest = (tmp_path / "src" / "codex-rs" / "cli" / "Cargo.toml").read_text()
    assert 'puffin-launcher = { path = "../puffin" }' in cli_manifest
    cli_main = (tmp_path / "src" / "codex-rs" / "cli" / "src" / "main.rs").read_text()
    assert "puffin_launcher::parse::<MultitoolCli>().await?" in cli_main
    status = subprocess.run(
        ["git", "-C", CODEX_SUBMODULE_DIR, "status", "--porcelain"], capture_output=True, text=True
    )
    assert status.stdout == ""


def test_the_patches_stay_small():
    # Anything bigger than a hook or a one-line string belongs in puffin-rs/, which Cargo compiles
    # into the same binary; the patches are only the places Codex has to call it or say "Puffin".
    # (The prompt rename used to be a ~390 KB patch to models.json; it is now rebrand() in Rust.)
    # Each hide/disable hook costs ~550 bytes, mostly diff headers, so the cap allows a few more of
    # those; it exists to catch a return to whole-file patches, not to count one-line hooks. Raised
    # from 20,000 on 2026-09-30, explicitly and only by what was needed, for the product-name hooks
    # in 0001 (slash-command descriptions, the Full Access warning, `exec`'s reply label); the
    # Night Shift patch will need another explicit raise.
    assert sum(os.path.getsize(p) for p in CodexBrandedBuilder.patches()) < 22_000


def test_the_build_key_changes_with_the_patches(tmp_path):
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "0001-a.patch").write_text("one")
    with patch.object(builder_module, "CODEX_PATCH_DIR", str(patch_dir)), \
            patch.object(CodexBrandedBuilder, "source_commit", return_value="a" * 40):
        first = CodexBrandedBuilder.build_key()
        (patch_dir / "0001-a.patch").write_text("two")
        assert CodexBrandedBuilder.build_key() != first


def test_the_build_key_changes_with_the_launcher_source(tmp_path):
    crate = tmp_path / "puffin-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "src" / "lib.rs").write_text("// one")
    with patch.object(builder_module, "PUFFIN_CRATE_DIR", str(crate)), \
            patch.object(CodexBrandedBuilder, "source_commit", return_value="a" * 40):
        first = CodexBrandedBuilder.build_key()
        (crate / "src" / "lib.rs").write_text("// two")
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
            patch.object(CodexBrandedBuilder, "build_web_tools", return_value=True), \
            patch.object(CodexBrandedBuilder, "build_code_index", return_value=True), \
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
    # Cargo builds `codex`; the builder installs it as `puffin`.
    assert command[command.index("--bin") + 1] == "codex"


def test_the_runner_never_falls_back_to_an_upstream_codex(tmp_path):
    upstream = tmp_path / "codex"
    upstream.write_text("#!/bin/sh\n")
    upstream.chmod(0o755)
    with patch.object(builder_module, "INSTALL_DIR", str(tmp_path / "not-built")), \
            patch.dict(os.environ, {"PATH": str(tmp_path)}):
        assert CodexInstaller.get_codex_executable() is None


def test_the_runner_resolves_the_branded_executable(tmp_path):
    binary = tmp_path / "bin" / "puffin"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    with patch.object(builder_module, "INSTALL_DIR", str(tmp_path)):
        assert CodexInstaller.get_codex_executable() == str(binary)


def test_puffin_admin_and_the_web_commands_are_linked_onto_path_for_the_models_shell(tmp_path, monkeypatch):
    # Puffin's prompt tells the model to run `puffin-search` and `puffin-fetch` for web access,
    # but the commands lived only in the repository's virtualenv: every call from inside a session
    # ended in "command not found" (exit 127). conftest points the links into the test home.
    import os
    from dreamference.runner import codex_branded_builder as builder

    binary = tmp_path / "puffin"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(builder.CodexBrandedBuilder, "executable_path", classmethod(lambda cls: str(binary)))
    monkeypatch.setattr(builder, "INSTALL_DIR", str(tmp_path / "install"))
    admin = builder.CodexBrandedBuilder.console_script_path("puffin-admin")
    assert admin and admin.endswith("puffin-admin")

    # A web command is linked only once its binary exists: never a dangling link.
    builder.CodexBrandedBuilder.link_onto_path()
    assert os.readlink(builder.PATH_LINK) == str(binary)
    assert os.readlink(builder.ADMIN_PATH_LINK) == admin
    assert not os.path.lexists(builder.SEARCH_PATH_LINK) and not os.path.lexists(builder.FETCH_PATH_LINK)

    installed = {}
    for name in ("puffin-search", "puffin-fetch"):
        path = tmp_path / "install" / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        installed[name] = str(path)
    builder.CodexBrandedBuilder.link_onto_path()
    # The Rust binaries, not a console script of this virtualenv.
    assert os.readlink(builder.SEARCH_PATH_LINK) == installed["puffin-search"]
    assert os.readlink(builder.FETCH_PATH_LINK) == installed["puffin-fetch"]

    # A real file of that name belongs to someone else and is left alone.
    os.remove(builder.ADMIN_PATH_LINK)
    open(builder.ADMIN_PATH_LINK, "w").write("mine")
    builder.CodexBrandedBuilder.link_onto_path()
    assert open(builder.ADMIN_PATH_LINK).read() == "mine"


def test_the_web_commands_build_from_their_own_crate_with_its_lockfile(tmp_path, monkeypatch):
    calls = []

    def fake_call(command, cwd=None, env=None):
        calls.append((command, cwd, env))
        release = tmp_path / "cache" / "target" / "release"
        release.mkdir(parents=True, exist_ok=True)
        for name in builder_module.WEB_BIN_NAMES:
            (release / name).write_text("#!/bin/sh\n")
            (release / name).chmod(0o755)
        return 0

    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(builder_module, "WEB_BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(builder_module.DesktopInstaller, "install_rust", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: None))
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)

    assert not CodexBrandedBuilder.web_tools_are_current()
    assert CodexBrandedBuilder.build_web_tools() is True
    (command, cwd, env), = calls
    assert cwd == builder_module.WEB_CRATE_DIR
    assert command[:4] == ["cargo", "build", "--release", "--locked"]
    assert [command[i + 1] for i, arg in enumerate(command) if arg == "--bin"] == ["puffin-search", "puffin-fetch"]
    assert env["CARGO_TARGET_DIR"] == str(tmp_path / "cache" / "target")
    for name in builder_module.WEB_BIN_NAMES:
        assert os.access(tmp_path / "install" / "bin" / name, os.X_OK)

    # Current now, so a second build compiles nothing.
    assert CodexBrandedBuilder.web_tools_are_current()
    assert CodexBrandedBuilder.build_web_tools() is True
    assert len(calls) == 1


def test_the_web_crate_key_changes_with_its_source(tmp_path):
    crate = tmp_path / "puffin-web-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "src" / "lib.rs").write_text("// one")
    (crate / "target").mkdir()
    first = CodexBrandedBuilder.crate_key(str(crate))
    (crate / "target" / "junk").write_text("build output is not source")
    assert CodexBrandedBuilder.crate_key(str(crate)) == first
    (crate / "src" / "lib.rs").write_text("// two")
    assert CodexBrandedBuilder.crate_key(str(crate)) != first


def test_the_web_commands_are_built_even_when_codex_is_current(monkeypatch):
    built = []
    monkeypatch.setattr(CodexBrandedBuilder, "is_current", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: None))
    monkeypatch.setattr(
        CodexBrandedBuilder, "build_web_tools", classmethod(lambda cls, force=False: built.append(("web", force)) or True)
    )
    monkeypatch.setattr(
        CodexBrandedBuilder, "build_code_index", classmethod(lambda cls, force=False: built.append(("code", force)) or True)
    )
    assert CodexBrandedBuilder.build() is True
    assert built == [("web", False), ("code", False)]


def test_puffin_code_builds_from_its_own_crate_and_is_linked_onto_path(tmp_path, monkeypatch):
    # The prompt's `# Code navigation` block tells the model to run `puffin-code`; like the web
    # commands it must be on the PATH of the shell puffin gives the model, or every call is exit 127.
    calls = []

    def fake_call(command, cwd=None, env=None):
        calls.append((command, cwd, env))
        release = tmp_path / "cache" / "target" / "release"
        release.mkdir(parents=True, exist_ok=True)
        (release / "puffin-code").write_text("#!/bin/sh\n")
        (release / "puffin-code").chmod(0o755)
        return 0

    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(builder_module, "CODE_BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(builder_module.DesktopInstaller, "install_rust", classmethod(lambda cls: True))
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)
    monkeypatch.setattr(CodexBrandedBuilder, "executable_path", classmethod(lambda cls: str(tmp_path / "puffin")))

    assert CodexBrandedBuilder.build_code_index() is True
    (command, cwd, env), = calls
    assert cwd == builder_module.CODE_CRATE_DIR
    assert command == ["cargo", "build", "--release", "--locked", "--bin", "puffin-code"]
    assert env["CARGO_TARGET_DIR"] == str(tmp_path / "cache" / "target")
    installed = tmp_path / "install" / "bin" / "puffin-code"
    assert os.access(installed, os.X_OK)
    assert os.readlink(builder_module.CODE_PATH_LINK) == str(installed)
    # Current now: nothing is compiled again.
    assert CodexBrandedBuilder.build_code_index() is True
    assert len(calls) == 1


def test_the_code_index_crate_is_committed_with_its_lockfile():
    assert os.path.isfile(os.path.join(builder_module.CODE_CRATE_DIR, "Cargo.lock"))
    assert os.path.isfile(os.path.join(builder_module.CODE_CRATE_DIR, "rust-toolchain.toml"))


def test_the_web_crate_is_committed_with_its_lockfile():
    # `--locked` fails without it, and the release build must resolve exactly what was tested.
    assert os.path.isfile(os.path.join(builder_module.WEB_CRATE_DIR, "Cargo.lock"))
    assert os.path.isfile(os.path.join(builder_module.WEB_CRATE_DIR, "Cargo.toml"))


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_the_remaining_codex_names_on_screen_say_puffin(tmp_path):
    # Found by driving the real TUI through every popup/inline slash command, an approval prompt,
    # each subcommand's --help and `exec` (2026-09-30): the slash list said "exit Codex" and
    # "choose what Codex is allowed to do", /permissions warned "Codex can edit files outside this
    # workspace", and `puffin exec` labelled the model's replies "codex".
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    rs = tmp_path / "src" / "codex-rs"
    popup = (rs / "tui" / "src" / "bottom_pane" / "command_popup.rs").read_text()
    assert 'item.description().replace("Codex", "Puffin")' in popup
    permissions = (rs / "tui" / "src" / "chatwidget" / "permissions_menu.rs").read_text()
    assert "Puffin can edit files outside this workspace" in permissions
    exec_output = (rs / "exec" / "src" / "event_processor_with_human_output.rs").read_text()
    assert '"codex".style' not in exec_output and exec_output.count('"puffin".style') == 2
    # The frame drawn while puffin starts up has its own composer, built outside the one patch
    # 0001 renames; found on 2026-10-01 by Codex's own PTY test, which waits for that placeholder.
    startup = (rs / "tui" / "src" / "startup_draft.rs").read_text()
    assert '"Ask Codex to do anything"' not in startup and '"Ask Puffin to do anything"' in startup
