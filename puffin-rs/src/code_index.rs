//! The launcher's two calls to `puffin-code`, the code index router
//! (specs/DREAMFERENCE_PUFFIN_CODE_INDEX.md §4.2). The router is a program of its own, so this is
//! all the launcher knows about it:
//!
//! 1. it starts `puffin-code session --parent-pid <this process>` detached, outside Codex's
//!    sandbox (the launcher runs before Codex sandboxes anything), and continues;
//! 2. it appends what `puffin-code prompt-block` prints to the model's prompt, so the commands and
//!    the prompt that describes them live in one place.
//!
//! 3. it offers the index to the model as tools: the `-c mcp_servers.…` arguments that make Codex
//!    start `puffin-code mcp` for the session ([`with_tools`]), and one sentence of Codex's own
//!    prompt rewritten to name the index before `rg` ([`search_habit`]).
//!
//! With `puffin-code` not installed, all of it is skipped and nothing changes.
//!
//! **Why tools, and why the sentence** (measured 2026-10-02, specs/DREAMFERENCE_PUFFIN_CODE_INDEX.md
//! §15). With the commands only described at the end of the prompt, the model queried the index
//! in 1 of 146 recorded sessions and in none of 24 SWE-bench instances: its habit for "find the
//! code" is `grep`, which Codex's prompt itself teaches ("you reach first for `rg`"), and a
//! program named in prose does not compete with that. Declared as function tools, the same
//! operations were the first thing it called.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;

/// Where `puffin-admin codex build` installs `puffin-code`: beside `puffin`, never looked up on
/// PATH, for the same reason `puffin` itself is not.
pub fn binary() -> Option<PathBuf> {
    let path = match std::env::var_os("PUFFIN_CODE_BIN") {
        Some(path) => PathBuf::from(path),
        None => PathBuf::from(std::env::var_os("HOME")?).join(".local/share/dreamference/puffin/bin/puffin-code"),
    };
    path.is_file().then_some(path)
}

/// The name of the MCP server in Codex's configuration. Its tools reach the model under their own
/// names, `code_def`, `code_search`, … (patch 0020 and `puffin-rs/tools`).
pub const MCP_SERVER: &str = "puffin_code";

/// Environment variables `puffin-code mcp` must see. Codex starts an MCP server with a short
/// allow-list (`HOME`, `PATH`, …), not with its own environment, and these say where the
/// repository, its index and the configuration are when they are not in their default places
/// (a SWE-bench container mounts the index and names it this way).
const FORWARDED_ENV: &[&str] = &[
    "PUFFIN_CODE_ROOT",
    "PUFFIN_CODE_STATE_DIR",
    "PUFFIN_CODE_GRAPH_DB",
    "PUFFIN_CODE_PROJECT",
    "PUFFIN_CODE_TOOLS_DIR",
    "PUFFIN_CODE_INDEXERS_DIR",
    "CODEX_HOME",
    "DREAMFERENCE_CONFIG_PATH",
    "DREAMFERENCE_VLLM_HOST",
];

/// How long the first request of a session waits for MCP servers still starting, in place of
/// Codex's 1 s default (`mcp_optional_startup_grace_ms`). Codex leaves an optional server out of
/// the tool list when it is not up by then, and keeps it out for that first request's turn: on a
/// busy GB10 (three SWE-bench containers starting at once, 2026-10-03) `puffin-code mcp` was not up
/// within that second, the model's `code_search` came back "unsupported call", and it used grep for
/// the rest of the task. The wait ends as soon as the servers are up, so a fast start costs nothing;
/// `required = true` would wait too, but makes a server that fails to start end the session.
pub const STARTUP_GRACE_MS: u64 = 15_000;

/// The sentence of Codex's prompt that teaches the search habit ([`search_habit`]).
pub const RG_SENTENCE: &str = "- When you search for text or files, you reach first for `rg` or `rg --files`; they are much faster than alternatives like `grep`. If `rg` is unavailable, you use the next best tool without fuss.";

/// Whether the index is offered as tools: `DREAMFERENCE_PUFFIN_CODE_TOOLS`, then
/// `puffin_code_tools` in the config file, then on. Off leaves the shell commands and their
/// prompt block, as before 2026-10-02.
pub fn tools_enabled() -> bool {
    if let Ok(setting) = std::env::var("DREAMFERENCE_PUFFIN_CODE_TOOLS")
        && !setting.is_empty()
    {
        return !matches!(setting.to_lowercase().as_str(), "0" | "false" | "no" | "off");
    }
    crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok())
        .and_then(|table| table.get("puffin_code_tools")?.as_bool())
        .unwrap_or(true)
}

/// Adds the arguments that make Codex start `puffin-code mcp` for this session.
///
/// Arguments, not `config.toml`: the file is shared by every session, and whether a repository
/// has an index is a fact about one of them. A `-c mcp_servers.puffin_code…` of the user's own
/// wins: nothing is added then.
pub fn with_tools(args: Vec<OsString>) -> Vec<OsString> {
    let Some(binary) = binary() else { return args };
    let key = format!("mcp_servers.{MCP_SERVER}.");
    if args.iter().any(|arg| arg.to_string_lossy().contains(&key)) {
        return args;
    }
    let quoted = |text: &str| toml::Value::String(text.to_string()).to_string();
    let forwarded: Vec<String> = FORWARDED_ENV.iter().map(|name| quoted(name)).collect();
    let mut settings = vec![
        format!("{key}command={}", quoted(&binary.to_string_lossy())),
        format!("{key}args=[\"mcp\"]"),
        format!("{key}env_vars=[{}]", forwarded.join(", ")),
    ];
    if !args.iter().any(|arg| arg.to_string_lossy().contains("mcp_optional_startup_grace_ms")) {
        settings.push(format!("mcp_optional_startup_grace_ms={STARTUP_GRACE_MS}"));
    }
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    for setting in settings {
        out.extend(["-c".into(), OsString::from(setting)]);
    }
    out.extend(args);
    out
}

/// Rewrites the one sentence of Codex's prompt that says what to search with.
///
/// It tells the model to "reach first for `rg`" for text *or files*, which is the habit that kept
/// the index unused; and `rg` is not installed on a GB10 or in a SWE-bench image, so every
/// session also began with a command that failed. `index` is how the index is reached (the
/// `code_*` tools, or `puffin-code`), or None when the repository has none.
pub fn search_habit(prompt: &str, index: Option<&str>, rg_installed: bool) -> String {
    let text = if rg_installed {
        "you use `rg` or `rg --files`; they are much faster than alternatives like `grep`. If `rg` is unavailable, you use the next best tool without fuss."
    } else {
        "you use `grep -rn` and `find`: `rg` is not installed on this machine."
    };
    let sentence = match index {
        Some(index) => format!(
            "- When you look for code (where something is defined, used or called, or which code does something), you reach first for {index}; the Code navigation section says how. For text that is not code, and for files, {text}"
        ),
        None if rg_installed => return prompt.to_string(),
        None => format!("- When you search for text or files, {text}"),
    };
    prompt.replacen(RG_SENTENCE, &sentence, 1)
}

/// How the tools are named to the model in the rewritten sentence.
pub const TOOLS_NAME: &str = "the `code_*` tools";

/// How a prompt block says the index is reached: the tools, the shell command, or None when
/// there is no block or the index is still being built (its block says to use `rg` until then).
pub fn named_in(block: &str) -> Option<&'static str> {
    if !block.contains("# Code navigation") || block.contains("is being built") {
        None
    } else if block.contains("`code_*` tools") {
        Some(TOOLS_NAME)
    } else {
        Some("`puffin-code`")
    }
}

/// Whether `rg` is on `PATH`.
pub fn rg_installed() -> bool {
    std::env::var_os("PATH").is_some_and(|path| std::env::split_paths(&path).any(|dir| dir.join("rg").is_file()))
}

/// The directory a session works in: `-C`/`--cd` when given (relative to the current directory),
/// else the current directory.
///
/// The index is the repository's there, not the launcher's own directory: a night task or a
/// SWE-bench instance runs `puffin exec -C <tree>` from elsewhere, and asking `puffin-code` in the
/// launcher's directory found no repository, so the session got neither block nor tools (measured
/// 2026-10-02: 0 of 5 tasks used the index that way, against 12 of 12 started inside the tree).
pub fn session_dir(user_args: &[String]) -> PathBuf {
    let here = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let mut chosen = None;
    let mut args = user_args.iter();
    while let Some(arg) = args.next() {
        if arg == "-C" || arg == "--cd" {
            chosen = args.next().cloned();
        } else if let Some(value) = arg.strip_prefix("--cd=") {
            chosen = Some(value.to_string());
        } else if let Some(value) = arg.strip_prefix("-C").filter(|rest| !rest.is_empty()) {
            chosen = Some(value.to_string());
        }
    }
    chosen.map_or(here.clone(), |dir| here.join(dir))
}

/// Starts the session process and returns the prompt block (empty when there is none), both for
/// the repository at `dir`. With `tools`, the block names the `code_*` tools instead of the shell
/// commands.
pub fn start_and_prompt_block(tools: bool, dir: &std::path::Path) -> String {
    let Some(binary) = binary() else { return String::new() };
    // Its own process group, so Ctrl-C in the TUI does not reach it; it exits with this process.
    let _ = {
        use std::os::unix::process::CommandExt;
        Command::new(&binary)
            .args(["session", "--parent-pid", &std::process::id().to_string()])
            .current_dir(dir)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
    };
    block_from(&binary, tools, dir)
}

/// The prompt block alone, starting nothing: what `puffin prompt show` appends.
pub fn prompt_block(tools: bool, dir: &std::path::Path) -> String {
    binary().map(|binary| block_from(&binary, tools, dir)).unwrap_or_default()
}

fn block_from(binary: &std::path::Path, tools: bool, dir: &std::path::Path) -> String {
    let mut prompt_block = Command::new(binary);
    prompt_block.arg("prompt-block").current_dir(dir);
    if tools {
        prompt_block.arg("--tools");
    }
    let Ok(output) = prompt_block.stdin(Stdio::null()).stderr(Stdio::null()).output() else {
        return String::new();
    };
    let block = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() || block.is_empty() {
        return String::new();
    }
    format!("\n\n{block}\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The tests that set `PUFFIN_CODE_BIN` take turns: tests run in parallel and the environment
    /// is the process's.
    static BIN_ENV: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn without_puffin_code_nothing_starts_and_no_block_is_added() {
        let _turn = BIN_ENV.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        // SAFETY: tests in this module are the only readers of PUFFIN_CODE_BIN.
        unsafe { std::env::set_var("PUFFIN_CODE_BIN", "/nonexistent/puffin-code") };
        assert_eq!(binary(), None);
        assert_eq!(start_and_prompt_block(true, std::path::Path::new(".")), "");
        // No binary, no tools: the arguments are returned as they came.
        let args: Vec<OsString> = vec!["puffin".into(), "exec".into()];
        assert_eq!(with_tools(args.clone()), args);
        unsafe { std::env::remove_var("PUFFIN_CODE_BIN") };
    }

    #[test]
    fn the_session_directory_follows_dash_c() {
        let here = std::env::current_dir().unwrap_or_default();
        let args = |words: &[&str]| words.iter().map(|w| w.to_string()).collect::<Vec<_>>();
        assert_eq!(session_dir(&args(&["exec", "hi"])), here);
        assert_eq!(session_dir(&args(&["exec", "-C", "/srv/tree", "hi"])), std::path::PathBuf::from("/srv/tree"));
        assert_eq!(session_dir(&args(&["--cd=sub", "exec"])), here.join("sub"));
        assert_eq!(session_dir(&args(&["-Csub", "exec"])), here.join("sub"));
    }

    #[test]
    fn the_index_is_offered_as_an_mcp_server_for_this_session_only() {
        let _turn = BIN_ENV.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let dir = std::env::temp_dir().join(format!("puffin-code-tools-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap_or_default();
        let binary = dir.join("puffin-code");
        std::fs::write(&binary, "").unwrap_or_default();
        // SAFETY: tests in this module are the only readers of PUFFIN_CODE_BIN.
        unsafe { std::env::set_var("PUFFIN_CODE_BIN", &binary) };
        let out: Vec<String> = with_tools(vec!["puffin".into(), "exec".into(), "hi".into()])
            .iter()
            .map(|arg| arg.to_string_lossy().into_owned())
            .collect();
        assert_eq!(out[0], "puffin");
        assert_eq!(out[1], "-c");
        assert_eq!(out[2], format!("mcp_servers.puffin_code.command=\"{}\"", binary.display()));
        assert_eq!(out[4], "mcp_servers.puffin_code.args=[\"mcp\"]");
        assert!(out[6].starts_with("mcp_servers.puffin_code.env_vars=[\"PUFFIN_CODE_ROOT\", "), "{}", out[6]);
        assert_eq!(out[8], format!("mcp_optional_startup_grace_ms={STARTUP_GRACE_MS}"));
        assert_eq!(&out[9..], ["exec", "hi"]);
        // Each value is TOML, which is what Codex parses a `-c` value as.
        for setting in [&out[2], &out[4], &out[6], &out[8]] {
            let (_, value) = setting.split_once('=').unwrap_or_default();
            assert!(format!("v = {value}").parse::<toml::Table>().is_ok(), "{setting}");
        }
        // The user's own setting for the server wins.
        let own: Vec<OsString> = vec!["puffin".into(), "-c".into(), "mcp_servers.puffin_code.enabled=false".into()];
        assert_eq!(with_tools(own.clone()), own);
        // So does the user's own grace.
        let grace: Vec<String> = with_tools(vec!["puffin".into(), "-c".into(), "mcp_optional_startup_grace_ms=0".into()])
            .iter()
            .map(|arg| arg.to_string_lossy().into_owned())
            .collect();
        assert_eq!(grace.iter().filter(|arg| arg.contains("mcp_optional_startup_grace_ms")).count(), 1);
        unsafe { std::env::remove_var("PUFFIN_CODE_BIN") };
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_search_sentence_names_the_index_and_the_truth_about_rg() {
        // The sentence must still be in Codex's prompt: a Codex bump that rewords it fails here,
        // not silently in every session.
        let prompt = crate::base_instructions();
        assert_eq!(prompt.matches(RG_SENTENCE).count(), 1, "Codex's prompt no longer has the `rg` sentence");
        let with_tools = search_habit(&prompt, Some(TOOLS_NAME), false);
        assert!(!with_tools.contains("reach first for `rg`"));
        assert!(with_tools.contains("you reach first for the `code_*` tools; the Code navigation section says how."));
        assert!(with_tools.contains("you use `grep -rn` and `find`: `rg` is not installed on this machine."));
        // No index and `rg` installed: Codex's sentence stands.
        assert_eq!(search_habit(&prompt, None, true), prompt);
        // No index, no `rg`: the sentence stops promising a tool that is not there.
        let plain = search_habit(&prompt, None, false);
        assert!(plain.contains("- When you search for text or files, you use `grep -rn` and `find`"));
        // Which wording a block calls for.
        assert_eq!(named_in(""), None);
        assert_eq!(named_in("# Code navigation\n\nA code index for this repository is being built."), None);
        assert_eq!(named_in("# Code navigation\n\nbehind the `code_*` tools"), Some(TOOLS_NAME));
        assert_eq!(named_in("# Code navigation\n\n`puffin-code def`"), Some("`puffin-code`"));
    }
}
