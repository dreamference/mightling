//! The compatibility mode's permissions: Claude Code's `permissions.allow`, `ask` and `deny`
//! rules, and its permission modes (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §9.3).
//!
//! **Rules** come from the settings files (managed, `~/.claude/settings.json`, and in a trusted
//! folder `.claude/settings.json` and `.claude/settings.local.json`) and from `--allowedTools` /
//! `--disallowedTools`. They are enforced by one `PreToolUse` hook, `ling compat guard`, that
//! the launcher registers beside the others (compat_hooks.rs). Claude Code's order holds: a deny
//! rule wins, then an ask rule, then an allow rule.
//!
//! - A **deny** rule refuses the call, with the rule named.
//! - An **ask** rule refuses it too, saying that Claude Code would ask: a Codex hook cannot make
//!   Codex ask (an `ask` decision is not one it takes), so the mode refuses, the safe direction,
//!   and the user runs the command with `!` or changes the rule.
//! - An **allow** rule needs no hook. Codex runs what its sandbox allows without asking, and asks
//!   for what would leave the sandbox, which an allow rule cannot waive here.
//!
//! What is matched, after Claude Code's permissions page:
//!
//! - `Bash(…)`: every subcommand of a compound command (`&&`, `||`, `;`, `|`, `&`, newlines), after
//!   the wrappers Claude Code strips (`timeout`, `time`, `nice`, `nohup`, `stdbuf`, `command`,
//!   `builtin`, `noglob`, bare `xargs`) and any leading variable assignment; `*` stands for any
//!   text, a trailing ` *` also matches the bare command, and `:*` is the older spelling of ` *`.
//! - `Read(…)` and `Edit(…)`: gitignore-style paths (`//abs`, `~/home`, `/` from the settings
//!   file's anchor, relative from the working folder). `Edit` covers `apply_patch`'s files;
//!   both cover the file commands Claude Code recognises in a shell command (`cat`, `head`, `tail`,
//!   `less`, `more`, `sed`, `tee`) and redirections. A `Read` deny also refuses an edit there.
//! - `WebFetch(domain:…)` and `WebSearch`: the `ling-fetch` and `ling-search` commands.
//! - `mcp__<server>__<tool>`, tool-name globs (`mcp__*`, `*`), `Agent(<role>)`, a bare tool name.
//! - Writes to `.claude/` and `.git/` are never approved by the mode: the guard refuses them,
//!   as Claude Code would ask.

use std::path::Path;
use std::path::PathBuf;

use serde_json::Value;
use serde_json::json;

/// What a rule says.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Allow,
    Ask,
    Deny,
}

/// One rule, with the folder a `/path` pattern in it is anchored at.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rule {
    pub kind: Kind,
    pub text: String,
    pub anchor: PathBuf,
}

/// A rule's tool and its specifier: `Bash(npm run *)` is (`Bash`, `npm run *`).
pub fn split_rule(text: &str) -> (String, Option<String>) {
    let text = text.trim();
    match text.find('(') {
        Some(open) if text.ends_with(')') => (text[..open].trim().to_string(), Some(text[open + 1..text.len() - 1].to_string())),
        _ => (text.to_string(), None),
    }
}

/// A `*` wildcard match over the whole text.
fn wildcard(pattern: &str, text: &str) -> bool {
    let p: Vec<char> = pattern.chars().collect();
    let t: Vec<char> = text.chars().collect();
    let (mut pi, mut ti, mut star, mut mark) = (0, 0, None, 0);
    while ti < t.len() {
        if pi < p.len() && p[pi] == '*' {
            star = Some(pi);
            mark = ti;
            pi += 1;
        } else if pi < p.len() && p[pi] == t[ti] {
            pi += 1;
            ti += 1;
        } else if let Some(s) = star {
            pi = s + 1;
            mark += 1;
            ti = mark;
        } else {
            return false;
        }
    }
    while pi < p.len() && p[pi] == '*' {
        pi += 1;
    }
    pi == p.len()
}

/// Whether a `Bash` specifier matches one simple command.
pub fn bash_matches(specifier: &str, command: &str) -> bool {
    let specifier = specifier.trim();
    if specifier.is_empty() || specifier == "*" {
        return true;
    }
    let specifier = match specifier.strip_suffix(":*") {
        Some(prefix) => format!("{prefix} *"),
        None => specifier.to_string(),
    };
    if wildcard(&specifier, command) {
        return true;
    }
    // A trailing ` *`, when it is the only wildcard, also matches the bare command.
    match specifier.strip_suffix(" *") {
        Some(bare) if !bare.contains('*') => command == bare,
        _ => false,
    }
}

/// The simple commands of a shell command line, split at Claude Code's separators, with the
/// wrappers and leading assignments taken off.
pub fn subcommands(line: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;
    let chars: Vec<char> = line.chars().collect();
    let mut index = 0;
    while index < chars.len() {
        let c = chars[index];
        match (quote, c) {
            (Some(q), c) if c == q => {
                quote = None;
                current.push(c);
            }
            // Inside double quotes a substitution still runs: `$( … )` and backticks split too.
            (Some('"'), '`') => parts.push(std::mem::take(&mut current)),
            (Some('"'), '$') if chars.get(index + 1) == Some(&'(') => {
                parts.push(std::mem::take(&mut current));
                index += 1;
            }
            (Some('"'), ')') => parts.push(std::mem::take(&mut current)),
            (Some(_), c) => current.push(c),
            (None, '\'' | '"') => {
                quote = Some(c);
                current.push(c);
            }
            (None, ';' | '\n' | '|' | '&') => {
                parts.push(std::mem::take(&mut current));
                // `&&`, `||`, `|&` are one separator.
                if index + 1 < chars.len() && matches!((c, chars[index + 1]), ('&', '&') | ('|', '|') | ('|', '&')) {
                    index += 1;
                }
            }
            (None, '(' | ')' | '`') => parts.push(std::mem::take(&mut current)),
            (None, '$') if chars.get(index + 1) == Some(&'(') => {
                parts.push(std::mem::take(&mut current));
                index += 1;
            }
            _ => current.push(c),
        }
        index += 1;
    }
    parts.push(current);
    parts.into_iter().map(|part| strip_wrappers(part.trim())).filter(|part| !part.is_empty()).collect()
}

fn strip_wrappers(command: &str) -> String {
    let mut words: Vec<&str> = command.split_whitespace().collect();
    loop {
        let Some(first) = words.first().copied() else { break };
        if first.contains('=') && !first.starts_with('=') && first.split('=').next().is_some_and(|name| name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')) {
            words.remove(0);
            continue;
        }
        let taken = match first {
            "time" | "nohup" | "command" | "builtin" | "noglob" => 1,
            "xargs" if words.get(1).is_some_and(|next| !next.starts_with('-')) => 1,
            "timeout" => 1 + words.iter().skip(1).take_while(|w| w.starts_with('-')).count() + 1,
            "nice" | "stdbuf" => 1 + words.iter().skip(1).take_while(|w| w.starts_with('-')).count() + usize::from(first == "nice" && words.get(1) == Some(&"-n")),
            _ => 0,
        };
        if taken == 0 || taken > words.len() || (first == "command" && words.get(1) == Some(&"-v")) {
            break;
        }
        words.drain(..taken);
    }
    words.join(" ")
}

/// Lexically normalised absolute path (no `.` or `..`), without touching the disk.
fn absolute(path: &str, cwd: &Path) -> PathBuf {
    let path = Path::new(path);
    let joined = if path.is_absolute() { path.to_path_buf() } else { cwd.join(path) };
    let mut out = PathBuf::new();
    for part in joined.components() {
        match part {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// A gitignore-style glob over `/`-separated segments: `**` any segments, `*` within one.
fn glob_segments(pattern: &[&str], path: &[&str]) -> bool {
    match pattern.first() {
        None => path.is_empty(),
        Some(&"**") => (0..=path.len()).any(|skip| glob_segments(&pattern[1..], &path[skip..])),
        Some(first) => !path.is_empty() && wildcard(first, path[0]) && glob_segments(&pattern[1..], &path[1..]),
    }
}

/// Whether a `Read` or `Edit` specifier matches a file.
pub fn path_matches(specifier: &str, file: &Path, cwd: &Path, rule: &Rule, home: Option<&Path>) -> bool {
    let specifier = specifier.trim();
    let (base, pattern): (PathBuf, String) = if let Some(rest) = specifier.strip_prefix("//") {
        (PathBuf::from("/"), rest.to_string())
    } else if let Some(rest) = specifier.strip_prefix("~/") {
        match home {
            Some(home) => (home.to_path_buf(), rest.to_string()),
            None => return false,
        }
    } else if let Some(rest) = specifier.strip_prefix('/') {
        (rule.anchor.clone(), rest.to_string())
    } else {
        let rest = specifier.strip_prefix("./").unwrap_or(specifier);
        // A bare name, or one directory segment in a deny or ask rule, matches at any depth.
        let segments: Vec<&str> = rest.split('/').filter(|s| !s.is_empty()).collect();
        let any_depth = !rest.contains('/') || (rule.kind != Kind::Allow && segments.len() == 2 && segments[1] == "**");
        (cwd.to_path_buf(), if any_depth { format!("**/{rest}") } else { rest.to_string() })
    };
    let Ok(relative) = file.strip_prefix(&base) else { return false };
    let path: Vec<String> = relative.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
    let path: Vec<&str> = path.iter().map(String::as_str).collect();
    let pattern: Vec<&str> = pattern.split('/').filter(|s| !s.is_empty()).collect();
    glob_segments(&pattern, &path) || (pattern.last() == Some(&"**") && glob_segments(&pattern[..pattern.len() - 1], &path))
}

/// What a call reads and writes, as the rules see it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Call {
    /// The tool's name as Claude Code names it.
    pub tool: String,
    pub commands: Vec<String>,
    pub reads: Vec<PathBuf>,
    pub writes: Vec<PathBuf>,
    pub fetch_domains: Vec<String>,
    pub searches: bool,
    pub agent: Option<String>,
}

/// A Codex `PreToolUse` input as the rules see it.
pub fn call_from_input(input: &Value) -> Call {
    let cwd = PathBuf::from(input["cwd"].as_str().unwrap_or("/"));
    let tool = input["tool_name"].as_str().unwrap_or_default();
    let mut call = Call::default();
    match tool {
        "Bash" => {
            call.tool = "Bash".into();
            let line = input["tool_input"]["command"].as_str().unwrap_or_default();
            call.commands = subcommands(line);
            for command in &call.commands {
                let words: Vec<&str> = command.split_whitespace().collect();
                let args = || words.iter().skip(1).filter(|w| !w.starts_with('-')).map(|w| absolute(w.trim_matches(['"', '\'']), &cwd));
                match words.first().copied() {
                    Some("cat" | "head" | "tail" | "less" | "more" | "sed") => call.reads.extend(args()),
                    Some("tee") => call.writes.extend(args()),
                    Some("ling-fetch") => {
                        call.fetch_domains.extend(words.iter().skip(1).filter_map(|w| host_of(w.trim_matches(['"', '\'']))));
                    }
                    Some("ling-search") => call.searches = true,
                    _ => {}
                }
                for (index, word) in words.iter().enumerate() {
                    let target = |rest: &str| if rest.is_empty() { words.get(index + 1).map(|w| w.to_string()) } else { Some(rest.to_string()) };
                    if let Some(rest) = word.strip_prefix(">>").or_else(|| word.strip_prefix('>')) {
                        call.writes.extend(target(rest).map(|t| absolute(&t, &cwd)));
                    } else if let Some(rest) = word.strip_prefix('<') {
                        call.reads.extend(target(rest).map(|t| absolute(&t, &cwd)));
                    }
                }
            }
        }
        "apply_patch" => {
            call.tool = "Edit".into();
            let patch = input["tool_input"]["command"].as_str().unwrap_or_default();
            for line in patch.lines() {
                for prefix in ["*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "] {
                    if let Some(path) = line.strip_prefix(prefix) {
                        call.writes.push(absolute(path.trim(), &cwd));
                    }
                }
            }
        }
        "spawn_agent" => {
            call.tool = "Agent".into();
            call.agent = input["tool_input"]["agent_type"].as_str().map(str::to_string);
        }
        other => call.tool = other.to_string(),
    }
    call
}

fn host_of(url: &str) -> Option<String> {
    let rest = url.split_once("://").map(|(_, rest)| rest)?;
    let host = rest.split(['/', '?', '#']).next()?.rsplit('@').next()?.split(':').next()?;
    (!host.is_empty()).then(|| host.trim_end_matches('.').to_ascii_lowercase())
}

/// Whether a `domain:` specifier matches a host: `*.x` any subdomain, `*` everything, and
/// elsewhere `*` stays within one label.
fn domain_matches(specifier: &str, host: &str) -> bool {
    let pattern = specifier.trim().trim_end_matches('.').to_ascii_lowercase();
    if pattern == "*" {
        return true;
    }
    if let Some(suffix) = pattern.strip_prefix("*.") {
        return host.ends_with(&format!(".{suffix}"));
    }
    let labels: Vec<&str> = pattern.split('.').collect();
    let host_labels: Vec<&str> = host.split('.').collect();
    labels.len() == host_labels.len() && labels.iter().zip(&host_labels).all(|(p, h)| wildcard(p, h))
}

/// Whether one rule covers a call.
pub fn rule_matches(rule: &Rule, call: &Call, cwd: &Path, home: Option<&Path>) -> bool {
    let (tool, specifier) = split_rule(&rule.text);
    let Some(specifier) = specifier else {
        // A bare name, or a tool-name glob (deny and ask only, as in Claude Code).
        let by_command = match tool.as_str() {
            "WebSearch" => call.searches,
            "WebFetch" => !call.fetch_domains.is_empty(),
            "Read" => !call.reads.is_empty() && rule.kind != Kind::Allow,
            _ => false,
        };
        return by_command || tool == call.tool || (tool.contains('*') && rule.kind != Kind::Allow && wildcard(&tool, &call.tool));
    };
    match tool.as_str() {
        "Bash" => call.tool == "Bash" && call.commands.iter().any(|command| bash_matches(&specifier, command)),
        "Read" => call.reads.iter().chain(call.writes.iter().filter(|_| rule.kind != Kind::Allow)).any(|file| path_matches(&specifier, file, cwd, rule, home)),
        "Edit" => call.writes.iter().any(|file| path_matches(&specifier, file, cwd, rule, home)),
        "WebFetch" => match specifier.strip_prefix("domain:") {
            Some(domain) => call.fetch_domains.iter().any(|host| domain_matches(domain, host)),
            None => false,
        },
        "Agent" => call.agent.as_deref() == Some(specifier.trim()),
        _ => false,
    }
}

/// The rules' answer for a call: deny, then ask, then allow; None when no rule speaks.
pub fn decide<'a>(rules: &'a [Rule], call: &Call, cwd: &Path, home: Option<&Path>) -> Option<(Kind, &'a Rule)> {
    for kind in [Kind::Deny, Kind::Ask, Kind::Allow] {
        if let Some(rule) = rules.iter().filter(|rule| rule.kind == kind).find(|rule| rule_matches(rule, call, cwd, home)) {
            return Some((kind, rule));
        }
    }
    // `WebSearch` is a tool-level rule only, matched on `ling-search` commands.
    None
}

/// A write to a path the mode never lets through without the user: `.claude/` and `.git/`.
pub fn protected_write(call: &Call, root: &Path) -> Option<PathBuf> {
    call.writes.iter().find(|file| file.starts_with(root.join(".claude")) || file.starts_with(root.join(".git"))).cloned()
}

/// The rules of the settings files and the command line.
pub fn rules_for(root: &Path, home: Option<&Path>, project_trusted: bool, extra: &[(Kind, String)]) -> (Vec<Rule>, Vec<String>) {
    let mut rules = Vec::new();
    let mut notes = Vec::new();
    let files = crate::compat_hooks::settings_files(root, home, Path::new(crate::compat_hooks::MANAGED_SETTINGS));
    for (file, origin) in files {
        if origin == crate::compat_hooks::Origin::Project && !project_trusted {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&file) else { continue };
        let Ok(settings) = serde_json::from_str::<Value>(&text) else { continue };
        let anchor = match origin {
            crate::compat_hooks::Origin::User => home.map(|home| home.join(".claude")).unwrap_or_default(),
            _ => root.to_path_buf(),
        };
        for (key, kind) in [("allow", Kind::Allow), ("ask", Kind::Ask), ("deny", Kind::Deny)] {
            for text in settings.pointer(&format!("/permissions/{key}")).and_then(Value::as_array).into_iter().flatten() {
                if let Some(text) = text.as_str() {
                    rules.push(Rule { kind, text: text.to_string(), anchor: anchor.clone() });
                }
            }
        }
        if settings.pointer("/permissions/allow").is_some() {
            notes.push(format!(
                "allow rules in {} need no hook: the sandbox decides, and a command that would leave it is still asked",
                file.display()
            ));
        }
    }
    for (kind, text) in extra {
        rules.push(Rule { kind: *kind, text: text.clone(), anchor: root.to_path_buf() });
    }
    (rules, notes)
}

/// The guard's answer to a hook input: the JSON to print, or None to let Codex decide.
pub fn guard_answer(input: &Value, rules: &[Rule], root: &Path, home: Option<&Path>) -> Option<Value> {
    let cwd = PathBuf::from(input["cwd"].as_str().unwrap_or("/"));
    let call = call_from_input(input);
    let deny = |reason: String| {
        Some(json!({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))
    };
    match decide(rules, &call, &cwd, home) {
        Some((Kind::Deny, rule)) => return deny(format!("Claude Code's permission rule {} denies this", rule.text)),
        Some((Kind::Ask, rule)) => {
            return deny(format!(
                "Claude Code's permission rule {} asks before this, and the compatibility mode cannot ask: \
                 it is refused; the user can run it with ! or change the rule",
                rule.text
            ));
        }
        _ => {}
    }
    if let Some(file) = protected_write(&call, root) {
        return deny(format!("{} is protected: the compatibility mode never changes .claude/ or .git/ without the user", file.display()));
    }
    None
}

/// Why the guard refuses a call it could not judge: its rules could not be read, the hook input
/// was not Codex's, or the guard did not run at all (the fallback in [`guard_command`]). Plain
/// words only: the fallback prints it through `cmd.exe` and `sh`, where `%`, `&`, `(` or a single
/// quote would be read.
pub const GUARD_FAILED_REASON: &str =
    "Mightling refused this tool call because the permission guard for Claude Code rules could not judge it. Run it yourself, or see ling compat doctor";

/// The guard's answer when it cannot judge a call: deny, never let it through.
pub fn guard_failed_answer() -> Value {
    json!({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": GUARD_FAILED_REASON}})
}

/// `ling compat guard [--allow R]... [--deny R]... [--allow-hex H]... [--deny-hex H]...`: the
/// `PreToolUse` hook. It fails closed: a rule it cannot decode or an input it cannot read is a
/// refusal, not a pass.
pub fn run_guard(args: &[String]) -> i32 {
    use std::io::Read;
    let mut raw = String::new();
    let _ = std::io::stdin().read_to_string(&mut raw);
    let answer = match (guard_rules_from_args(args), serde_json::from_str::<Value>(&raw)) {
        (Some(extra), Ok(input)) if input.is_object() => {
            let cwd = PathBuf::from(input["cwd"].as_str().unwrap_or("."));
            let root = crate::compat_tui::project_root(&cwd);
            let trusted =
                codex_utils_home_dir::find_codex_home().ok().is_some_and(|home| ling_skills::catalog::is_trusted(home.as_path(), &root));
            let home = ling_node_locator::home_dir();
            let (rules, _) = rules_for(&root, home.as_deref(), trusted, &extra);
            guard_answer(&input, &rules, &root, home.as_deref())
        }
        _ => Some(guard_failed_answer()),
    };
    if let Some(answer) = answer {
        println!("{answer}");
    }
    0
}

/// The guard's command-line rules: `--allow R`, `--deny R`, and their hex forms (the rule's text
/// as a [`crate::compat_hooks::hex_word`], how Windows passes it). None when a flag has no value
/// or a hex word does not decode, which the guard reads as "cannot judge".
pub fn guard_rules_from_args(args: &[String]) -> Option<Vec<(Kind, String)>> {
    let mut extra = Vec::new();
    let mut words = args.iter();
    while let Some(flag) = words.next() {
        let (kind, hex) = match flag.as_str() {
            "--allow" => (Kind::Allow, false),
            "--deny" => (Kind::Deny, false),
            "--allow-hex" => (Kind::Allow, true),
            "--deny-hex" => (Kind::Deny, true),
            _ => continue,
        };
        let value = words.next()?;
        let text = if hex { crate::compat_hooks::from_hex_word(value)? } else { value.clone() };
        extra.push((kind, text));
    }
    Some(extra)
}

/// The guard hook's command, with the session's command-line rules in it.
pub fn guard_command(exe: &Path, extra: &[(Kind, String)]) -> String {
    guard_command_for(exe, extra, cfg!(windows))
}

/// [`guard_command`] for either shell Codex runs hooks through: `$SHELL -lc` elsewhere, `cmd.exe
/// /C` on Windows, where the program goes in double quotes and each rule as a hex word (cmd reads
/// `%`, `&`, `|`, `(` and quotes inside rules; WINDOWS_ARM §23.4 W1).
///
/// The line ends in a fallback that prints a refusal when the guard itself fails: a program that
/// cannot start (a moved install, a quoting fault, as W1 was), a crash, or any exit but 0. Codex
/// lets a tool call through when a `PreToolUse` hook fails, so without it a guard that does not
/// run enforces nothing. The guard exits 0 whenever it has judged, so the fallback answers only
/// for a guard that did not.
pub fn guard_command_for(exe: &Path, extra: &[(Kind, String)], windows: bool) -> String {
    let exe = exe.display().to_string();
    let mut command = if windows {
        format!("{} compat guard", crate::compaction::quote_program(&exe, true))
    } else {
        format!("{} compat guard", crate::compat_hooks::shell_word(&exe))
    };
    for (kind, text) in extra {
        let flag = if *kind == Kind::Deny { "--deny" } else { "--allow" };
        if windows {
            command.push_str(&format!(" {flag}-hex {}", crate::compat_hooks::hex_word(text)));
        } else {
            command.push_str(&format!(" {flag} {}", crate::compat_hooks::shell_word(text)));
        }
    }
    let refusal = guard_failed_answer().to_string();
    if windows {
        // `echo` prints the rest of the line as it is; the JSON's quotes are balanced, and it holds
        // none of cmd's operators outside them.
        command.push_str(&format!(" || echo {refusal}"));
    } else {
        command.push_str(&format!(" || printf '%s\\n' '{refusal}'"));
    }
    command
}

/// The variable through which print mode hands `--allowedTools` and `--disallowedTools` to its
/// child session: a JSON list of `[kind, rule]`.
pub const RULES_ENV: &str = "DREAMFERENCE_MIGHTLING_COMPAT_RULES";

/// The command-line rules this process was given by print mode.
pub fn rules_from_env() -> Vec<(Kind, String)> {
    std::env::var(RULES_ENV)
        .ok()
        .and_then(|text| serde_json::from_str::<Vec<(String, String)>>(&text).ok())
        .unwrap_or_default()
        .into_iter()
        .filter_map(|(kind, rule)| match kind.as_str() {
            "allow" => Some((Kind::Allow, rule)),
            "deny" => Some((Kind::Deny, rule)),
            _ => None,
        })
        .collect()
}

/// The `--allowedTools` / `--disallowedTools` lists of a command line (comma or space separated),
/// as print mode passes them on.
pub fn rules_to_env(allowed: &[String], disallowed: &[String]) -> String {
    let mut list: Vec<(String, String)> = allowed.iter().map(|rule| ("allow".to_string(), rule.clone())).collect();
    list.extend(disallowed.iter().map(|rule| ("deny".to_string(), rule.clone())));
    serde_json::to_string(&list).unwrap_or_default()
}

/// A `--allowedTools` value split into rules: commas or spaces between them, never inside `( )`.
pub fn split_tool_list(text: &str) -> Vec<String> {
    let mut rules = Vec::new();
    let mut current = String::new();
    let mut depth = 0;
    for c in text.chars() {
        match c {
            '(' => {
                depth += 1;
                current.push(c);
            }
            ')' => {
                depth -= 1;
                current.push(c);
            }
            ',' | ' ' if depth == 0 => {
                if !current.trim().is_empty() {
                    rules.push(current.trim().to_string());
                }
                current.clear();
            }
            _ => current.push(c),
        }
    }
    if !current.trim().is_empty() {
        rules.push(current.trim().to_string());
    }
    rules
}

/// Claude Code's `--permission-mode` for a TUI session: the Codex options it becomes, a line to
/// show, or why it is refused.
pub fn tui_mode(mode: &str, airgapped_on: bool) -> Result<(Vec<String>, Option<String>), String> {
    match mode {
        "default" | "manual" | "acceptEdits" => Ok((Vec::new(), None)),
        "dontAsk" => Ok((vec!["-a".into(), "never".into()], None)),
        "plan" => Ok((Vec::new(), Some("Permission mode plan: type /plan to start planning (ling's TUI opens in its default mode).".into()))),
        "bypassPermissions" if airgapped_on => Err("--permission-mode bypassPermissions is Full Access, which /airgapped on refuses".into()),
        "bypassPermissions" => Ok((vec!["--dangerously-bypass-approvals-and-sandbox".into()], None)),
        "auto" => Err("--permission-mode auto is not offered: a second model call per tool call costs the local model too much".into()),
        other => Err(format!("--permission-mode {other} is not a permission mode")),
    }
}

/// Takes `--permission-mode`, `--allowedTools` and `--disallowedTools` off a TUI command line:
/// the remaining arguments, the mode's value, and the rules.
pub fn take_tui_flags(args: Vec<std::ffi::OsString>) -> (Vec<std::ffi::OsString>, Option<String>, Vec<(Kind, String)>) {
    let mut out = Vec::new();
    let mut mode = None;
    let mut rules = Vec::new();
    let mut iter = args.into_iter().peekable();
    let mut after_dashes = false;
    if let Some(program) = iter.next() {
        out.push(program);
    }
    while let Some(arg) = iter.next() {
        let text = arg.to_string_lossy().into_owned();
        if after_dashes {
            out.push(arg);
            continue;
        }
        let (name, inline) = match text.split_once('=') {
            Some((name, value)) if name.starts_with("--") => (name.to_string(), Some(value.to_string())),
            _ => (text.clone(), None),
        };
        let mut value = || inline.clone().or_else(|| iter.next().map(|v| v.to_string_lossy().into_owned()));
        match name.as_str() {
            "--" => {
                after_dashes = true;
                out.push(arg);
            }
            "--permission-mode" => mode = value(),
            "--allowedTools" | "--allowed-tools" => rules.extend(split_tool_list(&value().unwrap_or_default()).into_iter().map(|r| (Kind::Allow, r))),
            "--disallowedTools" | "--disallowed-tools" => {
                rules.extend(split_tool_list(&value().unwrap_or_default()).into_iter().map(|r| (Kind::Deny, r)));
            }
            _ => out.push(arg),
        }
    }
    (out, mode, rules)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(kind: Kind, text: &str) -> Rule {
        Rule { kind, text: text.into(), anchor: PathBuf::from("/w") }
    }

    fn bash(command: &str) -> Value {
        json!({"cwd": "/w", "tool_name": "Bash", "tool_input": {"command": command}})
    }

    #[test]
    fn bash_rules_match_as_the_permissions_page_shows() {
        for (rule, yes, no) in [
            ("npm run build", vec!["npm run build"], vec!["npm run build --watch"]),
            ("npm run *", vec!["npm run build", "npm run test --watch", "npm run"], vec!["npm install"]),
            ("git log * main", vec!["git log --oneline main", "git log -5 main"], vec!["git log main", "git push origin main"]),
            ("* --version", vec!["node --version"], vec!["node -v"]),
            ("ls *", vec!["ls -la", "ls"], vec!["lsof"]),
            ("ls*", vec!["ls -la", "lsof"], vec![]),
            ("ls:*", vec!["ls -la", "ls"], vec!["lsof"]),
            ("* --help *", vec!["npm --help x"], vec!["npm --help"]),
        ] {
            for command in yes {
                assert!(bash_matches(rule, command), "{rule} should match {command}");
            }
            for command in no {
                assert!(!bash_matches(rule, command), "{rule} should not match {command}");
            }
        }
    }

    #[test]
    fn compound_commands_wrappers_and_assignments_are_seen_through() {
        assert_eq!(subcommands("git status && npm test; echo $(git clean -f) | cat"), ["git status", "npm test", "echo", "git clean -f", "cat"]);
        assert_eq!(subcommands("timeout 30 npm test"), ["npm test"]);
        assert_eq!(subcommands("nice -n 5 FOO=bar rm -rf tmp/"), ["rm -rf tmp/"]);
        assert_eq!(subcommands("xargs grep pattern"), ["grep pattern"]);
        assert_eq!(subcommands("xargs -n1 grep pattern"), ["xargs -n1 grep pattern"]);
        assert_eq!(subcommands("echo 'a && b'"), ["echo 'a && b'"]);
        let rules = [rule(Kind::Deny, "Bash(git push *)"), rule(Kind::Ask, "Bash(git clean *)")];
        let home = None;
        let root = Path::new("/w");
        let answer = guard_answer(&bash("cd /tmp && git push origin main"), &rules, root, home).unwrap();
        assert_eq!(answer["hookSpecificOutput"]["permissionDecision"], "deny");
        assert!(answer["hookSpecificOutput"]["permissionDecisionReason"].as_str().unwrap().contains("Bash(git push *)"));
        let asked = guard_answer(&bash("echo \"$(git clean -f)\""), &rules, root, home).unwrap();
        assert!(asked["hookSpecificOutput"]["permissionDecisionReason"].as_str().unwrap().contains("cannot ask"));
        assert!(guard_answer(&bash("git status"), &rules, root, home).is_none());
    }

    #[test]
    fn deny_wins_over_ask_and_ask_over_allow() {
        let rules = [rule(Kind::Allow, "Bash(npm *)"), rule(Kind::Ask, "Bash(npm publish *)"), rule(Kind::Deny, "Bash(npm publish --force)")];
        let call = |c: &str| call_from_input(&bash(c));
        let cwd = Path::new("/w");
        assert_eq!(decide(&rules, &call("npm test"), cwd, None).map(|d| d.0), Some(Kind::Allow));
        assert_eq!(decide(&rules, &call("npm publish --tag x"), cwd, None).map(|d| d.0), Some(Kind::Ask));
        assert_eq!(decide(&rules, &call("npm publish --force"), cwd, None).map(|d| d.0), Some(Kind::Deny));
    }

    #[test]
    fn path_rules_follow_the_four_anchors() {
        let home = Path::new("/h");
        let cwd = Path::new("/w");
        let deny = |text: &str| rule(Kind::Deny, text);
        let m = |r: &Rule, f: &str| {
            let (_, spec) = split_rule(&r.text);
            path_matches(&spec.unwrap(), Path::new(f), cwd, r, Some(home))
        };
        assert!(m(&deny("Read(.env)"), "/w/.env") && m(&deny("Read(.env)"), "/w/a/b/.env"));
        assert!(!m(&deny("Read(.env)"), "/other/.env"));
        assert!(m(&deny("Read(./secrets/**)"), "/w/secrets/k.pem"));
        assert!(m(&deny("Read(secrets/**)"), "/w/x/secrets/k.pem"), "a single directory segment in a deny rule is at any depth");
        assert!(!m(&rule(Kind::Allow, "Edit(src/**)"), "/w/vendor/src/a.js"));
        assert!(m(&rule(Kind::Allow, "Edit(src/**)"), "/w/src/a.js"));
        assert!(m(&deny("Edit(/src/**/*.ts)"), "/w/src/a/b.ts") && !m(&deny("Edit(/src/**/*.ts)"), "/w/src/a/b.js"));
        assert!(m(&deny("Read(~/.zshrc)"), "/h/.zshrc"));
        assert!(m(&deny("Edit(//tmp/scratch.txt)"), "/tmp/scratch.txt"));
        let user = Rule { kind: Kind::Deny, text: "Read(/secrets/**)".into(), anchor: PathBuf::from("/h/.claude") };
        assert!(m(&user, "/h/.claude/secrets/x") && !m(&user, "/w/secrets/x"));
    }

    #[test]
    fn edits_shell_reads_redirections_and_fetches_are_seen() {
        let patch = json!({"cwd": "/w", "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Update File: src/a.rs\n@@\n-x\n+y\n*** Add File: .env\n+K=V\n*** End Patch\n"}});
        let rules = [rule(Kind::Deny, "Read(./.env)")];
        let answer = guard_answer(&patch, &rules, Path::new("/w"), None).unwrap();
        assert!(answer.to_string().contains("Read(./.env)"), "a Read deny also refuses an edit there");
        assert!(guard_answer(&bash("cat .env"), &rules, Path::new("/w"), None).is_some());
        assert!(guard_answer(&bash("grep x < .env"), &rules, Path::new("/w"), None).is_some());
        assert!(guard_answer(&bash("echo hi > notes.txt"), &rules, Path::new("/w"), None).is_none());
        let edits = [rule(Kind::Deny, "Edit(notes.txt)")];
        assert!(guard_answer(&bash("echo hi > notes.txt"), &edits, Path::new("/w"), None).is_some());
        assert!(guard_answer(&bash("echo hi | tee -a notes.txt"), &edits, Path::new("/w"), None).is_some());
        let web = [rule(Kind::Deny, "WebFetch(domain:*.evil.com)"), rule(Kind::Deny, "WebFetch(domain:example.*)")];
        assert!(guard_answer(&bash("ling-fetch https://api.evil.com/x"), &web, Path::new("/w"), None).is_some());
        assert!(guard_answer(&bash("ling-fetch https://evil.com/x"), &web, Path::new("/w"), None).is_none());
        assert!(guard_answer(&bash("ling-fetch https://example.org/"), &web, Path::new("/w"), None).is_some());
        assert!(guard_answer(&bash("ling-fetch https://example.evil.net/"), &web, Path::new("/w"), None).is_none());
    }

    #[test]
    fn tools_by_name_and_glob_and_protected_paths() {
        let mcp = json!({"cwd": "/w", "tool_name": "mcp__github__create_issue", "tool_input": {}});
        assert!(guard_answer(&mcp, &[rule(Kind::Deny, "mcp__*")], Path::new("/w"), None).is_some());
        assert!(guard_answer(&mcp, &[rule(Kind::Deny, "mcp__github__create_issue")], Path::new("/w"), None).is_some());
        assert!(guard_answer(&mcp, &[rule(Kind::Deny, "mcp__slack__*")], Path::new("/w"), None).is_none());
        let agent = json!({"cwd": "/w", "tool_name": "spawn_agent", "tool_input": {"agent_type": "deployer"}});
        assert!(guard_answer(&agent, &[rule(Kind::Deny, "Agent(deployer)")], Path::new("/w"), None).is_some());
        let settings = json!({"cwd": "/w", "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Update File: .claude/settings.json\n@@\n-a\n+b\n*** End Patch\n"}});
        assert!(guard_answer(&settings, &[], Path::new("/w"), None).unwrap().to_string().contains("protected"));
    }

    #[test]
    fn the_tui_flags_are_taken_off_and_mapped() {
        let args: Vec<std::ffi::OsString> = ["ling", "--permission-mode", "dontAsk", "--allowedTools", "Bash(npm *),Read", "--disallowedTools=Bash(git push *)", "fix it"]
            .iter()
            .map(std::ffi::OsString::from)
            .collect();
        let (out, mode, rules) = take_tui_flags(args);
        assert_eq!(out, ["ling", "fix it"].map(std::ffi::OsString::from));
        assert_eq!(mode.as_deref(), Some("dontAsk"));
        assert_eq!(rules, vec![(Kind::Allow, "Bash(npm *)".into()), (Kind::Allow, "Read".into()), (Kind::Deny, "Bash(git push *)".into())]);
        assert_eq!(tui_mode("dontAsk", false).unwrap().0, ["-a", "never"]);
        assert!(tui_mode("bypassPermissions", true).is_err());
        assert!(tui_mode("plan", false).unwrap().1.unwrap().contains("/plan"));
        assert!(tui_mode("auto", false).is_err());
        assert_eq!(split_tool_list("Bash(git log *) Read, Edit(src/**)"), ["Bash(git log *)", "Read", "Edit(src/**)"]);
        let refusal = guard_failed_answer().to_string();
        assert_eq!(
            guard_command_for(Path::new("/opt/ling"), &[(Kind::Deny, "Bash(rm *)".into())], false),
            format!("/opt/ling compat guard --deny 'Bash(rm *)' || printf '%s\\n' '{refusal}'")
        );
    }

    #[test]
    fn on_windows_the_guard_takes_its_rules_as_hex_words_and_reads_them_back() {
        let exe = Path::new(r"C:\Users\Jane Doe (x86)\AppData\Local\Programs\Mightling\bin\ling.exe");
        let rules = [(Kind::Deny, "Bash(echo %PATH% & del *)".to_string()), (Kind::Allow, "Read(./src/**)".to_string())];
        let line = guard_command_for(exe, &rules, true);
        let prefix = r#""C:\Users\Jane Doe (x86)\AppData\Local\Programs\Mightling\bin\ling.exe" compat guard --deny-hex "#;
        assert!(line.starts_with(prefix), "{line}");
        let (guard, fallback) = line.split_once(" || ").unwrap();
        assert_eq!(fallback, format!("echo {}", guard_failed_answer()));
        // What the guard reads back from its command line, as Windows splits it (no quotes, no spaces
        // in the words after the program).
        let args: Vec<String> = guard.split(" compat guard ").nth(1).unwrap().split(' ').map(str::to_string).collect();
        assert_eq!(guard_rules_from_args(&args), Some(rules.to_vec()));
        assert_eq!(guard_rules_from_args(&["--deny".into(), "Bash(rm *)".into()]), Some(vec![(Kind::Deny, "Bash(rm *)".into())]));
        assert_eq!(guard_rules_from_args(&["--deny-hex".into(), "zz".into()]), None);
        assert_eq!(guard_rules_from_args(&["--deny".into()]), None);
        assert_eq!(guard_rules_from_args(&[]), Some(vec![]));
    }

    #[test]
    fn the_guards_fallback_refusal_is_plain_words_both_shells_print_as_they_are() {
        let reason = GUARD_FAILED_REASON;
        assert!(!reason.contains(['%', '!', '&', '|', '<', '>', '^', '(', ')', '\'', '"', '\\']), "{reason}");
        let answer = guard_failed_answer();
        assert_eq!(answer["hookSpecificOutput"]["permissionDecision"], "deny");
        assert!(!answer.to_string().contains(['%', '!', '&', '|', '<', '>', '^', '\'']));
    }

    /// The guard's line run the way Codex runs a hook (`hooks/src/engine/command_runner.rs`): with
    /// `$SHELL -lc` (here `/bin/sh -c`) elsewhere and `cmd.exe /C "<line>"` on Windows. A guard
    /// that cannot start still refuses: its program is missing here, in a folder whose name has a
    /// space and parentheses.
    fn run_like_codex(line: &str) -> String {
        #[cfg(windows)]
        let output = {
            use std::os::windows::process::CommandExt;
            std::process::Command::new(std::env::var_os("COMSPEC").unwrap_or_else(|| "cmd.exe".into()))
                .arg("/C")
                .raw_arg(format!("\"{line}\""))
                .stdin(std::process::Stdio::null())
                .output()
                .unwrap()
        };
        #[cfg(not(windows))]
        let output = std::process::Command::new("/bin/sh").arg("-c").arg(line).stdin(std::process::Stdio::null()).output().unwrap();
        String::from_utf8_lossy(&output.stdout).into_owned()
    }

    #[test]
    fn a_repositorys_rules_wait_for_trust_and_the_users_do_not() {
        let base = std::env::temp_dir().join(format!("mightling-compat-permissions-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let (home, repo) = (base.join("home"), base.join("repo"));
        std::fs::create_dir_all(home.join(".claude")).unwrap();
        std::fs::create_dir_all(repo.join(".claude")).unwrap();
        let write = |file: PathBuf, value: Value| std::fs::write(file, value.to_string()).unwrap();
        write(home.join(".claude/settings.json"), json!({"permissions": {"deny": ["Bash(user-deny *)"]}}));
        write(repo.join(".claude/settings.json"), json!({"permissions": {"ask": ["Bash(repo-ask *)"], "allow": ["Bash(repo-allow *)"]}}));
        write(repo.join(".claude/settings.local.json"), json!({"permissions": {"deny": ["Bash(local-deny *)"]}}));
        let extra = [(Kind::Deny, "Bash(flag-deny *)".to_string())];
        // Only this fixture's rules: a managed settings file on the machine adds its own.
        let ours = |rules: Vec<Rule>| -> Vec<Rule> {
            let mine = |rule: &Rule| ["user", "repo", "local", "flag"].iter().any(|word| rule.text.starts_with(&format!("Bash({word}-")));
            rules.into_iter().filter(mine).collect()
        };

        let (untrusted, notes) = rules_for(&repo, Some(&home), false, &extra);
        assert_eq!(
            ours(untrusted),
            [
                Rule { kind: Kind::Deny, text: "Bash(user-deny *)".into(), anchor: home.join(".claude") },
                Rule { kind: Kind::Deny, text: "Bash(flag-deny *)".into(), anchor: repo.clone() },
            ]
        );
        assert!(!notes.iter().any(|note| note.contains(&repo.display().to_string())), "{notes:?}");

        let (trusted, notes) = rules_for(&repo, Some(&home), true, &extra);
        assert_eq!(
            ours(trusted),
            [
                Rule { kind: Kind::Deny, text: "Bash(user-deny *)".into(), anchor: home.join(".claude") },
                Rule { kind: Kind::Allow, text: "Bash(repo-allow *)".into(), anchor: repo.clone() },
                Rule { kind: Kind::Ask, text: "Bash(repo-ask *)".into(), anchor: repo.clone() },
                Rule { kind: Kind::Deny, text: "Bash(local-deny *)".into(), anchor: repo.clone() },
                Rule { kind: Kind::Deny, text: "Bash(flag-deny *)".into(), anchor: repo.clone() },
            ]
        );
        assert!(notes.iter().any(|note| note.starts_with("allow rules in") && note.contains("settings.json")), "{notes:?}");
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn a_guard_that_cannot_run_refuses_the_call() {
        let dir = std::env::temp_dir().join(format!("mightling guard (x86) {}", std::process::id()));
        let exe = dir.join(if cfg!(windows) { "ling.exe" } else { "ling" });
        let line = guard_command_for(&exe, &[(Kind::Deny, "Bash(rm *)".into())], cfg!(windows));
        let stdout = run_like_codex(&line);
        let answer: Value = serde_json::from_str(stdout.trim()).unwrap_or_else(|err| panic!("{err}: {stdout:?} from {line}"));
        assert_eq!(answer, guard_failed_answer());
    }
}
