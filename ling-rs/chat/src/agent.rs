//! The bridge's one connection to the agent, through `ling web` (MIGHTLING_CHAT §2).
//!
//! The hub talks to the agent through an [`AgentHandle`] (requests with answers, responses to the
//! server's requests) and reads what the agent says from an event channel: every notification and
//! server request the app-server sends, plus `mightling/connected` each time a connection is
//! (re)made, so the hub knows its thread subscriptions are gone. [`run_link`] is the real
//! connection: it signs in to `ling web` the way `ling web ask` does (a one-time login code
//! written into its state folder, traded for a session cookie), opens `/ws`, and relays. A dropped
//! connection, a restarted `ling web` (whose sessions live in memory) or a refused upgrade all lead
//! to the same place: sign in again and reconnect, with a backoff capped at a minute. The tests
//! drive the hub through a scripted stand-in on the same two channels.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

use futures::SinkExt;
use futures::StreamExt;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;
use tokio::sync::oneshot;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::http::header;

/// The event the link sends after every successful (re)connection.
pub const CONNECTED: &str = "mightling/connected";

/// What the hub asks of the agent.
pub enum AgentCommand {
    Request { method: String, params: Value, reply: oneshot::Sender<Result<Value, String>> },
    Respond { id: Value, result: Value },
}

/// The hub's side of the connection.
#[derive(Clone)]
pub struct AgentHandle {
    tx: mpsc::UnboundedSender<AgentCommand>,
}

impl AgentHandle {
    /// A handle and the receiving end the link (or a test's stand-in) serves.
    pub fn channel() -> (AgentHandle, mpsc::UnboundedReceiver<AgentCommand>) {
        let (tx, rx) = mpsc::unbounded_channel();
        (AgentHandle { tx }, rx)
    }

    /// Sends a request to the app-server and waits for its result.
    pub async fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        let (reply, answer) = oneshot::channel();
        self.tx
            .send(AgentCommand::Request { method: method.to_string(), params, reply })
            .map_err(|_| "the bridge to ling web has stopped".to_string())?;
        answer.await.map_err(|_| "the connection to ling web was lost".to_string())?
    }

    /// Answers one of the server's requests (an approval).
    pub fn respond(&self, id: Value, result: Value) {
        let _ = self.tx.send(AgentCommand::Respond { id, result });
    }
}

/// Where `ling web` is: its state folder (for login codes and its port), and how to start it.
#[derive(Clone, Debug)]
pub struct WebTarget {
    pub state: PathBuf,
    /// `ling`, to run `ling web start` when nothing answers.
    pub ling: Option<PathBuf>,
}

type Socket = tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>;

async fn open(target: &WebTarget) -> Result<Socket, String> {
    let port = ling_web_server::cli::running_port(&target.state);
    if ling_web_server::cli::probe(&target.state, port).is_none()
        && let Some(ling) = &target.ling {
            let _ = tokio::process::Command::new(ling).args(["web", "start"]).status().await;
            for _ in 0..60 {
                if ling_web_server::cli::probe(&target.state, ling_web_server::cli::running_port(&target.state)).is_some() {
                    break;
                }
                tokio::time::sleep(Duration::from_secs(2)).await;
            }
        }
    let port = ling_web_server::cli::running_port(&target.state);
    let cookie = ling_web_server::ask_client::sign_in(&target.state, port).await?;
    let stream = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.map_err(|err| format!("ling web is not answering on port {port}: {err}"))?;
    let mut request = format!("ws://127.0.0.1:{port}/ws").into_client_request().map_err(|err| err.to_string())?;
    let headers = request.headers_mut();
    headers.insert(header::ORIGIN, HeaderValue::from_str(&format!("http://127.0.0.1:{port}")).map_err(|err| err.to_string())?);
    headers.insert(header::COOKIE, HeaderValue::from_str(&cookie).map_err(|err| err.to_string())?);
    let (socket, _) = tokio_tungstenite::client_async(request, stream).await.map_err(|err| format!("ling web refused the bridge: {err}"))?;
    Ok(socket)
}

/// One bridge connection: the frames `ling web`'s relay speaks (`{"call", "message"}` out,
/// `{"answer", …}` and `{"event": {"channel", "payload"}}` in).
struct Session {
    socket: Socket,
    next_call: u64,
    next_id: u64,
    calls: HashMap<u64, oneshot::Sender<Result<Value, String>>>,
    requests: HashMap<String, oneshot::Sender<Result<Value, String>>>,
    /// The call that carried each request: a refused `work/send` fails its request at once.
    acks: HashMap<u64, String>,
}

enum Frame {
    Answer(u64, Result<Value, String>),
    Message(Value),
    Exit,
    Other,
}

fn parse(text: &str) -> Frame {
    let Ok(frame) = serde_json::from_str::<Value>(text) else { return Frame::Other };
    if let Some(call) = frame.get("answer").and_then(Value::as_u64) {
        let result = match frame.get("error") {
            Some(error) => Err(error.as_str().unwrap_or("refused").to_string()),
            None => Ok(frame.get("result").cloned().unwrap_or(Value::Null)),
        };
        return Frame::Answer(call, result);
    }
    match frame.pointer("/event/channel").and_then(Value::as_str) {
        Some("work://message") => Frame::Message(frame["event"]["payload"].clone()),
        Some("work://exit") => Frame::Exit,
        _ => Frame::Other,
    }
}

impl Session {
    async fn send_call(&mut self, message: Value) -> Result<u64, String> {
        self.next_call += 1;
        self.socket
            .send(Message::Text(json!({ "call": self.next_call, "message": message }).to_string().into()))
            .await
            .map_err(|err| err.to_string())?;
        Ok(self.next_call)
    }

    async fn call(&mut self, message: Value) -> Result<oneshot::Receiver<Result<Value, String>>, String> {
        let (tx, rx) = oneshot::channel();
        let call = self.send_call(message).await?;
        self.calls.insert(call, tx);
        Ok(rx)
    }

    /// Sends a JSON-RPC request; its response is matched by id when it arrives.
    async fn request(&mut self, method: &str, params: Value, reply: oneshot::Sender<Result<Value, String>>) {
        self.next_id += 1;
        let id = self.next_id;
        let message = json!({ "id": id, "method": method, "params": params });
        match self.send_call(json!({ "type": "work/send", "message": message })).await {
            Ok(call) => {
                self.requests.insert(id.to_string(), reply);
                self.acks.insert(call, id.to_string());
            }
            Err(err) => {
                let _ = reply.send(Err(err));
            }
        }
    }

    /// Reads frames until the call's answer arrives, keeping app-server messages for `events`.
    async fn handshake_call(&mut self, message: Value, events: &mpsc::UnboundedSender<Value>) -> Result<Value, String> {
        let rx = self.call(message).await?;
        self.wait(rx, events).await
    }

    async fn wait(&mut self, mut rx: oneshot::Receiver<Result<Value, String>>, events: &mpsc::UnboundedSender<Value>) -> Result<Value, String> {
        loop {
            if let Ok(result) = rx.try_recv() {
                return result;
            }
            let Some(frame) = self.socket.next().await else { return Err("ling web closed the connection".to_string()) };
            let text = match frame.map_err(|err| err.to_string())? {
                Message::Text(text) => text.as_str().to_string(),
                Message::Close(_) => return Err("ling web closed the connection".to_string()),
                _ => continue,
            };
            if !self.dispatch(&text, events) {
                return Err("the agent's server stopped".to_string());
            }
        }
    }

    /// Handles one frame. Returns false when the agent's server went away.
    fn dispatch(&mut self, text: &str, events: &mpsc::UnboundedSender<Value>) -> bool {
        match parse(text) {
            Frame::Answer(call, result) => {
                // A refused work/send means its request will get no response: fail it now.
                if let Some(id) = self.acks.remove(&call)
                    && let Err(error) = &result
                    && let Some(reply) = self.requests.remove(&id)
                {
                    let _ = reply.send(Err(error.clone()));
                }
                if let Some(tx) = self.calls.remove(&call) {
                    let _ = tx.send(result);
                } else if let Err(error) = result {
                    eprintln!("ling chat: ling web refused a message: {error}");
                }
                true
            }
            Frame::Message(message) => {
                let is_response = message.get("method").is_none() && message.get("id").is_some();
                if is_response {
                    let key = message.get("id").map(|id| id.as_u64().map(|n| n.to_string()).unwrap_or_else(|| id.to_string())).unwrap_or_default();
                    if let Some(reply) = self.requests.remove(&key) {
                        let result = match message.get("error") {
                            Some(error) => Err(error.get("message").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| error.to_string())),
                            None => Ok(message.get("result").cloned().unwrap_or(Value::Null)),
                        };
                        let _ = reply.send(result);
                    }
                } else {
                    let _ = events.send(message);
                }
                true
            }
            Frame::Exit => false,
            Frame::Other => true,
        }
    }

    fn fail_all(&mut self, error: &str) {
        for (_, tx) in self.calls.drain() {
            let _ = tx.send(Err(error.to_string()));
        }
        for (_, tx) in self.requests.drain() {
            let _ = tx.send(Err(error.to_string()));
        }
        self.acks.clear();
    }
}

async fn connect(target: &WebTarget, events: &mpsc::UnboundedSender<Value>) -> Result<Session, String> {
    let socket = open(target).await?;
    let mut session = Session { socket, next_call: 0, next_id: 0, calls: HashMap::new(), requests: HashMap::new(), acks: HashMap::new() };
    session.handshake_call(json!({ "type": "work/start" }), events).await?;
    let (tx, rx) = oneshot::channel();
    let info = json!({ "clientInfo": { "name": "ling-chat", "title": null, "version": env!("CARGO_PKG_VERSION") } });
    session.request("initialize", info, tx).await;
    session.wait(rx, events).await?;
    session.handshake_call(json!({ "type": "work/send", "message": { "method": "initialized" } }), events).await?;
    Ok(session)
}

/// Serves `commands` over `ling web` until the hub drops its handle, reconnecting as needed.
pub async fn run_link(target: WebTarget, mut commands: mpsc::UnboundedReceiver<AgentCommand>, events: mpsc::UnboundedSender<Value>) {
    let mut backoff = Duration::from_secs(1);
    loop {
        let mut session = match connect(&target, &events).await {
            Ok(session) => session,
            Err(err) => {
                eprintln!("ling chat: could not reach ling web ({err}); retrying in {}s", backoff.as_secs());
                // Requests made meanwhile fail fast rather than wait for a connection.
                let deadline = tokio::time::sleep(backoff);
                tokio::pin!(deadline);
                loop {
                    tokio::select! {
                        () = &mut deadline => break,
                        command = commands.recv() => match command {
                            None => return,
                            Some(AgentCommand::Request { reply, .. }) => { let _ = reply.send(Err(format!("ling web is not reachable: {err}"))); }
                            Some(AgentCommand::Respond { .. }) => {}
                        },
                    }
                }
                backoff = (backoff * 2).min(Duration::from_secs(60));
                continue;
            }
        };
        backoff = Duration::from_secs(1);
        let _ = events.send(json!({ "method": CONNECTED }));
        loop {
            tokio::select! {
                command = commands.recv() => match command {
                    None => {
                        let _ = session.socket.close(None).await;
                        return;
                    }
                    Some(AgentCommand::Request { method, params, reply }) => session.request(&method, params, reply).await,
                    Some(AgentCommand::Respond { id, result }) => {
                        let message = json!({ "id": id, "result": result });
                        if let Err(err) = session.send_call(json!({ "type": "work/send", "message": message })).await {
                            eprintln!("ling chat: could not answer the agent: {err}");
                        }
                    }
                },
                frame = session.socket.next() => {
                    let text = match frame {
                        Some(Ok(Message::Text(text))) => text.as_str().to_string(),
                        Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                        Some(Ok(_)) => continue,
                    };
                    if !session.dispatch(&text, &events) {
                        break;
                    }
                }
            }
        }
        session.fail_all("the connection to ling web was lost");
        eprintln!("ling chat: the connection to ling web closed; signing in again");
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frames_are_told_apart() {
        assert!(matches!(parse(r#"{"answer":3,"result":null}"#), Frame::Answer(3, Ok(Value::Null))));
        assert!(matches!(parse(r#"{"answer":4,"error":"no"}"#), Frame::Answer(4, Err(e)) if e == "no"));
        assert!(matches!(parse(r#"{"event":{"channel":"work://message","payload":{"method":"turn/started"}}}"#), Frame::Message(_)));
        assert!(matches!(parse(r#"{"event":{"channel":"work://exit","payload":null}}"#), Frame::Exit));
        assert!(matches!(parse(r#"{"event":{"channel":"work://stderr","payload":"waiting"}}"#), Frame::Other));
        assert!(matches!(parse("not json"), Frame::Other));
    }
}
