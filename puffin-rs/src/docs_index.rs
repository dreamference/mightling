//! The launcher's calls to `ling-docs`, the local file index
//! (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §6, §8). Like the code index (code_index.rs), the
//! index is a program of its own and this is all the launcher knows about it:
//!
//! 1. `puffin docs <command>` runs `ling-docs <command>` (`ling docs add|remove|search|read|status`);
//! 2. at a session's start it starts `ling-docs session --parent-pid <this process>` detached,
//!    outside the sandbox, which keeps every collection indexed;
//! 3. it appends what `ling-docs prompt-block --tools` prints, and when that block is not empty
//!    (a collection exists) it offers `docs_search` and `docs_read` through the `-c mcp_servers.…`
//!    arguments that make Codex start `ling-docs mcp` for the session.
//!
//! With `ling-docs` not installed, or `mightling_docs = false`, all of it is skipped. Every
//! collection is visible to every session (§14.2), so nothing here depends on the directory.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::{Command, Stdio};

/// Where `ling-admin codex build` installs `ling-docs`: beside `puffin`, never looked up on PATH.
pub fn binary() -> Option<PathBuf> {
    let path = match std::env::var_os("LING_DOCS_BIN") {
        Some(path) => PathBuf::from(path),
        None => PathBuf::from(std::env::var_os("HOME")?).join(".local/share/dreamference/puffin/bin/ling-docs"),
    };
    path.is_file().then_some(path)
}

/// The MCP server's name in Codex's configuration; its tools reach the model as `docs_search`
/// and `docs_read` (patch 0020).
pub const MCP_SERVER: &str = "ling_docs";

/// What `ling-docs mcp` must see beyond Codex's short allow-list: where the home, the settings
/// and the installed model are when they are not in their default places.
const FORWARDED_ENV: &[&str] = &[
    "CODEX_HOME",
    "DREAMFERENCE_CONFIG_PATH",
    "DREAMFERENCE_MIGHTLING_DOCS",
    "DREAMFERENCE_MIGHTLING_DOCS_BM25_WEIGHT",
    "MIGHTLING_DOCS_LIB_DIR",
    "MIGHTLING_DOCS_MODEL_DIR",
];

/// `mightling_docs`: the environment's `DREAMFERENCE_MIGHTLING_DOCS`, then the config file, then on.
pub fn enabled(config_file: Option<PathBuf>) -> bool {
    if let Ok(setting) = std::env::var("DREAMFERENCE_MIGHTLING_DOCS")
        && !setting.is_empty()
    {
        return !matches!(setting.to_lowercase().as_str(), "0" | "false" | "no" | "off");
    }
    config_file
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok())
        .and_then(|table| table.get("mightling_docs")?.as_bool())
        .unwrap_or(true)
}

/// `puffin docs …`: runs `ling-docs …` and returns its exit code.
pub fn run_cli(args: &[String]) -> i32 {
    let Some(binary) = binary() else {
        eprintln!("The local file index (ling-docs) is not installed: `puffin-admin codex build` builds it.");
        return 1;
    };
    match Command::new(&binary).args(args).status() {
        Ok(status) => status.code().unwrap_or(1),
        Err(error) => {
            eprintln!("cannot run {}: {error}", binary.display());
            1
        }
    }
}

/// Starts the session process and returns the prompt block (empty when there is none).
pub fn start_and_prompt_block(enabled: bool) -> String {
    let Some(binary) = binary().filter(|_| enabled) else { return String::new() };
    let _ = {
        use std::os::unix::process::CommandExt;
        Command::new(&binary)
            .args(["session", "--parent-pid", &std::process::id().to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
    };
    block_from(&binary)
}

/// The prompt block alone, starting nothing.
pub fn prompt_block(enabled: bool) -> String {
    binary().filter(|_| enabled).map(|binary| block_from(&binary)).unwrap_or_default()
}

fn block_from(binary: &std::path::Path) -> String {
    let Ok(output) = Command::new(binary).args(["prompt-block", "--tools"]).stdin(Stdio::null()).stderr(Stdio::null()).output() else {
        return String::new();
    };
    let block = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() || block.is_empty() {
        return String::new();
    }
    format!("\n\n{block}\n")
}

/// Adds the arguments that make Codex start `ling-docs mcp`, when `block` (the session's docs
/// block) says a collection exists. A `-c mcp_servers.ling_docs…` of the user's own wins.
pub fn with_tools(args: Vec<OsString>, block: &str) -> Vec<OsString> {
    if !block.contains("# Your documents") {
        return args;
    }
    let Some(binary) = binary() else { return args };
    let key = format!("mcp_servers.{MCP_SERVER}.");
    if args.iter().any(|arg| arg.to_string_lossy().contains(&key)) {
        return args;
    }
    let quoted = |text: &str| toml::Value::String(text.to_string()).to_string();
    let forwarded: Vec<String> = FORWARDED_ENV.iter().map(|name| quoted(name)).collect();
    let settings = [
        format!("{key}command={}", quoted(&binary.to_string_lossy())),
        format!("{key}args=[\"mcp\"]"),
        format!("{key}env_vars=[{}]", forwarded.join(", ")),
    ];
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    for setting in settings {
        out.extend(["-c".into(), OsString::from(setting)]);
    }
    out.extend(args);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    static BIN_ENV: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn without_ling_docs_nothing_is_added() {
        let _turn = BIN_ENV.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        // SAFETY: the tests of this module are the only readers of LING_DOCS_BIN.
        unsafe { std::env::set_var("LING_DOCS_BIN", "/nonexistent/ling-docs") };
        assert_eq!(start_and_prompt_block(true), "");
        let args: Vec<OsString> = vec!["puffin".into(), "exec".into()];
        assert_eq!(with_tools(args.clone(), "\n\n# Your documents\n"), args);
        assert_eq!(run_cli(&["status".to_string()]), 1);
        unsafe { std::env::remove_var("LING_DOCS_BIN") };
    }

    #[test]
    fn the_tools_are_offered_only_with_a_collection_and_the_users_setting_wins() {
        let _turn = BIN_ENV.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let dir = std::env::temp_dir().join(format!("ling-docs-tools-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap_or_default();
        let binary = dir.join("ling-docs");
        std::fs::write(&binary, "").unwrap_or_default();
        // SAFETY: the tests of this module are the only readers of LING_DOCS_BIN.
        unsafe { std::env::set_var("LING_DOCS_BIN", &binary) };
        let base: Vec<OsString> = vec!["puffin".into(), "exec".into(), "hi".into()];
        assert_eq!(with_tools(base.clone(), ""), base, "no collection, no tools");
        let out: Vec<String> = with_tools(base.clone(), "\n\n# Your documents\n\nx").iter().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(out[2], format!("mcp_servers.ling_docs.command=\"{}\"", binary.display()));
        assert_eq!(out[4], "mcp_servers.ling_docs.args=[\"mcp\"]");
        assert!(out[6].starts_with("mcp_servers.ling_docs.env_vars=[\"CODEX_HOME\""), "{}", out[6]);
        assert_eq!(&out[7..], ["exec", "hi"]);
        for setting in [&out[2], &out[4], &out[6]] {
            let (_, value) = setting.split_once('=').unwrap_or_default();
            assert!(format!("v = {value}").parse::<toml::Table>().is_ok(), "{setting}");
        }
        let own: Vec<OsString> = vec!["puffin".into(), "-c".into(), "mcp_servers.ling_docs.enabled=false".into()];
        assert_eq!(with_tools(own.clone(), "# Your documents"), own);
        unsafe { std::env::remove_var("LING_DOCS_BIN") };
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_setting_reads_the_environment_then_the_file() {
        let dir = std::env::temp_dir().join(format!("ling-docs-setting-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap_or_default();
        let file = dir.join("config.toml");
        std::fs::write(&file, "mightling_docs = false\n").unwrap_or_default();
        if std::env::var_os("DREAMFERENCE_MIGHTLING_DOCS").is_none() {
            assert!(!enabled(Some(file.clone())));
            assert!(enabled(None));
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
}
