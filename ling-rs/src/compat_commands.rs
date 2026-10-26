//! The compatibility mode's custom commands: Claude Code's `.claude/commands/*.md` files and its
//! skills, typed as `/name args` (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §9.4).
//!
//! Claude Code's documentation says custom commands have been merged into skills: a file at
//! `.claude/commands/deploy.md` and a skill at `.claude/skills/deploy/SKILL.md` both make
//! `/deploy`. In the mode the launcher does the same, without a patch, through the `Enter` hook
//! (compat_keys.rs) and print mode's prompt:
//!
//! - **A command file** is a prompt template. `/name args` is replaced by the file's body with the
//!   arguments put in: `$ARGUMENTS`, `$ARGUMENTS[N]` and `$N` (from 0), and `${CLAUDE_PROJECT_DIR}`.
//!   A body with no placeholder gets `ARGUMENTS: <args>` after it, as Claude Code does. The
//!   expanded text is what the session is asked.
//! - **A skill** (`~/.claude/skills`, a trusted repository's `.claude/skills`, which every `ling`
//!   session links in already) is named the way Codex selects a skill, `$name args`.
//! - **`` !`command` ``** lines are not run: the TUI's process is outside the sandbox, and a
//!   command from a repository's file must not run there. The placeholder becomes a request to
//!   run the command with the shell first, so the model runs it in the sandbox.
//!
//! The names come from `~/.claude/commands` and a trusted repository's `.claude/commands`,
//! subfolders included (`frontend/component.md` is `/frontend:component`); a project's file wins
//! over the user's of the same name, and a skill wins over a command, as in Claude Code. A Codex
//! command of the same name wins over both, since Codex's popup offers it.

use std::collections::BTreeMap;
use std::path::Path;
use std::path::PathBuf;

/// Codex's built-in commands in this build (with Mightling's), which a Claude Code command of
/// the same name does not shadow. A test checks the list against the pinned TUI's.
pub const CODEX_COMMANDS: &[&str] = &[
    "model", "ide", "permissions", "keymap", "vim", "setup-default-sandbox", "experimental", "approve", "memories",
    "skills", "import", "hooks", "review", "rename", "new", "archive", "delete", "resume", "fork", "worktree", "app",
    "init", "compact", "recap", "plan", "voice", "goal", "agents", "side", "btw", "copy", "export", "raw", "tui",
    "diff", "mention", "status", "daemon", "warnings", "cd", "pwd", "cwd", "usage", "debug-config", "title",
    "statusline", "theme", "pets", "pet", "mcp", "apps", "plugins", "logout", "quit", "exit", "feedback", "rollout",
    "ps", "stop", "clean", "clear", "test-approval", "subagents", "debug-m-drop", "debug-m-update",
    // Mightling's, from its patches.
    "cavemode", "adapter", "airgapped", "night", "node",
];

/// Where a command comes from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Kind {
    /// A command file: its body.
    File { body: String },
    /// A skill: Codex selects it by `$name`.
    Skill,
}

/// One command a user can type.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Command {
    pub name: String,
    pub description: String,
    pub source: PathBuf,
    pub kind: Kind,
}

/// The `.md` files under a folder, with the command name each makes.
fn command_files(dir: &Path) -> Vec<(String, PathBuf)> {
    let mut found = Vec::new();
    let mut folders = vec![dir.to_path_buf()];
    while let Some(folder) = folders.pop() {
        let Ok(entries) = std::fs::read_dir(&folder) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                folders.push(path);
            } else if path.extension().is_some_and(|extension| extension == "md")
                && let Ok(relative) = path.strip_prefix(dir)
            {
                let mut parts: Vec<String> =
                    relative.with_extension("").components().map(|part| part.as_os_str().to_string_lossy().into_owned()).collect();
                let name = parts.pop().unwrap_or_default();
                parts.push(name);
                found.push((parts.join(":"), path));
            }
        }
    }
    found.sort();
    found
}

/// The skills directly under a folder: `<dir>/<skill>/SKILL.md`, named by their frontmatter.
fn skill_names(dir: &Path) -> Vec<(String, String, PathBuf)> {
    let mut found = Vec::new();
    let Ok(entries) = std::fs::read_dir(dir) else { return found };
    for entry in entries.flatten() {
        let file = entry.path().join("SKILL.md");
        let Ok(text) = std::fs::read_to_string(&file) else { continue };
        let Ok(frontmatter) = ling_skills::frontmatter::parse(&text) else { continue };
        let name = frontmatter.name.unwrap_or_else(|| entry.file_name().to_string_lossy().into_owned());
        found.push((name, frontmatter.description, file));
    }
    found.sort();
    found
}

/// A command file's description and body.
fn read_command(text: &str) -> (String, String) {
    match ling_skills::frontmatter::split(text) {
        Some((yaml, body)) => {
            let description = serde_yaml::from_str::<serde_yaml::Value>(yaml)
                .ok()
                .and_then(|root| root.get("description").and_then(serde_yaml::Value::as_str).map(str::to_string))
                .unwrap_or_default();
            (description, body.trim().to_string())
        }
        None => (String::new(), text.trim().to_string()),
    }
}

/// The commands for a session in `cwd`, by name, lowest priority first so later ones replace.
pub fn discover(root: &Path, home: Option<&Path>, project_trusted: bool) -> BTreeMap<String, Command> {
    let mut commands = BTreeMap::new();
    let mut command_dirs: Vec<PathBuf> = home.map(|home| home.join(".claude").join("commands")).into_iter().collect();
    let mut skill_dirs: Vec<PathBuf> = home.map(|home| home.join(".claude").join("skills")).into_iter().collect();
    if project_trusted {
        command_dirs.push(root.join(".claude").join("commands"));
        skill_dirs.push(root.join(".claude").join("skills"));
    }
    for dir in command_dirs {
        for (name, path) in command_files(&dir) {
            let Ok(text) = std::fs::read_to_string(&path) else { continue };
            let (description, body) = read_command(&text);
            commands.insert(name.clone(), Command { name, description, source: path, kind: Kind::File { body } });
        }
    }
    for dir in skill_dirs {
        for (name, description, path) in skill_names(&dir) {
            commands.insert(name.clone(), Command { name, description, source: path, kind: Kind::Skill });
        }
    }
    commands
}

/// The arguments as Claude Code splits them: words, with quotes keeping spaces together.
pub fn split_arguments(args: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;
    let mut started = false;
    for c in args.chars() {
        match (quote, c) {
            (Some(q), c) if c == q => quote = None,
            (Some(_), c) => current.push(c),
            (None, '"' | '\'') => {
                quote = Some(c);
                started = true;
            }
            (None, c) if c.is_whitespace() => {
                if started || !current.is_empty() {
                    words.push(std::mem::take(&mut current));
                    started = false;
                }
            }
            (None, c) => {
                current.push(c);
                started = true;
            }
        }
    }
    if started || !current.is_empty() {
        words.push(current);
    }
    words
}

/// A command file's body with the arguments put in.
pub fn expand(body: &str, args: &str, project_dir: &Path) -> String {
    let words = split_arguments(args);
    let mut out = String::new();
    let mut used = false;
    let chars: Vec<char> = body.chars().collect();
    let mut index = 0;
    while index < chars.len() {
        let rest: String = chars[index..].iter().take(32).collect();
        if rest.starts_with("$ARGUMENTS[") {
            let digits: String = chars[index + 11..].iter().take_while(|c| c.is_ascii_digit()).collect();
            if !digits.is_empty() && chars.get(index + 11 + digits.len()) == Some(&']') {
                let n: usize = digits.parse().unwrap_or(usize::MAX);
                if let Some(word) = words.get(n) {
                    out.push_str(word);
                    used = true;
                } else {
                    out.push_str(&format!("$ARGUMENTS[{digits}]"));
                }
                index += 12 + digits.len();
                continue;
            }
        }
        if rest.starts_with("$ARGUMENTS") {
            out.push_str(args.trim());
            used = true;
            index += 10;
            continue;
        }
        if rest.starts_with("${CLAUDE_PROJECT_DIR}") {
            out.push_str(&project_dir.display().to_string());
            index += 21;
            continue;
        }
        if chars[index] == '$' && chars.get(index + 1).is_some_and(char::is_ascii_digit) {
            let digits: String = chars[index + 1..].iter().take_while(|c| c.is_ascii_digit()).collect();
            let n: usize = digits.parse().unwrap_or(usize::MAX);
            if let Some(word) = words.get(n) {
                out.push_str(word);
                used = true;
            } else {
                out.push('$');
                out.push_str(&digits);
            }
            index += 1 + digits.len();
            continue;
        }
        out.push(chars[index]);
        index += 1;
    }
    let out = without_shell_lines(&out);
    if !used && !args.trim().is_empty() {
        format!("{}\n\nARGUMENTS: {}", out.trim_end(), args.trim())
    } else {
        out
    }
}

/// `` !`command` `` at the start of a line or after a space becomes a request to run the command
/// with the shell first, since the launcher runs nothing outside the sandbox.
fn without_shell_lines(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(at) = rest.find("!`") {
        let before = &rest[..at];
        let after_space = before.is_empty() || before.ends_with(char::is_whitespace);
        let Some(end) = rest[at + 2..].find('`') else { break };
        out.push_str(before);
        let command = &rest[at + 2..at + 2 + end];
        if after_space {
            out.push_str(&format!("(the output of `{command}`: run it with the shell first)"));
        } else {
            out.push_str(&rest[at..at + 3 + end]);
        }
        rest = &rest[at + 3 + end..];
    }
    out.push_str(rest);
    out
}

/// What `/name args` becomes, or None when it is not one of these commands (or is Codex's).
pub fn resolve(name: &str, args: &str, commands: &BTreeMap<String, Command>, project_dir: &Path) -> Option<String> {
    if CODEX_COMMANDS.contains(&name) {
        return None;
    }
    let command = commands.get(name)?;
    Some(match &command.kind {
        Kind::Skill => if args.trim().is_empty() { format!("${name}") } else { format!("${name} {}", args.trim()) },
        Kind::File { body } => expand(body, args, project_dir),
    })
}

/// The commands for a session working in `cwd`, found as `ling` would.
pub fn for_dir(cwd: &Path) -> (BTreeMap<String, Command>, PathBuf) {
    let root = crate::compat_tui::project_root(cwd);
    let trusted = codex_utils_home_dir::find_codex_home()
        .ok()
        .is_some_and(|home| ling_skills::catalog::is_trusted(home.as_path(), &root));
    (discover(&root, ling_node_locator::home_dir().as_deref(), trusted), root)
}

/// A prompt that starts with `/name`: what it becomes, or None.
pub fn expand_prompt(prompt: &str, cwd: &Path) -> Option<String> {
    let rest = prompt.trim_start().strip_prefix('/')?;
    let (name, args) = rest.split_once(char::is_whitespace).unwrap_or((rest, ""));
    let (commands, root) = for_dir(cwd);
    resolve(name, args, &commands, &root)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn arguments_go_where_the_documentation_says() {
        let dir = Path::new("/p");
        assert_eq!(expand("Fix GitHub issue $ARGUMENTS following our standards.", "123", dir), "Fix GitHub issue 123 following our standards.");
        assert_eq!(
            expand("Migrate the $ARGUMENTS[0] component from $ARGUMENTS[1] to $ARGUMENTS[2].", "SearchBar JavaScript TypeScript", dir),
            "Migrate the SearchBar component from JavaScript to TypeScript."
        );
        assert_eq!(expand("Migrate the $0 component from $1 to $2.", "A B C", dir), "Migrate the A component from B to C.");
        assert_eq!(expand("Say $1.", "only", dir), "Say $1.\n\nARGUMENTS: only", "an indexed placeholder without its argument counts for nothing");
        assert_eq!(expand("No placeholder here.", "x y", dir), "No placeholder here.\n\nARGUMENTS: x y");
        assert_eq!(expand("No placeholder here.", "", dir), "No placeholder here.");
        assert_eq!(expand("cd ${CLAUDE_PROJECT_DIR}", "", dir), "cd /p");
        assert_eq!(expand("Name: $0", "\"two words\"", dir), "Name: two words");
    }

    #[test]
    fn shell_lines_are_asked_for_not_run() {
        assert_eq!(
            expand("- PR diff: !`gh pr diff`\nKEY=!`cmd`", "", Path::new("/p")),
            "- PR diff: (the output of `gh pr diff`: run it with the shell first)\nKEY=!`cmd`"
        );
    }

    #[test]
    fn commands_and_skills_are_found_named_and_ranked() {
        let root = std::env::temp_dir().join(format!("mightling-compat-commands-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let write = |path: &str, text: &str| {
            let path = root.join(path);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        };
        write("home/.claude/commands/deploy.md", "---\ndescription: mine\n---\nDeploy to $ARGUMENTS.\n");
        write("home/.claude/commands/frontend/component.md", "Make a $0 component.\n");
        write("home/.claude/skills/pdf/SKILL.md", "---\nname: pdf\ndescription: PDFs\n---\nbody\n");
        write("repo/.claude/commands/deploy.md", "---\ndescription: the project's\n---\nShip it to $ARGUMENTS.\n");
        write("repo/.claude/commands/review.md", "Codex has its own /review.\n");
        let home = root.join("home");
        let untrusted = discover(&root.join("repo"), Some(&home), false);
        assert_eq!(untrusted["deploy"].description, "mine");
        let commands = discover(&root.join("repo"), Some(&home), true);
        assert_eq!(commands["deploy"].description, "the project's");
        let p = root.join("repo");
        assert_eq!(resolve("deploy", "staging", &commands, &p).as_deref(), Some("Ship it to staging."));
        assert_eq!(resolve("frontend:component", "Button", &commands, &p).as_deref(), Some("Make a Button component."));
        assert_eq!(resolve("pdf", "report.pdf", &commands, &p).as_deref(), Some("$pdf report.pdf"));
        assert_eq!(resolve("review", "", &commands, &p), None, "Codex's command wins");
        assert_eq!(resolve("nothing", "", &commands, &p), None);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn codexs_command_list_is_the_pinned_tuis() {
        // Every name the pinned TUI's SlashCommand enum serializes, read from its source: a new
        // Codex command, or a renamed one, fails here until CODEX_COMMANDS has it.
        // A Windows checkout may have CRLF line endings.
        let source = include_str!("../../tui/src/slash_command.rs").replace("\r\n", "\n");
        let source = source.as_str();
        let start = source.find("pub enum SlashCommand").unwrap();
        let body = &source[start..source[start..].find("\n}\n").unwrap() + start];
        let mut names = Vec::new();
        let mut pending: Vec<String> = Vec::new();
        for line in body.lines().skip(1) {
            let line = line.trim();
            if line.starts_with("#[strum(") {
                for part in line.split('"').skip(1).step_by(2) {
                    pending.push(part.to_string());
                }
                continue;
            }
            if let Some(variant) = line.strip_suffix(',').filter(|v| v.chars().all(|c| c.is_ascii_alphanumeric()) && !v.is_empty()) {
                if pending.is_empty() {
                    let mut kebab = String::new();
                    for (i, c) in variant.chars().enumerate() {
                        if c.is_ascii_uppercase() && i > 0 {
                            kebab.push('-');
                        }
                        kebab.push(c.to_ascii_lowercase());
                    }
                    names.push(kebab);
                } else {
                    names.append(&mut pending);
                }
            }
        }
        assert!(names.len() > 50, "{names:?}");
        let missing: Vec<&String> = names.iter().filter(|name| !CODEX_COMMANDS.contains(&name.as_str())).collect();
        assert!(missing.is_empty(), "add to CODEX_COMMANDS: {missing:?}");
    }
}
