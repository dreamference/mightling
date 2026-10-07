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
    fn new_scratch_folder(&self) -> Result<String, String>;
    /// The scratch folder of an existing Ask thread, or None for any other thread: a resumed or
    /// forked Ask thread stays in its folder, whatever the UI asks for.
    fn scratch_folder_of(&self, thread_id: &str) -> Option<String>;
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
    pub scratch_fields: Vec<String>,
    pub scratch_sandbox: String,
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
            scratch_fields: strings(&value, "scratchFields")?,
            scratch_sandbox: string(&value, "scratchSandbox")?,
        };
        if !policy.scratch_prompts.is_subset(&policy.named_prompts) {
            return Err("policy.json: every scratch prompt must also be a named prompt".to_string());
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
        let method = object.get("method").and_then(Value::as_str).map(str::to_string);
        let key = object.get("id").map(|id| id.to_string());
        match (method, key) {
            (Some(method), Some(_)) => {
                if !self.allowed_requests.contains(&method) {
                    return Err(format!("the Mightling UI does not send {method}"));
                }
                let opener = self.thread_openers.contains(&method);
                let names_prompt =
                    object.get("params").and_then(Value::as_object).is_some_and(|params| params.contains_key(&self.prompt_field));
                if names_prompt && !self.prompt_openers.contains(&method) {
                    return Err(format!("a prompt can only be chosen when a thread is started, not by {method}"));
                }
                if opener {
                    if let Some(Value::Object(params)) = object.get("params") {
                        let params = self.vet_thread_params(params, prompts)?;
                        object.insert("params".to_string(), Value::Object(params));
                    }
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

    fn vet_thread_params(&self, params: &Map<String, Value>, prompts: &dyn PromptSource) -> Result<Map<String, Value>, String> {
        let mut params = params.clone();
        for field in &self.dropped_thread_fields {
            params.remove(field);
        }
        // A resumed or forked Ask thread stays where it was started.
        let existing = params.get("threadId").and_then(Value::as_str).and_then(|id| prompts.scratch_folder_of(id));
        if let Some(folder) = existing {
            self.confine(&mut params, folder);
        }
        let Some(name) = params.remove(&self.prompt_field) else { return Ok(params) };
        let Value::String(name) = name else {
            return Err(format!("`{}` must name a prompt", self.prompt_field));
        };
        if !self.named_prompts.contains(&name) {
            return Err(format!(
                "no prompt named \"{name}\" may be chosen here; named prompts: {}",
                self.named_prompts.iter().cloned().collect::<Vec<_>>().join(", ")
            ));
        }
        // `default` is what the launcher's catalog already carries: setting it again would only
        // freeze today's text into the thread.
        if name != "default" {
            params.insert("baseInstructions".to_string(), Value::String(prompts.composed(&name)?));
        }
        if self.scratch_prompts.contains(&name) {
            self.confine(&mut params, prompts.new_scratch_folder()?);
        }
        Ok(params)
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
    }

    impl PromptSource for Stub {
        fn composed(&self, name: &str) -> Result<String, String> {
            Ok(format!("PROMPT:{name}"))
        }
        fn new_scratch_folder(&self) -> Result<String, String> {
            let mut made = self.made.lock().unwrap();
            *made += 1;
            Ok(format!("SCRATCH/{made}"))
        }
        fn scratch_folder_of(&self, thread_id: &str) -> Option<String> {
            thread_id.starts_with("ask-").then(|| format!("ASK/{thread_id}"))
        }
    }

    #[test]
    fn the_embedded_policy_parses() {
        let policy = Policy::embedded();
        assert!(policy.allowed_requests.contains("thread/start"));
        assert!(policy.named_prompts.contains("ask"));
        for refused in ["feedback/upload", "account/login/start", "remoteControl/enable", "thread/realtime/start"] {
            assert!(!policy.allowed_requests.contains(refused), "{refused}");
        }
    }

    #[test]
    fn every_conformance_vector_holds() {
        let vectors: Value = serde_json::from_str(include_str!("../vectors/outgoing.json")).unwrap();
        let policy = Policy::embedded();
        let stub = Stub { made: Mutex::new(0) };
        let cases = vectors["cases"].as_array().unwrap();
        assert!(cases.len() >= 25);
        for case in cases {
            let name = case["name"].as_str().unwrap();
            let mut pending: HashSet<String> = case
                .get("pending")
                .and_then(Value::as_array)
                .map(|ids| ids.iter().map(|id| id.as_str().unwrap().to_string()).collect())
                .unwrap_or_default();
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
    fn a_policy_whose_scratch_prompt_is_not_named_is_rejected() {
        let mut value: Value = serde_json::from_str(POLICY_JSON).unwrap();
        value["scratchPrompts"] = serde_json::json!(["unnamed"]);
        assert!(Policy::parse(&value.to_string()).is_err());
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
}
