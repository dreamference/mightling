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
        self.requests.lock().unwrap().iter().filter(|(m, _)| m != "web").map(|(m, _)| m.clone()).collect()
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
                // `ling web`'s upload: a name with "text" in it has text on it; one with "vision"
                // goes to a model that takes images; "lost" never arrives.
                // `ling web`'s Night Shift routes: one repository, a queue of one, one report after "".
                AgentCommand::Web { path, body, reply } => {
                    agent_log.requests.lock().unwrap().push(("web".to_string(), json!({ "path": path, "body": body })));
                    let answer = if path == "/api/night/repos" {
                        json!({ "repos": [{ "path": "/w/app", "name": "app" }] })
                    } else if path == "/api/night/add" {
                        json!({ "queued": true, "lines": ["Queued 20261021-1300-abc on branch night/20261021-1300-abc, from 0123456789.", "Runs tonight 01:00-07:00."] })
                    } else if path == "/api/night/status" {
                        json!({ "tasks": [{ "id": "20261021-1300-abc", "status": "queued", "repo": "app", "task": "Fix the parser" }] })
                    } else if path.starts_with("/api/night/reports") {
                        if path.ends_with("after=") {
                            json!({ "reports": [], "latest": "2026-10-20.md" })
                        } else if path.ends_with("after=2026-10-21.md") {
                            json!({ "reports": [], "latest": "2026-10-21.md" })
                        } else {
                            json!({ "reports": [{ "name": "2026-10-21.md", "text": "## app\n- 20261021-1300-abc: done" }], "latest": "2026-10-21.md" })
                        }
                    } else if path.starts_with("/api/sync/notices") {
                        // Sync's notices: the first look sees the newest is 4, then one more comes.
                        if path.ends_with("after=0") {
                            json!({ "notices": [{ "seq": 4, "text": "Turn finished: Old" }], "latest": 4 })
                        } else if path.ends_with("after=4") {
                            json!({ "notices": [{ "seq": 5, "thread": "t", "device": "lap", "kind": "approval", "text": "Approval needed: Fix the login" }], "latest": 5 })
                        } else {
                            json!({ "notices": [], "latest": 5 })
                        }
                    } else if path == "/api/telegram/pending-pairs" {
                        json!({ "pending": [{ "id": "pair-req-1", "code": "482 913", "user": 7, "created_at": 100 }] })
                    } else if path == "/api/telegram/pending-pairs/approve" || path == "/api/telegram/pending-pairs/decline" {
                        json!({ "ok": true })
                    } else {
                        json!({})
                    };
                    let _ = reply.send(Ok(answer));
                }
                // The agent's files: a thread "th-files" made report.md (and a huge one); bytes on request.
                AgentCommand::MadeFiles { thread, since_ms: _, reply } => {
                    agent_log.requests.lock().unwrap().push(("madeFiles".to_string(), json!({ "thread": thread })));
                    let files = if thread == "th1" {
                        vec![
                            ling_web_server::ask_client::MadeFile { name: "report.md".to_string(), bytes: 2048 },
                            ling_web_server::ask_client::MadeFile { name: "huge.bin".to_string(), bytes: 900 * 1024 * 1024 },
                            ling_web_server::ask_client::MadeFile { name: "scratch.py".to_string(), bytes: 10 },
                        ]
                    } else {
                        vec![]
                    };
                    let _ = reply.send(Ok(files));
                }
                AgentCommand::MadeFile { thread, name, reply } => {
                    agent_log.requests.lock().unwrap().push(("madeFile".to_string(), json!({ "thread": thread, "name": name })));
                    let _ = reply.send(Ok(format!("BYTES:{name}").into_bytes()));
                }
                // `ling web`'s speech-to-text: bytes "silence" are refused, anything else heard as is.
                AgentCommand::Transcribe { media, bytes, reply } => {
                    agent_log.requests.lock().unwrap().push(("transcribe".to_string(), json!({ "media": media })));
                    let heard = String::from_utf8_lossy(&bytes).into_owned();
                    let _ = reply.send(if heard == "silence" { Err("Speech-to-text is not running on this machine".to_string()) } else { Ok(heard) });
                }
                AgentCommand::Upload { thread, name, image, bytes: _, reply } => {
                    agent_log.requests.lock().unwrap().push(("upload".to_string(), json!({ "thread": thread, "name": name, "image": image })));
                    let saved = if name.starts_with("lost") {
                        Err("refused".to_string())
                    } else {
                        Ok(ling_web_server::ask_client::Saved {
                            path: format!("/ask/{thread}/{name}"),
                            text: name.contains("text").then(|| "Quiet 24 dB, HEPA 13".to_string()),
                            vision: name.contains("vision"),
                        })
                    };
                    let _ = reply.send(saved);
                }
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
    assert_eq!(rig.log.params("thread/start")[0], json!({ "prompt": "chat" }));
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
    // Several that waited are one turn, not one each (the morning of 2026-10-20).
    rig.inbound.send(text(CHAT, "in French")).unwrap();
    rig.inbound.send(text(CHAT, "and rhyming")).unwrap();
    settle().await;
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed" } }))).unwrap();
    settle().await;
    let starts = rig.log.params("turn/start");
    assert_eq!(starts.len(), 3);
    assert_eq!(starts[2]["input"][0]["text"], "in French\n\nand rhyming");
    while let Ok(out) = rig.telegram.try_recv() {
        let _ = out;
    }
}

async fn open_approval(rig: &mut Rig, method: &str, params: Value) -> (String, Vec<String>, String) {
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
    rig.inbound.send(Inbound::Answer { chat: "telegram:8".into(), approval: id.clone(), choice: 0 }).unwrap();
    settle().await;
    assert!(rig.log.responses.lock().unwrap().is_empty());
    rig.inbound.send(Inbound::Answer { chat: CHAT.into(), approval: id.clone(), choice: 0 }).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::ApprovalClosed { chat: CHAT.into(), id: id.clone(), outcome: "Approved.".into() });
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

fn photo(chat: &str, text: &str, name: &str) -> Inbound {
    let attachments = vec![ling_chat::attachments::Media { name: name.to_string(), mime: "image/jpeg".to_string(), bytes: b"jpeg".to_vec() }];
    Inbound::Media { chat: chat.to_string(), text: text.to_string(), attachments, dropped: vec![] }
}

/// The regression of 2026-10-20 (Signal, but the same rule here): an image with nothing the model
/// can use never becomes an empty turn; the bridge answers itself.
#[tokio::test]
async fn an_unreadable_image_alone_is_answered_by_the_bridge_and_starts_no_turn() {
    let mut rig = rig("unreadable", default_script(), false, |_| {});
    rig.inbound.send(photo(CHAT, "", "plain.jpg")).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::Notice { chat: CHAT.into(), text: ling_chat::attachments::UNREADABLE_IMAGE.into() });
    settle().await;
    assert!(!rig.log.methods().contains(&"turn/start".to_string()), "{:?}", rig.log.methods());
    assert_eq!(rig.log.params("upload")[0]["image"], true);
}

#[tokio::test]
async fn a_screenshot_goes_in_as_its_text_and_as_an_image_for_a_model_that_takes_images() {
    let mut reader = rig("screenshot", default_script(), false, |_| {});
    reader.inbound.send(photo(CHAT, "compare it", "text-shot.jpg")).unwrap();
    assert_eq!(next(&mut reader.telegram).await, Outbound::Draft { chat: CHAT.into(), text: String::new() });
    settle().await;
    let input = &reader.log.params("turn/start")[0]["input"];
    assert_eq!(input[0]["text"], "compare it\n\n[Screenshot text: Quiet 24 dB, HEPA 13]");
    assert_eq!(input.as_array().unwrap().len(), 1, "a text-only model gets no image");
    assert_eq!(reader.log.params("thread/name/set")[0]["name"], "compare it");

    let mut seeing = rig("vision", default_script(), false, |_| {});
    seeing.inbound.send(photo(CHAT, "", "vision-text.jpg")).unwrap();
    assert_eq!(next(&mut seeing.telegram).await, Outbound::Draft { chat: CHAT.into(), text: String::new() });
    settle().await;
    let input = &seeing.log.params("turn/start")[0]["input"];
    assert_eq!(input[1], json!({ "type": "localImage", "path": "/ask/th1/vision-text.jpg" }));
    // A sticker alone: named as dropped, nothing sent to the agent.
    seeing.inbound.send(Inbound::Media { chat: CHAT.into(), text: String::new(), attachments: vec![], dropped: vec!["a sticker".into()] }).unwrap();
    let Outbound::Notice { text, .. } = next_settled(&mut seeing.telegram).await else { panic!() };
    assert!(text.contains("a sticker"), "{text}");
}


/// A message after a long silence starts a new thread and says how to go back; `/use 2` does.
#[tokio::test]
async fn a_message_after_a_long_silence_starts_a_new_thread() {
    let mut rig = rig("silence", default_script(), false, |config| {
        config.settings.new_thread_after = Some(Duration::from_millis(300));
    });
    rig.inbound.send(text(CHAT, "about a song")).unwrap();
    settle().await;
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": [] } }))).unwrap();
    settle().await;
    // Soon after: the same thread.
    rig.inbound.send(text(CHAT, "and the chorus?")).unwrap();
    settle().await;
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": [] } }))).unwrap();
    assert_eq!(rig.log.params("thread/start").len(), 1);
    tokio::time::sleep(Duration::from_millis(400)).await;
    while rig.telegram.try_recv().is_ok() {}
    rig.inbound.send(text(CHAT, "compare it with a Dyson")).unwrap();
    let notice = next_settled(&mut rig.telegram).await;
    let Outbound::Notice { text: line, .. } = notice else { panic!("{notice:?}") };
    assert!(line.starts_with("New thread (the last message was") && line.ends_with("; /use 2 to go back."), "{line}");
    settle().await;
    assert_eq!(rig.log.params("thread/start").len(), 2);
    let threads = ling_chat::store::Threads::load(&rig.dir);
    assert_eq!(threads.chats[CHAT].current.as_deref(), Some("th2"));
    assert_eq!(threads.chats[CHAT].recent[1].id, "th1");
}

/// The morning of 2026-10-20: one answer ran sixteen minutes. Past its time the agent is told to
/// answer now; still running, it is interrupted and a final turn asks for the answer with no tools;
/// that one out of time too, the user gets what it wrote and a line saying so.
#[tokio::test]
async fn an_answer_past_its_time_is_steered_then_interrupted_then_given_a_final_turn() {
    let mut rig = rig("bounds", default_script(), false, |config| {
        config.settings.answer_budget = Some(Duration::from_millis(200));
        config.grace = Duration::from_millis(1_000);
        config.final_budget = Duration::from_millis(600);
    });
    rig.inbound.send(text(CHAT, "compare these two purifiers")).unwrap();
    settle().await;
    rig.events.send(event("item/agentMessage/delta", json!({ "threadId": "th1", "turnId": "tu1", "itemId": "w", "delta": "Checking the pages" }))).unwrap();
    tokio::time::sleep(Duration::from_millis(620)).await;
    let steer = rig.log.params("turn/steer");
    assert_eq!(steer.len(), 1, "{:?}", rig.log.methods());
    assert!(steer[0]["input"][0]["text"].as_str().unwrap().contains("Stop now: run no more tools"));
    assert!(rig.log.params("turn/interrupt").is_empty());
    tokio::time::sleep(Duration::from_millis(1_000)).await;
    assert_eq!(rig.log.params("turn/interrupt").len(), 1);
    let Outbound::Notice { text: line, .. } = next_settled(&mut rig.telegram).await else { panic!() };
    assert!(line.starts_with("This is taking too long (this answer has taken"), "{line}");
    // The interrupted turn ends: what it wrote is kept, marked, then a final turn; no "Stopped.".
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "interrupted", "items": [] } }))).unwrap();
    let cut = loop {
        if let Outbound::Message { markdown, .. } = next_settled(&mut rig.telegram).await {
            break markdown;
        }
    };
    assert!(cut.starts_with("Checking the pages\n\n_(cut off: this answer has taken"), "{cut}");
    settle().await;
    let starts = rig.log.params("turn/start");
    assert_eq!(starts.len(), 2);
    assert!(starts[1]["input"][0]["text"].as_str().unwrap().contains("without running any tool"));
    // The final turn writes a little, then runs out too.
    rig.events.send(event("item/agentMessage/delta", json!({ "threadId": "th1", "turnId": "tu1", "itemId": "a", "delta": "The first one is quieter" }))).unwrap();
    tokio::time::sleep(Duration::from_millis(1_100)).await;
    assert_eq!(rig.log.params("turn/interrupt").len(), 2);
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "interrupted", "items": [] } }))).unwrap();
    let mut outs = Vec::new();
    for _ in 0..3 {
        outs.push(next_settled(&mut rig.telegram).await);
    }
    assert!(outs.contains(&Outbound::Message { chat: CHAT.into(), markdown: "The first one is quieter".into() }), "{outs:?}");
    assert!(outs.contains(&Outbound::Notice { chat: CHAT.into(), text: ling_chat::limits::GAVE_UP.into() }), "{outs:?}");
    assert!(!outs.iter().any(|o| matches!(o, Outbound::Notice { text, .. } if text == "Stopped.")));
}

/// A `ling web` from before the phone prompt refuses `chat`: the thread is an Ask thread anyway.
#[tokio::test]
async fn an_older_ling_web_without_the_chat_prompt_gets_ask() {
    let threads = Arc::new(Mutex::new(0u32));
    let script: Script = Arc::new(move |method, params| match method {
        "thread/start" if params["prompt"] == "chat" => Err("no prompt named \"chat\" may be chosen here; named prompts: ask, default".to_string()),
        "thread/start" => {
            *threads.lock().unwrap() += 1;
            Ok(json!({ "thread": { "id": "th1" } }))
        }
        "turn/start" => Ok(json!({ "turn": { "id": "tu1", "status": "inProgress", "items": [] } })),
        _ => Ok(json!({})),
    });
    let rig = rig("oldweb", script, false, |_| {});
    rig.inbound.send(text(CHAT, "hello")).unwrap();
    settle().await;
    let starts = rig.log.params("thread/start");
    assert_eq!(starts, vec![json!({ "prompt": "chat" }), json!({ "prompt": "ask" })]);
    assert_eq!(rig.log.params("turn/start").len(), 1);
}

/// The model answered, called a tool, then said only "the answer above stands": the phone gets the
/// answer, not the pointer alone.
#[tokio::test]
async fn a_short_last_word_after_a_longer_answer_brings_the_answer_with_it() {
    let mut rig = rig("above", default_script(), false, |_| {});
    rig.inbound.send(text(CHAT, "which purifier is quieter?")).unwrap();
    settle().await;
    let answer = format!(
        "The Levoit is quieter: 24 dB on its night setting against 30 dB for the other one, and it keeps the HEPA 13 filter. {} [1]\n\n[1] Product page — https://example.com/p",
        "Both run all day; the Levoit's filter lasts longer between changes and its fan is rated for continuous use. ".repeat(3)
    );
    let items = json!([
        { "type": "agentMessage", "id": "m0", "text": "I'll check both product pages first.", "phase": "commentary" },
        { "type": "agentMessage", "id": "m1", "text": answer },
        { "type": "commandExecution", "id": "c1", "command": "ling-search x" },
        { "type": "agentMessage", "id": "m2", "text": "The answer above stands.", "phase": "final_answer" }
    ]);
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": items } }))).unwrap();
    loop {
        if let Outbound::Message { markdown, .. } = next_settled(&mut rig.telegram).await {
            assert_eq!(markdown, format!("{answer}\n\nThe answer above stands."));
            break;
        }
    }
    // A short final after short chatter is sent as it is.
    rig.inbound.send(text(CHAT, "and the price?")).unwrap();
    settle().await;
    let items = json!([
        { "type": "agentMessage", "id": "m3", "text": "Let me look the price up on the page." },
        { "type": "agentMessage", "id": "m4", "text": "It costs 199 euros [1].", "phase": "final_answer" }
    ]);
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": items } }))).unwrap();
    loop {
        if let Outbound::Message { markdown, .. } = next_settled(&mut rig.telegram).await {
            assert_eq!(markdown, "It costs 199 euros [1].");
            break;
        }
    }
}

/// The stop button keeps what was written (MOONSHOT D9): sent as a message marked stopped, not
/// left in a draft that vanishes.
#[tokio::test]
async fn a_stopped_answer_is_kept_as_a_message_marked_stopped() {
    let mut rig = rig("keep", default_script(), false, |_| {});
    rig.inbound.send(text(CHAT, "write a long poem")).unwrap();
    settle().await;
    rig.events.send(event("item/agentMessage/delta", json!({ "threadId": "th1", "turnId": "tu1", "itemId": "a", "delta": "Roses are red" }))).unwrap();
    rig.inbound.send(Inbound::Stop { chat: CHAT.into() }).unwrap();
    settle().await;
    assert_eq!(rig.log.params("turn/interrupt").len(), 1);
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "interrupted", "items": [] } }))).unwrap();
    let mut outs = Vec::new();
    loop {
        let out = next_settled(&mut rig.telegram).await;
        let message = matches!(out, Outbound::Message { .. });
        outs.push(out);
        if message {
            break;
        }
    }
    assert!(outs.contains(&Outbound::Message { chat: CHAT.into(), markdown: "Roses are red\n\n_(stopped)_".into() }), "{outs:?}");
    settle().await;
    assert!(rig.telegram.try_recv().is_err(), "no separate Stopped. line");
}

/// Topics (MOONSHOT §3.1): each topic is a thread of its own, named after its first message, with
/// no gap rule inside it; /threads lists the topics.
#[tokio::test]
async fn each_topic_is_its_own_thread_and_is_named_after_its_first_message() {
    let mut rig = rig("topics", default_script(), false, |config| {
        config.settings.new_thread_after = Some(Duration::from_millis(1));
    });
    rig.inbound.send(text("telegram:7/5", "Which purifier is quieter?")).unwrap();
    settle().await;
    assert_eq!(ling_chat::store::last_topic(&rig.dir, 7), Some(5));
    assert_eq!(next(&mut rig.telegram).await, Outbound::ThreadNamed { chat: "telegram:7/5".into(), name: "Which purifier is quieter?".into() });
    rig.inbound.send(text("telegram:7/6", "A song")).unwrap();
    settle().await;
    assert_eq!(ling_chat::store::last_topic(&rig.dir, 7), Some(6));
    // Topic 5's answer finishes after topic 6 was asked in: topic 5 is now the last used topic.
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": [] } }))).unwrap();
    settle().await;
    assert_eq!(ling_chat::store::last_topic(&rig.dir, 7), Some(5));
    rig.events.send(event("turn/completed", json!({ "threadId": "th2", "turn": { "id": "tu1", "status": "completed", "items": [] } }))).unwrap();
    tokio::time::sleep(Duration::from_millis(20)).await;
    assert_eq!(ling_chat::store::last_topic(&rig.dir, 7), Some(6));
    // Long after, inside the first topic: the same thread, no "New thread" line.
    rig.inbound.send(text("telegram:7/5", "and the price?")).unwrap();
    settle().await;
    assert_eq!(ling_chat::store::last_topic(&rig.dir, 7), Some(5));
    assert_eq!(rig.log.params("thread/start").len(), 2);
    assert_eq!(rig.log.params("turn/start")[2]["threadId"], "th1");
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": [] } }))).unwrap();
    settle().await;
    while rig.telegram.try_recv().is_ok() {}
    rig.inbound.send(text("telegram:7/5", "/threads")).unwrap();
    let list = loop {
        if let Outbound::Notice { text, .. } = next_settled(&mut rig.telegram).await
            && text.contains("topic")
        {
            break text;
        }
    };
    assert!(list.contains("• A song") && list.contains("• Which purifier is quieter? (this topic)") && list.contains("open a topic"), "{list}");
}

/// A button press naming an approval the hub never issued is ignored (MOONSHOT §3.2).
#[tokio::test]
async fn a_forged_approval_nonce_is_ignored() {
    let mut rig = rig("forged", default_script(), false, |_| {});
    let (id, _, _) = open_approval(&mut rig, "item/commandExecution/requestApproval", json!({ "command": "ls" })).await;
    assert_eq!(id.len(), 16);
    assert!(id.chars().all(|c| c.is_ascii_hexdigit()));
    rig.inbound.send(Inbound::Answer { chat: CHAT.into(), approval: "0000000000000001".into(), choice: 0 }).unwrap();
    settle().await;
    assert!(rig.log.responses.lock().unwrap().is_empty());
    assert!(rig.telegram.try_recv().is_err(), "not even a reply");
}

fn voice(chat: &str, caption: &str, heard: &str) -> Inbound {
    let attachments = vec![ling_chat::attachments::Media { name: "voice.ogg".to_string(), mime: "audio/ogg".to_string(), bytes: heard.as_bytes().to_vec() }];
    Inbound::Media { chat: chat.to_string(), text: caption.to_string(), attachments, dropped: vec![] }
}

/// A voice message is transcribed on the node, the text said back ("Heard: …"), and asked
/// (MOONSHOT §3.4); a failed transcription says so and asks nothing.
#[tokio::test]
async fn a_voice_message_is_heard_echoed_and_answered_and_a_failure_is_said() {
    let mut rig = rig("voice", default_script(), false, |_| {});
    rig.inbound.send(voice(CHAT, "", "which purifier is quieter")).unwrap();
    assert_eq!(next_settled(&mut rig.telegram).await, Outbound::Notice { chat: CHAT.into(), text: "Heard: which purifier is quieter".into() });
    settle().await;
    assert_eq!(rig.log.params("transcribe")[0]["media"], "audio/ogg");
    assert_eq!(rig.log.params("turn/start")[0]["input"][0]["text"], "which purifier is quieter");
    assert!(rig.log.params("upload").is_empty(), "a recording is not uploaded");
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": [] } }))).unwrap();
    settle().await;
    while rig.telegram.try_recv().is_ok() {}
    rig.inbound.send(voice(CHAT, "", "silence")).unwrap();
    let Outbound::Notice { text: line, .. } = next_settled(&mut rig.telegram).await else { panic!() };
    assert!(line.starts_with("I couldn't transcribe the voice message (Speech-to-text is not running"), "{line}");
    settle().await;
    assert_eq!(rig.log.params("turn/start").len(), 1, "nothing asked");
}

/// Files back (MOONSHOT §3.7): a file the agent made and named in its answer is offered and sent
/// only when the user says so; one it did not name is not offered; one too large is named and kept.
#[tokio::test]
async fn a_file_the_agent_made_and_named_is_offered_and_sent_only_when_asked() {
    let mut rig = rig("files", default_script(), false, |_| {});
    rig.inbound.send(text(CHAT, "compare them in a table")).unwrap();
    settle().await;
    let items = json!([{ "type": "agentMessage", "id": "m", "text": "The table is in report.md; huge.bin has the raw data.", "phase": "final_answer" }]);
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": items } }))).unwrap();
    let mut outs = Vec::new();
    let offer = loop {
        let out = next_settled(&mut rig.telegram).await;
        if let Outbound::Approval { id, text, options, .. } = &out {
            break (id.clone(), text.clone(), options.clone());
        }
        outs.push(out);
    };
    assert_eq!(offer.1, "Mightling made report.md (2 KB). Send it here?");
    assert_eq!(offer.2, vec!["Send it".to_string(), "No".to_string()]);
    outs.push(next_settled(&mut rig.telegram).await);
    assert!(outs.iter().any(|o| matches!(o, Outbound::Notice { text, .. } if text.contains("huge.bin") && text.contains("stays in the thread's folder"))), "{outs:?}");
    assert!(!rig.log.params("madeFile").iter().any(|_| true), "nothing sent before the press");
    // "yes" sends it (the newest open offer); scratch.py was never named, so never offered.
    rig.inbound.send(text(CHAT, "yes")).unwrap();
    let mut sent = None;
    for _ in 0..4 {
        if let Outbound::File { name, bytes, .. } = next_settled(&mut rig.telegram).await {
            sent = Some((name, bytes));
            break;
        }
    }
    assert_eq!(sent, Some(("report.md".to_string(), b"BYTES:report.md".to_vec())));
    assert!(rig.log.responses.lock().unwrap().is_empty(), "the agent is not answered for the bridge's own question");
}

#[tokio::test]
async fn no_file_is_offered_at_air_gap_on() {
    let mut rig = rig("files-airgap", default_script(), true, |_| {});
    rig.inbound.send(text("matrix:!r:x", "make a report")).unwrap();
    settle().await;
    let items = json!([{ "type": "agentMessage", "id": "m", "text": "Done: report.md.", "phase": "final_answer" }]);
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": items } }))).unwrap();
    settle().await;
    assert!(rig.log.params("madeFiles").is_empty());
    while let Ok(out) = rig.matrix.try_recv() {
        assert!(!matches!(out, Outbound::Approval { .. }), "{out:?}");
    }
}

/// Night Shift from the phone (MOONSHOT §3.5): `/night add` confirms the repository first, the
/// task carries the thread's last exchange, and nothing is queued before the press.
#[tokio::test]
async fn night_add_confirms_first_and_carries_the_threads_context() {
    let base = default_script();
    let script: Script = Arc::new(move |method, params| {
        if method == "thread/turns/list" {
            return Ok(json!({ "data": [{ "id": "tu1", "status": "completed", "items": [
                { "type": "userMessage", "id": "u", "content": [{ "type": "text", "text": "why does the parser drop tabs?" }] },
                { "type": "agentMessage", "id": "a", "text": "Because split_whitespace eats them." }
            ] }] }));
        }
        base(method, params)
    });
    let mut rig = rig("night", script, false, |_| {});
    rig.inbound.send(text(CHAT, "why does the parser drop tabs?")).unwrap();
    settle().await;
    rig.events.send(event("turn/completed", json!({ "threadId": "th1", "turn": { "id": "tu1", "status": "completed", "items": [] } }))).unwrap();
    settle().await;
    while rig.telegram.try_recv().is_ok() {}
    rig.inbound.send(text(CHAT, "/night add fix it so tabs survive")).unwrap();
    let (id, asked, options) = loop {
        if let Outbound::Approval { id, text, options, .. } = next_settled(&mut rig.telegram).await {
            break (id, text, options);
        }
    };
    assert!(asked.starts_with("Queue this for Night Shift tonight, in app?") && asked.contains("fix it so tabs survive") && asked.contains("split_whitespace"), "{asked}");
    assert_eq!(options, vec!["Queue it".to_string(), "Cancel".to_string()]);
    let adds = |rig: &Rig| rig.log.params("web").into_iter().filter(|w| w["path"] == "/api/night/add").collect::<Vec<_>>();
    assert!(adds(&rig).is_empty(), "nothing queued before the press");
    rig.inbound.send(Inbound::Answer { chat: CHAT.into(), approval: id, choice: 0 }).unwrap();
    let line = loop {
        if let Outbound::Notice { text, .. } = next_settled(&mut rig.telegram).await
            && text.starts_with("Queued ")
        {
            break text;
        }
    };
    assert!(line.contains("night/20261021-1300-abc"), "{line}");
    let added = adds(&rig);
    assert_eq!(added[0]["body"]["repo"], "/w/app");
    let task = added[0]["body"]["task"].as_str().unwrap();
    assert!(task.starts_with("fix it so tabs survive\n\nContext, from the phone conversation") && task.contains("Question: why does the parser drop tabs?") && task.contains("Answer: Because split_whitespace"), "{task}");
    // /night status reads the queue.
    rig.inbound.send(text(CHAT, "/night status")).unwrap();
    let status = loop {
        if let Outbound::Notice { text, .. } = next_settled(&mut rig.telegram).await
            && text.starts_with("Night Shift:")
        {
            break text;
        }
    };
    assert!(status.contains("• 20261021-1300-abc — queued [app] Fix the parser"), "{status}");
}

/// The morning report is pushed to Signal and Matrix chats by default, never twice, and the first
/// look after a fresh start sends nothing old.
#[tokio::test]
async fn the_morning_report_is_pushed_once_to_the_messengers_named() {
    let mut rig = rig("reports", default_script(), false, |config| config.report_interval = Duration::from_millis(150));
    rig.inbound.send(text("matrix:!r:x", "hello")).unwrap();
    rig.inbound.send(text(CHAT, "hello")).unwrap();
    settle().await;
    tokio::time::sleep(Duration::from_millis(500)).await;
    let mut pushed = Vec::new();
    while let Ok(out) = rig.matrix.try_recv() {
        if let Outbound::Message { markdown, .. } = out {
            pushed.push(markdown);
        }
    }
    assert_eq!(pushed.len(), 1, "once: {pushed:?}");
    assert!(pushed[0].starts_with("**Night Shift's report** (2026-10-21)") && pushed[0].contains("done"));
    while let Ok(out) = rig.telegram.try_recv() {
        assert!(!matches!(&out, Outbound::Message { markdown, .. } if markdown.contains("Night Shift's report")), "Telegram only when named");
    }
    assert_eq!(std::fs::read_to_string(rig.dir.join("night-report-seen")).unwrap(), "2026-10-21.md");
}


/// Sync's notices go to Signal and Matrix chats by default, once each, nothing old after a fresh
/// start, and never to Telegram unless named.
#[tokio::test]
async fn a_sync_notice_is_passed_on_once_to_the_messengers_named() {
    let mut rig = rig("sync-notices", default_script(), false, |config| config.report_interval = Duration::from_millis(150));
    rig.inbound.send(text("matrix:!r:x", "hello")).unwrap();
    rig.inbound.send(text(CHAT, "hello")).unwrap();
    settle().await;
    tokio::time::sleep(Duration::from_millis(600)).await;
    let mut passed = Vec::new();
    while let Ok(out) = rig.matrix.try_recv() {
        if let Outbound::Notice { text, .. } = out
            && text.contains("Approval needed")
        {
            passed.push(text);
        }
    }
    assert_eq!(passed, ["Approval needed: Fix the login (open Other devices in Mightling)"]);
    while let Ok(out) = rig.telegram.try_recv() {
        assert!(!matches!(&out, Outbound::Notice { text, .. } if text.contains("Approval needed") || text.contains("Turn finished")), "Telegram only when named");
    }
    assert_eq!(std::fs::read_to_string(rig.dir.join("sync-notice-seen")).unwrap(), "5");
}

/// Pending Mini App launch-pair requests (`GET /api/telegram/pending-pairs`, CHAT_REPOS §12.1 G2)
/// prompt the paired Telegram user once with the 6-digit code and Approve/Decline options, ignore
/// forged or reused nonces, and call `/api/telegram/pending-pairs/approve` only when approved.
#[tokio::test]
async fn a_pending_mini_app_launch_pair_is_prompted_once_and_approved_only_by_its_nonce() {
    let mut rig = rig("launch-pair", default_script(), false, |config| config.pair_interval = Duration::from_millis(80));
    tokio::time::sleep(Duration::from_millis(250)).await;
    let mut prompts = Vec::new();
    while let Ok(out) = rig.telegram.try_recv() {
        if let Outbound::Approval { chat, id, text, options } = out {
            prompts.push((chat, id, text, options));
        }
    }
    assert_eq!(prompts.len(), 1, "prompted once per request id: {prompts:?}");
    let (chat, nonce, prompt_text, options) = &prompts[0];
    assert_eq!(chat, CHAT);
    assert_eq!(prompt_text, "A Mini App on a new device asks to pair (code 482 913).");
    assert_eq!(options, &["Approve".to_string(), "Decline".to_string()]);

    // Plain chat text ("yes", "approve", "1") never approves a LaunchPair; only the callback nonce does.
    for word in ["yes", "approve", "1"] {
        rig.inbound.send(text(CHAT, word)).unwrap();
        settle().await;
    }
    while rig.telegram.try_recv().is_ok() {}

    // A forged nonce is ignored and approves nothing.
    rig.inbound
        .send(Inbound::Answer { chat: CHAT.into(), approval: "deadbeef00000000".into(), choice: 0 })
        .unwrap();
    settle().await;
    let approved_calls = || {
        rig.log
            .params("web")
            .into_iter()
            .filter(|p| p["path"] == "/api/telegram/pending-pairs/approve")
            .collect::<Vec<_>>()
    };
    assert!(approved_calls().is_empty(), "plain text or forged nonce must not approve");

    // Pressing Approve with the issued nonce (even from a topic chat key `telegram:7/10`) approves once.
    rig.inbound
        .send(Inbound::Answer { chat: "telegram:7/10".into(), approval: nonce.clone(), choice: 0 })
        .unwrap();
    let closed = next_settled(&mut rig.telegram).await;
    assert_eq!(
        closed,
        Outbound::ApprovalClosed { chat: CHAT.into(), id: nonce.clone(), outcome: "Approved.".into() }
    );
    settle().await;
    let calls = approved_calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0]["body"]["id"], "pair-req-1");

    // Reusing the same nonce after it closed does not approve again.
    rig.inbound
        .send(Inbound::Answer { chat: CHAT.into(), approval: nonce.clone(), choice: 0 })
        .unwrap();
    let notice = next_settled(&mut rig.telegram).await;
    assert_eq!(notice, Outbound::Notice { chat: CHAT.into(), text: "That request is no longer open.".into() });
    settle().await;
    assert_eq!(approved_calls().len(), 1, "reused nonce must not approve again");
}

/// When a new launch from the same Telegram user replaces an earlier unapproved launch pair, the
/// earlier prompt in the bot is closed as declined and only the new prompt's nonce can approve.
#[tokio::test]
async fn a_new_launch_pair_from_the_same_user_closes_the_old_prompt_as_declined() {
    let dir = scratch("launch-pair-replace");
    let (agent, mut commands) = AgentHandle::channel();
    let polls = Arc::new(Mutex::new(0u32));
    let approved_ids = Arc::new(Mutex::new(Vec::<String>::new()));
    let polls_clone = polls.clone();
    let approved_clone = approved_ids.clone();
    tokio::spawn(async move {
        while let Some(command) = commands.recv().await {
            if let AgentCommand::Web { path, body, reply } = command {
                let answer = if path == "/api/telegram/pending-pairs" {
                    let mut n = polls_clone.lock().unwrap();
                    *n += 1;
                    if *n == 1 {
                        json!({ "pending": [{ "id": "pair-req-1", "code": "111 222", "user": 7 }] })
                    } else {
                        json!({ "pending": [{ "id": "pair-req-2", "code": "333 444", "user": 7 }] })
                    }
                } else if path == "/api/telegram/pending-pairs/approve" {
                    if let Some(id) = body.as_ref().and_then(|b| b.get("id")).and_then(Value::as_str) {
                        approved_clone.lock().unwrap().push(id.to_string());
                    }
                    json!({ "ok": true })
                } else {
                    json!({})
                };
                let _ = reply.send(Ok(answer));
            }
        }
    });

    let (out_tx, mut out_rx) = mpsc::unbounded_channel();
    let mut hub = Hub::new(HubConfig::new(dir), agent, Box::new(|| false));
    hub.add_adapter("telegram", out_tx);

    // First poll prompts pair-req-1.
    hub.push_pending_pairs().await;
    let Outbound::Approval { id: nonce1, text: text1, .. } = out_rx.try_recv().expect("first prompt") else {
        panic!("expected first Approval");
    };
    assert_eq!(text1, "A Mini App on a new device asks to pair (code 111 222).");

    // Second poll sees pair-req-2 has replaced pair-req-1: closes nonce1 as Declined and prompts nonce2.
    hub.push_pending_pairs().await;
    let closed1 = out_rx.try_recv().expect("old prompt closed");
    assert_eq!(
        closed1,
        Outbound::ApprovalClosed { chat: CHAT.into(), id: nonce1.clone(), outcome: "Declined.".into() }
    );
    let Outbound::Approval { id: nonce2, text: text2, .. } = out_rx.try_recv().expect("second prompt") else {
        panic!("expected second Approval");
    };
    assert_eq!(text2, "A Mini App on a new device asks to pair (code 333 444).");
    assert_ne!(nonce1, nonce2);

    // Pressing the old prompt's nonce does not approve anything.
    hub.on_inbound(Inbound::Answer { chat: CHAT.into(), approval: nonce1, choice: 0 }).await;
    assert_eq!(
        out_rx.try_recv().expect("closed notice"),
        Outbound::Notice { chat: CHAT.into(), text: "That request is no longer open.".into() }
    );
    settle().await;
    assert!(approved_ids.lock().unwrap().is_empty());

    // Pressing the new prompt's nonce approves pair-req-2.
    hub.on_inbound(Inbound::Answer { chat: CHAT.into(), approval: nonce2.clone(), choice: 0 }).await;
    assert_eq!(
        out_rx.try_recv().expect("approved close"),
        Outbound::ApprovalClosed { chat: CHAT.into(), id: nonce2, outcome: "Approved.".into() }
    );
    settle().await;
    assert_eq!(*approved_ids.lock().unwrap(), vec!["pair-req-2".to_string()]);
}
