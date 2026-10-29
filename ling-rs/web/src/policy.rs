//! What the Mightling UI may say to `ling app-server`, and what it hears back
//! (specs/DREAMFERENCE_MIGHTLING_ASK.md §2.3, §3.2).
//!
//! The rules are data, `policy.json`, so the desktop app's main process enforces the same ones; the
//! cases in `vectors/` are the contract both implementations test against. Everything the UI sends
//! passes [`Policy::vet_outgoing`]: a request outside the allow-list, a notification it may not
//! send, or an answer to a server request that is not waiting is refused; a thread opener loses the
//! fields that would replace Mightling's prompt, provider or policy, and a named prompt becomes
//! `baseInstructions` here, never from the UI's own text. An Ask thread (§3.1) also gets a fresh
//! scratch folder as its `cwd` and a sandbox rooted there, whatever folder the UI asked for.
//!
//! Plugins are allowed while `/airgapped` is off (2026-10-22). At a configured `on` the policy
//! refuses what fetches one (`airgappedRefusedRequests`: installing, adding or upgrading a
//! marketplace, all `git` in the app server's own process, outside the sandbox), an import loses
//! its plugin items (`airgappedRefusedImportItems`), and every thread opener gets
//! `airgappedThreadConfig`, which starts the thread without plugins: their MCP servers would run
//! outside the sandbox (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §14.12). The composer's `!`
//! command (`thread/shellCommand`, run in the user's own shell with no sandbox) is refused the same
//! way, judged by the level of the thread it names (§14.13).

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::collections::HashSet;

use serde_json::Map;
use serde_json::Value;

/// The policy file, compiled in so a running server cannot be pointed at a weaker one.
pub const POLICY_JSON: &str = include_str!("../policy.json");

/// Where the policy layer gets what the UI may only name: a prompt's composed text, and a new
/// scratch folder for an Ask thread.
pub trait PromptSource: Send + Sync {
    /// The system prompt a session under `name` receives, exactly as `ling prompt show <name>
    /// --composed` prints it.
    fn composed(&self, name: &str) -> Result<String, String>;
    /// Creates a new, empty scratch folder for one Ask thread and returns its path.
    /// A new Ask folder for a thread started with the prompt `prompt` (ask.rs names it after it).
    fn new_scratch_folder(&self, prompt: &str) -> Result<String, String>;
    /// The scratch folder of an existing Ask thread, or None for any other thread: a resumed or
    /// forked Ask thread stays in its folder, whatever the UI asks for.
    fn scratch_folder_of(&self, thread_id: &str) -> Option<String>;
    /// Whether the air-gap level is `on`, as the launcher resolves it: for the thread `thread_id`
    /// names, or the configured level (`ling airgapped`, no thread) for None. Installing is not one
    /// thread's act, and a thread being opened has no level of its own yet, so both ask with None;
    /// a `!` command asks for its thread. An implementation that cannot tell answers true.
    fn airgapped_on(&self, thread_id: Option<&str>) -> bool;
    /// Whether `cwd` is an existing checkout recorded in `$CODEX_HOME/chat-repos/index.json` under
    /// `$CODEX_HOME/chat-repos/`: `Some(read_only)` when recorded, `None` otherwise.
    /// A source that knows no checkouts answers None.
    fn repo_checkout(&self, _cwd: &str) -> Option<bool> {
        None
    }
    /// The recorded checkout `cwd` when `thread_id` is in `app_threads` of a checkout in
    /// `$CODEX_HOME/chat-repos/index.json`, or `None` otherwise.
    fn repo_thread_cwd(&self, _thread_id: &str) -> Option<String> {
        None
    }
    /// Whether `thread_id` is a repository thread whose checkout is currently granted Full Access.
    fn repo_thread_full_access(&self, _thread_id: &str) -> bool {
        false
    }
    /// Whether the caller is a paired phone bridge device or bridge session rather than the owner's browser session or desktop app.
    fn is_bridge(&self) -> bool {
        false
    }
    /// Whether `cwd` is a folder a Work thread may work in: an existing directory, named by an
    /// absolute path, outside the Ask folders' root. A fork of an Ask thread may move there (a
    /// project's primary folder, DESKTOP §16 B33). An implementation that cannot tell answers false.
    fn work_folder(&self, _cwd: &str) -> bool {
        false
    }
    /// The workspace sandbox the policy writes on a repository thread's `turn/start` without Full
    /// Access, as the thread `thread_id` would get it from `sandbox: "workspace-write"`: the user's
    /// `[sandbox_workspace_write]` in `$CODEX_HOME/config.toml`, with no network while the thread's
    /// air gap is on. A source that cannot tell answers no network and no extra roots.
    fn workspace_sandbox(&self, _thread_id: &str) -> WorkspaceSandbox {
        WorkspaceSandbox::default()
    }
}

/// What a `workspaceWrite` sandbox policy holds beside its type (the app-server's `SandboxPolicy`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WorkspaceSandbox {
    pub network_access: bool,
    pub writable_roots: Vec<String>,
    pub exclude_tmpdir_env_var: bool,
    pub exclude_slash_tmp: bool,
}

impl WorkspaceSandbox {
    /// The `sandboxPolicy` for a turn: `readOnly` in a read-only checkout, else `workspaceWrite`.
    /// The read-only sandbox takes the same network setting, as the launcher gives it (patch 0031).
    pub fn policy(&self, read_only: bool) -> Value {
        if read_only {
            serde_json::json!({"type": "readOnly", "networkAccess": self.network_access})
        } else {
            serde_json::json!({
                "type": "workspaceWrite",
                "writableRoots": self.writable_roots,
                "networkAccess": self.network_access,
                "excludeTmpdirEnvVar": self.exclude_tmpdir_env_var,
                "excludeSlashTmp": self.exclude_slash_tmp
            })
        }
    }
}

/// The rules of `policy.json`.
#[derive(Clone, Debug)]
pub struct Policy {
    pub allowed_requests: BTreeSet<String>,
    pub allowed_notifications: BTreeSet<String>,
    pub thread_openers: BTreeSet<String>,
    pub dropped_thread_fields: Vec<String>,
    pub prompt_field: String,
    pub prompt_openers: BTreeSet<String>,
    pub named_prompts: BTreeSet<String>,
    pub scratch_prompts: BTreeSet<String>,
    pub repo_prompts: BTreeSet<String>,
    pub repo_thread_params: Map<String, Value>,
    pub repo_read_only_thread_params: Map<String, Value>,
    pub pinned_turn_fields: Vec<String>,
    pub scratch_fields: Vec<String>,
    pub scratch_sandbox: String,
    /// The request that forks a thread: an Ask thread's fork may leave its folder for a Work folder.
    pub fork_request: String,
    pub import_request: String,
    pub refused_import_items: BTreeSet<String>,
    /// Requests refused at `on`: they fetch a plugin or a marketplace outside the sandbox, or run a
    /// `!` command with no sandbox. Judged by the level of the thread they name, else the configured one.
    pub airgapped_refused_requests: BTreeSet<String>,
    /// Why a request is refused at `on`, for those with a reason of their own.
    pub airgapped_refusal_reasons: BTreeMap<String, String>,
    /// Import items dropped at `on`, beside `refused_import_items`.
    pub airgapped_refused_import_items: BTreeSet<String>,
    /// The `config` overrides a thread opener gets at `on`: no plugins.
    pub airgapped_thread_config: Map<String, Value>,
    /// The `config` overrides a thread of a named prompt gets, started or resumed (an existing one
    /// is known by its folder, `<prompt>-<hex>`): a phone's thread (`chat`) runs without
    /// `image_search`, whose pictures no phone bridge shows and which took 14 s of a 40 s answer on
    /// 2026-10-25 (Signal spec §19.8). Its `command` only keeps the entry a valid server where image search is not set
    /// up (Codex refuses a server with neither a command nor a url); a disabled server never starts.
    pub prompt_thread_config: BTreeMap<String, Map<String, Value>>,
    /// The field of `thread/start` in which the UI names the window's own tools it answers
    /// (`windowTools`: the terminal's output, the preview's console; DESKTOP_PARITY §4.5, the
    /// owner's decision 5). The policy, not the UI, writes their definitions as `dynamicTools`.
    pub window_tools_field: String,
    pub window_tools: BTreeMap<String, Value>,
}

fn strings(value: &Value, key: &str) -> Result<Vec<String>, String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("policy.json: `{key}` must be a list"))?
        .iter()
        .map(|item| item.as_str().map(str::to_string).ok_or_else(|| format!("policy.json: `{key}` holds a non-string")))
        .collect()
}

fn string(value: &Value, key: &str) -> Result<String, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| format!("policy.json: `{key}` must be a string"))
}

impl Policy {
    /// Parses a policy file.
    pub fn parse(text: &str) -> Result<Policy, String> {
        let value: Value = serde_json::from_str(text).map_err(|err| format!("policy.json: {err}"))?;
        let policy = Policy {
            allowed_requests: strings(&value, "allowedRequests")?.into_iter().collect(),
            allowed_notifications: strings(&value, "allowedNotifications")?.into_iter().collect(),
            thread_openers: strings(&value, "threadOpeners")?.into_iter().collect(),
            dropped_thread_fields: strings(&value, "droppedThreadFields")?,
            prompt_field: string(&value, "promptField")?,
            prompt_openers: strings(&value, "promptOpeners")?.into_iter().collect(),
            named_prompts: strings(&value, "namedPrompts")?.into_iter().collect(),
            scratch_prompts: strings(&value, "scratchPrompts")?.into_iter().collect(),
            repo_prompts: strings(&value, "repoPrompts")?.into_iter().collect(),
            repo_thread_params: value
                .get("repoThreadParams")
                .and_then(Value::as_object)
                .cloned()
                .ok_or_else(|| "policy.json: `repoThreadParams` must be an object".to_string())?,
            repo_read_only_thread_params: value
                .get("repoReadOnlyThreadParams")
                .and_then(Value::as_object)
                .cloned()
                .ok_or_else(|| "policy.json: `repoReadOnlyThreadParams` must be an object".to_string())?,
            pinned_turn_fields: strings(&value, "pinnedTurnFields")?,
            scratch_fields: strings(&value, "scratchFields")?,
            scratch_sandbox: string(&value, "scratchSandbox")?,
            fork_request: string(&value, "forkRequest")?,
            import_request: string(&value, "importRequest")?,
            refused_import_items: strings(&value, "refusedImportItems")?.into_iter().collect(),
            airgapped_refused_requests: strings(&value, "airgappedRefusedRequests")?.into_iter().collect(),
            airgapped_refused_import_items: strings(&value, "airgappedRefusedImportItems")?.into_iter().collect(),
            airgapped_refusal_reasons: value
                .get("airgappedRefusalReasons")
                .and_then(Value::as_object)
                .ok_or_else(|| "policy.json: `airgappedRefusalReasons` must be an object".to_string())?
                .iter()
                .map(|(method, reason)| {
                    reason
                        .as_str()
                        .map(|reason| (method.clone(), reason.to_string()))
                        .ok_or_else(|| format!("policy.json: the reason `{method}` is refused at on must be a string"))
                })
                .collect::<Result<_, _>>()?,
            airgapped_thread_config: value
                .get("airgappedThreadConfig")
                .and_then(Value::as_object)
                .cloned()
                .ok_or_else(|| "policy.json: `airgappedThreadConfig` must be an object".to_string())?,
            prompt_thread_config: match value.get("promptThreadConfig") {
                None => BTreeMap::new(),
                Some(Value::Object(prompts)) => prompts
                    .iter()
                    .map(|(name, config)| {
                        config
                            .as_object()
                            .map(|config| (name.clone(), config.clone()))
                            .ok_or_else(|| format!("policy.json: the config of the prompt `{name}` must be an object"))
                    })
                    .collect::<Result<_, _>>()?,
                Some(_) => return Err("policy.json: `promptThreadConfig` must be an object".to_string()),
            },
            window_tools_field: string(&value, "windowToolsField")?,
            window_tools: value
                .get("windowTools")
                .and_then(Value::as_object)
                .ok_or_else(|| "policy.json: `windowTools` must be an object".to_string())?
                .iter()
                .map(|(name, spec)| (name.clone(), spec.clone()))
                .collect(),
        };
        for (name, spec) in &policy.window_tools {
            if spec.get("name").and_then(Value::as_str) != Some(name.as_str()) {
                return Err(format!("policy.json: the window tool `{name}` must carry its own name"));
            }
        }
        if !policy.scratch_prompts.is_subset(&policy.named_prompts) {
            return Err("policy.json: every scratch prompt must also be a named prompt".to_string());
        }
        if !policy.repo_prompts.is_subset(&policy.named_prompts) {
            return Err("policy.json: every repo prompt must also be a named prompt".to_string());
        }
        if !policy.repo_prompts.is_disjoint(&policy.scratch_prompts) {
            return Err("policy.json: a repo prompt cannot also be a scratch prompt".to_string());
        }
        if !policy.airgapped_refused_requests.is_subset(&policy.allowed_requests) {
            return Err("policy.json: every request refused at airgapped on must be allowed at off".to_string());
        }
        if let Some(method) = policy.airgapped_refusal_reasons.keys().find(|method| !policy.airgapped_refused_requests.contains(*method)) {
            return Err(format!("policy.json: `{method}` has a reason to be refused at on but is not refused there"));
        }
        if !policy.prompt_openers.is_subset(&policy.thread_openers) {
            return Err("policy.json: every prompt opener must also be a thread opener".to_string());
        }
        Ok(policy)
    }

    /// The compiled-in policy.
    pub fn embedded() -> Policy {
        Policy::parse(POLICY_JSON).expect("the compiled-in policy.json is valid; a test checks it")
    }

    /// Checks a message the UI wants to send, removes or sets what only the policy decides, and
    /// returns the message to forward. `pending` holds the ids (as JSON text) of server requests
    /// not yet answered; answering one removes it, and an answer to anything else is refused, so
    /// the UI cannot invent approvals.
    pub fn vet_outgoing(
        &self,
        message: &Value,
        pending: &mut HashSet<String>,
        prompts: &dyn PromptSource,
    ) -> Result<Value, String> {
        let Value::Object(object) = message else {
            return Err("a message must be a JSON object".to_string());
        };
        let mut object = object.clone();
        object.remove("jsonrpc");
        // The app-server also reads a list of params, field by field in declaration order, so a
        // list would carry `sandbox` or `baseInstructions` past every rule below, which read only an
        // object's fields. Absent or null is left to the app-server, which refuses what needs params.
        if object.get("params").is_some_and(|params| !params.is_object() && !params.is_null()) {
            return Err("a message's params must be a JSON object".to_string());
        }
        let method = object.get("method").and_then(Value::as_str).map(str::to_string);
        let key = object.get("id").map(|id| id.to_string());
        match (method, key) {
            (Some(method), Some(_)) => {
                if !self.allowed_requests.contains(&method) {
                    return Err(format!("the Mightling UI does not send {method}"));
                }
                if self.airgapped_refused_requests.contains(&method) && prompts.airgapped_on(airgap_thread(&object)) {
                    return Err(self.airgapped_refusal_reasons.get(&method).cloned().unwrap_or_else(|| {
                        format!("airgapped is on: {method} fetches a plugin or a marketplace with git outside the sandbox. Run ling airgapped default off to install one.")
                    }));
                }
                let opener = self.thread_openers.contains(&method);
                let names_prompt =
                    object.get("params").and_then(Value::as_object).is_some_and(|params| params.contains_key(&self.prompt_field));
                if names_prompt && !self.prompt_openers.contains(&method) {
                    return Err(format!("a prompt can only be chosen when a thread is started, not by {method}"));
                }
                let names_tools = object
                    .get("params")
                    .and_then(Value::as_object)
                    .is_some_and(|params| params.contains_key(&self.window_tools_field));
                if names_tools && !self.prompt_openers.contains(&method) {
                    return Err(format!("window tools can only be named when a thread is started, not by {method}"));
                }
                if opener {
                    if let Some(Value::Object(params)) = object.get("params") {
                        let params = self.vet_thread_params(&method, params, prompts)?;
                        object.insert("params".to_string(), Value::Object(params));
                    }
                }
                if method == "turn/start"
                    && let Some(Value::Object(params)) = object.get_mut("params")
                {
                    let repo_thread = params
                        .get("threadId")
                        .and_then(Value::as_str)
                        .and_then(|id| prompts.repo_thread_cwd(id).map(|cwd| (id.to_string(), cwd)));
                    let is_repo = repo_thread.is_some();
                    // An Ask thread keeps the folder and sandbox it was started with: a turn's
                    // `sandboxPolicy` would hold for the turns after it.
                    let is_ask = params.get("threadId").and_then(Value::as_str).and_then(|id| prompts.scratch_folder_of(id)).is_some();
                    if prompts.is_bridge() || is_repo || is_ask {
                        for field in &self.pinned_turn_fields {
                            params.remove(field);
                        }
                    }
                    // A repository turn's sandbox is written on every turn, never left to the
                    // thread: a turn's `sandboxPolicy` holds for the turns after it. Full Access only
                    // for a turn from the page (never a messenger bridge's) on a writable checkout
                    // whose grant still stands; otherwise the sandbox `workspace-write` gives the thread, from the
                    // user's own configuration (network on unless the air gap is), read-only in a
                    // read-only checkout.
                    if let Some((thread_id, repo_cwd)) = repo_thread {
                        let read_only = prompts.repo_checkout(&repo_cwd) == Some(true);
                        let sandbox_policy = if !read_only && !prompts.is_bridge() && prompts.repo_thread_full_access(&thread_id) {
                            serde_json::json!({"type": "dangerFullAccess"})
                        } else {
                            prompts.workspace_sandbox(&thread_id).policy(read_only)
                        };
                        params.insert("sandboxPolicy".to_string(), sandbox_policy);
                    }
                }
                if method == self.import_request {
                    let params = object.get("params").and_then(Value::as_object).cloned().unwrap_or_default();
                    object.insert("params".to_string(), Value::Object(self.vet_import_params(params, prompts.airgapped_on(None))?));
                }
                Ok(Value::Object(object))
            }
            (Some(method), None) => {
                if self.allowed_notifications.contains(&method) {
                    Ok(Value::Object(object))
                } else {
                    Err(format!("the Mightling UI does not send the notification {method}"))
                }
            }
            (None, Some(key)) if object.contains_key("result") || object.contains_key("error") => {
                if pending.remove(&key) {
                    Ok(Value::Object(object))
                } else {
                    Err(format!("no server request {key} is waiting for an answer"))
                }
            }
            _ => Err("not a request, a notification or an answer".to_string()),
        }
    }

    fn vet_thread_params(&self, method: &str, params: &Map<String, Value>, prompts: &dyn PromptSource) -> Result<Map<String, Value>, String> {
        let mut params = params.clone();
        for field in &self.dropped_thread_fields {
            params.remove(field);
        }
        // A resumed or forked Ask thread stays where it was started, unless a fork continues it in a
        // Work folder (a project's, DESKTOP §16 B33): a Work thread there, with the sandbox an Ask
        // folder gets and nothing broader, and without its prompt's config.
        let mut existing = params.get("threadId").and_then(Value::as_str).and_then(|id| prompts.scratch_folder_of(id));
        let moved = params
            .get("cwd")
            .and_then(Value::as_str)
            .filter(|cwd| existing.is_some() && method == self.fork_request && prompts.work_folder(cwd))
            .map(str::to_string);
        if moved.is_some() {
            existing = None;
        }
        if let Some(folder) = moved.or_else(|| existing.clone()) {
            self.confine(&mut params, folder);
        }
        // A resumed or forked chat-work thread stays in its recorded checkout with its sandbox and on-request approvals.
        let existing_repo_cwd = params.get("threadId").and_then(Value::as_str).and_then(|id| prompts.repo_thread_cwd(id));
        if let Some(ref repo_cwd) = existing_repo_cwd {
            for field in &self.scratch_fields {
                params.remove(field);
            }
            params.insert("cwd".to_string(), Value::String(repo_cwd.clone()));
            let read_only = prompts.repo_checkout(repo_cwd).unwrap_or(false);
            let pinned = if read_only { &self.repo_read_only_thread_params } else { &self.repo_thread_params };
            for (k, v) in pinned {
                params.insert(k.clone(), v.clone());
            }
        }
        if let Some(names) = params.remove(&self.window_tools_field) {
            let Value::Array(names) = names else {
                return Err(format!("`{}` must list tool names", self.window_tools_field));
            };
            let mut tools = Vec::new();
            for name in names {
                let Some(name) = name.as_str() else {
                    return Err(format!("`{}` must list tool names", self.window_tools_field));
                };
                let Some(spec) = self.window_tools.get(name) else {
                    return Err(format!("no window tool named \"{name}\""));
                };
                tools.push(spec.clone());
            }
            if !tools.is_empty() {
                params.insert("dynamicTools".to_string(), Value::Array(tools));
            }
        }
        // At `on` the thread starts without plugins, whatever the UI asked (`config` is dropped).
        let mut config = Map::new();
        if prompts.airgapped_on(None) {
            config.extend(self.airgapped_thread_config.clone());
        }
        // A resumed thread of a prompt with a config of its own is known by its folder's name.
        if let Some(prompt) = existing.as_deref().and_then(|folder| self.prompt_of_folder(folder)) {
            config.extend(self.prompt_thread_config[prompt].clone());
        }
        if existing_repo_cwd.is_some() {
            for prompt in &self.repo_prompts {
                if let Some(prompt_config) = self.prompt_thread_config.get(prompt) {
                    config.extend(prompt_config.clone());
                }
            }
        }
        let name = match params.remove(&self.prompt_field) {
            None => None,
            Some(Value::String(name)) => Some(name),
            Some(_) => return Err(format!("`{}` must name a prompt", self.prompt_field)),
        };
        if let Some(ref name) = name
            && !self.named_prompts.contains(name)
        {
            return Err(format!(
                "no prompt named \"{name}\" may be chosen here; named prompts: {}",
                self.named_prompts.iter().cloned().collect::<Vec<_>>().join(", ")
            ));
        }
        if self.prompt_openers.contains(method)
            && prompts.is_bridge()
            && !name
                .as_deref()
                .is_some_and(|n| self.scratch_prompts.contains(n) || self.repo_prompts.contains(n))
        {
            return Err("a phone bridge starts only Ask threads or chat-work threads in a forge checkout".to_string());
        }
        if !self.prompt_openers.contains(method)
            && prompts.is_bridge()
            && existing.is_none()
            && existing_repo_cwd.is_none()
        {
            return Err("a phone bridge resumes only Ask threads or chat-work threads".to_string());
        }
        if let Some(prompt_config) = name.as_ref().and_then(|name| self.prompt_thread_config.get(name)) {
            config.extend(prompt_config.clone());
        }
        if !config.is_empty() {
            params.insert("config".to_string(), Value::Object(config));
        }
        let Some(name) = name else { return Ok(params) };
        if self.repo_prompts.contains(&name) {
            let Some(read_only) = params.get("cwd").and_then(Value::as_str).and_then(|cwd| prompts.repo_checkout(cwd)) else {
                return Err("a chat-work thread starts only in a checkout ling web made for it".to_string());
            };
            // Its checkout is its only place and the pinned sandbox its only one: no permissions
            // profile or extra workspace roots asked beside them.
            for field in self.scratch_fields.iter().filter(|field| field.as_str() != "cwd") {
                params.remove(field);
            }
            let pinned = if read_only { &self.repo_read_only_thread_params } else { &self.repo_thread_params };
            for (k, v) in pinned {
                params.insert(k.clone(), v.clone());
            }
        }
        // `default` is what the launcher's catalog already carries: setting it again would only
        // freeze today's text into the thread.
        if name != "default" {
            params.insert("baseInstructions".to_string(), Value::String(prompts.composed(&name)?));
        }
        if self.scratch_prompts.contains(&name) {
            self.confine(&mut params, prompts.new_scratch_folder(&name)?);
        }
        Ok(params)
    }

    /// Removes the items an import may not carry (`refusedImportItems`, and at `on`
    /// `airgappedRefusedImportItems`: plugins, whose sources are fetched from the network); the
    /// others are imported as asked. An item that does not name its type, or an import left with
    /// nothing to do, is refused rather than guessed at.
    fn vet_import_params(&self, mut params: Map<String, Value>, airgapped_on: bool) -> Result<Map<String, Value>, String> {
        let Some(Value::Array(items)) = params.get("migrationItems") else {
            return Err("an import must list its migrationItems".to_string());
        };
        let mut kept = Vec::new();
        for item in items {
            let Some(kind) = item.get("itemType").and_then(Value::as_str) else {
                return Err("every migration item must name its itemType".to_string());
            };
            let refused = self.refused_import_items.contains(kind)
                || (airgapped_on && self.airgapped_refused_import_items.contains(kind));
            if !refused {
                kept.push(item.clone());
            }
        }
        if kept.is_empty() {
            return Err(if airgapped_on {
                "nothing left to import: plugins are not imported while airgapped is on, since their sources are fetched from the network".to_string()
            } else {
                "nothing left to import".to_string()
            });
        }
        params.insert("migrationItems".to_string(), Value::Array(kept));
        Ok(params)
    }

    /// The prompt with a config of its own whose threads live in `folder` (`<prompt>-<hex>`, ask.rs).
    fn prompt_of_folder(&self, folder: &str) -> Option<&str> {
        let base = folder.rsplit('/').next().unwrap_or(folder);
        self.prompt_thread_config.keys().map(String::as_str).find(|prompt| base.starts_with(&format!("{prompt}-")))
    }

    /// Puts a thread in its scratch folder: whatever the UI said about where it works and what it
    /// may write is replaced.
    fn confine(&self, params: &mut Map<String, Value>, folder: String) {
        for field in &self.scratch_fields {
            params.remove(field);
        }
        params.insert("cwd".to_string(), Value::String(folder));
        params.insert("sandbox".to_string(), Value::String(self.scratch_sandbox.clone()));
    }
}

/// The thread whose air-gap level decides a request in `airgappedRefusedRequests`: its `threadId`.
fn airgap_thread(object: &Map<String, Value>) -> Option<&str> {
    object.get("params").and_then(|params| params.get("threadId")).and_then(Value::as_str)
}

/// The thread a message from the app-server describes and the folder it works in, if it carries
/// one (`thread/started`, and the answers to `thread/start|resume|fork|read`): the enforcer keeps
/// it, so a thread's level is read with its own repository's configuration file.
pub fn thread_place(value: &Value) -> Option<(String, String)> {
    let thread = value.pointer("/params/thread").or_else(|| value.pointer("/result/thread"))?;
    let id = thread.get("id").and_then(Value::as_str)?;
    let cwd = thread.get("cwd").and_then(Value::as_str)?;
    Some((id.to_string(), cwd.to_string()))
}

/// One thing the app-server sent.
#[derive(Debug, PartialEq, Eq)]
pub enum Incoming {
    /// The answer to one of the UI's requests.
    Response,
    /// A request the UI must answer (an approval); `key` is its id as JSON text.
    ServerRequest { key: String, method: String },
    Notification { method: String },
    /// Not a protocol message.
    NotProtocol,
}

/// Reads one message the app-server sent.
pub fn classify(text: &str) -> (Incoming, Option<Value>) {
    let Ok(value) = serde_json::from_str::<Value>(text) else { return (Incoming::NotProtocol, None) };
    let Some(object) = value.as_object() else { return (Incoming::NotProtocol, Some(value)) };
    let method = object.get("method").and_then(Value::as_str).map(str::to_string);
    let key = object.get("id").map(|id| id.to_string());
    let kind = match (method, key) {
        (Some(method), Some(key)) => Incoming::ServerRequest { key, method },
        (Some(method), None) => Incoming::Notification { method },
        (None, Some(_)) if object.contains_key("result") || object.contains_key("error") => Incoming::Response,
        _ => Incoming::NotProtocol,
    };
    (kind, Some(value))
}

/// The threads with a turn running, from the server's own notifications: the marker Night Shift
/// reads to give way to a person (MIGHTLING_DESKTOP §8.3), kept the same way the desktop app keeps it.
#[derive(Debug, Default)]
pub struct BusyTracker {
    threads: BTreeSet<String>,
}

impl BusyTracker {
    /// Notes a notification. Returns true when the set of busy threads changed.
    pub fn observe(&mut self, method: &str, params: Option<&Value>) -> bool {
        let Some(thread) = params.and_then(|params| params.get("threadId")).and_then(Value::as_str) else {
            return false;
        };
        match method {
            "turn/started" => self.threads.insert(thread.to_string()),
            "turn/completed" | "thread/closed" => self.threads.remove(thread),
            _ => false,
        }
    }

    pub fn threads(&self) -> impl Iterator<Item = &String> {
        self.threads.iter()
    }

    pub fn is_busy(&self) -> bool {
        !self.threads.is_empty()
    }

    pub fn marker_text(&self) -> String {
        serde_json::json!({ "threads": self.threads.iter().collect::<Vec<_>>() }).to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// The stubs every implementation's conformance runner uses (vectors/outgoing.json).
    struct Stub {
        made: Mutex<u32>,
        airgapped: std::sync::atomic::AtomicBool,
        repo_checkouts: Mutex<BTreeMap<String, bool>>,
        repo_threads: Mutex<BTreeMap<String, String>>,
        repo_full_access: Mutex<BTreeSet<String>>,
        bridge: std::sync::atomic::AtomicBool,
        workspace: Mutex<WorkspaceSandbox>,
    }

    impl PromptSource for Stub {
        fn composed(&self, name: &str) -> Result<String, String> {
            Ok(format!("PROMPT:{name}"))
        }
        fn new_scratch_folder(&self, _prompt: &str) -> Result<String, String> {
            let mut made = self.made.lock().unwrap();
            *made += 1;
            Ok(format!("SCRATCH/{made}"))
        }
        fn scratch_folder_of(&self, thread_id: &str) -> Option<String> {
            (thread_id.starts_with("ask-") || thread_id.starts_with("chat-")).then(|| format!("ASK/{thread_id}"))
        }
        fn airgapped_on(&self, _thread_id: Option<&str>) -> bool {
            self.airgapped.load(std::sync::atomic::Ordering::Relaxed)
        }
        fn repo_checkout(&self, cwd: &str) -> Option<bool> {
            self.repo_checkouts.lock().unwrap().get(cwd).copied()
        }
        fn repo_thread_cwd(&self, thread_id: &str) -> Option<String> {
            self.repo_threads.lock().unwrap().get(thread_id).cloned()
        }
        fn repo_thread_full_access(&self, thread_id: &str) -> bool {
            self.repo_full_access.lock().unwrap().contains(thread_id)
        }
        fn is_bridge(&self) -> bool {
            self.bridge.load(std::sync::atomic::Ordering::Relaxed)
        }
        fn work_folder(&self, cwd: &str) -> bool {
            cwd.starts_with("WORK/")
        }
        fn workspace_sandbox(&self, _thread_id: &str) -> WorkspaceSandbox {
            self.workspace.lock().unwrap().clone()
        }
    }

    #[test]
    fn the_embedded_policy_parses() {
        let policy = Policy::embedded();
        assert!(policy.allowed_requests.contains("thread/start"));
        assert!(policy.named_prompts.contains("ask"));
        assert!(policy.named_prompts.contains("chat-work"));
        assert!(policy.repo_prompts.contains("chat-work"));
        assert!(!policy.scratch_prompts.contains("chat-work"));
        assert!(!policy.refused_import_items.contains("PLUGINS"), "plugins import while airgapped is off");
        assert!(policy.airgapped_refused_import_items.contains("PLUGINS"), "an import fetches no plugin at on");
        for fetches in ["plugin/install", "marketplace/add", "marketplace/upgrade"] {
            assert!(policy.allowed_requests.contains(fetches) && policy.airgapped_refused_requests.contains(fetches), "{fetches}");
        }
        assert_eq!(policy.airgapped_thread_config.get("features.plugins"), Some(&Value::Bool(false)));
        // Deleting a thread for good is local: allowed at both levels (DESKTOP §16 B36).
        assert!(policy.allowed_requests.contains("thread/delete") && !policy.airgapped_refused_requests.contains("thread/delete"));
        // An Ask thread's fork may leave its folder for a Work folder (DESKTOP §16 B33).
        assert_eq!(policy.fork_request, "thread/fork");
        let chat = &policy.prompt_thread_config["chat"];
        assert_eq!(chat.get("mcp_servers.mightling_images.enabled"), Some(&Value::Bool(false)), "a phone shows no pictures");
        // An approval on a phone holds the answer until the user replies; the sandbox has the network.
        assert_eq!(chat.get("approval_policy"), Some(&Value::String("never".to_string())), "a phone thread asks no approvals");
        let chat_work = &policy.prompt_thread_config["chat-work"];
        assert_eq!(chat_work.get("approval_policy"), Some(&Value::String("on-request".to_string())));
        assert_eq!(policy.repo_thread_params.get("sandbox"), Some(&Value::String("workspace-write".to_string())));
        assert_eq!(policy.repo_thread_params.get("approvalPolicy"), Some(&Value::String("on-request".to_string())));
        assert_eq!(policy.repo_read_only_thread_params.get("sandbox"), Some(&Value::String("read-only".to_string())));
        assert_eq!(policy.repo_read_only_thread_params.get("approvalPolicy"), Some(&Value::String("on-request".to_string())));
        assert_eq!(
            policy.pinned_turn_fields,
            vec!["cwd", "approvalPolicy", "sandboxPolicy", "permissions", "runtimeWorkspaceRoots", "approvalsReviewer"]
        );
        assert_eq!(policy.repo_thread_params.get("approvalsReviewer"), Some(&Value::String("user".to_string())));
        assert!(policy.prompt_thread_config.keys().all(|name| policy.named_prompts.contains(name)));
        for refused in ["feedback/upload", "account/login/start", "remoteControl/enable", "thread/realtime/start"] {
            assert!(!policy.allowed_requests.contains(refused), "{refused}");
        }
    }

    #[test]
    fn every_conformance_vector_holds() {
        let vectors: Value = serde_json::from_str(include_str!("../vectors/outgoing.json")).unwrap();
        let policy = Policy::embedded();
        let stub = Stub {
            made: Mutex::new(0),
            airgapped: Default::default(),
            repo_checkouts: Mutex::new(BTreeMap::new()),
            repo_threads: Mutex::new(BTreeMap::new()),
            repo_full_access: Mutex::new(BTreeSet::new()),
            bridge: Default::default(),
            workspace: Mutex::new(WorkspaceSandbox::default()),
        };
        let cases = vectors["cases"].as_array().unwrap();
        assert!(cases.len() >= 25);
        for case in cases {
            let name = case["name"].as_str().unwrap();
            let mut pending: HashSet<String> = case
                .get("pending")
                .and_then(Value::as_array)
                .map(|ids| ids.iter().map(|id| id.as_str().unwrap().to_string()).collect())
                .unwrap_or_default();
            let level = case.get("airgapped").and_then(Value::as_str).unwrap_or("off");
            assert!(matches!(level, "off" | "on"), "{name}: airgapped is off or on");
            stub.airgapped.store(level == "on", std::sync::atomic::Ordering::Relaxed);
            *stub.repo_checkouts.lock().unwrap() = case
                .get("repoCheckouts")
                .and_then(Value::as_object)
                .map(|items| items.iter().map(|(k, v)| (k.clone(), v.as_bool().unwrap())).collect())
                .unwrap_or_default();
            *stub.repo_threads.lock().unwrap() = case
                .get("repoThreads")
                .and_then(Value::as_object)
                .map(|items| items.iter().map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string())).collect())
                .unwrap_or_default();
            *stub.repo_full_access.lock().unwrap() = case
                .get("repoFullAccess")
                .and_then(Value::as_array)
                .map(|ids| ids.iter().map(|id| id.as_str().unwrap().to_string()).collect())
                .unwrap_or_default();
            stub.bridge.store(case.get("bridge").and_then(Value::as_bool).unwrap_or(false), std::sync::atomic::Ordering::Relaxed);
            // The user's workspace sandbox (`workspaceSandbox`), no network and no extra roots when absent.
            let workspace = case.get("workspaceSandbox");
            let flag = |key: &str| workspace.and_then(|w| w.get(key)).and_then(Value::as_bool).unwrap_or(false);
            *stub.workspace.lock().unwrap() = WorkspaceSandbox {
                network_access: flag("networkAccess"),
                writable_roots: workspace
                    .and_then(|w| w.get("writableRoots"))
                    .and_then(Value::as_array)
                    .map(|roots| roots.iter().map(|root| root.as_str().unwrap().to_string()).collect())
                    .unwrap_or_default(),
                exclude_tmpdir_env_var: flag("excludeTmpdirEnvVar"),
                exclude_slash_tmp: flag("excludeSlashTmp"),
            };
            let result = policy.vet_outgoing(&case["message"], &mut pending, &stub);
            if case["accept"].as_bool().unwrap() {
                let out = result.unwrap_or_else(|err| panic!("{name}: refused: {err}"));
                assert_eq!(out, case["out"], "{name}");
            } else {
                assert!(result.is_err(), "{name}: accepted {result:?}");
            }
            if let Some(after) = case.get("pendingAfter").and_then(Value::as_array) {
                let after: HashSet<String> = after.iter().map(|id| id.as_str().unwrap().to_string()).collect();
                assert_eq!(pending, after, "{name}");
            }
        }
    }

    #[test]
    fn the_bang_command_is_refused_at_on_with_its_own_reason() {
        let policy = Policy::embedded();
        assert!(policy.allowed_requests.contains("thread/shellCommand"));
        assert!(policy.airgapped_refused_requests.contains("thread/shellCommand"));
        assert!(policy.airgapped_refusal_reasons["thread/shellCommand"].starts_with("airgapped is on"));
        let mut value: Value = serde_json::from_str(POLICY_JSON).unwrap();
        value["airgappedRefusalReasons"] = serde_json::json!({"thread/list": "no"});
        assert!(Policy::parse(&value.to_string()).is_err(), "a reason for a request not refused at on");
    }

    /// The level asked for is the named thread's: a `!` command names one, an install does not.
    #[test]
    fn a_refused_request_is_judged_by_the_thread_it_names() {
        struct Sealed;
        impl PromptSource for Sealed {
            fn composed(&self, name: &str) -> Result<String, String> {
                Ok(name.to_string())
            }
            fn new_scratch_folder(&self, _prompt: &str) -> Result<String, String> {
                Err("none".to_string())
            }
            fn scratch_folder_of(&self, _thread_id: &str) -> Option<String> {
                None
            }
            fn airgapped_on(&self, thread_id: Option<&str>) -> bool {
                thread_id == Some("sealed")
            }
            fn repo_checkout(&self, _cwd: &str) -> Option<bool> {
                None
            }
            fn repo_thread_cwd(&self, _thread_id: &str) -> Option<String> {
                None
            }
            fn is_bridge(&self) -> bool {
                false
            }
        }
        let policy = Policy::embedded();
        let shell = |thread: &str| serde_json::json!({"id": 1, "method": "thread/shellCommand", "params": {"threadId": thread, "command": "ls"}});
        assert!(policy.vet_outgoing(&shell("open"), &mut HashSet::new(), &Sealed).is_ok());
        assert!(policy.vet_outgoing(&shell("sealed"), &mut HashSet::new(), &Sealed).is_err());
    }

    /// Only a fork of an Ask thread leaves its folder, only for a Work folder, and only with the Ask
    /// folder's sandbox (DESKTOP §16 B33); a resume, or a fork elsewhere, stays where it was.
    #[test]
    fn an_ask_thread_forks_into_a_work_folder_and_nowhere_else() {
        let policy = Policy::embedded();
        let stub = Stub {
            made: Mutex::new(0),
            airgapped: Default::default(),
            repo_checkouts: Mutex::new(BTreeMap::new()),
            repo_threads: Mutex::new(BTreeMap::new()),
            repo_full_access: Mutex::new(BTreeSet::new()),
            bridge: Default::default(),
            workspace: Mutex::new(WorkspaceSandbox::default()),
        };
        let vet = |method: &str, thread: &str, cwd: &str| {
            let message = serde_json::json!({"id": 1, "method": method, "params": {"threadId": thread, "cwd": cwd, "sandbox": "danger-full-access", "permissions": ":danger-full-access", "runtimeWorkspaceRoots": ["/"]}});
            policy.vet_outgoing(&message, &mut HashSet::new(), &stub).unwrap()["params"].clone()
        };
        let forked = vet("thread/fork", "ask-7", "WORK/repo");
        assert_eq!(forked, serde_json::json!({"threadId": "ask-7", "cwd": "WORK/repo", "sandbox": "workspace-write"}));
        assert_eq!(vet("thread/fork", "ask-7", "/home/user")["cwd"], "ASK/ask-7");
        assert_eq!(vet("thread/resume", "ask-7", "WORK/repo")["cwd"], "ASK/ask-7");
        // A phone's thread continued in a project leaves its prompt's config behind.
        assert_eq!(vet("thread/fork", "chat-3", "WORK/repo").get("config"), None);
        assert_eq!(vet("thread/fork", "chat-3", "/home/user")["config"]["approval_policy"], "never");
        // A Work thread's fork is not the policy's to place: as asked, as before.
        assert_eq!(vet("thread/fork", "w1", "WORK/repo")["sandbox"], "danger-full-access");
        // A source that cannot tell a Work folder keeps every Ask fork in its folder.
        struct Unsure;
        impl PromptSource for Unsure {
            fn composed(&self, name: &str) -> Result<String, String> {
                Ok(name.to_string())
            }
            fn new_scratch_folder(&self, _prompt: &str) -> Result<String, String> {
                Err("none".to_string())
            }
            fn scratch_folder_of(&self, thread_id: &str) -> Option<String> {
                Some(format!("ASK/{thread_id}"))
            }
            fn airgapped_on(&self, _thread_id: Option<&str>) -> bool {
                false
            }
        }
        let message = serde_json::json!({"id": 2, "method": "thread/fork", "params": {"threadId": "ask-7", "cwd": "WORK/repo"}});
        assert_eq!(policy.vet_outgoing(&message, &mut HashSet::new(), &Unsure).unwrap()["params"]["cwd"], "ASK/ask-7");
    }

    /// A resumed thread's prompt is the one its folder is named for, up to the `-` before its hex.
    #[test]
    fn a_resumed_thread_s_prompt_is_read_from_its_folder_s_whole_name() {
        struct Folder(&'static str);
        impl PromptSource for Folder {
            fn composed(&self, name: &str) -> Result<String, String> {
                Ok(name.to_string())
            }
            fn new_scratch_folder(&self, _prompt: &str) -> Result<String, String> {
                Err("none".to_string())
            }
            fn scratch_folder_of(&self, _thread_id: &str) -> Option<String> {
                Some(self.0.to_string())
            }
            fn airgapped_on(&self, _thread_id: Option<&str>) -> bool {
                false
            }
        }
        let policy = Policy::embedded();
        let message = serde_json::json!({"id": 1, "method": "thread/resume", "params": {"threadId": "t1"}});
        let config = |folder| policy.vet_outgoing(&message, &mut HashSet::new(), &Folder(folder)).unwrap()["params"].get("config").cloned();
        assert_eq!(config("/scratch/chat-1a2b").unwrap()["approval_policy"], "never");
        assert_eq!(config("/scratch/chatter-1a2b"), None);
    }

    #[test]
    fn a_thread_and_its_folder_are_read_from_what_the_server_says() {
        let started = serde_json::json!({"method": "thread/started", "params": {"thread": {"id": "t1", "cwd": "/work/repo"}}});
        assert_eq!(thread_place(&started), Some(("t1".to_string(), "/work/repo".to_string())));
        let resumed = serde_json::json!({"id": 3, "result": {"thread": {"id": "t2", "cwd": "/work/other"}}});
        assert_eq!(thread_place(&resumed), Some(("t2".to_string(), "/work/other".to_string())));
        assert_eq!(thread_place(&serde_json::json!({"id": 4, "result": {}})), None);
    }

    #[test]
    fn a_policy_whose_scratch_prompt_is_not_named_is_rejected() {
        let mut value: Value = serde_json::from_str(POLICY_JSON).unwrap();
        value["scratchPrompts"] = serde_json::json!(["unnamed"]);
        assert!(Policy::parse(&value.to_string()).is_err());
    }

    #[test]
    fn a_policy_that_breaks_one_of_its_own_rules_is_rejected() {
        let policy: Value = serde_json::from_str(POLICY_JSON).unwrap();
        let mut refused = policy["airgappedRefusedRequests"].clone();
        refused.as_array_mut().unwrap().push(Value::String("not/allowed".to_string()));
        for (field, broken, rule) in [
            ("airgappedRefusedRequests", refused, "a request refused at on that is not allowed at off"),
            ("repoPrompts", serde_json::json!(["unnamed"]), "a repo prompt that is not a named prompt"),
            ("repoPrompts", serde_json::json!(["ask"]), "a prompt that is both a repo and a scratch prompt"),
            ("promptOpeners", serde_json::json!(["turn/start"]), "a prompt opener that opens no thread"),
            ("windowTools", serde_json::json!({"x": {"name": "y"}}), "a window tool under another name"),
        ] {
            let mut value = policy.clone();
            value[field] = broken;
            assert!(Policy::parse(&value.to_string()).is_err(), "{rule}");
        }
    }

    #[test]
    fn classify_reads_what_the_server_sends() {
        assert_eq!(classify(r#"{"id":1,"result":{}}"#).0, Incoming::Response);
        assert_eq!(
            classify(r#"{"id":"a","method":"item/commandExecution/requestApproval","params":{}}"#).0,
            Incoming::ServerRequest { key: "\"a\"".to_string(), method: "item/commandExecution/requestApproval".to_string() }
        );
        assert_eq!(classify(r#"{"method":"turn/started","params":{}}"#).0, Incoming::Notification { method: "turn/started".to_string() });
        assert_eq!(classify("not json").0, Incoming::NotProtocol);
        assert_eq!(classify("[1]").0, Incoming::NotProtocol);
    }

    #[test]
    fn an_id_with_neither_a_result_nor_an_error_is_not_an_answer() {
        assert_eq!(classify(r#"{"id":1,"error":{}}"#).0, Incoming::Response);
        assert_eq!(classify(r#"{"id":1}"#).0, Incoming::NotProtocol);
    }

    #[test]
    fn the_busy_tracker_follows_turns() {
        let mut busy = BusyTracker::default();
        let t1 = serde_json::json!({"threadId": "t1"});
        assert!(busy.observe("turn/started", Some(&t1)));
        assert!(!busy.observe("turn/started", Some(&t1)));
        assert!(busy.is_busy());
        assert_eq!(busy.marker_text(), r#"{"threads":["t1"]}"#);
        assert!(busy.observe("turn/completed", Some(&t1)));
        assert!(!busy.is_busy());
        assert!(!busy.observe("item/started", Some(&t1)));
    }

    #[test]
    fn a_closed_thread_is_no_longer_busy() {
        let mut busy = BusyTracker::default();
        let t1 = serde_json::json!({"threadId": "t1"});
        assert!(busy.observe("turn/started", Some(&t1)));
        assert!(busy.observe("thread/closed", Some(&t1)));
        assert!(!busy.is_busy());
    }
}
