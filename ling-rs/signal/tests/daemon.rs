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

/// A stand-in for signal-cli: every request line goes to `log`; `listIdentities` trusts the owner;
/// everything else answers with a timestamp. Two messages arrive after a second.
fn fake_signal_cli(dir: &Path, log: &Path) -> PathBuf {
    let path = dir.join("signal-cli");
    let script = format!(
        r#"#!/bin/sh
LOG='{log}'
(sleep 2
 echo '{{"jsonrpc":"2.0","method":"receive","params":{{"envelope":{{"sourceUuid":"stranger","sourceDevice":1,"timestamp":1000,"dataMessage":{{"message":"who are you?"}}}}}}}}'
 echo '{{"jsonrpc":"2.0","method":"receive","params":{{"envelope":{{"sourceUuid":"owner","sourceDevice":1,"timestamp":'$(($(date +%s) * 1000))',"dataMessage":{{"message":"what is 2+2?"}}}}}}}}'
) &
while IFS= read -r line; do
  printf '%s\n' "$line" >> "$LOG"
  id=$(printf '%s' "$line" | sed -n 's/.*"id":\([0-9]*\).*/\1/p')
  case "$line" in
    *'"method":"listIdentities"'*) echo "{{\"jsonrpc\":\"2.0\",\"result\":[{{\"uuid\":\"owner\",\"fingerprint\":\"fp-owner\",\"trustLevel\":\"TRUSTED_VERIFIED\"}},{{\"uuid\":\"stranger\",\"fingerprint\":\"fp-s\",\"trustLevel\":\"TRUSTED_UNVERIFIED\"}}],\"id\":$id}}" ;;
    *) echo "{{\"jsonrpc\":\"2.0\",\"result\":{{\"timestamp\":$id}},\"id\":$id}}" ;;
  esac
done
"#,
        log = log.display()
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
async fn fake_ling_web(seen: tokio::sync::mpsc::UnboundedSender<Value>) -> u16 {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let seen = seen.clone();
            tokio::spawn(async move {
                // Plain HTTP (the air-gap poll) is not a WebSocket: the handshake fails and the
                // connection drops, which the bridge treats as "level unknown".
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
    let port = fake_ling_web(seen_tx).await;
    Config {
        mode: Mode::Dedicated,
        account: "+15550000".to_string(),
        own_uuid: Some("bridge".to_string()),
        owner: Some(Owner { aci: "owner".to_string(), fingerprint: "fp-owner".to_string() }),
        binding: None,
        port,
        node: "test-node".to_string(),
        signal_cli: fake_signal_cli(&dir.0, &log),
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
