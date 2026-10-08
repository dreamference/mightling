"""The Mightling-branded Codex is built from a patched *copy* of the submodule, and is the only Codex the runner uses."""

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
    assert '"OpenAI Codex"' not in session and '"Mightling"' in session
    # The launcher crate is copied in from ling-rs/ and reached through one dependency line.
    assert (tmp_path / "src" / "codex-rs" / "ling" / "src" / "lib.rs").is_file()
    cli_manifest = (tmp_path / "src" / "codex-rs" / "cli" / "Cargo.toml").read_text()
    assert 'ling-launcher = { path = "../ling" }' in cli_manifest
    cli_main = (tmp_path / "src" / "codex-rs" / "cli" / "src" / "main.rs").read_text()
    assert "ling_launcher::parse::<MultitoolCli>().await?" in cli_main
    status = subprocess.run(
        ["git", "-C", CODEX_SUBMODULE_DIR, "status", "--porcelain"], capture_output=True, text=True
    )
    assert status.stdout == ""


def test_the_patches_stay_small():
    # Anything bigger than a hook or a one-line string belongs in ling-rs/, which Cargo compiles
    # into the same binary; the patches are only the places Codex has to call it or say "Mightling".
    # (The prompt rename used to be a ~390 KB patch to models.json; it is now rebrand() in Rust.)
    # Each hide/disable hook costs ~550 bytes, mostly diff headers, so the cap allows a few more of
    # those; it exists to catch a return to whole-file patches, not to count one-line hooks. Raised
    # from 20,000 on 2026-09-30, explicitly and only by what was needed, for the product-name hooks
    # in 0001 (slash-command descriptions, the Full Access warning, `exec`'s reply label). Raised to
    # 25,000 on 2026-10-01 for 0017's cave-mode hooks (~3 KB: `/cavemode` in five places, and the
    # extension's registration in the app server and in `debug prompt-input`); the series was 21,807
    # bytes before it. Raised to 27,500 on 2026-10-01 for 0018's `/night` hooks (2,133 bytes: the
    # variant, its description, two capability lists and three dispatch arms); the series was
    # 24,800 bytes before it and is 26,933 after. Raised to 31,500 on 2026-10-01 for 0019's
    # `/airgapped` hooks (4,242 bytes: the slash command in seven places, two World State
    # registrations, and the sandbox helper's dependency and three-line hook); 31,175 after. Raised
    # to 32,500 on 2026-10-02 for 0020's two one-line hooks in core (1,250 bytes: MCP tools sent to
    # the model as plain functions, and a call mapped back to its server), without which no MCP
    # tool reaches the local model at all; 32,425 after. Raised to 33,750 on 2026-10-03 for 0019's
    # Full Access hooks (1,261 bytes: the Full Access row disabled at `on` in both permission
    # pickers, and `/airgapped` told whether the session runs in Full Access); 33,686 after.
    # On 2026-10-03 the user approved a ceiling of 37,500 for two planned patches, the context
    # budget's masking hook (~0.9 KB) and MIGHTLING_APPS' `/apps` hooks (~2.4 KB). The cap is still
    # raised here only when each lands, by its size as written, with a line saying so. Raised to
    # 34,750 for 0021's observation masking (1,011 bytes: one dependency line, and four lines at the
    # end of `for_prompt_annotated` that read the size auto-compaction uses and hand it with the
    # items to the leaf crate ling-rs/masking); 34,697 after. Raised to 36,250 on 2026-10-06 for
    # 0022's `/apps` hooks (1,371 bytes: the TUI's gate also opens when Mightling offers apps, and the
    # app server answers `app/list` with Mightling's rows before any directory request); 36,068 after.
    # On 2026-10-05 the user approved a ceiling of 38,500 for the Desktop Work window's hook. Raised
    # to 37,500 on 2026-10-06 for 0023's air-gap check in the app server (1,220 bytes: a dependency
    # line, and a validator on the config's permission constraint that refuses Full Access at `on`
    # for every client of the app server); 37,288 after. Raised to 41,000 on 2026-10-07 for 0025's
    # `/node` hooks (3,382 bytes: the variant, its description, two capability lists, two dispatch
    # arms sending an app event, and the event's arm, which suspends the TUI with `with_restored` and
    # runs ling-admin node in the terminal); approved by the user on 2026-10-07 up to ~41 KB for
    # /node; 40,693 after.
    # On 2026-10-07 the user also approved a ceiling of 39,500 for Windows' `/airgapped on`: 0024
    # (2,042 bytes: a dependency line, a helper that clears a command's network when its session is
    # sealed, and the two elevated Windows sandbox entry points calling it, so the command runs as
    # the offline account) made 39,330 on its own.
    # Raised to 43,000 on 2026-10-08 when 0024 and 0025 were merged together: each approval was
    # given with the other absent, and the two together make 42,741.
    assert sum(os.path.getsize(p) for p in CodexBrandedBuilder.patches()) < 43_000


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
    crate = tmp_path / "ling-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "src" / "lib.rs").write_text("// one")
    with patch.object(builder_module, "MIGHTLING_CRATE_DIR", str(crate)), \
            patch.object(CodexBrandedBuilder, "source_commit", return_value="a" * 40):
        first = CodexBrandedBuilder.build_key()
        (crate / "src" / "lib.rs").write_text("// two")
        assert CodexBrandedBuilder.build_key() != first


def test_the_export_reports_lings_version_not_the_upstream_tag(tmp_path, monkeypatch):
    # Every banner reads CARGO_PKG_VERSION, so the workspace version is what `ling --version`, the
    # session header and the status card show; it is stamped into the export, never the submodule.
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[workspace]\nmembers = ["cli"]\n\n[workspace.package]\nversion = "0.158.0"\n'
                        'edition = "2024"\n\n[workspace.dependencies]\nfoo = { version = "1" }\n')
    assert CodexBrandedBuilder.stamp_version(str(manifest), "1.4.1")
    text = manifest.read_text()
    assert '[workspace.package]\nversion = "1.4.1"\n' in text and 'foo = { version = "1" }' in text
    assert not CodexBrandedBuilder.stamp_version(str(manifest), "not a version")

    monkeypatch.setenv("MIGHTLING_VERSION", "2.0.0-rc.1")
    assert CodexBrandedBuilder.mightling_version() == "2.0.0-rc.1"
    monkeypatch.delenv("MIGHTLING_VERSION")
    from dreamference import __version__
    assert CodexBrandedBuilder.mightling_version() == __version__


def test_the_build_key_changes_with_the_version(monkeypatch):
    with patch.object(CodexBrandedBuilder, "source_commit", return_value="a" * 40):
        monkeypatch.setenv("MIGHTLING_VERSION", "1.4.1")
        first = CodexBrandedBuilder.build_key()
        monkeypatch.setenv("MIGHTLING_VERSION", "1.4.2")
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
            patch.object(CodexBrandedBuilder, "build_docs_index", return_value=True), \
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
    # Cargo builds `codex`; the builder installs it as `ling`.
    assert command[command.index("--bin") + 1] == "codex"
    # On Linux the Signal bridge is built in the same run, so its dependencies are the workspace's.
    bins = [command[i + 1] for i, arg in enumerate(command) if arg == "--bin"]
    assert ("ling-signal" in bins) == builder_module.BUILDS_SIGNAL
    if builder_module.BUILDS_SIGNAL:
        assert command[command.index("ling-signal") - 1] == "-p"


def test_on_linux_a_build_without_the_signal_bridge_is_not_current(tmp_path, monkeypatch):
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(builder_module, "BUILDS_SIGNAL", True)
    monkeypatch.setattr(CodexBrandedBuilder, "build_key", classmethod(lambda cls: "k"))
    (tmp_path / "build-key").write_text("k\n")
    for name in ("ling", "codex-code-mode-host"):
        path = tmp_path / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    assert CodexBrandedBuilder.is_current() is False
    bridge = tmp_path / "bin" / "ling-signal"
    bridge.write_text("#!/bin/sh\n")
    bridge.chmod(0o755)
    assert CodexBrandedBuilder.is_current() is True


def test_the_runner_never_falls_back_to_an_upstream_codex(tmp_path):
    upstream = tmp_path / "codex"
    upstream.write_text("#!/bin/sh\n")
    upstream.chmod(0o755)
    with patch.object(builder_module, "INSTALL_DIR", str(tmp_path / "not-built")), \
            patch.dict(os.environ, {"PATH": str(tmp_path)}):
        assert CodexInstaller.get_codex_executable() is None


def test_the_runner_resolves_the_branded_executable(tmp_path):
    binary = tmp_path / "bin" / "ling"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    with patch.object(builder_module, "INSTALL_DIR", str(tmp_path)):
        assert CodexInstaller.get_codex_executable() == str(binary)


def test_mightling_admin_and_the_web_commands_are_linked_onto_path_for_the_models_shell(tmp_path, monkeypatch):
    # Mightling's prompt tells the model to run `ling-search` and `ling-fetch` for web access,
    # but the commands lived only in the repository's virtualenv: every call from inside a session
    # ended in "command not found" (exit 127). conftest points the links into the test home.
    import os
    from dreamference.runner import codex_branded_builder as builder

    binary = tmp_path / "ling"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(builder.CodexBrandedBuilder, "executable_path", classmethod(lambda cls: str(binary)))
    monkeypatch.setattr(builder, "INSTALL_DIR", str(tmp_path / "install"))
    admin = builder.CodexBrandedBuilder.console_script_path("ling-admin")
    assert admin and admin.endswith("ling-admin")

    # A web command is linked only once its binary exists: never a dangling link.
    builder.CodexBrandedBuilder.link_onto_path()
    assert os.readlink(builder.PATH_LINK) == str(binary)
    assert os.readlink(builder.ADMIN_PATH_LINK) == admin
    assert not os.path.lexists(builder.SEARCH_PATH_LINK) and not os.path.lexists(builder.FETCH_PATH_LINK)

    installed = {}
    for name in ("ling-search", "ling-fetch"):
        path = tmp_path / "install" / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        installed[name] = str(path)
    builder.CodexBrandedBuilder.link_onto_path()
    # The Rust binaries, not a console script of this virtualenv.
    assert os.readlink(builder.SEARCH_PATH_LINK) == installed["ling-search"]
    assert os.readlink(builder.FETCH_PATH_LINK) == installed["ling-fetch"]

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
    assert [command[i + 1] for i, arg in enumerate(command) if arg == "--bin"] == ["ling-search", "ling-fetch"]
    assert env["CARGO_TARGET_DIR"] == str(tmp_path / "cache" / "target")
    for name in builder_module.WEB_BIN_NAMES:
        assert os.access(tmp_path / "install" / "bin" / name, os.X_OK)

    # Current now, so a second build compiles nothing.
    assert CodexBrandedBuilder.web_tools_are_current()
    assert CodexBrandedBuilder.build_web_tools() is True
    assert len(calls) == 1


def test_the_web_crate_key_changes_with_its_source(tmp_path):
    crate = tmp_path / "ling-web-rs"
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
    monkeypatch.setattr(
        CodexBrandedBuilder, "build_docs_index", classmethod(lambda cls, force=False: built.append(("docs", force)) or True)
    )
    assert CodexBrandedBuilder.build() is True
    assert built == [("web", False), ("code", False), ("docs", False)]


def test_mightling_code_builds_from_its_own_crate_and_is_linked_onto_path(tmp_path, monkeypatch):
    # The prompt's `# Code navigation` block tells the model to run `ling-code`; like the web
    # commands it must be on the PATH of the shell ling gives the model, or every call is exit 127.
    calls = []

    def fake_call(command, cwd=None, env=None):
        calls.append((command, cwd, env))
        release = tmp_path / "cache" / "target" / "release"
        release.mkdir(parents=True, exist_ok=True)
        (release / "ling-code").write_text("#!/bin/sh\n")
        (release / "ling-code").chmod(0o755)
        return 0

    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(builder_module, "CODE_BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(builder_module.DesktopInstaller, "install_rust", classmethod(lambda cls: True))
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)
    monkeypatch.setattr(CodexBrandedBuilder, "executable_path", classmethod(lambda cls: str(tmp_path / "ling")))

    assert CodexBrandedBuilder.build_code_index() is True
    (command, cwd, env), = calls
    assert cwd == builder_module.CODE_CRATE_DIR
    assert command == ["cargo", "build", "--release", "--locked", "--bin", "ling-code"]
    assert env["CARGO_TARGET_DIR"] == str(tmp_path / "cache" / "target")
    installed = tmp_path / "install" / "bin" / "ling-code"
    assert os.access(installed, os.X_OK)
    assert os.readlink(builder_module.CODE_PATH_LINK) == str(installed)
    # Current now: nothing is compiled again.
    assert CodexBrandedBuilder.build_code_index() is True
    assert len(calls) == 1


def test_ling_docs_builds_from_its_own_crate_installs_its_runtime_and_is_linked(tmp_path, monkeypatch):
    calls, setups = [], []

    def fake_call(command, cwd=None, env=None):
        calls.append((command, cwd, env))
        release = tmp_path / "cache" / "target" / "release"
        release.mkdir(parents=True, exist_ok=True)
        (release / "ling-docs").write_text("#!/bin/sh\n")
        (release / "ling-docs").chmod(0o755)
        return 0

    from dreamference.runner.docs_index_setup import DocsIndexSetup
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(builder_module, "DOCS_BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(builder_module.DesktopInstaller, "install_rust", classmethod(lambda cls: True))
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)
    monkeypatch.setattr(CodexBrandedBuilder, "executable_path", classmethod(lambda cls: str(tmp_path / "ling")))
    monkeypatch.setattr(DocsIndexSetup, "install", classmethod(lambda cls, machine=None: setups.append(1) or True))

    assert CodexBrandedBuilder.build_docs_index() is True
    (command, cwd, env), = calls
    assert cwd == builder_module.DOCS_CRATE_DIR
    assert command == ["cargo", "build", "--release", "--locked", "--bin", "ling-docs"]
    installed = tmp_path / "install" / "bin" / "ling-docs"
    assert os.readlink(builder_module.DOCS_PATH_LINK) == str(installed)
    assert setups == [1], "the run-time files are installed after the build"
    # A failed download does not fail the build.
    monkeypatch.setattr(DocsIndexSetup, "install", classmethod(lambda cls, machine=None: False))
    assert CodexBrandedBuilder.build_docs_index(force=True) is True


def test_the_docs_index_crate_is_committed_with_its_lockfile():
    assert os.path.isfile(os.path.join(builder_module.DOCS_CRATE_DIR, "Cargo.lock"))
    assert os.path.isfile(os.path.join(builder_module.DOCS_CRATE_DIR, "rust-toolchain.toml"))


def test_the_code_index_crate_is_committed_with_its_lockfile():
    assert os.path.isfile(os.path.join(builder_module.CODE_CRATE_DIR, "Cargo.lock"))
    assert os.path.isfile(os.path.join(builder_module.CODE_CRATE_DIR, "rust-toolchain.toml"))


def test_the_web_crate_is_committed_with_its_lockfile():
    # `--locked` fails without it, and the release build must resolve exactly what was tested.
    assert os.path.isfile(os.path.join(builder_module.WEB_CRATE_DIR, "Cargo.lock"))
    assert os.path.isfile(os.path.join(builder_module.WEB_CRATE_DIR, "Cargo.toml"))


@pytest.mark.parametrize("system, machine, target", [
    ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
    ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
    # macOS says arm64 where Rust and the prebuilt V8 assets say aarch64.
    ("Darwin", "arm64", "aarch64-apple-darwin"),
    ("Darwin", "x86_64", "x86_64-apple-darwin"),
])
def test_the_target_names_the_v8_assets_and_release_assets_per_platform(monkeypatch, system, machine, target):
    import platform

    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert CodexBrandedBuilder.host_target() == target


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_the_remaining_codex_names_on_screen_say_mightling(tmp_path):
    # Found by driving the real TUI through every popup/inline slash command, an approval prompt,
    # each subcommand's --help and `exec` (2026-09-30): the slash list said "exit Codex" and
    # "choose what Codex is allowed to do", /permissions warned "Codex can edit files outside this
    # workspace", and `ling exec` labelled the model's replies "codex".
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    rs = tmp_path / "src" / "codex-rs"
    popup = (rs / "tui" / "src" / "bottom_pane" / "command_popup.rs").read_text()
    assert 'item.description().replace("Codex", "Mightling")' in popup
    permissions = (rs / "tui" / "src" / "chatwidget" / "permissions_menu.rs").read_text()
    assert "Mightling can edit files outside this workspace" in permissions
    exec_output = (rs / "exec" / "src" / "event_processor_with_human_output.rs").read_text()
    assert '"codex".style' not in exec_output and exec_output.count('"ling".style') == 2
    # The frame drawn while ling starts up has its own composer, built outside the one patch
    # 0001 renames; found on 2026-10-01 by Codex's own PTY test, which waits for that placeholder.
    startup = (rs / "tui" / "src" / "startup_draft.rs").read_text()
    assert '"Ask Codex to do anything"' not in startup and '"Ask Mightling to do anything"' in startup
