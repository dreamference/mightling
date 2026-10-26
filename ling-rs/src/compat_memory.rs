//! The compatibility mode's memory: Claude Code's `CLAUDE.md` files, read live beside `AGENTS.md`
//! (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §9.1).
//!
//! Codex keeps reading `AGENTS.md` itself, from the repository root to the working folder, and
//! that stays Mightling's canonical project document. The launcher composes the `CLAUDE.md` files
//! as Claude Code's memory page documents them and hands the text to the session through a
//! `SessionStart` hook, `ling compat memory`, registered for that session only with `-c` and its
//! trust entry (Phase 0, §16.3). No patch is needed. The files are, in this order:
//!
//! 1. the managed file, `/etc/claude-code/CLAUDE.md`;
//! 2. the user's `~/.claude/CLAUDE.md`;
//! 3. in the working folder and every folder above it, from the top down: `CLAUDE.md`, then
//!    `.claude/CLAUDE.md`, then `CLAUDE.local.md`.
//!
//! Each file's `@path` imports are expanded: relative to the importing file, `~/` for the home
//! folder, at most four hops deep. An import inside a code span or a fenced block is left as
//! text, as are a quoted path and a path that does not exist. `\ ` stands for a space. Block-level
//! HTML comments are dropped.
//!
//! Mightling is stricter than Claude Code in two ways:
//!
//! - An import in a project's file that leads outside the project is not followed. Claude Code
//!   asks first; there is no dialog here, so the import is reported. A user-scope file's imports
//!   are the user's own and are followed.
//! - `AGENTS.md` is always read, by Codex. A file Codex already reads, or one read once here, is
//!   not read again. A `CLAUDE.md` that is only `@AGENTS.md` adds nothing.
//!
//! The memory and the `AGENTS.md` files share one budget, Codex's `project_doc_max_bytes` (32 KiB):
//! when they do not fit, the largest `CLAUDE.md` files are cut first, each with a notice.

use std::collections::HashSet;
use std::path::Path;
use std::path::PathBuf;

use serde_json::json;

use crate::compaction::SessionHook;

/// The budget the memory and the `AGENTS.md` files share, in bytes: Codex's default
/// `project_doc_max_bytes`.
pub const BUDGET: usize = 32 * 1024;

/// How many hops of `@path` imports are followed.
pub const MAX_IMPORT_DEPTH: usize = 4;

/// The managed memory file on Linux.
pub const MANAGED_FILE: &str = "/etc/claude-code/CLAUDE.md";

/// Where a memory file came from, which decides what its imports may reach.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scope {
    Managed,
    User,
    Project,
}

/// One memory file as it goes into the session, its imports expanded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MemoryFile {
    pub path: PathBuf,
    pub text: String,
}

/// What was read, and what was not and why.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Memory {
    pub files: Vec<MemoryFile>,
    pub notes: Vec<String>,
}

/// Where to look: the folders and files a session's memory is built from, given as values so
/// tests need no real home.
#[derive(Clone, Debug)]
pub struct Places {
    pub cwd: PathBuf,
    pub home: Option<PathBuf>,
    pub managed: PathBuf,
    /// The project's root: the git top level, or the working folder. Imports from a project's
    /// files stay inside it.
    pub root: PathBuf,
    /// The `AGENTS.md` files Codex reads itself, which are never read again here.
    pub agents_files: Vec<PathBuf>,
}

impl Places {
    /// The places for a session working in `cwd`.
    pub fn for_dir(cwd: &Path, codex_home: Option<&Path>) -> Places {
        let root = crate::compat_tui::project_root(cwd);
        let mut agents_files = Vec::new();
        if let Some(home) = codex_home {
            agents_files.push(home.join("AGENTS.md"));
        }
        for folder in folders_down_to(&root, cwd) {
            agents_files.push(folder.join("AGENTS.md"));
            agents_files.push(folder.join("AGENTS.override.md"));
        }
        Places {
            cwd: cwd.to_path_buf(),
            home: ling_node_locator::home_dir(),
            managed: PathBuf::from(MANAGED_FILE),
            root,
            agents_files,
        }
    }
}

/// The folders from `top` down to `bottom`, both included, when `bottom` is under `top`;
/// otherwise `bottom` alone.
fn folders_down_to(top: &Path, bottom: &Path) -> Vec<PathBuf> {
    let mut chain = Vec::new();
    let mut folder = Some(bottom);
    while let Some(current) = folder {
        chain.push(current.to_path_buf());
        if current == top {
            chain.reverse();
            return chain;
        }
        folder = current.parent();
    }
    vec![bottom.to_path_buf()]
}

fn canonical(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| path.to_path_buf())
}

/// Reads the memory for a session.
pub fn read(places: &Places) -> Memory {
    let mut memory = Memory::default();
    let mut seen: HashSet<PathBuf> = places.agents_files.iter().map(|path| canonical(path)).collect();
    let mut candidates: Vec<(PathBuf, Scope)> = vec![(places.managed.clone(), Scope::Managed)];
    if let Some(home) = &places.home {
        candidates.push((home.join(".claude").join("CLAUDE.md"), Scope::User));
    }
    // Every folder from the filesystem's root down to the working folder, as Claude Code reads them.
    let mut ancestors: Vec<&Path> = places.cwd.ancestors().collect();
    ancestors.reverse();
    for folder in ancestors {
        for name in ["CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md"] {
            candidates.push((folder.join(name), Scope::Project));
        }
    }
    for (path, scope) in candidates {
        if !path.is_file() {
            continue;
        }
        let key = canonical(&path);
        if !seen.insert(key) {
            continue;
        }
        let Ok(raw) = std::fs::read_to_string(&path) else {
            memory.notes.push(format!("{} could not be read", path.display()));
            continue;
        };
        let text = expand(&raw, &path, scope, places, 0, &mut seen, &mut memory.notes);
        if !text.trim().is_empty() {
            memory.files.push(MemoryFile { path, text });
        }
    }
    memory
}

/// A file's text with its imports expanded and its block-level HTML comments dropped.
fn expand(
    raw: &str,
    path: &Path,
    scope: Scope,
    places: &Places,
    depth: usize,
    seen: &mut HashSet<PathBuf>,
    notes: &mut Vec<String>,
) -> String {
    let mut out = String::new();
    let mut fenced = false;
    let mut in_comment = false;
    for line in raw.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("```") || trimmed.starts_with("~~~") {
            fenced = !fenced;
            out.push_str(line);
            out.push('\n');
            continue;
        }
        if fenced {
            out.push_str(line);
            out.push('\n');
            continue;
        }
        if in_comment || trimmed.starts_with("<!--") {
            in_comment = !line.contains("-->");
            continue;
        }
        let targets = imports(line);
        // A line that is one import and nothing else is replaced by what it imports.
        let only_an_import = targets.len() == 1 && line.trim().trim_start_matches("- ").trim() == format!("@{}", targets[0]).as_str();
        if !only_an_import {
            out.push_str(line);
            out.push('\n');
        }
        for target in targets {
            let Some(resolved) = resolve_import(&target, path, places.home.as_deref()) else { continue };
            if scope == Scope::Project && !canonical(&resolved).starts_with(canonical(&places.root)) {
                notes.push(format!(
                    "@{target} in {} leads outside the project and was not read (Claude Code would ask first)",
                    path.display()
                ));
                continue;
            }
            if depth + 1 > MAX_IMPORT_DEPTH {
                notes.push(format!("@{target} in {} is more than {MAX_IMPORT_DEPTH} imports deep and was not read", path.display()));
                continue;
            }
            if !resolved.is_file() || !seen.insert(canonical(&resolved)) {
                continue;
            }
            if let Ok(imported) = std::fs::read_to_string(&resolved) {
                let text = expand(&imported, &resolved, scope, places, depth + 1, seen, notes);
                if !text.trim().is_empty() {
                    out.push_str(&format!("(from {})\n{}\n", resolved.display(), text.trim_end()));
                }
            }
        }
    }
    out
}

/// The `@path` imports on one line outside code spans: a word that starts with `@`, ending at the
/// first space that is not `\ `. A quoted path is not an import.
pub fn imports(line: &str) -> Vec<String> {
    let mut found = Vec::new();
    let mut in_code = false;
    let chars: Vec<char> = line.chars().collect();
    let mut index = 0;
    while index < chars.len() {
        let c = chars[index];
        if c == '`' {
            in_code = !in_code;
            index += 1;
            continue;
        }
        let at_word_start = index == 0 || chars[index - 1].is_whitespace() || chars[index - 1] == '(';
        if !in_code && c == '@' && at_word_start {
            let mut path = String::new();
            let mut end = index + 1;
            while end < chars.len() {
                match chars[end] {
                    '\\' if chars.get(end + 1) == Some(&' ') => {
                        path.push(' ');
                        end += 2;
                    }
                    ch if ch.is_whitespace() => break,
                    ch => {
                        path.push(ch);
                        end += 1;
                    }
                }
            }
            let path = path.trim_end_matches(['.', ',', ';', ':', ')', '!', '?']).to_string();
            let quoted = path.starts_with(['"', '\'']);
            if !path.is_empty() && !quoted && !path.contains('@') {
                found.push(path);
            }
            index = end;
            continue;
        }
        index += 1;
    }
    found
}

/// Where an import points: `~/` from the home folder, an absolute path as it is, anything else
/// from the importing file's folder.
fn resolve_import(target: &str, from: &Path, home: Option<&Path>) -> Option<PathBuf> {
    if let Some(rest) = target.strip_prefix("~/") {
        return home.map(|home| home.join(rest));
    }
    let path = Path::new(target);
    if path.is_absolute() {
        return Some(path.to_path_buf());
    }
    Some(from.parent()?.join(path))
}

/// The memory as one block of context, inside `budget` bytes: the largest files are cut first,
/// each with a notice, until the whole fits.
pub fn render(memory: &Memory, budget: usize) -> String {
    let mut texts: Vec<String> = memory.files.iter().map(|file| file.text.trim_end().to_string()).collect();
    let header_len: usize = memory.files.iter().map(|file| file.path.display().to_string().len() + 32).sum::<usize>() + 160;
    let total = |texts: &[String]| texts.iter().map(String::len).sum::<usize>() + header_len;
    while total(&texts) > budget {
        let Some((largest, _)) = texts.iter().enumerate().max_by_key(|(_, text)| text.len()) else { break };
        let over = total(&texts) - budget;
        let keep = texts[largest].len().saturating_sub(over + 120);
        let mut cut = keep.min(texts[largest].len());
        while !texts[largest].is_char_boundary(cut) {
            cut -= 1;
        }
        if cut == 0 && texts[largest].is_empty() {
            break;
        }
        let shortened = format!("{}\n[... cut here: the memory files are over the {budget}-byte budget ...]", &texts[largest][..cut]);
        if shortened.len() >= texts[largest].len() {
            texts[largest] = String::new();
        } else {
            texts[largest] = shortened;
        }
    }
    let mut out = String::from(
        "The project's Claude Code memory files (CLAUDE.md), read by Mightling's compatibility mode. \
         Follow them as you follow AGENTS.md; where the two disagree, AGENTS.md wins.\n",
    );
    for (file, text) in memory.files.iter().zip(texts) {
        if text.is_empty() {
            continue;
        }
        out.push_str(&format!("\n## {}\n\n{}\n", file.path.display(), text));
    }
    out
}

/// The bytes of the `AGENTS.md` files Codex reads for this session, which come out of the budget.
pub fn agents_bytes(places: &Places) -> usize {
    places.agents_files.iter().filter_map(|path| std::fs::metadata(path).ok()).map(|meta| meta.len() as usize).sum()
}

/// The hook's answer: the memory as `additionalContext`, or nothing when there is none.
pub fn hook_answer(memory: &Memory, budget: usize) -> Option<serde_json::Value> {
    if memory.files.is_empty() {
        return None;
    }
    Some(json!({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": render(memory, budget),
        }
    }))
}

/// `ling compat memory`: the `SessionStart` hook. It reads the hook's input on stdin for the
/// session's folder and prints the answer. A session started with `--bare`
/// (compat_print::BARE_ENV) gets none.
pub fn run_hook() -> i32 {
    use std::io::Read;
    if std::env::var_os(crate::compat_print::BARE_ENV).is_some() {
        return 0;
    }
    let mut input = String::new();
    let _ = std::io::stdin().read_to_string(&mut input);
    let cwd = serde_json::from_str::<serde_json::Value>(&input)
        .ok()
        .and_then(|value| value["cwd"].as_str().map(PathBuf::from))
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("."));
    let codex_home = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf());
    let places = Places::for_dir(&cwd, codex_home.as_deref());
    let memory = read(&places);
    let budget = BUDGET.saturating_sub(agents_bytes(&places)).max(4 * 1024);
    if let Some(answer) = hook_answer(&memory, budget) {
        println!("{answer}");
    }
    0
}

/// The hook: `<this binary> compat memory` at a new session, `/clear`, and after a compaction
/// (when Claude Code puts the memory back too); a resumed or forked session has it in its history
/// already. Codex never moves its context to a file (`additionalContextLimit = 0`): the memory is
/// within the budget already.
pub const MEMORY_HOOK: SessionHook = SessionHook {
    subcommand: "memory",
    matcher: "startup|clear|compact",
    timeout: 10,
    event: "SessionStart",
    status_message: "",
    context_limit: Some(0),
};

/// The `-c` settings that register the hook for this session, with its trust entry. Hooks given
/// on the command line live in Codex's session-flags layer, whose trust key is
/// `/<session-flags>/config.toml:<event>:<group>:<handler>`, given as an inline table because a
/// dotted `-c` key is split at the dots of its quoted part (spec §16.3).
pub fn hook_settings(command: &str) -> Vec<String> {
    let hook = &MEMORY_HOOK;
    let hash = crate::compaction::hook_hash_for(command, hook);
    vec![
        format!(
            "hooks.SessionStart=[{{matcher={}, hooks=[{{type=\"command\", command={}, timeout={}, additionalContextLimit=0}}]}}]",
            toml_string(hook.matcher),
            toml_string(command),
            hook.timeout
        ),
        format!(
            "hooks.state={{{}={{trusted_hash={}}}}}",
            toml_string("/<session-flags>/config.toml:session_start:0:0"),
            toml_string(&hash)
        ),
    ]
}

fn toml_string(text: &str) -> String {
    toml::Value::String(text.to_string()).to_string()
}

/// The variable and the Dreamference config key that make a folder's `CLAUDE.md` Codex's
/// fallback project document in a plain `ling` session (spec §9.1, last item): read only where
/// a folder has neither `AGENTS.override.md` nor `AGENTS.md`, which is Codex's rule.
pub const CLAUDE_MD_ENV: &str = "DREAMFERENCE_MIGHTLING_CLAUDE_MD";
pub const CLAUDE_MD_KEY: &str = "mightling_claude_md";

/// Whether `mightling_claude_md` is on: the variable, then the key, then off.
pub fn claude_md_fallback_on(env: Option<&str>, config_text: Option<&str>) -> bool {
    if let Some(value) = env.map(str::trim).filter(|value| !value.is_empty()) {
        return matches!(value.to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on");
    }
    config_text
        .and_then(|text| text.parse::<toml::Table>().ok())
        .and_then(|table| table.get(CLAUDE_MD_KEY).and_then(toml::Value::as_bool))
        .unwrap_or(false)
}

/// The `-c project_doc_fallback_filenames=[…]` setting that adds `CLAUDE.md` to the names Codex
/// already falls back to (from `$CODEX_HOME/config.toml`), or None when it is there already.
pub fn fallback_setting(codex_config_text: &str) -> Option<String> {
    let mut names: Vec<String> = codex_config_text
        .parse::<toml::Table>()
        .ok()
        .and_then(|table| table.get("project_doc_fallback_filenames").and_then(toml::Value::as_array).cloned())
        .unwrap_or_default()
        .iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect();
    if names.iter().any(|name| name == "CLAUDE.md") {
        return None;
    }
    names.push("CLAUDE.md".to_string());
    let list: Vec<String> = names.iter().map(|name| toml_string(name)).collect();
    Some(format!("project_doc_fallback_filenames=[{}]", list.join(", ")))
}

/// The command Codex runs for the hook: this binary and `compat memory`, quoted for the shell
/// Codex starts it through.
pub fn hook_command(exe: &Path) -> String {
    hook_command_for(exe, cfg!(windows))
}

/// [`hook_command`] for either shell: a POSIX shell's single quotes, or `cmd.exe`'s double quotes
/// on Windows, which single quotes broke there (`CLAUDE.md` never reached the model; WINDOWS_ARM
/// §23.4 W1).
pub fn hook_command_for(exe: &Path, windows: bool) -> String {
    format!("{} compat memory", crate::compaction::quote_program(&exe.display().to_string(), windows))
}

/// Puts `-c` settings right after the program name.
fn with_settings(args: Vec<std::ffi::OsString>, settings: &[String]) -> Vec<std::ffi::OsString> {
    let mut args = args.into_iter();
    let mut out: Vec<std::ffi::OsString> = args.next().into_iter().collect();
    for setting in settings {
        out.extend(["-c".into(), std::ffi::OsString::from(setting)]);
    }
    out.extend(args);
    out
}

/// The session's memory settings outside the mode: `CLAUDE.md` as a fallback document when
/// `mightling_claude_md` is on. In the mode the hook is registered by compat_hooks.rs, together
/// with the settings files' hooks, since all of them share one `-c hooks={…}` table.
pub fn prepare(args: Vec<std::ffi::OsString>, mode: crate::compat::Mode, codex_home: &Path) -> Vec<std::ffi::OsString> {
    // In the mode the hook goes in with the settings files' hooks, one table (compat_hooks.rs).
    if mode == crate::compat::Mode::Claude {
        return args;
    }
    let config_text = crate::config_file().and_then(|path| std::fs::read_to_string(path).ok());
    if !claude_md_fallback_on(std::env::var(CLAUDE_MD_ENV).ok().as_deref(), config_text.as_deref()) {
        return args;
    }
    let codex_config = std::fs::read_to_string(codex_home.join("config.toml")).unwrap_or_default();
    match fallback_setting(&codex_config) {
        Some(setting) => with_settings(args, &[setting]),
        None => args,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture {
        root: PathBuf,
    }

    impl Fixture {
        fn new(tag: &str) -> Fixture {
            let root = std::env::temp_dir().join(format!("mightling-memory-{tag}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(root.join("home/.claude")).unwrap();
            std::fs::create_dir_all(root.join("etc")).unwrap();
            std::fs::create_dir_all(root.join("work/repo/.git")).unwrap();
            std::fs::create_dir_all(root.join("work/repo/sub/deep")).unwrap();
            Fixture { root }
        }
        fn write(&self, path: &str, text: &str) {
            let path = self.root.join(path);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        fn places(&self, cwd: &str) -> Places {
            let cwd = self.root.join(cwd);
            let repo = self.root.join("work/repo");
            let mut agents_files = vec![self.root.join("codex-home/AGENTS.md")];
            for folder in folders_down_to(&repo, &cwd) {
                agents_files.push(folder.join("AGENTS.md"));
            }
            Places { cwd, home: Some(self.root.join("home")), managed: self.root.join("etc/CLAUDE.md"), root: repo, agents_files }
        }
        fn names(&self, memory: &Memory) -> Vec<String> {
            // `/` on every system, so the expected names read the same on Windows.
            memory.files.iter().map(|f| f.path.strip_prefix(&self.root).unwrap().display().to_string().replace('\\', "/")).collect()
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    #[test]
    fn the_files_load_broadest_first_and_local_after_shared() {
        let f = Fixture::new("order");
        f.write("etc/CLAUDE.md", "managed\n");
        f.write("home/.claude/CLAUDE.md", "mine\n");
        f.write("work/CLAUDE.md", "above the repository\n");
        f.write("work/repo/CLAUDE.md", "project\n");
        f.write("work/repo/.claude/CLAUDE.md", "project, in .claude\n");
        f.write("work/repo/CLAUDE.local.md", "local\n");
        f.write("work/repo/sub/CLAUDE.md", "sub\n");
        f.write("work/repo/sub/deep/CLAUDE.md", "below the working folder: not at start\n");
        let memory = read(&f.places("work/repo/sub"));
        assert_eq!(
            f.names(&memory),
            [
                "etc/CLAUDE.md",
                "home/.claude/CLAUDE.md",
                "work/CLAUDE.md",
                "work/repo/CLAUDE.md",
                "work/repo/.claude/CLAUDE.md",
                "work/repo/CLAUDE.local.md",
                "work/repo/sub/CLAUDE.md",
            ]
        );
    }

    #[test]
    fn imports_expand_relative_to_their_file_and_stop_at_four_hops() {
        let f = Fixture::new("imports");
        f.write("work/repo/CLAUDE.md", "Read @docs/a.md and `@not-this.md`.\n```\n@nor-this.md\n```\n");
        f.write("work/repo/docs/a.md", "a then @b.md\n");
        f.write("work/repo/docs/b.md", "b then @c.md\n");
        f.write("work/repo/docs/c.md", "c then @d.md\n");
        f.write("work/repo/docs/d.md", "d then @e.md\n");
        f.write("work/repo/docs/e.md", "e: five hops down\n");
        f.write("work/repo/not-this.md", "NOT\n");
        f.write("work/repo/nor-this.md", "NOR\n");
        let memory = read(&f.places("work/repo"));
        let text = &memory.files[0].text;
        for part in ["a then", "b then", "c then", "d then"] {
            assert!(text.contains(part), "{part}: {text}");
        }
        assert!(!text.contains("five hops") && !text.contains("NOT\n") && !text.contains("NOR\n"), "{text}");
        assert!(memory.notes.iter().any(|note| note.contains("more than 4 imports deep")), "{:?}", memory.notes);
    }

    #[test]
    fn a_cycle_is_read_once_and_a_space_is_escaped() {
        let f = Fixture::new("cycle");
        f.write("work/repo/CLAUDE.md", "top @Design\\ Docs/x.md\n");
        f.write("work/repo/Design Docs/x.md", "x @../CLAUDE.md @x.md\n");
        let memory = read(&f.places("work/repo"));
        assert_eq!(memory.files.len(), 1);
        assert_eq!(memory.files[0].text.matches("top").count(), 1);
        assert!(memory.files[0].text.contains("x @../CLAUDE.md"));
    }

    #[test]
    fn a_project_import_stays_inside_the_project_and_a_users_may_leave_it() {
        let f = Fixture::new("outside");
        f.write("home/notes.md", "private notes\n");
        f.write("work/repo/CLAUDE.md", "see @~/notes.md and @../../home/notes.md\n");
        f.write("home/.claude/CLAUDE.md", "@~/notes.md\n");
        let memory = read(&f.places("work/repo"));
        assert_eq!(f.names(&memory), ["home/.claude/CLAUDE.md", "work/repo/CLAUDE.md"]);
        assert!(memory.files[0].text.contains("private notes"));
        assert!(!memory.files[1].text.contains("private notes"));
        assert_eq!(memory.notes.iter().filter(|note| note.contains("leads outside the project")).count(), 2, "{:?}", memory.notes);
    }

    #[test]
    fn agents_md_is_never_read_twice_and_a_claude_md_that_only_imports_it_adds_nothing() {
        let f = Fixture::new("agents");
        f.write("work/repo/AGENTS.md", "the rules\n");
        f.write("work/repo/CLAUDE.md", "@AGENTS.md\n");
        assert!(read(&f.places("work/repo")).files.is_empty());
        f.write("work/repo/CLAUDE.md", "@AGENTS.md\n\nAlso: be brief.\n");
        let memory = read(&f.places("work/repo"));
        assert_eq!(memory.files.len(), 1);
        assert!(!memory.files[0].text.contains("the rules") && memory.files[0].text.contains("be brief"));
        // One file reached twice, as itself and through an import, is read once.
        f.write("work/repo/CLAUDE.local.md", "@CLAUDE.md\nlocal\n");
        let memory = read(&f.places("work/repo"));
        assert_eq!(memory.files.len(), 2);
        assert_eq!(memory.files.iter().map(|m| m.text.matches("be brief").count()).sum::<usize>(), 1);
    }

    #[test]
    fn comments_are_dropped_but_not_inside_code() {
        let f = Fixture::new("comments");
        f.write("work/repo/CLAUDE.md", "keep\n<!-- maintainer\nnote -->\nalso keep\n```\n<!-- in code -->\n```\n");
        let text = read(&f.places("work/repo")).files[0].text.clone();
        assert!(text.contains("keep") && text.contains("also keep") && text.contains("<!-- in code -->"));
        assert!(!text.contains("maintainer"));
    }

    #[test]
    fn the_budget_cuts_the_largest_file_first() {
        let memory = Memory {
            files: vec![
                MemoryFile { path: PathBuf::from("/a/CLAUDE.md"), text: "small\n".into() },
                MemoryFile { path: PathBuf::from("/b/CLAUDE.md"), text: "x".repeat(10_000) },
            ],
            notes: vec![],
        };
        let rendered = render(&memory, 4_000);
        assert!(rendered.len() <= 4_000, "{}", rendered.len());
        assert!(rendered.contains("small") && rendered.contains("over the 4000-byte budget"));
        assert!(render(&memory, BUDGET).len() > 10_000);
    }

    #[test]
    fn the_hook_answers_with_context_only_when_there_is_memory() {
        assert!(hook_answer(&Memory::default(), BUDGET).is_none());
        let memory = Memory { files: vec![MemoryFile { path: PathBuf::from("/r/CLAUDE.md"), text: "word: ZEBRA\n".into() }], notes: vec![] };
        let answer = hook_answer(&memory, BUDGET).unwrap();
        assert_eq!(answer["hookSpecificOutput"]["hookEventName"], "SessionStart");
        assert!(answer["hookSpecificOutput"]["additionalContext"].as_str().unwrap().contains("ZEBRA"));
    }

    #[test]
    fn imports_are_found_as_the_memory_page_describes() {
        assert_eq!(imports("See @README for an overview and @package.json."), ["README", "package.json"]);
        assert_eq!(imports("- git workflow @docs/git-instructions.md"), ["docs/git-instructions.md"]);
        assert_eq!(imports("API @Design\\ Docs/api.md here"), ["Design Docs/api.md"]);
        assert!(imports("mail me at me@example.com").is_empty());
        assert!(imports("literal `@README` only").is_empty());
        assert!(imports("quoted @\"a b.md\"").is_empty());
    }

    #[test]
    fn plain_ling_can_fall_back_to_claude_md() {
        assert!(!claude_md_fallback_on(None, None));
        assert!(claude_md_fallback_on(None, Some("mightling_claude_md = true\n")));
        assert!(!claude_md_fallback_on(Some("off"), Some("mightling_claude_md = true\n")));
        assert_eq!(fallback_setting("").as_deref(), Some("project_doc_fallback_filenames=[\"CLAUDE.md\"]"));
        assert_eq!(
            fallback_setting("project_doc_fallback_filenames = [\"GUIDE.md\"]\n").as_deref(),
            Some("project_doc_fallback_filenames=[\"GUIDE.md\", \"CLAUDE.md\"]")
        );
        assert_eq!(fallback_setting("project_doc_fallback_filenames = [\"CLAUDE.md\"]\n"), None);
    }

    #[test]
    fn the_hook_command_is_quoted_for_the_shell() {
        assert_eq!(hook_command_for(Path::new("/opt/ling/bin/ling"), false), "/opt/ling/bin/ling compat memory");
        assert_eq!(hook_command_for(Path::new("/home/a b/ling"), false), "'/home/a b/ling' compat memory");
        assert_eq!(
            hook_command_for(Path::new(r"C:\Users\Jane Doe\AppData\Local\Programs\Mightling\bin\ling.exe"), true),
            r#""C:\Users\Jane Doe\AppData\Local\Programs\Mightling\bin\ling.exe" compat memory"#
        );
    }

    #[test]
    fn the_hook_is_registered_and_trusted_as_codex_hashes_it() {
        let settings = hook_settings("/opt/ling compat memory");
        let table: toml::Table = toml::from_str(&settings.join("\n")).unwrap();
        let group = &table["hooks"]["SessionStart"][0];
        assert_eq!(group["matcher"].as_str(), Some("startup|clear|compact"));
        assert_eq!(group["hooks"][0]["command"].as_str(), Some("/opt/ling compat memory"));
        assert_eq!(group["hooks"][0]["additionalContextLimit"].as_integer(), Some(0));
        let state = table["hooks"]["state"].as_table().unwrap();
        let (key, entry) = state.iter().next().unwrap();
        assert_eq!(key, "/<session-flags>/config.toml:session_start:0:0");
        // The hash Codex computes from its own types for that group.
        use codex_config::HookHandlerConfig;
        use codex_config::MatcherGroup;
        #[derive(serde::Serialize)]
        struct Identity {
            event_name: &'static str,
            #[serde(flatten)]
            group: MatcherGroup,
        }
        let group = MatcherGroup {
            matcher: Some(MEMORY_HOOK.matcher.to_string()),
            hooks: vec![HookHandlerConfig::Command {
                command: "/opt/ling compat memory".to_string(),
                command_windows: None,
                timeout_sec: Some(10),
                r#async: false,
                status_message: None,
                additional_context_limit: Some(0),
            }],
        };
        let value = toml::Value::try_from(Identity { event_name: "session_start", group }).unwrap();
        assert_eq!(entry["trusted_hash"].as_str(), Some(codex_config::version_for_toml(&value).as_str()));
    }
}
