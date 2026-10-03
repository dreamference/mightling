//! When a session compacts, and what it is handed afterwards
//! (specs/DREAMFERENCE_PUFFIN_COMPACTION.md §4.2 and §10.1).
//!
//! Two things, both done at every launch and neither with a Codex patch:
//!
//! 1. **The limit follows the KV pool.** The catalog tells Codex to compact at the model's context
//!    window, which on the default model (262,144 tokens) is larger than everything the server
//!    can hold for all its streams together (about 157,000). A session that grew would exhaust
//!    the pool long before it compacted. The launcher reads the pool from the server's
//!    `/metrics` and passes `-c model_auto_compact_token_limit=<60% of it>`, unless the user set
//!    that key themselves.
//! 2. **The ledger hook.** `puffin ledger` (ledger.rs) is registered in `config.toml` as a
//!    `SessionStart` hook with matcher `compact`, together with the hash that marks it trusted:
//!    Codex skips a hook nobody trusted, silently. The hook is this binary and nothing else.
//! 3. **Compaction at the end of a turn** (§4.3), off until measured: Codex's
//!    `model_post_turn_compact_threshold_percent` compacts after a final answer, while the user
//!    reads it, instead of in the middle of the next turn. [`with_turn_end`] sets it so that it
//!    fires below the limit of point 1, for sessions someone reads (not `exec`).

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
/// (specs/DREAMFERENCE_PUFFIN_COMPACTION.md §11.2) found it halved compactions and commands.
pub const LEDGER_DEFAULT: bool = true;

/// Codex's key for turn-end compaction (`codex-rs/config/src/config_toml.rs`, rust-v0.158.0): the
/// percentage of the *usable* context window at which a turn that has just given its final answer
/// compacts, when no input is queued (`core/src/session/turn.rs`). 0 or absent switches it off.
/// It fires as well whenever the ordinary limit has been reached (`context_window.rs`).
pub const TURN_END_KEY: &str = "model_post_turn_compact_threshold_percent";

/// The environment variable that switches turn-end compaction on or off for one process.
pub const TURN_END_ENV: &str = "DREAMFERENCE_PUFFIN_TURN_END_COMPACTION";

/// The key in the Dreamference config file.
pub const TURN_END_SETTING: &str = "puffin_turn_end_compaction";

/// Off until the measurement of §5 Phase 2 says what a turn-end compaction costs a user who types
/// at once (about 24 s per compaction measured, §9.2).
pub const TURN_END_DEFAULT: bool = false;

/// Where the turn-end threshold sits, as a share of the session's compaction limit: 70% of
/// today's 94K limit is about 66K, the spec's example (§4.3: 25% of 262K ≈ 65K).
pub const TURN_END_SHARE_PERCENT: u64 = 70;

/// The headroom Codex keeps off a model's window when it has no `effective_context_window_percent`
/// (the catalog sets none): `default_effective_context_window_percent` in
/// `protocol/src/openai_models.rs`. The turn-end percentage is of the window *after* it.
const EFFECTIVE_WINDOW_PERCENT: u64 = 95;

/// Codex compacts at 90% of the window at most, whatever the limit says
/// (`ModelInfo::auto_compact_token_limit`).
const MAX_LIMIT_PERCENT_OF_WINDOW: u64 = 90;

/// Codex's key for the model's window; a `-c` of it narrows the catalog's (`with_config_overrides`).
const WINDOW_KEY: &str = "model_context_window";

/// Seconds Codex waits for the hook. It reads one file and runs `git status`.
const HOOK_TIMEOUT_SEC: i64 = 10;

/// The last word of the hook's command: `<this binary> ledger`.
const HOOK_SUBCOMMAND: &str = "ledger";

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
    if is_set(&args, config, LIMIT_KEY) {
        return args;
    }
    with_assignment(args, LIMIT_KEY, limit)
}

/// Turn-end compaction for this launch: reads the switch and `config.toml`, then
/// [`with_turn_end`]. Called once the limit of [`prepare`] is on the command line.
pub fn prepare_turn_end(args: Vec<OsString>, codex_home: &Path, window: u64, is_read: bool) -> Vec<OsString> {
    let settings = crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok())
        .unwrap_or_default();
    let enabled = turn_end_enabled(std::env::var(TURN_END_ENV).ok().as_deref(), &settings);
    let config = std::fs::read_to_string(codex_home.join("config.toml")).unwrap_or_default();
    with_turn_end(args, &config, window, is_read, enabled)
}

/// Whether turn-end compaction is on: [`TURN_END_ENV`], then [`TURN_END_SETTING`] in the
/// Dreamference config file (`settings`), then [`TURN_END_DEFAULT`].
pub fn turn_end_enabled(env: Option<&str>, settings: &toml::Table) -> bool {
    if let Some(value) = env.filter(|value| !value.is_empty()) {
        return matches!(value.to_lowercase().as_str(), "1" | "true" | "yes" | "on");
    }
    settings.get(TURN_END_SETTING).and_then(toml::Value::as_bool).unwrap_or(TURN_END_DEFAULT)
}

/// The percentage to give [`TURN_END_KEY`] so that a turn ends in a compaction once the context
/// passes [`TURN_END_SHARE_PERCENT`] of `limit`. Codex reads the percentage against the usable
/// window, `window` less its 5% headroom, so the share is converted to that base: 26 for the
/// default model (64,749 of its 262,144 tokens, below the 94,144 limit). At most 66, since Codex
/// never compacts later than 90% of the window. `None` only for a window of 0.
pub fn turn_end_percent(window: u64, limit: u64) -> Option<u8> {
    let usable = window * EFFECTIVE_WINDOW_PERCENT / 100;
    if usable == 0 {
        return None;
    }
    let target = limit.min(window * MAX_LIMIT_PERCENT_OF_WINDOW / 100) * TURN_END_SHARE_PERCENT / 100;
    let percent = (target * 100 / usable).max(1);
    (percent < 100).then(|| percent as u8)
}

/// Puts `-c model_post_turn_compact_threshold_percent=<p>` in front of the arguments when
/// turn-end compaction is `enabled` and the session `is_read` (the TUI, `resume`, `app-server`:
/// not `exec`, whose process ends with its turn, so a compaction after its last answer would cost
/// ~24 s and serve nothing). The limit it sits below is the one the session will use: a `-c` on
/// the command line (point 1, or Night Shift's), then `config.toml` (`config`), then the window's.
/// A percentage the user set themselves, either way, is left alone.
pub fn with_turn_end(args: Vec<OsString>, config: &str, window: u64, is_read: bool, enabled: bool) -> Vec<OsString> {
    if !enabled || !is_read || is_set(&args, config, TURN_END_KEY) {
        return args;
    }
    let number = |key: &str| value_of(&args, config, key).and_then(|text| text.parse::<u64>().ok());
    let window = number(WINDOW_KEY).map_or(window, |narrowed| narrowed.min(window));
    let limit = number(LIMIT_KEY).unwrap_or(window);
    match turn_end_percent(window, limit) {
        Some(percent) => with_assignment(args, TURN_END_KEY, percent),
        None => args,
    }
}

/// Whether `key` is set on the command line (`-c key=…` in any of its spellings) or at the top of
/// `config.toml` (`config`).
fn is_set(args: &[OsString], config: &str, key: &str) -> bool {
    command_line_value(args, key).is_some()
        || config.parse::<toml::Table>().is_ok_and(|table| table.contains_key(key))
}

/// What `key` is set to: the command line first, as Codex applies `-c` over the file.
fn value_of(args: &[OsString], config: &str, key: &str) -> Option<String> {
    command_line_value(args, key).or_else(|| {
        let table = config.parse::<toml::Table>().ok()?;
        match table.get(key)? {
            toml::Value::Integer(number) => Some(number.to_string()),
            toml::Value::String(text) => Some(text.clone()),
            _ => None,
        }
    })
}

/// The value of the last `-c key=value` on the command line (`-c k=v`, `--config k=v`,
/// `--config=k=v`, `-ck=v`); `Some("")` for a `-c key` with no value.
fn command_line_value(args: &[OsString], key: &str) -> Option<String> {
    let words: Vec<String> = args.iter().skip(1).map(|arg| arg.to_string_lossy().into_owned()).collect();
    let mut found = None;
    for (index, word) in words.iter().enumerate() {
        let assignment = match word.as_str() {
            "-c" | "--config" => words.get(index + 1).map(String::as_str),
            _ => word
                .strip_prefix("--config=")
                .or_else(|| word.strip_prefix("-c").filter(|rest| !rest.is_empty())),
        };
        let Some(assignment) = assignment else { continue };
        let (name, value) = assignment.split_once('=').unwrap_or((assignment, ""));
        if name.trim() == key {
            found = Some(value.trim().trim_matches('"').to_string());
        }
    }
    found
}

/// `args` with `-c key=value` after the program name.
fn with_assignment(args: Vec<OsString>, key: &str, value: impl std::fmt::Display) -> Vec<OsString> {
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    out.extend(["-c".into(), format!("{key}={value}").into()]);
    out.extend(args);
    out
}

/// Whether the ledger hook is registered: `DREAMFERENCE_PUFFIN_COMPACTION_LEDGER`, then
/// `puffin_compaction_ledger` in the config file, then [`LEDGER_DEFAULT`].
pub fn ledger_enabled() -> bool {
    if let Ok(setting) = std::env::var("DREAMFERENCE_PUFFIN_COMPACTION_LEDGER")
        && !setting.is_empty()
    {
        return matches!(setting.to_lowercase().as_str(), "1" | "true" | "yes" | "on");
    }
    crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok()?.get("puffin_compaction_ledger")?.as_bool())
        .unwrap_or(LEDGER_DEFAULT)
}

/// Brings `config.toml` in line with `enabled`: the hook and its trust entry present, or both
/// gone. A `hooks.json` beside it means the user keeps hooks there, and two representations in
/// one folder draw a warning from Codex at every start, so then the file is left as it is.
pub fn register_ledger_hook(config_path: &Path, enabled: bool) -> anyhow::Result<()> {
    let has_hooks_json = config_path
        .parent()
        .is_some_and(|home| home.join("hooks.json").is_file());
    let Ok(exe) = std::env::current_exe() else { return Ok(()) };
    let existing = std::fs::read_to_string(config_path).unwrap_or_default();
    let command = hook_command(&exe);
    let updated = with_ledger_hook(&existing, config_path, &command, enabled && !has_hooks_json)?;
    if updated != existing {
        crate::write_atomically(config_path, updated.as_bytes())?;
    }
    Ok(())
}

/// The hook's command line: this executable, quoted for the shell where it has to be.
pub fn hook_command(exe: &Path) -> String {
    let path = exe.to_string_lossy();
    let plain = path.chars().all(|c| c.is_ascii_alphanumeric() || "/._-+".contains(c));
    if plain {
        format!("{path} {HOOK_SUBCOMMAND}")
    } else {
        format!("'{}' {HOOK_SUBCOMMAND}", path.replace('\'', r"'\''"))
    }
}

/// The hash Codex compares with `hooks.state.<key>.trusted_hash` before it runs a hook
/// (`codex-rs/hooks/src/engine/discovery.rs`, `hook_hash`): SHA-256 over the canonical JSON of the
/// normalised hook, its keys sorted at every level. Rebuilt here from that definition; a test
/// pins a value Codex itself accepted, so a Codex bump that changes the scheme fails the test
/// rather than silently switching the hook off.
pub fn hook_hash(command: &str) -> String {
    // Keys in sorted order, which is what the canonical form is whatever the map's own order.
    let mut handler = serde_json::Map::new();
    handler.insert("async".to_string(), json!(false));
    handler.insert("command".to_string(), json!(command));
    handler.insert("timeout".to_string(), json!(HOOK_TIMEOUT_SEC));
    handler.insert("type".to_string(), json!("command"));
    let mut identity = serde_json::Map::new();
    identity.insert("event_name".to_string(), json!("session_start"));
    identity.insert("hooks".to_string(), json!([handler]));
    identity.insert("matcher".to_string(), json!("compact"));
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
    if !hooks.get("SessionStart").is_none_or(Item::is_array_of_tables) {
        return Ok(existing.to_string());
    }
    let groups = hooks["SessionStart"].or_insert(Item::ArrayOfTables(ArrayOfTables::new()));
    let Some(groups) = groups.as_array_of_tables_mut() else { return Ok(existing.to_string()) };

    // Ours is the group whose only handler runs `<something> ledger` under matcher `compact`.
    let is_ours = |group: &Table| {
        group.get("matcher").and_then(Item::as_str) == Some("compact")
            && group
                .get("hooks")
                .and_then(Item::as_array_of_tables)
                .is_some_and(|handlers| {
                    handlers.len() == 1
                        && handlers.iter().all(|handler| {
                            handler
                                .get("command")
                                .and_then(Item::as_str)
                                .is_some_and(|text| text.ends_with(&format!(" {HOOK_SUBCOMMAND}")))
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
                handler.insert("timeout", value(HOOK_TIMEOUT_SEC));
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
            handler.insert("timeout", value(HOOK_TIMEOUT_SEC));
            let mut handlers = ArrayOfTables::new();
            handlers.push(handler);
            let mut group = Table::new();
            group.insert("matcher", value("compact"));
            group.insert("hooks", Item::ArrayOfTables(handlers));
            groups.push(group);
            Some(groups.len() - 1)
        }
        (None, false) => None,
    };
    if groups.is_empty() {
        hooks.remove("SessionStart");
    }

    // The trust entries that are ours carry the hash of our command, the one in the file until
    // now or the one written now. They go, and the one for the group's position is written.
    let prefix = format!("{}:session_start:", config_path.display());
    let ours: Vec<String> = old_command.iter().map(|old| hook_hash(old)).chain([hook_hash(command)]).collect();
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
            entry.insert("trusted_hash", value(hook_hash(command)));
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
            with_limit(args(&["puffin", "exec", "hi"]), Some(94_144), ""),
            args(&["puffin", "-c", "model_auto_compact_token_limit=94144", "exec", "hi"])
        );
        assert_eq!(with_limit(args(&["puffin"]), None, ""), args(&["puffin"]));
    }

    #[test]
    fn a_limit_the_user_or_night_shift_set_wins() {
        for given in [
            vec!["puffin", "exec", "-c", "model_auto_compact_token_limit=49152", "hi"],
            vec!["puffin", "--config", "model_auto_compact_token_limit=49152"],
            vec!["puffin", "--config=model_auto_compact_token_limit=49152"],
            vec!["puffin", "-cmodel_auto_compact_token_limit=49152"],
        ] {
            assert_eq!(with_limit(args(&given), Some(94_144), ""), args(&given), "{given:?}");
        }
        let given = args(&["puffin"]);
        assert_eq!(with_limit(given.clone(), Some(94_144), "model_auto_compact_token_limit = 64000\n"), given);
        // Another `-c` does not count.
        assert_eq!(with_limit(args(&["puffin", "-c", "x=1"]), Some(1), "").len(), 5);
    }

    #[test]
    fn the_turn_end_threshold_sits_below_the_limit_on_codexs_base() {
        // The default model: 70% of the 94,144 limit is 65,900; on the usable window of
        // 249,036 (95% of 262,144) that is 26%, i.e. 64,749 tokens.
        assert_eq!(turn_end_percent(262_144, 94_144), Some(26));
        let threshold = 262_144 * EFFECTIVE_WINDOW_PERCENT / 100 * 26 / 100;
        assert!(threshold < 94_144 && threshold > 94_144 / 2, "{threshold}");
        // With no limit below the window, Codex's own 90% cap is the limit: 63% of the window.
        assert_eq!(turn_end_percent(262_144, 262_144), Some(66));
        // Never 0 (which would switch it off), and nothing for an empty window.
        assert_eq!(turn_end_percent(262_144, 100), Some(1));
        assert_eq!(turn_end_percent(0, 0), None);
    }

    #[test]
    fn turn_end_compaction_is_off_unless_switched_on() {
        let on: toml::Table = "puffin_turn_end_compaction = true".parse().unwrap();
        let off: toml::Table = "puffin_turn_end_compaction = false".parse().unwrap();
        assert!(!turn_end_enabled(None, &toml::Table::new()));
        assert!(turn_end_enabled(None, &on));
        assert!(!turn_end_enabled(Some("0"), &on));
        assert!(turn_end_enabled(Some("on"), &off));
        assert!(!turn_end_enabled(Some(""), &off));
    }

    #[test]
    fn turn_end_follows_the_limit_on_the_command_line() {
        let given = with_limit(args(&["puffin"]), Some(94_144), "");
        assert_eq!(
            with_turn_end(given.clone(), "", 262_144, true, true),
            [args(&["puffin", "-c", "model_post_turn_compact_threshold_percent=26"]), given[1..].to_vec()].concat()
        );
        // The limit in config.toml counts when the command line has none.
        let from_file = with_turn_end(args(&["puffin"]), "model_auto_compact_token_limit = 94144\n", 262_144, true, true);
        assert_eq!(from_file, args(&["puffin", "-c", "model_post_turn_compact_threshold_percent=26"]));
        // A narrower window on the command line is the base Codex uses.
        // 65,900 of a usable 124,518 (95% of 131,072) is 52%.
        let narrowed = args(&["puffin", "-c", "model_context_window=131072", "-c", "model_auto_compact_token_limit=94144"]);
        assert_eq!(command_line_value(&with_turn_end(narrowed, "", 262_144, true, true), TURN_END_KEY).as_deref(), Some("52"));
    }

    #[test]
    fn turn_end_is_left_alone_for_exec_when_off_and_when_the_user_set_it() {
        let given = args(&["puffin", "-c", "model_auto_compact_token_limit=94144", "exec", "hi"]);
        assert_eq!(with_turn_end(given.clone(), "", 262_144, false, true), given);
        assert_eq!(with_turn_end(given.clone(), "", 262_144, true, false), given);
        let own = args(&["puffin", "--config=model_post_turn_compact_threshold_percent=40"]);
        assert_eq!(with_turn_end(own.clone(), "", 262_144, true, true), own);
        let in_file = "model_post_turn_compact_threshold_percent = 0\n";
        assert_eq!(with_turn_end(args(&["puffin"]), in_file, 262_144, true, true), args(&["puffin"]));
    }

    #[test]
    fn a_value_is_read_from_every_spelling_of_c_and_the_last_wins() {
        let given = args(&["puffin", "-c", "a=1", "--config", "a=2", "--config=b=3", "-cc=\"4\"", "-c", "a=5"]);
        assert_eq!(command_line_value(&given, "a").as_deref(), Some("5"));
        assert_eq!(command_line_value(&given, "b").as_deref(), Some("3"));
        assert_eq!(command_line_value(&given, "c").as_deref(), Some("4"));
        assert_eq!(command_line_value(&given, "d"), None);
    }

    /// The hook and hash below are the ones a live session accepted on 2026-10-02: defined in a
    /// `config.toml` exactly like this, the hook ran after a compaction with no
    /// `--dangerously-bypass-hook-trust`. If Codex changes how it hashes a hook, this is where to
    /// find out; re-run that check before changing the expected value.
    #[test]
    fn the_trust_hash_is_the_one_codex_computes() {
        assert_eq!(
            hook_hash("/home/user/.cache/dreamference/compaction-phase0/ledger-bin/target/release/puffin-ledger"),
            "sha256:5cee02e5271aa2540559a85813a274411fdf53a822a6b933805d44325e4fbb76"
        );
    }

    #[test]
    fn the_hook_is_registered_with_its_trust_entry_and_other_settings_are_kept() {
        let path = Path::new("/home/u/.puffin/config.toml");
        let existing = "model = \"m\"\n\n[features]\ncode_mode = true\n";
        let updated = with_ledger_hook(existing, path, "/opt/puffin ledger", true).unwrap_or_default();
        let parsed: toml::Table = updated.parse().unwrap_or_default();
        assert_eq!(parsed["model"].as_str(), Some("m"));
        assert_eq!(parsed["features"]["code_mode"].as_bool(), Some(true));
        let group = &parsed["hooks"]["SessionStart"][0];
        assert_eq!(group["matcher"].as_str(), Some("compact"));
        assert_eq!(group["hooks"][0]["type"].as_str(), Some("command"));
        assert_eq!(group["hooks"][0]["command"].as_str(), Some("/opt/puffin ledger"));
        assert_eq!(group["hooks"][0]["timeout"].as_integer(), Some(10));
        let key = "/home/u/.puffin/config.toml:session_start:0:0";
        assert_eq!(
            parsed["hooks"]["state"][key]["trusted_hash"].as_str(),
            Some(hook_hash("/opt/puffin ledger").as_str())
        );
        // A second launch changes nothing.
        assert_eq!(with_ledger_hook(&updated, path, "/opt/puffin ledger", true).unwrap_or_default(), updated);
    }

    #[test]
    fn a_moved_binary_replaces_the_entry_and_switching_off_removes_it() {
        let path = Path::new("/h/config.toml");
        let first = with_ledger_hook("", path, "/old/puffin ledger", true).unwrap_or_default();
        let moved = with_ledger_hook(&first, path, "/new/puffin ledger", true).unwrap_or_default();
        let parsed: toml::Table = moved.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["SessionStart"].as_array().map(Vec::len), Some(1));
        assert_eq!(parsed["hooks"]["SessionStart"][0]["hooks"][0]["command"].as_str(), Some("/new/puffin ledger"));
        assert_eq!(parsed["hooks"]["state"].as_table().map(toml::Table::len), Some(1));
        let off = with_ledger_hook(&moved, path, "/new/puffin ledger", false).unwrap_or_default();
        assert!(off.parse::<toml::Table>().is_ok_and(|table| !table.contains_key("hooks")), "{off}");
        // Never registered and switched off: the file is not touched at all.
        assert_eq!(with_ledger_hook("a = 1\n", path, "/new/puffin ledger", false).unwrap_or_default(), "a = 1\n");
    }

    #[test]
    fn the_users_own_hooks_stay_and_ours_is_keyed_by_its_position() {
        let path = Path::new("/h/config.toml");
        let existing = "[[hooks.SessionStart]]\nmatcher = \"startup\"\n\n[[hooks.SessionStart.hooks]]\ntype = \"command\"\ncommand = \"echo hi\"\n\n[hooks.state.\"/h/config.toml:session_start:0:0\"]\ntrusted_hash = \"sha256:theirs\"\n";
        let updated = with_ledger_hook(existing, path, "/opt/puffin ledger", true).unwrap_or_default();
        let parsed: toml::Table = updated.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["SessionStart"][0]["hooks"][0]["command"].as_str(), Some("echo hi"));
        assert_eq!(parsed["hooks"]["SessionStart"][1]["hooks"][0]["command"].as_str(), Some("/opt/puffin ledger"));
        assert_eq!(parsed["hooks"]["state"]["/h/config.toml:session_start:0:0"]["trusted_hash"].as_str(), Some("sha256:theirs"));
        assert!(parsed["hooks"]["state"]["/h/config.toml:session_start:1:0"]["trusted_hash"].as_str().is_some());
        let off = with_ledger_hook(&updated, path, "/opt/puffin ledger", false).unwrap_or_default();
        let parsed: toml::Table = off.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["SessionStart"].as_array().map(Vec::len), Some(1));
        assert_eq!(parsed["hooks"]["state"].as_table().map(toml::Table::len), Some(1));
    }

    #[test]
    fn session_start_written_another_way_is_left_alone() {
        let existing = "[hooks]\nSessionStart = [{ matcher = \"startup\", hooks = [] }]\n";
        assert_eq!(
            with_ledger_hook(existing, Path::new("/h/config.toml"), "/opt/puffin ledger", true).unwrap_or_default(),
            existing
        );
    }

    #[test]
    fn a_path_the_shell_would_split_is_quoted() {
        assert_eq!(hook_command(Path::new("/opt/puffin/bin/puffin")), "/opt/puffin/bin/puffin ledger");
        assert_eq!(hook_command(Path::new("/home/a b/puffin")), "'/home/a b/puffin' ledger");
    }
}
