//! The compatibility mode's subagents: Claude Code's `.claude/agents/*.md` and
//! `~/.claude/agents/*.md`, given to the session as Codex agent roles
//! (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §9.4).
//!
//! Each file's frontmatter names the agent (`name`, `description`) and its body is the agent's
//! instructions. The launcher writes one Codex role file per agent under
//! `$CODEX_HOME/compat/claude/<project>/agents/`, rebuilt at every start from the source files and
//! never in the repository or `~/.claude`. It declares the roles on the command line,
//! `-c agents={<name>={description=…, config_file=…}}`, so they exist for this session only. The
//! priority is Claude Code's: a project's agent, the one closest to the working folder, wins over
//! the user's of the same name. A repository's agents are read only once the folder is trusted, as
//! its skills are.
//!
//! What maps:
//!
//! - `description`: the role's description.
//! - The body: its `developer_instructions`.
//! - `tools` and `disallowedTools`: an agent left with no editing tool (`Edit`, `Write`,
//!   `MultiEdit`, `NotebookEdit`) and no `Bash` works in a read-only sandbox, so Claude Code's
//!   read-only agents stay read-only. Other tool lists cannot be expressed in a Codex role; they
//!   are reported.
//! - `permissionMode: plan` is read-only too. `bypassPermissions` is refused, so a role never runs
//!   with less of a sandbox than its session.
//! - `effort`: `model_reasoning_effort` (`xhigh` and `max` become `high`).
//! - `model`: ignored, since Mightling serves one model. `maxTurns`, `skills`, `mcpServers`,
//!   `hooks`, `memory`, `isolation` and `background` are reported as not honoured.

use std::collections::BTreeMap;
use std::path::Path;
use std::path::PathBuf;

use serde_yaml::Value as Yaml;

/// One Claude Code subagent, as read from its file.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Agent {
    pub name: String,
    pub description: String,
    pub instructions: String,
    pub read_only: bool,
    pub effort: Option<String>,
    pub source: PathBuf,
    /// Frontmatter fields Codex has no place for.
    pub not_honoured: Vec<String>,
}

/// The tools that change files or run commands; an agent without any works read-only.
const EDITING_TOOLS: &[&str] = &["Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell"];

/// Frontmatter fields read but not honoured, and why.
const NOT_HONOURED: &[(&str, &str)] = &[
    ("maxTurns", "Codex roles have no turn limit"),
    ("skills", "skills are not preloaded into a role"),
    ("mcpServers", "a role shares the session's MCP servers"),
    ("hooks", "hooks are the session's"),
    ("memory", "no persistent agent memory"),
    ("isolation", "roles run in the session's folder"),
    ("background", "Codex decides how a role runs"),
];

fn tool_list(value: Option<&Yaml>) -> Option<Vec<String>> {
    let value = value?;
    let names: Vec<String> = match value {
        Yaml::String(text) => text.split(',').map(|name| name.trim().to_string()).collect(),
        Yaml::Sequence(items) => items.iter().filter_map(Yaml::as_str).map(|name| name.trim().to_string()).collect(),
        _ => return None,
    };
    Some(
        names
            .into_iter()
            .filter(|name| !name.is_empty())
            .map(|name| name.split('(').next().unwrap_or_default().trim().to_string())
            .collect(),
    )
}

/// Reads one agent file. Err says why it was skipped.
pub fn parse(text: &str, source: &Path) -> Result<Agent, String> {
    let (yaml, body) = ling_skills::frontmatter::split(text).ok_or("no frontmatter block (--- … ---) at the top")?;
    let root: Yaml = serde_yaml::from_str(yaml).map_err(|err| format!("the frontmatter is not valid YAML: {err}"))?;
    let field = |key: &str| root.get(key).and_then(Yaml::as_str).map(str::trim).filter(|value| !value.is_empty());
    let name = field("name").ok_or("no name")?.to_string();
    if name.contains(':') || name.starts_with('-') || name.len() > 256 {
        return Err(format!("the name {name:?} is not one Claude Code accepts"));
    }
    let description = field("description").ok_or("no description")?.to_string();
    let instructions = body.trim().to_string();
    let allowed = tool_list(root.get("tools"));
    let denied = tool_list(root.get("disallowedTools")).unwrap_or_default();
    let mut not_honoured = Vec::new();
    let can_edit = |tool: &&str| allowed.as_ref().is_none_or(|list| list.iter().any(|name| name == tool)) && !denied.iter().any(|name| name == tool);
    let tools_read_only = !EDITING_TOOLS.iter().any(|tool| can_edit(&tool));
    if allowed.is_some() && !tools_read_only {
        not_honoured.push("tools (Codex roles take the session's tools; only a read-only list is kept, as a read-only sandbox)".to_string());
    }
    let mode = field("permissionMode").unwrap_or("default");
    if mode == "bypassPermissions" {
        not_honoured.push("permissionMode bypassPermissions (a role never runs with less of a sandbox than its session)".to_string());
    }
    let read_only = tools_read_only || mode == "plan";
    let effort = field("effort").map(|level| match level {
        "xhigh" | "max" => "high".to_string(),
        other => other.to_string(),
    });
    for (key, why) in NOT_HONOURED {
        if root.get(*key).is_some() {
            not_honoured.push(format!("{key} ({why})"));
        }
    }
    Ok(Agent { name, description, instructions, read_only, effort, source: source.to_path_buf(), not_honoured })
}

/// The `.md` files under a folder, its subfolders included, sorted.
fn agent_files(dir: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut folders = vec![dir.to_path_buf()];
    while let Some(folder) = folders.pop() {
        let Ok(entries) = std::fs::read_dir(&folder) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                folders.push(path);
            } else if path.extension().is_some_and(|extension| extension == "md") {
                found.push(path);
            }
        }
    }
    found.sort();
    found
}

/// The agents for a session, by name, and what was skipped.
pub fn discover(cwd: &Path, root: &Path, home: Option<&Path>, project_trusted: bool) -> (BTreeMap<String, Agent>, Vec<String>) {
    let mut agents = BTreeMap::new();
    let mut notes = Vec::new();
    // Lowest priority first, so a later one replaces an earlier one of the same name: the user's,
    // then the project's from the repository root down to the working folder.
    let mut folders: Vec<PathBuf> = home.map(|home| home.join(".claude").join("agents")).into_iter().collect();
    let mut project: Vec<PathBuf> = Vec::new();
    let mut folder = Some(cwd);
    while let Some(current) = folder {
        project.push(current.join(".claude").join("agents"));
        if current == root {
            break;
        }
        folder = current.parent();
    }
    project.reverse();
    let project_has_agents = project.iter().any(|dir| !agent_files(dir).is_empty());
    if project_trusted {
        folders.extend(project);
    } else if project_has_agents {
        notes.push("this repository's .claude/agents wait until the folder is trusted (Codex asks when a session starts here)".to_string());
    }
    for dir in folders {
        for file in agent_files(&dir) {
            let Ok(text) = std::fs::read_to_string(&file) else { continue };
            match parse(&text, &file) {
                Ok(agent) => {
                    for field in &agent.not_honoured {
                        notes.push(format!("agent {}: {field} not honoured", agent.name));
                    }
                    agents.insert(agent.name.clone(), agent);
                }
                Err(reason) => notes.push(format!("{} is skipped: {reason}", file.display())),
            }
        }
    }
    (agents, notes)
}

fn toml_string(text: &str) -> String {
    toml::Value::String(text.to_string()).to_string()
}

/// A Codex role file for an agent.
pub fn role_file(agent: &Agent) -> String {
    let mut text = format!(
        "# Written by ling's compatibility mode from {} at the start of a session; rebuilt every time.\n\
         name = {}\ndescription = {}\ndeveloper_instructions = {}\n",
        agent.source.display(),
        toml_string(&agent.name),
        toml_string(&agent.description),
        toml_string(if agent.instructions.is_empty() { &agent.description } else { &agent.instructions })
    );
    if agent.read_only {
        text.push_str("sandbox_mode = \"read-only\"\n");
    }
    if let Some(effort) = &agent.effort {
        text.push_str(&format!("model_reasoning_effort = {}\n", toml_string(effort)));
    }
    text
}

/// A short, stable name for a project's folder under `compat/claude/`.
pub fn project_key(root: &Path) -> String {
    use sha2::Digest;
    let digest = sha2::Sha256::digest(root.display().to_string().as_bytes());
    let hex: String = digest.iter().take(8).map(|byte| format!("{byte:02x}")).collect();
    let name = root.file_name().and_then(|name| name.to_str()).unwrap_or("project");
    let name: String = name.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '_' }).collect();
    format!("{name}-{hex}")
}

/// Writes the role files into `dir` (emptied first) and returns the `-c agents={…}` setting.
pub fn write_roles(agents: &BTreeMap<String, Agent>, dir: &Path) -> std::io::Result<Option<String>> {
    let _ = std::fs::remove_dir_all(dir);
    if agents.is_empty() {
        return Ok(None);
    }
    std::fs::create_dir_all(dir)?;
    let mut entries = Vec::new();
    for (index, agent) in agents.values().enumerate() {
        let file = dir.join(format!("{index:03}.toml"));
        std::fs::write(&file, role_file(agent))?;
        entries.push(format!(
            "{}={{description={}, config_file={}}}",
            toml_string(&agent.name),
            toml_string(&agent.description),
            toml_string(&file.display().to_string())
        ));
    }
    Ok(Some(format!("agents={{{}}}", entries.join(", "))))
}

/// The session's agent settings in the mode.
pub fn prepare(args: Vec<std::ffi::OsString>, codex_home: &Path, cwd: &Path) -> (Vec<std::ffi::OsString>, usize, Vec<String>) {
    if std::env::var_os(crate::compat_print::BARE_ENV).is_some() {
        return (args, 0, Vec::new());
    }
    let root = crate::compat_tui::project_root(cwd);
    let trusted = ling_skills::catalog::is_trusted(codex_home, &root);
    let (agents, mut notes) = discover(cwd, &root, ling_node_locator::home_dir().as_deref(), trusted);
    let dir = codex_home.join("compat").join("claude").join(project_key(&root)).join("agents");
    let setting = match write_roles(&agents, &dir) {
        Ok(setting) => setting,
        Err(err) => {
            notes.push(format!("the agent roles could not be written to {}: {err}", dir.display()));
            None
        }
    };
    let Some(setting) = setting else { return (args, 0, notes) };
    let mut args = args.into_iter();
    let mut out: Vec<std::ffi::OsString> = args.next().into_iter().collect();
    out.extend(["-c".into(), std::ffi::OsString::from(setting)]);
    out.extend(args);
    (out, agents.len(), notes)
}

#[cfg(test)]
mod tests {
    use super::*;

    const REVIEWER: &str = "---\nname: code-reviewer\ndescription: Reviews code for quality\ntools: Read, Glob, Grep\nmodel: sonnet\n---\nYou are a code reviewer.\n";

    #[test]
    fn an_agent_file_becomes_a_role() {
        let agent = parse(REVIEWER, Path::new("/r/.claude/agents/code-reviewer.md")).unwrap();
        assert_eq!(agent.name, "code-reviewer");
        assert!(agent.read_only, "no editing tool: read-only");
        assert!(agent.not_honoured.is_empty(), "{:?}", agent.not_honoured);
        let text = role_file(&agent);
        let table: toml::Table = toml::from_str(&text).unwrap();
        assert_eq!(table["name"].as_str(), Some("code-reviewer"));
        assert_eq!(table["developer_instructions"].as_str(), Some("You are a code reviewer."));
        assert_eq!(table["sandbox_mode"].as_str(), Some("read-only"));
        // Codex's own parser takes the file.
        let parsed = codex_agent_role_parse(&text);
        assert_eq!(parsed.as_deref(), Ok("code-reviewer"));
    }

    /// Codex's own parser of role files, which is what the session runs.
    fn codex_agent_role_parse(text: &str) -> Result<String, String> {
        codex_agent_roles::parse_agent_role_file_contents(text, Path::new("/r/000.toml"), Path::new("/r"), None)
            .map(|role| role.role_name)
            .map_err(|err| err.to_string())
    }

    #[test]
    fn tools_permission_mode_and_effort_map_or_are_reported() {
        let writer = parse("---\nname: w\ndescription: d\ntools: [Read, Edit]\neffort: max\nmaxTurns: 3\n---\nbody\n", Path::new("/w.md")).unwrap();
        assert!(!writer.read_only);
        assert_eq!(writer.effort.as_deref(), Some("high"));
        assert!(writer.not_honoured.iter().any(|f| f.starts_with("tools")));
        assert!(writer.not_honoured.iter().any(|f| f.starts_with("maxTurns")));
        let all_tools = parse("---\nname: a\ndescription: d\n---\nbody\n", Path::new("/a.md")).unwrap();
        assert!(!all_tools.read_only && all_tools.not_honoured.is_empty());
        let denied = parse("---\nname: x\ndescription: d\ndisallowedTools: Edit, Write, MultiEdit, NotebookEdit, Bash(git push *), PowerShell\n---\nb\n", Path::new("/x.md")).unwrap();
        assert!(denied.read_only);
        let plan = parse("---\nname: p\ndescription: d\npermissionMode: plan\n---\nb\n", Path::new("/p.md")).unwrap();
        assert!(plan.read_only);
        let bypass = parse("---\nname: b\ndescription: d\npermissionMode: bypassPermissions\n---\nb\n", Path::new("/b.md")).unwrap();
        assert!(!bypass.read_only && bypass.not_honoured.iter().any(|f| f.contains("bypassPermissions")));
        for bad in ["no frontmatter", "---\ndescription: d\n---\n", "---\nname: a:b\ndescription: d\n---\n", "---\nname: n\n---\n"] {
            assert!(parse(bad, Path::new("/bad.md")).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_project_wins_over_the_user_and_waits_for_trust() {
        let root = std::env::temp_dir().join(format!("mightling-compat-agents-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let write = |path: &str, text: &str| {
            let path = root.join(path);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        };
        write("home/.claude/agents/review.md", "---\nname: reviewer\ndescription: mine\n---\nuser body\n");
        write("home/.claude/agents/sub/helper.md", "---\nname: helper\ndescription: helps\n---\nhelp\n");
        write("repo/.claude/agents/review.md", "---\nname: reviewer\ndescription: the project's\n---\nproject body\n");
        write("repo/sub/.claude/agents/review.md", "---\nname: reviewer\ndescription: nearest\n---\nnearest body\n");
        std::fs::create_dir_all(root.join("repo/.git")).unwrap();
        let home = root.join("home");
        let (agents, notes) = discover(&root.join("repo/sub"), &root.join("repo"), Some(&home), false);
        assert_eq!(agents["reviewer"].description, "mine");
        assert!(agents.contains_key("helper"), "subfolders are scanned");
        assert!(notes.iter().any(|n| n.contains("wait until the folder is trusted")));
        let (agents, _) = discover(&root.join("repo/sub"), &root.join("repo"), Some(&home), true);
        assert_eq!(agents["reviewer"].description, "nearest");
        let dir = root.join("codex-home/compat/claude/x/agents");
        let setting = write_roles(&agents, &dir).unwrap().unwrap();
        let table: toml::Table = toml::from_str(&setting).unwrap();
        let reviewer = &table["agents"]["reviewer"];
        assert_eq!(reviewer["description"].as_str(), Some("nearest"));
        assert!(Path::new(reviewer["config_file"].as_str().unwrap()).is_file());
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 2);
        // Rebuilt from nothing: an agent removed from the sources is gone.
        assert_eq!(write_roles(&BTreeMap::new(), &dir).unwrap(), None);
        assert!(!dir.exists());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn project_keys_are_short_stable_and_safe() {
        let key = project_key(Path::new("/srv/a/my repo"));
        assert!(key.starts_with("my_repo-") && key.len() == "my_repo-".len() + 16, "{key}");
        assert_eq!(key, project_key(Path::new("/srv/a/my repo")));
        assert_ne!(key, project_key(Path::new("/srv/b/my repo")));
    }
}
