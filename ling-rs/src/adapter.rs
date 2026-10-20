//! Adapters: signed LoRA specialists on the served base, one per session
//! (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §10, §11.1).
//!
//! The model gate lists each verified adapter in `/v1/models` after the base, as
//! `<base>+<name>` with a `parent` naming the base and the manifest's fields under
//! `mightling_adapter`. The launcher writes one catalog entry per listed id (lib.rs) and names the
//! session's choice with `--model`; the gate rewrites the id for the engine. Nothing here installs,
//! loads or downloads an adapter: that is `ling-admin adapter` on the node.
//!
//! The session's adapter, first match wins: `ling --adapter <name>` (removed from the arguments
//! before Codex parses them), then for `resume` and `fork` the thread's own record
//! (`$CODEX_HOME/adapter/<thread-id>`, written by `/adapter`), then `DREAMFERENCE_MIGHTLING_ADAPTER`,
//! then `mightling_adapter` in the Dreamference TOML file, then the base. Unlike cave mode's tiers,
//! a tier that is set but empty (or `off`) means the base and stops there: unattended callers set
//! the variable to `""` so that a user's `mightling_adapter` default never reaches a benchmark run
//! (§10.4), and `/adapter off` writes `off` so that a resumed thread stays on the base.
//!
//! The record is the launcher's because Codex's own restore never runs under `ling`: `resume`
//! restores the model saved with the thread only when no model or provider is overridden
//! (`resume_model_settings_for_overrides` in codex-rs/tui/src/app/config_persistence.rs), and the
//! launcher always passes `--model`, `--oss` and `-c model_provider=…`, so a resume takes the
//! `--model` it is given (`ResumeModelSettings::OverrideFromCurrentConfig`). The thread's id is
//! known at launch only for `ling resume <id>` and `ling fork <id>`; `resume --last` and the picker
//! start from the variable and the TOML file.
//!
//! A name `/v1/models` does not list falls back to the base with a notice at start, never an error
//! that stops the session; mid-session the gate refuses a removed adapter instead (§10.3).

use std::ffi::OsString;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;
use std::time::SystemTime;

use clap::Command;
use serde_json::Value;

/// The environment variable that names the adapter new sessions start with; empty means the base.
pub const ENV_VAR: &str = "DREAMFERENCE_MIGHTLING_ADAPTER";

/// The same, in the Dreamference TOML file.
pub const TOML_KEY: &str = "mightling_adapter";

/// The launcher's flag, never seen by Codex.
pub const FLAG: &str = "--adapter";

/// The word for the base, in the flag, the variable, the record and `/adapter off`.
pub const OFF: &str = "off";

/// The `trust` of an adapter not signed by Dreamference.
pub const THIRD_PARTY: &str = "third-party";

const SESSION_DIR: &str = "adapter";
const SESSION_FILE_MAX_AGE: Duration = Duration::from_secs(30 * 24 * 60 * 60);
const USAGE: &str = "Usage: /adapter, /adapter <name> or /adapter off";
const THIRD_PARTY_WARNING: &str =
    "Third-party adapter: not signed by Dreamference; what it answers is its publisher's, not Mightling's.";

/// An adapter the gate lists for the served base.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Adapter {
    pub name: String,
    pub version: String,
    pub display_name: String,
    pub description: String,
    pub third_party: bool,
}

impl Adapter {
    /// The model id a session on this adapter names: `<base>+<name>`.
    pub fn model_id(&self, base: &str) -> String {
        format!("{base}+{}", self.name)
    }
}

/// Whether `name` is an installed adapter's name: `[a-z0-9][a-z0-9-]{0,39}`.
pub fn valid_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 40
        && (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
        && bytes.iter().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'-')
}

/// The adapters a `/v1/models` body lists for `base`: entries whose `parent` is the base and whose
/// id is `<base>+<name>`, in the order listed.
pub fn listed(body: &Value, base: &str) -> Vec<Adapter> {
    let Some(entries) = body.get("data").and_then(Value::as_array) else { return Vec::new() };
    let text = |value: Option<&Value>| value.and_then(Value::as_str).unwrap_or("").to_string();
    entries
        .iter()
        .filter(|entry| entry.get("parent").and_then(Value::as_str) == Some(base))
        .filter_map(|entry| {
            let name = entry.get("id")?.as_str()?.strip_prefix(base)?.strip_prefix('+')?;
            let manifest = entry.get("mightling_adapter");
            let field = |key: &str| manifest.and_then(|manifest| manifest.get(key));
            if !valid_name(name) || field("name").and_then(Value::as_str).is_some_and(|listed| listed != name) {
                return None;
            }
            let display_name = Some(text(field("display_name"))).filter(|shown| !shown.trim().is_empty());
            Some(Adapter {
                name: name.to_string(),
                version: text(field("version")),
                display_name: display_name.unwrap_or_else(|| title(name)),
                description: text(field("description")),
                third_party: field("trust").and_then(Value::as_str) == Some(THIRD_PARTY),
            })
        })
        .collect()
}

/// The name with its first letter capitalised: what a notice calls an adapter it has no manifest for.
fn title(name: &str) -> String {
    let mut chars = name.chars();
    chars.next().map(|first| first.to_uppercase().chain(chars).collect()).unwrap_or_default()
}

/// The base's short name for an adapter's display name: the repository's last segment without its
/// quantisation suffixes, with spaces for dashes (`RadixArk/Qwen3.8-27B-NVFP4` is `Qwen3.8 27B`).
pub fn base_label(base: &str) -> String {
    const SUFFIXES: &[&str] = &["nvfp4", "fp4", "fp8", "int4", "int8", "awq", "gptq", "bf16", "fp16", "gguf", "mlx"];
    let mut words: Vec<&str> = base.rsplit('/').next().unwrap_or(base).split('-').collect();
    while words.len() > 1 && words.last().is_some_and(|word| SUFFIXES.contains(&word.to_ascii_lowercase().as_str())) {
        words.pop();
    }
    words.join(" ")
}

/// The display name of an adapter's catalog entry: "Qwen3.8 27B · Rust", with " (third-party)"
/// at the end for an adapter not signed by Dreamference.
pub fn display_name(base: &str, adapter: &Adapter) -> String {
    let flag = if adapter.third_party { " (third-party)" } else { "" };
    format!("{} · {}{flag}", base_label(base), adapter.display_name)
}

/// The catalog entry for `adapter`: the base's entry, with the adapter's id, name and description.
pub fn catalog_entry(base_entry: &Value, base: &str, adapter: &Adapter) -> Value {
    let mut entry = base_entry.clone();
    let id = adapter.model_id(base);
    entry["id"] = Value::from(id.clone());
    entry["slug"] = Value::from(id);
    entry["display_name"] = Value::from(display_name(base, adapter));
    if !adapter.description.trim().is_empty() {
        entry["description"] = Value::from(adapter.description.trim());
    }
    entry
}

/// The command line without `--adapter <name>` and `--adapter=<name>`, which Codex does not know,
/// and the last value given. Nothing after a `--` is touched.
pub fn without_flag(args: Vec<OsString>) -> (Vec<OsString>, Option<String>) {
    let mut out = Vec::with_capacity(args.len());
    let mut found = None;
    let mut args = args.into_iter().enumerate().peekable();
    while let Some((index, arg)) = args.next() {
        if index > 0 {
            match arg.to_str() {
                Some("--") => {
                    out.push(arg);
                    out.extend(args.map(|(_, rest)| rest));
                    break;
                }
                Some(FLAG) => {
                    if args.peek().is_some_and(|(_, next)| next != "--") {
                        found = args.next().map(|(_, value)| value.to_string_lossy().into_owned());
                    }
                    continue;
                }
                Some(other) => {
                    if let Some(value) = other.strip_prefix(FLAG).and_then(|rest| rest.strip_prefix('=')) {
                        found = Some(value.to_string());
                        continue;
                    }
                }
                None => {}
            }
        }
        out.push(arg);
    }
    (out, found)
}

/// Where the session's adapter came from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    Flag,
    Thread,
    Environment,
    ConfigFile(PathBuf),
    Base,
}

/// The session's adapter: a name, or `None` for the base.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Choice {
    pub name: Option<String>,
    pub source: Source,
}

/// Resolves the adapter from already-read tiers; [`resolve`] supplies the real ones. A tier that
/// is present decides, so an empty value or `off` is the base, not a pass to the next tier.
pub fn resolve_from(
    flag: Option<&str>,
    thread: Option<&str>,
    environment: Option<&str>,
    config: Option<(&Path, &str)>,
) -> Choice {
    let decide = |value: &str, source: Source| {
        let value = value.trim().to_ascii_lowercase();
        let name = (!value.is_empty() && value != OFF).then_some(value);
        Choice { name, source }
    };
    if let Some(value) = flag {
        return decide(value, Source::Flag);
    }
    if let Some(value) = thread {
        return decide(value, Source::Thread);
    }
    if let Some(value) = environment {
        return decide(value, Source::Environment);
    }
    if let Some((path, text)) = config
        && let Some(value) = toml::from_str::<toml::Table>(text).ok().and_then(|table| table.get(TOML_KEY)?.as_str().map(str::to_string))
    {
        return decide(&value, Source::ConfigFile(path.to_path_buf()));
    }
    Choice { name: None, source: Source::Base }
}

/// Resolves the session's adapter, reading every tier. `thread_id` is the thread a `resume` or
/// `fork` names ([`resumed_thread`]).
pub fn resolve(flag: Option<&str>, thread_id: Option<&str>) -> Choice {
    let thread = thread_id.and_then(read_record);
    let environment = std::env::var_os(ENV_VAR).map(|value| value.to_string_lossy().into_owned());
    let config = crate::config_file().and_then(|path| std::fs::read_to_string(&path).ok().map(|text| (path, text)));
    resolve_from(
        flag,
        thread.as_deref(),
        environment.as_deref(),
        config.as_ref().map(|(path, text)| (path.as_path(), text.as_str())),
    )
}

/// The model id the session names, and the notice to give when its adapter is not listed.
pub fn session_model(choice: &Choice, base: &str, adapters: &[Adapter]) -> (String, Option<String>) {
    let Some(name) = choice.name.as_deref() else { return (base.to_string(), None) };
    match adapters.iter().find(|adapter| adapter.name == name) {
        Some(adapter) => (adapter.model_id(base), None),
        None => (
            base.to_string(),
            Some(format!("The {} adapter is not on this node; this session uses the base model.", title(name))),
        ),
    }
}

/// The thread a `ling resume <id>` or `ling fork <id>` names, found with Codex's own definition of
/// the subcommand's options. `None` for `--last`, the picker and every other command.
pub fn resumed_thread(command: &Command, user_args: &[String], subcommand: Option<usize>) -> Option<String> {
    let index = subcommand?;
    let name = user_args.get(index)?;
    if !matches!(name.as_str(), "resume" | "fork") {
        return None;
    }
    let found = command.find_subcommand(name)?;
    let rest = &user_args[index + 1..];
    crate::first_positional(found, rest).map(|position| rest[position].clone())
}

fn session_dir() -> Option<PathBuf> {
    let home = codex_utils_home_dir::find_codex_home().ok()?;
    Some(home.as_path().join(SESSION_DIR))
}

/// This thread's record. Thread ids are UUIDs; anything else is refused rather than joined.
fn session_file(thread_id: &str) -> Option<PathBuf> {
    if thread_id.is_empty() || !thread_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return None;
    }
    Some(session_dir()?.join(thread_id))
}

fn read_record(thread_id: &str) -> Option<String> {
    std::fs::read_to_string(session_file(thread_id)?).ok()
}

/// Records the thread's adapter (`None` for the base, written as `off`) so a `resume` keeps it.
pub fn write_record(thread_id: &str, name: Option<&str>) -> std::io::Result<()> {
    let Some(path) = session_file(thread_id) else {
        return Err(std::io::Error::new(std::io::ErrorKind::InvalidInput, "not a thread id"));
    };
    write_record_at(&path, name)
}

fn write_record_at(path: &Path, name: Option<&str>) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, format!("{}\n", name.unwrap_or(OFF)))
}

/// Deletes records older than thirty days. Called at launch; errors are ignored.
pub fn prune_session_files() {
    let Some(dir) = session_dir() else { return };
    let Ok(entries) = std::fs::read_dir(&dir) else { return };
    let now = SystemTime::now();
    for entry in entries.flatten() {
        let stale = entry
            .metadata()
            .and_then(|meta| meta.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age > SESSION_FILE_MAX_AGE);
        if stale {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// What `/adapter` decided: the lines to print, the model to switch this thread to, and the record
/// to write (`Some(None)` is the base).
#[derive(Debug, PartialEq, Eq)]
pub struct Outcome {
    pub lines: Vec<String>,
    pub model: Option<String>,
    pub record: Option<Option<String>>,
}

/// One listed adapter as `/adapter` reads it back from the catalog.
struct Entry {
    name: String,
    id: String,
    display_name: String,
    description: String,
}

/// The base and the adapters in a catalog the launcher wrote: the base is the first entry, an
/// adapter every later one whose id is `<base>+<name>`.
fn catalog_entries(catalog: &Value) -> Option<(String, Vec<Entry>)> {
    let models = catalog.get("models")?.as_array()?;
    let base = models.first()?.get("id")?.as_str()?.to_string();
    let prefix = format!("{base}+");
    let field = |model: &Value, key: &str| model.get(key).and_then(Value::as_str).unwrap_or("").to_string();
    let adapters = models[1..]
        .iter()
        .filter_map(|model| {
            let id = model.get("id")?.as_str()?;
            let name = id.strip_prefix(&prefix).filter(|name| valid_name(name))?;
            Some(Entry { name: name.to_string(), id: id.to_string(), display_name: field(model, "display_name"), description: field(model, "description") })
        })
        .collect();
    Some((base, adapters))
}

fn is_third_party(entry: &Entry) -> bool {
    entry.display_name.ends_with(" (third-party)")
}

/// `/adapter [args]` against a catalog: `current` is the thread's model now.
pub fn decide(catalog: &Value, current: &str, args: &str) -> Outcome {
    let say = |lines: Vec<String>| Outcome { lines, model: None, record: None };
    let Some((base, adapters)) = catalog_entries(catalog) else {
        return say(vec!["No model catalog yet: the launcher writes it when a session starts.".to_string()]);
    };
    let words: Vec<&str> = args.split_whitespace().collect();
    match words.as_slice() {
        [] => say(status_lines(&base, &adapters, current)),
        [word] if word.eq_ignore_ascii_case(OFF) => {
            let lines = if current == base {
                vec![format!("Adapter: none; this session is on the base model, {base}.")]
            } else {
                vec![format!("Adapter: off. This session is on the base model, {base}, from the next model request.")]
            };
            Outcome { lines, model: (current != base).then(|| base.clone()), record: Some(None) }
        }
        [word] => {
            let name = word.to_ascii_lowercase();
            let Some(entry) = adapters.iter().find(|entry| entry.name == name) else {
                if !valid_name(&name) {
                    return say(vec![USAGE.to_string()]);
                }
                return say(vec![
                    format!("The {} adapter is not installed on this node. On the node, run:", title(&name)),
                    format!("  ling-admin adapter install {name}"),
                ]);
            };
            let mut lines = if entry.id == current {
                vec![format!("Adapter: {} is already this session's.", entry.name)]
            } else {
                vec![format!("Adapter: {} ({}) for this session, from the next model request.", entry.name, entry.display_name)]
            };
            if is_third_party(entry) {
                lines.push(THIRD_PARTY_WARNING.to_string());
            }
            Outcome { lines, model: (entry.id != current).then(|| entry.id.clone()), record: Some(Some(entry.name.clone())) }
        }
        _ => say(vec![USAGE.to_string()]),
    }
}

fn status_lines(base: &str, adapters: &[Entry], current: &str) -> Vec<String> {
    let marker = |id: &str| if id == current { "   ← this session" } else { "" };
    let mut lines = vec![format!("Base model: {base}{}", marker(base))];
    if adapters.is_empty() {
        lines.push("No adapters are installed for it on this node. On the node: ling-admin adapter list --available".to_string());
        return lines;
    }
    lines.push("Adapters:".to_string());
    for entry in adapters {
        let description = if entry.description.is_empty() { String::new() } else { format!(" — {}", entry.description) };
        lines.push(format!("  {:<12} {}{description}{}", entry.name, entry.display_name, marker(&entry.id)));
    }
    if adapters.iter().any(is_third_party) {
        lines.push("(third-party) adapters are not signed by Dreamference.".to_string());
    }
    lines.push("Switch with /adapter <name>; /adapter off returns to the base.".to_string());
    lines
}

/// Runs `/adapter [args]` for the TUI's hook: the lines to print and the model to switch this
/// thread to. The TUI sends that as `AppEvent::UpdateModel`, Codex's per-thread override, and never
/// as `PersistModelSelection`, which would write the global config. Generic over the thread id so
/// the hook can pass its `Option<ThreadId>` as it is.
pub fn command<T: std::fmt::Display>(thread_id: Option<T>, current: &str, args: &str) -> (Vec<String>, Option<String>) {
    let catalog = codex_utils_home_dir::find_codex_home()
        .ok()
        .and_then(|home| std::fs::read_to_string(home.as_path().join("model_catalog.json")).ok())
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .unwrap_or(Value::Null);
    let mut outcome = decide(&catalog, current, args);
    if let (Some(record), Some(thread_id)) = (outcome.record.as_ref(), thread_id.map(|id| id.to_string()))
        && let Err(err) = write_record(&thread_id, record.as_deref())
    {
        outcome.lines.push(format!("Could not keep the choice for `ling resume`: {err}"));
    }
    (outcome.lines, outcome.model)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const BASE: &str = "RadixArk/Qwen3.8-27B-NVFP4";

    fn adapter_entry(name: &str, trust: &str) -> Value {
        json!({
            "id": format!("{BASE}+{name}"), "object": "model", "created": 1, "owned_by": "sglang",
            "root": BASE, "parent": BASE, "max_model_len": 131072,
            "mightling_adapter": {"name": name, "version": "1.0.0", "display_name": title(name),
                                  "description": format!("{name} specialist."), "trust": trust},
        })
    }

    fn models_body() -> Value {
        json!({"object": "list", "data": [
            {"id": BASE, "object": "model", "owned_by": "sglang", "max_model_len": 131072},
            adapter_entry("rust", "dreamference"),
            adapter_entry("acme", "third-party"),
            // Not for this base, a bad name, and an id that does not match its manifest: skipped.
            {"id": "other+go", "parent": "other"},
            {"id": format!("{BASE}+Bad_Name"), "parent": BASE},
            {"id": format!("{BASE}+go"), "parent": BASE, "mightling_adapter": {"name": "python"}},
        ]})
    }

    fn os(args: &[&str]) -> Vec<OsString> {
        args.iter().map(OsString::from).collect()
    }

    #[test]
    fn names_follow_the_contract() {
        assert!(valid_name("rust") && valid_name("0day") && valid_name("go-web"));
        assert!(valid_name(&"a".repeat(40)) && !valid_name(&"a".repeat(41)));
        for bad in ["", "-x", "Rust", "a_b", "a:b", "a+b", "a/b", "../x"] {
            assert!(!valid_name(bad), "{bad}");
        }
    }

    #[test]
    fn only_adapters_of_the_served_base_are_listed() {
        let adapters = listed(&models_body(), BASE);
        assert_eq!(adapters.iter().map(|a| a.name.as_str()).collect::<Vec<_>>(), vec!["rust", "acme"]);
        assert_eq!(adapters[0].display_name, "Rust");
        assert_eq!(adapters[0].version, "1.0.0");
        assert!(!adapters[0].third_party && adapters[1].third_party);
        assert!(listed(&models_body(), "other-base").is_empty());
        assert!(listed(&json!({"data": [{"id": BASE}]}), BASE).is_empty());
    }

    #[test]
    fn display_names_name_the_base_and_flag_third_party_ones() {
        assert_eq!(base_label(BASE), "Qwen3.8 27B");
        assert_eq!(base_label("Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8"), "Qwen3 Coder 30B A3B Instruct");
        assert_eq!(base_label("m"), "m");
        let adapters = listed(&models_body(), BASE);
        assert_eq!(display_name(BASE, &adapters[0]), "Qwen3.8 27B · Rust");
        assert_eq!(display_name(BASE, &adapters[1]), "Qwen3.8 27B · Acme (third-party)");
    }

    #[test]
    fn the_flag_is_removed_and_its_last_value_kept() {
        let (args, found) = without_flag(os(&["ling", "--adapter", "rust", "-a", "on-request", "fix it"]));
        assert_eq!(args, os(&["ling", "-a", "on-request", "fix it"]));
        assert_eq!(found.as_deref(), Some("rust"));
        let (args, found) = without_flag(os(&["ling", "resume", "019a", "--adapter=go", "--adapter", "off"]));
        assert_eq!(args, os(&["ling", "resume", "019a"]));
        assert_eq!(found.as_deref(), Some("off"));
        // After `--` it is the prompt's.
        let (args, found) = without_flag(os(&["ling", "exec", "--", "--adapter", "rust"]));
        assert_eq!(args, os(&["ling", "exec", "--", "--adapter", "rust"]));
        assert_eq!(found, None);
        // A dangling flag is dropped without taking anything with it.
        let (args, found) = without_flag(os(&["ling", "--adapter"]));
        assert_eq!((args, found), (os(&["ling"]), None));
        assert_eq!(without_flag(os(&["ling", "exec", "hi"])), (os(&["ling", "exec", "hi"]), None));
    }

    #[test]
    fn each_tier_shadows_the_next() {
        let path = Path::new("/tmp/dreamference.toml");
        let toml = "mightling_adapter = \"go\"\n";
        let name = |choice: Choice| choice.name;
        assert_eq!(resolve_from(Some("rust"), Some("python\n"), Some("c"), Some((path, toml))).source, Source::Flag);
        assert_eq!(name(resolve_from(Some("Rust "), Some("python\n"), Some("c"), Some((path, toml)))).as_deref(), Some("rust"));
        assert_eq!(name(resolve_from(None, Some("python\n"), Some("c"), Some((path, toml)))).as_deref(), Some("python"));
        assert_eq!(name(resolve_from(None, None, Some("c"), Some((path, toml)))).as_deref(), Some("c"));
        let from_file = resolve_from(None, None, None, Some((path, toml)));
        assert_eq!(from_file, Choice { name: Some("go".into()), source: Source::ConfigFile(path.to_path_buf()) });
        assert_eq!(resolve_from(None, None, None, Some((path, "vllm_host = \"x\"\n"))), Choice { name: None, source: Source::Base });
        assert_eq!(resolve_from(None, None, None, None).source, Source::Base);
    }

    #[test]
    fn a_tier_set_to_empty_or_off_is_the_base_and_stops_there() {
        let path = Path::new("/tmp/dreamference.toml");
        let toml = "mightling_adapter = \"rust\"\n";
        // An unattended caller's empty variable never lets the user's default through (§10.4).
        assert_eq!(resolve_from(None, None, Some(""), Some((path, toml))), Choice { name: None, source: Source::Environment });
        // `/adapter off` keeps a resumed thread on the base whatever the default says.
        assert_eq!(resolve_from(None, Some("off\n"), Some("go"), Some((path, toml))), Choice { name: None, source: Source::Thread });
        assert_eq!(resolve_from(Some("off"), Some("rust\n"), None, None), Choice { name: None, source: Source::Flag });
        assert_eq!(resolve_from(None, None, None, Some((path, "mightling_adapter = \"\"\n"))).name, None);
    }

    #[test]
    fn a_resumed_thread_keeps_its_adapter() {
        let dir = std::env::temp_dir().join(format!("ling-adapter-test-{}", std::process::id()));
        let file = dir.join("019a-thread");
        write_record_at(&file, Some("rust")).unwrap();
        let record = std::fs::read_to_string(&file).unwrap();
        let path = Path::new("/tmp/dreamference.toml");
        let choice = resolve_from(None, Some(&record), Some("go"), Some((path, "mightling_adapter = \"go\"\n")));
        assert_eq!(choice, Choice { name: Some("rust".into()), source: Source::Thread });
        assert_eq!(session_model(&choice, BASE, &listed(&models_body(), BASE)), (format!("{BASE}+rust"), None));
        write_record_at(&file, None).unwrap();
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "off\n");
        let _ = std::fs::remove_dir_all(&dir);
        assert!(session_file("../../etc/passwd").is_none());
        assert!(session_file("").is_none());
    }

    #[test]
    fn the_thread_of_a_resume_or_fork_is_found_past_its_options() {
        let command = Command::new("ling")
            .arg(clap::Arg::new("model").short('m').long("model").num_args(1))
            .subcommand(
                Command::new("resume")
                    .arg(clap::Arg::new("config").short('c').num_args(1))
                    .arg(clap::Arg::new("last").long("last").action(clap::ArgAction::SetTrue))
                    .arg(clap::Arg::new("id")),
            )
            .subcommand(Command::new("fork").arg(clap::Arg::new("id")))
            .subcommand(Command::new("exec").arg(clap::Arg::new("prompt")));
        let user = |args: &[&str]| args.iter().map(|arg| arg.to_string()).collect::<Vec<_>>();
        let found = |args: &[&str]| {
            let args = user(args);
            let subcommand = crate::first_positional(&command, &args);
            resumed_thread(&command, &args, subcommand)
        };
        assert_eq!(found(&["resume", "-c", "x=1", "019a-b"]).as_deref(), Some("019a-b"));
        assert_eq!(found(&["-m", "m", "fork", "019c"]).as_deref(), Some("019c"));
        assert_eq!(found(&["resume", "--last"]), None);
        assert_eq!(found(&["resume"]), None);
        assert_eq!(found(&["exec", "019a"]), None);
        assert_eq!(found(&[]), None);
    }

    #[test]
    fn an_unlisted_adapter_falls_back_to_the_base_with_the_notice() {
        let adapters = listed(&models_body(), BASE);
        let choice = |name: &str| Choice { name: Some(name.into()), source: Source::Environment };
        assert_eq!(session_model(&choice("rust"), BASE, &adapters), (format!("{BASE}+rust"), None));
        assert_eq!(
            session_model(&choice("rust"), BASE, &[]),
            (BASE.to_string(), Some("The Rust adapter is not on this node; this session uses the base model.".to_string()))
        );
        assert_eq!(session_model(&Choice { name: None, source: Source::Base }, BASE, &[]), (BASE.to_string(), None));
    }

    fn catalog() -> Value {
        let base = json!({"id": BASE, "slug": BASE, "display_name": BASE, "max_context_window": 131072});
        let adapters = listed(&models_body(), BASE);
        let mut models = vec![base.clone()];
        models.extend(adapters.iter().map(|adapter| catalog_entry(&base, BASE, adapter)));
        json!({"models": models})
    }

    #[test]
    fn the_catalog_entry_is_the_bases_with_the_adapters_names() {
        let entry = &catalog()["models"][1];
        assert_eq!(entry["id"], json!(format!("{BASE}+rust")));
        assert_eq!(entry["slug"], entry["id"]);
        assert_eq!(entry["display_name"], json!("Qwen3.8 27B · Rust"));
        assert_eq!(entry["description"], json!("rust specialist."));
        assert_eq!(entry["max_context_window"], json!(131072));
    }

    #[test]
    fn adapter_alone_lists_them_and_marks_the_sessions() {
        let outcome = decide(&catalog(), &format!("{BASE}+rust"), "");
        assert_eq!((outcome.model.clone(), outcome.record.clone()), (None, None));
        let lines = outcome.lines;
        assert_eq!(lines[0], format!("Base model: {BASE}"));
        assert_eq!(lines.iter().filter(|line| line.contains("← this session")).count(), 1);
        assert!(lines.iter().any(|line| line.contains("rust") && line.contains("← this session")));
        assert!(lines.iter().any(|line| line.contains("Acme (third-party)")));
        assert!(lines.iter().all(|line| !line.contains("Codex")));
        let only_base = json!({"models": [{"id": BASE}]});
        assert!(decide(&only_base, BASE, "").lines[1].starts_with("No adapters are installed"));
    }

    #[test]
    fn adapter_name_switches_this_thread_and_records_it() {
        let outcome = decide(&catalog(), BASE, "rust");
        assert_eq!(outcome.model, Some(format!("{BASE}+rust")));
        assert_eq!(outcome.record, Some(Some("rust".to_string())));
        let third = decide(&catalog(), BASE, "acme");
        assert!(third.lines.iter().any(|line| line.starts_with("Third-party adapter")));
        // Already on it: recorded, no switch.
        assert_eq!(decide(&catalog(), &format!("{BASE}+rust"), "rust").model, None);
        let off = decide(&catalog(), &format!("{BASE}+rust"), "off");
        assert_eq!((off.model, off.record), (Some(BASE.to_string()), Some(None)));
        assert_eq!(decide(&catalog(), BASE, "OFF").model, None);
    }

    #[test]
    fn adapter_never_installs_and_names_the_command_to_run_on_the_node() {
        let outcome = decide(&catalog(), BASE, "go");
        assert_eq!((outcome.model, outcome.record), (None, None));
        assert_eq!(outcome.lines[1], "  ling-admin adapter install go");
        assert_eq!(decide(&catalog(), BASE, "rust now").lines, vec![USAGE.to_string()]);
        assert_eq!(decide(&catalog(), BASE, "../x").lines, vec![USAGE.to_string()]);
        assert!(decide(&Value::Null, BASE, "rust").lines[0].starts_with("No model catalog yet"));
    }
}
