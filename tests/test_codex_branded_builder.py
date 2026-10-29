"""The Mightling-branded Codex is built from a patched *copy* of the submodule, and is the only Codex the runner uses."""

import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from dreamference.chat.desktop_installer import DesktopInstaller
from dreamference.runner import codex_branded_builder as builder_module
from dreamference.runner.codex_branded_builder import (
    CODEX_LOCKFILE,
    CODEX_LOCKFILE_COMMIT,
    CODEX_SUBMODULE_DIR,
    MIGHTLING_CRATE_DIR,
    CodexBrandedBuilder,
)
from dreamference.runner.codex_installer import CodexInstaller

SUBMODULE_PRESENT = Path(os.path.join(CODEX_SUBMODULE_DIR, "codex-rs")).is_dir()


def test_the_patch_series_is_ordered_and_non_empty():
    names = [os.path.basename(p) for p in CodexBrandedBuilder.patches()]
    assert names == sorted(names)
    assert any("brand" in name for name in names)


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_every_patch_applies_to_the_pinned_submodule_and_leaves_it_untouched(tmp_path):
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "src"))
    session = (
        tmp_path / "src" / "codex-rs" / "tui" / "src" / "history_cell" / "session.rs"
    ).read_text()
    assert '"OpenAI Codex"' not in session and '"Mightling"' in session
    # The plugin catalog's section label is what "<label> unavailable." shows when the section fails.
    requests = (
        tmp_path / "src" / "codex-rs" / "tui" / "src" / "app" / "background_requests.rs"
    ).read_text()
    assert '"vertical",\n            "Curated",' in requests
    # The launcher crate is copied in from ling-rs/ and reached through one dependency line.
    assert (tmp_path / "src" / "codex-rs" / "ling" / "src" / "lib.rs").is_file()
    cli_manifest = (tmp_path / "src" / "codex-rs" / "cli" / "Cargo.toml").read_text()
    assert 'ling-launcher = { path = "../ling" }' in cli_manifest
    cli_main = (tmp_path / "src" / "codex-rs" / "cli" / "src" / "main.rs").read_text()
    assert "ling_launcher::parse::<MultitoolCli>().await?" in cli_main
    # The exit summary's "To continue this session, run: codex resume <id>" names ling (1.7.1:
    # found by a capture of a session's end); `codex/` itself is never changed.
    assert (
        'println!("{}", line.replace("codex resume", "ling resume")'
        '.replace("codex --remote", "ling --remote").replace("codex agents", "ling agents"));'
    ) in cli_main
    assert 'println!("{line}");\n        }\n    }\n    if is_fatal' not in cli_main
    status = subprocess.run(
        ["git", "-C", CODEX_SUBMODULE_DIR, "status", "--porcelain"], capture_output=True, text=True
    )
    assert status.stdout == ""


@pytest.fixture
def stand_in_codex(tmp_path, monkeypatch):
    """A stand-in submodule, patch and launcher crate; returns the folder of an unrelated repository."""
    def git(*args, cwd):
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    submodule = tmp_path / "codex"
    (submodule / "codex-rs").mkdir(parents=True)
    (submodule / "codex-rs" / "main.rs").write_text("codex\n")
    git("init", "-q", cwd=submodule)
    git("add", "-A", cwd=submodule)
    git("commit", "-q", "-m", "pin", cwd=submodule)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=submodule, capture_output=True, text=True
    ).stdout.strip()
    patch_file = tmp_path / "0001-brand.patch"
    patch_file.write_text(
        "diff --git a/codex-rs/main.rs b/codex-rs/main.rs\n--- a/codex-rs/main.rs\n"
        "+++ b/codex-rs/main.rs\n@@ -1 +1 @@\n-codex\n+ling\n"
    )
    crate = tmp_path / "ling-rs"
    crate.mkdir()
    outer = tmp_path / "outer"
    outer.mkdir()
    git("init", "-q", cwd=outer)
    monkeypatch.setattr(builder_module, "CODEX_SUBMODULE_DIR", str(submodule))
    monkeypatch.setattr(builder_module, "MIGHTLING_CRATE_DIR", str(crate))
    monkeypatch.setattr(CodexBrandedBuilder, "source_commit", classmethod(lambda cls: commit))
    monkeypatch.setattr(CodexBrandedBuilder, "patches", classmethod(lambda cls: [str(patch_file)]))
    monkeypatch.setattr(CodexBrandedBuilder, "stamp_version", classmethod(lambda cls, manifest, version: True))
    return outer


def test_the_patches_apply_to_an_export_inside_another_git_repository(stand_in_codex):
    # Run from a folder inside a repository, `git apply` resolves the patch's paths from that
    # repository's top and silently skips every hunk outside the folder: the export came out
    # unpatched and prepare_source still said it succeeded (found 2026-10-29, exporting into a
    # worktree). Here the export is inside an unrelated repository.
    assert CodexBrandedBuilder.prepare_source(str(stand_in_codex / "export"))
    assert (stand_in_codex / "export" / "codex-rs" / "main.rs").read_text() == "ling\n"


def test_the_export_builds_from_the_committed_lockfile(stand_in_codex, tmp_path, monkeypatch):
    # Upstream's lock cannot pin the crates ling-rs/ brings in, so without this copy every build
    # resolved them afresh from crates.io (found 2026-10-29: 55 crates, pgp and mdns-sd among them).
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text("# pinned\n")
    monkeypatch.setattr(builder_module, "CODEX_LOCKFILE", str(lockfile))
    assert CodexBrandedBuilder.prepare_source(str(stand_in_codex / "export"))
    assert (stand_in_codex / "export" / "codex-rs" / "Cargo.lock").read_text() == "# pinned\n"

    lockfile.unlink()
    with pytest.raises(FileNotFoundError):
        CodexBrandedBuilder.prepare_source(str(stand_in_codex / "export"))


def _locked_names(lock_path):
    return {package["name"] for package in tomllib.loads(Path(lock_path).read_text())["package"]}


def _third_party_dependencies(manifest):
    """Yields the name of every dependency of a ling-rs manifest that comes from a registry."""
    data = tomllib.loads(manifest.read_text())
    kinds = ("dependencies", "build-dependencies", "dev-dependencies")
    tables = [table.get(kind, {}) for table in [data, *data.get("target", {}).values()] for kind in kinds]
    for name, spec in (item for table in tables for item in table.items()):
        if not isinstance(spec, dict):
            yield name
        elif "path" not in spec and not spec.get("workspace"):
            yield spec.get("package", name)


def test_the_lockfile_pins_every_third_party_crate_of_ling_rs():
    # Cargo builds without --locked, so a crate the lock does not name is resolved to whatever is
    # newest on crates.io that day. A new dependency in ling-rs/ means refreshing the lock.
    locked = _locked_names(CODEX_LOCKFILE)
    unpinned = set()
    for manifest in Path(MIGHTLING_CRATE_DIR).rglob("Cargo.toml"):
        if "target" not in manifest.parts:
            unpinned |= {
                f"{name} ({manifest.relative_to(MIGHTLING_CRATE_DIR)})"
                for name in _third_party_dependencies(manifest)
                if name not in locked
            }
    assert not unpinned, f"refresh codex-patches/Cargo.lock (docs/dev/codex-build.md): {sorted(unpinned)}"


def test_the_lockfile_was_made_for_the_pinned_codex():
    # A submodule bump keeps the lock's old upstream pins until the lock is refreshed with it.
    listed = subprocess.run(
        ["git", "-C", builder_module.REPO_ROOT, "ls-files", "-s", "codex"], capture_output=True, text=True
    )
    if listed.returncode != 0 or not listed.stdout:
        pytest.skip("not a git checkout")
    assert listed.stdout.split()[1] == CODEX_LOCKFILE_COMMIT, "refresh codex-patches/Cargo.lock with the bump"


def test_the_lockfile_fetches_only_from_crates_io_and_pinned_git():
    for package in tomllib.loads(Path(CODEX_LOCKFILE).read_text())["package"]:
        source = package.get("source", "registry+https://github.com/rust-lang/crates.io-index")
        assert source.startswith(("registry+https://github.com/rust-lang/crates.io-index", "git+https://")), package


@pytest.mark.skipif(not SUBMODULE_PRESENT, reason="codex submodule not checked out")
def test_the_lockfile_keeps_every_crate_upstream_pins():
    upstream = subprocess.run(
        ["git", "-C", CODEX_SUBMODULE_DIR, "show", f"{CODEX_LOCKFILE_COMMIT}:codex-rs/Cargo.lock"],
        capture_output=True, text=True, check=True,
    ).stdout
    third_party = {package["name"] for package in tomllib.loads(upstream)["package"] if package.get("source")}
    assert third_party <= _locked_names(CODEX_LOCKFILE)


def test_the_codex_submodule_is_committed_as_a_submodule():
    # A worktree whose codex/ was replaced by a symlink to another checkout's committed that symlink
    # once (release/1.7.1, 266ae50): the Windows build then found no codex/.github/actions and the
    # release run failed. The index must hold codex as a submodule (mode 160000), never a link.
    listed = subprocess.run(
        ["git", "-C", builder_module.REPO_ROOT, "ls-files", "-s", "codex"],
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0 or not listed.stdout:
        pytest.skip("not a git checkout")
    assert listed.stdout.split()[0] == "160000", listed.stdout


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
    # given with the other absent, and the two together make 42,741. Not raised on 2026-10-12 when
    # 0020 grew by 217 bytes (the request hook also takes the request's input, and one line turns each
    # output item back) so Code Mode's `exec` is offered as a function on SGLang; 42,958 after.
    # Raised to 45,000 on 2026-10-14, once, by the owner, to cover three changes together: drop-first
    # compaction (0026, 1,039 bytes: a method on the history that hands its items and size to
    # ling-rs/masking's `drop_first`, and three lines at the top of `run_auto_compact` that skip the
    # summary when the drop was enough; 43,997 after), the sandbox fix and the coming compaction
    # cache patch.
    # Not raised for 0027 (675 bytes, 2026-10-14: the request hook also gets the thread id and whether
    # the request is a compaction, so a compaction offers the turn's tools and hits the prefix cache;
    # logic in ling-rs/tools/src/compaction.rs), the cache patch the cap allowed for; 44,672 after.
    # Not raised for /import (2026-10-19): two lines in 0012's hunk hide it (163 bytes; a patch of
    # its own would have cost ~550 and crossed the cap), since its plugin step clones a marketplace
    # hosted on GitHub, which the egress audit does not cover; 44,835 after.
    # Not raised for the exit summary's resume line (2026-10-20, 1.7.1): one hunk in 0001's
    # cli/src/main.rs (`codex resume` -> `ling resume` where the lines are printed), paid for by
    # dropping two of 0001's optional hunk-header section names; 44,952 after.
    # Raised to 46,849 on 2026-10-20 for 0028's `/adapter` hook (1,849 bytes: the variant, its
    # description, two capability lists, the queue-drain list and two dispatch arms that print the
    # handler's lines and send its model as `AppEvent::UpdateModel`; the handler is in
    # ling-rs/src/adapter.rs, and the hunk headers carry no section names); the series was 44,952
    # bytes before it and is 46,801 after.
    # Raised to 55,000 on 2026-10-20 by the owner, on top of that series, for 1.7.1 (the sandbox's
    # host sockets, the vendor names in the TUI) and ling-claude's five TUI hooks (about 3.8 KB).
    # The first: 0019's sandbox hunk denied the host's control sockets for every managed profile
    # (`ling_airgapped::host_sockets`, 377 bytes), after a sandboxed command ran `docker restart` on
    # the host; 47,178 after. Since 2026-10-22 only at /airgapped on (`host_sockets_at`).
    # The compatibility mode's five TUI hooks, 0029-compat-hooks (3,464 bytes; 50,642 after), each
    # one call into ling-rs/src/compat_keys.rs that answers "Codex's own behaviour" outside the mode:
    # Enter in the composer (a draft ending in `\` gets a newline; Claude Code's command names run
    # Codex's command or are answered), Ctrl+C / Ctrl+D asking for a second press (three uses of
    # DOUBLE_PRESS_QUIT_SHORTCUT_ENABLED), `[` and `v` in the transcript overlay (the conversation into
    # the session's history, or $EDITOR through Tui::with_restored), and Shift+Tab (BackTab) as the approval-
    # mode shortcut.
    # Raised to 58,000 on 2026-10-21 by the owner, for the vendor names (6,881 bytes; 57,523 after):
    # /apps in the slash-command popup (upstream's popup drops it whatever the connectors gate says)
    # and the status card's account line in 0001, and 0030-neutral-vendor-names, which rewords every
    # user-reachable "ChatGPT" and "OpenAI" string the patches had left (the app page, the plugin
    # install rows, the plugin catalog's "OpenAI Curated", the remote catalogs' sign-in hints, the
    # model menu's base-URL warning) and the /hooks screen's "Right before Codex ends its turn".
    # Not raised on 2026-10-22 for 0031-read-only-network (1,004 bytes: a dependency line and one
    # line in SandboxManager::transform giving the read-only sandbox the network below
    # /airgapped on): the series' 59 `index` lines, which `git apply` does not need, were dropped
    # to make room (1,770 bytes); 56,757 after.
    # Raised to 60,000 on 2026-10-26 with the owner's approval, for the Windows fixes W1/W2/W9
    # (0032 guard fails closed, 0033 sandbox window station, 0034 doctor on Windows); 59,496 after.
    # Raised to 64,000 on 2026-10-27 by the owner: headroom for Windows and compat fixes; 59,496 then.
    # Not raised on 2026-10-28 for 0035 (640 bytes: the code-mode host found beside the resolved
    # executable); 60,136 after. Down the same day when cave mode was removed: 0017 (2,834 bytes)
    # deleted and 0018, 0019, 0025 and 0028 re-cut; 57,571 after.
    assert sum(Path(p).stat().st_size for p in CodexBrandedBuilder.patches()) < 64_000


def test_airgapped_treats_every_profile_without_a_sandbox_like_full_access():
    # The air gap is enforced by the sandbox helper, and Codex runs no helper for any profile whose
    # network is on and whose file system is fully writable: Full Access, but also a
    # `[permissions]` profile with `":root" = "write"`. Asking only "is it Full Access?" let such a
    # profile take `/airgapped on`, and a configured `on`, with commands that kept the network.
    # Both checks now ask Codex's own question: does this profile get a sandbox?
    texts = {Path(p).name: Path(p).read_text() for p in CodexBrandedBuilder.patches()}
    no_sandbox = "codex_sandboxing::policy_transforms::runs_without_platform_sandbox("
    tui = texts["0019-airgapped.patch"]
    assert "PermissionProfile::Disabled)).into_iter()" not in tui
    assert tui.count(no_sandbox + "self.config.permissions.permission_profile())") == 2
    # The question is asked in one place, Codex's sandboxing crate, with its own unit test there.
    assert "+pub fn runs_without_platform_sandbox(profile: &PermissionProfile) -> bool {" in tui
    assert (
        "+fn a_profile_writing_the_whole_disk_with_the_network_on_runs_without_a_sandbox() {" in tui
    )
    app_server = texts["0023-airgapped-app-server.patch"]
    assert "permission_refusal(matches!(p, PermissionProfile::Disabled)" not in app_server
    assert "permission_refusal(" + no_sandbox + "p)" in app_server


def test_the_exit_summary_names_ling_in_every_command_it_prints():
    # Codex's exit summary prints `codex resume`, and for a task left running on a daemon or a
    # remote server also `codex --remote <address> resume` and `codex agents`; the CLI prints it
    # (patch 0001) with each command renamed, so none of them sends the user to a missing binary.
    import re

    cli = Path(
        next(p for p in CodexBrandedBuilder.patches() if Path(p).name.startswith("0001-"))
    ).read_text()
    line = next(
        line
        for line in cli.splitlines()
        if line.startswith("+") and "format_exit_messages" not in line and "line.replace(" in line
    )
    printed = (
        "Reconnect: codex --remote ws://server:80/ resume 1. Stop the current turn: run codex agents. "
        "Or run codex resume."
    )
    for old, new in re.findall(r'replace\("([^"]*)", "([^"]*)"\)', line):
        printed = printed.replace(old, new)
    assert "codex" not in printed


def test_the_build_key_changes_with_the_patches(tmp_path):
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "0001-a.patch").write_text("one")
    with (
        patch.object(builder_module, "CODEX_PATCH_DIR", str(patch_dir)),
        patch.object(CodexBrandedBuilder, "source_commit", return_value="a" * 40),
    ):
        first = CodexBrandedBuilder.build_key()
        (patch_dir / "0001-a.patch").write_text("two")
        assert CodexBrandedBuilder.build_key() != first


def test_the_build_key_changes_with_the_launcher_source(tmp_path):
    crate = tmp_path / "ling-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "src" / "lib.rs").write_text("// one")
    with (
        patch.object(builder_module, "MIGHTLING_CRATE_DIR", str(crate)),
        patch.object(CodexBrandedBuilder, "source_commit", return_value="a" * 40),
    ):
        first = CodexBrandedBuilder.build_key()
        (crate / "src" / "lib.rs").write_text("// two")
        assert CodexBrandedBuilder.build_key() != first


def test_the_build_key_changes_with_the_ui_that_ling_web_embeds(tmp_path):
    ui = tmp_path / "ui"
    (ui / "src").mkdir(parents=True)
    (ui / "src" / "App.tsx").write_text("// one")
    (ui / "node_modules").mkdir()
    (ui / "dist").mkdir()
    with (
        patch.object(builder_module, "WEB_UI_DIR", str(ui)),
        patch.object(CodexBrandedBuilder, "source_commit", return_value="a" * 40),
    ):
        first = CodexBrandedBuilder.build_key()
        # Installed packages and a previous build's output are not sources.
        (ui / "node_modules" / "x.js").write_text("x")
        (ui / "dist" / "index.html").write_text("x")
        assert CodexBrandedBuilder.build_key() == first
        assert CodexBrandedBuilder.web_ui_files() == [str(ui / "src" / "App.tsx")]
        (ui / "src" / "App.tsx").write_text("// two")
        assert CodexBrandedBuilder.build_key() != first


def test_ling_is_not_built_without_the_ui_it_serves(tmp_path, capsys):
    # A `ling` whose `ling web` serves the placeholder page is the bug this build step fixes.
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "package.json").write_text("{}")
    with (
        patch.object(builder_module, "WEB_UI_DIR", str(ui)),
        patch.object(builder_module.shutil, "which", return_value=None),
    ):
        assert CodexBrandedBuilder.build_web_ui() is None
    assert "npm is needed" in capsys.readouterr().out

    calls = []

    def fake_call(command, cwd=None, env=None):
        calls.append(command[1:])
        if command[1:] == ["run", "build"]:
            (ui / "dist").mkdir()
            (ui / "dist" / "index.html").write_text("<html>")
        return 0

    with (
        patch.object(builder_module, "WEB_UI_DIR", str(ui)),
        patch.object(builder_module, "WEB_UI_DIST", str(ui / "dist")),
        patch.object(builder_module.shutil, "which", return_value="/usr/bin/npm"),
        patch.object(builder_module.subprocess, "call", side_effect=fake_call),
    ):
        assert CodexBrandedBuilder.build_web_ui() == str(ui / "dist")
    assert calls == [["ci", "--no-audit", "--no-fund"], ["run", "build"]]

    with (
        patch.object(builder_module, "BUILD_CACHE_DIR", str(tmp_path / "cache")),
        patch.object(CodexBrandedBuilder, "build_key", return_value="k"),
        patch.object(CodexBrandedBuilder, "prepare_source", return_value=True),
        patch.object(
            CodexBrandedBuilder,
            "fetch_rusty_v8",
            return_value={"RUSTY_V8_ARCHIVE": "a", "RUSTY_V8_SRC_BINDING_PATH": "b"},
        ),
        patch.object(DesktopInstaller, "install_rust", return_value=True),
        patch.object(CodexBrandedBuilder, "build_web_ui", return_value=None),
        patch.object(
            builder_module.subprocess, "call", side_effect=AssertionError("cargo must not run")
        ),
    ):
        assert CodexBrandedBuilder._build_locked() is False


def test_the_export_reports_lings_version_not_the_upstream_tag(tmp_path, monkeypatch):
    # Every banner reads CARGO_PKG_VERSION, so the workspace version is what `ling --version`, the
    # session header and the status card show; it is stamped into the export, never the submodule.
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        '[workspace]\nmembers = ["cli"]\n\n[workspace.package]\nversion = "0.158.0"\n'
        'edition = "2024"\n\n[workspace.dependencies]\nfoo = { version = "1" }\n'
    )
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

    with (
        patch.object(builder_module, "BUILD_CACHE_DIR", str(tmp_path)),
        patch.object(CodexBrandedBuilder, "is_current", return_value=False),
        patch.object(CodexBrandedBuilder, "build_key", return_value="k"),
        patch.object(CodexBrandedBuilder, "prepare_source", return_value=True),
        patch.object(
            CodexBrandedBuilder,
            "fetch_rusty_v8",
            return_value={"RUSTY_V8_ARCHIVE": "a", "RUSTY_V8_SRC_BINDING_PATH": "b"},
        ),
        patch.object(DesktopInstaller, "install_rust", return_value=True),
        patch.object(CodexBrandedBuilder, "build_web_tools", return_value=True),
        patch.object(CodexBrandedBuilder, "build_code_index", return_value=True),
        patch.object(CodexBrandedBuilder, "build_docs_index", return_value=True),
        patch.object(CodexBrandedBuilder, "build_web_ui", return_value="/ui/dist"),
        patch.object(builder_module.subprocess, "call", side_effect=fake_call),
    ):
        assert CodexBrandedBuilder.build() is False

    ((command, cwd, env),) = calls
    # `ling web` embeds the UI built just before (ling-rs/web/build.rs).
    assert env["LING_WEB_UI_DIST"] == "/ui/dist"
    assert cwd == os.path.join(str(tmp_path), "src", "codex-rs")
    assert not cwd.startswith(CODEX_SUBMODULE_DIR)
    assert env["RUSTY_V8_ARCHIVE"] == "a"
    # A flag, not CARGO_TARGET_DIR, which sccache would hash into every crate's key.
    assert command[command.index("--target-dir") + 1] == os.path.join(str(tmp_path), "target")
    assert "CARGO_TARGET_DIR" not in env
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
    # The delegation plugin's server rides the same run, and is installed beside `ling`.
    assert ("ling-delegate" in bins) == builder_module.BUILDS_DELEGATE
    if builder_module.BUILDS_DELEGATE:
        assert command[command.index("ling-delegate") - 1] == "-p"


def test_on_linux_a_build_without_the_delegate_binary_is_not_current(tmp_path, monkeypatch):
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(builder_module, "BUILDS_SIGNAL", False)
    monkeypatch.setattr(builder_module, "BUILDS_DELEGATE", True)
    monkeypatch.setattr(CodexBrandedBuilder, "build_key", classmethod(lambda cls: "k"))
    (tmp_path / "build-key").write_text("k\n")
    for name in ("ling", "codex-code-mode-host"):
        path = tmp_path / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    assert CodexBrandedBuilder.is_current() is False
    delegate = tmp_path / "bin" / "ling-delegate"
    delegate.write_text("#!/bin/sh\n")
    delegate.chmod(0o755)
    assert CodexBrandedBuilder.is_current() is True


def test_the_delegate_binary_is_installed_beside_ling_and_linked_onto_path():
    script = (Path(__file__).resolve().parent.parent / "install.sh").read_text()
    assert "ling-signal ling-delegate" in script
    assert "codex-code-mode-host|ling-signal) ;;" in script
    assert builder_module.DELEGATE_PATH_LINK.endswith("/.local/bin/ling-delegate")


def test_on_linux_a_build_without_the_signal_bridge_is_not_current(tmp_path, monkeypatch):
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(builder_module, "BUILDS_SIGNAL", True)
    monkeypatch.setattr(builder_module, "BUILDS_DELEGATE", False)
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
    with (
        patch.object(builder_module, "INSTALL_DIR", str(tmp_path / "not-built")),
        patch.dict(os.environ, {"PATH": str(tmp_path)}),
    ):
        assert CodexInstaller.get_codex_executable() is None


def test_the_runner_resolves_the_branded_executable(tmp_path):
    binary = tmp_path / "bin" / "ling"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    with patch.object(builder_module, "INSTALL_DIR", str(tmp_path)):
        assert CodexInstaller.get_codex_executable() == str(binary)


def test_mightling_admin_and_the_web_commands_are_linked_onto_path_for_the_models_shell(
    tmp_path, monkeypatch
):
    # Mightling's prompt tells the model to run `ling-search` and `ling-fetch` for web access,
    # but the commands lived only in the repository's virtualenv: every call from inside a session
    # ended in "command not found" (exit 127). conftest points the links into the test home.
    import os

    from dreamference.runner import codex_branded_builder as builder

    binary = tmp_path / "ling"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        builder.CodexBrandedBuilder, "executable_path", classmethod(lambda cls: str(binary))
    )
    monkeypatch.setattr(builder, "INSTALL_DIR", str(tmp_path / "install"))
    admin = builder.CodexBrandedBuilder.console_script_path("ling-admin")
    assert admin and admin.endswith("ling-admin")

    # A web command is linked only once its binary exists: never a dangling link.
    builder.CodexBrandedBuilder.link_onto_path()
    assert os.readlink(builder.PATH_LINK) == str(binary)
    assert os.readlink(builder.ADMIN_PATH_LINK) == admin
    assert not os.path.lexists(builder.SEARCH_PATH_LINK) and not os.path.lexists(
        builder.FETCH_PATH_LINK
    )

    installed = {}
    for name in ("ling-search", "ling-fetch", "ling-delegate"):
        path = tmp_path / "install" / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        installed[name] = str(path)
    builder.CodexBrandedBuilder.link_onto_path()
    # The Rust binaries, not a console script of this virtualenv.
    assert os.readlink(builder.SEARCH_PATH_LINK) == installed["ling-search"]
    assert os.readlink(builder.FETCH_PATH_LINK) == installed["ling-fetch"]
    assert str(Path(builder.DELEGATE_PATH_LINK).readlink()) == installed["ling-delegate"]

    # A real file of that name belongs to someone else and is left alone.
    Path(builder.ADMIN_PATH_LINK).unlink()
    Path(builder.ADMIN_PATH_LINK).open("w").write("mine")
    builder.CodexBrandedBuilder.link_onto_path()
    assert Path(builder.ADMIN_PATH_LINK).open().read() == "mine"


def test_ling_is_not_linked_onto_path_before_it_is_built(tmp_path, monkeypatch):
    # build() runs the crate builds, which link, before the Codex build: a failed or skipped
    # Codex build left ~/.local/bin/ling pointing at nothing.
    from dreamference.runner import codex_branded_builder as builder

    monkeypatch.setattr(
        builder.CodexBrandedBuilder,
        "executable_path",
        classmethod(lambda cls: str(tmp_path / "install" / "bin" / "ling")),
    )
    monkeypatch.setattr(builder, "INSTALL_DIR", str(tmp_path / "install"))
    builder.CodexBrandedBuilder.link_onto_path()
    assert not os.path.lexists(builder.PATH_LINK)


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
    monkeypatch.setattr(
        DesktopInstaller, "install_rust", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: None))
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)

    assert not CodexBrandedBuilder.web_tools_are_current()
    assert CodexBrandedBuilder.build_web_tools() is True
    ((command, cwd, env),) = calls
    assert cwd == builder_module.WEB_CRATE_DIR
    assert command[:4] == ["cargo", "build", "--release", "--locked"]
    assert [command[i + 1] for i, arg in enumerate(command) if arg == "--bin"] == [
        "ling-search",
        "ling-fetch",
    ]
    assert command[command.index("--target-dir") + 1] == str(tmp_path / "cache" / "target")
    assert "CARGO_TARGET_DIR" not in env
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


def test_a_crate_key_changes_with_a_path_dependency_s_source(tmp_path):
    # ling-web-rs and ling-docs-rs link the air-gap reader from ling-rs/airgapped as a path
    # dependency (spec DREAMFERENCE_MIGHTLING_AIRGAPPED.md §6.5): a change there must rebuild them.
    leaf = tmp_path / "ling-rs" / "airgapped"
    (leaf / "src").mkdir(parents=True)
    (leaf / "Cargo.toml").write_text('[package]\nname = "ling-airgapped"\n')
    (leaf / "src" / "lib.rs").write_text("// one")
    (leaf / "target").mkdir()
    crate = tmp_path / "ling-web-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "src" / "lib.rs").write_text("// web")
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "ling-web"\n\n[dependencies]\nling-airgapped = { path = "../ling-rs/airgapped" }\n'
        'serde = "1"\n\n[target.\'cfg(unix)\'.dependencies]\ngone = { path = "../nowhere" }\n'
    )
    first = CodexBrandedBuilder.crate_key(str(crate))
    (leaf / "target" / "junk").write_text("build output is not source")
    assert CodexBrandedBuilder.crate_key(str(crate)) == first
    (leaf / "src" / "lib.rs").write_text("// two")
    assert CodexBrandedBuilder.crate_key(str(crate)) != first


def test_the_web_commands_are_built_even_when_codex_is_current(monkeypatch):
    built = []
    monkeypatch.setattr(CodexBrandedBuilder, "is_current", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: None))
    monkeypatch.setattr(
        CodexBrandedBuilder,
        "build_web_tools",
        classmethod(lambda cls, force=False: built.append(("web", force)) or True),
    )
    monkeypatch.setattr(
        CodexBrandedBuilder,
        "build_code_index",
        classmethod(lambda cls, force=False: built.append(("code", force)) or True),
    )
    monkeypatch.setattr(
        CodexBrandedBuilder,
        "build_docs_index",
        classmethod(lambda cls, force=False: built.append(("docs", force)) or True),
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
    monkeypatch.setattr(
        DesktopInstaller, "install_rust", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)
    monkeypatch.setattr(
        CodexBrandedBuilder, "executable_path", classmethod(lambda cls: str(tmp_path / "ling"))
    )

    assert CodexBrandedBuilder.build_code_index() is True
    ((command, cwd, env),) = calls
    assert cwd == builder_module.CODE_CRATE_DIR
    target = str(tmp_path / "cache" / "target")
    assert command == [
        "cargo",
        "build",
        "--release",
        "--locked",
        "--target-dir",
        target,
        "--bin",
        "ling-code",
    ]
    assert "CARGO_TARGET_DIR" not in env
    installed = tmp_path / "install" / "bin" / "ling-code"
    assert os.access(installed, os.X_OK)
    assert os.readlink(builder_module.CODE_PATH_LINK) == str(installed)
    # Current now: nothing is compiled again.
    assert CodexBrandedBuilder.build_code_index() is True
    assert len(calls) == 1


def test_ling_docs_builds_from_its_own_crate_installs_its_runtime_and_is_linked(
    tmp_path, monkeypatch
):
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
    monkeypatch.setattr(
        DesktopInstaller, "install_rust", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)
    monkeypatch.setattr(
        CodexBrandedBuilder, "executable_path", classmethod(lambda cls: str(tmp_path / "ling"))
    )
    monkeypatch.setattr(
        DocsIndexSetup, "install", classmethod(lambda cls, machine=None: setups.append(1) or True)
    )

    assert CodexBrandedBuilder.build_docs_index() is True
    ((command, cwd, env),) = calls
    assert cwd == builder_module.DOCS_CRATE_DIR
    target = str(tmp_path / "cache" / "target")
    assert command == [
        "cargo",
        "build",
        "--release",
        "--locked",
        "--target-dir",
        target,
        "--bin",
        "ling-docs",
    ]
    installed = tmp_path / "install" / "bin" / "ling-docs"
    assert os.readlink(builder_module.DOCS_PATH_LINK) == str(installed)
    assert setups == [1], "the run-time files are installed after the build"
    # A failed download does not fail the build.
    monkeypatch.setattr(DocsIndexSetup, "install", classmethod(lambda cls, machine=None: False))
    assert CodexBrandedBuilder.build_docs_index(force=True) is True


def test_the_docs_index_crate_is_committed_with_its_lockfile():
    assert Path(os.path.join(builder_module.DOCS_CRATE_DIR, "Cargo.lock")).is_file()
    assert Path(os.path.join(builder_module.DOCS_CRATE_DIR, "rust-toolchain.toml")).is_file()


def test_the_code_index_crate_is_committed_with_its_lockfile():
    assert Path(os.path.join(builder_module.CODE_CRATE_DIR, "Cargo.lock")).is_file()
    assert Path(os.path.join(builder_module.CODE_CRATE_DIR, "rust-toolchain.toml")).is_file()


def test_the_web_crate_is_committed_with_its_lockfile():
    # `--locked` fails without it, and the release build must resolve exactly what was tested.
    assert Path(os.path.join(builder_module.WEB_CRATE_DIR, "Cargo.lock")).is_file()
    assert Path(os.path.join(builder_module.WEB_CRATE_DIR, "Cargo.toml")).is_file()


@pytest.mark.parametrize(
    "system, machine, target",
    [
        ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
        ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
        # macOS says arm64 where Rust and the prebuilt V8 assets say aarch64.
        ("Darwin", "arm64", "aarch64-apple-darwin"),
        ("Darwin", "x86_64", "x86_64-apple-darwin"),
    ],
)
def test_the_target_names_the_v8_assets_and_release_assets_per_platform(
    monkeypatch, system, machine, target
):
    import platform

    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert CodexBrandedBuilder.host_target() == target


@pytest.mark.parametrize("machine, target", [
    # platform.machine() on Windows; ARM64 on an RTX Spark laptop (WINDOWS_ARM section 16.1).
    ("ARM64", "aarch64-pc-windows-msvc"),
    ("AMD64", "x86_64-pc-windows-msvc"),
    ("x64", "x86_64-pc-windows-msvc"),
])
def test_a_windows_machine_builds_for_the_msvc_target_and_fetches_v8s_lib(machine, target):
    assert CodexBrandedBuilder.target_for(machine, windows=True) == target
    assert CodexBrandedBuilder.v8_archive_name(target) == f"rusty_v8_{builder_module.RUSTY_V8_PROFILE}_{target}.lib.gz"


def test_v8_elsewhere_is_the_static_library_upstream_names_lib_a():
    name = CodexBrandedBuilder.v8_archive_name("aarch64-unknown-linux-gnu")
    assert name == f"librusty_v8_{builder_module.RUSTY_V8_PROFILE}_aarch64-unknown-linux-gnu.a.gz"


def test_a_windows_checkout_keeps_the_patches_and_shell_scripts_as_lf():
    """With core.autocrlf=true a CRLF patch no longer applies to the LF export, and bash reads the CR."""
    rules = (Path(__file__).resolve().parent.parent / ".gitattributes").read_text().splitlines()
    assert "*.patch -text" in rules
    assert "*.sh text eol=lf" in rules


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
    assert (
        '"Ask Codex to do anything"' not in startup and '"Ask Mightling to do anything"' in startup
    )


def _sccache_on_path(tmp_path):
    """A stand-in sccache in a folder of its own, and an environment whose PATH finds it."""
    folder = tmp_path / "bin"
    folder.mkdir()
    sccache = folder / "sccache"
    sccache.write_text("#!/bin/sh\nexit 0\n")
    sccache.chmod(0o755)
    return str(sccache), {"PATH": str(folder)}


@pytest.fixture
def real_compiler_cache(monkeypatch):
    from conftest import REAL_COMPILER_CACHE_ENVIRONMENT

    monkeypatch.setattr(
        CodexBrandedBuilder, "compiler_cache_environment", REAL_COMPILER_CACHE_ENVIRONMENT
    )


def test_builds_compile_through_sccache_when_it_is_installed(
    tmp_path, monkeypatch, real_compiler_cache
):
    sccache, environment = _sccache_on_path(tmp_path)
    started = []
    monkeypatch.setattr(
        CodexBrandedBuilder,
        "sccache_server_ready",
        classmethod(lambda cls, program, env: started.append((program, env)) or True),
    )
    added = CodexBrandedBuilder.compiler_cache_environment(environment)
    assert added["RUSTC_WRAPPER"] == sccache
    assert added["SCCACHE_CACHE_SIZE"] == "40G"
    assert added["SCCACHE_DIR"] == str(Path.home() / ".cache" / "sccache")
    assert added["SCCACHE_IGNORE_SERVER_IO_ERROR"] == "1"
    assert added["CARGO_INCREMENTAL"] == "0"
    # The server starts with the cap and the folder, which it reads only then.
    ((program, env),) = started
    assert program == sccache and env["SCCACHE_CACHE_SIZE"] == "40G"
    # A user's own folder and cap win.
    mine = CodexBrandedBuilder.compiler_cache_environment(
        {**environment, "SCCACHE_DIR": "/elsewhere", "SCCACHE_CACHE_SIZE": "5G"}
    )
    assert (mine["SCCACHE_DIR"], mine["SCCACHE_CACHE_SIZE"]) == ("/elsewhere", "5G")


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "OFF"])
def test_sccache_is_switched_off_by_the_environment(
    tmp_path, monkeypatch, real_compiler_cache, value
):
    _sccache, environment = _sccache_on_path(tmp_path)
    monkeypatch.setattr(
        CodexBrandedBuilder, "sccache_server_ready", classmethod(lambda cls, p, e: True)
    )
    assert (
        CodexBrandedBuilder.compiler_cache_environment(
            {**environment, "DREAMFERENCE_SCCACHE": value}
        )
        == {}
    )


def test_without_sccache_or_with_a_wrapper_already_set_nothing_changes(
    tmp_path, monkeypatch, real_compiler_cache
):
    monkeypatch.setattr(
        CodexBrandedBuilder, "sccache_server_ready", classmethod(lambda cls, p, e: True)
    )
    # A release install or a CI runner: no sccache on PATH, the build as it always was.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert CodexBrandedBuilder.compiler_cache_environment({"PATH": str(empty)}) == {}
    # A wrapper the caller chose is theirs.
    _sccache, environment = _sccache_on_path(tmp_path)
    assert (
        CodexBrandedBuilder.compiler_cache_environment({**environment, "RUSTC_WRAPPER": "mine"})
        == {}
    )


def test_a_server_that_does_not_start_means_a_build_without_the_cache(
    tmp_path, monkeypatch, real_compiler_cache
):
    _sccache, environment = _sccache_on_path(tmp_path)
    monkeypatch.setattr(
        CodexBrandedBuilder, "sccache_server_ready", classmethod(lambda cls, p, e: False)
    )
    assert CodexBrandedBuilder.compiler_cache_environment(environment) == {}


def test_the_server_probe_answers_from_the_exit_status(tmp_path):
    sccache, environment = _sccache_on_path(tmp_path)
    assert CodexBrandedBuilder.sccache_server_ready(sccache, environment) is True
    failing = tmp_path / "failing"
    failing.write_text("#!/bin/sh\nexit 2\n")
    failing.chmod(0o755)
    assert CodexBrandedBuilder.sccache_server_ready(str(failing), environment) is False
    assert CodexBrandedBuilder.sccache_server_ready(str(tmp_path / "missing"), environment) is False


def test_the_codex_and_crate_builds_take_the_compile_cache(tmp_path, monkeypatch):
    calls = []

    def fake_call(command, cwd=None, env=None):
        calls.append(env)
        return 1

    monkeypatch.setattr(
        CodexBrandedBuilder,
        "compiler_cache_environment",
        classmethod(lambda cls, environment: {"RUSTC_WRAPPER": "/x/sccache"}),
    )
    monkeypatch.setattr(
        DesktopInstaller, "install_rust", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path / "install"))
    assert (
        CodexBrandedBuilder.build_crate(str(tmp_path), str(tmp_path / "cache"), "stamp", ("x",))
        is False
    )
    with (
        patch.object(builder_module, "BUILD_CACHE_DIR", str(tmp_path)),
        patch.object(CodexBrandedBuilder, "build_key", return_value="k"),
        patch.object(CodexBrandedBuilder, "prepare_source", return_value=True),
        patch.object(CodexBrandedBuilder, "fetch_rusty_v8", return_value={}),
        patch.object(CodexBrandedBuilder, "build_web_ui", return_value="/ui/dist"),
    ):
        assert CodexBrandedBuilder._build_locked() is False
    assert [env["RUSTC_WRAPPER"] for env in calls] == ["/x/sccache", "/x/sccache"]


def _fake_crate(tmp_path, monkeypatch, during_build=None):
    """A one-binary crate whose cargo build is faked; `during_build` runs while it compiles."""
    crate = tmp_path / "crate"
    (crate / "src").mkdir(parents=True)
    (crate / "src" / "main.rs").write_text("fn main() {}\n")

    def fake_call(command, cwd=None, env=None):
        if during_build:
            during_build(crate)
        release = tmp_path / "cache" / "target" / "release"
        release.mkdir(parents=True, exist_ok=True)
        (release / "x").write_text("#!/bin/sh\n")
        (release / "x").chmod(0o755)
        return 0

    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(
        DesktopInstaller, "install_rust", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: None))
    monkeypatch.setattr(builder_module.subprocess, "call", fake_call)
    return crate


def test_a_crate_edited_during_its_build_is_not_stamped_current(tmp_path, monkeypatch):
    # The binary was compiled from the source as it was when cargo started; an edit made while it
    # ran must leave the next run rebuilding, not stamp the old binary with the new key.
    crate = _fake_crate(
        tmp_path,
        monkeypatch,
        lambda crate: (crate / "src" / "main.rs").write_text("fn main() { 1; }\n"),
    )
    assert (
        CodexBrandedBuilder.build_crate(str(crate), str(tmp_path / "cache"), "stamp", ("x",))
        is True
    )
    assert not CodexBrandedBuilder.crate_is_current(str(crate), "stamp", ("x",))


def test_a_crate_install_leaves_another_installs_staging_file_alone(tmp_path, monkeypatch):
    # Crate builds run outside the Codex build's lock: two `ling-admin` processes installing the
    # same binary must not copy into, or rename away, each other's staging file.
    crate = _fake_crate(tmp_path, monkeypatch)
    bin_dir = tmp_path / "install" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / ".x.new").write_text("another install's copy")
    assert (
        CodexBrandedBuilder.build_crate(str(crate), str(tmp_path / "cache"), "stamp", ("x",))
        is True
    )
    assert (bin_dir / ".x.new").read_text() == "another install's copy"
    assert (bin_dir / "x").read_text() == "#!/bin/sh\n"


def test_a_directory_named_like_a_crate_binary_is_not_current(tmp_path, monkeypatch):
    # The stamped check took any path with the execute bit, a directory included; the unstamped
    # one (binaries_installed) asks for a file.
    crate = _fake_crate(tmp_path, monkeypatch)
    assert CodexBrandedBuilder.build_crate(str(crate), str(tmp_path / "cache"), "stamp", ("x",)) is True
    binary = tmp_path / "install" / "bin" / "x"
    binary.unlink()
    binary.mkdir()
    assert not CodexBrandedBuilder.crate_is_current(str(crate), "stamp", ("x",))


def test_a_failed_crate_copy_leaves_no_staging_file(tmp_path, monkeypatch):
    # Each install stages under a name of its own, so a copy that failed (a full disk) left one
    # more partial binary in bin/ every time.
    crate = _fake_crate(tmp_path, monkeypatch)

    def full_disk(source, target):
        Path(target).write_text("half a bin")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(builder_module.shutil, "copy2", full_disk)
    with pytest.raises(OSError):
        CodexBrandedBuilder.build_crate(str(crate), str(tmp_path / "cache"), "stamp", ("x",))
    assert list((tmp_path / "install" / "bin").iterdir()) == []


def test_linking_leaves_another_links_staging_link_alone(tmp_path):
    link = tmp_path / "bin" / "ling"
    link.parent.mkdir()
    staging = tmp_path / "bin" / "ling.new"
    staging.symlink_to("/another/process")
    CodexBrandedBuilder._link("/install/ling", str(link))
    assert link.readlink() == Path("/install/ling")
    assert staging.readlink() == Path("/another/process")


def test_a_second_build_waits_for_the_lock_and_stops_if_the_first_one_built_ling(
    tmp_path, monkeypatch
):
    # specs/DREAMFERENCE_MIGHTLING_CODEX.md §2 step 0: the build takes `.build.lock`, and re-checks
    # after waiting, so a build that waited on another finishes at once.
    current = iter([False, True])
    locked = []
    monkeypatch.setattr(builder_module, "BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(CodexBrandedBuilder, "is_current", classmethod(lambda cls: next(current)))
    monkeypatch.setattr(
        CodexBrandedBuilder,
        "_lock_exclusive",
        classmethod(lambda cls, handle: locked.append(handle.name)),
    )
    monkeypatch.setattr(
        CodexBrandedBuilder, "_build_locked", classmethod(lambda cls: pytest.fail("built twice"))
    )
    assert CodexBrandedBuilder._build_codex() is True
    assert locked == [str(tmp_path / "cache" / ".build.lock")]


@pytest.mark.skipif(os.name == "nt", reason="flock is POSIX")
def test_a_held_build_lock_is_waited_for_and_said_so(tmp_path, monkeypatch, capsys):
    import fcntl

    calls = []

    def flock(handle, operation):
        calls.append(operation)
        if operation & fcntl.LOCK_NB:
            raise BlockingIOError

    monkeypatch.setattr(fcntl, "flock", flock)
    with Path(tmp_path / ".build.lock").open("w") as lock:
        CodexBrandedBuilder._lock_exclusive(lock)
    assert calls == [fcntl.LOCK_EX | fcntl.LOCK_NB, fcntl.LOCK_EX]
    assert "Another ling build is running" in capsys.readouterr().out


def _stand_in_export(tmp_path, monkeypatch, patch_text):
    """A stand-in submodule, launcher crate and one patch, wired into the builder."""

    def git(*args, cwd):
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    submodule = tmp_path / "codex"
    (submodule / "codex-rs").mkdir(parents=True)
    (submodule / "codex-rs" / "main.rs").write_text("codex\n")
    git("init", "-q", cwd=submodule)
    git("add", "-A", cwd=submodule)
    git("commit", "-q", "-m", "pin", cwd=submodule)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=submodule, capture_output=True, text=True
    ).stdout.strip()
    patch_file = tmp_path / "0001-brand.patch"
    patch_file.write_text(patch_text)
    crate = tmp_path / "ling-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "src" / "lib.rs").write_text("// launcher\n")
    monkeypatch.setattr(builder_module, "CODEX_SUBMODULE_DIR", str(submodule))
    monkeypatch.setattr(builder_module, "MIGHTLING_CRATE_DIR", str(crate))
    monkeypatch.setattr(CodexBrandedBuilder, "source_commit", classmethod(lambda cls: commit))
    monkeypatch.setattr(CodexBrandedBuilder, "patches", classmethod(lambda cls: [str(patch_file)]))
    monkeypatch.setattr(
        CodexBrandedBuilder, "stamp_version", classmethod(lambda cls, manifest, version: True)
    )
    return crate


def test_the_launcher_is_copied_into_the_export_with_fresh_mtimes(tmp_path, monkeypatch):
    # §2 step 2: a plain copy, so Cargo never takes an edited launcher file as already compiled.
    crate = _stand_in_export(
        tmp_path,
        monkeypatch,
        "diff --git a/codex-rs/main.rs b/codex-rs/main.rs\n"
        "--- a/codex-rs/main.rs\n+++ b/codex-rs/main.rs\n@@ -1 +1 @@\n-codex\n+ling\n",
    )
    os.utime(crate / "src" / "lib.rs", (1_000_000, 1_000_000))
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "export"))
    copied = tmp_path / "export" / "codex-rs" / "ling" / "src" / "lib.rs"
    assert copied.read_text() == "// launcher\n"
    assert copied.stat().st_mtime > 1_000_000


def test_a_patch_that_does_not_fit_stops_the_export_and_names_the_release_tag(
    tmp_path, monkeypatch, capsys
):
    # §2 step 3: each patch is checked before it is applied; one that does not fit names the tag.
    _stand_in_export(
        tmp_path,
        monkeypatch,
        "diff --git a/codex-rs/main.rs b/codex-rs/main.rs\n"
        "--- a/codex-rs/main.rs\n+++ b/codex-rs/main.rs\n@@ -1 +1 @@\n-upstream\n+ling\n",
    )
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "export")) is False
    out = capsys.readouterr().out
    assert "0001-brand.patch does not apply" in out
    assert builder_module.CODEX_RELEASE_TAG in out
    assert (tmp_path / "export" / "codex-rs" / "main.rs").read_text() == "codex\n"


V8_TARGET = "x86_64-unknown-linux-gnu"
V8_CHECKSUMS = f"rusty_v8_{builder_module.RUSTY_V8_PROFILE}_{V8_TARGET}.sha256"
V8_ARCHIVE = f"librusty_v8_{builder_module.RUSTY_V8_PROFILE}_{V8_TARGET}.a.gz"
V8_BINDING = f"src_binding_{builder_module.RUSTY_V8_PROFILE}_{V8_TARGET}.rs"


def _digest(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _v8_release(tmp_path, monkeypatch, served=None, manifest_returncode=0, manifest=None):
    """A stand-in export, submodule manifest and release server for `fetch_rusty_v8`.

    `served` maps a file name to the bytes the release serves; by default every file matches its
    pinned digest. Returns the export directory, the manifest paths `git show` was asked for, and
    the names that were downloaded.
    """
    honest = {V8_ARCHIVE: b"v8 archive", V8_BINDING: b"// v8 bindings"}
    # Upstream writes some checksum files with CRLF and a `*` (binary mode) before the name.
    honest[V8_CHECKSUMS] = (
        f"{_digest(honest[V8_ARCHIVE])} *{V8_ARCHIVE}\r\n{_digest(honest[V8_BINDING])}  {V8_BINDING}\r\n"
    ).encode()
    files = {**honest, **(served or {})}
    if manifest is None:
        manifest = (
            f"{'0' * 64}  rusty_v8_{builder_module.RUSTY_V8_PROFILE}_aarch64-apple-darwin.sha256\n"
            f"{_digest(honest[V8_CHECKSUMS])}  {V8_CHECKSUMS}\n"
        )
    export = tmp_path / "export"
    (export / "codex-rs").mkdir(parents=True)
    (export / "codex-rs" / "Cargo.toml").write_text(
        '[workspace.dependencies]\nrusty_v8 = "=9.9.9"\nv8 = "=146.4.0"\n'
    )
    shown, downloaded = [], []

    def run(command, **kwargs):
        shown.append(command[-1])
        return subprocess.CompletedProcess(command, manifest_returncode, stdout=manifest, stderr="")

    def download(cls, url, path):
        name = url.rsplit("/", 1)[-1]
        downloaded.append(name)
        assert url == f"{builder_module.RUSTY_V8_RELEASE_URL.format(version='146.4.0')}/{name}"
        if name not in files:
            raise OSError(f"404 {name}")
        Path(path).write_bytes(files[name])

    monkeypatch.setattr(builder_module, "BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(CodexBrandedBuilder, "host_target", classmethod(lambda cls: V8_TARGET))
    monkeypatch.setattr(CodexBrandedBuilder, "download", classmethod(download))
    monkeypatch.setattr(builder_module.subprocess, "run", run)
    return str(export), shown, downloaded


def test_the_prebuilt_v8_is_verified_through_the_manifest_the_submodule_pins(tmp_path, monkeypatch):
    # The chain of trust: the manifest committed in the submodule pins the per-target checksum
    # file, and that file pins the archive and its bindings. Each lands in a per-version cache.
    export, shown, downloaded = _v8_release(tmp_path, monkeypatch)
    env = CodexBrandedBuilder.fetch_rusty_v8(export)
    assert shown == ["HEAD:third_party/v8/rusty_v8_146_4_0_release_manifests.sha256"]
    assert downloaded == [V8_CHECKSUMS, V8_ARCHIVE, V8_BINDING]
    cache = tmp_path / "cache" / "rusty_v8" / "146.4.0"
    assert env == {
        "RUSTY_V8_ARCHIVE": str(cache / V8_ARCHIVE),
        "RUSTY_V8_SRC_BINDING_PATH": str(cache / V8_BINDING),
    }
    assert Path(env["RUSTY_V8_ARCHIVE"]).read_bytes() == b"v8 archive"
    assert Path(env["RUSTY_V8_SRC_BINDING_PATH"]).read_bytes() == b"// v8 bindings"


@pytest.mark.parametrize(
    "returncode, listed",
    [(128, True), (0, False)],
    ids=["git-show-failed", "target-not-pinned"],
)
def test_no_v8_is_fetched_without_a_pinned_checksum_for_this_target(
    tmp_path, monkeypatch, returncode, listed
):
    manifest = f"{'1' * 64}  {V8_CHECKSUMS}\n" if listed else f"{'1' * 64}  other.sha256\n"
    export, _, downloaded = _v8_release(
        tmp_path, monkeypatch, manifest_returncode=returncode, manifest=manifest
    )
    assert CodexBrandedBuilder.fetch_rusty_v8(export) is None
    assert downloaded == []


@pytest.mark.parametrize("tampered", [V8_CHECKSUMS, V8_ARCHIVE, V8_BINDING])
def test_a_v8_file_that_does_not_match_its_pin_is_discarded(tmp_path, monkeypatch, tampered):
    # A swapped checksum file vouches for a swapped archive; neither may reach Cargo or the cache.
    evil = b"not v8"
    served = {tampered: evil}
    if tampered == V8_CHECKSUMS:
        served = {
            V8_ARCHIVE: evil,
            V8_CHECKSUMS: f"{_digest(evil)}  {V8_ARCHIVE}\n{_digest(b'// v8 bindings')}  {V8_BINDING}\n".encode(),
        }
    export, _, downloaded = _v8_release(tmp_path, monkeypatch, served=served)
    assert CodexBrandedBuilder.fetch_rusty_v8(export) is None
    assert tampered in downloaded
    assert not (tmp_path / "cache" / "rusty_v8" / "146.4.0" / tampered).exists()


def test_a_cached_v8_file_is_reused_only_while_it_still_matches(tmp_path, monkeypatch):
    export, _, downloaded = _v8_release(tmp_path, monkeypatch)
    first = CodexBrandedBuilder.fetch_rusty_v8(export)
    assert CodexBrandedBuilder.fetch_rusty_v8(export) == first
    assert downloaded == [V8_CHECKSUMS, V8_ARCHIVE, V8_BINDING]

    # A cached archive changed on disk is fetched again, never handed to Cargo as it is.
    Path(first["RUSTY_V8_ARCHIVE"]).write_bytes(b"changed on disk")
    assert CodexBrandedBuilder.fetch_rusty_v8(export) == first
    assert downloaded[3:] == [V8_ARCHIVE]
    assert Path(first["RUSTY_V8_ARCHIVE"]).read_bytes() == b"v8 archive"


def test_a_failed_v8_download_stops_the_fetch(tmp_path, monkeypatch):
    def offline(cls, url, path):
        raise TimeoutError("timed out")

    export, _, _ = _v8_release(tmp_path, monkeypatch)
    monkeypatch.setattr(CodexBrandedBuilder, "download", classmethod(offline))
    assert CodexBrandedBuilder.fetch_rusty_v8(export) is None


@pytest.mark.parametrize("missing", [V8_ARCHIVE, V8_BINDING])
def test_a_checksum_file_that_omits_a_v8_file_stops_the_fetch(tmp_path, monkeypatch, missing):
    kept = V8_BINDING if missing == V8_ARCHIVE else V8_ARCHIVE
    checksums = f"{'2' * 64}  {kept}\n".encode()
    export, _, downloaded = _v8_release(
        tmp_path,
        monkeypatch,
        served={V8_CHECKSUMS: checksums},
        manifest=f"{_digest(checksums)}  {V8_CHECKSUMS}\n",
    )
    assert CodexBrandedBuilder.fetch_rusty_v8(export) is None
    assert downloaded == [V8_CHECKSUMS]


def test_the_build_key_changes_with_the_lockfile_and_the_profile_overrides(tmp_path, monkeypatch):
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text("one")
    monkeypatch.setattr(builder_module, "CODEX_LOCKFILE", str(lockfile))
    monkeypatch.setattr(CodexBrandedBuilder, "source_commit", classmethod(lambda cls: "a" * 40))
    first = CodexBrandedBuilder.build_key()
    lockfile.write_text("two")
    second = CodexBrandedBuilder.build_key()
    assert second != first
    monkeypatch.setitem(builder_module.RELEASE_PROFILE_OVERRIDES, "CARGO_PROFILE_RELEASE_LTO", "fat")
    assert CodexBrandedBuilder.build_key() != second


def test_the_build_key_changes_when_a_launcher_file_is_renamed(tmp_path, monkeypatch):
    # A module's path is part of the crate as much as its text is.
    crate = tmp_path / "ling-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "src" / "a.rs").write_text("// same")
    monkeypatch.setattr(builder_module, "MIGHTLING_CRATE_DIR", str(crate))
    monkeypatch.setattr(CodexBrandedBuilder, "source_commit", classmethod(lambda cls: "a" * 40))
    first = CodexBrandedBuilder.build_key()
    (crate / "src" / "a.rs").rename(crate / "src" / "b.rs")
    assert CodexBrandedBuilder.build_key() != first


def _installed_ling(tmp_path, monkeypatch, key="k"):
    """`ling` and its code-mode host installed under a stamp for `key`, nothing else required."""
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(builder_module, "BUILDS_SIGNAL", False)
    monkeypatch.setattr(builder_module, "BUILDS_DELEGATE", False)
    monkeypatch.setattr(CodexBrandedBuilder, "build_key", classmethod(lambda cls: key))
    (tmp_path / "build-key").write_text("k\n")
    for name in ("ling", "codex-code-mode-host"):
        path = tmp_path / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    return tmp_path / "bin" / "codex-code-mode-host"


def test_ling_is_current_only_under_a_stamp_for_its_inputs(tmp_path, monkeypatch):
    _installed_ling(tmp_path, monkeypatch)
    assert CodexBrandedBuilder.is_current() is True
    (tmp_path / "build-key").write_text("built from older patches\n")
    assert CodexBrandedBuilder.is_current() is False
    (tmp_path / "build-key").unlink()
    assert CodexBrandedBuilder.is_current() is False


@pytest.mark.parametrize("damage", ["missing", "not-executable"])
def test_ling_without_a_runnable_code_mode_host_is_not_current(tmp_path, monkeypatch, damage):
    host = _installed_ling(tmp_path, monkeypatch)
    if damage == "missing":
        host.unlink()
    else:
        host.chmod(0o644)
    assert CodexBrandedBuilder.is_current() is False


def test_a_release_install_is_current_only_with_both_binaries_runnable(tmp_path, monkeypatch):
    # Without a checkout there is no stamp to compare, so the installed binaries are all there is.
    monkeypatch.setattr(CodexBrandedBuilder, "has_source", classmethod(lambda cls: False))
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path))
    assert CodexBrandedBuilder.is_current() is False
    (tmp_path / "bin").mkdir()
    for name in ("ling", "codex-code-mode-host"):
        (tmp_path / "bin" / name).write_text("#!/bin/sh\n")
        (tmp_path / "bin" / name).chmod(0o755)
        assert CodexBrandedBuilder.is_current() is (name == "codex-code-mode-host")
    (tmp_path / "bin" / "codex-code-mode-host").chmod(0o644)
    assert CodexBrandedBuilder.is_current() is False


def test_a_checkout_needs_both_the_patch_series_and_the_launcher_crate(tmp_path, monkeypatch):
    monkeypatch.setattr(builder_module, "CODEX_PATCH_DIR", str(tmp_path / "patches"))
    monkeypatch.setattr(builder_module, "MIGHTLING_CRATE_DIR", str(tmp_path / "ling-rs"))
    (tmp_path / "patches").mkdir()
    assert CodexBrandedBuilder.has_source() is False
    (tmp_path / "ling-rs").mkdir()
    assert CodexBrandedBuilder.has_source() is True
    (tmp_path / "patches").rmdir()
    assert CodexBrandedBuilder.has_source() is False


@pytest.mark.parametrize(
    "returncode, codex_rs", [(0, False), (128, True)], ids=["no-codex-rs", "no-commit"]
)
def test_the_submodule_is_checked_out_only_with_a_commit_and_codex_rs(
    tmp_path, monkeypatch, returncode, codex_rs
):
    if codex_rs:
        (tmp_path / "codex-rs").mkdir()
    monkeypatch.setattr(builder_module, "CODEX_SUBMODULE_DIR", str(tmp_path))
    monkeypatch.setattr(
        builder_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, returncode, "a" * 40 + "\n", ""),
    )
    assert CodexBrandedBuilder.source_commit() is None
    assert CodexBrandedBuilder.build_key() is None


def test_without_the_submodule_nothing_is_exported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(CodexBrandedBuilder, "source_commit", classmethod(lambda cls: None))
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "export")) is False
    assert "git submodule update --init codex" in capsys.readouterr().out
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize("failure", ["git-archive", "unpack"])
def test_a_failed_export_is_not_built(stand_in_codex, tmp_path, monkeypatch, failure):
    # Either half failing leaves a partial tree; with no patches to trip over, only the check stops it.
    def extract(cls, stream, destination):
        stream.read()
        return failure == "git-archive"

    if failure == "git-archive":
        monkeypatch.setattr(CodexBrandedBuilder, "source_commit", classmethod(lambda cls: "f" * 40))
    monkeypatch.setattr(CodexBrandedBuilder, "extract_archive", classmethod(extract))
    monkeypatch.setattr(CodexBrandedBuilder, "patches", classmethod(lambda cls: []))
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "export")) is False


def test_the_launchers_own_build_output_is_not_exported(stand_in_codex, tmp_path):
    (tmp_path / "ling-rs" / "target" / "release").mkdir(parents=True)
    (tmp_path / "ling-rs" / "target" / "release" / "ling").write_text("a stale binary")
    (tmp_path / "ling-rs" / "Cargo.toml").write_text("[package]\n")
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "export"))
    launcher = tmp_path / "export" / builder_module.MIGHTLING_CRATE_DEST
    assert (launcher / "Cargo.toml").is_file()
    assert not (launcher / "target").exists()


def test_a_patch_that_checks_clean_but_fails_to_apply_stops_the_export(
    stand_in_codex, tmp_path, monkeypatch
):
    def git_apply(cls, source_dir, *args):
        return subprocess.CompletedProcess(["git", "apply", *args], 0 if "--check" in args else 1)

    monkeypatch.setattr(CodexBrandedBuilder, "git_apply", classmethod(git_apply))
    with pytest.raises(subprocess.CalledProcessError):
        CodexBrandedBuilder.prepare_source(str(tmp_path / "export"))


def test_an_export_whose_version_cannot_be_stamped_is_not_built(stand_in_codex, tmp_path, monkeypatch):
    monkeypatch.setattr(
        CodexBrandedBuilder, "stamp_version", classmethod(lambda cls, manifest, version: False)
    )
    assert CodexBrandedBuilder.prepare_source(str(tmp_path / "export")) is False


def test_git_apply_drops_an_inherited_repository_and_stops_at_the_export(tmp_path, monkeypatch):
    # A hook or a `git` alias that exported GIT_DIR would point every patch at another repository.
    seen = {}

    def run(command, cwd=None, env=None, check=False):
        seen.update(command=command, cwd=cwd, env=env)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other" / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "other"))
    monkeypatch.setattr(builder_module.subprocess, "run", run)
    export = tmp_path / "work" / "export"
    export.mkdir(parents=True)
    CodexBrandedBuilder.git_apply(str(export), "--check", "0001.patch")
    assert seen["command"] == ["git", "apply", "--check", "0001.patch"]
    assert seen["cwd"] == str(export)
    assert "GIT_DIR" not in seen["env"] and "GIT_WORK_TREE" not in seen["env"]
    assert seen["env"]["GIT_CEILING_DIRECTORIES"] == str((tmp_path / "work").resolve())


@pytest.mark.parametrize("version", ["1.4", "1", '1.4.1"\nbuild = "evil.rs', "1.4.1 "])
def test_only_a_full_cargo_version_is_stamped(tmp_path, version):
    # The version is written into the manifest as it is, so anything after it would be TOML too.
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[workspace.package]\nversion = "0.158.0"\n')
    assert CodexBrandedBuilder.stamp_version(str(manifest), version) is False
    assert manifest.read_text() == '[workspace.package]\nversion = "0.158.0"\n'


def test_the_version_is_stamped_only_inside_workspace_package(tmp_path):
    manifest = tmp_path / "Cargo.toml"
    text = '[workspace.package]\nedition = "2024"\n\n[package]\nname = "x"\nversion = "0.0.1"\n'
    manifest.write_text(text)
    assert CodexBrandedBuilder.stamp_version(str(manifest), "1.4.1") is False
    assert manifest.read_text() == text

    # Keys that end in "version" stay as they are.
    manifest.write_text('[workspace.package]\nrust-version = "1.85"\nversion = "0.158.0"\n')
    assert CodexBrandedBuilder.stamp_version(str(manifest), "1.4.1") is True
    assert manifest.read_text() == '[workspace.package]\nrust-version = "1.85"\nversion = "1.4.1"\n'


def _tar(*members):
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, target in members:
            info = tarfile.TarInfo(name)
            if target is None:
                info.size = 4
                archive.addfile(info, io.BytesIO(b"text"))
            else:
                info.type, info.linkname = tarfile.SYMTYPE, target
                archive.addfile(info)
    buffer.seek(0)
    return buffer


@pytest.mark.parametrize("windows", [False, True])
def test_the_export_keeps_its_symbolic_links_except_on_windows(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(builder_module, "IS_WINDOWS", windows)
    stream = _tar(("codex-rs/LICENSE.txt", None), ("codex-rs/LICENSE", "LICENSE.txt"))
    assert CodexBrandedBuilder.extract_archive(stream, str(tmp_path)) is True
    assert (tmp_path / "codex-rs" / "LICENSE.txt").read_text() == "text"
    assert (tmp_path / "codex-rs" / "LICENSE").is_symlink() is not windows


def test_an_export_that_cannot_be_written_is_reported_not_raised(tmp_path, capsys):
    (tmp_path / "codex-rs").write_text("a file where the tree's directory goes")
    assert CodexBrandedBuilder.extract_archive(_tar(("codex-rs/main.rs", None)), str(tmp_path)) is False
    assert "Could not unpack the exported source" in capsys.readouterr().out


def _codex_build(tmp_path, monkeypatch, **steps):
    """`_build_locked` with every step before and around cargo replaced; `steps` override one.

    Returns the cargo calls and the number of times the links were refreshed.
    """
    calls, linked = [], []
    release = tmp_path / "cache" / "target" / "release"

    def cargo(command, cwd=None, env=None):
        calls.append((command, env))
        release.mkdir(parents=True, exist_ok=True)
        for name in [command[i + 1] for i, arg in enumerate(command) if arg == "--bin"]:
            (release / name).write_text(f"built {name}")
            (release / name).chmod(0o755)
        return 0

    defaults = {
        "install_rust": True,
        "build_key": "k",
        "prepare_source": True,
        "fetch_rusty_v8": {"RUSTY_V8_ARCHIVE": "a", "RUSTY_V8_SRC_BINDING_PATH": "b"},
        "build_web_ui": "/ui/dist",
    }
    result = {**defaults, **steps}
    monkeypatch.setattr(builder_module, "BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(builder_module, "BUILDS_SIGNAL", True)
    monkeypatch.setattr(builder_module, "BUILDS_DELEGATE", True)
    monkeypatch.setattr(builder_module, "IS_WINDOWS", False)
    monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(
        DesktopInstaller,
        "install_rust",
        classmethod(lambda cls: result["install_rust"]),
    )
    monkeypatch.setattr(CodexBrandedBuilder, "build_key", classmethod(lambda cls: result["build_key"]))
    for name in ("prepare_source", "fetch_rusty_v8"):
        monkeypatch.setattr(
            CodexBrandedBuilder, name, classmethod(lambda cls, source_dir, name=name: result[name])
        )
    monkeypatch.setattr(CodexBrandedBuilder, "build_web_ui", classmethod(lambda cls: result["build_web_ui"]))
    monkeypatch.setattr(
        CodexBrandedBuilder, "compiler_cache_environment", classmethod(lambda cls, env: {})
    )
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: linked.append(1)))
    monkeypatch.setattr(builder_module.subprocess, "call", cargo)
    return calls, linked


def test_a_codex_build_installs_every_binary_under_its_name_and_stamps_the_key(tmp_path, monkeypatch):
    calls, linked = _codex_build(tmp_path, monkeypatch)
    assert CodexBrandedBuilder._build_locked() is True
    ((command, env),) = calls
    # The lock pins the third-party crates, but stamp_version rewrites the workspace's own entries.
    assert "--locked" not in command
    assert command[command.index("codex-code-mode-host") - 1] == "-p"
    assert "CARGO_TARGET_DIR" not in env
    bin_dir = tmp_path / "install" / "bin"
    # Cargo's `codex` is installed as `ling`; nothing named codex is put on the machine.
    assert (bin_dir / "ling").read_text() == "built codex"
    assert not (bin_dir / "codex").exists()
    for name in ("codex-code-mode-host", "ling-signal", "ling-delegate"):
        assert (bin_dir / name).read_text() == f"built {name}"
        assert os.access(bin_dir / name, os.X_OK)
    assert (tmp_path / "install" / "build-key").read_text() == "k\n"
    assert linked == [1]
    assert CodexBrandedBuilder.is_current() is True


@pytest.mark.parametrize(
    "step, value",
    [
        ("install_rust", False),
        ("build_key", None),
        ("prepare_source", False),
        ("fetch_rusty_v8", None),
    ],
)
def test_a_codex_build_stops_before_cargo_when_a_step_fails(tmp_path, monkeypatch, step, value):
    calls, linked = _codex_build(tmp_path, monkeypatch, **{step: value})
    assert CodexBrandedBuilder._build_locked() is False
    assert calls == [] and linked == []
    assert not (tmp_path / "install" / "build-key").exists()


def test_a_codex_install_replaces_ling_rather_than_writing_into_it(tmp_path, monkeypatch):
    # A running session executes the old file; writing over it in place fails with ETXTBSY or
    # hands that session a half-written binary.
    _codex_build(tmp_path, monkeypatch)
    bin_dir = tmp_path / "install" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "ling").write_text("the running ling")
    os.link(bin_dir / "ling", tmp_path / "running")
    assert CodexBrandedBuilder._build_locked() is True
    assert (bin_dir / "ling").read_text() == "built codex"
    assert (tmp_path / "running").read_text() == "the running ling"
    assert not [p.name for p in bin_dir.iterdir() if p.name.endswith(".new")]


def test_force_rebuilds_ling_even_when_it_is_current(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(builder_module, "BUILD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(CodexBrandedBuilder, "is_current", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexBrandedBuilder, "_lock_exclusive", classmethod(lambda cls, handle: None))
    monkeypatch.setattr(CodexBrandedBuilder, "_build_locked", classmethod(lambda cls: built.append(1) or True))
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: built.append("link")))
    assert CodexBrandedBuilder._build_codex(force=True) is True
    assert built == [1]
    # Without force a current build is not redone, but its links are still refreshed.
    assert CodexBrandedBuilder._build_codex() is True
    assert built == [1, "link"]


def test_force_reaches_every_build_and_any_failure_fails_the_whole(monkeypatch):
    seen, failing = [], set()
    for name in ("_build_codex", "build_web_tools", "build_code_index", "build_docs_index"):
        monkeypatch.setattr(
            CodexBrandedBuilder,
            name,
            classmethod(
                lambda cls, force=False, name=name: seen.append((name, force)) or name not in failing
            ),
        )
    assert CodexBrandedBuilder.build(force=True) is True
    assert sorted(seen) == [
        ("_build_codex", True),
        ("build_code_index", True),
        ("build_docs_index", True),
        ("build_web_tools", True),
    ]
    for name in ("_build_codex", "build_web_tools", "build_code_index", "build_docs_index"):
        failing = {name}
        seen.clear()
        assert CodexBrandedBuilder.build() is False
        # A failing step does not keep the others from running.
        assert len(seen) == 4


def test_force_rebuilds_a_current_crate_and_without_it_the_links_are_refreshed(tmp_path, monkeypatch):
    crate = _fake_crate(tmp_path, monkeypatch)
    cargo, linked = builder_module.subprocess.call, []
    calls = []
    monkeypatch.setattr(
        builder_module.subprocess, "call", lambda *a, **k: calls.append(1) or cargo(*a, **k)
    )
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: linked.append(1)))

    def build(force=False):
        return CodexBrandedBuilder.build_crate(
            str(crate), str(tmp_path / "cache"), "stamp", ("x",), force=force
        )

    assert build() is True and len(calls) == 1
    linked.clear()
    assert build() is True and len(calls) == 1
    assert linked == [1]
    assert build(force=True) is True and len(calls) == 2


def test_a_crate_is_not_built_without_rust(tmp_path, monkeypatch):
    crate = _fake_crate(tmp_path, monkeypatch)
    monkeypatch.setattr(DesktopInstaller, "install_rust", classmethod(lambda cls: False))
    monkeypatch.setattr(builder_module.subprocess, "call", lambda *a, **k: pytest.fail("cargo ran"))
    assert CodexBrandedBuilder.build_crate(str(crate), str(tmp_path / "cache"), "stamp", ("x",)) is False


def test_without_its_crate_a_crate_binary_is_current_only_if_installed(tmp_path, monkeypatch):
    # A release install has the binaries but not the crate to build them from.
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(tmp_path))
    missing = str(tmp_path / "no-crate")
    assert CodexBrandedBuilder.crate_is_current(missing, "stamp", ("x",)) is False
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "x").write_text("#!/bin/sh\n")
    (tmp_path / "bin" / "x").chmod(0o755)
    assert CodexBrandedBuilder.crate_is_current(missing, "stamp", ("x",)) is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ling-docs is built on Linux")
def test_a_failed_docs_index_build_fails_and_downloads_nothing(monkeypatch):
    from dreamference.runner import docs_index_setup

    monkeypatch.setattr(
        CodexBrandedBuilder, "build_crate", classmethod(lambda cls, *args, force=False: False)
    )
    monkeypatch.setattr(
        docs_index_setup.DocsIndexSetup, "install", classmethod(lambda cls, machine=None: pytest.fail("installed"))
    )
    assert CodexBrandedBuilder.build_docs_index() is False


def test_linking_again_moves_an_existing_link_to_the_new_target(tmp_path):
    link = tmp_path / "bin" / "ling"
    link.parent.mkdir()
    link.symlink_to("/old/install/ling")
    CodexBrandedBuilder._link("/new/install/ling", str(link))
    assert link.readlink() == Path("/new/install/ling")


def test_a_staging_link_left_by_a_crashed_link_of_this_process_is_replaced(tmp_path):
    link = tmp_path / "bin" / "ling"
    link.parent.mkdir()
    Path(f"{link}.{os.getpid()}.new").symlink_to("/left/behind")
    CodexBrandedBuilder._link("/install/ling", str(link))
    assert link.readlink() == Path("/install/ling")
    assert not os.path.lexists(f"{link}.{os.getpid()}.new")


def test_only_a_runnable_console_script_is_linked(tmp_path, monkeypatch):
    # A link to a script this virtualenv does not have would be a dangling ling-admin.
    monkeypatch.setattr(builder_module.sys, "executable", str(tmp_path / "python"))
    assert CodexBrandedBuilder.console_script_path("ling-admin") is None
    script = tmp_path / "ling-admin"
    script.write_text("#!/bin/sh\n")
    assert CodexBrandedBuilder.console_script_path("ling-admin") is None
    script.chmod(0o755)
    assert CodexBrandedBuilder.console_script_path("ling-admin") == str(script)


def test_the_runner_does_not_resolve_a_branded_file_it_cannot_run(tmp_path):
    binary = tmp_path / "bin" / "ling"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    with patch.object(builder_module, "INSTALL_DIR", str(tmp_path)):
        assert CodexInstaller.get_codex_executable() is None


@pytest.mark.parametrize("ling_current, web_current", [(True, False), (False, True)])
def test_ling_is_installed_only_with_both_ling_and_the_web_tools_current(
    monkeypatch, ling_current, web_current
):
    monkeypatch.setattr(CodexBrandedBuilder, "is_current", classmethod(lambda cls: ling_current))
    monkeypatch.setattr(CodexBrandedBuilder, "web_tools_are_current", classmethod(lambda cls: web_current))
    assert CodexInstaller.is_installed() is False


def test_an_installed_ling_is_not_built_again(monkeypatch):
    monkeypatch.setattr(CodexInstaller, "is_installed", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexBrandedBuilder, "build", classmethod(lambda cls, force=False: pytest.fail("built")))
    assert CodexInstaller.install_if_missing() is True


def test_a_release_install_refreshes_its_links_when_asked_to_build(monkeypatch):
    linked = []
    monkeypatch.setattr(CodexBrandedBuilder, "has_source", classmethod(lambda cls: False))
    monkeypatch.setattr(CodexBrandedBuilder, "is_current", classmethod(lambda cls: True))
    monkeypatch.setattr(CodexBrandedBuilder, "link_onto_path", classmethod(lambda cls: linked.append(True)))
    assert CodexBrandedBuilder.build() is True
    assert linked == [True]


def test_a_missing_ling_is_built_and_its_build_decides(monkeypatch):
    monkeypatch.setattr(CodexInstaller, "is_installed", classmethod(lambda cls: False))
    monkeypatch.setattr(CodexBrandedBuilder, "build", classmethod(lambda cls, force=False: False))
    assert CodexInstaller.install_if_missing() is False


def test_without_a_patch_directory_there_is_no_patch_series(tmp_path):
    with patch.object(builder_module, "CODEX_PATCH_DIR", str(tmp_path / "no-patches")):
        assert CodexBrandedBuilder.patches() == []


def test_the_export_names_its_extraction_filter_so_python_cannot_change_it(tmp_path):
    # Without one, Python 3.12 warns and Python 3.14 switches to the stricter "data" filter.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        archive = _tar(("codex-rs/main.rs", None), ("codex-rs/LICENSE", "main.rs"))
        assert CodexBrandedBuilder.extract_archive(archive, str(tmp_path)) is True
