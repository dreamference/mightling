//! `ling web ask "<question>"`: one Ask thread through a running `ling web`, the way a browser tab
//! goes, with the owner token instead of a cookie. It prints the answer as it streams. It exists
//! for scripts and for `ling-admin audit egress --web`, which traces the server while this asks it
//! something; it is not a second chat interface.

use std::path::Path;
use std::time::Duration;

use futures::SinkExt;
use futures::StreamExt;
use serde_json::Value;
use serde_json::json;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::http::header;

type Socket = tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>;

struct Client {
    socket: Socket,
    next_call: u64,
    /// Protocol messages that arrived while waiting for something else.
    backlog: Vec<Value>,
}

impl Client {
    async fn frame(&mut self) -> Result<Value, String> {
        loop {
            match self.socket.next().await {
                Some(Ok(Message::Text(text))) => return serde_json::from_str(text.as_str()).map_err(|err| err.to_string()),
                Some(Ok(Message::Close(_))) | None => return Err("ling web closed the connection".to_string()),
                Some(Ok(_)) => continue,
                Some(Err(err)) => return Err(err.to_string()),
            }
        }
    }

    /// Sends one bridge message and waits for its answer, keeping what the server says meanwhile.
    async fn call(&mut self, message: Value) -> Result<Value, String> {
        self.next_call += 1;
        let call = self.next_call;
        self.socket
            .send(Message::Text(json!({ "call": call, "message": message }).to_string().into()))
            .await
            .map_err(|err| err.to_string())?;
        loop {
            let frame = self.frame().await?;
            if frame.get("answer") == Some(&json!(call)) {
                return match frame.get("error") {
                    Some(error) => Err(error.as_str().unwrap_or("refused").to_string()),
                    None => Ok(frame.get("result").cloned().unwrap_or(Value::Null)),
                };
            }
            self.keep(frame)?;
        }
    }

    fn keep(&mut self, frame: Value) -> Result<(), String> {
        match frame.pointer("/event/channel").and_then(Value::as_str) {
            Some("work://message") => self.backlog.push(frame["event"]["payload"].clone()),
            Some("work://exit") => return Err("the agent's server stopped".to_string()),
            Some("work://stderr") => eprintln!("{}", frame["event"]["payload"].as_str().unwrap_or_default()),
            _ => {}
        }
        Ok(())
    }

    /// The next protocol message from the app-server.
    async fn message(&mut self) -> Result<Value, String> {
        loop {
            if !self.backlog.is_empty() {
                return Ok(self.backlog.remove(0));
            }
            let frame = self.frame().await?;
            self.keep(frame)?;
        }
    }

    /// Sends a request and waits for its response.
    async fn request(&mut self, id: u64, method: &str, params: Value) -> Result<Value, String> {
        self.call(json!({ "type": "work/send", "message": { "id": id, "method": method, "params": params } })).await?;
        loop {
            let message = self.message().await?;
            if message.get("id") == Some(&json!(id)) && message.get("method").is_none() {
                if let Some(error) = message.get("error") {
                    return Err(format!("{method}: {error}"));
                }
                return Ok(message.get("result").cloned().unwrap_or(Value::Null));
            }
        }
    }
}

/// Asks one question in a new Ask thread; returns the answer, streaming it to stdout.
pub async fn ask(state: &Path, port: u16, question: &str, limit: Duration) -> Result<String, String> {
    let token = std::fs::read_to_string(state.join("token")).map_err(|err| format!("no owner token: {err}"))?;
    let stream = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.map_err(|err| format!("ling web is not answering on port {port}: {err}"))?;
    let mut request = format!("ws://127.0.0.1:{port}/ws").into_client_request().map_err(|err| err.to_string())?;
    let headers = request.headers_mut();
    let origin = HeaderValue::from_str(&format!("http://127.0.0.1:{port}")).map_err(|err| err.to_string())?;
    headers.insert(header::ORIGIN, origin);
    let bearer = HeaderValue::from_str(&format!("Bearer {}", token.trim())).map_err(|err| err.to_string())?;
    headers.insert(header::AUTHORIZATION, bearer);
    let (socket, _) = tokio_tungstenite::client_async(request, stream).await.map_err(|err| format!("ling web refused the bridge: {err}"))?;
    let mut client = Client { socket, next_call: 0, backlog: Vec::new() };
    tokio::time::timeout(limit, conversation(&mut client, question)).await.map_err(|_| "no answer in time".to_string())?
}

async fn conversation(client: &mut Client, question: &str) -> Result<String, String> {
    client.call(json!({ "type": "work/start" })).await?;
    let info = json!({ "clientInfo": { "name": "ling-web-ask", "title": null, "version": env!("CARGO_PKG_VERSION") } });
    client.request(1, "initialize", info).await?;
    client.call(json!({ "type": "work/send", "message": { "method": "initialized" } })).await?;
    let started = client.request(2, "thread/start", json!({ "prompt": "ask" })).await?;
    let thread = started.pointer("/thread/id").and_then(Value::as_str).ok_or("thread/start named no thread")?.to_string();
    let input = json!([{ "type": "text", "text": question, "text_elements": [] }]);
    client.request(3, "turn/start", json!({ "threadId": thread, "input": input })).await?;
    let mut answer = String::new();
    loop {
        let message = client.message().await?;
        let params = message.get("params").cloned().unwrap_or(Value::Null);
        let of_thread = params.get("threadId").and_then(Value::as_str) == Some(thread.as_str());
        match message.get("method").and_then(Value::as_str) {
            Some("item/agentMessage/delta") if of_thread => {
                let delta = params.get("delta").and_then(Value::as_str).unwrap_or_default();
                print!("{delta}");
                use std::io::Write;
                let _ = std::io::stdout().flush();
                answer.push_str(delta);
            }
            Some("turn/completed") if of_thread => break,
            Some("error") if of_thread && params.get("willRetry") != Some(&Value::Bool(true)) => {
                return Err(format!("the turn failed: {params}"));
            }
            _ => {}
        }
    }
    println!();
    Ok(answer)
}
