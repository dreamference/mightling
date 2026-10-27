//! The compatibility mode's hooks: the `hooks` block of Claude Code's settings files, translated
//! into Codex hooks for one session (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §9.5, §8.4).
//!
//! Codex's hook engine already has Claude Code's core events, its exit codes and its decision
//! fields. It also knows `Bash`, `Edit`, `Write` and `Agent` as matcher names, and names MCP tools
//! `mcp__<server>__<tool>`. What differs is small:
//!
//! - **Where the hooks live.** They are read from the managed file, `~/.claude/settings.json`,
//!   `.claude/settings.json` and `.claude/settings.local.json`, all of them merged, and never
//!   written. They are given to the session on the command line (`-c hooks={…}`) together with the
//!   memory hook (compat_memory.rs). Nothing goes into `config.toml`.
//! - **Trust.** The user's own hooks and the managed ones are trusted with an entry beside them.
//!   A repository's hooks are offered only once the folder is trusted, and then go through
//!   Codex's own review (`/hooks`), by hash, so a changed command must be approved again. Codex
//!   keeps that approval in `config.toml` under the hook's session-flags key.
//! - **Matchers.** Claude Code reads a matcher of letters, digits, `_`, `-`, spaces, `,` and `|` as
//!   a list of exact names; Codex's exact class has no `-`, space or `,`. Such a matcher becomes a
//!   `|` list, or an anchored expression when a name holds a `-`. `MultiEdit` becomes `Edit`.
//!   An expression that is not valid for Codex's regex engine is skipped and reported.
//! - **Input and output.** Each command runs through `ling compat hook-shim`, which gives it the
//!   input Claude Code documents (`apply_patch` as `Write` or `Edit` with `file_path`, `content`,
//!   `old_string`, `new_string`) and `CLAUDE_PROJECT_DIR`. It returns the output with the decisions
//!   Codex does not accept taken out: a plain `allow`, `ask` and `defer` become "no decision", so
//!   Codex's own approval applies.
//! - **Events** Codex lacks (`Notification`, `PostToolUseFailure`, `InstructionsLoaded`, …) are
//!   skipped and listed by `ling compat doctor`. `disableAllHooks: true` in any file turns them all
//!   off, except the mode's memory hook, which is Mightling's.

use std::path::Path;
use std::path::PathBuf;

use serde_json::Value;
use serde_json::json;

/// Claude Code's events that Codex has, under the same name.
pub const EVENTS: &[&str] = &[
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "SessionEnd",
];

/// The managed settings file on Linux.
pub const MANAGED_SETTINGS: &str = "/etc/claude-code/managed-settings.json";

/// Codex's timeout for a command hook when none is given, in seconds; Claude Code's is 60.
const DEFAULT_TIMEOUT_S: i64 = 60;

/// Where a settings file came from, which decides whether its hooks are trusted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Origin {
    Managed,
    User,
    Project,
}

/// One command hook, translated.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Handler {
    pub command: String,
    pub timeout: i64,
    pub status_message: Option<String>,
}

/// One matcher group for one event.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Group {
    pub event: String,
    pub matcher: Option<String>,
    pub handlers: Vec<Handler>,
    /// Whether the launcher writes its trust entry (the user's own hooks, the managed ones, the
    /// mode's memory hook); a repository's are left to Codex's review.
    pub trusted: bool,
    /// Codex's `additionalContextLimit` for the handlers, when not Codex's default.
    pub context_limit: Option<i64>,
}

/// The hooks for a session, and what was left out.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Plan {
    pub groups: Vec<Group>,
    pub notes: Vec<String>,
}

/// The settings files, in the order their hooks are added (all of them merge).
pub fn settings_files(project_root: &Path, home: Option<&Path>, managed: &Path) -> Vec<(PathBuf, Origin)> {
    let mut files = vec![(managed.to_path_buf(), Origin::Managed)];
    if let Some(home) = home {
        files.push((home.join(".claude").join("settings.json"), Origin::User));
    }
    files.push((project_root.join(".claude").join("settings.json"), Origin::Project));
    files.push((project_root.join(".claude").join("settings.local.json"), Origin::Project));
    files
}

/// A Claude Code matcher as Codex reads it, or Err when Codex cannot use it.
pub fn translate_matcher(matcher: Option<&str>) -> Result<Option<String>, String> {
    let Some(matcher) = matcher.map(str::trim) else { return Ok(None) };
    if matcher.is_empty() || matcher == "*" {
        return Ok(None);
    }
    let exact = matcher.chars().all(|c| c.is_ascii_alphanumeric() || "_- ,|".contains(c));
    if exact {
        let mut names: Vec<String> = matcher
            .split(['|', ','])
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .map(|name| if name == "MultiEdit" { "Edit".to_string() } else { name.to_string() })
            .collect();
        let mut seen = std::collections::HashSet::new();
        names.retain(|name| seen.insert(name.clone()));
        if names.iter().all(|name| name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')) {
            return Ok(Some(names.join("|")));
        }
        let escaped: Vec<String> = names.iter().map(|name| regex_lite_escape(name)).collect();
        return Ok(Some(format!("^(?:{})$", escaped.join("|"))));
    }
    match regex_check(matcher) {
        Ok(()) => Ok(Some(matcher.to_string())),
        Err(err) => Err(format!("matcher {matcher:?} is not an expression Codex can read ({err})")),
    }
}

fn regex_lite_escape(text: &str) -> String {
    let mut out = String::new();
    for c in text.chars() {
        if "\\.+*?()|[]{}^$#&-~".contains(c) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

/// A light check of an expression against what Codex's `regex` crate refuses: look-around and
/// back-references, which JavaScript has and Rust's engine does not.
fn regex_check(pattern: &str) -> Result<(), String> {
    for refused in ["(?=", "(?!", "(?<=", "(?<!"] {
        if pattern.contains(refused) {
            return Err(format!("look-around `{refused}`"));
        }
    }
    let bytes = pattern.as_bytes();
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'\\' && bytes.get(index + 1).is_some_and(|next| next.is_ascii_digit() && *next != b'0') {
            return Err("a back-reference".to_string());
        }
    }
    let mut depth: i32 = 0;
    for c in pattern.chars() {
        match c {
            '(' => depth += 1,
            ')' => depth -= 1,
            _ => {}
        }
        if depth < 0 {
            return Err("unbalanced parentheses".to_string());
        }
    }
    if depth != 0 {
        return Err("unbalanced parentheses".to_string());
    }
    Ok(())
}

/// The hook groups of one settings file's JSON.
pub fn groups_from_settings(settings: &Value, origin: Origin, shim: &dyn Fn(&str) -> String, plan: &mut Plan, file: &Path) {
    let Some(hooks) = settings.get("hooks").and_then(Value::as_object) else { return };
    for (event, groups) in hooks {
        if !EVENTS.contains(&event.as_str()) {
            plan.notes.push(format!("{event} hooks in {} are skipped: Codex has no {event} event", file.display()));
            continue;
        }
        let Some(groups) = groups.as_array() else { continue };
        for group in groups {
            let matcher = match translate_matcher(group.get("matcher").and_then(Value::as_str)) {
                Ok(matcher) => matcher,
                Err(reason) => {
                    plan.notes.push(format!("a {event} hook in {} is skipped: {reason}", file.display()));
                    continue;
                }
            };
            let mut handlers = Vec::new();
            for handler in group.get("hooks").and_then(Value::as_array).into_iter().flatten() {
                let kind = handler.get("type").and_then(Value::as_str).unwrap_or("command");
                let Some(command) = handler.get("command").and_then(Value::as_str).filter(|_| kind == "command") else {
                    plan.notes.push(format!("a {kind} hook for {event} in {} is skipped: only command hooks are run", file.display()));
                    continue;
                };
                handlers.push(Handler {
                    command: shim(command),
                    timeout: handler.get("timeout").and_then(Value::as_i64).unwrap_or(DEFAULT_TIMEOUT_S),
                    status_message: handler.get("statusMessage").and_then(Value::as_str).map(str::to_string),
                });
            }
            if !handlers.is_empty() {
                plan.groups.push(Group {
                    event: event.clone(),
                    matcher,
                    handlers,
                    trusted: origin != Origin::Project,
                    context_limit: None,
                });
            }
        }
    }
}

/// The shim command Codex runs for a hook: this binary, `compat hook-shim`, and the user's
/// command, quoted for the shell Codex runs hooks through on this platform.
pub fn shim_command(exe: &Path, command: &str) -> String {
    shim_command_for(exe, command, cfg!(windows))
}

/// [`shim_command`] for either shell. A POSIX shell elsewhere: the command as one shell word,
/// readable in Codex's `/hooks` review. `cmd.exe /C` on Windows (Codex's hook runner), which knows
/// no single quotes, expands `%VAR%` even inside double quotes and reads `&|<>^()` as operators:
/// there the command goes as a hex word ([`hex_word`]) that no shell rule touches, and the program
/// in double quotes ([`crate::compaction::quote_program`]).
pub fn shim_command_for(exe: &Path, command: &str, windows: bool) -> String {
    let exe = exe.display().to_string();
    if windows {
        format!("{} compat hook-shim --hex {}", crate::compaction::quote_program(&exe, true), hex_word(command))
    } else {
        format!("{} compat hook-shim -- {}", shell_word(&exe), shell_word(command))
    }
}

/// Text as a word every shell passes through untouched: its UTF-8 bytes in lowercase hex.
pub fn hex_word(text: &str) -> String {
    text.bytes().map(|byte| format!("{byte:02x}")).collect()
}

/// The text of a [`hex_word`], or None when the word is not one.
pub fn from_hex_word(word: &str) -> Option<String> {
    if word.len() % 2 != 0 || !word.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return None;
    }
    let bytes: Option<Vec<u8>> = (0..word.len()).step_by(2).map(|index| u8::from_str_radix(&word[index..index + 2], 16).ok()).collect();
    String::from_utf8(bytes?).ok()
}

/// A string as one POSIX shell word.
pub fn shell_word(text: &str) -> String {
    if !text.is_empty() && text.chars().all(|c| c.is_ascii_alphanumeric() || "/._-+=:@%".contains(c)) {
        return text.to_string();
    }
    format!("'{}'", text.replace('\'', "'\\''"))
}

/// Codex's trust hash for one handler of a group, from the same identity Codex builds:
/// `{event_name, hooks: [handler], matcher?}`, each key in order, hashed with SHA-256.
pub fn trust_hash(event: &str, matcher: Option<&str>, handler: &Handler, context_limit: Option<i64>) -> String {
    use sha2::Digest;
    let mut fields = serde_json::Map::new();
    if let Some(limit) = context_limit.filter(|limit| *limit != 2_500) {
        fields.insert("additionalContextLimit".to_string(), json!(limit));
    }
    fields.insert("async".to_string(), json!(false));
    fields.insert("command".to_string(), json!(handler.command));
    if let Some(message) = &handler.status_message {
        fields.insert("statusMessage".to_string(), json!(message));
    }
    fields.insert("timeout".to_string(), json!(handler.timeout));
    fields.insert("type".to_string(), json!("command"));
    let mut identity = serde_json::Map::new();
    identity.insert("event_name".to_string(), json!(event_label(event)));
    identity.insert("hooks".to_string(), json!([Value::Object(fields)]));
    if let Some(matcher) = matcher {
        identity.insert("matcher".to_string(), json!(matcher));
    }
    let serialized = serde_json::to_vec(&Value::Object(identity)).unwrap_or_default();
    let hex: String = sha2::Sha256::digest(serialized).iter().map(|byte| format!("{byte:02x}")).collect();
    format!("sha256:{hex}")
}

/// The source Codex names in the trust key of a hook given with `-c`: its session-flags layer's
/// synthetic path (`hooks/src/engine/discovery.rs`, `synthetic_layer_path`), resolved against `/`,
/// or against `C:\` on Windows, where it reads `C:\<session-flags>\config.toml`. Keys in the Unix
/// form left every hook of the mode untrusted on Windows, so Codex ran none of them, `CLAUDE.md`'s
/// and the permission guard included (WINDOWS_ARM §23.4 W1).
pub fn session_flags_source(windows: bool) -> &'static str {
    if windows { r"C:\<session-flags>\config.toml" } else { "/<session-flags>/config.toml" }
}

/// `PreToolUse` → `pre_tool_use`, as Codex names events in trust keys and hashes.
pub fn event_label(event: &str) -> String {
    let mut label = String::new();
    for (index, c) in event.chars().enumerate() {
        if c.is_ascii_uppercase() && index > 0 {
            label.push('_');
        }
        label.push(c.to_ascii_lowercase());
    }
    label
}

fn toml_string(text: &str) -> String {
    toml::Value::String(text.to_string()).to_string()
}

/// The one `-c hooks={…}` setting for a plan: every group under its event, and the trust entries of
/// the trusted ones, keyed by their place among the session-flags groups of their event.
pub fn setting(plan: &Plan) -> Option<String> {
    if plan.groups.is_empty() {
        return None;
    }
    let mut events: Vec<&str> = Vec::new();
    for group in &plan.groups {
        if !events.contains(&group.event.as_str()) {
            events.push(&group.event);
        }
    }
    let mut parts = Vec::new();
    let mut state = Vec::new();
    for event in events {
        let mut groups = Vec::new();
        for (index, group) in plan.groups.iter().filter(|group| group.event == event).enumerate() {
            let handlers: Vec<String> = group
                .handlers
                .iter()
                .map(|handler| {
                    let mut fields = vec![
                        "type=\"command\"".to_string(),
                        format!("command={}", toml_string(&handler.command)),
                        format!("timeout={}", handler.timeout),
                    ];
                    if let Some(message) = &handler.status_message {
                        fields.push(format!("statusMessage={}", toml_string(message)));
                    }
                    if let Some(limit) = group.context_limit {
                        fields.push(format!("additionalContextLimit={limit}"));
                    }
                    format!("{{{}}}", fields.join(", "))
                })
                .collect();
            let matcher = group.matcher.as_deref().map(|m| format!("matcher={}, ", toml_string(m))).unwrap_or_default();
            groups.push(format!("{{{matcher}hooks=[{}]}}", handlers.join(", ")));
            if group.trusted {
                for (handler_index, handler) in group.handlers.iter().enumerate() {
                    let key = format!("{}:{}:{index}:{handler_index}", session_flags_source(cfg!(windows)), event_label(event));
                    let hash = trust_hash(event, group.matcher.as_deref(), handler, group.context_limit);
                    state.push(format!("{}={{trusted_hash={}}}", toml_string(&key), toml_string(&hash)));
                }
            }
        }
        parts.push(format!("{event}=[{}]", groups.join(", ")));
    }
    if !state.is_empty() {
        parts.push(format!("state={{{}}}", state.join(", ")));
    }
    Some(format!("hooks={{{}}}", parts.join(", ")))
}

/// The memory hook (compat_memory.rs) as a group of the plan, first among `SessionStart`'s.
pub fn memory_group(command: &str) -> Group {
    let hook = &crate::compat_memory::MEMORY_HOOK;
    Group {
        event: hook.event.to_string(),
        matcher: Some(hook.matcher.to_string()),
        handlers: vec![Handler { command: command.to_string(), timeout: hook.timeout, status_message: None }],
        trusted: true,
        context_limit: hook.context_limit,
    }
}

/// Reads every settings file's hooks into a plan, after the memory group. A repository's hooks
/// only when `project_trusted`.
pub fn plan_for(
    files: &[(PathBuf, Origin)],
    project_trusted: bool,
    memory: Option<Group>,
    shim: &dyn Fn(&str) -> String,
) -> Plan {
    let mut plan = Plan::default();
    plan.groups.extend(memory);
    let mut parsed = Vec::new();
    for (path, origin) in files {
        let Ok(text) = std::fs::read_to_string(path) else { continue };
        match serde_json::from_str::<Value>(&text) {
            Ok(settings) => parsed.push((path.clone(), *origin, settings)),
            Err(err) => plan.notes.push(format!("{} is not JSON ({err}); its hooks are skipped", path.display())),
        }
    }
    if parsed.iter().any(|(_, _, settings)| settings.get("disableAllHooks").and_then(Value::as_bool) == Some(true)) {
        plan.notes.push("disableAllHooks is set: no hook from Claude Code's settings runs".to_string());
        return plan;
    }
    for (path, origin, settings) in parsed {
        if origin == Origin::Project && !project_trusted {
            if settings.get("hooks").is_some() {
                plan.notes.push(format!(
                    "the hooks in {} wait until this folder is trusted (Codex asks when a session starts here)",
                    path.display()
                ));
            }
            continue;
        }
        groups_from_settings(&settings, origin, shim, &mut plan, &path);
    }
    plan
}

/// The session's hook settings in the mode: the memory hook, then the settings files' hooks.
pub fn prepare(
    args: Vec<std::ffi::OsString>,
    codex_home: &Path,
    cwd: &Path,
    extra_rules: &[(crate::compat_permissions::Kind, String)],
) -> (Vec<std::ffi::OsString>, Plan) {
    let bare = std::env::var_os(crate::compat_print::BARE_ENV).is_some();
    let Ok(exe) = std::env::current_exe() else { return (args, Plan::default()) };
    let root = crate::compat_tui::project_root(cwd);
    let files = if bare {
        Vec::new()
    } else {
        settings_files(&root, ling_node_locator::home_dir().as_deref(), Path::new(MANAGED_SETTINGS))
    };
    let trusted = ling_skills::catalog::is_trusted(codex_home, &root);
    let memory = (!bare).then(|| memory_group(&crate::compat_memory::hook_command(&exe)));
    let shim = |command: &str| shim_command(&exe, command);
    let mut plan = plan_for(&files, trusted, memory, &shim);
    // Claude Code's permission rules, enforced by one `PreToolUse` hook (compat_permissions.rs).
    if !bare {
        plan.groups.push(Group {
            event: "PreToolUse".to_string(),
            matcher: None,
            handlers: vec![Handler {
                command: crate::compat_permissions::guard_command(&exe, extra_rules),
                timeout: DEFAULT_TIMEOUT_S,
                status_message: None,
            }],
            trusted: true,
            context_limit: None,
        });
    }
    let Some(setting) = setting(&plan) else { return (args, plan) };
    let mut args = args.into_iter();
    let mut out: Vec<std::ffi::OsString> = args.next().into_iter().collect();
    out.extend(["-c".into(), std::ffi::OsString::from(setting)]);
    out.extend(args);
    (out, plan)
}

// -- the shim ---------------------------------------------------------------------------------

/// A Codex `apply_patch` as the file tool Claude Code would have used: `Write` for a patch that
/// only adds files, `Edit` otherwise, with the first file's fields.
pub fn file_tool_input(patch: &str, cwd: &Path) -> (String, Value) {
    let mut adds = Vec::new();
    let mut others = Vec::new();
    let mut current: Option<(String, bool, Vec<String>, Vec<String>)> = None;
    let mut flush = |current: &mut Option<(String, bool, Vec<String>, Vec<String>)>| {
        if let Some((path, is_add, old, new)) = current.take() {
            if is_add {
                adds.push((path, new.join("\n")));
            } else {
                others.push((path, old.join("\n"), new.join("\n")));
            }
        }
    };
    for line in patch.lines() {
        if let Some(path) = line.strip_prefix("*** Add File: ") {
            flush(&mut current);
            current = Some((path.trim().to_string(), true, Vec::new(), Vec::new()));
        } else if let Some(path) = line.strip_prefix("*** Update File: ").or_else(|| line.strip_prefix("*** Delete File: ")) {
            flush(&mut current);
            current = Some((path.trim().to_string(), false, Vec::new(), Vec::new()));
        } else if line.starts_with("*** ") {
            continue;
        } else if let Some((_, _, old, new)) = current.as_mut() {
            if let Some(text) = line.strip_prefix('+') {
                new.push(text.to_string());
            } else if let Some(text) = line.strip_prefix('-') {
                old.push(text.to_string());
            } else if let Some(text) = line.strip_prefix(' ') {
                old.push(text.to_string());
                new.push(text.to_string());
            }
        }
    }
    flush(&mut current);
    let absolute = |path: &str| {
        let path = Path::new(path);
        if path.is_absolute() { path.to_path_buf() } else { cwd.join(path) }.display().to_string()
    };
    if others.is_empty()
        && let Some((path, content)) = adds.first()
    {
        return ("Write".to_string(), json!({"file_path": absolute(path), "content": content}));
    }
    let (path, old, new) = others.first().cloned().unwrap_or_default();
    ("Edit".to_string(), json!({"file_path": absolute(&path), "old_string": old, "new_string": new, "replace_all": false}))
}

/// Codex's hook input as Claude Code documents it.
pub fn translate_input(mut input: Value) -> Value {
    let cwd = PathBuf::from(input["cwd"].as_str().unwrap_or("."));
    match input["tool_name"].as_str() {
        Some("apply_patch") => {
            let patch = input["tool_input"]["command"].as_str().unwrap_or_default().to_string();
            let (name, tool_input) = file_tool_input(&patch, &cwd);
            input["tool_name"] = json!(name);
            input["tool_input"] = tool_input;
        }
        Some("spawn_agent") => input["tool_name"] = json!("Agent"),
        _ => {}
    }
    // `permission_mode` is left as Codex gives it: Codex already uses Claude Code's names.
    input
}

/// A hook's stdout with the decisions Codex refuses taken out, and a note for each.
pub fn translate_output(stdout: &str, event: &str) -> (String, Vec<String>) {
    let Ok(mut output) = serde_json::from_str::<Value>(stdout.trim()) else { return (stdout.to_string(), Vec::new()) };
    let mut notes = Vec::new();
    if let Some(specific) = output.get_mut("hookSpecificOutput").and_then(Value::as_object_mut) {
        let decision = specific.get("permissionDecision").and_then(Value::as_str).map(str::to_string);
        let updated = specific.contains_key("updatedInput");
        let keep = match decision.as_deref() {
            Some("allow") => updated && event == "PreToolUse",
            Some("ask") | Some("defer") => false,
            _ => true,
        };
        if !keep {
            specific.remove("permissionDecision");
            specific.remove("permissionDecisionReason");
            specific.remove("updatedInput");
            notes.push(format!(
                "the hook's permissionDecision {:?} is not one Codex takes; its own approval applies instead",
                decision.unwrap_or_default()
            ));
        }
        if specific.keys().all(|key| key == "hookEventName") {
            output.as_object_mut().map(|object| object.remove("hookSpecificOutput"));
        }
    }
    (output.to_string(), notes)
}

/// The shell a user's hook command runs in, as Claude Code runs it: `/bin/sh -c` on Unix. On
/// Windows Claude Code runs hooks in Git Bash; so does the shim when it finds one
/// (`CLAUDE_CODE_GIT_BASH_PATH`, as Claude Code reads it, or the `bash.exe` beside `git.exe` on
/// `PATH`), and otherwise `cmd.exe /C`, Codex's own hook shell, given the command as it is written.
fn hook_shell(command: &str) -> std::process::Command {
    #[cfg(windows)]
    {
        if let Some(bash) = git_bash() {
            let mut shell = std::process::Command::new(bash);
            shell.arg("-c").arg(command);
            return shell;
        }
        use std::os::windows::process::CommandExt;
        let comspec = std::env::var_os("COMSPEC").unwrap_or_else(|| "cmd.exe".into());
        let mut shell = std::process::Command::new(comspec);
        shell.arg("/C").raw_arg(format!("\"{command}\""));
        shell
    }
    #[cfg(not(windows))]
    {
        let mut shell = std::process::Command::new("/bin/sh");
        shell.arg("-c").arg(command);
        shell
    }
}

/// Git for Windows' `bash.exe`: `CLAUDE_CODE_GIT_BASH_PATH`, else the one beside the `git.exe` on
/// `PATH` (`Git\cmd\git.exe` or `Git\bin\git.exe` → `Git\bin\bash.exe`). Never WSL's
/// `System32\bash.exe`, which would run the hook in another system.
#[cfg(windows)]
fn git_bash() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("CLAUDE_CODE_GIT_BASH_PATH").map(PathBuf::from).filter(|path| path.is_file()) {
        return Some(path);
    }
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).filter(|dir| dir.join("git.exe").is_file()).find_map(|dir| {
        let git_root = dir.parent()?;
        [git_root.join("bin").join("bash.exe"), git_root.join("usr").join("bin").join("bash.exe")].into_iter().find(|bash| bash.is_file())
    })
}

/// `ling compat hook-shim -- <command>`: runs the user's command with Claude Code's input and
/// passes its result back to Codex.
pub fn run_shim(args: &[String]) -> i32 {
    use std::io::Read;
    use std::io::Write;
    let command = match args {
        [dashes, command] if dashes == "--" => Some(command.clone()),
        [flag, word] if flag == "--hex" => from_hex_word(word),
        _ => None,
    };
    let Some(command) = command else {
        eprintln!("usage: ling compat hook-shim -- <command> | --hex <hex>");
        return 2;
    };
    let mut raw = String::new();
    let _ = std::io::stdin().read_to_string(&mut raw);
    let input = serde_json::from_str::<Value>(&raw).map(translate_input).unwrap_or(Value::Null);
    let event = input["hook_event_name"].as_str().unwrap_or_default().to_string();
    let cwd = input["cwd"].as_str().map(PathBuf::from).or_else(|| std::env::current_dir().ok()).unwrap_or_default();
    let mut child = match hook_shell(&command)
        .current_dir(&cwd)
        .env("CLAUDE_PROJECT_DIR", crate::compat_tui::project_root(&cwd))
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(err) => {
            eprintln!("could not run the hook: {err}");
            return 1;
        }
    };
    if let Some(mut stdin) = child.stdin.take() {
        let text = if input.is_null() { raw } else { input.to_string() };
        let _ = stdin.write_all(text.as_bytes());
    }
    let Ok(output) = child.wait_with_output() else { return 1 };
    let code = output.status.code().unwrap_or(1);
    let stdout = String::from_utf8_lossy(&output.stdout);
    let (stdout, notes) = if code == 0 { translate_output(&stdout, &event) } else { (stdout.to_string(), Vec::new()) };
    print!("{stdout}");
    for note in notes {
        eprintln!("{note}");
    }
    code
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shim(command: &str) -> String {
        shim_command_for(Path::new("/opt/ling"), command, false)
    }

    #[test]
    fn matchers_become_what_codex_reads() {
        assert_eq!(translate_matcher(None), Ok(None));
        assert_eq!(translate_matcher(Some("*")), Ok(None));
        assert_eq!(translate_matcher(Some("Bash")), Ok(Some("Bash".into())));
        assert_eq!(translate_matcher(Some("Edit|Write|MultiEdit")), Ok(Some("Edit|Write".into())));
        assert_eq!(translate_matcher(Some("Edit, Write")), Ok(Some("Edit|Write".into())));
        assert_eq!(translate_matcher(Some("code-reviewer")), Ok(Some("^(?:code\\-reviewer)$".into())));
        assert_eq!(translate_matcher(Some("mcp__memory__.*")), Ok(Some("mcp__memory__.*".into())));
        assert!(translate_matcher(Some("^(?!Bash).*")).is_err());
    }

    #[test]
    fn hooks_merge_from_every_file_and_a_repositorys_wait_for_trust() {
        let root = std::env::temp_dir().join(format!("mightling-compat-hooks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("home/.claude")).unwrap();
        std::fs::create_dir_all(root.join("repo/.claude")).unwrap();
        std::fs::write(
            root.join("home/.claude/settings.json"),
            r#"{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"~/bin/guard.sh"}]}],
                "Notification":[{"hooks":[{"type":"command","command":"notify"}]}]}}"#,
        )
        .unwrap();
        std::fs::write(
            root.join("repo/.claude/settings.json"),
            r#"{"hooks":{"PostToolUse":[{"matcher":"Edit|Write","hooks":[{"type":"command","command":"npm run lint","timeout":30},{"type":"http","url":"http://x"}]}]}}"#,
        )
        .unwrap();
        let files = settings_files(&root.join("repo"), Some(&root.join("home")), &root.join("managed.json"));
        let untrusted = plan_for(&files, false, Some(memory_group("/opt/ling compat memory")), &shim);
        assert_eq!(untrusted.groups.len(), 2, "{:?}", untrusted.groups);
        assert!(untrusted.notes.iter().any(|n| n.contains("Notification hooks")));
        assert!(untrusted.notes.iter().any(|n| n.contains("wait until this folder is trusted")));
        let trusted = plan_for(&files, true, Some(memory_group("/opt/ling compat memory")), &shim);
        assert_eq!(trusted.groups.len(), 3);
        let lint = &trusted.groups[2];
        assert_eq!((lint.event.as_str(), lint.matcher.as_deref(), lint.trusted), ("PostToolUse", Some("Edit|Write"), false));
        assert_eq!(lint.handlers[0].command, "/opt/ling compat hook-shim -- 'npm run lint'");
        assert_eq!(lint.handlers[0].timeout, 30);
        assert!(trusted.notes.iter().any(|n| n.contains("http hook")));
        // disableAllHooks anywhere: only the mode's own memory hook is left.
        std::fs::write(root.join("repo/.claude/settings.local.json"), r#"{"disableAllHooks": true}"#).unwrap();
        let off = plan_for(&files, true, Some(memory_group("/opt/ling compat memory")), &shim);
        assert_eq!(off.groups.len(), 1);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn the_setting_is_one_table_with_trust_for_the_users_hooks_only() {
        let plan = Plan {
            groups: vec![
                memory_group("/opt/ling compat memory"),
                Group {
                    event: "SessionStart".into(),
                    matcher: None,
                    handlers: vec![Handler { command: shim("echo hi"), timeout: 60, status_message: None }],
                    trusted: true,
                    context_limit: None,
                },
                Group {
                    event: "PreToolUse".into(),
                    matcher: Some("Bash".into()),
                    handlers: vec![Handler { command: shim("./check.sh"), timeout: 60, status_message: Some("checking".into()) }],
                    trusted: false,
                    context_limit: None,
                },
            ],
            notes: vec![],
        };
        let text = setting(&plan).unwrap();
        let table: toml::Table = toml::from_str(&text).unwrap();
        let hooks = &table["hooks"];
        assert_eq!(hooks["SessionStart"].as_array().unwrap().len(), 2);
        assert_eq!(hooks["PreToolUse"][0]["hooks"][0]["statusMessage"].as_str(), Some("checking"));
        let state = hooks["state"].as_table().unwrap();
        let keys: Vec<&String> = state.keys().collect();
        let source = session_flags_source(cfg!(windows));
        let (first, second) = (format!("{source}:session_start:0:0"), format!("{source}:session_start:1:0"));
        assert_eq!(keys, [&first, &second]);
        // The memory group's entry is the hash compat_memory computes for it.
        assert_eq!(
            state[&format!("{source}:session_start:0:0")]["trusted_hash"].as_str(),
            Some(crate::compaction::hook_hash_for("/opt/ling compat memory", &crate::compat_memory::MEMORY_HOOK).as_str())
        );
        assert_eq!(setting(&Plan::default()), None);
    }

    #[test]
    fn the_trust_hash_is_the_one_codex_computes() {
        use codex_config::HookHandlerConfig;
        use codex_config::MatcherGroup;
        #[derive(serde::Serialize)]
        struct Identity {
            event_name: &'static str,
            #[serde(flatten)]
            group: MatcherGroup,
        }
        let handler = Handler { command: "/opt/ling compat hook-shim -- x".into(), timeout: 30, status_message: Some("s".into()) };
        let group = MatcherGroup {
            matcher: Some("Edit|Write".to_string()),
            hooks: vec![HookHandlerConfig::Command {
                command: handler.command.clone(),
                command_windows: None,
                timeout_sec: Some(30),
                r#async: false,
                status_message: Some("s".to_string()),
                additional_context_limit: None,
            }],
        };
        let value = toml::Value::try_from(Identity { event_name: "post_tool_use", group }).unwrap();
        assert_eq!(trust_hash("PostToolUse", Some("Edit|Write"), &handler, None), codex_config::version_for_toml(&value));
    }

    #[test]
    fn the_shim_gives_claude_codes_input_and_takes_out_what_codex_refuses() {
        let input = json!({
            "hook_event_name": "PreToolUse", "cwd": "/w", "tool_name": "apply_patch", "permission_mode": "default",
            "tool_input": {"command": "*** Begin Patch\n*** Update File: src/a.rs\n@@\n-old line\n+new line\n*** End Patch\n"},
        });
        let out = translate_input(input);
        assert_eq!(out["tool_name"], "Edit");
        assert_eq!(out["tool_input"]["file_path"], "/w/src/a.rs");
        assert_eq!(out["tool_input"]["old_string"], "old line");
        assert_eq!(out["tool_input"]["new_string"], "new line");
        let add = translate_input(json!({"cwd": "/w", "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Add File: b.txt\n+one\n+two\n*** End Patch\n"}}));
        assert_eq!(add["tool_name"], "Write");
        assert_eq!(add["tool_input"]["content"], "one\ntwo");
        assert_eq!(translate_input(json!({"tool_name": "Bash", "tool_input": {"command": "ls"}}))["tool_input"]["command"], "ls");
        assert_eq!(translate_input(json!({"tool_name": "spawn_agent"}))["tool_name"], "Agent");

        let (out, notes) = translate_output(r#"{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}"#, "PreToolUse");
        assert_eq!(out, "{}");
        assert_eq!(notes.len(), 1);
        let (out, _) = translate_output(r#"{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"no"}}"#, "PreToolUse");
        assert!(out.contains("deny"));
        let (out, notes) = translate_output(r#"{"hookSpecificOutput":{"permissionDecision":"allow","updatedInput":{"command":"ls -a"}}}"#, "PreToolUse");
        assert!(out.contains("ls -a") && notes.is_empty());
        let (out, _) = translate_output(r#"{"continue":false,"stopReason":"x","hookSpecificOutput":{"permissionDecision":"ask"}}"#, "PreToolUse");
        assert!(out.contains("\"continue\":false") && !out.contains("ask"));
        assert_eq!(translate_output("plain text", "Stop").0, "plain text");
    }

    #[test]
    fn the_session_flags_source_is_the_path_codex_resolves() {
        // Codex builds it as AbsolutePathBuf::resolve_path_against_base("<session-flags>/config.toml",
        // "/" or "C:\\"); on this machine that must give this platform's form.
        let base = if cfg!(windows) { r"C:\" } else { "/" };
        let codex = codex_utils_absolute_path::AbsolutePathBuf::resolve_path_against_base("<session-flags>/config.toml", base);
        assert_eq!(codex.as_path().display().to_string(), session_flags_source(cfg!(windows)));
        assert_eq!(session_flags_source(true), r"C:\<session-flags>\config.toml");
    }

    #[test]
    fn commands_are_quoted_as_one_shell_word() {
        assert_eq!(shell_word("/opt/ling"), "/opt/ling");
        assert_eq!(shell_word("npm run lint"), "'npm run lint'");
        assert_eq!(shell_word("echo 'hi'"), "'echo '\\''hi'\\'''");
    }

    #[test]
    fn on_windows_the_shim_names_the_command_by_a_hex_word_and_the_program_in_double_quotes() {
        // cmd.exe would expand %PATH%, cut at & and |, and keep single quotes as text.
        let command = r#"echo "%PATH%" & npm run lint | findstr ^x 'y' (z) — é"#;
        let exe = Path::new(r"C:\Users\Jane Doe (x86)\AppData\Local\Programs\Mightling\bin\ling.exe");
        let line = shim_command_for(exe, command, true);
        let prefix = r#""C:\Users\Jane Doe (x86)\AppData\Local\Programs\Mightling\bin\ling.exe" compat hook-shim --hex "#;
        assert!(line.starts_with(prefix), "{line}");
        let word = &line[prefix.len()..];
        assert!(word.bytes().all(|byte| byte.is_ascii_hexdigit()), "{word}");
        assert_eq!(from_hex_word(word).as_deref(), Some(command));
        assert_eq!(from_hex_word("6"), None);
        assert_eq!(from_hex_word("zz"), None);
        assert_eq!(from_hex_word(""), Some(String::new()));
    }
}
