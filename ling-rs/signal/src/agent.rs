//! The agent side (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §6, §8): `ling web`, reached the way a
//! paired phone reaches it, with a device cookie on loopback.
//!
//! - [`pair`] trades an eight-digit code from `ling web pair` for the device cookie, once.
//! - [`Connection`] is one WebSocket on `/ws` with the bridge envelope `ling web ask` uses
//!   (`{call, message}` out; `{answer, …}` and `{event: {channel, payload}}` in).
//! - [`Translator`] turns the app-server's messages into the conversation's [`AgentEvent`]s; it is
//!   pure, so the protocol mapping is tested without a server.
//! - [`upload`] and [`airgap_level`] are the two plain HTTP calls.

use std::collections::HashMap;

use futures::SinkExt;
use futures::StreamExt;
use serde_json::Value;
use serde_json::json;
use tokio::io::AsyncReadExt;
use tokio::io::AsyncWriteExt;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::http::header;

use crate::bridge::AgentEvent;
use crate::bridge::ApprovalKind;

/// `ling web`'s device cookie name (ling-rs/web/src/auth.rs).
pub const DEVICE_COOKIE: &str = "mightling_device";
/// The name the bridge's device gets in `ling web devices`.
pub const DEVICE_NAME: &str = "signal-bridge";

/// What a request the bridge sent was for, so its response can be read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Pending {
    OpenThread,
    StartTurn { thread: String },
    Other,
}

/// Something to do after reading one message.
#[derive(Clone, Debug, PartialEq)]
pub enum Translated {
    Event(AgentEvent),
    /// A response to send at once: a server request the bridge never serves.
    Respond(Value),
}

/// Reads the app-server's messages for the conversation.
#[derive(Default)]
pub struct Translator {
    pending: HashMap<String, Pending>,
    /// The last error notification per thread, used if its turn ends failed without one.
    errors: HashMap<String, String>,
}

fn thread_of(params: &Value) -> Option<String> {
    params.get("threadId").and_then(Value::as_str).map(str::to_string)
}

impl Translator {
    pub fn expect(&mut self, id: &Value, what: Pending) {
        self.pending.insert(id.to_string(), what);
    }

    pub fn read(&mut self, message: &Value) -> Vec<Translated> {
        let method = message.get("method").and_then(Value::as_str);
        let id = message.get("id");
        let params = message.get("params").cloned().unwrap_or(Value::Null);
        match (method, id) {
            // A response to one of the bridge's requests.
            (None, Some(id)) => {
                let Some(what) = self.pending.remove(&id.to_string()) else { return Vec::new() };
                let error = message.get("error").filter(|e| !e.is_null()).map(|e| e.get("message").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| e.to_string()));
                match (what, error) {
                    (Pending::OpenThread, None) => match message.pointer("/result/thread/id").and_then(Value::as_str) {
                        Some(thread) => vec![Translated::Event(AgentEvent::ThreadReady { thread: thread.to_string() })],
                        None => vec![Translated::Event(AgentEvent::ThreadFailed { error: "the server named no thread".to_string() })],
                    },
                    (Pending::OpenThread, Some(error)) => vec![Translated::Event(AgentEvent::ThreadFailed { error })],
                    (Pending::StartTurn { thread }, Some(error)) => vec![Translated::Event(AgentEvent::TurnCompleted { thread, error: Some(error) })],
                    _ => Vec::new(),
                }
            }
            // A server request: approvals are the owner's to answer; anything else is declined here.
            (Some(method), Some(id)) => {
                let kind = match method {
                    "item/commandExecution/requestApproval" => Some(ApprovalKind::Command),
                    "item/fileChange/requestApproval" => Some(ApprovalKind::FileChange),
                    "item/permissions/requestApproval" => Some(ApprovalKind::Permissions),
                    _ => None,
                };
                match kind {
                    Some(kind) => vec![Translated::Event(AgentEvent::Approval {
                        id: id.clone(),
                        thread: thread_of(&params).unwrap_or_default(),
                        kind,
                        command: params.get("command").and_then(Value::as_str).map(str::to_string),
                        cwd: params.get("cwd").and_then(Value::as_str).map(str::to_string),
                        reason: params.get("reason").and_then(Value::as_str).map(str::to_string),
                    })],
                    None => vec![Translated::Respond(json!({
                        "id": id,
                        "error": { "code": -32601, "message": format!("{method} is not answered from Signal") }
                    }))],
                }
            }
            (Some(method), None) => {
                let Some(thread) = thread_of(&params) else { return Vec::new() };
                match method {
                    "turn/started" => params
                        .pointer("/turn/id")
                        .and_then(Value::as_str)
                        .map(|turn| vec![Translated::Event(AgentEvent::TurnStarted { thread, turn: turn.to_string() })])
                        .unwrap_or_default(),
                    "item/agentMessage/delta" => {
                        let text = params.get("delta").and_then(Value::as_str).unwrap_or_default();
                        if text.is_empty() { Vec::new() } else { vec![Translated::Event(AgentEvent::Delta { thread, text: text.to_string() })] }
                    }
                    "item/started" => activity(&params).map(|text| vec![Translated::Event(AgentEvent::Activity { thread, text })]).unwrap_or_default(),
                    "error" => {
                        if params.get("willRetry") != Some(&Value::Bool(true)) {
                            let text = params.pointer("/error/message").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| params.to_string());
                            self.errors.insert(thread, text);
                        }
                        Vec::new()
                    }
                    "turn/completed" => {
                        let status = params.pointer("/turn/status").and_then(Value::as_str).unwrap_or("completed");
                        let stashed = self.errors.remove(&thread);
                        let error = match status {
                            "failed" => Some(params.pointer("/turn/error/message").and_then(Value::as_str).map(str::to_string).or(stashed).unwrap_or_else(|| "the turn failed".to_string())),
                            "interrupted" => Some("interrupted".to_string()),
                            _ => None,
                        };
                        vec![Translated::Event(AgentEvent::TurnCompleted { thread, error })]
                    }
                    _ => Vec::new(),
                }
            }
            (None, None) => Vec::new(),
        }
    }
}

/// What a started item says the agent is doing, for `/status` and progress notes.
fn activity(params: &Value) -> Option<String> {
    let item = params.get("item")?;
    match item.get("type").and_then(Value::as_str)? {
        "commandExecution" => {
            let command = item.get("command").and_then(Value::as_str).unwrap_or("a command");
            let short: String = command.chars().take(80).collect();
            Some(format!("running `{short}`"))
        }
        "fileChange" => Some("editing files".to_string()),
        "webSearch" => Some("searching the web".to_string()),
        "mcpToolCall" => {
            let tool = item.get("tool").and_then(Value::as_str).unwrap_or("a tool");
            Some(format!("using {tool}"))
        }
        "reasoning" => Some("thinking".to_string()),
        _ => None,
    }
}

/// A raw HTTP/1.1 exchange on loopback; returns (status, headers, body).
async fn http(port: u16, request: &[u8]) -> Result<(u16, String, Vec<u8>), String> {
    let mut stream = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.map_err(|err| format!("ling web is not answering on port {port}: {err}"))?;
    stream.write_all(request).await.map_err(|err| err.to_string())?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).await.map_err(|err| err.to_string())?;
    let split = response.windows(4).position(|w| w == b"\r\n\r\n").ok_or("ling web sent no HTTP response")?;
    let head = String::from_utf8_lossy(&response[..split]).into_owned();
    let status = head.split_whitespace().nth(1).and_then(|code| code.parse().ok()).ok_or("ling web sent no status")?;
    Ok((status, head, response[split + 4..].to_vec()))
}

fn form_escape(text: &str) -> String {
    text.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

fn header_value(head: &str, name: &str) -> Vec<String> {
    head.lines()
        .filter_map(|line| {
            let (key, value) = line.split_once(':')?;
            key.trim().eq_ignore_ascii_case(name).then(|| value.trim().to_string())
        })
        .collect()
}

/// Trades a pairing code from `ling web pair` for a device cookie (`mightling_device=…`).
pub async fn pair(port: u16, code: &str) -> Result<String, String> {
    let body = format!("code={}&name={}", form_escape(code.trim()), form_escape(DEVICE_NAME));
    let request = format!(
        "POST /pair HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let (status, head, _) = http(port, request.as_bytes()).await?;
    header_value(&head, "set-cookie")
        .into_iter()
        .map(|cookie| cookie.split(';').next().unwrap_or_default().trim().to_string())
        .find(|cookie| cookie.starts_with(&format!("{DEVICE_COOKIE}=")))
        .ok_or_else(|| format!("ling web refused the pairing code (HTTP {status}); run `ling web pair` for a new one"))
}

/// Puts one attachment into an Ask thread's folder (`/api/upload`); returns where it was written.
pub async fn upload(port: u16, cookie: &str, thread: &str, name: &str, image: bool, bytes: &[u8]) -> Result<String, String> {
    let kind = if image { "image" } else { "file" };
    let mut request = format!(
        "POST /api/upload?thread={}&kind={kind}&name={} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\nCookie: {cookie}\r\nContent-Type: application/octet-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        form_escape(thread),
        form_escape(name),
        bytes.len()
    )
    .into_bytes();
    request.extend_from_slice(bytes);
    let (status, _, body) = http(port, &request).await?;
    let body = String::from_utf8_lossy(&body).trim().to_string();
    if status != 200 {
        return Err(format!("the upload was refused (HTTP {status}): {body}"));
    }
    let value: Value = serde_json::from_str(&body).unwrap_or(Value::String(body.clone()));
    Ok(value.get("path").and_then(Value::as_str).map(str::to_string).unwrap_or(body))
}

/// The user-level air-gap level, as `ling web` resolves it (`/api/airgapped`): true for `on`.
pub async fn airgap_level(port: u16, cookie: &str) -> Result<bool, String> {
    let request = format!("GET /api/airgapped HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {cookie}\r\nConnection: close\r\n\r\n");
    let (status, _, body) = http(port, request.as_bytes()).await?;
    if status != 200 {
        return Err(format!("ling web did not report the air-gap level (HTTP {status})"));
    }
    let value: Value = serde_json::from_slice(&body).map_err(|err| err.to_string())?;
    match value.get("level").and_then(Value::as_str) {
        Some("on") => Ok(true),
        Some("off") => Ok(false),
        other => Err(format!("unknown air-gap level {other:?}")),
    }
}

type Socket = tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>;
type Sink = futures::stream::SplitSink<Socket, Message>;

/// One bridge connection to `ling web`.
pub struct Connection {
    sink: Sink,
    next_call: u64,
    next_request: u64,
}

/// What the reader task delivers.
#[derive(Debug)]
pub enum Incoming {
    /// A protocol message from the app-server.
    Message(Value),
    /// The answer to one of the envelope's calls.
    Answer { call: u64, error: Option<String> },
    Closed,
}

impl Connection {
    /// Opens `/ws` with the device cookie; the reader's frames arrive on the returned channel.
    pub async fn open(port: u16, cookie: &str) -> Result<(Connection, tokio::sync::mpsc::UnboundedReceiver<Incoming>), String> {
        let stream = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.map_err(|err| format!("ling web is not answering on port {port}: {err}"))?;
        let mut request = format!("ws://127.0.0.1:{port}/ws").into_client_request().map_err(|err| err.to_string())?;
        let headers = request.headers_mut();
        headers.insert(header::ORIGIN, HeaderValue::from_str(&format!("http://127.0.0.1:{port}")).map_err(|err| err.to_string())?);
        headers.insert(header::COOKIE, HeaderValue::from_str(cookie).map_err(|err| err.to_string())?);
        let (socket, _) = tokio_tungstenite::client_async(request, stream).await.map_err(|err| format!("ling web refused the bridge: {err}"))?;
        let (sink, mut stream) = socket.split();
        let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
        tokio::spawn(async move {
            while let Some(Ok(frame)) = stream.next().await {
                let Message::Text(text) = frame else {
                    if matches!(frame, Message::Close(_)) {
                        break;
                    }
                    continue;
                };
                let Ok(value) = serde_json::from_str::<Value>(text.as_str()) else { continue };
                if let Some(call) = value.get("answer").and_then(Value::as_u64) {
                    let error = value.get("error").map(|e| e.as_str().map(str::to_string).unwrap_or_else(|| e.to_string()));
                    let _ = tx.send(Incoming::Answer { call, error });
                    continue;
                }
                match value.pointer("/event/channel").and_then(Value::as_str) {
                    Some("work://message") => {
                        let _ = tx.send(Incoming::Message(value["event"]["payload"].clone()));
                    }
                    Some("work://exit") => break,
                    _ => {}
                }
            }
            let _ = tx.send(Incoming::Closed);
        });
        Ok((Connection { sink, next_call: 0, next_request: 100 }, rx))
    }

    /// Sends one envelope call; returns its number (the answer arrives as [`Incoming::Answer`]).
    pub async fn call(&mut self, message: Value) -> Result<u64, String> {
        self.next_call += 1;
        let call = self.next_call;
        self.sink.send(Message::Text(json!({ "call": call, "message": message }).to_string().into())).await.map_err(|err| err.to_string())?;
        Ok(call)
    }

    /// Sends one protocol message through `work/send`.
    pub async fn send(&mut self, message: Value) -> Result<u64, String> {
        self.call(json!({ "type": "work/send", "message": message })).await
    }

    /// Sends a request; returns its id, for the translator.
    pub async fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        self.next_request += 1;
        let id = json!(self.next_request);
        self.send(json!({ "id": id, "method": method, "params": params })).await?;
        Ok(id)
    }
}

/// The opening of every connection: connect the tab to the app-server, `initialize`, `initialized`.
pub fn handshake() -> Vec<Value> {
    vec![
        json!({ "type": "work/start" }),
        json!({ "type": "work/send", "message": { "id": 1, "method": "initialize", "params": { "clientInfo": { "name": "ling-signal", "title": "Mightling over Signal", "version": env!("CARGO_PKG_VERSION") } } } }),
        json!({ "type": "work/send", "message": { "method": "initialized" } }),
    ]
}

/// A turn's input: the text, and each image uploaded into the thread's folder.
pub fn turn_input(text: &str, images: &[String], files: &[String]) -> Value {
    let mut text = text.to_string();
    if !files.is_empty() {
        let names: Vec<String> = files.iter().map(|path| std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.clone())).collect();
        text.push_str(&format!("\n\n[Attached and saved in this thread's folder: {}]", names.join(", ")));
    }
    let mut input = vec![json!({ "type": "text", "text": text.trim(), "text_elements": [] })];
    for image in images {
        input.push(json!({ "type": "localImage", "path": image }));
    }
    Value::Array(input)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn events(t: &mut Translator, message: Value) -> Vec<AgentEvent> {
        t.read(&message).into_iter().filter_map(|x| if let Translated::Event(e) = x { Some(e) } else { None }).collect()
    }

    #[test]
    fn thread_responses_name_the_thread_or_the_failure() {
        let mut t = Translator::default();
        t.expect(&json!(101), Pending::OpenThread);
        assert_eq!(events(&mut t, json!({"id": 101, "result": {"thread": {"id": "th-1"}}})), vec![AgentEvent::ThreadReady { thread: "th-1".to_string() }]);
        t.expect(&json!(102), Pending::OpenThread);
        assert_eq!(events(&mut t, json!({"id": 102, "error": {"message": "no rollout"}})), vec![AgentEvent::ThreadFailed { error: "no rollout".to_string() }]);
        // A response nobody waits for is nothing.
        assert!(events(&mut t, json!({"id": 999, "result": {}})).is_empty());
        t.expect(&json!(103), Pending::StartTurn { thread: "th-1".to_string() });
        assert_eq!(
            events(&mut t, json!({"id": 103, "error": {"message": "busy"}})),
            vec![AgentEvent::TurnCompleted { thread: "th-1".to_string(), error: Some("busy".to_string()) }]
        );
    }

    #[test]
    fn a_turn_streams_and_completes() {
        let mut t = Translator::default();
        assert_eq!(events(&mut t, json!({"method": "turn/started", "params": {"threadId": "a", "turn": {"id": "u"}}})), vec![AgentEvent::TurnStarted { thread: "a".to_string(), turn: "u".to_string() }]);
        assert_eq!(
            events(&mut t, json!({"method": "item/started", "params": {"threadId": "a", "item": {"type": "commandExecution", "command": "ls -la"}}})),
            vec![AgentEvent::Activity { thread: "a".to_string(), text: "running `ls -la`".to_string() }]
        );
        assert_eq!(events(&mut t, json!({"method": "item/agentMessage/delta", "params": {"threadId": "a", "delta": "Hi"}})), vec![AgentEvent::Delta { thread: "a".to_string(), text: "Hi".to_string() }]);
        assert_eq!(events(&mut t, json!({"method": "turn/completed", "params": {"threadId": "a", "turn": {"status": "completed"}}})), vec![AgentEvent::TurnCompleted { thread: "a".to_string(), error: None }]);
    }

    #[test]
    fn a_failed_turn_carries_its_error() {
        let mut t = Translator::default();
        events(&mut t, json!({"method": "error", "params": {"threadId": "a", "willRetry": true, "error": {"message": "retrying"}}}));
        events(&mut t, json!({"method": "error", "params": {"threadId": "a", "willRetry": false, "error": {"message": "connection refused"}}}));
        assert_eq!(
            events(&mut t, json!({"method": "turn/completed", "params": {"threadId": "a", "turn": {"status": "failed"}}})),
            vec![AgentEvent::TurnCompleted { thread: "a".to_string(), error: Some("connection refused".to_string()) }]
        );
        assert_eq!(
            events(&mut t, json!({"method": "turn/completed", "params": {"threadId": "a", "turn": {"status": "interrupted"}}})),
            vec![AgentEvent::TurnCompleted { thread: "a".to_string(), error: Some("interrupted".to_string()) }]
        );
    }

    #[test]
    fn approvals_are_the_owners_and_other_requests_are_declined() {
        let mut t = Translator::default();
        let approval = json!({"id": 7, "method": "item/commandExecution/requestApproval", "params": {"threadId": "a", "turnId": "u", "itemId": "i", "command": "rm -rf build", "cwd": "/x", "reason": "cleanup"}});
        assert_eq!(
            events(&mut t, approval),
            vec![AgentEvent::Approval { id: json!(7), thread: "a".to_string(), kind: ApprovalKind::Command, command: Some("rm -rf build".to_string()), cwd: Some("/x".to_string()), reason: Some("cleanup".to_string()) }]
        );
        let other = t.read(&json!({"id": "e1", "method": "mcpServer/elicitation/request", "params": {"threadId": "a"}}));
        assert!(matches!(&other[0], Translated::Respond(v) if v["id"] == json!("e1") && v["error"]["code"] == json!(-32601)));
    }

    #[test]
    fn turn_input_lists_files_and_adds_images() {
        let input = turn_input("look", &["/a/x.jpg".to_string()], &["/a/report.pdf".to_string()]);
        assert_eq!(input[0]["text"], json!("look\n\n[Attached and saved in this thread's folder: report.pdf]"));
        assert_eq!(input[1], json!({"type": "localImage", "path": "/a/x.jpg"}));
        assert_eq!(turn_input("plain", &[], &[]).as_array().unwrap().len(), 1);
    }

    #[test]
    fn form_values_are_escaped() {
        assert_eq!(form_escape("a b&c=d/é"), "a%20b%26c%3Dd%2F%C3%A9");
    }
}
