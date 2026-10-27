//! The Telegram adapter against a stand-in Bot API (an axum server on loopback): never Telegram.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use axum::Json;
use axum::Router;
use axum::extract::Path;
use axum::extract::State;
use axum::http::StatusCode;
use axum::routing::post;
use ling_chat::hub::Inbound;
use ling_chat::hub::Outbound;
use ling_chat::store;
use ling_chat::store::TelegramConfig;
use ling_chat::telegram::Poller;
use ling_chat::telegram::Renderer;
use ling_chat::telegram::TelegramApi;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;

const TOKEN: &str = "123456:SECRET-token";

type Answer = Arc<dyn Fn(&str, &Value, usize) -> (StatusCode, Value) + Send + Sync>;

#[derive(Clone)]
struct Stand {
    calls: Arc<Mutex<Vec<(String, Value)>>>,
    answer: Answer,
}

impl Stand {
    fn methods(&self) -> Vec<String> {
        self.calls.lock().unwrap().iter().map(|(m, _)| m.clone()).collect()
    }
    fn bodies(&self, method: &str) -> Vec<Value> {
        self.calls.lock().unwrap().iter().filter(|(m, _)| m == method).map(|(_, b)| b.clone()).collect()
    }
}

async fn handle(State(stand): State<Stand>, Path((bot, method)): Path<(String, String)>, Json(body): Json<Value>) -> (StatusCode, Json<Value>) {
    assert_eq!(bot, format!("bot{TOKEN}"));
    let index = {
        let mut calls = stand.calls.lock().unwrap();
        calls.push((method.clone(), body.clone()));
        calls.iter().filter(|(m, _)| *m == method).count() - 1
    };
    let (status, value) = (stand.answer)(&method, &body, index);
    (status, Json(value))
}

/// `sendDocument`: a multipart upload; the body is kept for the test to read.
async fn document(State(stand): State<Stand>, body: axum::body::Bytes) -> (StatusCode, Json<Value>) {
    stand.calls.lock().unwrap().push(("sendDocument".to_string(), json!({ "raw": String::from_utf8_lossy(&body) })));
    (StatusCode::OK, Json(json!({ "ok": true, "result": { "message_id": 77 } })))
}

/// The Bot API's file download: the bytes of `photos/<name>`, named in them.
async fn file(Path((bot, path)): Path<(String, String)>) -> (StatusCode, Vec<u8>) {
    assert_eq!(bot, format!("bot{TOKEN}"));
    (StatusCode::OK, format!("BYTES:{path}").into_bytes())
}

fn ok(result: Value) -> (StatusCode, Value) {
    (StatusCode::OK, json!({ "ok": true, "result": result }))
}

async fn stand_in(answer: Answer) -> (Stand, TelegramApi) {
    let stand = Stand { calls: Arc::default(), answer };
    let app = Router::new()
        .route("/{bot}/{method}", post(handle))
        .route("/file/{bot}/{*path}", axum::routing::get(file))
        .route("/{bot}/sendDocument", post(document))
        .with_state(stand.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let root = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (stand, TelegramApi::new(&root, TOKEN))
}

fn default_answer() -> Answer {
    Arc::new(|method, _, _| match method {
        "sendMessage" => ok(json!({ "message_id": 900 })),
        "getMe" => ok(json!({ "id": 1, "username": "my_mightling_bot" })),
        _ => ok(json!(true)),
    })
}

fn scratch(tag: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("ling-chat-tg-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let mut config = TelegramConfig::new(TOKEN.to_string());
    config.users = vec![111];
    config.save(&dir).unwrap();
    dir
}

/// A press of an inline button by `user`, in their private chat with the bot.
fn callback(user: i64, data: &str) -> Value {
    json!({ "update_id": 90, "callback_query": { "id": "cbp", "from": { "id": user }, "data": data,
        "message": { "message_id": 9, "chat": { "id": user, "type": "private" } } } })
}

fn private_message(user: i64, text: &str) -> Value {
    json!({ "update_id": 1, "message": { "message_id": 5, "from": { "id": user }, "chat": { "id": user, "type": "private" }, "text": text } })
}

#[tokio::test]
async fn a_paired_users_message_reaches_the_hub_and_strangers_get_one_reply_a_day() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("paired");
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let mut poller = Poller::new(api, dir, hub);
    poller.handle(&private_message(111, "hello")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111".into(), text: "hello".into() });
    poller.handle(&private_message(222, "let me in")).await;
    poller.handle(&private_message(222, "please")).await;
    assert!(inbound.try_recv().is_err());
    let replies = stand.bodies("sendMessage");
    assert_eq!(replies.len(), 1);
    assert_eq!(replies[0]["chat_id"], 222);
    assert_eq!(replies[0]["text"], ling_chat::telegram::UNPAIRED_REPLY);
}

/// A photo (its largest size) and its caption reach the hub as media; what the bridge does not
/// take is named, never silently lost, and never sent on as an empty message.
#[tokio::test]
async fn a_photo_and_a_voice_message_reach_the_hub_and_a_sticker_is_named_as_dropped() {
    let answer: Answer = Arc::new(|method, body, _| match method {
        "getFile" => ok(json!({ "file_id": body["file_id"], "file_path": format!("photos/{}.jpg", body["file_id"].as_str().unwrap()) })),
        _ => ok(json!({ "message_id": 1 })),
    });
    let (stand, api) = stand_in(answer).await;
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let mut poller = Poller::new(api, scratch("photo"), hub);
    let photo = json!({ "update_id": 6, "message": { "message_id": 7, "from": { "id": 111 }, "chat": { "id": 111, "type": "private" },
        "caption": "compare it with a Dyson",
        "photo": [ { "file_id": "small", "width": 90, "height": 160 }, { "file_id": "big", "width": 1080, "height": 1920 } ] } });
    poller.handle(&photo).await;
    let Inbound::Media { chat, text, attachments, dropped } = inbound.try_recv().unwrap() else { panic!("not media") };
    assert_eq!((chat.as_str(), text.as_str()), ("telegram:111", "compare it with a Dyson"));
    assert_eq!(attachments.len(), 1);
    assert_eq!((attachments[0].name.as_str(), attachments[0].mime.as_str()), ("photo.jpg", "image/jpeg"));
    assert_eq!(attachments[0].bytes, b"BYTES:photos/big.jpg");
    assert!(dropped.is_empty());
    assert_eq!(stand.bodies("getFile"), vec![json!({ "file_id": "big" })]);
    // A voice message is downloaded to be transcribed on the node (MOONSHOT §3.4).
    let voice = json!({ "update_id": 8, "message": { "message_id": 9, "from": { "id": 111 }, "chat": { "id": 111, "type": "private" }, "voice": { "file_id": "v", "duration": 3, "mime_type": "audio/ogg" } } });
    poller.handle(&voice).await;
    let Inbound::Media { attachments, dropped, .. } = inbound.try_recv().unwrap() else { panic!("not media") };
    assert_eq!((attachments[0].name.as_str(), attachments[0].mime.as_str(), attachments[0].bytes.as_slice()), ("voice.ogg", "audio/ogg", b"BYTES:photos/v.jpg".as_slice()));
    assert!(dropped.is_empty());
    // A sticker is named as dropped.
    let sticker = json!({ "update_id": 10, "message": { "message_id": 11, "from": { "id": 111 }, "chat": { "id": 111, "type": "private" }, "sticker": { "file_id": "s" } } });
    poller.handle(&sticker).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Media { chat: "telegram:111".into(), text: String::new(), attachments: vec![], dropped: vec!["a sticker".into()] });
}

#[tokio::test]
async fn groups_are_left_at_once() {
    let (stand, api) = stand_in(default_answer()).await;
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let mut poller = Poller::new(api, scratch("group"), hub);
    let update = json!({ "update_id": 2, "message": { "from": { "id": 111 }, "chat": { "id": -100, "type": "supergroup" }, "text": "hi bot" } });
    poller.handle(&update).await;
    assert!(inbound.try_recv().is_err());
    assert_eq!(stand.bodies("leaveChat"), vec![json!({ "chat_id": -100 })]);
}

#[tokio::test]
async fn pairing_takes_a_code_once_and_ten_wrong_codes_withdraw_the_rest() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("pair");
    let (hub, _inbound) = mpsc::unbounded_channel();
    let mut poller = Poller::new(api, dir.clone(), hub);
    let code = store::issue_pairing_code(&dir).unwrap();
    poller.handle(&private_message(333, &format!("/pair {code}"))).await;
    assert_eq!(TelegramConfig::load(&dir).unwrap().users, vec![111, 333]);
    assert!(stand.bodies("sendMessage").last().unwrap()["text"].as_str().unwrap().starts_with("Paired."));
    // The same code again, from someone else: refused.
    poller.handle(&private_message(444, &format!("/pair {code}"))).await;
    assert_eq!(TelegramConfig::load(&dir).unwrap().users, vec![111, 333]);
    // Ten wrong codes withdraw a pending good one.
    let good = store::issue_pairing_code(&dir).unwrap();
    for n in 0..9 {
        poller.handle(&private_message(444, &format!("/pair {:08}", n))).await;
    }
    poller.handle(&private_message(444, "/pair 99999999")).await;
    poller.handle(&private_message(444, &format!("/pair {good}"))).await;
    assert_eq!(TelegramConfig::load(&dir).unwrap().users, vec![111, 333]);
}

#[tokio::test]
async fn buttons_and_the_stop_button_come_from_paired_users_only() {
    let (stand, api) = stand_in(default_answer()).await;
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let mut poller = Poller::new(api, scratch("buttons"), hub);
    let press = |user: i64| {
        json!({ "update_id": 3, "callback_query": { "id": "cb1", "from": { "id": user }, "data": "a:00ff4a:1", "message": { "message_id": 9, "chat": { "id": 111, "type": "private" } } } })
    };
    poller.handle(&press(999)).await;
    assert!(inbound.try_recv().is_err());
    poller.handle(&press(111)).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Answer { chat: "telegram:111".into(), approval: "00ff4a".into(), choice: 1 });
    assert_eq!(stand.bodies("answerCallbackQuery").len(), 2);
    poller.handle(&json!({ "update_id": 4, "stopped_message_generation": { "chat": { "id": 111, "type": "private" }, "draft_id": 1 } })).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Stop { chat: "telegram:111".into() });
}

#[tokio::test]
async fn drafts_stream_with_a_stop_button_and_fall_back_to_typing() {
    let (stand, api) = stand_in(default_answer()).await;
    let renderer = Renderer::new(api);
    renderer.render(Outbound::Draft { chat: "telegram:111".into(), text: String::new() }).await;
    renderer.render(Outbound::Draft { chat: "telegram:111".into(), text: "Partial".into() }).await;
    let drafts = stand.bodies("sendMessageDraft");
    assert_eq!(drafts.len(), 2);
    assert_eq!(drafts[0]["text"], "");
    assert_eq!(drafts[1]["text"], "Partial");
    assert_eq!(drafts[0]["draft_id"], drafts[1]["draft_id"]);
    assert_eq!(drafts[1]["can_stop"], true);
    renderer.render(Outbound::TurnEnded { chat: "telegram:111".into() }).await;
    renderer.render(Outbound::Draft { chat: "telegram:111".into(), text: "Next turn".into() }).await;
    assert_ne!(stand.bodies("sendMessageDraft")[2]["draft_id"], drafts[0]["draft_id"]);

    // An older Bot API without drafts: typing instead.
    let old: Answer = Arc::new(|method, _, _| match method {
        "sendMessageDraft" => (StatusCode::NOT_FOUND, json!({ "ok": false, "error_code": 404, "description": "Not Found" })),
        _ => ok(json!(true)),
    });
    let (stand, api) = stand_in(old).await;
    let renderer = Renderer::new(api);
    renderer.render(Outbound::Draft { chat: "telegram:111".into(), text: "a".into() }).await;
    renderer.render(Outbound::Draft { chat: "telegram:111".into(), text: "ab".into() }).await;
    assert_eq!(stand.methods(), vec!["sendMessageDraft", "sendChatAction"]);
    assert_eq!(stand.bodies("sendChatAction")[0]["action"], "typing");
}

#[tokio::test]
async fn answers_are_html_split_under_the_limit_with_a_plain_fallback() {
    let (stand, api) = stand_in(default_answer()).await;
    let renderer = Renderer::new(api);
    let long = format!("**Title**\n\n{}", "A sentence of the answer. ".repeat(400));
    renderer.render(Outbound::Message { chat: "telegram:111".into(), markdown: long }).await;
    let sent = stand.bodies("sendMessage");
    assert!(sent.len() >= 3);
    assert!(sent.iter().all(|m| m["parse_mode"] == "HTML" && m["text"].as_str().unwrap().chars().count() <= 4096));
    assert!(sent[0]["text"].as_str().unwrap().starts_with("<b>Title</b>"));

    let picky: Answer = Arc::new(|method, body, _| {
        if method == "sendMessage" && body.get("parse_mode").is_some() {
            (StatusCode::BAD_REQUEST, json!({ "ok": false, "error_code": 400, "description": "Bad Request: can't parse entities: unsupported start tag" }))
        } else {
            ok(json!({ "message_id": 1 }))
        }
    });
    let (stand, api) = stand_in(picky).await;
    Renderer::new(api).render(Outbound::Message { chat: "telegram:111".into(), markdown: "**x**".into() }).await;
    let sent = stand.bodies("sendMessage");
    assert_eq!(sent.len(), 2);
    assert_eq!(sent[1], json!({ "chat_id": 111, "text": "**x**" }));
}

#[tokio::test]
async fn approvals_are_coloured_buttons_and_closing_one_leaves_its_outcome_as_a_disabled_button() {
    let (stand, api) = stand_in(default_answer()).await;
    let renderer = Renderer::new(api);
    renderer
        .render(Outbound::Approval { chat: "telegram:111".into(), id: "9a7f".into(), text: "Run `ls`?".into(), options: vec!["Approve once".into(), "Decline".into()] })
        .await;
    let sent = &stand.bodies("sendMessage")[0];
    assert_eq!(sent["reply_markup"]["inline_keyboard"][0][0], json!({ "text": "Approve once", "callback_data": "a:9a7f:0", "style": "success" }));
    assert_eq!(sent["reply_markup"]["inline_keyboard"][1][0], json!({ "text": "Decline", "callback_data": "a:9a7f:1", "style": "danger" }));
    // A question's own options are not painted as approvals.
    renderer.render(Outbound::Approval { chat: "telegram:111".into(), id: "c0".into(), text: "Which?".into(), options: vec!["PDF".into(), "HTML".into(), "Decline".into()] }).await;
    let question = &stand.bodies("sendMessage")[1]["reply_markup"]["inline_keyboard"];
    assert!(question[0][0].get("style").is_none() && question[1][0].get("style").is_none());
    assert_eq!(question[2][0]["style"], "danger");
    renderer.render(Outbound::ApprovalClosed { chat: "telegram:111".into(), id: "9a7f".into(), outcome: "Approved.".into() }).await;
    let edit = &stand.bodies("editMessageText")[0];
    assert_eq!(edit["message_id"], 900);
    assert!(edit["text"].as_str().unwrap().ends_with("<i>Approved.</i>"));
    assert_eq!(edit["reply_markup"], json!({ "inline_keyboard": [[{ "text": "Approved", "disabled": {} }]] }));
}

/// An older Bot API refuses the disabled button: the keyboard is removed instead, so the closed
/// request cannot be pressed again.
#[tokio::test]
async fn a_bot_api_without_disabled_buttons_gets_no_keyboard() {
    let old: Answer = Arc::new(|method, body, _| match method {
        "editMessageText" if body["reply_markup"].to_string().contains("disabled") => {
            (StatusCode::BAD_REQUEST, json!({ "ok": false, "error_code": 400, "description": "Bad Request: can't parse inline keyboard button" }))
        }
        _ => ok(json!({ "message_id": 900 })),
    });
    let (stand, api) = stand_in(old).await;
    let renderer = Renderer::new(api);
    renderer.render(Outbound::Approval { chat: "telegram:111".into(), id: "ab".into(), text: "Run?".into(), options: vec!["Approve once".into(), "Decline".into()] }).await;
    renderer.render(Outbound::ApprovalClosed { chat: "telegram:111".into(), id: "ab".into(), outcome: "Declined: no answer in time.".into() }).await;
    let edits = stand.bodies("editMessageText");
    assert_eq!(edits.len(), 2);
    assert_eq!(edits[1]["reply_markup"], json!({ "inline_keyboard": [] }));
}

/// A callback names a nonce, never a counter; anything else is ignored.
#[test]
fn approval_callbacks_carry_a_hex_nonce() {
    use ling_chat::telegram::approval_callback;
    assert_eq!(approval_callback("a:0123abcd0123abcd:1"), Some(("0123abcd0123abcd".to_string(), 1)));
    assert_eq!(approval_callback("a:zz:1"), None);
    assert_eq!(approval_callback("a::1"), None);
    assert_eq!(approval_callback("a:ab:1:2"), None);
    assert_eq!(approval_callback("b:ab:1"), None);
}

#[tokio::test]
async fn rate_limits_are_waited_out() {
    let limited: Answer = Arc::new(|method, _, index| {
        if method == "sendMessage" && index == 0 {
            (StatusCode::TOO_MANY_REQUESTS, json!({ "ok": false, "error_code": 429, "description": "Too Many Requests", "parameters": { "retry_after": 1 } }))
        } else {
            ok(json!({ "message_id": 1 }))
        }
    });
    let (stand, api) = stand_in(limited).await;
    let started = std::time::Instant::now();
    api.call("sendMessage", json!({ "chat_id": 1, "text": "x" })).await.unwrap();
    assert!(started.elapsed() >= Duration::from_millis(900));
    assert_eq!(stand.bodies("sendMessage").len(), 2);
}

#[tokio::test]
async fn the_token_never_appears_in_an_error() {
    // Nothing listens on this port.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener);
    let api = TelegramApi::new(&format!("http://127.0.0.1:{port}"), TOKEN);
    let err = api.call("getMe", json!({})).await.unwrap_err();
    assert!(!err.description.contains("SECRET"), "{}", err.description);
    assert!(!format!("{err:?}").contains("SECRET"));
}

#[tokio::test]
async fn an_approval_whose_html_is_refused_still_shows_its_buttons() {
    let picky: Answer = Arc::new(|method, body, _| {
        if method == "sendMessage" && body.get("parse_mode").is_some() {
            (StatusCode::BAD_REQUEST, json!({ "ok": false, "error_code": 400, "description": "Bad Request: can't parse entities" }))
        } else {
            ok(json!({ "message_id": 901 }))
        }
    });
    let (stand, api) = stand_in(picky).await;
    let renderer = Renderer::new(api);
    renderer
        .render(Outbound::Approval { chat: "telegram:111".into(), id: "8".into(), text: "Run <this>?".into(), options: vec!["Approve once".into(), "Decline".into()] })
        .await;
    let sent = stand.bodies("sendMessage");
    assert_eq!(sent.len(), 2);
    assert_eq!(sent[1]["text"], "Run <this>?");
    assert_eq!(sent[1]["reply_markup"]["inline_keyboard"][1][0]["callback_data"], "a:8:1");
}

#[tokio::test]
async fn telegram_starts_even_when_it_does_not_answer_yet() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener);
    let api = TelegramApi::new(&format!("http://127.0.0.1:{port}"), TOKEN);
    let (hub, _inbound) = mpsc::unbounded_channel();
    let (out_tx, out_rx) = mpsc::unbounded_channel();
    let started = ling_chat::telegram::start(api, scratch("unreachable"), hub, out_rx, ling_chat::telegram::Options::default()).await;
    assert!(started.is_err());
    // The renderer is running all the same: its channel is still open.
    assert!(out_tx.send(Outbound::TurnEnded { chat: "telegram:111".into() }).is_ok());
}

fn topic_message(user: i64, topic: i64, text: &str) -> Value {
    json!({ "update_id": 20, "message": { "message_id": 21, "from": { "id": user }, "chat": { "id": user, "type": "private" },
        "message_thread_id": topic, "is_topic_message": true, "text": text } })
}

/// Topics as threads (MOONSHOT §3.1): a message in a topic is that topic's conversation; `/new`
/// makes a topic; the stop button and a button press name their topic; service messages are not
/// questions.
#[tokio::test]
async fn topics_are_conversations_of_their_own_and_new_makes_one() {
    let answer: Answer = Arc::new(|method, _, _| match method {
        "createForumTopic" => ok(json!({ "message_thread_id": 77, "name": "New thread", "icon_color": 7322096 })),
        _ => ok(json!({ "message_id": 1 })),
    });
    let (stand, api) = stand_in(answer).await;
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let mut poller = Poller::new(api, scratch("topics"), hub);
    poller.topics = true;
    poller.handle(&topic_message(111, 5, "about purifiers")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/5".into(), text: "about purifiers".into() });
    // Outside any topic: continues the topic used last in that chat.
    poller.handle(&private_message(111, "hello")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/5".into(), text: "hello".into() });
    // /new: a topic, and a line in it; nothing for the hub until its first message.
    poller.handle(&topic_message(111, 5, "/new")).await;
    assert!(inbound.try_recv().is_err());
    assert_eq!(stand.bodies("createForumTopic"), vec![json!({ "chat_id": 111, "name": "New thread" })]);
    assert_eq!(stand.bodies("sendMessage").last().unwrap()["message_thread_id"], 77);
    // A service message is not a question.
    let created = json!({ "update_id": 22, "message": { "message_id": 23, "from": { "id": 111 }, "chat": { "id": 111, "type": "private" },
        "message_thread_id": 9, "is_topic_message": true, "forum_topic_created": { "name": "Mine", "icon_color": 7322096 } } });
    poller.handle(&created).await;
    assert!(inbound.try_recv().is_err());
    poller.handle(&json!({ "update_id": 24, "stopped_message_generation": { "chat": { "id": 111, "type": "private" }, "message_thread_id": 5, "draft_id": 3 } })).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Stop { chat: "telegram:111/5".into() });
    let press = json!({ "update_id": 25, "callback_query": { "id": "cb", "from": { "id": 111 }, "data": "a:beef:0",
        "message": { "message_id": 30, "chat": { "id": 111, "type": "private" }, "message_thread_id": 5, "is_topic_message": true } } });
    poller.handle(&press).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Answer { chat: "telegram:111/5".into(), approval: "beef".into(), choice: 0 });
    // Without topics on the bot, /new is the hub's.
    let mut plain = Poller::new(stand_in(default_answer()).await.1, scratch("notopics"), poller.hub.clone());
    plain.handle(&private_message(111, "/new")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111".into(), text: "/new".into() });
}

/// With topics on, a message in the main chat (outside any topic) continues the topic used last in
/// that chat; the first message ever opens a topic; `/new` opens a fresh topic and makes it the
/// last one; and if the last topic was deleted or closed (`TOPIC_DELETED`, "message thread not
/// found", or `forum_topic_closed`), the bridge opens a new topic and remembers it.
#[tokio::test]
async fn main_chat_messages_continue_the_last_topic_and_open_a_new_one_when_missing_or_deleted() {
    let gone: Arc<Mutex<HashMap<i64, &'static str>>> = Arc::default();
    let gone_for_stand = gone.clone();
    let answer: Answer = Arc::new(move |method, body, index| {
        if method == "createForumTopic" {
            return ok(json!({ "message_thread_id": 10 + index as i64, "name": "New thread", "icon_color": 7322096 }));
        }
        if let Some(topic) = body.get("message_thread_id").and_then(Value::as_i64)
            && let Some(reason) = gone_for_stand.lock().unwrap().get(&topic).copied()
        {
            return (StatusCode::BAD_REQUEST, json!({ "ok": false, "error_code": 400, "description": reason }));
        }
        ok(json!({ "message_id": 1 }))
    });
    let (stand, api) = stand_in(answer).await;
    let dir = scratch("last-topic");
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let renderer = Renderer::new(api.clone()).with_dir(dir.clone());
    let mut poller = Poller::new(api, dir.clone(), hub);
    poller.topics = true;

    // First message ever (no last topic yet): opens topic 10 and remembers it.
    assert_eq!(store::last_topic(&dir, 111), None);
    poller.handle(&private_message(111, "first question")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/10".into(), text: "first question".into() });
    assert_eq!(store::last_topic(&dir, 111), Some(10));
    assert_eq!(stand.bodies("createForumTopic").len(), 1);

    // Next message outside any topic continues topic 10; the reply is sent with message_thread_id 10.
    poller.handle(&private_message(111, "follow up outside")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/10".into(), text: "follow up outside".into() });
    assert_eq!(stand.bodies("createForumTopic").len(), 1);
    renderer.render(Outbound::Message { chat: "telegram:111/10".into(), markdown: "Answered in 10.".into() }).await;
    assert_eq!(stand.bodies("sendMessage").last().unwrap()["message_thread_id"], 10);

    // User sends in topic 25, then in the main chat: continues 25; then the bot answers in 10, so
    // the next main-chat message goes back to 10.
    poller.handle(&topic_message(111, 25, "inside 25")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/25".into(), text: "inside 25".into() });
    poller.handle(&private_message(111, "continues 25")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/25".into(), text: "continues 25".into() });
    renderer.render(Outbound::Message { chat: "telegram:111/10".into(), markdown: "Late answer in 10.".into() }).await;
    poller.handle(&private_message(111, "after bot answered in 10")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/10".into(), text: "after bot answered in 10".into() });

    // /new opens a fresh topic (11) and remembers it as the last topic.
    poller.handle(&private_message(111, "/new")).await;
    assert!(inbound.try_recv().is_err());
    assert_eq!(store::last_topic(&dir, 111), Some(11));
    poller.handle(&private_message(111, "in the new topic")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/11".into(), text: "in the new topic".into() });
    assert_eq!(stand.bodies("createForumTopic").len(), 2);

    // Topic 11 is deleted (TOPIC_DELETED): a main-chat message opens topic 12 and remembers it.
    gone.lock().unwrap().insert(11, "Bad Request: TOPIC_DELETED");
    poller.handle(&private_message(111, "after 11 deleted")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/12".into(), text: "after 11 deleted".into() });
    assert_eq!(store::last_topic(&dir, 111), Some(12));
    poller.handle(&private_message(111, "still in 12")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/12".into(), text: "still in 12".into() });
    assert_eq!(stand.bodies("createForumTopic").len(), 3);

    // Topic 12 is gone ("message thread not found"): falls back to opening topic 13.
    gone.lock().unwrap().insert(12, "Bad Request: message thread not found");
    poller.handle(&private_message(111, "after 12 not found")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/13".into(), text: "after 12 not found".into() });
    assert_eq!(store::last_topic(&dir, 111), Some(13));

    // Topic 13 is closed (`forum_topic_closed` service message): next main-chat message opens topic 14.
    let closed = json!({ "update_id": 99, "message": { "message_id": 100, "from": { "id": 111 }, "chat": { "id": 111, "type": "private" },
        "message_thread_id": 13, "is_topic_message": true, "forum_topic_closed": {} } });
    poller.handle(&closed).await;
    assert_eq!(store::last_topic(&dir, 111), None);
    poller.handle(&private_message(111, "after 13 closed")).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "telegram:111/14".into(), text: "after 13 closed".into() });
    assert_eq!(store::last_topic(&dir, 111), Some(14));
}

#[tokio::test]
async fn a_topics_output_goes_to_its_topic_and_its_thread_names_it() {
    let (stand, api) = stand_in(default_answer()).await;
    let renderer = Renderer::new(api);
    let key = "telegram:111/5".to_string();
    renderer.render(Outbound::Draft { chat: key.clone(), text: "Part".into() }).await;
    renderer.render(Outbound::Message { chat: key.clone(), markdown: "Done.".into() }).await;
    renderer.render(Outbound::Notice { chat: key.clone(), text: "Stopped.".into() }).await;
    renderer.render(Outbound::ThreadNamed { chat: key.clone(), name: "Two purifiers".into() }).await;
    assert_eq!(stand.bodies("sendMessageDraft")[0]["message_thread_id"], 5);
    assert_eq!(stand.bodies("sendMessageDraft")[0]["keep_on_stop"], true);
    assert!(stand.bodies("sendMessage").iter().all(|m| m["message_thread_id"] == 5));
    assert_eq!(stand.bodies("editForumTopic"), vec![json!({ "chat_id": 111, "message_thread_id": 5, "name": "Two purifiers" })]);
    // Another topic of the same chat, within a second: its draft waits (one message a second per chat).
    renderer.render(Outbound::Draft { chat: "telegram:111/6".into(), text: "Other".into() }).await;
    assert_eq!(stand.bodies("sendMessageDraft").len(), 1);
    // The chat without topics is never renamed.
    renderer.render(Outbound::ThreadNamed { chat: "telegram:111".into(), name: "x".into() }).await;
    assert_eq!(stand.bodies("editForumTopic").len(), 1);
}

/// Rich messages (MOONSHOT §3.2, `[chat] telegram_rich`): drafts and answers as blocks the bridge
/// built, the thinking placeholder first; refused, the answer goes as HTML and nothing is lost.
#[tokio::test]
async fn rich_drafts_and_answers_are_blocks_and_a_refusal_falls_back_to_html() {
    use ling_chat::telegram::Style;
    let (stand, api) = stand_in(default_answer()).await;
    let renderer = Renderer::styled(api, Style { rich: true });
    let key = "telegram:111".to_string();
    renderer.render(Outbound::Draft { chat: key.clone(), text: String::new() }).await;
    renderer.render(Outbound::Draft { chat: key.clone(), text: "**Levoit** is quieter".into() }).await;
    let drafts = stand.bodies("sendRichMessageDraft");
    assert_eq!(drafts[0]["rich_message"]["blocks"], json!([{ "type": "thinking", "text": "Thinking…" }]));
    assert_eq!(drafts[1]["rich_message"]["blocks"][0]["text"][0], json!({ "type": "bold", "text": "Levoit" }));
    assert_eq!((drafts[1]["can_stop"].clone(), drafts[1]["keep_on_stop"].clone()), (json!(true), json!(true)));
    renderer.render(Outbound::Message { chat: key.clone(), markdown: "| A | B |\n|---|---|\n| 1 | <b>2</b> |".into() }).await;
    let sent = stand.bodies("sendRichMessage");
    assert_eq!(sent[0]["rich_message"]["blocks"][0]["type"], "table");
    assert!(stand.bodies("sendMessage").is_empty(), "no HTML copy");
    assert!(!sent[0]["rich_message"].as_object().unwrap().contains_key("markdown") && !sent[0]["rich_message"].as_object().unwrap().contains_key("html"));

    let refuse: Answer = Arc::new(|method, _, _| match method {
        "sendRichMessage" | "sendRichMessageDraft" => (StatusCode::NOT_FOUND, json!({ "ok": false, "error_code": 404, "description": "Not Found" })),
        _ => ok(json!({ "message_id": 1 })),
    });
    let (stand, api) = stand_in(refuse).await;
    let renderer = Renderer::styled(api, Style { rich: true });
    renderer.render(Outbound::Message { chat: key.clone(), markdown: "**x**".into() }).await;
    assert_eq!(stand.bodies("sendMessage")[0]["text"], "<b>x</b>");
    // Refused once for good (an older Bot API): drafts go plain too.
    renderer.render(Outbound::Draft { chat: key.clone(), text: "y".into() }).await;
    assert_eq!(stand.bodies("sendRichMessage").len(), 1);
    assert!(stand.bodies("sendRichMessageDraft").is_empty());
    assert_eq!(stand.bodies("sendMessageDraft").len(), 1);
}

const MINI_APP: &str = "https://node.example.ts.net/";

/// The Mini App (MOONSHOT §15): with an address set, `/app` is offered and every paired user's
/// menu button opens it; without one, the buttons go back to Telegram's default.
#[tokio::test]
async fn the_mini_app_is_the_paired_users_menu_button_and_follows_the_setting() {
    let (stand, api) = stand_in(default_answer()).await;
    let (hub, _inbound) = mpsc::unbounded_channel();
    let (_out_tx, out_rx) = mpsc::unbounded_channel();
    let options = ling_chat::telegram::Options { mini_app_url: Some(MINI_APP.to_string()), ..Default::default() };
    ling_chat::telegram::start(api.clone(), scratch("miniapp-on"), hub.clone(), out_rx, options).await.unwrap();
    let commands = &stand.bodies("setMyCommands")[0]["commands"];
    assert!(commands.as_array().unwrap().iter().any(|c| c["command"] == "app"));
    let buttons = stand.bodies("setChatMenuButton");
    assert_eq!(buttons, vec![json!({ "chat_id": 111, "menu_button": { "type": "web_app", "text": "Mightling", "web_app": { "url": MINI_APP } } })]);

    let (stand, api) = stand_in(default_answer()).await;
    let (_out_tx, out_rx) = mpsc::unbounded_channel();
    ling_chat::telegram::start(api, scratch("miniapp-off"), hub, out_rx, ling_chat::telegram::Options::default()).await.unwrap();
    assert!(!stand.bodies("setMyCommands")[0]["commands"].as_array().unwrap().iter().any(|c| c["command"] == "app"));
    assert_eq!(stand.bodies("setChatMenuButton"), vec![json!({ "chat_id": 111, "menu_button": { "type": "default" } })]);
}

#[tokio::test]
async fn app_answers_with_a_web_app_button_or_how_to_set_one_up_and_is_no_question() {
    let (stand, api) = stand_in(default_answer()).await;
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let mut poller = Poller::new(api, scratch("app-cmd"), hub);
    poller.handle(&private_message(111, "/app")).await;
    let reply = stand.bodies("sendMessage").pop().unwrap();
    assert!(reply.get("reply_markup").is_none());
    assert!(reply["text"].as_str().unwrap().contains("telegram_mini_app_url"));
    poller.mini_app_url = Some(MINI_APP.to_string());
    poller.handle(&private_message(111, "/app")).await;
    let reply = stand.bodies("sendMessage").pop().unwrap();
    assert_eq!(reply["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"], MINI_APP);
    assert!(inbound.try_recv().is_err());
    // A stranger gets the one unpaired reply, never the button.
    poller.handle(&private_message(999, "/app")).await;
    assert_eq!(stand.bodies("sendMessage").pop().unwrap()["text"], ling_chat::telegram::UNPAIRED_REPLY);
}

#[tokio::test]
async fn a_newly_paired_user_gets_the_mini_app_button() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("app-pair");
    let (hub, _inbound) = mpsc::unbounded_channel();
    let mut poller = Poller::new(api, dir.clone(), hub);
    poller.mini_app_url = Some(MINI_APP.to_string());
    let code = store::issue_pairing_code(&dir).unwrap();
    poller.handle(&private_message(333, &format!("/pair {code}"))).await;
    assert_eq!(stand.bodies("setChatMenuButton")[0]["chat_id"], 333);
}

#[tokio::test]
async fn a_file_is_sent_as_a_document_in_its_topic() {
    let (stand, api) = stand_in(default_answer()).await;
    let renderer = Renderer::new(api);
    renderer.render(Outbound::File { chat: "telegram:111/5".into(), name: "report \"final\".md".into(), bytes: b"# Purifiers".to_vec() }).await;
    let raw = stand.bodies("sendDocument")[0]["raw"].as_str().unwrap().to_string();
    assert!(raw.contains("name=\"chat_id\"\r\n\r\n111\r\n") && raw.contains("name=\"message_thread_id\"\r\n\r\n5\r\n"), "{raw}");
    assert!(raw.contains("filename=\"report _final_.md\"") && raw.contains("# Purifiers"), "{raw}");
}

/// End-to-end launch pairing through the Telegram adapter (`Poller` + `Renderer` + `Hub` +
/// `telegram_app` state, CHAT_REPOS §12.1 G2): sends the pairing prompt with Approve/Decline inline
/// buttons carrying a random nonce, ignores forged and reused callbacks, and issues the
/// `telegram-app` device credential only when the paired user presses Approve.
#[tokio::test]
async fn launch_pairing_sends_buttons_to_telegram_and_pairs_only_on_the_approve_callback() {
    use ling_chat::agent::{AgentCommand, AgentHandle};
    use ling_chat::hub::{Hub, HubConfig};
    use ling_web_server::auth::{self, DeviceKind, Scope};
    use ling_web_server::telegram_app::{self, ClaimOutcome};

    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("launch-pair-tg");
    let state = dir.join("web-state");
    auth::private_dir(&state).unwrap();

    let created = telegram_app::create_pending_pair(&state, 111, "init-data-1", 1_000).unwrap();
    let state_for_agent = state.clone();
    let (agent, mut commands) = AgentHandle::channel();
    tokio::spawn(async move {
        while let Some(command) = commands.recv().await {
            if let AgentCommand::Web { path, body, reply } = command {
                let answer = if path == "/api/telegram/pending-pairs" {
                    let list = telegram_app::list_pending_pairs(&state_for_agent, 1_010);
                    json!({ "pending": list })
                } else if path == "/api/telegram/pending-pairs/approve" {
                    let id = body.as_ref().and_then(|b| b.get("id")).and_then(Value::as_str).unwrap_or_default();
                    let dev = telegram_app::approve_pending_pair(&state_for_agent, id, 1_010).unwrap();
                    json!({ "ok": dev.is_some(), "device": dev })
                } else if path == "/api/telegram/pending-pairs/decline" {
                    let id = body.as_ref().and_then(|b| b.get("id")).and_then(Value::as_str).unwrap_or_default();
                    let ok = telegram_app::decline_pending_pair(&state_for_agent, id, 1_010).unwrap();
                    json!({ "ok": ok })
                } else {
                    json!({})
                };
                let _ = reply.send(Ok(answer));
            }
        }
    });

    let (inbound_tx, mut inbound_rx) = mpsc::unbounded_channel();
    let (out_tx, mut out_rx) = mpsc::unbounded_channel();
    let mut hub = Hub::new(HubConfig::new(dir.clone()), agent, Box::new(|| false));
    hub.add_adapter("telegram", out_tx);
    let mut poller = Poller::new(api.clone(), dir.clone(), inbound_tx);
    let mut renderer = Renderer::new(api);

    // Hub polls `/api/telegram/pending-pairs` and emits the approval prompt to the Telegram adapter.
    hub.push_pending_pairs().await;
    let out = out_rx.try_recv().expect("approval prompt");
    renderer.render(out).await;

    let sent = stand.bodies("sendMessage").pop().expect("Telegram sendMessage");
    assert_eq!(sent["chat_id"], 111);
    assert_eq!(
        sent["text"],
        format!("A Mini App on a new device asks to pair (code {}).", created.code)
    );
    // One button per row or several in one: the order is what counts.
    let buttons: Vec<Value> =
        sent["reply_markup"]["inline_keyboard"].as_array().unwrap().iter().flat_map(|row| row.as_array().unwrap().clone()).collect();
    assert_eq!(buttons[0]["text"], "Approve");
    assert_eq!(buttons[1]["text"], "Decline");
    let approve_cb = buttons[0]["callback_data"].as_str().unwrap().to_string();
    assert!(approve_cb.starts_with("a:") && approve_cb.ends_with(":0"), "{approve_cb}");

    // Before Approve is pressed, no device is paired and the cookie cannot be claimed.
    assert!(auth::load_devices(&state).is_empty());
    assert_eq!(
        telegram_app::claim_pending_pair(&state, &created.id, 111, "init-data-1", 1_020),
        ClaimOutcome::Pending
    );

    // A forged callback nonce is ignored.
    poller.handle(&callback(111, "a:0000000000000000:0")).await;
    while let Ok(msg) = inbound_rx.try_recv() {
        hub.on_inbound(msg).await;
    }
    tokio::time::sleep(std::time::Duration::from_millis(30)).await;
    assert!(auth::load_devices(&state).is_empty());

    // Pressing Approve in Telegram approves the pending pair and issues `telegram-app` with Control scope.
    poller.handle(&callback(111, &approve_cb)).await;
    while let Ok(msg) = inbound_rx.try_recv() {
        hub.on_inbound(msg).await;
    }
    while let Ok(out) = out_rx.try_recv() {
        renderer.render(out).await;
    }
    tokio::time::sleep(std::time::Duration::from_millis(30)).await;

    let devices = auth::load_devices(&state);
    assert_eq!(devices.len(), 1);
    assert_eq!(devices[0].name, "telegram-app");
    assert_eq!(devices[0].kind, DeviceKind::Browser);
    assert_eq!(devices[0].scope, Scope::Control);

    // Reusing the same callback nonce does not issue another device.
    poller.handle(&callback(111, &approve_cb)).await;
    while let Ok(msg) = inbound_rx.try_recv() {
        hub.on_inbound(msg).await;
    }
    tokio::time::sleep(std::time::Duration::from_millis(30)).await;
    assert_eq!(auth::load_devices(&state).len(), 1);

    // The launching webview claims the device token once, and a second claim finds nothing.
    match telegram_app::claim_pending_pair(&state, &created.id, 111, "init-data-1", 1_030) {
        ClaimOutcome::Approved(token) => assert!(auth::device_for_token(&state, &token).is_some()),
        other => panic!("expected Approved, got {other:?}"),
    }
    assert_eq!(
        telegram_app::claim_pending_pair(&state, &created.id, 111, "init-data-1", 1_031),
        ClaimOutcome::NotFound
    );
}

