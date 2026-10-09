//! When a session compacts, and what it is handed afterwards
//! (specs/DREAMFERENCE_MIGHTLING_COMPACTION.md §4.2 and §10.1).
//!
//! Two things, both done at every launch and neither with a Codex patch:
//!
//! 1. **The limit follows the KV pool.** The catalog tells Codex to compact at the model's context
//!    window, which on the default model (262,144 tokens) is larger than everything the server
//!    can hold for all its streams together (about 157,000). A session that grew would exhaust
//!    the pool long before it compacted. The launcher reads the pool from the server's
//!    `/metrics` and passes `-c model_auto_compact_token_limit=<60% of it>`, unless the user set
//!    that key themselves.
//! 2. **The ledger hook.** `ling ledger` (ledger.rs) is registered in `config.toml` as a
//!    `SessionStart` hook with matcher `compact`, together with the hash that marks it trusted:
//!    Codex skips a hook nobody trusted, silently. The hook is this binary and nothing else.

use std::ffi::OsString;
use std::path::Path;
use std::time::Duration;

use anyhow::Context;
use serde_json::json;
use sha2::Digest;
use sha2::Sha256;
use toml_edit::ArrayOfTables;
use toml_edit::DocumentMut;
use toml_edit::Item;
use toml_edit::Table;
use toml_edit::value;

use crate::ServedModel;

/// The share of the KV pool one session may fill before it compacts. The rest is room for a
/// second stream (a subagent, another terminal) and for the compaction request itself, which
/// sends the whole history once more.
pub const POOL_SHARE_PERCENT: u64 = 60;

/// Codex's config key for the compaction limit; a `-c` of it overrides the model catalog.
pub const LIMIT_KEY: &str = "model_auto_compact_token_limit";

/// Whether the ledger hook is registered when nothing says otherwise. On since 2026-10-02: Phase 0
/// (specs/DREAMFERENCE_MIGHTLING_COMPACTION.md §11.2) found it halved compactions and commands.
pub const LEDGER_DEFAULT: bool = true;

/// A hook the launcher registers in `config.toml`: `<this binary> <subcommand>` for `event`,
/// under `matcher`, waited for `timeout` seconds. The trust hash covers every field. The ledger
/// and the notice are `SessionStart` hooks; refine mode's is a `UserPromptSubmit` one (refine.rs).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SessionHook {
    /// The command's last word, which is also how the launcher recognises its own group.
    pub subcommand: &'static str,
    /// Empty for an event Codex matches nothing against (`UserPromptSubmit`): no `matcher` key.
    pub matcher: &'static str,
    pub timeout: i64,
    /// The event's table under `[hooks]`: `SessionStart`, `UserPromptSubmit`.
    pub event: &'static str,
    /// `statusMessage`, shown while the hook runs; empty for none.
    pub status_message: &'static str,
    /// `additionalContextLimit`, the tokens of context above which Codex moves it to a file; `0`
    /// never does. `None` leaves Codex's default (2,500).
    pub context_limit: Option<i64>,
}

impl SessionHook {
    /// A `SessionStart` hook with no status message and Codex's context limit.
    pub const fn session_start(subcommand: &'static str, matcher: &'static str, timeout: i64) -> SessionHook {
        SessionHook { subcommand, matcher, timeout, event: "SessionStart", status_message: "", context_limit: None }
    }

    /// The event's name in Codex's trust keys and hashes: `session_start`, `user_prompt_submit`.
    pub fn event_label(&self) -> String {
        let mut label = String::new();
        for (index, c) in self.event.chars().enumerate() {
            if c.is_ascii_uppercase() && index > 0 {
                label.push('_');
            }
            label.push(c.to_ascii_lowercase());
        }
        label
    }
}

/// Codex's own `additionalContextLimit` when none is given; a limit equal to it is left out of
/// the trust hash, as Codex leaves it out.
const DEFAULT_CONTEXT_LIMIT: i64 = 2_500;

/// The ledger (ledger.rs), after each compaction. It reads one file and runs `git status`.
pub const LEDGER_HOOK: SessionHook = SessionHook::session_start("ledger", "compact", 10);

/// Registers the ledger hook and puts the pool-derived limit on the command line. Nothing here
/// may stop a session from starting, so every failure leaves things as they were.
pub async fn prepare(args: Vec<OsString>, codex_home: &Path, host: &str, model: &ServedModel) -> Vec<OsString> {
    let config_path = codex_home.join("config.toml");
    let _ = register_ledger_hook(&config_path, ledger_enabled());
    let config = std::fs::read_to_string(&config_path).unwrap_or_default();
    with_limit(args, limit_for(model.max_model_len, kv_pool(host).await), &config)
}

/// The tokens of KV cache the server has for all its streams, from its `/metrics`: SGLang's
/// `sglang:max_total_num_tokens`, or vLLM's `num_gpu_blocks` times `block_size`. `None` when the
/// endpoint does not answer or names neither.
pub async fn kv_pool(host: &str) -> Option<u64> {
    let client = reqwest::Client::builder().timeout(Duration::from_secs(1)).build().ok()?;
    let response = client
        .get(format!("{}/metrics", host.trim_end_matches('/')))
        .send()
        .await
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    parse_kv_pool(&response.text().await.ok()?)
}

/// Finds the pool in Prometheus text (see [`kv_pool`]).
pub fn parse_kv_pool(metrics: &str) -> Option<u64> {
    for line in metrics.lines() {
        if let Some(rest) = line.strip_prefix("sglang:max_total_num_tokens") {
            let tokens: f64 = rest.rsplit(' ').next()?.trim().parse().ok()?;
            return (tokens >= 1.0).then_some(tokens as u64);
        }
        if line.starts_with("vllm:cache_config_info") {
            let label = |name: &str| -> Option<u64> {
                let start = line.find(&format!("{name}=\""))? + name.len() + 2;
                line[start..].split('"').next()?.parse().ok()
            };
            if let (Some(blocks), Some(size)) = (label("num_gpu_blocks"), label("block_size")) {
                return Some(blocks * size);
            }
        }
    }
    None
}

/// The limit to compact at: [`POOL_SHARE_PERCENT`] of the pool, when that is below the window.
/// `None` leaves the catalog's limit (the window) alone: the pool is unknown, or large enough.
pub fn limit_for(max_model_len: u64, pool: Option<u64>) -> Option<u64> {
    let limit = pool? * POOL_SHARE_PERCENT / 100;
    (limit > 0 && limit < max_model_len).then_some(limit)
}

/// Puts `-c model_auto_compact_token_limit=<limit>` in front of the user's arguments, unless the
/// command line or `config.toml` (whose text is `config`) already sets that key: Night Shift
/// passes its own, smaller limit this way, and it must win.
pub fn with_limit(args: Vec<OsString>, limit: Option<u64>, config: &str) -> Vec<OsString> {
    let Some(limit) = limit else { return args };
    let in_config = config
        .parse::<toml::Table>()
        .is_ok_and(|table| table.contains_key(LIMIT_KEY));
    let words: Vec<String> = args.iter().skip(1).map(|arg| arg.to_string_lossy().into_owned()).collect();
    let sets_key = |assignment: &str| assignment.split('=').next().is_some_and(|key| key.trim() == LIMIT_KEY);
    let on_command_line = words.iter().enumerate().any(|(index, word)| match word.as_str() {
        "-c" | "--config" => words.get(index + 1).is_some_and(|next| sets_key(next)),
        _ => word
            .strip_prefix("--config=")
            .or_else(|| word.strip_prefix("-c").filter(|rest| !rest.is_empty()))
            .is_some_and(sets_key),
    });
    if in_config || on_command_line {
        return args;
    }
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    out.extend(["-c".into(), format!("{LIMIT_KEY}={limit}").into()]);
    out.extend(args);
    out
}

/// Whether the ledger hook is registered: `DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER`, then
/// `mightling_compaction_ledger` in the config file, then [`LEDGER_DEFAULT`].
pub fn ledger_enabled() -> bool {
    if let Ok(setting) = std::env::var("DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER")
        && !setting.is_empty()
    {
        return matches!(setting.to_lowercase().as_str(), "1" | "true" | "yes" | "on");
    }
    crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok()?.get("mightling_compaction_ledger")?.as_bool())
        .unwrap_or(LEDGER_DEFAULT)
}

/// Brings `config.toml` in line with `enabled`: the hook and its trust entry present, or both
/// gone. A `hooks.json` beside it means the user keeps hooks there, and two representations in
/// one folder draw a warning from Codex at every start, so then the file is left as it is.
pub fn register_ledger_hook(config_path: &Path, enabled: bool) -> anyhow::Result<()> {
    register_session_hook(config_path, &LEDGER_HOOK, "", enabled)
}

/// Brings `config.toml` in line with `enabled` for any of the launcher's `SessionStart` hooks;
/// `argument` follows the subcommand on the command line (empty for none). See
/// [`register_ledger_hook`] for the `hooks.json` rule.
pub fn register_session_hook(config_path: &Path, hook: &SessionHook, argument: &str, enabled: bool) -> anyhow::Result<()> {
    let has_hooks_json = config_path
        .parent()
        .is_some_and(|home| home.join("hooks.json").is_file());
    let Ok(exe) = std::env::current_exe() else { return Ok(()) };
    let existing = std::fs::read_to_string(config_path).unwrap_or_default();
    let command = hook_command_for(&exe, hook, argument);
    let updated = with_session_hook(&existing, config_path, &command, hook, enabled && !has_hooks_json)?;
    if updated != existing {
        crate::write_atomically(config_path, updated.as_bytes())?;
    }
    Ok(())
}

/// The ledger hook's command line: this executable, quoted for the shell where it has to be.
pub fn hook_command(exe: &Path) -> String {
    hook_command_for(exe, &LEDGER_HOOK, "")
}

/// A hook's command line: `<exe> <subcommand>[ <argument>]`, the path quoted where it has to be.
/// `argument` must be a plain word (letters, digits, `-`, `_`).
pub fn hook_command_for(exe: &Path, hook: &SessionHook, argument: &str) -> String {
    let program = quote_program(&exe.to_string_lossy(), cfg!(windows));
    match argument {
        "" => format!("{program} {}", hook.subcommand),
        argument => format!("{program} {} {argument}", hook.subcommand),
    }
}

/// The program part of a hook's command line, quoted for the shell Codex runs hooks through.
///
/// A POSIX shell elsewhere: single quotes, only when the path is not plain. `cmd.exe` on Windows,
/// which does not understand single quotes: the path always goes in double quotes (a Windows path
/// is never plain, and `C:\Users\Jane Doe` splits at the space). Codex wraps the whole line in one
/// more pair of quotes for `cmd /C` (`hooks/src/engine/command_runner.rs`), so cmd's quote stripping
/// takes those and leaves these. A Windows path cannot contain `"`.
pub fn quote_program(path: &str, windows: bool) -> String {
    if windows {
        return format!("\"{path}\"");
    }
    let plain = path.chars().all(|c| c.is_ascii_alphanumeric() || "/._-+".contains(c));
    if plain { path.to_string() } else { format!("'{}'", path.replace('\'', r"'\''")) }
}

/// The hash Codex compares with `hooks.state.<key>.trusted_hash` before it runs a hook
/// (`codex-rs/hooks/src/engine/discovery.rs`, `hook_hash`): SHA-256 over the canonical JSON of the
/// normalised hook, its keys sorted at every level. Rebuilt here from that definition; a test
/// pins a value Codex itself accepted, so a Codex bump that changes the scheme fails the test
/// rather than silently switching the hook off.
pub fn hook_hash(command: &str) -> String {
    hook_hash_for(command, &LEDGER_HOOK)
}

/// [`hook_hash`] for any of the launcher's hooks: the event, matcher, timeout, status message and
/// context limit are part of the hash. Fields Codex leaves unset (`None`) are absent from its
/// identity, which it serialises as TOML; so are they here.
pub fn hook_hash_for(command: &str, hook: &SessionHook) -> String {
    // Keys in sorted order, which is what the canonical form is whatever the map's own order.
    let mut handler = serde_json::Map::new();
    if let Some(limit) = hook.context_limit.filter(|limit| *limit != DEFAULT_CONTEXT_LIMIT) {
        handler.insert("additionalContextLimit".to_string(), json!(limit));
    }
    handler.insert("async".to_string(), json!(false));
    handler.insert("command".to_string(), json!(command));
    if !hook.status_message.is_empty() {
        handler.insert("statusMessage".to_string(), json!(hook.status_message));
    }
    handler.insert("timeout".to_string(), json!(hook.timeout));
    handler.insert("type".to_string(), json!("command"));
    let mut identity = serde_json::Map::new();
    identity.insert("event_name".to_string(), json!(hook.event_label()));
    identity.insert("hooks".to_string(), json!([handler]));
    if !hook.matcher.is_empty() {
        identity.insert("matcher".to_string(), json!(hook.matcher));
    }
    let serialized = serde_json::to_vec(&identity).unwrap_or_default();
    let hex: String = Sha256::digest(serialized).iter().map(|byte| format!("{byte:02x}")).collect();
    format!("sha256:{hex}")
}

/// Applies the hook's registration to the text of a `config.toml`.
///
/// The hook is one `[[hooks.SessionStart]]` group with matcher `compact`; the trust entry is
/// `[hooks.state."<config path>:session_start:<group>:0"]`, keyed by the group's position. An
/// entry left by an earlier launch (another install path) is rewritten in place, so the file
/// never collects stale ones. Hooks the user wrote are not touched, and if `SessionStart` is
/// written in a form other than an array of tables the file is returned unchanged.
pub fn with_ledger_hook(existing: &str, config_path: &Path, command: &str, enabled: bool) -> anyhow::Result<String> {
    with_session_hook(existing, config_path, command, &LEDGER_HOOK, enabled)
}

/// [`with_ledger_hook`] for any of the launcher's hooks. Each is recognised as the group whose
/// matcher is the hook's and whose only handler's command has the hook's subcommand as its last
/// word or the word before an argument, so two of them live side by side.
pub fn with_session_hook(existing: &str, config_path: &Path, command: &str, hook: &SessionHook, enabled: bool) -> anyhow::Result<String> {
    let mut doc: DocumentMut = existing.parse().context("config.toml is not valid TOML")?;
    let has_hooks = doc.get("hooks").is_some();
    if !has_hooks && !enabled {
        return Ok(existing.to_string());
    }
    if !doc.get("hooks").is_none_or(Item::is_table) {
        return Ok(existing.to_string());
    }
    let hooks = doc["hooks"].or_insert(Item::Table(Table::new()));
    let Some(hooks) = hooks.as_table_mut() else { return Ok(existing.to_string()) };
    hooks.set_implicit(true);
    if !hooks.get(hook.event).is_none_or(Item::is_array_of_tables) {
        return Ok(existing.to_string());
    }
    let groups = hooks[hook.event].or_insert(Item::ArrayOfTables(ArrayOfTables::new()));
    let Some(groups) = groups.as_array_of_tables_mut() else { return Ok(existing.to_string()) };

    // Ours is the group whose only handler runs `<something> <subcommand>[ <argument>]` under the
    // hook's matcher.
    let runs_ours = |text: &str| {
        let words: Vec<&str> = text.rsplit(' ').take(2).collect();
        words.first() == Some(&hook.subcommand) || words.get(1) == Some(&hook.subcommand)
    };
    let is_ours = |group: &Table| {
        group.get("matcher").and_then(Item::as_str).unwrap_or_default() == hook.matcher
            && group
                .get("hooks")
                .and_then(Item::as_array_of_tables)
                .is_some_and(|handlers| {
                    handlers.len() == 1
                        && handlers.iter().all(|handler| {
                            handler
                                .get("command")
                                .and_then(Item::as_str)
                                .is_some_and(runs_ours)
                        })
                })
    };
    // The group stays where it is when it exists: trust entries are keyed by position, so
    // moving ours would break the trust of any hook the user added after it.
    let command_of = |group: &Table| {
        group
            .get("hooks")
            .and_then(Item::as_array_of_tables)
            .and_then(|handlers| handlers.get(0))
            .and_then(|handler| handler.get("command"))
            .and_then(Item::as_str)
            .map(str::to_string)
    };
    let found = groups.iter().position(is_ours);
    let old_command = found.and_then(|index| groups.get(index)).and_then(command_of);
    let position = match (found, enabled) {
        (Some(index), true) => {
            if let Some(handler) = groups
                .get_mut(index)
                .and_then(|group| group.get_mut("hooks"))
                .and_then(Item::as_array_of_tables_mut)
                .and_then(|handlers| handlers.get_mut(0))
            {
                handler.insert("command", value(command));
                handler.insert("timeout", value(hook.timeout));
                set_optional_fields(handler, hook);
            }
            Some(index)
        }
        (Some(index), false) => {
            groups.remove(index);
            None
        }
        (None, true) => {
            let mut handler = Table::new();
            handler.insert("type", value("command"));
            handler.insert("command", value(command));
            handler.insert("timeout", value(hook.timeout));
            set_optional_fields(&mut handler, hook);
            let mut handlers = ArrayOfTables::new();
            handlers.push(handler);
            let mut group = Table::new();
            if !hook.matcher.is_empty() {
                group.insert("matcher", value(hook.matcher));
            }
            group.insert("hooks", Item::ArrayOfTables(handlers));
            groups.push(group);
            Some(groups.len() - 1)
        }
        (None, false) => None,
    };
    if groups.is_empty() {
        hooks.remove(hook.event);
    }

    // The trust entries that are ours carry the hash of our command, the one in the file until
    // now or the one written now. They go, and the one for the group's position is written.
    let prefix = format!("{}:{}:", config_path.display(), hook.event_label());
    let ours: Vec<String> = old_command.iter().map(|old| hook_hash_for(old, hook)).chain([hook_hash_for(command, hook)]).collect();
    if let Some(state) = hooks.get_mut("state").and_then(Item::as_table_mut) {
        let stale: Vec<String> = state
            .iter()
            .filter(|(key, entry)| {
                key.starts_with(&prefix)
                    && entry
                        .get("trusted_hash")
                        .and_then(Item::as_str)
                        .is_some_and(|hash| ours.iter().any(|our| our == hash))
            })
            .map(|(key, _)| key.to_string())
            .collect();
        for key in stale {
            state.remove(&key);
        }
    }
    if let Some(position) = position {
        let state = hooks["state"].or_insert(Item::Table(Table::new()));
        if let Some(state) = state.as_table_mut() {
            state.set_implicit(true);
            let mut entry = Table::new();
            entry.insert("trusted_hash", value(hook_hash_for(command, hook)));
            state.insert(&format!("{prefix}{position}:0"), Item::Table(entry));
        }
    } else if hooks.get("state").and_then(Item::as_table).is_some_and(Table::is_empty) {
        hooks.remove("state");
    }
    if hooks.is_empty() {
        doc.remove("hooks");
    }
    Ok(doc.to_string())
}

/// Writes or removes a handler's `statusMessage` and `additionalContextLimit` to match `hook`.
fn set_optional_fields(handler: &mut Table, hook: &SessionHook) {
    if hook.status_message.is_empty() {
        handler.remove("statusMessage");
    } else {
        handler.insert("statusMessage", value(hook.status_message));
    }
    match hook.context_limit {
        Some(limit) => {
            handler.insert("additionalContextLimit", value(limit));
        }
        None => {
            handler.remove("additionalContextLimit");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SGLANG: &str = "# HELP sglang:max_total_num_tokens x\nsglang:max_total_num_tokens{engine_type=\"unified\",model_name=\"m\"} 156907.0\n";
    const VLLM: &str = "vllm:cache_config_info{block_size=\"16\",cache_dtype=\"auto\",num_gpu_blocks=\"9000\"} 1.0\n";

    fn args(words: &[&str]) -> Vec<OsString> {
        words.iter().map(OsString::from).collect()
    }

    #[test]
    fn the_pool_is_read_under_both_engines_names() {
        assert_eq!(parse_kv_pool(SGLANG), Some(156_907));
        assert_eq!(parse_kv_pool(VLLM), Some(144_000));
        assert_eq!(parse_kv_pool("vllm:num_requests_running 0.0\n"), None);
    }

    #[test]
    fn the_limit_is_a_share_of_the_pool_and_never_above_the_window() {
        // The default model: a 262,144-token window over a 156,907-token pool.
        assert_eq!(limit_for(262_144, Some(156_907)), Some(94_144));
        // A pool larger than the window needs no limit below it, and an unknown pool gives none.
        assert_eq!(limit_for(32_768, Some(144_000)), None);
        assert_eq!(limit_for(262_144, None), None);
    }

    #[test]
    fn the_limit_goes_first_on_the_command_line() {
        assert_eq!(
            with_limit(args(&["ling", "exec", "hi"]), Some(94_144), ""),
            args(&["ling", "-c", "model_auto_compact_token_limit=94144", "exec", "hi"])
        );
        assert_eq!(with_limit(args(&["ling"]), None, ""), args(&["ling"]));
    }

    #[test]
    fn a_limit_the_user_or_night_shift_set_wins() {
        for given in [
            vec!["ling", "exec", "-c", "model_auto_compact_token_limit=49152", "hi"],
            vec!["ling", "--config", "model_auto_compact_token_limit=49152"],
            vec!["ling", "--config=model_auto_compact_token_limit=49152"],
            vec!["ling", "-cmodel_auto_compact_token_limit=49152"],
        ] {
            assert_eq!(with_limit(args(&given), Some(94_144), ""), args(&given), "{given:?}");
        }
        let given = args(&["ling"]);
        assert_eq!(with_limit(given.clone(), Some(94_144), "model_auto_compact_token_limit = 64000\n"), given);
        // Another `-c` does not count.
        assert_eq!(with_limit(args(&["ling", "-c", "x=1"]), Some(1), "").len(), 5);
    }

    /// The hook below is the one a live session accepted on 2026-10-02: defined in a `config.toml`
    /// exactly like this, the hook ran after a compaction with no `--dangerously-bypass-hook-trust`.
    /// The path keeps that day's binary name (before the product was renamed) but not its home
    /// folder, which is neutral here; the expected value was then taken from Codex's own
    /// `hook_hash` (codex-rs/hooks, rust-v0.158.0) for this path, which also reproduced the hash the
    /// live session recorded for the original one. If Codex changes how it hashes a hook, this is
    /// where to find out; re-check against Codex before changing the expected value.
    #[test]
    fn the_trust_hash_is_the_one_codex_computes() {
        assert_eq!(
            hook_hash("/home/user/.cache/dreamference/compaction-phase0/ledger-bin/target/release/puffin-ledger"),
            "sha256:1b64b41e805a60f826cec5251cf954562100119e9235cdddd20580387d4ff2d7"
        );
    }

    #[test]
    fn the_hook_is_registered_with_its_trust_entry_and_other_settings_are_kept() {
        let path = Path::new("/home/u/.mightling/config.toml");
        let existing = "model = \"m\"\n\n[features]\ncode_mode = true\n";
        let updated = with_ledger_hook(existing, path, "/opt/ling ledger", true).unwrap_or_default();
        let parsed: toml::Table = updated.parse().unwrap_or_default();
        assert_eq!(parsed["model"].as_str(), Some("m"));
        assert_eq!(parsed["features"]["code_mode"].as_bool(), Some(true));
        let group = &parsed["hooks"]["SessionStart"][0];
        assert_eq!(group["matcher"].as_str(), Some("compact"));
        assert_eq!(group["hooks"][0]["type"].as_str(), Some("command"));
        assert_eq!(group["hooks"][0]["command"].as_str(), Some("/opt/ling ledger"));
        assert_eq!(group["hooks"][0]["timeout"].as_integer(), Some(10));
        let key = "/home/u/.mightling/config.toml:session_start:0:0";
        assert_eq!(
            parsed["hooks"]["state"][key]["trusted_hash"].as_str(),
            Some(hook_hash("/opt/ling ledger").as_str())
        );
        // A second launch changes nothing.
        assert_eq!(with_ledger_hook(&updated, path, "/opt/ling ledger", true).unwrap_or_default(), updated);
    }

    #[test]
    fn a_moved_binary_replaces_the_entry_and_switching_off_removes_it() {
        let path = Path::new("/h/config.toml");
        let first = with_ledger_hook("", path, "/old/ling ledger", true).unwrap_or_default();
        let moved = with_ledger_hook(&first, path, "/new/ling ledger", true).unwrap_or_default();
        let parsed: toml::Table = moved.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["SessionStart"].as_array().map(Vec::len), Some(1));
        assert_eq!(parsed["hooks"]["SessionStart"][0]["hooks"][0]["command"].as_str(), Some("/new/ling ledger"));
        assert_eq!(parsed["hooks"]["state"].as_table().map(toml::Table::len), Some(1));
        let off = with_ledger_hook(&moved, path, "/new/ling ledger", false).unwrap_or_default();
        assert!(off.parse::<toml::Table>().is_ok_and(|table| !table.contains_key("hooks")), "{off}");
        // Never registered and switched off: the file is not touched at all.
        assert_eq!(with_ledger_hook("a = 1\n", path, "/new/ling ledger", false).unwrap_or_default(), "a = 1\n");
    }

    #[test]
    fn the_users_own_hooks_stay_and_ours_is_keyed_by_its_position() {
        let path = Path::new("/h/config.toml");
        let existing = "[[hooks.SessionStart]]\nmatcher = \"startup\"\n\n[[hooks.SessionStart.hooks]]\ntype = \"command\"\ncommand = \"echo hi\"\n\n[hooks.state.\"/h/config.toml:session_start:0:0\"]\ntrusted_hash = \"sha256:theirs\"\n";
        let updated = with_ledger_hook(existing, path, "/opt/ling ledger", true).unwrap_or_default();
        let parsed: toml::Table = updated.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["SessionStart"][0]["hooks"][0]["command"].as_str(), Some("echo hi"));
        assert_eq!(parsed["hooks"]["SessionStart"][1]["hooks"][0]["command"].as_str(), Some("/opt/ling ledger"));
        assert_eq!(parsed["hooks"]["state"]["/h/config.toml:session_start:0:0"]["trusted_hash"].as_str(), Some("sha256:theirs"));
        assert!(parsed["hooks"]["state"]["/h/config.toml:session_start:1:0"]["trusted_hash"].as_str().is_some());
        let off = with_ledger_hook(&updated, path, "/opt/ling ledger", false).unwrap_or_default();
        let parsed: toml::Table = off.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["SessionStart"].as_array().map(Vec::len), Some(1));
        assert_eq!(parsed["hooks"]["state"].as_table().map(toml::Table::len), Some(1));
    }

    #[test]
    fn session_start_written_another_way_is_left_alone() {
        let existing = "[hooks]\nSessionStart = [{ matcher = \"startup\", hooks = [] }]\n";
        assert_eq!(
            with_ledger_hook(existing, Path::new("/h/config.toml"), "/opt/ling ledger", true).unwrap_or_default(),
            existing
        );
    }

    #[test]
    #[cfg(not(windows))]
    fn a_path_the_shell_would_split_is_quoted() {
        assert_eq!(hook_command(Path::new("/opt/ling/bin/ling")), "/opt/ling/bin/ling ledger");
        assert_eq!(hook_command(Path::new("/home/a b/ling")), "'/home/a b/ling' ledger");
    }

    #[test]
    fn a_windows_path_is_quoted_for_cmd() {
        // cmd.exe does not understand single quotes, and a path with a space would split there.
        assert_eq!(
            quote_program(r"C:\Users\Jane Doe\AppData\Local\Programs\Mightling\bin\ling.exe", true),
            r#""C:\Users\Jane Doe\AppData\Local\Programs\Mightling\bin\ling.exe""#
        );
        assert_eq!(quote_program(r"C:\Program Files (x86)\Mightling\ling.exe", true), r#""C:\Program Files (x86)\Mightling\ling.exe""#);
        // The POSIX rule is unchanged.
        assert_eq!(quote_program("/opt/ling/bin/ling", false), "/opt/ling/bin/ling");
        assert_eq!(quote_program("/home/o'b/ling", false), r"'/home/o'\''b/ling'");
    }
}
