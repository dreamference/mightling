//! What the compatibility mode's TUI hooks ask the launcher (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md
//! §6.5.5, §6.5.6). Each hook in Codex is one call into this file, and outside the mode each call
//! answers that Codex's own behaviour stands:
//!
//! - [`enter`]: `Enter` in the composer. A draft ending in `\` gets a newline instead of being
//!   sent, and a slash command Claude Code names but Codex does not is run under Codex's name or
//!   answered here (the slash-command fallback).
//! - [`double_press_exit`]: whether `Ctrl+C` and `Ctrl+D` on an empty prompt ask for a second
//!   press, as in Claude Code, instead of quitting at once.
//! - [`transcript`]: `[` and `v` in the transcript overlay put the whole conversation in the
//!   session's history, which the classic renderer keeps in the terminal's scrollback, or open
//!   it in `$VISUAL`/`$EDITOR`.
//! - [`shift_tab_cycles_approvals`]: whether `Shift+Tab` cycles `ling`'s approval modes, as Claude
//!   Code's cycles its permission modes, instead of Codex's collaboration modes (owner's decision
//!   D6). The cycle Codex offers holds Read Only and the default mode only, never Full Access, so
//!   it never lands on Full Access at `/airgapped on` (§16.1).
//!
//! The mode is read once per process: it is fixed for a session's life.

use std::cell::Cell;
use std::path::Path;
use std::path::PathBuf;
use std::sync::OnceLock;

thread_local! {
    static FORCED: Cell<Option<bool>> = const { Cell::new(None) };
}

/// Makes this thread see the mode on or off, whatever the process's setting: for tests of the
/// hooks inside Codex's own test suite (codex-tests/patches), which run on one thread each.
pub fn force_on_this_thread(on: bool) {
    FORCED.with(|forced| forced.set(Some(on)));
}

/// Whether this process runs in the mode, read once.
fn on() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    FORCED.with(Cell::get).unwrap_or_else(|| *ON.get_or_init(|| crate::compat::mode_now() == crate::compat::Mode::Claude))
}

/// What `Enter` does to the composer's text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Enter {
    /// Replace the draft and keep editing (`\` + `Enter`).
    Draft(String),
    /// Submit this text instead: a Codex command under the name Claude Code gives it.
    Run(String),
    /// Clear the draft and show these lines.
    Say(String),
}

/// Claude Code's commands that are Codex's under another name: Claude Code's name, Codex's.
const RENAMED: &[(&str, &str)] = &[
    ("branch", "fork"),
    ("keybindings", "keymap"),
    ("tasks", "ps"),
    ("plugin", "plugins"),
    ("cost", "usage"),
    ("stats", "usage"),
    ("context", "status"),
];

/// Claude Code's commands that are not part of the mode: those tied to Anthropic's services or
/// accounts, and a few whose job Mightling does differently.
const NOT_IN_THE_MODE: &[&str] = &[
    "login", "logout", "upgrade", "usage-credits", "passes", "privacy-settings", "rate-limit-options", "mobile",
    "desktop", "remote-control", "remote-env", "teleport", "web-setup", "install-github-app", "install-slack-app",
    "chrome", "schedule", "ultrareview", "autofix-pr", "artifacts", "advisor", "fast", "insights", "release-notes",
    "feedback", "bug", "radio", "stickers", "powerup", "heapdump", "focus", "output-style", "color", "scroll-speed",
    "terminal-setup", "sandbox", "background", "voice",
];

/// `Enter` with this draft in the composer: what to do instead of Codex's own handling, or None.
pub fn enter(text: &str) -> Option<Enter> {
    if !on() {
        return None;
    }
    enter_in_mode(text, &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

/// [`enter`] for a session in the mode, working in `dir`.
pub fn enter_in_mode(text: &str, dir: &Path) -> Option<Enter> {
    if let Some(kept) = text.strip_suffix('\\')
        && !kept.ends_with('\\')
    {
        return Some(Enter::Draft(format!("{kept}\n")));
    }
    slash(text, dir)
}

/// The slash-command fallback: a command Claude Code names and Codex does not.
fn slash(text: &str, dir: &Path) -> Option<Enter> {
    let line = text.trim();
    let rest = line.strip_prefix('/')?;
    let (name, args) = rest.split_once(char::is_whitespace).map_or((rest, ""), |(n, a)| (n, a.trim()));
    if let Some((_, codex)) = RENAMED.iter().find(|(claude, _)| *claude == name) {
        return Some(Enter::Run(if args.is_empty() { format!("/{codex}") } else { format!("/{codex} {args}") }));
    }
    let say = |lines: Vec<String>| Some(Enter::Say(lines.join("\n")));
    match name {
        "memory" => say(memory_lines(dir)),
        "config" | "doctor" => say(crate::compat::report_lines()),
        "help" => say(help_lines()),
        "rewind" | "checkpoint" | "undo" => say(vec![
            "To go back to an earlier message, press Esc twice on an empty prompt and choose it.".to_string(),
            "This restores the conversation only: changes made to files are not undone.".to_string(),
        ]),
        "add-dir" => say(vec![format!(
            "A folder is added when a session starts: ling --compat claude --add-dir {}",
            if args.is_empty() { "<dir>" } else { args }
        )]),
        "reload-skills" => say(vec!["Skills are read when a session starts; start a new session to read them again.".to_string()]),
        "effort" => say(vec!["Alt+, lowers the reasoning effort and Alt+. raises it.".to_string()]),
        _ if NOT_IN_THE_MODE.contains(&name) => say(vec![format!("/{name} is not part of the compatibility mode.")]),
        // A custom command (.claude/commands) or a skill, typed the way Claude Code types them.
        _ => crate::compat_commands::expand_prompt(line, dir).map(Enter::Run),
    }
}

/// The memory files a session here would read, Claude Code's and Mightling's, each marked
/// present or not.
pub fn memory_lines(dir: &Path) -> Vec<String> {
    let mut files: Vec<PathBuf> = Vec::new();
    if let Some(home) = ling_node_locator::home_dir() {
        files.push(home.join(".claude").join("CLAUDE.md"));
    }
    let root = crate::compat_tui::project_root(dir);
    let mut folder = Some(dir);
    let mut chain = Vec::new();
    while let Some(current) = folder {
        chain.push(current.to_path_buf());
        if current == root {
            break;
        }
        folder = current.parent();
    }
    for current in chain.iter().rev() {
        for name in ["AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md"] {
            files.push(current.join(name));
        }
    }
    let mut lines = vec!["Memory files for this folder:".to_string()];
    for file in files.iter().filter(|file| file.is_file()) {
        lines.push(format!("  {}", file.display()));
    }
    if lines.len() == 1 {
        lines.push("  none yet (AGENTS.md or CLAUDE.md in the project, ~/.claude/CLAUDE.md for you)".to_string());
    }
    lines.push("Open one in your editor; the session reads them when it starts.".to_string());
    lines
}

fn help_lines() -> Vec<String> {
    vec![
        "Compatibility mode: Claude Code's keys and command names in ling.".to_string(),
        "  /  commands · ! shell · @ files · ? shortcuts · Esc interrupts · Esc Esc goes back".to_string(),
        "  Ctrl+O transcript ([ to scrollback, v to the editor) · \\ then Enter, or Ctrl+J, for a newline".to_string(),
        "  Shift+Tab approval mode · Ctrl+C twice or Ctrl+D twice to exit".to_string(),
        format!(
            "  Claude Code names: {} · /memory /config /doctor /help /rewind /add-dir /effort",
            RENAMED.iter().map(|(claude, codex)| format!("/{claude} (/{codex})")).collect::<Vec<_>>().join(" ")
        ),
        "  `ling compat doctor` lists what the mode reads and what differs.".to_string(),
    ]
}

/// Whether `Ctrl+C` and `Ctrl+D` on an empty prompt ask for a second press.
pub fn double_press_exit() -> bool {
    on()
}

/// Whether `Shift+Tab` cycles the approval modes.
pub fn shift_tab_cycles_approvals() -> bool {
    on()
}

/// What `[` or `v` in the transcript overlay does: write the conversation out, then come back.
pub struct Transcript {
    text: String,
    to_editor: bool,
}

/// `key` pressed in the transcript overlay of the session `thread_id`: the action, or None for
/// any other key, outside the mode, or when the session's record cannot be found.
pub fn transcript<T: std::fmt::Display>(key: char, thread_id: Option<T>) -> Option<Transcript> {
    if !on() || !matches!(key, '[' | 'v') {
        return None;
    }
    let thread_id = thread_id?.to_string();
    let home = codex_utils_home_dir::find_codex_home().ok()?.as_path().to_path_buf();
    let rollout = find_rollout(&home.join("sessions"), &thread_id)?;
    let text = render(&std::fs::read_to_string(rollout).ok()?);
    Some(Transcript { text, to_editor: key == 'v' })
}

/// The rollout file of a thread: the one under `sessions/` whose name ends with its id.
pub fn find_rollout(sessions: &Path, thread_id: &str) -> Option<PathBuf> {
    let suffix = format!("{thread_id}.jsonl");
    let mut folders = vec![sessions.to_path_buf()];
    while let Some(folder) = folders.pop() {
        for entry in std::fs::read_dir(&folder).ok()?.flatten() {
            let path = entry.path();
            if path.is_dir() {
                folders.push(path);
            } else if path.file_name().and_then(|name| name.to_str()).is_some_and(|name| name.ends_with(&suffix)) {
                return Some(path);
            }
        }
    }
    None
}

/// The conversation in a rollout as plain text: each message, each tool call and its output.
pub fn render(rollout: &str) -> String {
    let mut out = String::new();
    for line in rollout.lines() {
        let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) else { continue };
        if entry["type"] != "response_item" {
            continue;
        }
        let item = &entry["payload"];
        let text = |key: &str| item[key].as_str().unwrap_or_default().to_string();
        match item["type"].as_str().unwrap_or_default() {
            "message" => {
                let role = item["role"].as_str().unwrap_or_default();
                if role == "developer" || role == "system" {
                    continue;
                }
                let body: Vec<&str> = item["content"]
                    .as_array()
                    .map(|parts| parts.iter().filter_map(|part| part["text"].as_str()).collect())
                    .unwrap_or_default();
                let body = body.join("\n");
                if role == "user" && body.trim_start().starts_with('<') {
                    continue; // the environment context and other injected blocks
                }
                out.push_str(&format!("[{role}]\n{}\n\n", body.trim_end()));
            }
            "function_call" | "custom_tool_call" => {
                let input = if item["type"] == "function_call" { text("arguments") } else { text("input") };
                out.push_str(&format!("[tool] {} {}\n", text("name"), input));
            }
            "function_call_output" | "custom_tool_call_output" => {
                let output = item["output"].as_str().map(str::to_string).unwrap_or_else(|| item["output"].to_string());
                for row in output.trim_end().lines() {
                    out.push_str(&format!("    {row}\n"));
                }
                out.push('\n');
            }
            _ => {}
        }
    }
    out
}

impl Transcript {
    /// For `[`: the conversation's lines, which the hook adds to the session's history (in the
    /// classic renderer that is the terminal's scrollback, where Codex keeps it across a redraw).
    /// None for `v`, which runs [`Transcript::execute`] instead.
    pub fn lines(&self) -> Option<Vec<String>> {
        (!self.to_editor).then(|| self.text.lines().map(str::to_string).collect())
    }

    /// For `v`: opens the conversation in `$VISUAL`/`$EDITOR` (else `vi`) while the TUI has handed
    /// the terminal back (Codex's `Tui::with_restored`).
    pub async fn execute(self) {
        let path = std::env::temp_dir().join(format!("ling-transcript-{}.md", std::process::id()));
        if std::fs::write(&path, &self.text).is_ok() {
            let editor = std::env::var("VISUAL").or_else(|_| std::env::var("EDITOR")).unwrap_or_else(|_| "vi".to_string());
            let mut words = editor.split_whitespace();
            if let Some(program) = words.next() {
                let _ = std::process::Command::new(program).args(words).arg(&path).status();
            }
            let _ = std::fs::remove_file(&path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir() -> PathBuf {
        std::env::temp_dir()
    }

    #[test]
    fn outside_the_mode_every_hook_leaves_codex_alone() {
        force_on_this_thread(false);
        assert_eq!(enter("first line\\"), None);
        assert_eq!(enter("/branch"), None);
        assert!(!double_press_exit() && !shift_tab_cycles_approvals());
        assert!(transcript('[', Some("t")).is_none());
        force_on_this_thread(true);
        assert_eq!(enter("/branch"), Some(Enter::Run("/fork".into())));
        assert!(double_press_exit() && shift_tab_cycles_approvals());
        assert!(transcript('x', Some("t")).is_none(), "only [ and v");
    }

    #[test]
    fn a_trailing_backslash_becomes_a_newline() {
        assert_eq!(enter_in_mode("first line\\", &dir()), Some(Enter::Draft("first line\n".into())));
        // An escaped backslash is the text's own; nothing else is touched.
        assert_eq!(enter_in_mode("C:\\\\", &dir()), None);
        assert_eq!(enter_in_mode("fix it", &dir()), None);
    }

    #[test]
    fn claude_codes_names_run_codexs_commands() {
        assert_eq!(enter_in_mode("/branch", &dir()), Some(Enter::Run("/fork".into())));
        assert_eq!(enter_in_mode("/tasks", &dir()), Some(Enter::Run("/ps".into())));
        assert_eq!(enter_in_mode("/branch try another way", &dir()), Some(Enter::Run("/fork try another way".into())));
        assert_eq!(enter_in_mode("/context", &dir()), Some(Enter::Run("/status".into())));
        // Codex's own commands, and words that only start with a slash, are left to Codex.
        assert_eq!(enter_in_mode("/fork", &dir()), None);
        assert_eq!(enter_in_mode("/status", &dir()), None);
        assert_eq!(enter_in_mode("/usr/bin is missing", &dir()), None);
    }

    #[test]
    fn missing_and_unplanned_commands_are_answered_here() {
        let Some(Enter::Say(help)) = enter_in_mode("/help", &dir()) else { panic!("help") };
        assert!(help.contains("/branch (/fork)"));
        let Some(Enter::Say(text)) = enter_in_mode("/rewind", &dir()) else { panic!("rewind") };
        assert!(text.contains("Esc twice"));
        let Some(Enter::Say(text)) = enter_in_mode("/login", &dir()) else { panic!("login") };
        assert_eq!(text, "/login is not part of the compatibility mode.");
        let Some(Enter::Say(text)) = enter_in_mode("/add-dir ../lib", &dir()) else { panic!("add-dir") };
        assert!(text.ends_with("--add-dir ../lib"));
        assert!(matches!(enter_in_mode("/memory", &dir()), Some(Enter::Say(_))));
    }

    #[test]
    fn memory_lists_the_files_from_the_project_root_down() {
        let root = std::env::temp_dir().join(format!("mightling-compat-keys-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join(".git")).unwrap();
        std::fs::create_dir_all(root.join("sub")).unwrap();
        std::fs::write(root.join("CLAUDE.md"), "@AGENTS.md\n").unwrap();
        std::fs::write(root.join("AGENTS.md"), "rules\n").unwrap();
        std::fs::write(root.join("sub").join("CLAUDE.local.md"), "mine\n").unwrap();
        let lines = memory_lines(&root.join("sub"));
        let shown: Vec<&String> = lines.iter().filter(|line| line.contains(root.to_str().unwrap())).collect();
        assert_eq!(shown.len(), 3, "{lines:?}");
        assert!(shown[0].ends_with("AGENTS.md") && shown[1].ends_with("CLAUDE.md") && shown[2].ends_with("CLAUDE.local.md"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn the_transcript_is_the_conversation_without_injected_blocks() {
        let rollout = [
            r#"{"type":"session_meta","payload":{"id":"t"}}"#,
            r#"{"type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"rules"}]}}"#,
            r#"{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<environment_context>x</environment_context>"}]}}"#,
            r#"{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"list files"}]}}"#,
            r#"{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"ls\"}","call_id":"c"}}"#,
            r#"{"type":"response_item","payload":{"type":"function_call_output","call_id":"c","output":"a.txt\nb.txt"}}"#,
            r#"{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Two files."}]}}"#,
        ]
        .join("\n");
        let text = render(&rollout);
        assert!(!text.contains("rules") && !text.contains("environment_context"), "{text}");
        assert!(text.contains("[user]\nlist files\n"));
        assert!(text.contains("[tool] exec_command {\"cmd\":\"ls\"}\n"));
        assert!(text.contains("    a.txt\n    b.txt\n"));
        assert!(text.contains("[assistant]\nTwo files.\n"));
    }

    #[test]
    fn the_rollout_is_found_by_its_thread_id() {
        let root = std::env::temp_dir().join(format!("mightling-compat-rollout-{}", std::process::id()));
        let day = root.join("2026").join("10").join("20");
        std::fs::create_dir_all(&day).unwrap();
        std::fs::write(day.join("rollout-2026-10-20T10-00-00-abc.jsonl"), "").unwrap();
        std::fs::write(day.join("rollout-2026-10-20T11-00-00-xyz.jsonl"), "").unwrap();
        assert_eq!(find_rollout(&root, "xyz"), Some(day.join("rollout-2026-10-20T11-00-00-xyz.jsonl")));
        assert_eq!(find_rollout(&root, "nope"), None);
        let _ = std::fs::remove_dir_all(&root);
    }
}
