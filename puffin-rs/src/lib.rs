//! The Puffin launcher.
//!
//! Before Codex parses its command line, this points the session at the model Dreamference serves
//! on this machine. It is the Rust port of what Dreamference's Python `puffin` entry point did
//! before exec'ing Codex, and it replaces that entry point:
//!
//! 1. Find the vLLM server (`DREAMFERENCE_VLLM_HOST`, then `vllm_host` in `dreamference.toml`).
//! 2. Wait for it, printing the same progress the Python runner did.
//! 3. Ask it which model it serves and how long that model's context is. vLLM's `/v1/models`
//!    reports both, so no model registry is needed here.
//! 4. Write a model catalog entry for that model, plus the `config.toml` settings Codex needs to
//!    use it. Both go in `$CODEX_HOME` (default `~/.codex`).
//! 5. Put `--oss --local-provider openai-custom --model <id>` in front of the user's arguments.
//!    These are root options, so `exec`, `resume` and `fork` inherit them.
//!
//! Everything after that is Codex, so every Codex flag and subcommand works unchanged.

use std::ffi::OsString;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;
use std::time::Instant;

use anyhow::Context;
use anyhow::bail;
use serde_json::json;
use toml_edit::DocumentMut;
use toml_edit::Item;
use toml_edit::Table;
use toml_edit::value;

/// Where Dreamference serves its model unless configured otherwise.
pub const DEFAULT_VLLM_HOST: &str = "http://localhost:8000";

/// The provider name the catalog and `config.toml` agree on.
pub const PROVIDER: &str = "openai-custom";

/// How long to wait for a model server that is still loading. A cold load of the default model
/// runs to several minutes.
const MAX_WAIT: Duration = Duration::from_secs(600);

/// Codex subcommands that never open a session, so `puffin apply` or `puffin completion bash`
/// answers at once instead of waiting for a model server that may not be running.
const COMMANDS_WITHOUT_MODEL: &[&str] = &[
    "help", "completion", "apply", "a", "features", "doctor", "login", "logout", "mcp", "plugin",
    "archive", "unarchive", "delete", "sandbox",
];

/// Appended to Codex's own system prompt. Web access has to travel with the session rather than
/// the directory: an `AGENTS.md` would only apply inside this repository. It is a shell command,
/// not an MCP tool, because Codex exposes MCP tools only inside Code Mode's JavaScript runtime and
/// the local model does not reliably call them there.
pub const WEB_ACCESS_INSTRUCTIONS: &str = r#"

# Web access

You have web access through two shell commands, run like any other command:

    puffin-admin search "your query here"        # search; -n N for more results (default 5)
    puffin-admin fetch "https://example.com"     # fetch a page as readable text

Use them whenever the answer depends on something you cannot know from training or from the files
in front of you: today's weather or tides, current events, release versions, live documentation,
anything dated. Search first, then `puffin-admin fetch` a promising URL when the snippets are not enough.

Do not say you cannot browse the web. You can, through these commands.

Do not use `curl` or `wget` for this. They are frequently blocked by the sandbox and return nothing,
which looks like the site being down rather than the command being unavailable.

There is no web search *tool* — do not look for one. Search is a shell command, shown above.

Queries go to a SearXNG instance on this machine, which contacts upstream engines on your behalf:
no API key, no account, and no query addressed to a search company. If it reports the instance is
unreachable, the error names the command that restarts it.
"#;

/// The model vLLM is serving, as `/v1/models` describes it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServedModel {
    pub id: String,
    pub max_model_len: u64,
}

/// Rewrites the command line so Codex talks to the local model, doing the setup that requires.
///
/// Returns the arguments unchanged when the command never reaches a model, e.g. `--version`.
pub async fn prepare_args(args: Vec<OsString>) -> anyhow::Result<Vec<OsString>> {
    let user_args: Vec<String> = args
        .iter()
        .skip(1)
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect();
    if !needs_model(&user_args) {
        return Ok(args);
    }

    let host = vllm_host();
    let model = wait_for_model(&host).await?;
    let codex_home = codex_utils_home_dir::find_codex_home()
        .context("could not resolve CODEX_HOME")?
        .as_path()
        .to_path_buf();
    configure_codex_home(&codex_home, &host, &model)?;
    Ok(with_local_model_args(args, &model.id))
}

/// Tells whether a command line talks to the model at all.
pub fn needs_model(user_args: &[String]) -> bool {
    if user_args
        .iter()
        .any(|arg| matches!(arg.as_str(), "--help" | "-h" | "--version" | "-V"))
    {
        return false;
    }
    !user_args
        .first()
        .is_some_and(|first| COMMANDS_WITHOUT_MODEL.contains(&first.as_str()))
}

/// Resolves the vLLM URL through the same tiers Dreamference's config uses: environment, then the
/// config file, then the default.
pub fn vllm_host() -> String {
    if let Ok(host) = std::env::var("DREAMFERENCE_VLLM_HOST")
        && !host.is_empty()
    {
        return host;
    }
    config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| vllm_host_from_toml(&text))
        .unwrap_or_else(|| DEFAULT_VLLM_HOST.to_string())
}

fn vllm_host_from_toml(text: &str) -> Option<String> {
    let parsed: toml::Table = toml::from_str(text).ok()?;
    parsed.get("vllm_host")?.as_str().map(str::to_string)
}

/// `DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then `~/.config/dreamference/config.toml`.
fn config_file() -> Option<PathBuf> {
    if let Ok(path) = std::env::var("DREAMFERENCE_CONFIG_PATH")
        && !path.is_empty()
    {
        return Some(PathBuf::from(path));
    }
    let local = PathBuf::from("dreamference.toml");
    if local.is_file() {
        return Some(local);
    }
    let global = std::env::var_os("HOME")
        .map(PathBuf::from)?
        .join(".config/dreamference/config.toml");
    global.is_file().then_some(global)
}

/// Asks the server which model it serves. `None` means it is not answering yet.
pub async fn served_model(client: &reqwest::Client, host: &str) -> Option<ServedModel> {
    let url = format!("{}/v1/models", host.trim_end_matches('/'));
    let response = client.get(url).send().await.ok()?;
    if !response.status().is_success() {
        return None;
    }
    let body: serde_json::Value = response.json().await.ok()?;
    parse_served_model(&body)
}

fn parse_served_model(body: &serde_json::Value) -> Option<ServedModel> {
    let card = body.get("data")?.as_array()?.first()?;
    Some(ServedModel {
        id: card.get("id")?.as_str()?.to_string(),
        // vLLM always reports it; the fallback only covers another OpenAI-compatible server.
        max_model_len: card
            .get("max_model_len")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(32_768),
    })
}

async fn wait_for_model(host: &str) -> anyhow::Result<ServedModel> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(1))
        .build()?;
    if let Some(model) = served_model(&client, host).await {
        return Ok(model);
    }
    eprintln!("⏳ Waiting for local vLLM server at {host} to become available...");
    let started = Instant::now();
    loop {
        if let Some(model) = served_model(&client, host).await {
            eprintln!("\n✅ vLLM server is online and ready!");
            return Ok(model);
        }
        if started.elapsed() > MAX_WAIT {
            eprintln!(
                "\n❌ Timed out waiting for vLLM server after {} seconds.",
                MAX_WAIT.as_secs()
            );
            eprintln!("💡 Start vLLM in another terminal via: `puffin-admin server start`");
            bail!("the model server at {host} is not answering");
        }
        eprint!(".");
        let _ = std::io::stderr().flush();
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

/// Codex's own system prompt, with the web section appended.
///
/// The catalog must supply `base_instructions` or Codex rejects the model, and what goes there is
/// the entire system prompt, not a label. The longest bundled template is used: the shorter ones
/// are trimmed variants for narrower modes, and a missing section costs more than an irrelevant
/// one. Patch 0003 has already renamed its identity to Puffin.
pub fn base_instructions() -> String {
    let prompt = codex_models_manager::bundled_models_response()
        .ok()
        .and_then(|response| {
            response
                .models
                .into_iter()
                .filter_map(|model| model.model_messages?.instructions_template)
                .max_by_key(String::len)
        })
        .unwrap_or_else(|| "You are Puffin, a coding agent.".to_string());
    prompt + WEB_ACCESS_INSTRUCTIONS
}

/// The catalog entry Codex needs before it will talk to a model it does not know.
///
/// Codex parses this with named serde structs, so a field of the wrong shape stops it at startup.
/// The non-obvious shapes: reasoning levels are `{effort, description}` structs, `visibility` is
/// `list|hide|none`, and `truncation_policy` is `{mode, limit}`. `tool_mode = "code_mode"` gives
/// the model Code Mode's `exec` tool, the only place MCP tools are reachable.
pub fn model_catalog(model: &ServedModel) -> serde_json::Value {
    let context = model.max_model_len;
    json!({
        "models": [{
            "id": model.id,
            "slug": model.id,
            "display_name": model.id,
            "max_context_window": context,
            // The local model runs with thinking disabled, so it advertises exactly one level.
            "supported_reasoning_levels": [{"effort": "none", "description": "No reasoning"}],
            "default_reasoning_level": "none",
            "shell_type": "default",
            "visibility": "list",
            "auto_compact_token_limit": context,
            "supported_in_api": false,
            "priority": 0,
            "support_verbosity": false,
            "supports_parallel_tool_calls": false,
            "truncation_policy": {"mode": "tokens", "limit": context},
            "experimental_supported_tools": [],
            "tool_mode": "code_mode",
            "base_instructions": base_instructions(),
        }]
    })
}

/// Writes the catalog and brings `config.toml` up to what a local session needs.
///
/// The catalog is rewritten on every launch, because the served model can change between runs.
/// In `config.toml`, keys the user may want to override are only added when absent. The provider
/// URL is always rewritten, because it has to follow the server.
pub fn configure_codex_home(
    codex_home: &Path,
    host: &str,
    model: &ServedModel,
) -> anyhow::Result<()> {
    std::fs::create_dir_all(codex_home)?;
    let catalog_path = codex_home.join("model_catalog.json");
    std::fs::write(
        &catalog_path,
        serde_json::to_string_pretty(&model_catalog(model))?,
    )?;

    let config_path = codex_home.join("config.toml");
    let existing = std::fs::read_to_string(&config_path).unwrap_or_default();
    let updated = updated_config(&existing, &catalog_path, host)?;
    if updated != existing {
        std::fs::write(&config_path, updated)?;
    }
    Ok(())
}

/// Applies the launcher's settings to the text of a `config.toml`.
///
/// Editing the document rather than appending text matters: TOML scopes a bare key to the most
/// recent table header, so a top-level key appended after `[tui.…]` silently becomes part of that
/// table, and Codex then refuses to start.
pub fn updated_config(existing: &str, catalog_path: &Path, host: &str) -> anyhow::Result<String> {
    let mut doc: DocumentMut = existing.parse().context("config.toml is not valid TOML")?;

    doc["model_catalog_json"] = value(catalog_path.to_string_lossy().into_owned());
    set_if_absent(doc.as_table_mut(), "suppress_unstable_features_warning", true);
    // The update check compares this build with openai/codex releases and offers to install
    // theirs, which would replace Puffin with upstream Codex.
    set_if_absent(doc.as_table_mut(), "check_for_update_on_startup", false);

    let providers = table(doc.as_table_mut(), "model_providers");
    providers.set_implicit(true);
    let provider = table(providers, PROVIDER);
    provider["name"] = value(PROVIDER);
    provider["base_url"] = value(format!("{}/v1", host.trim_end_matches('/')));

    let features = table(doc.as_table_mut(), "features");
    set_if_absent(features, "code_mode", true);
    set_if_absent(features, "enable_mcp_apps", false);

    // Without network access the workspace-write sandbox also blocks DNS, and the web commands in
    // the prompt fail with "Temporary failure in name resolution".
    let sandbox = table(doc.as_table_mut(), "sandbox_workspace_write");
    set_if_absent(sandbox, "network_access", true);

    Ok(doc.to_string())
}

fn table<'a>(parent: &'a mut Table, key: &str) -> &'a mut Table {
    if !parent.get(key).is_some_and(Item::is_table) {
        parent.insert(key, Item::Table(Table::new()));
    }
    parent[key]
        .as_table_mut()
        .unwrap_or_else(|| unreachable!("{key} was just made a table"))
}

fn set_if_absent(table: &mut Table, key: &str, setting: bool) {
    if !table.contains_key(key) {
        table.insert(key, value(setting));
    }
}

/// Puts the local-model options in front of the user's arguments, unless the user chose their own.
pub fn with_local_model_args(args: Vec<OsString>, model_id: &str) -> Vec<OsString> {
    let has = |flags: &[&str]| {
        args.iter().skip(1).any(|arg| {
            let arg = arg.to_string_lossy();
            flags
                .iter()
                .any(|flag| arg == *flag || arg.starts_with(&format!("{flag}=")))
        })
    };
    let mut injected: Vec<OsString> = Vec::new();
    if !has(&["--oss"]) {
        injected.push("--oss".into());
    }
    if !has(&["--local-provider"]) {
        injected.extend(["--local-provider".into(), PROVIDER.into()]);
    }
    if !has(&["--model", "-m"]) {
        injected.extend(["--model".into(), model_id.into()]);
    }
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    out.extend(injected);
    out.extend(args);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(args: &[&str]) -> Vec<String> {
        args.iter().map(|arg| (*arg).to_string()).collect()
    }

    #[test]
    fn version_help_and_offline_subcommands_skip_the_server() {
        assert!(!needs_model(&strings(&["--version"])));
        assert!(!needs_model(&strings(&["exec", "--help"])));
        assert!(!needs_model(&strings(&["completion", "bash"])));
        assert!(needs_model(&strings(&[])));
        assert!(needs_model(&strings(&["exec", "fix the tests"])));
        assert!(needs_model(&strings(&["resume", "--last"])));
    }

    #[test]
    fn local_model_args_go_before_the_users_own() {
        let args: Vec<OsString> = ["puffin", "exec", "hi"].iter().map(OsString::from).collect();
        let out: Vec<String> = with_local_model_args(args, "Intel/Qwen")
            .into_iter()
            .map(|arg| arg.to_string_lossy().into_owned())
            .collect();
        assert_eq!(
            out,
            strings(&[
                "puffin", "--oss", "--local-provider", PROVIDER, "--model", "Intel/Qwen", "exec",
                "hi"
            ])
        );
    }

    #[test]
    fn a_model_the_user_names_is_left_alone() {
        let args: Vec<OsString> = ["puffin", "-m", "other"].iter().map(OsString::from).collect();
        let out = with_local_model_args(args, "Intel/Qwen");
        assert!(!out.iter().any(|arg| arg == "Intel/Qwen"));
    }

    #[test]
    fn the_served_model_and_its_context_come_from_v1_models() {
        let body = json!({"data": [{"id": "Intel/Qwen", "max_model_len": 32768}]});
        assert_eq!(
            parse_served_model(&body),
            Some(ServedModel { id: "Intel/Qwen".into(), max_model_len: 32768 })
        );
        assert_eq!(parse_served_model(&json!({"data": []})), None);
    }

    #[test]
    fn top_level_keys_stay_top_level_after_a_table() {
        let existing = "[tui.model_availability_nux]\nfoo = 1\n";
        let text = updated_config(existing, Path::new("/h/model_catalog.json"), "http://x:8000/")
            .unwrap_or_default();
        let parsed: toml::Table = toml::from_str(&text).unwrap_or_default();
        assert_eq!(
            parsed.get("model_catalog_json").and_then(toml::Value::as_str),
            Some("/h/model_catalog.json")
        );
        assert_eq!(
            parsed.get("check_for_update_on_startup").and_then(toml::Value::as_bool),
            Some(false)
        );
        let tui = parsed.get("tui").and_then(toml::Value::as_table);
        assert!(tui.is_some_and(|tui| !tui.contains_key("model_catalog_json")));
        let base_url = parsed
            .get("model_providers")
            .and_then(|providers| providers.get(PROVIDER))
            .and_then(|provider| provider.get("base_url"))
            .and_then(toml::Value::as_str);
        assert_eq!(base_url, Some("http://x:8000/v1"));
    }

    #[test]
    fn user_choices_in_config_are_kept() {
        let existing = "check_for_update_on_startup = true\n[features]\ncode_mode = false\n";
        let text =
            updated_config(existing, Path::new("/c.json"), DEFAULT_VLLM_HOST).unwrap_or_default();
        let parsed: toml::Table = toml::from_str(&text).unwrap_or_default();
        assert_eq!(
            parsed.get("check_for_update_on_startup").and_then(toml::Value::as_bool),
            Some(true)
        );
        let code_mode = parsed
            .get("features")
            .and_then(|features| features.get("code_mode"))
            .and_then(toml::Value::as_bool);
        assert_eq!(code_mode, Some(false));
    }

    #[test]
    fn the_prompt_is_puffins_and_carries_web_access() {
        let prompt = base_instructions();
        assert!(prompt.starts_with("You are Puffin"), "{}", &prompt[..80.min(prompt.len())]);
        assert!(prompt.contains("puffin-admin search"));
    }

    #[test]
    fn host_comes_from_the_config_file() {
        assert_eq!(
            vllm_host_from_toml("vllm_host = \"http://gb10:9000\"\nmodel = \"x\"\n"),
            Some("http://gb10:9000".into())
        );
        assert_eq!(vllm_host_from_toml("model = \"x\"\n"), None);
    }
}
