//! The daemon end to end, with no Signal, no network beyond loopback and no real `ling web`:
//! signal-cli is a shell script that logs every request and delivers two messages (a stranger's,
//! then the owner's), and `ling web` is a WebSocket server in this test that plays one Ask thread.

use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use futures::SinkExt;
use futures::StreamExt;
use ling_signal::gate::Mode;
use ling_signal::gate::Owner;
use ling_signal::state::Config;
use serde_json::Value;
use serde_json::json;
use tokio_tungstenite::tungstenite::Message;

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64
}

/// The two messages of the dedicated-mode tests: a stranger's, then the owner's.
fn dedicated_messages() -> Vec<Value> {
    vec![
        json!({"jsonrpc":"2.0","method":"receive","params":{"envelope":{"sourceUuid":"stranger","sourceDevice":1,"timestamp":1000,"dataMessage":{"message":"who are you?"}}}}),
        json!({"jsonrpc":"2.0","method":"receive","params":{"envelope":{"sourceUuid":"owner","sourceDevice":1,"timestamp":now_ms(),"dataMessage":{"message":"what is 2+2?"}}}}),
    ]
}

/// A stand-in for signal-cli: every request line goes to `log`; `listIdentities` trusts the owner;
/// everything else answers with a timestamp. `messages` arrive after two seconds.
fn fake_signal_cli(dir: &Path, log: &Path, messages: &[Value]) -> PathBuf {
    let path = dir.join("signal-cli");
    let incoming = dir.join("incoming.jsonl");
    std::fs::write(&incoming, messages.iter().map(|m| m.to_string() + "\n").collect::<String>()).unwrap();
    let script = format!(
        r#"#!/bin/sh
LOG='{log}'
(sleep 2; cat '{incoming}') &
while IFS= read -r line; do
  printf '%s\n' "$line" >> "$LOG"
  id=$(printf '%s' "$line" | sed -n 's/.*"id":\([0-9]*\).*/\1/p')
  case "$line" in
    *'"method":"listIdentities"'*) echo "{{\"jsonrpc\":\"2.0\",\"result\":[{{\"uuid\":\"owner\",\"fingerprint\":\"fp-owner\",\"trustLevel\":\"TRUSTED_VERIFIED\"}},{{\"uuid\":\"stranger\",\"fingerprint\":\"fp-s\",\"trustLevel\":\"TRUSTED_UNVERIFIED\"}}],\"id\":$id}}" ;;
    *) echo "{{\"jsonrpc\":\"2.0\",\"result\":{{\"timestamp\":$id}},\"id\":$id}}" ;;
  esac
done
"#,
        log = log.display(),
        incoming = incoming.display()
    );
    std::fs::write(&path, script).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    path
}

fn event(payload: Value) -> Message {
    Message::Text(json!({ "event": { "channel": "work://message", "payload": payload } }).to_string().into())
}

/// A stand-in for `ling web`'s `/ws`: answers every call, and plays one turn for `turn/start`.
/// Records what the bridge asked for on `seen`.
async fn fake_ling_web(seen: tokio::sync::mpsc::UnboundedSender<Value>, airgap: &'static str) -> u16 {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let seen = seen.clone();
            tokio::spawn(async move {
                // The air-gap poll is plain HTTP: answer it with `off`. Anything else is the bridge.
                let mut peek = [0u8; 32];
                let read = stream.peek(&mut peek).await.unwrap_or(0);
                if peek[..read].starts_with(b"GET /api/airgapped") {
                    use tokio::io::AsyncWriteExt;
                    let mut stream = stream;
                    let mut request = vec![0u8; 4096];
                    let _ = tokio::io::AsyncReadExt::read(&mut stream, &mut request).await;
                    let body = format!(r#"{{"level":"{airgap}","source":"test"}}"#);
                    let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                    let _ = stream.write_all(reply.as_bytes()).await;
                    return;
                }
                let Ok(mut socket) = tokio_tungstenite::accept_async(stream).await else { return };
                while let Some(Ok(Message::Text(text))) = socket.next().await {
                    let frame: Value = serde_json::from_str(text.as_str()).unwrap();
                    let call = frame["call"].clone();
                    let message = frame["message"].clone();
                    let _ = seen.send(message.clone());
                    socket.send(Message::Text(json!({ "answer": call, "result": {} }).to_string().into())).await.unwrap();
                    let inner = &message["message"];
                    let id = inner["id"].clone();
                    match inner["method"].as_str() {
                        Some("initialize") => socket.send(event(json!({ "id": id, "result": {} }))).await.unwrap(),
                        Some("thread/start") => socket.send(event(json!({ "id": id, "result": { "thread": { "id": "th-1" } } }))).await.unwrap(),
                        Some("turn/start") => {
                            for payload in [
                                json!({ "id": id, "result": { "turn": { "id": "tu-1" } } }),
                                json!({ "method": "turn/started", "params": { "threadId": "th-1", "turn": { "id": "tu-1" } } }),
                                json!({ "method": "item/agentMessage/delta", "params": { "threadId": "th-1", "delta": "The answer is " } }),
                                json!({ "method": "item/agentMessage/delta", "params": { "threadId": "th-1", "delta": "**4**." } }),
                                json!({ "method": "turn/completed", "params": { "threadId": "th-1", "turn": { "status": "completed" } } }),
                            ] {
                                socket.send(event(payload)).await.unwrap();
                            }
                        }
                        _ => {}
                    }
                }
            });
        }
    });
    port
}

#[tokio::test]
async fn the_owners_question_is_answered_through_ling_web_and_a_strangers_is_not() {
    let dir = Scratch(std::env::temp_dir().join(format!("ling-signal-daemon-{}", std::process::id())));
    let _ = std::fs::remove_dir_all(&dir.0);
    let state = dir.0.join("state");
    std::fs::create_dir_all(&state).unwrap();
    let log = dir.0.join("signal-cli.log");
    let (seen_tx, mut seen) = tokio::sync::mpsc::unbounded_channel();
    let port = fake_ling_web(seen_tx, "off").await;
    Config {
        mode: Mode::Dedicated,
        account: "+15550000".to_string(),
        own_uuid: Some("bridge".to_string()),
        owner: Some(Owner { aci: "owner".to_string(), fingerprint: "fp-owner".to_string() }),
        binding: None,
        port,
        node: "test-node".to_string(),
        signal_cli: fake_signal_cli(&dir.0, &log, &dedicated_messages()),
        signal_cli_env: vec![],
        signal_cli_version: "0.14.9".to_string(),
        vision: false,
    }
    .save(&state)
    .unwrap();
    ling_signal::state::save_cookie(&state, "mightling_device=test").unwrap();
    let runtime = dir.0.join("run");
    let daemon = {
        let (state, runtime) = (state.clone(), runtime.clone());
        tokio::spawn(async move { ling_signal::serve::serve(&state, Some(&runtime)).await })
    };

    // The answer reaches signal-cli as one styled `send` to the owner.
    let deadline = tokio::time::Instant::now() + Duration::from_secs(20);
    let sends = loop {
        let text = std::fs::read_to_string(&log).unwrap_or_default();
        let sends: Vec<Value> = text
            .lines()
            .filter_map(|line| serde_json::from_str::<Value>(line).ok())
            .filter(|v| v["method"] == "send")
            .collect();
        if sends.iter().any(|s| s["params"]["message"] == "The answer is 4.") {
            break sends;
        }
        assert!(tokio::time::Instant::now() < deadline, "no answer was sent; signal-cli saw:\n{text}");
        tokio::time::sleep(Duration::from_millis(200)).await;
    };
    let answer = sends.iter().find(|s| s["params"]["message"] == "The answer is 4.").unwrap();
    assert_eq!(answer["params"]["recipient"], json!(["owner"]));
    assert_eq!(answer["params"]["textStyle"], json!(["14:1:BOLD"]));
    assert!(sends.iter().all(|s| s["params"]["recipient"] == json!(["owner"])), "nothing goes to a stranger: {sends:?}");
    let all = std::fs::read_to_string(&log).unwrap();
    assert!(all.contains(r#""method":"sendReceipt""#), "the owner's message is marked read");
    assert!(all.contains(r#""method":"sendTyping""#), "the owner sees typing");

    // ling web was asked for an Ask thread and one turn with the owner's words.
    let mut messages = Vec::new();
    while let Ok(message) = seen.try_recv() {
        messages.push(message);
    }
    assert_eq!(messages[0], json!({ "type": "work/start" }));
    let methods: Vec<&str> = messages.iter().filter_map(|m| m["message"]["method"].as_str()).collect();
    assert_eq!(methods, vec!["initialize", "initialized", "thread/start", "turn/start"]);
    let start = messages.iter().find(|m| m["message"]["method"] == "thread/start").unwrap();
    assert_eq!(start["message"]["params"], json!({ "prompt": "ask" }));
    let turn = messages.iter().find(|m| m["message"]["method"] == "turn/start").unwrap();
    assert_eq!(turn["message"]["params"]["input"][0]["text"], "what is 2+2?");

    // The state remembers the thread, and the status counts the stranger without naming them.
    let conversation: Value = serde_json::from_str(&std::fs::read_to_string(state.join("conversation.json")).unwrap()).unwrap();
    assert_eq!(conversation["current"], "th-1");
    assert_eq!(conversation["ignored"], 1);
    tokio::time::sleep(Duration::from_millis(5_200)).await;
    let status = std::fs::read_to_string(runtime.join("status.json")).unwrap();
    assert!(status.contains(r#""strangersIgnored":1"#), "{status}");
    assert!(!status.contains("stranger\"") && !status.contains("2+2"), "no ids or text in the status: {status}");
    daemon.abort();
}

#[tokio::test]
async fn at_air_gap_on_the_owner_gets_nothing_not_even_a_receipt() {
    let dir = Scratch(std::env::temp_dir().join(format!("ling-signal-airgap-{}", std::process::id())));
    let _ = std::fs::remove_dir_all(&dir.0);
    let state = dir.0.join("state");
    std::fs::create_dir_all(&state).unwrap();
    let log = dir.0.join("signal-cli.log");
    let (seen_tx, mut seen) = tokio::sync::mpsc::unbounded_channel();
    let port = fake_ling_web(seen_tx, "on").await;
    Config {
        mode: Mode::Dedicated,
        account: "+15550000".to_string(),
        own_uuid: None,
        owner: Some(Owner { aci: "owner".to_string(), fingerprint: "fp-owner".to_string() }),
        binding: None,
        port,
        node: "test-node".to_string(),
        signal_cli: fake_signal_cli(&dir.0, &log, &dedicated_messages()),
        signal_cli_env: vec![],
        signal_cli_version: "0.14.9".to_string(),
        vision: false,
    }
    .save(&state)
    .unwrap();
    ling_signal::state::save_cookie(&state, "mightling_device=test").unwrap();
    let daemon = {
        let state = state.clone();
        tokio::spawn(async move { ling_signal::serve::serve(&state, None).await })
    };
    // The owner's message arrives after two seconds; give the bridge time to (not) answer it.
    tokio::time::sleep(Duration::from_secs(6)).await;
    let all = std::fs::read_to_string(&log).unwrap_or_default();
    for method in ["\"send\"", "\"sendReceipt\"", "\"sendTyping\""] {
        assert!(!all.contains(&format!("\"method\":{method}")), "nothing may be sent at air gap on, saw:\n{all}");
    }
    let mut asked = Vec::new();
    while let Ok(message) = seen.try_recv() {
        asked.push(message);
    }
    assert!(!asked.iter().any(|m| m["message"]["method"] == "turn/start" || m["message"]["method"] == "thread/start"), "{asked:?}");
    daemon.abort();
}

/// Every request signal-cli was sent, in order.
fn requests(log: &Path) -> Vec<Value> {
    std::fs::read_to_string(log).unwrap_or_default().lines().filter_map(|line| serde_json::from_str(line).ok()).collect()
}

#[tokio::test]
async fn linked_to_the_owners_account_only_note_to_self_is_read_and_answered_there() {
    let dir = Scratch(std::env::temp_dir().join(format!("ling-signal-linked-{}", std::process::id())));
    let _ = std::fs::remove_dir_all(&dir.0);
    let state = dir.0.join("state");
    std::fs::create_dir_all(&state).unwrap();
    let log = dir.0.join("signal-cli.log");
    let now = now_ms();
    let messages = vec![
        // A friend writes to the owner.
        json!({"jsonrpc":"2.0","method":"receive","params":{"envelope":{"sourceUuid":"friend","sourceDevice":1,"timestamp":now,"dataMessage":{"message":"dinner tonight?"}}}}),
        // The owner answers the friend from the phone: a sync message, not to themselves.
        json!({"jsonrpc":"2.0","method":"receive","params":{"envelope":{"sourceUuid":"me","sourceDevice":1,"timestamp":now + 1,"syncMessage":{"sentMessage":{"destinationNumber":"+15550000","destinationUuid":"friend","message":"sure","timestamp":now + 1}}}}}),
        // A group message from the phone.
        json!({"jsonrpc":"2.0","method":"receive","params":{"envelope":{"sourceUuid":"me","sourceDevice":1,"timestamp":now + 2,"syncMessage":{"sentMessage":{"message":"hi all","groupInfo":{"groupId":"g"},"timestamp":now + 2}}}}}),
        // A marked reply, as a second bridge on the account would see one: never a question.
        json!({"jsonrpc":"2.0","method":"receive","params":{"envelope":{"sourceUuid":"me","sourceDevice":5,"timestamp":now + 3,"syncMessage":{"sentMessage":{"destinationNumber":"+15559999","destinationUuid":"me","message":"🐦 an earlier answer","timestamp":now + 3}}}}}),
        // The owner writes to Note to Self from the phone: the one question.
        json!({"jsonrpc":"2.0","method":"receive","params":{"envelope":{"sourceUuid":"me","sourceDevice":1,"timestamp":now + 4,"syncMessage":{"sentMessage":{"destinationNumber":"+15559999","destinationUuid":"me","message":"what is 2+2?","timestamp":now + 4}}}}}),
    ];
    let (seen_tx, mut seen) = tokio::sync::mpsc::unbounded_channel();
    let port = fake_ling_web(seen_tx, "off").await;
    Config {
        mode: Mode::Linked { own_device: 4 },
        account: "+15559999".to_string(),
        own_uuid: Some("me".to_string()),
        owner: Some(Owner { aci: "me".to_string(), fingerprint: String::new() }),
        binding: None,
        port,
        node: "test-node".to_string(),
        signal_cli: fake_signal_cli(&dir.0, &log, &messages),
        signal_cli_env: vec![],
        signal_cli_version: "0.14.9".to_string(),
        vision: false,
    }
    .save(&state)
    .unwrap();
    ling_signal::state::save_cookie(&state, "mightling_device=test").unwrap();
    let runtime = dir.0.join("run");
    let daemon = {
        let (state, runtime) = (state.clone(), runtime.clone());
        tokio::spawn(async move { ling_signal::serve::serve(&state, Some(&runtime)).await })
    };
    let deadline = tokio::time::Instant::now() + Duration::from_secs(20);
    loop {
        if requests(&log).iter().any(|r| r["method"] == "send" && r["params"]["message"] == "🐦 The answer is 4.") {
            break;
        }
        assert!(tokio::time::Instant::now() < deadline, "no answer in Note to Self; signal-cli saw: {:?}", requests(&log));
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    // Let anything else that might happen, happen.
    tokio::time::sleep(Duration::from_secs(5)).await;
    let sent = requests(&log);
    let sends: Vec<&Value> = sent.iter().filter(|r| r["method"] == "send").collect();
    assert_eq!(sends.len(), 1, "one answer and nothing else: {sends:?}");
    assert_eq!(sends[0]["params"]["noteToSelf"], true);
    assert!(sends[0]["params"].get("recipient").is_none(), "never to anyone else");
    // The bold "4" moved by the marker's three UTF-16 units.
    assert_eq!(sends[0]["params"]["textStyle"], json!(["17:1:BOLD"]));
    for method in ["sendReceipt", "sendTyping", "listIdentities", "getAttachment"] {
        assert!(!sent.iter().any(|r| r["method"] == method), "{method} was called: {sent:?}");
    }
    // Only the note became a turn.
    let mut asked = Vec::new();
    while let Ok(message) = seen.try_recv() {
        asked.push(message);
    }
    let turns: Vec<&Value> = asked.iter().filter(|m| m["message"]["method"] == "turn/start").collect();
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0]["message"]["params"]["input"][0]["text"], "what is 2+2?");
    // Nothing about the other conversations is kept or counted.
    let conversation = std::fs::read_to_string(state.join("conversation.json")).unwrap();
    let conversation: Value = serde_json::from_str(&conversation).unwrap();
    assert_eq!(conversation["ignored"], 0);
    assert_eq!(conversation["seen"], json!([now + 4]), "only the note's timestamp is remembered");
    let status = std::fs::read_to_string(runtime.join("status.json")).unwrap();
    assert!(status.contains(r#""strangersIgnored":null"#) && status.contains(r#""mode":"linked""#), "{status}");
    for private in ["dinner", "sure", "hi all", "friend"] {
        assert!(!status.contains(private) && !conversation.to_string().contains(private), "{private} leaked");
    }
    daemon.abort();
}
