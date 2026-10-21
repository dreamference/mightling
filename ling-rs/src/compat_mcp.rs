//! The compatibility mode's MCP servers: Claude Code's project `.mcp.json` and the user's
//! `~/.claude.json`, given to the session as Codex MCP servers once the user approved each one
//! (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §9.2, §8.1; owner's decision of 2026-10-21).
//!
//! **The scopes**, as Claude Code's MCP page documents them, and which definition of a name wins:
//! the local scope (`~/.claude.json`, under `projects.<project path>.mcpServers`), then the project
//! scope (`.mcp.json` at the project's root), then the user scope (`~/.claude.json`'s top-level
//! `mcpServers`). A project's file is read only once the folder is trusted.
//!
//! **Approval.** No server starts until the user approved it: `ling compat mcp approve <name>`
//! records the hash of the server's definition (its name, scope, file and JSON) in
//! `$CODEX_HOME/compat/claude/mcp-approved.json`, which only the user writes (the session's
//! sandbox cannot). A definition that changed has a new hash and waits again. The same check is
//! made when Codex starts a stdio server, by the wrapper below, so a file changed after the session
//! began is not run either.
//!
//! **Starting a stdio server.** Codex is given `ling compat mcp-run <scope> <name> <file>` in its
//! place. The wrapper reads the definition again, checks its approval, expands `${VAR}` and
//! `${VAR:-default}` in `command`, `args` and `env` (Codex forwards only the variables named in
//! `env_vars`, so the values never appear on a command line), and replaces itself with the server.
//! At `/airgapped on` it runs the server under `bwrap --unshare-net`, so it has no network; without
//! `bwrap` the server is not started.
//!
//! **HTTP servers** (`type: http` or `streamable-http`) are connected by Codex itself. A header
//! that is `${VAR}` becomes `env_http_headers`, an `Authorization: Bearer ${VAR}` becomes
//! `bearer_token_env_var`, a literal header stays literal; any other mix is refused, since its value
//! would be on the command line. At `/airgapped on` an HTTP server is not offered. `sse` (deprecated)
//! and `ws` servers are refused: Codex has neither transport.

use std::collections::BTreeMap;
use std::path::Path;
use std::path::PathBuf;

use serde_json::Value;
use serde_json::json;

/// Where a server is defined, highest precedence first.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Scope {
    Local,
    Project,
    User,
}

impl Scope {
    pub fn name(self) -> &'static str {
        match self {
            Scope::Local => "local",
            Scope::Project => "project",
            Scope::User => "user",
        }
    }

    pub fn parse(text: &str) -> Option<Scope> {
        match text {
            "local" => Some(Scope::Local),
            "project" => Some(Scope::Project),
            "user" => Some(Scope::User),
            _ => None,
        }
    }
}

/// One server as defined in a Claude Code file.
#[derive(Clone, Debug, PartialEq)]
pub struct Server {
    pub name: String,
    pub scope: Scope,
    pub source: PathBuf,
    pub definition: Value,
}

/// The names Mightling's own MCP servers use, which a Claude Code server may not take.
const RESERVED: &[&str] = &["ling_code", "ling_docs", "mightling_gmail", "mightling_drive", "mightling_calendar", "mightling_images"];

/// Whether a name is one Codex accepts for an MCP server and Mightling has not taken.
pub fn usable_name(name: &str) -> Result<(), String> {
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        return Err(format!("the name {name:?} is not one Codex accepts (letters, digits, _ and -)"));
    }
    if RESERVED.contains(&name) || name.starts_with("mightling_") {
        return Err(format!("the name {name:?} is one of Mightling's own servers"));
    }
    Ok(())
}

fn servers_in(value: Option<&Value>, scope: Scope, source: &Path) -> Vec<Server> {
    value
        .and_then(Value::as_object)
        .map(|servers| {
            servers
                .iter()
                .map(|(name, definition)| Server { name: name.clone(), scope, source: source.to_path_buf(), definition: definition.clone() })
                .collect()
        })
        .unwrap_or_default()
}

/// The servers for a project, by name, the highest-precedence definition of each.
pub fn discover(root: &Path, home: Option<&Path>, project_trusted: bool) -> (BTreeMap<String, Server>, Vec<String>) {
    let mut notes = Vec::new();
    let mut all: Vec<Server> = Vec::new();
    if let Some(home) = home {
        let file = home.join(".claude.json");
        if let Ok(text) = std::fs::read_to_string(&file) {
            match serde_json::from_str::<Value>(&text) {
                Ok(config) => {
                    all.extend(servers_in(config.get("mcpServers"), Scope::User, &file));
                    let project = config.get("projects").and_then(|projects| {
                        projects.as_object()?.iter().find(|(path, _)| same_path(Path::new(path), root)).map(|(_, entry)| entry.clone())
                    });
                    all.extend(servers_in(project.as_ref().and_then(|entry| entry.get("mcpServers")), Scope::Local, &file));
                }
                Err(err) => notes.push(format!("{} is not JSON ({err}); its servers are skipped", file.display())),
            }
        }
    }
    let project_file = root.join(".mcp.json");
    if project_file.is_file() {
        if project_trusted {
            match std::fs::read_to_string(&project_file).map(|text| serde_json::from_str::<Value>(&text)) {
                Ok(Ok(config)) => all.extend(servers_in(config.get("mcpServers"), Scope::Project, &project_file)),
                Ok(Err(err)) => notes.push(format!("{} is not JSON ({err}); its servers are skipped", project_file.display())),
                Err(err) => notes.push(format!("{} could not be read ({err})", project_file.display())),
            }
        } else {
            notes.push(format!("the servers in {} wait until this folder is trusted", project_file.display()));
        }
    }
    all.sort_by_key(|server| std::cmp::Reverse(server.scope));
    let mut servers = BTreeMap::new();
    for server in all {
        servers.insert(server.name.clone(), server);
    }
    (servers, notes)
}

fn same_path(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => a == b,
    }
}

/// The hash a user approves: of the server's name, scope, file and definition.
pub fn definition_hash(server: &Server) -> String {
    use sha2::Digest;
    let identity = json!({
        "definition": sorted(&server.definition),
        "name": server.name,
        "scope": server.scope.name(),
        "source": server.source.display().to_string(),
    });
    let hex: String = sha2::Sha256::digest(serde_json::to_vec(&identity).unwrap_or_default()).iter().map(|b| format!("{b:02x}")).collect();
    format!("sha256:{hex}")
}

fn sorted(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            Value::Object(keys.into_iter().map(|key| (key.clone(), sorted(&map[key]))).collect())
        }
        Value::Array(items) => Value::Array(items.iter().map(sorted).collect()),
        other => other.clone(),
    }
}

/// The approvals file.
pub fn approvals_file(codex_home: &Path) -> PathBuf {
    codex_home.join("compat").join("claude").join("mcp-approved.json")
}

/// The approved hashes.
pub fn approved(codex_home: &Path) -> Vec<String> {
    std::fs::read_to_string(approvals_file(codex_home))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| value.get("approved").and_then(Value::as_array).cloned())
        .unwrap_or_default()
        .iter()
        .filter_map(|entry| entry.get("hash").and_then(Value::as_str).map(str::to_string))
        .collect()
}

fn write_approvals(codex_home: &Path, entries: &[Value]) -> std::io::Result<()> {
    let file = approvals_file(codex_home);
    if let Some(parent) = file.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let text = serde_json::to_string_pretty(&json!({"approved": entries})).unwrap_or_default();
    crate::write_atomically(&file, text.as_bytes()).map_err(std::io::Error::other)
}

fn approval_entries(codex_home: &Path) -> Vec<Value> {
    std::fs::read_to_string(approvals_file(codex_home))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| value.get("approved").and_then(Value::as_array).cloned())
        .unwrap_or_default()
}

/// `${VAR}` and `${VAR:-default}` in a string, from `env`. Err names a variable that is unset and
/// has no default.
pub fn expand(text: &str, env: &dyn Fn(&str) -> Option<String>) -> Result<String, String> {
    let mut out = String::new();
    let mut rest = text;
    while let Some(start) = rest.find("${") {
        out.push_str(&rest[..start]);
        let Some(end) = rest[start..].find('}') else {
            out.push_str(&rest[start..]);
            return Ok(out);
        };
        let inner = &rest[start + 2..start + end];
        let (name, default) = match inner.split_once(":-") {
            Some((name, default)) => (name, Some(default)),
            None => (inner, None),
        };
        match env(name).filter(|value| !value.is_empty()).or_else(|| default.map(str::to_string)) {
            Some(value) => out.push_str(&value),
            None => return Err(format!("${{{name}}} is not set and has no default")),
        }
        rest = &rest[start + end + 1..];
    }
    out.push_str(rest);
    Ok(out)
}

/// The variable names a definition refers to, which Codex must forward to the wrapper.
pub fn referenced_vars(definition: &Value) -> Vec<String> {
    let mut names = Vec::new();
    let mut texts: Vec<String> = Vec::new();
    collect_strings(definition, &mut texts);
    for text in texts {
        let mut rest = text.as_str();
        while let Some(start) = rest.find("${") {
            let Some(end) = rest[start..].find('}') else { break };
            let inner = &rest[start + 2..start + end];
            let name = inner.split(":-").next().unwrap_or_default().to_string();
            if !name.is_empty() && !names.contains(&name) {
                names.push(name);
            }
            rest = &rest[start + end + 1..];
        }
    }
    names
}

fn collect_strings(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::String(text) => out.push(text.clone()),
        Value::Array(items) => items.iter().for_each(|item| collect_strings(item, out)),
        Value::Object(map) => map.values().for_each(|item| collect_strings(item, out)),
        _ => {}
    }
}

fn toml_string(text: &str) -> String {
    toml::Value::String(text.to_string()).to_string()
}

fn toml_list(items: &[String]) -> String {
    format!("[{}]", items.iter().map(|item| toml_string(item)).collect::<Vec<_>>().join(", "))
}

/// The server's transport as Claude Code names it.
fn transport(definition: &Value) -> String {
    match definition.get("type").and_then(Value::as_str) {
        Some("streamable-http") => "http".to_string(),
        Some(kind) => kind.to_string(),
        None if definition.get("url").is_some() => "http".to_string(),
        None => "stdio".to_string(),
    }
}

/// What starting one server takes: its `-c` setting, or why it is not offered.
pub fn setting(server: &Server, exe: &Path, root: &Path, airgapped_on: bool, bwrap: bool) -> Result<String, String> {
    usable_name(&server.name)?;
    let definition = &server.definition;
    let timeout = definition.get("timeout").and_then(Value::as_u64).map(|ms| format!(", tool_timeout_sec={}", ms.div_ceil(1000)));
    match transport(definition).as_str() {
        "stdio" => {
            definition.get("command").and_then(Value::as_str).ok_or("it has no command")?;
            if airgapped_on && !bwrap {
                return Err("at /airgapped on a server runs only without network, under bwrap, which is not installed".to_string());
            }
            let mut args = vec![
                "compat".to_string(),
                "mcp-run".to_string(),
                server.scope.name().to_string(),
                server.name.clone(),
                server.source.display().to_string(),
                root.display().to_string(),
            ];
            if airgapped_on {
                args.push("--offline".to_string());
            }
            let mut vars = referenced_vars(definition);
            for name in ["CODEX_HOME"] {
                if std::env::var_os(name).is_some() && !vars.iter().any(|var| var == name) {
                    vars.push(name.to_string());
                }
            }
            Ok(format!(
                "mcp_servers.{}={{command={}, args={}, env_vars={}{}}}",
                server.name,
                toml_string(&exe.display().to_string()),
                toml_list(&args),
                toml_list(&vars),
                timeout.unwrap_or_default()
            ))
        }
        "http" => {
            if airgapped_on {
                return Err("an HTTP server is not offered at /airgapped on".to_string());
            }
            let url = definition.get("url").and_then(Value::as_str).ok_or("it has no url")?;
            let url = expand(url, &|name| std::env::var(name).ok())?;
            let mut fields = vec![format!("url={}", toml_string(&url))];
            let mut literal = Vec::new();
            let mut from_env = Vec::new();
            for (header, value) in definition.get("headers").and_then(Value::as_object).into_iter().flatten() {
                let value = value.as_str().unwrap_or_default();
                let whole_var = value.strip_prefix("${").and_then(|v| v.strip_suffix('}')).filter(|v| !v.contains(['$', '}', ':']));
                let bearer = value.strip_prefix("Bearer ${").and_then(|v| v.strip_suffix('}')).filter(|v| !v.contains(['$', '}', ':']));
                if let Some(var) = whole_var {
                    from_env.push(format!("{}={}", toml_string(header), toml_string(var)));
                } else if let (Some(var), true) = (bearer, header.eq_ignore_ascii_case("authorization")) {
                    fields.push(format!("bearer_token_env_var={}", toml_string(var)));
                } else if !value.contains("${") {
                    literal.push(format!("{}={}", toml_string(header), toml_string(value)));
                } else {
                    return Err(format!("its header {header} mixes text and variables; its value would be on the command line"));
                }
            }
            if !literal.is_empty() {
                fields.push(format!("http_headers={{{}}}", literal.join(", ")));
            }
            if !from_env.is_empty() {
                fields.push(format!("env_http_headers={{{}}}", from_env.join(", ")));
            }
            Ok(format!("mcp_servers.{}={{{}{}}}", server.name, fields.join(", "), timeout.unwrap_or_default()))
        }
        "sse" => Err("its transport, SSE, is not one Codex has (Claude Code calls it deprecated; use http)".to_string()),
        other => Err(format!("its transport, {other}, is not one Codex has")),
    }
}

/// The servers a session gets and the notes about the rest.
pub struct Plan {
    pub settings: Vec<String>,
    pub offered: Vec<String>,
    pub notes: Vec<String>,
}

/// The plan for a project.
pub fn plan(servers: &BTreeMap<String, Server>, approved: &[String], exe: &Path, root: &Path, airgapped_on: bool, bwrap: bool) -> Plan {
    let mut plan = Plan { settings: Vec::new(), offered: Vec::new(), notes: Vec::new() };
    for server in servers.values() {
        if !approved.contains(&definition_hash(server)) {
            plan.notes.push(format!(
                "MCP server {} ({} scope) waits for your approval: ling compat mcp approve {}",
                server.name,
                server.scope.name(),
                server.name
            ));
            continue;
        }
        match setting(server, exe, root, airgapped_on, bwrap) {
            Ok(setting) => {
                plan.settings.push(setting);
                plan.offered.push(server.name.clone());
            }
            Err(reason) => plan.notes.push(format!("MCP server {} is not offered: {reason}", server.name)),
        }
    }
    plan
}

fn bwrap_installed() -> bool {
    std::env::var_os("PATH")
        .map(|path| std::env::split_paths(&path).any(|dir| dir.join("bwrap").is_file()))
        .unwrap_or(false)
}

/// The session's MCP settings in the mode.
pub fn prepare(args: Vec<std::ffi::OsString>, codex_home: &Path, cwd: &Path, airgapped_on: bool) -> (Vec<std::ffi::OsString>, Plan) {
    let empty = Plan { settings: Vec::new(), offered: Vec::new(), notes: Vec::new() };
    if std::env::var_os(crate::compat_print::BARE_ENV).is_some() {
        return (args, empty);
    }
    let Ok(exe) = std::env::current_exe() else { return (args, empty) };
    let root = crate::compat_tui::project_root(cwd);
    let trusted = ling_skills::catalog::is_trusted(codex_home, &root);
    let (servers, notes) = discover(&root, ling_node_locator::home_dir().as_deref(), trusted);
    let mut plan = plan(&servers, &approved(codex_home), &exe, &root, airgapped_on, bwrap_installed());
    plan.notes.splice(0..0, notes);
    let mut args = args.into_iter();
    let mut out: Vec<std::ffi::OsString> = args.next().into_iter().collect();
    for setting in &plan.settings {
        out.extend(["-c".into(), std::ffi::OsString::from(setting)]);
    }
    out.extend(args);
    (out, plan)
}

/// `ling compat mcp-run <scope> <name> <file> <project root> [--offline]`: starts one approved
/// stdio server, in the project's root as Claude Code does.
pub fn run_server(args: &[String]) -> i32 {
    let (scope, name, file, root, offline) = match args {
        [scope, name, file, root] => (scope, name, file, root, false),
        [scope, name, file, root, flag] if flag == "--offline" => (scope, name, file, root, true),
        _ => {
            eprintln!("usage: ling compat mcp-run <scope> <name> <file> <project root> [--offline]");
            return 2;
        }
    };
    let root = PathBuf::from(root);
    let Some(scope) = Scope::parse(scope) else {
        eprintln!("ling compat mcp-run: {scope} is not a scope");
        return 2;
    };
    let file = PathBuf::from(file);
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        eprintln!("ling compat mcp-run: no CODEX_HOME");
        return 1;
    };
    let definition = std::fs::read_to_string(&file)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|config| match scope {
            Scope::Project | Scope::User => config.get("mcpServers")?.get(name).cloned(),
            Scope::Local => config
                .get("projects")?
                .as_object()?
                .iter()
                .filter(|(path, _)| same_path(Path::new(path), &root))
                .find_map(|(_, entry)| entry.get("mcpServers")?.get(name).cloned()),
        });
    let Some(definition) = definition else {
        eprintln!("MCP server {name}: its definition is no longer in {}", file.display());
        return 1;
    };
    let server = Server { name: name.clone(), scope, source: file, definition };
    if !approved(&codex_home).contains(&definition_hash(&server)) {
        eprintln!("MCP server {name}: its definition changed since you approved it; run `ling compat mcp approve {name}`");
        return 1;
    }
    let env = |var: &str| std::env::var(var).ok();
    let expanded = (|| -> Result<(String, Vec<String>, Vec<(String, String)>), String> {
        let command = expand(server.definition["command"].as_str().unwrap_or_default(), &env)?;
        let mut arguments = Vec::new();
        for arg in server.definition.get("args").and_then(Value::as_array).into_iter().flatten() {
            arguments.push(expand(arg.as_str().unwrap_or_default(), &env)?);
        }
        let mut vars = Vec::new();
        for (key, value) in server.definition.get("env").and_then(Value::as_object).into_iter().flatten() {
            vars.push((key.clone(), expand(value.as_str().unwrap_or_default(), &env)?));
        }
        Ok((command, arguments, vars))
    })();
    let (command, arguments, vars) = match expanded {
        Ok(parts) => parts,
        Err(reason) => {
            eprintln!("MCP server {name}: {reason}");
            return 1;
        }
    };
    let mut process = if offline {
        let mut process = std::process::Command::new("bwrap");
        process.args(["--dev-bind", "/", "/", "--unshare-net", "--die-with-parent", "--"]).arg(&command).args(&arguments);
        process
    } else {
        let mut process = std::process::Command::new(&command);
        process.args(&arguments);
        process
    };
    process.envs(vars).current_dir(&root);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let err = process.exec();
        eprintln!("MCP server {name}: could not start {command}: {err}");
        1
    }
    #[cfg(not(unix))]
    {
        match process.status() {
            Ok(status) => status.code().unwrap_or(1),
            Err(err) => {
                eprintln!("MCP server {name}: could not start {command}: {err}");
                1
            }
        }
    }
}

/// `ling compat mcp [list | approve <name>|--all | revoke <name>]`, for the folder it runs in.
pub fn run_cli(args: &[String]) -> i32 {
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        eprintln!("ling compat mcp: no CODEX_HOME");
        return 1;
    };
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let root = crate::compat_tui::project_root(&cwd);
    let trusted = ling_skills::catalog::is_trusted(&codex_home, &root);
    let (servers, notes) = discover(&root, ling_node_locator::home_dir().as_deref(), trusted);
    let approved_hashes = approved(&codex_home);
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        [] | ["list"] => {
            println!("Claude Code's MCP servers for {}:", root.display());
            if servers.is_empty() {
                println!("  none");
            }
            for server in servers.values() {
                let state = if approved_hashes.contains(&definition_hash(server)) { "approved" } else { "waits for approval" };
                println!("  {} ({} scope, {}, {}): {state}", server.name, server.scope.name(), transport(&server.definition), server.source.display());
            }
            for note in notes {
                println!("  ⚠️  {note}");
            }
            0
        }
        ["approve", wanted] => {
            let chosen: Vec<&Server> = servers.values().filter(|server| *wanted == "--all" || server.name == *wanted).collect();
            if chosen.is_empty() {
                eprintln!("ling compat mcp: no server named {wanted} here (`ling compat mcp` lists them)");
                return 2;
            }
            let mut entries = approval_entries(&codex_home);
            for server in &chosen {
                let hash = definition_hash(server);
                entries.retain(|entry| {
                    !(entry["name"] == json!(server.name) && entry["source"] == json!(server.source.display().to_string()))
                });
                entries.push(json!({"name": server.name, "scope": server.scope.name(), "source": server.source.display().to_string(), "hash": hash}));
                println!("Approved {} ({} scope). It starts in the next session in the mode.", server.name, server.scope.name());
            }
            match write_approvals(&codex_home, &entries) {
                Ok(()) => 0,
                Err(err) => {
                    eprintln!("ling compat mcp: could not write {}: {err}", approvals_file(&codex_home).display());
                    1
                }
            }
        }
        ["revoke", wanted] => {
            let mut entries = approval_entries(&codex_home);
            let before = entries.len();
            entries.retain(|entry| entry["name"].as_str() != Some(wanted));
            println!("Revoked {} approval(s) of {wanted}.", before - entries.len());
            write_approvals(&codex_home, &entries).map_or(1, |()| 0)
        }
        _ => {
            eprintln!("usage: ling compat mcp [list | approve <name>|--all | revoke <name>]");
            2
        }
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
            let root = std::env::temp_dir().join(format!("mightling-compat-mcp-{tag}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(root.join("home")).unwrap();
            std::fs::create_dir_all(root.join("repo/.git")).unwrap();
            Fixture { root }
        }
        fn write(&self, path: &str, text: &str) {
            std::fs::write(self.root.join(path), text).unwrap();
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn server(definition: Value) -> Server {
        Server { name: "s".into(), scope: Scope::Project, source: PathBuf::from("/r/.mcp.json"), definition }
    }

    #[test]
    fn the_local_scope_wins_then_the_project_then_the_user_and_a_project_waits_for_trust() {
        let f = Fixture::new("scopes");
        let repo = f.root.join("repo");
        f.write(
            "home/.claude.json",
            &json!({
                "mcpServers": {"shared": {"command": "user-shared"}, "mine": {"command": "user-mine"}},
                "projects": {repo.display().to_string(): {"mcpServers": {"shared": {"command": "local-shared"}}}},
            })
            .to_string(),
        );
        f.write("repo/.mcp.json", r#"{"mcpServers": {"shared": {"command": "project-shared"}, "team": {"type": "http", "url": "https://x"}}}"#);
        let home = f.root.join("home");
        let (servers, notes) = discover(&repo, Some(&home), false);
        assert_eq!(servers["shared"].definition["command"], "local-shared");
        assert!(!servers.contains_key("team"));
        assert!(notes[0].contains("wait until this folder is trusted"));
        let (servers, _) = discover(&repo, Some(&home), true);
        assert_eq!(servers["shared"].scope, Scope::Local);
        assert_eq!(servers["team"].scope, Scope::Project);
        assert_eq!(servers["mine"].scope, Scope::User);
    }

    #[test]
    fn nothing_starts_without_approval_and_a_changed_definition_waits_again() {
        let s = server(json!({"command": "srv"}));
        let exe = Path::new("/opt/ling");
        let mut servers = BTreeMap::new();
        servers.insert("s".to_string(), s.clone());
        let none = plan(&servers, &[], exe, Path::new("/r"), false, true);
        assert!(none.settings.is_empty() && none.notes[0].contains("ling compat mcp approve s"));
        let ok = plan(&servers, &[definition_hash(&s)], exe, Path::new("/r"), false, true);
        assert_eq!(ok.offered, ["s"]);
        let changed = server(json!({"command": "srv", "args": ["--evil"]}));
        assert_ne!(definition_hash(&changed), definition_hash(&s));
        // Key order in the file does not change the hash.
        let reordered = server(serde_json::from_str(r#"{"args": [], "command": "srv"}"#).unwrap());
        let same = server(serde_json::from_str(r#"{"command": "srv", "args": []}"#).unwrap());
        assert_eq!(definition_hash(&reordered), definition_hash(&same));
    }

    #[test]
    fn a_stdio_server_runs_through_the_wrapper_with_its_variables_forwarded_by_name() {
        let s = server(json!({"command": "${TOOLS:-/usr/bin}/srv", "args": ["--key", "${API_KEY}"], "env": {"TOKEN": "${SECRET}"}, "timeout": 30000}));
        let setting = setting(&s, Path::new("/opt/ling"), Path::new("/r"), false, true).unwrap();
        let table: toml::Table = toml::from_str(&setting).unwrap();
        let entry = &table["mcp_servers"]["s"];
        assert_eq!(entry["command"].as_str(), Some("/opt/ling"));
        let args: Vec<&str> = entry["args"].as_array().unwrap().iter().filter_map(toml::Value::as_str).collect();
        assert_eq!(args, ["compat", "mcp-run", "project", "s", "/r/.mcp.json", "/r"]);
        let vars: Vec<&str> = entry["env_vars"].as_array().unwrap().iter().filter_map(toml::Value::as_str).collect();
        assert!(vars.contains(&"TOOLS") && vars.contains(&"API_KEY") && vars.contains(&"SECRET"));
        assert_eq!(entry["tool_timeout_sec"].as_integer(), Some(30));
        assert!(!setting.contains("secret-value"));
        // At /airgapped on the server runs without network, or not at all without bwrap.
        let offline = super::setting(&s, Path::new("/opt/ling"), Path::new("/r"), true, true).unwrap();
        assert!(offline.contains("\"--offline\""));
        assert!(super::setting(&s, Path::new("/opt/ling"), Path::new("/r"), true, false).is_err());
    }

    #[test]
    fn an_http_server_passes_its_secrets_by_variable_and_is_refused_at_airgapped_on() {
        let s = server(json!({"type": "http", "url": "https://api.example.com/mcp",
            "headers": {"Authorization": "Bearer ${API_TOKEN}", "X-Team": "${TEAM}", "X-Client": "ling"}}));
        let setting = setting(&s, Path::new("/opt/ling"), Path::new("/r"), false, true).unwrap();
        let table: toml::Table = toml::from_str(&setting).unwrap();
        let entry = &table["mcp_servers"]["s"];
        assert_eq!(entry["url"].as_str(), Some("https://api.example.com/mcp"));
        assert_eq!(entry["bearer_token_env_var"].as_str(), Some("API_TOKEN"));
        assert_eq!(entry["env_http_headers"]["X-Team"].as_str(), Some("TEAM"));
        assert_eq!(entry["http_headers"]["X-Client"].as_str(), Some("ling"));
        assert!(super::setting(&s, Path::new("/opt/ling"), Path::new("/r"), true, true).unwrap_err().contains("/airgapped on"));
        let mixed = server(json!({"type": "http", "url": "https://x", "headers": {"X-Key": "key=${K}"}}));
        assert!(super::setting(&mixed, Path::new("/opt/ling"), Path::new("/r"), false, true).is_err());
        for kind in ["sse", "ws"] {
            assert!(super::setting(&server(json!({"type": kind, "url": "https://x"})), Path::new("/l"), Path::new("/r"), false, true).is_err());
        }
    }

    #[test]
    fn names_codex_rejects_or_mightling_uses_are_refused() {
        assert!(usable_name("github").is_ok() && usable_name("my-server_2").is_ok());
        for bad in ["", "a.b", "has space", "ling_code", "mightling_gmail", "mightling_x"] {
            assert!(usable_name(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn variables_expand_as_the_documentation_says() {
        let env = |name: &str| match name {
            "HOME" => Some("/h".to_string()),
            "EMPTY" => Some(String::new()),
            _ => None,
        };
        assert_eq!(expand("${HOME}/bin", &env).unwrap(), "/h/bin");
        assert_eq!(expand("${MISSING:-/opt}/x", &env).unwrap(), "/opt/x");
        assert_eq!(expand("${EMPTY:-d}", &env).unwrap(), "d");
        assert!(expand("${MISSING}", &env).is_err());
        assert_eq!(expand("plain", &env).unwrap(), "plain");
        assert_eq!(referenced_vars(&json!({"a": "${X}", "b": ["${Y:-1}", "${X}"]})), ["X", "Y"]);
    }

    #[test]
    fn approvals_round_trip() {
        let f = Fixture::new("approve");
        let home = f.root.join("codex-home");
        let s = server(json!({"command": "srv"}));
        write_approvals(&home, &[json!({"name": "s", "hash": definition_hash(&s)})]).unwrap();
        assert_eq!(approved(&home), [definition_hash(&s)]);
    }
}
