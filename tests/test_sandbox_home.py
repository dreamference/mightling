"""The home folder hidden from sandboxed commands (specs/DREAMFERENCE_MIGHTLING_SANDBOX_HOME.md;
security review 2026-10, finding 13): what the Python suite can check without the Codex workspace.
The rule's own tests are Rust (`cargo test --release -p ling-sandbox-home` in the build's export)."""

import re
from pathlib import Path

from dreamference.config.config_file_storage_manager import ConfigFileStorageManager
from dreamference.config import DreamferenceConfig

REPO = Path(__file__).resolve().parent.parent
LEAF = REPO / "ling-rs" / "sandbox-home"
LEAF_RS = LEAF / "src" / "lib.rs"
PATCH_0019 = REPO / "codex-patches" / "0019-airgapped.patch"


def rust_list(name: str) -> list:
    """The strings of a `pub const NAME: &[&str] = &[...];` in the leaf crate."""
    match = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", LEAF_RS.read_text(), re.S)
    assert match, name
    return re.findall(r'"([^"]*)"', match.group(1))


def test_the_sandbox_helper_hands_every_profile_to_the_leaf_crate():
    patch = PATCH_0019.read_text()
    assert '+ling-sandbox-home = { path = "../ling/sandbox-home" }' in patch
    # After the air-gap hook, so the network decision and the filesystem rule see the same profile.
    assert patch.index("ling_airgapped::sealed_for_command()") < patch.index(
        "+    ling_sandbox_home::hide_home(&mut permission_profile);")
    assert 'name = "ling-sandbox-home"' in (LEAF / "Cargo.toml").read_text()
    launcher = (REPO / "ling-rs" / "Cargo.toml").read_text()
    assert 'ling-sandbox-home = { path = "sandbox-home" }' in launcher
    assert 'user_args[index] == "sandbox-home"' in (REPO / "ling-rs" / "src" / "lib.rs").read_text()


def test_nothing_secret_is_readable_by_default():
    readable = rust_list("DEFAULT_READABLE")
    assert ".cargo" in readable and ".rustup" in readable and ".local/bin" in readable
    for secret in (".ssh", ".gnupg", ".mozilla", ".config/google-chrome", ".config/chromium",
                   "snap", ".aws", ".azure", ".kube", ".docker", ".netrc", ".npmrc", ".pypirc",
                   ".git-credentials", ".config/gh", ".password-store", ".local/share/keyrings",
                   ".mightling", ".codex", ".claude", ".config/dreamference", ".cache",
                   ".local", ".local/share", ".config", ".cache/huggingface", ".bashrc", ".profile"):
        assert secret not in readable, secret
    # Only the configuration file of ~/.config/dreamference: Gmail, pairing and signing keys stay hidden.
    assert [path for path in readable if path.startswith(".config/dreamference")] == [
        ".config/dreamference/config.toml"]


def test_only_what_commands_need_of_codex_home_is_readable():
    readable = rust_list("CODEX_HOME_READABLE")
    assert {"skills", "shell_snapshots", "tmp", "airgapped"} <= set(readable)
    for private in ("sessions", "history.jsonl", "config.toml", "web", "ask", "docs", "night",
                    "memories_1.sqlite", "state_5.sqlite", "logs_2.sqlite", "auth.json",
                    "app-server-control", "audit"):
        assert private not in readable, private


def test_credential_files_inside_readable_folders_are_hidden_again():
    readable = rust_list("DEFAULT_READABLE")
    hidden = rust_list("DEFAULT_HIDDEN")
    assert ".cargo/credentials.toml" in hidden
    for path in hidden:
        assert any(path.startswith(folder + "/") for folder in readable) or path.startswith(".cache/huggingface/"), path


def test_the_user_s_sandbox_table_survives_ling_admin_rewriting_the_file(tmp_path):
    # The settings are a table, not flat keys: save_config writes the flat keys it knows and keeps
    # tables, so `ling-admin main-model set` cannot drop a path the user added.
    config = tmp_path / "config.toml"
    config.write_text('vllm_host = "http://127.0.0.1:8000"\n\n[sandbox]\nreadable = ["~/datasets"]\nhome = "hidden"\n')
    DreamferenceConfig(config_file=str(config)).save_config()
    saved = ConfigFileStorageManager.load_config_dict(config)
    assert saved["sandbox"] == {"readable": ["~/datasets"], "home": "hidden"}


def test_the_spec_s_live_check_uses_the_profile_the_rust_tests_parse():
    rust = re.search(r'const LIVE_CHECK_PROFILE: &str = r#"(.*?)"#;', LEAF_RS.read_text()).group(1)
    spec = (REPO / "specs" / "DREAMFERENCE_MIGHTLING_SANDBOX_HOME.md").read_text()
    assert f"P='{rust}'" in spec
