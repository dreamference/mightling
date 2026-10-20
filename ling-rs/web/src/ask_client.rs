//! `ling web ask "<question>"`: one Ask thread through a running `ling web`, the way a browser tab
//! goes: it writes a one-time login code, as `ling web open` does, trades it for a session cookie,
//! and opens the bridge with that. It prints the answer as it streams. It exists
//! for scripts and for `ling-admin audit egress --web`, which traces the server while this asks it
//! something; it is not a second chat interface.

use std::path::Path;
use std::time::Duration;

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

/// Signs in as `ling web open` does, without a browser: returns the session cookie (`name=value`).
pub async fn sign_in(state: &Path, port: u16) -> Result<String, String> {
    let code = crate::auth::issue_login_code(state).map_err(|err| format!("could not write a sign-in code: {err}"))?;
    let mut stream = tokio::net::TcpStream::connect(("127.0.0.1", port))
        .await
        .map_err(|err| format!("ling web is not answering on port {port}: {err}"))?;
    let request = format!("GET /login?code={code} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).await.map_err(|err| err.to_string())?;
    let mut response = String::new();
    stream.read_to_string(&mut response).await.map_err(|err| err.to_string())?;
    response
        .split("\r\n\r\n")
        .next()
        .unwrap_or_default()
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.trim().eq_ignore_ascii_case("set-cookie").then(|| value.split(';').next().unwrap_or_default().trim().to_string())
        })
        .ok_or_else(|| "ling web did not sign this client in".to_string())
}

/// What `/api/upload` answered a phone bridge (`read=1`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Saved {
    /// Where it was written in the thread's folder.
    pub path: String,
    /// An image's text, read by the offline reader (ocr.rs).
    pub text: Option<String>,
    /// Whether the served model takes images.
    pub vision: bool,
}

/// Reads `/api/upload`'s answer.
pub fn saved_from(body: &str) -> Result<Saved, String> {
    let value: Value = serde_json::from_str(body).map_err(|err| format!("ling web answered the upload with {body:?}: {err}"))?;
    Ok(Saved {
        path: value.get("path").and_then(Value::as_str).ok_or("ling web named no path for the upload")?.to_string(),
        text: value.get("text").and_then(Value::as_str).map(str::to_string),
        vision: value.get("vision").and_then(Value::as_bool).unwrap_or(false),
    })
}

fn form_escape(text: &str) -> String {
    text.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

/// Puts a phone bridge's attachment into an Ask thread's folder, as a signed-in client
/// (`cookie` from [`sign_in`]): the image's text is read and the model's image support reported.
pub async fn upload(port: u16, cookie: &str, thread: &str, name: &str, image: bool, bytes: &[u8]) -> Result<Saved, String> {
    let kind = if image { "image" } else { "file" };
    let mut request = format!(
        "POST /api/upload?thread={}&kind={kind}&name={}&read=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\nCookie: {cookie}\r\nContent-Type: application/octet-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        form_escape(thread),
        form_escape(name),
        bytes.len()
    )
    .into_bytes();
    request.extend_from_slice(bytes);
    let mut stream = tokio::net::TcpStream::connect(("127.0.0.1", port))
        .await
        .map_err(|err| format!("ling web is not answering on port {port}: {err}"))?;
    stream.write_all(&request).await.map_err(|err| err.to_string())?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).await.map_err(|err| err.to_string())?;
    let split = response.windows(4).position(|w| w == b"\r\n\r\n").ok_or("ling web sent no HTTP response")?;
    let head = String::from_utf8_lossy(&response[..split]).into_owned();
    let body = String::from_utf8_lossy(&response[split + 4..]).trim().to_string();
    let status = head.split_whitespace().nth(1).unwrap_or_default();
    if status != "200" {
        return Err(format!("the upload was refused (HTTP {status}): {body}"));
    }
    saved_from(&body)
}

/// `/cavemode <text>` from a phone bridge, as a signed-in client: what `ling cavemode` printed for
/// `thread` (None before the first question), or why it was refused (cave.rs).
pub async fn cavemode(port: u16, cookie: &str, thread: Option<&str>, text: &str) -> Result<String, String> {
    let body = serde_json::to_vec(&json!({ "command": text, "thread": thread })).map_err(|err| err.to_string())?;
    let mut request = format!(
        "POST /api/cavemode HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\nCookie: {cookie}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    )
    .into_bytes();
    request.extend_from_slice(&body);
    let mut stream = tokio::net::TcpStream::connect(("127.0.0.1", port))
        .await
        .map_err(|err| format!("ling web is not answering on port {port}: {err}"))?;
    stream.write_all(&request).await.map_err(|err| err.to_string())?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).await.map_err(|err| err.to_string())?;
    let split = response.windows(4).position(|w| w == b"\r\n\r\n").ok_or("ling web sent no HTTP response")?;
    let value: Value = serde_json::from_slice(&response[split + 4..]).unwrap_or(Value::Null);
    match value.get("said").and_then(Value::as_str) {
        Some(said) => Ok(said.to_string()),
        None => Err(value.get("error").and_then(Value::as_str).unwrap_or("ling web did not answer").to_string()),
    }
}

/// Asks one question in a new Ask thread; returns the answer, streaming it to stdout.
pub async fn ask(state: &Path, port: u16, question: &str, limit: Duration) -> Result<String, String> {
    let cookie = sign_in(state, port).await?;
    let stream = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.map_err(|err| format!("ling web is not answering on port {port}: {err}"))?;
    let mut request = format!("ws://127.0.0.1:{port}/ws").into_client_request().map_err(|err| err.to_string())?;
    let headers = request.headers_mut();
    let origin = HeaderValue::from_str(&format!("http://127.0.0.1:{port}")).map_err(|err| err.to_string())?;
    headers.insert(header::ORIGIN, origin);
    headers.insert(header::COOKIE, HeaderValue::from_str(&cookie).map_err(|err| err.to_string())?);
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
