//! The agent's side of the hub's routes (`ling_web_server::sync_hub`): plain HTTP to the node's
//! `ling web` port, the device secret as a bearer credential, and the server's own address as the
//! `Origin` its guard asks of every change. No proxy is ever used: the hub is the user's own node.

use std::time::Duration;

use serde_json::Value;
use serde_json::json;

/// What an upload came to.
#[derive(Debug, PartialEq, Eq)]
pub enum Uploaded {
    Taken { length: u64, hash: String },
    /// The hub holds something else at that offset: here is its length and hash.
    Mismatch { length: u64, hash: String },
    /// Another device owns the thread: publish under these ids.
    Owned { fork: String, thread: String },
    /// The thread was deleted.
    Gone,
}

/// What a request for the hub says besides the bytes.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct UploadParams {
    pub offset: u64,
    pub prefix: String,
    pub thread: Option<String>,
    pub file: Option<String>,
    pub archived: bool,
    pub fork_of: Option<String>,
}

pub struct HubClient {
    base: String,
    secret: String,
    http: reqwest::Client,
}

fn client(timeout: Duration) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(5))
        .timeout(timeout)
        .build()
        .map_err(|err| format!("could not make an HTTP client: {err}"))
}

/// The text of a refusal, from its JSON `error` when it has one.
async fn refusal(response: reqwest::Response) -> String {
    let status = response.status();
    let text = response.text().await.unwrap_or_default();
    let said = serde_json::from_str::<Value>(&text).ok().and_then(|value| value.get("error").and_then(Value::as_str).map(str::to_string));
    format!("the hub answered {status}: {}", said.unwrap_or_else(|| text.trim().to_string()))
}

/// Pairs this device with the hub at `base` (`http://host:port`) using a code from `ling web pair`.
/// Returns the device id and its secret.
pub async fn pair(base: &str, code: &str, name: &str) -> Result<(String, String), String> {
    let response = client(Duration::from_secs(30))?
        .post(format!("{base}/api/sync/pair"))
        .header("Origin", base)
        .json(&json!({ "code": code, "name": name }))
        .send()
        .await
        .map_err(|err| format!("could not reach the node's ling web at {base}: {err}"))?;
    if response.status() == reqwest::StatusCode::NOT_FOUND {
        return Err("the node's hub is off: run `ling sync enable` on the node first".to_string());
    }
    if !response.status().is_success() {
        return Err(refusal(response).await);
    }
    let value: Value = response.json().await.map_err(|err| err.to_string())?;
    let device = value.get("device").and_then(Value::as_str).ok_or("the hub gave no device id")?;
    let secret = value.get("secret").and_then(Value::as_str).ok_or("the hub gave no secret")?;
    Ok((device.to_string(), secret.to_string()))
}

impl HubClient {
    pub fn new(base: &str, secret: &str) -> Result<HubClient, String> {
        Ok(HubClient { base: base.trim_end_matches('/').to_string(), secret: secret.to_string(), http: client(Duration::from_secs(60))? })
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    fn request(&self, method: reqwest::Method, path: &str) -> reqwest::RequestBuilder {
        self.http.request(method, format!("{}{path}", self.base)).bearer_auth(&self.secret).header("Origin", &self.base)
    }

    async fn send(&self, builder: reqwest::RequestBuilder) -> Result<reqwest::Response, String> {
        builder.send().await.map_err(|err| format!("could not reach the hub at {}: {err}", self.base))
    }

    pub async fn status(&self) -> Result<Value, String> {
        let response = self.send(self.request(reqwest::Method::GET, "/api/sync/status")).await?;
        if !response.status().is_success() {
            return Err(refusal(response).await);
        }
        response.json().await.map_err(|err| err.to_string())
    }

    /// Every change after `since`; with `wait`, held by the hub until there is one.
    pub async fn manifest(&self, since: u64, wait: u64) -> Result<Value, String> {
        let response = self.send(self.request(reqwest::Method::GET, &format!("/api/sync/manifest?since={since}&wait={wait}"))).await?;
        if !response.status().is_success() {
            return Err(refusal(response).await);
        }
        response.json().await.map_err(|err| err.to_string())
    }

    pub async fn upload(&self, rollout: &str, params: &UploadParams, bytes: Vec<u8>) -> Result<Uploaded, String> {
        let mut query: Vec<(&str, String)> = vec![("offset", params.offset.to_string()), ("prefix", params.prefix.clone())];
        if let Some(thread) = &params.thread {
            query.push(("thread", thread.clone()));
        }
        if let Some(file) = &params.file {
            query.push(("file", file.clone()));
        }
        if params.archived {
            query.push(("archived", "1".to_string()));
        }
        if let Some(fork_of) = &params.fork_of {
            query.push(("fork_of", fork_of.clone()));
        }
        let builder = self.request(reqwest::Method::PUT, &format!("/api/sync/rollouts/{rollout}")).query(&query).body(bytes);
        let response = self.send(builder).await?;
        let status = response.status();
        if status == reqwest::StatusCode::GONE {
            return Ok(Uploaded::Gone);
        }
        if !status.is_success() && status != reqwest::StatusCode::CONFLICT {
            return Err(refusal(response).await);
        }
        let value: Value = response.json().await.map_err(|err| err.to_string())?;
        let text = |key: &str| value.get(key).and_then(Value::as_str).unwrap_or_default().to_string();
        let length = value.get("length").and_then(Value::as_u64).unwrap_or(0);
        Ok(match (status.is_success(), value.get("error").and_then(Value::as_str)) {
            (true, _) => Uploaded::Taken { length, hash: text("hash") },
            (false, Some("owned")) => Uploaded::Owned { fork: text("fork"), thread: text("thread") },
            _ => Uploaded::Mismatch { length, hash: text("hash") },
        })
    }

    async fn post(&self, path: &str, body: Value) -> Result<Value, String> {
        let response = self.send(self.request(reqwest::Method::POST, path).json(&body)).await?;
        if !response.status().is_success() {
            return Err(refusal(response).await);
        }
        response.json().await.map_err(|err| err.to_string())
    }

    /// A thread's archive state or name, with this device's clock reading.
    pub async fn set_meta(&self, thread: &str, body: Value) -> Result<Value, String> {
        self.post(&format!("/api/sync/threads/{thread}/meta"), body).await
    }

    /// Deletes a thread everywhere.
    pub async fn delete(&self, thread: &str) -> Result<Value, String> {
        self.post(&format!("/api/sync/threads/{thread}/delete"), json!({})).await
    }

    /// Takes this device's thread off the hub, without deleting it anywhere (`[sync] exclude`).
    pub async fn withdraw(&self, thread: &str) -> Result<Value, String> {
        self.post(&format!("/api/sync/threads/{thread}/withdraw"), json!({})).await
    }

    /// Ends this device's credential on the hub (`ling sync forget`).
    pub async fn forget(&self) -> Result<Value, String> {
        self.post("/api/sync/forget", json!({})).await
    }
}
