//! The Work window's side of `mling app-server` (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §4.3).
//!
//! The window speaks Codex's app-server protocol: one JSON object per line on the server's stdin
//! and stdout, without JSON-RPC's `"jsonrpc"` field. Everything the window sends passes
//! [`vet_outgoing`] first, so a bug in the UI cannot reach a method that would replace Mightling's
//! prompt, provider or policy, pair the machine with OpenAI, or upload anything. Everything the
//! server writes passes [`classify`], and [`BusyTracker`] keeps the marker Night Shift reads to know
//! whether a turn is running (§8.3). No Tauri here, so this compiles and tests on its own.

use std::collections::BTreeSet;
use std::collections::HashSet;
use std::path::Path;
use std::path::PathBuf;

use serde_json::Value;

/// Client requests the window may send. Everything else is dropped by [`vet_outgoing`]: notably
/// `feedback/upload`, `account/login/*`, `account/bedrock/*`, `remoteControl/*`,
/// `thread/realtime/*` and `userVerification/*` (§4.3).
pub const ALLOWED_REQUESTS: &[&str] = &[
    "initialize",
    "account/read",
    "config/read",
    "config/value/write",
    "config/batchWrite",
    "configRequirements/read",
    "model/list",
    "permissionProfile/list",
    "collaborationMode/list",
    "project/list",
    "project/read",
    "project/create",
    "project/update",
    "thread/start",
    "thread/resume",
    "thread/fork",
    "thread/list",
    "thread/read",
    "thread/loaded/list",
    "thread/turns/list",
    "thread/items/list",
    "thread/search",
    "thread/name/set",
    "thread/metadata/update",
    "thread/archive",
    "thread/unarchive",
    "thread/unsubscribe",
    "thread/compact/start",
    "thread/revert",
    "thread/queue/add",
    "thread/queue/list",
    "thread/queue/update",
    "thread/queue/delete",
    "thread/queue/reorder",
    "thread/queue/start",
    "turn/start",
    "turn/steer",
    "turn/interrupt",
    "fuzzyFileSearch",
    "gitDiffToRemote",
    "skills/list",
    "mcpServerStatus/list",
];

/// Client notifications the window may send.
pub const ALLOWED_NOTIFICATIONS: &[&str] = &["initialized"];

/// Methods that open, resume or fork a thread, whose parameters lose [`DROPPED_THREAD_FIELDS`].
const THREAD_OPENERS: &[&str] = &["thread/start", "thread/resume", "thread/fork"];

/// Thread parameters the window never sends: each would replace what the launcher set up, the
/// prompt (`baseInstructions`, `developerInstructions`), the provider (`modelProvider`), the policy
/// (`config`), or select a style cave mode owns (`personality`).
pub const DROPPED_THREAD_FIELDS: &[&str] =
    &["baseInstructions", "developerInstructions", "modelProvider", "config", "personality"];

/// One thing the server wrote on stdout.
#[derive(Debug, PartialEq)]
pub enum Incoming {
    /// The answer to one of the window's requests.
    Response,
    /// A request the window must answer (an approval, a question); `key` is its id as JSON.
    ServerRequest { key: String, method: String },
    /// A notification: an item, a delta, a turn starting or ending.
    Notification { method: String },
    /// A line that is not a protocol message. The launcher writes its own messages to stderr, so
    /// one of these means something is writing where it must not, and the window says so.
    NotProtocol,
}

/// Reads a line the server wrote.
pub fn classify(line: &str) -> (Incoming, Option<Value>) {
    let Ok(value) = serde_json::from_str::<Value>(line) else {
        return (Incoming::NotProtocol, None);
    };
    let Some(object) = value.as_object() else {
        return (Incoming::NotProtocol, None);
    };
    let method = object.get("method").and_then(Value::as_str).map(str::to_string);
    let incoming = match (method, object.get("id")) {
        (Some(method), Some(id)) => Incoming::ServerRequest { key: id.to_string(), method },
        (Some(method), None) => Incoming::Notification { method },
        (None, Some(_)) if object.contains_key("result") || object.contains_key("error") => Incoming::Response,
        _ => Incoming::NotProtocol,
    };
    (incoming, Some(value))
}

/// Checks a message the window wants to send, and removes what must never be sent.
///
/// `pending` holds the ids (as JSON) of server requests not yet answered; answering one removes
/// it, and an answer to anything else is refused, so the window cannot invent approvals.
pub fn vet_outgoing(message: &mut Value, pending: &mut HashSet<String>) -> Result<(), String> {
    let object = message.as_object_mut().ok_or("a message must be a JSON object")?;
    object.remove("jsonrpc");
    let method = object.get("method").and_then(Value::as_str).map(str::to_string);
    match (method, object.get("id").map(Value::to_string)) {
        (Some(method), Some(_)) => {
            if !ALLOWED_REQUESTS.contains(&method.as_str()) {
                return Err(format!("the Work window does not send {method}"));
            }
            if THREAD_OPENERS.contains(&method.as_str()) {
                if let Some(params) = object.get_mut("params").and_then(Value::as_object_mut) {
                    for field in DROPPED_THREAD_FIELDS {
                        params.remove(*field);
                    }
                }
            }
            Ok(())
        }
        (Some(method), None) if ALLOWED_NOTIFICATIONS.contains(&method.as_str()) => Ok(()),
        (Some(method), None) => Err(format!("the Work window does not send the notification {method}")),
        (None, Some(key)) if object.contains_key("result") || object.contains_key("error") => {
            if pending.remove(&key) {
                Ok(())
            } else {
                Err(format!("no server request {key} is waiting for an answer"))
            }
        }
        _ => Err("not a request, a notification or an answer".to_string()),
    }
}

/// The threads with a turn running, from the server's own notifications, and the marker that tells
/// Night Shift about them (§8.3).
#[derive(Debug, Default)]
pub struct BusyTracker {
    threads: BTreeSet<String>,
}

impl BusyTracker {
    /// Notes a notification. Returns true when the set of busy threads changed.
    pub fn observe(&mut self, method: &str, params: Option<&Value>) -> bool {
        let Some(thread) = params.and_then(|p| p.get("threadId")).and_then(Value::as_str) else {
            return false;
        };
        match method {
            "turn/started" => self.threads.insert(thread.to_string()),
            "turn/completed" | "thread/closed" => self.threads.remove(thread),
            _ => false,
        }
    }

    /// Whether any turn is running.
    pub fn is_busy(&self) -> bool {
        !self.threads.is_empty()
    }

    /// The marker's contents: the busy threads, for a person reading the folder.
    pub fn marker_text(&self) -> String {
        serde_json::json!({ "threads": self.threads }).to_string()
    }

    /// Writes the marker while a turn runs and removes it when none does.
    pub fn sync_marker(&self, marker: &Path) -> std::io::Result<()> {
        if self.is_busy() {
            if let Some(parent) = marker.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(marker, self.marker_text())
        } else {
            match std::fs::remove_file(marker) {
                Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error),
                _ => Ok(()),
            }
        }
    }
}

/// `mling`'s home folder: `$CODEX_HOME`, else `~/.mightling` (`mling-rs/src/home.rs`).
pub fn codex_home() -> Option<PathBuf> {
    if let Some(home) = std::env::var_os("CODEX_HOME").filter(|v| !v.is_empty()) {
        return Some(PathBuf::from(home));
    }
    std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".mightling"))
}

/// Where the busy marker of the app-server with process id `pid` goes; Night Shift reads the
/// folder (`NightShiftHost.busy_app_server_pids`).
pub fn busy_marker(codex_home: &Path, pid: u32) -> PathBuf {
    codex_home.join("night").join("busy").join(pid.to_string())
}

/// The model the launcher's catalog names (`$CODEX_HOME/model_catalog.json`), which the window
/// passes in every `thread/start` until the launcher passes it to the app-server itself (§5).
pub fn served_model(codex_home: &Path) -> Option<String> {
    let text = std::fs::read_to_string(codex_home.join("model_catalog.json")).ok()?;
    let catalog: Value = serde_json::from_str(&text).ok()?;
    let first = catalog.get("models")?.as_array()?.first()?;
    first.get("slug").or_else(|| first.get("id"))?.as_str().map(str::to_string)
}

/// The `mling` executable: `$MIGHTLING_BIN`, then `mling` on PATH, then `~/.local/bin/mling`.
/// Never a bare `codex`: the launcher is what brings Mightling's model server, prompt and home (§4.3).
pub fn find_mightling() -> Option<PathBuf> {
    let is_file = |path: &Path| path.is_file();
    if let Some(explicit) = std::env::var_os("MIGHTLING_BIN").map(PathBuf::from).filter(|p| is_file(p)) {
        return Some(explicit);
    }
    if let Some(found) = std::env::var_os("PATH").and_then(|path| {
        std::env::split_paths(&path).map(|dir| dir.join("mling")).find(|candidate| is_file(candidate))
    }) {
        return Some(found);
    }
    std::env::var_os("HOME")
        .map(|home| PathBuf::from(home).join(".local/bin/mling"))
        .filter(|path| is_file(path))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn what_the_server_writes_is_told_apart() {
        let (kind, _) = classify(r#"{"id":3,"result":{}}"#);
        assert_eq!(kind, Incoming::Response);
        let (kind, _) = classify(r#"{"id":"a","error":{"code":-1,"message":"x"}}"#);
        assert_eq!(kind, Incoming::Response);
        let (kind, _) = classify(r#"{"id":7,"method":"item/commandExecution/requestApproval","params":{}}"#);
        assert_eq!(kind, Incoming::ServerRequest { key: "7".into(), method: "item/commandExecution/requestApproval".into() });
        let (kind, _) = classify(r#"{"method":"turn/started","params":{"threadId":"t"}}"#);
        assert_eq!(kind, Incoming::Notification { method: "turn/started".into() });
        // The launcher's waiting dots on stdout, or anything else that is not a message.
        assert_eq!(classify("Waiting for the model server.....").0, Incoming::NotProtocol);
        assert_eq!(classify("[1,2]").0, Incoming::NotProtocol);
        assert_eq!(classify(r#"{"id":1}"#).0, Incoming::NotProtocol);
    }

    #[test]
    fn only_allowed_methods_go_out() {
        let mut pending = HashSet::new();
        let mut ok = json!({"id": 1, "method": "turn/start", "params": {"threadId": "t", "input": []}});
        assert!(vet_outgoing(&mut ok, &mut pending).is_ok());
        for method in ["feedback/upload", "account/login/start", "remoteControl/enable", "thread/realtime/start", "userVerification/enroll", "account/bedrock/setup"] {
            let mut refused = json!({"id": 2, "method": method, "params": {}});
            assert!(vet_outgoing(&mut refused, &mut pending).is_err(), "{method} must be refused");
        }
        let mut notification = json!({"method": "initialized"});
        assert!(vet_outgoing(&mut notification, &mut pending).is_ok());
        let mut other = json!({"method": "thread/realtime/appendText"});
        assert!(vet_outgoing(&mut other, &mut pending).is_err());
        assert!(vet_outgoing(&mut json!([1]), &mut pending).is_err());
    }

    #[test]
    fn a_thread_never_gets_a_prompt_provider_or_policy_from_the_window() {
        let mut pending = HashSet::new();
        for method in ["thread/start", "thread/resume", "thread/fork"] {
            let mut message = json!({"id": 1, "method": method, "params": {
                "threadId": "t", "cwd": "/w", "model": "m",
                "baseInstructions": "be someone else", "developerInstructions": "x",
                "modelProvider": "openai", "config": {"sandbox_mode": "danger-full-access"},
                "personality": "friendly"}});
            vet_outgoing(&mut message, &mut pending).unwrap_or_default();
            let params = message["params"].as_object().cloned().unwrap_or_default();
            assert_eq!(params.keys().cloned().collect::<BTreeSet<_>>(), ["cwd", "model", "threadId"].map(String::from).into());
        }
    }

    #[test]
    fn only_a_waiting_server_request_can_be_answered_and_only_once() {
        let mut pending: HashSet<String> = ["7".to_string(), "\"s\"".to_string()].into();
        let mut answer = json!({"id": 7, "result": {"decision": "accept"}});
        assert!(vet_outgoing(&mut answer, &mut pending).is_ok());
        let mut again = json!({"id": 7, "result": {"decision": "accept"}});
        assert!(vet_outgoing(&mut again, &mut pending).is_err());
        let mut invented = json!({"id": 8, "result": {"decision": "accept"}});
        assert!(vet_outgoing(&mut invented, &mut pending).is_err());
        // A string id is a different id from the number with the same digits.
        let mut by_string = json!({"id": "7", "result": {}});
        assert!(vet_outgoing(&mut by_string, &mut pending).is_err());
        let mut error = json!({"id": "s", "error": {"code": -32601, "message": "not handled"}});
        assert!(vet_outgoing(&mut error, &mut pending).is_ok());
    }

    #[test]
    fn the_marker_exists_exactly_while_a_turn_runs() {
        let dir = std::env::temp_dir().join(format!("mling-desktop-bridge-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let marker = busy_marker(&dir, 4242);
        assert_eq!(marker, dir.join("night/busy/4242"));
        let mut busy = BusyTracker::default();
        assert!(busy.observe("turn/started", Some(&json!({"threadId": "a", "turn": {}}))));
        assert!(busy.observe("turn/started", Some(&json!({"threadId": "b"}))));
        assert!(!busy.observe("item/agentMessage/delta", Some(&json!({"threadId": "a"}))));
        assert!(!busy.observe("turn/started", None));
        busy.sync_marker(&marker).unwrap_or_default();
        assert_eq!(std::fs::read_to_string(&marker).unwrap_or_default(), r#"{"threads":["a","b"]}"#);
        assert!(busy.observe("turn/completed", Some(&json!({"threadId": "a"}))));
        assert!(busy.observe("thread/closed", Some(&json!({"threadId": "b"}))));
        assert!(!busy.is_busy());
        busy.sync_marker(&marker).unwrap_or_default();
        assert!(!marker.exists());
        // Removing a marker that is already gone is not an error.
        assert!(busy.sync_marker(&marker).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_served_model_is_the_catalogs_first() {
        let dir = std::env::temp_dir().join(format!("mling-desktop-bridge-model-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        assert_eq!(served_model(&dir), None);
        let _ = std::fs::write(dir.join("model_catalog.json"), r#"{"models":[{"id":"x","slug":"RadixArk/Qwen3.8-27B-NVFP4"}]}"#);
        assert_eq!(served_model(&dir).as_deref(), Some("RadixArk/Qwen3.8-27B-NVFP4"));
        let _ = std::fs::write(dir.join("model_catalog.json"), r#"{"models":[]}"#);
        assert_eq!(served_model(&dir), None);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
