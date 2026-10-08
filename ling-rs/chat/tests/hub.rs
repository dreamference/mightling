//! The conversation model against a scripted agent: no `ling web`, no messenger.

use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use ling_chat::agent::AgentCommand;
use ling_chat::agent::AgentHandle;
use ling_chat::agent::CONNECTED;
use ling_chat::hub::Hub;
use ling_chat::hub::HubConfig;
use ling_chat::hub::Inbound;
use ling_chat::hub::Outbound;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;

type Script = Arc<dyn Fn(&str, &Value) -> Result<Value, String> + Send + Sync>;

#[derive(Clone, Default)]
struct Log {
    requests: Arc<Mutex<Vec<(String, Value)>>>,
    responses: Arc<Mutex<Vec<(Value, Value)>>>,
}

impl Log {
    fn methods(&self) -> Vec<String> {
        self.requests.lock().unwrap().iter().map(|(m, _)| m.clone()).collect()
    }
    fn params(&self, method: &str) -> Vec<Value> {
        self.requests.lock().unwrap().iter().filter(|(m, _)| m == method).map(|(_, p)| p.clone()).collect()
    }
}

fn default_script() -> Script {
    let threads = Arc::new(Mutex::new(0u32));
    Arc::new(move |method, _params| match method {
        "thread/start" => {
            let mut n = threads.lock().unwrap();
            *n += 1;
            Ok(json!({ "thread": { "id": format!("th{n}") } }))
        }
        "turn/start" => Ok(json!({ "turn": { "id": "tu1", "status": "inProgress", "items": [] } })),
        _ => Ok(json!({})),
    })
}

struct Rig {
    log: Log,
    inbound: mpsc::UnboundedSender<Inbound>,
    events: mpsc::UnboundedSender<Value>,
    telegram: mpsc::UnboundedReceiver<Outbound>,
    matrix: mpsc::UnboundedReceiver<Outbound>,
    dir: std::path::PathBuf,
}

fn scratch(tag: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("ling-chat-hub-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    dir
}

fn rig(tag: &str, script: Script, airgapped: bool, tweak: impl FnOnce(&mut HubConfig)) -> Rig {
    let dir = scratch(tag);
    let (agent, mut commands) = AgentHandle::channel();
    let log = Log::default();
    let agent_log = log.clone();
    tokio::spawn(async move {
        while let Some(command) = commands.recv().await {
            match command {
                AgentCommand::Request { method, params, reply } => {
                    agent_log.requests.lock().unwrap().push((method.clone(), params.clone()));
                    let _ = reply.send(script(&method, &params));
                }
                AgentCommand::Respond { id, result } => agent_log.responses.lock().unwrap().push((id, result)),
            }
        }
    });
    let mut config = HubConfig::new(dir.clone());
    config.draft_interval = Duration::from_millis(10);
    tweak(&mut config);
    let mut hub = Hub::new(config, agent, Box::new(move || airgapped));
    let (tg_tx, telegram) = mpsc::unbounded_channel();
    let (mx_tx, matrix) = mpsc::unbounded_channel();
    hub.add_adapter("telegram", tg_tx);
    hub.add_adapter("matrix", mx_tx);
    let (inbound, inbound_rx) = mpsc::unbounded_channel();
    let (events, events_rx) = mpsc::unbounded_channel();
    tokio::spawn(hub.run(inbound_rx, events_rx));
    Rig { log, inbound, events, telegram, matrix, dir }
}

const CHAT: &str = "telegram:7";

async fn next(rx: &mut mpsc::UnboundedReceiver<Outbound>) -> Outbound {
    tokio::time::timeout(Duration::from_secs(5), rx.recv()).await.expect("no output in time").expect("channel closed")
}

/// The next output that is not a draft.
async fn next_settled(rx: &mut mpsc::UnboundedReceiver<Outbound>) -> Outbound {
    loop {
        let out = next(rx).await;
        if !matches!(out, Outbound::Draft { .. }) {
            return out;
        }
    }
}

async fn settle() {
    tokio::time::sleep(Duration::from_millis(80)).await;
}

fn text(chat: &str, text: &str) -> Inbound {
    Inbound::Text { chat: chat.to_string(), text: text.to_string() }
}

fn event(method: &str, params: Value) -> Value {
    json!({ "method": method, "params": params })
}

#[tokio::test]
async fn a_first_message_starts_an_ask_thread_and_streams_the_answer() {
    let mut rig = rig("first", default_script(), false, |_| {});
    rig.inbound.send(text(CHAT, "What is the weather in Paris today?")).unwrap();
    assert_eq!(next(&mut rig.telegram).await, Outbound::Draft { chat: CHAT.into(), text: String::new() });
    settle().await;
    assert_eq!(rig.log.methods(), vec!["thread/start", "thread/name/set", "turn/start"]);
    assert_eq!(rig.log.params("thread/start")[0], json!({ "prompt": "ask" }));
    assert_eq!(rig.log.params("thread/name/set")[0]["name"], "What is the weather in Paris today?");
    assert_eq!(rig.log.params("turn/start")[0]["input"][0]["text"], "What is the weather in Paris today?");

    rig.events.send(event("item/agentMessage/delta", json!({ "threadId": "th1", "turnId": "tu1", "itemId": "a", "delta": "Searching" }))).unwrap();
    let draft = loop {
        if let Outbound::Draft { text, .. } = next(&mut rig.telegram).await
            && !text.is_empty()
        {
            break text;
        }
    };
    assert_eq!(draft, "Searching");
    rig.events
        .send(event("item/completed", json!({ "threadId": "th1", "turnId": "tu1", "item": { "type": "agentMessage", "id": "a", "text": "Searching the web.", "phase": "commentary" } })))
        .unwrap();
    rig.events
        .send(event("item/completed", json!({ "threadId": "th1", "turnId": "tu1", "item": { "type": "agentMessage", "id": "b", "text": "**18 °C**, sunny [1].", "phase": "final_answer" } })))
        .unwrap();
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": [] } }))).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::TurnEnded { chat: CHAT.into() });
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::Message { chat: CHAT.into(), markdown: "**18 °C**, sunny [1].".into() });

    // The next message continues the same thread.
    rig.inbound.send(text(CHAT, "And tomorrow?")).unwrap();
    settle().await;
    assert_eq!(rig.log.params("thread/start").len(), 1);
    assert_eq!(rig.log.params("turn/start")[1]["threadId"], "th1");
    let saved = std::fs::read_to_string(rig.dir.join("threads.json")).unwrap();
    assert!(saved.contains("\"current\": \"th1\""));
}

#[tokio::test]
async fn new_threads_and_use_switch_threads() {
    let mut rig = rig("threads", default_script(), false, |_| {});
    rig.inbound.send(text(CHAT, "first question")).unwrap();
    settle().await;
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed" } }))).unwrap();
    settle().await;
    rig.inbound.send(text(CHAT, "/new")).unwrap();
    rig.inbound.send(text(CHAT, "second question")).unwrap();
    settle().await;
    rig.events.send(event("turn/completed", json!({ "threadId": "th2", "turn": { "id": "tu1", "status": "completed" } }))).unwrap();
    settle().await;
    rig.inbound.send(text(CHAT, "/threads")).unwrap();
    let listing = loop {
        if let Outbound::Notice { text, .. } = next(&mut rig.telegram).await
            && text.starts_with("1.")
        {
            break text;
        }
    };
    assert!(listing.starts_with("1. second question (current)\n2. first question"), "{listing}");
    rig.inbound.send(text(CHAT, "/use 2")).unwrap();
    rig.inbound.send(text(CHAT, "back to the first")).unwrap();
    settle().await;
    // Started on this connection: no resume needed.
    assert!(rig.log.params("thread/resume").is_empty());
    assert_eq!(rig.log.params("turn/start").last().unwrap()["threadId"], "th1");
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed" } }))).unwrap();
    // After a reconnection the subscription is gone: the thread is resumed before its next turn.
    rig.events.send(json!({ "method": CONNECTED })).unwrap();
    settle().await;
    rig.inbound.send(text(CHAT, "one more")).unwrap();
    settle().await;
    let methods = rig.log.methods();
    assert_eq!(&methods[methods.len() - 2..], ["thread/resume", "turn/start"]);
    assert_eq!(rig.log.params("thread/resume")[0]["threadId"], "th1");
    rig.inbound.send(text(CHAT, "/use 9")).unwrap();
    loop {
        if let Outbound::Notice { text, .. } = next(&mut rig.telegram).await
            && text.starts_with("No such thread")
        {
            break;
        }
    }
}

#[tokio::test]
async fn a_message_during_a_turn_steers_it_or_waits_for_the_next() {
    let refuse_steer: Script = {
        let base = default_script();
        Arc::new(move |method, params| if method == "turn/steer" { Err("no active turn".into()) } else { base(method, params) })
    };
    let mut rig = rig("steer", refuse_steer, false, |_| {});
    rig.inbound.send(text(CHAT, "write a poem")).unwrap();
    settle().await;
    rig.events.send(event("turn/started", json!({ "threadId": "th1", "turn": { "id": "tu1" } }))).unwrap();
    rig.inbound.send(text(CHAT, "make it short")).unwrap();
    settle().await;
    let steer = rig.log.params("turn/steer");
    assert_eq!(steer[0]["expectedTurnId"], "tu1");
    assert_eq!(steer[0]["input"][0]["text"], "make it short");
    // Refused: it waits, and starts the next turn when this one ends.
    assert_eq!(rig.log.params("turn/start").len(), 1);
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed" } }))).unwrap();
    settle().await;
    let starts = rig.log.params("turn/start");
    assert_eq!(starts.len(), 2);
    assert_eq!(starts[1]["input"][0]["text"], "make it short");
    while let Ok(out) = rig.telegram.try_recv() {
        let _ = out;
    }
}

async fn open_approval(rig: &mut Rig, method: &str, params: Value) -> (u64, Vec<String>, String) {
    rig.inbound.send(text(CHAT, "do something")).unwrap();
    settle().await;
    let mut params = params;
    params["threadId"] = json!("th1");
    rig.events.send(json!({ "id": 55, "method": method, "params": params })).unwrap();
    loop {
        if let Outbound::Approval { id, options, text, .. } = next(&mut rig.telegram).await {
            return (id, options, text);
        }
    }
}

#[tokio::test]
async fn a_command_approval_is_answered_with_the_protocols_payload() {
    let mut rig = rig("approve", default_script(), false, |_| {});
    let (id, options, text) =
        open_approval(&mut rig, "item/commandExecution/requestApproval", json!({ "command": "rm -rf build", "reason": "clean", "cwd": "/w" })).await;
    assert_eq!(options, vec!["Approve once", "Decline"]);
    assert!(text.contains("rm -rf build") && text.contains("clean"));
    // An answer from another chat is ignored.
    rig.inbound.send(Inbound::Answer { chat: "telegram:8".into(), approval: id, choice: 0 }).unwrap();
    settle().await;
    assert!(rig.log.responses.lock().unwrap().is_empty());
    rig.inbound.send(Inbound::Answer { chat: CHAT.into(), approval: id, choice: 0 }).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::ApprovalClosed { chat: CHAT.into(), id, outcome: "Approved.".into() });
    assert_eq!(*rig.log.responses.lock().unwrap(), vec![(json!(55), json!({ "decision": "accept" }))]);
    // A second answer finds nothing open.
    rig.inbound.send(Inbound::Answer { chat: CHAT.into(), approval: id, choice: 0 }).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::Notice { chat: CHAT.into(), text: "That request is no longer open.".into() });
    assert_eq!(rig.log.responses.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn no_declines_by_text_and_silence_declines_in_time() {
    let mut rig = rig("decline", default_script(), false, |config| config.approval_timeout = Duration::from_millis(300));
    let (id, _, _) = open_approval(&mut rig, "item/fileChange/requestApproval", json!({ "reason": "write notes", "grantRoot": "/w" })).await;
    rig.inbound.send(text(CHAT, "no")).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::ApprovalClosed { chat: CHAT.into(), id, outcome: "Declined.".into() });
    assert_eq!(rig.log.responses.lock().unwrap()[0].1, json!({ "decision": "decline" }));
    rig.events.send(json!({ "id": 56, "method": "item/commandExecution/requestApproval", "params": { "threadId": "th1", "command": "ls" } })).unwrap();
    let id2 = loop {
        if let Outbound::Approval { id, .. } = next(&mut rig.telegram).await {
            break id;
        }
    };
    assert_eq!(
        next_settled(&mut rig.telegram).await,
        Outbound::ApprovalClosed { chat: CHAT.into(), id: id2, outcome: "Declined: no answer in time.".into() }
    );
    assert_eq!(rig.log.responses.lock().unwrap()[1], (json!(56), json!({ "decision": "decline" })));
}

#[tokio::test]
async fn permissions_and_questions_use_their_own_payloads() {
    let mut rig = rig("payloads", default_script(), false, |_| {});
    let wanted = json!({ "network": { "enabled": true } });
    let (id, _, _) = open_approval(&mut rig, "item/permissions/requestApproval", json!({ "reason": "fetch", "permissions": wanted, "cwd": "/w" })).await;
    rig.inbound.send(text(CHAT, "yes")).unwrap();
    let _ = next_settled(&mut rig.telegram).await;
    assert_eq!(rig.log.responses.lock().unwrap()[0].1, json!({ "permissions": wanted, "scope": "turn" }));
    let _ = id;

    let question = json!({ "questions": [{ "id": "q1", "header": "Format", "question": "Which one?", "options": [{ "label": "PDF", "description": "" }, { "label": "HTML", "description": "" }] }] });
    rig.events.send(json!({ "id": 57, "method": "item/tool/requestUserInput", "params": { "threadId": "th1", "questions": question["questions"] } })).unwrap();
    let options = loop {
        if let Outbound::Approval { options, .. } = next(&mut rig.telegram).await {
            break options;
        }
    };
    assert_eq!(options, vec!["PDF", "HTML", "Decline"]);
    rig.inbound.send(text(CHAT, "2")).unwrap();
    let _ = next_settled(&mut rig.telegram).await;
    assert_eq!(rig.log.responses.lock().unwrap()[1].1, json!({ "answers": { "q1": { "answers": ["HTML"] } } }));

    // A free-text question takes the next message.
    rig.events
        .send(json!({ "id": 58, "method": "item/tool/requestUserInput", "params": { "threadId": "th1", "questions": [{ "id": "q2", "header": "", "question": "Name?", "options": null }] } }))
        .unwrap();
    loop {
        if let Outbound::Approval { .. } = next(&mut rig.telegram).await {
            break;
        }
    }
    rig.inbound.send(text(CHAT, "Mightling notes")).unwrap();
    let _ = next_settled(&mut rig.telegram).await;
    assert_eq!(rig.log.responses.lock().unwrap()[2].1, json!({ "answers": { "q2": { "answers": ["Mightling notes"] } } }));

    // A secret is never asked for on a phone.
    rig.events
        .send(json!({ "id": 59, "method": "item/tool/requestUserInput", "params": { "threadId": "th1", "questions": [{ "id": "q3", "header": "", "question": "Password?", "isSecret": true, "options": null }] } }))
        .unwrap();
    let notice = next_settled(&mut rig.telegram).await;
    assert!(matches!(notice, Outbound::Notice { ref text, .. } if text.contains("secret")), "{notice:?}");
    assert_eq!(rig.log.responses.lock().unwrap()[3], (json!(59), json!({ "answers": {} })));
}

#[tokio::test]
async fn requests_on_threads_the_bridge_is_not_answering_are_left_alone() {
    let mut rig = rig("foreign", default_script(), false, |_| {});
    rig.events.send(json!({ "id": 9, "method": "item/commandExecution/requestApproval", "params": { "threadId": "someone-else", "command": "ls" } })).unwrap();
    settle().await;
    assert!(rig.telegram.try_recv().is_err());
    assert!(rig.log.responses.lock().unwrap().is_empty());
}

#[tokio::test]
async fn the_air_gap_pauses_telegram_and_not_matrix() {
    let mut rig = rig("airgap", default_script(), true, |_| {});
    rig.inbound.send(text(CHAT, "hello")).unwrap();
    let notice = next(&mut rig.telegram).await;
    assert!(matches!(notice, Outbound::Notice { ref text, .. } if text.contains("air gap is on")), "{notice:?}");
    settle().await;
    assert!(rig.log.methods().is_empty());
    rig.inbound.send(text("matrix:!r:x", "hello")).unwrap();
    assert!(matches!(next(&mut rig.matrix).await, Outbound::Draft { .. }));
    settle().await;
    assert_eq!(rig.log.methods(), vec!["thread/start", "thread/name/set", "turn/start"]);
}

#[tokio::test]
async fn after_a_reconnection_a_turn_that_ended_meanwhile_is_still_delivered() {
    let script: Script = {
        let base = default_script();
        Arc::new(move |method, params| {
            if method == "thread/turns/list" {
                Ok(json!({ "data": [{ "id": "tu1", "status": "completed", "items": [{ "type": "agentMessage", "id": "m", "text": "Done while you were away.", "phase": "final_answer" }] }] }))
            } else {
                base(method, params)
            }
        })
    };
    let mut rig = rig("reconnect", script, false, |_| {});
    rig.inbound.send(text(CHAT, "long task")).unwrap();
    settle().await;
    rig.events.send(json!({ "method": CONNECTED })).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::TurnEnded { chat: CHAT.into() });
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::Message { chat: CHAT.into(), markdown: "Done while you were away.".into() });
    assert_eq!(rig.log.params("thread/resume")[0]["threadId"], "th1");
    assert_eq!(rig.log.params("thread/turns/list")[0], json!({ "threadId": "th1", "limit": 1, "sortDirection": "desc" }));
}

#[tokio::test]
async fn stop_interrupts_the_running_turn_and_a_failure_is_reported() {
    let mut rig = rig("stop", default_script(), false, |_| {});
    rig.inbound.send(text(CHAT, "count to a million")).unwrap();
    settle().await;
    rig.inbound.send(Inbound::Stop { chat: CHAT.into() }).unwrap();
    settle().await;
    assert_eq!(rig.log.params("turn/interrupt")[0], json!({ "threadId": "th1", "turnId": "tu1" }));
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "interrupted" } }))).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::TurnEnded { chat: CHAT.into() });
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::Notice { chat: CHAT.into(), text: "Stopped.".into() });

    rig.inbound.send(text(CHAT, "again")).unwrap();
    settle().await;
    rig.events.send(event("error", json!({ "threadId": "th1", "turnId": "tu1", "willRetry": false, "error": { "message": "model server unreachable" } }))).unwrap();
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "failed" } }))).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::TurnEnded { chat: CHAT.into() });
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::Notice { chat: CHAT.into(), text: "The answer failed: model server unreachable".into() });
}

#[tokio::test]
async fn an_unreachable_agent_is_said_plainly() {
    let down: Script = Arc::new(|_, _| Err("ling web is not reachable".into()));
    let mut rig = rig("down", down, false, |_| {});
    rig.inbound.send(text(CHAT, "hello")).unwrap();
    let notice = next(&mut rig.telegram).await;
    assert!(matches!(notice, Outbound::Notice { ref text, .. } if text.starts_with("Mightling is not reachable right now")), "{notice:?}");
}
