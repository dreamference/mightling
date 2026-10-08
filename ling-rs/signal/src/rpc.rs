//! signal-cli's JSON-RPC interface on stdio (`signal-cli … jsonRpc`; specs/DREAMFERENCE_MIGHTLING_SIGNAL.md
//! §2): one JSON object per line each way. Requests carry an `id` and get a response with it;
//! incoming messages arrive as `receive` notifications.

use std::collections::HashMap;
use std::process::Stdio;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::AtomicU64;
use std::sync::atomic::Ordering;

use serde_json::Value;
use serde_json::json;
use tokio::io::AsyncBufReadExt;
use tokio::io::AsyncWriteExt;
use tokio::io::BufReader;
use tokio::process::Child;
use tokio::process::ChildStdin;
use tokio::process::Command;
use tokio::sync::mpsc;
use tokio::sync::oneshot;

use crate::format::Styled;

type Waiters = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>>;

/// How signal-cli is started.
#[derive(Clone, Debug)]
pub struct Launch {
    pub program: std::path::PathBuf,
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
}

impl Launch {
    /// The daemon's command line: its data under `config`, the account, keys trusted on first use
    /// only (a changed key must be trusted by hand, §5.3), stories ignored. In linked mode
    /// attachments are not downloaded at all; the owner's are fetched one by one.
    pub fn json_rpc(program: std::path::PathBuf, config: &std::path::Path, account: &str, linked: bool, env: Vec<(String, String)>) -> Launch {
        let mut args = vec![
            "--config".to_string(),
            config.to_string_lossy().into_owned(),
            "-a".to_string(),
            account.to_string(),
            "--trust-new-identities".to_string(),
            "on-first-use".to_string(),
            "jsonRpc".to_string(),
            "--receive-mode".to_string(),
            "on-start".to_string(),
            "--ignore-stories".to_string(),
            "--ignore-avatars".to_string(),
            "--ignore-stickers".to_string(),
        ];
        // Linked to the owner's account, every conversation's attachments would otherwise be
        // downloaded into the bridge's state. None is: the owner's own are fetched one by one.
        if linked {
            args.push("--ignore-attachments".to_string());
        }
        Launch { program, args, env }
    }
}

/// Lines in signal-cli's log that mean this device is no longer on the account: unlinked from the
/// phone, or the account registered again (§4.2, "Unlinking"). Matched in lower case. Taken from
/// the exception names signal-cli logs; not yet seen on a real account (§12).
/// Only the server refusing this device's own credentials counts: "not registered" is also what
/// signal-cli logs for a recipient without Signal, which says nothing about this device.
pub const DEAUTHORIZED_MARKERS: &[&str] = &["authorizationfailedexception", "authorization failed"];

pub fn deauthorized(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    DEAUTHORIZED_MARKERS.iter().any(|marker| lower.contains(marker))
}

/// A running signal-cli.
pub struct SignalCli {
    /// Set once signal-cli logs that this device is no longer on the account.
    pub deauthorized: Arc<std::sync::atomic::AtomicBool>,
    stdin: tokio::sync::Mutex<ChildStdin>,
    waiters: Waiters,
    next_id: AtomicU64,
    child: tokio::sync::Mutex<Child>,
}

/// One line of a request.
pub fn request_line(id: u64, method: &str, params: &Value) -> String {
    json!({ "jsonrpc": "2.0", "id": id, "method": method, "params": params }).to_string()
}

/// What one line from signal-cli is.
#[derive(Debug, PartialEq)]
pub enum Line {
    Response { id: u64, result: Result<Value, String> },
    /// A `receive` notification's params.
    Receive(Value),
    Other,
}

pub fn classify(line: &str) -> Line {
    let Ok(value) = serde_json::from_str::<Value>(line) else { return Line::Other };
    if let Some(id) = value.get("id").and_then(Value::as_u64)
        && value.get("method").is_none()
    {
        let result = match value.get("error") {
            Some(error) if !error.is_null() => Err(error.get("message").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| error.to_string())),
            _ => Ok(value.get("result").cloned().unwrap_or(Value::Null)),
        };
        return Line::Response { id, result };
    }
    if value.get("method").and_then(Value::as_str) == Some("receive") {
        return Line::Receive(value.get("params").cloned().unwrap_or(Value::Null));
    }
    Line::Other
}

impl SignalCli {
    /// Starts signal-cli; incoming `receive` params arrive on the returned channel, which closes when
    /// signal-cli exits.
    pub fn spawn(launch: &Launch) -> std::io::Result<(SignalCli, mpsc::UnboundedReceiver<Value>)> {
        let mut command = Command::new(&launch.program);
        command.args(&launch.args).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
        for (key, value) in &launch.env {
            command.env(key, value);
        }
        let mut child = command.spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| std::io::Error::other("no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| std::io::Error::other("no stdout"))?;
        let stderr = child.stderr.take().ok_or_else(|| std::io::Error::other("no stderr"))?;
        let waiters: Waiters = Arc::new(Mutex::new(HashMap::new()));
        let (tx, rx) = mpsc::unbounded_channel();
        let reader_waiters = waiters.clone();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                match classify(&line) {
                    Line::Response { id, result } => {
                        let waiter = reader_waiters.lock().unwrap_or_else(|p| p.into_inner()).remove(&id);
                        if let Some(waiter) = waiter {
                            let _ = waiter.send(result);
                        }
                    }
                    Line::Receive(params) => {
                        let _ = tx.send(params);
                    }
                    Line::Other => {}
                }
            }
            // signal-cli is gone: every waiting request fails.
            for (_, waiter) in reader_waiters.lock().unwrap_or_else(|p| p.into_inner()).drain() {
                let _ = waiter.send(Err("signal-cli exited".to_string()));
            }
        });
        let deauthorized_flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let flag = deauthorized_flag.clone();
        tokio::spawn(async move {
            // signal-cli's own log goes to the journal, line by line; it never carries message text
            // at the default log level.
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if deauthorized(&line) {
                    flag.store(true, Ordering::Relaxed);
                }
                eprintln!("signal-cli: {line}");
            }
        });
        let cli = SignalCli {
            deauthorized: deauthorized_flag,
            stdin: tokio::sync::Mutex::new(stdin),
            waiters,
            next_id: AtomicU64::new(1),
            child: tokio::sync::Mutex::new(child),
        };
        Ok((cli, rx))
    }

    /// One request; waits for its response.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        self.waiters.lock().unwrap_or_else(|p| p.into_inner()).insert(id, tx);
        let mut line = request_line(id, method, &params);
        line.push('\n');
        {
            let mut stdin = self.stdin.lock().await;
            if let Err(err) = stdin.write_all(line.as_bytes()).await {
                self.waiters.lock().unwrap_or_else(|p| p.into_inner()).remove(&id);
                return Err(format!("could not write to signal-cli: {err}"));
            }
            let _ = stdin.flush().await;
        }
        match tokio::time::timeout(std::time::Duration::from_secs(120), rx).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("signal-cli exited".to_string()),
            Err(_) => {
                self.waiters.lock().unwrap_or_else(|p| p.into_inner()).remove(&id);
                Err(format!("signal-cli did not answer {method} in two minutes"))
            }
        }
    }

    pub async fn kill(&self) {
        let _ = self.child.lock().await.kill().await;
    }
}

/// `send` params for one message to one recipient (by account id), with its styles and files.
pub fn send_params(recipient: &str, message: &Styled, attachments: &[String]) -> Value {
    let mut params = json!({ "recipient": [recipient], "message": message.text });
    if !message.styles.is_empty() {
        params["textStyle"] = json!(message.style_arguments());
    }
    if !attachments.is_empty() {
        params["attachments"] = json!(attachments);
    }
    params
}

/// `send` params for a message to the account's own Note to Self (linked mode).
pub fn note_to_self_params(message: &Styled, attachments: &[String]) -> Value {
    let mut params = json!({ "noteToSelf": true, "message": message.text });
    if !message.styles.is_empty() {
        params["textStyle"] = json!(message.style_arguments());
    }
    if !attachments.is_empty() {
        params["attachments"] = json!(attachments);
    }
    params
}

/// `getAttachment` params: one attachment of a Note to Self message, which linked mode does not
/// download with the rest (`--ignore-attachments`). The answer carries the file as base64 `data`.
pub fn get_attachment_params(id: &str, own_number: &str) -> Value {
    json!({ "id": id, "recipient": own_number })
}

/// Standard base64, as `getAttachment` returns a file; None if it is not base64.
pub fn base64_decode(text: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(text.len() * 3 / 4);
    let mut buffer: u32 = 0;
    let mut bits = 0;
    for byte in text.bytes() {
        let value = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' => break,
            b'\r' | b'\n' | b' ' => continue,
            _ => return None,
        };
        buffer = (buffer << 6) | u32::from(value);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buffer >> bits) as u8);
            buffer &= (1 << bits) - 1;
        }
    }
    Some(out)
}

pub fn typing_params(recipient: &str, typing: bool) -> Value {
    if typing { json!({ "recipient": [recipient] }) } else { json!({ "recipient": [recipient], "stop": true }) }
}

pub fn receipt_params(recipient: &str, timestamp: u64) -> Value {
    json!({ "recipient": recipient, "targetTimestamp": [timestamp], "type": "read" })
}

/// The fingerprint signal-cli holds for one account id, from `listIdentities`' result, and whether
/// it is trusted. Unknown or untrusted keys give None: the gate then refuses.
pub fn trusted_fingerprint(identities: &Value, aci: &str) -> Option<String> {
    identities.as_array()?.iter().find_map(|identity| {
        let matches = identity.get("uuid").and_then(Value::as_str) == Some(aci);
        let level = identity.get("trustLevel").and_then(Value::as_str).unwrap_or_default();
        let trusted = matches!(level, "TRUSTED_VERIFIED" | "TRUSTED_UNVERIFIED");
        (matches && trusted).then(|| identity.get("fingerprint").and_then(Value::as_str).map(str::to_string)).flatten()
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::format::render;

    #[test]
    fn lines_are_told_apart() {
        assert_eq!(classify(r#"{"jsonrpc":"2.0","result":{"timestamp":9},"id":4}"#), Line::Response { id: 4, result: Ok(json!({"timestamp": 9})) });
        assert_eq!(
            classify(r#"{"jsonrpc":"2.0","error":{"code":-1,"message":"Untrusted identity"},"id":5}"#),
            Line::Response { id: 5, result: Err("Untrusted identity".to_string()) }
        );
        assert_eq!(classify(r#"{"jsonrpc":"2.0","method":"receive","params":{"envelope":{}}}"#), Line::Receive(json!({"envelope": {}})));
        assert_eq!(classify("not json"), Line::Other);
        assert_eq!(classify(r#"{"jsonrpc":"2.0","method":"somethingElse","params":{}}"#), Line::Other);
    }

    #[test]
    fn send_carries_styles_and_files() {
        let params = send_params("aci-1", &render("a **b**"), &["/tmp/answer.md".to_string()]);
        assert_eq!(params, json!({"recipient": ["aci-1"], "message": "a b", "textStyle": ["2:1:BOLD"], "attachments": ["/tmp/answer.md"]}));
        assert!(send_params("aci-1", &render("plain"), &[]).get("textStyle").is_none());
        assert_eq!(typing_params("a", false), json!({"recipient": ["a"], "stop": true}));
        assert_eq!(receipt_params("a", 7), json!({"recipient": "a", "targetTimestamp": [7], "type": "read"}));
    }

    #[test]
    fn note_to_self_has_no_recipient_and_attachments_decode() {
        let params = note_to_self_params(&render("**ok**"), &[]);
        assert_eq!(params, json!({"noteToSelf": true, "message": "ok", "textStyle": ["0:2:BOLD"]}));
        assert!(params.get("recipient").is_none());
        assert_eq!(get_attachment_params("abc", "+1555"), json!({"id": "abc", "recipient": "+1555"}));
        assert_eq!(base64_decode("aGVsbG8gd29ybGQ=").unwrap(), b"hello world");
        assert_eq!(base64_decode("AAEC/w==").unwrap(), vec![0, 1, 2, 255]);
        assert!(base64_decode("not*base64").is_none());
    }

    #[test]
    fn an_unlinked_device_is_recognised_in_the_log() {
        assert!(deauthorized("WARN  ... org.whispersystems.signalservice.api.push.exceptions.AuthorizationFailedException: Authorization failed!"));
        assert!(!deauthorized("ERROR User +1555 is not registered."), "a recipient without Signal is not this device");
        assert!(!deauthorized("INFO  Received message from +1555"));
    }

    #[test]
    fn only_a_trusted_key_has_a_fingerprint() {
        let list = json!([
            {"number": "+1", "uuid": "a", "fingerprint": "f-a", "trustLevel": "TRUSTED_VERIFIED"},
            {"number": "+2", "uuid": "b", "fingerprint": "f-b", "trustLevel": "UNTRUSTED"},
        ]);
        assert_eq!(trusted_fingerprint(&list, "a").as_deref(), Some("f-a"));
        assert_eq!(trusted_fingerprint(&list, "b"), None);
        assert_eq!(trusted_fingerprint(&list, "c"), None);
    }

    #[tokio::test]
    async fn a_stand_in_signal_cli_answers_and_delivers() {
        // A shell script in signal-cli's place: it announces one message, then answers each
        // request with its own id.
        let script = r#"
echo '{"jsonrpc":"2.0","method":"receive","params":{"envelope":{"sourceUuid":"o","timestamp":3,"dataMessage":{"message":"hi"}}}}'
while IFS= read -r line; do
  id=$(printf '%s' "$line" | sed -n 's/.*"id":\([0-9]*\).*/\1/p')
  echo "{\"jsonrpc\":\"2.0\",\"result\":{\"timestamp\":$id},\"id\":$id}"
done
"#;
        let launch = Launch { program: "sh".into(), args: vec!["-c".to_string(), script.to_string()], env: vec![] };
        let (cli, mut incoming) = SignalCli::spawn(&launch).unwrap();
        let params = incoming.recv().await.unwrap();
        assert_eq!(params.pointer("/envelope/dataMessage/message"), Some(&json!("hi")));
        let first = cli.call("send", json!({"recipient": ["o"], "message": "x"})).await.unwrap();
        let second = cli.call("sendTyping", json!({"recipient": ["o"]})).await.unwrap();
        assert_eq!((first["timestamp"].as_u64(), second["timestamp"].as_u64()), (Some(1), Some(2)));
        cli.kill().await;
        assert!(cli.call("send", json!({})).await.is_err());
    }

    #[test]
    fn the_daemon_trusts_new_keys_on_first_use_only() {
        let launch = Launch::json_rpc("/opt/signal-cli".into(), std::path::Path::new("/var/lib/m/signal-cli"), "+15550000", false, vec![]);
        let args = launch.args.join(" ");
        assert!(args.contains("--trust-new-identities on-first-use"));
        assert!(args.contains("-a +15550000 "));
        assert!(args.contains("jsonRpc"));
        assert!(!args.contains("--ignore-attachments"));
        let linked = Launch::json_rpc("/x".into(), std::path::Path::new("/y"), "+1", true, vec![]);
        assert!(linked.args.contains(&"--ignore-attachments".to_string()));
        assert!(args.contains("--ignore-avatars") && args.contains("--ignore-stickers"));
        assert!(!args.contains("--send-read-receipts"), "read receipts are never sent for every message");
    }
}
