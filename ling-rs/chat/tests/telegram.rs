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

/// An approval whose text is over Telegram's limit (a long question from the agent) is cut, so
/// its buttons are shown: refused whole, the request would wait unseen.
#[tokio::test]
async fn a_long_approval_is_cut_to_the_limit_and_its_buttons_are_shown() {
    let limit: Answer = Arc::new(|method, body, _| match (method, body["text"].as_str()) {
        ("sendMessage", Some(text)) if text.chars().count() > 4096 => (StatusCode::BAD_REQUEST, json!({ "ok": false, "error_code": 400, "description": "Bad Request: message is too long" })),
        _ => ok(json!({ "message_id": 7 })),
    });
    let (stand, api) = stand_in(limit).await;
    let renderer = Renderer::new(api);
    let text = format!("Which one?\n{}", "word ".repeat(1500));
    renderer.render(Outbound::Approval { chat: "telegram:111".into(), id: "q1".into(), text, options: vec!["This".into(), "Decline".into()] }).await;
    renderer.render(Outbound::ApprovalClosed { chat: "telegram:111".into(), id: "q1".into(), outcome: "Answered.".into() }).await;
    let sent = stand.bodies("sendMessage");
    assert_eq!(sent.len(), 1, "sent once, within the limit");
    assert!(sent[0]["text"].as_str().unwrap().starts_with("Which one?"));
    assert!(sent[0]["reply_markup"].is_object());
    assert_eq!(stand.bodies("editMessageText").len(), 1, "the approval was shown, so its outcome replaces it");
}

/// A rich answer in two messages whose second Telegram refuses: that part goes as plain text, and
/// the first, already shown, is not sent again.
#[tokio::test]
async fn a_refused_later_part_of_a_rich_answer_goes_as_plain_text() {
    use ling_chat::telegram::Style;
    let second: Answer = Arc::new(|method, _, index| match (method, index) {
        ("sendRichMessage", 1) => (StatusCode::BAD_REQUEST, json!({ "ok": false, "error_code": 400, "description": "Bad Request: can't parse blocks" })),
        _ => ok(json!({ "message_id": 1 })),
    });
    let (stand, api) = stand_in(second).await;
    let renderer = Renderer::styled(api, Style { rich: true });
    let markdown = format!("{}\n\n{}\n\nThe end, with **bold** and [a link](https://example.org/x).", "a".repeat(20_000), "b".repeat(12_000));
    renderer.render(Outbound::Message { chat: "telegram:111".into(), markdown }).await;
    assert_eq!(stand.bodies("sendRichMessage").len(), 2);
    let plain: String = stand.bodies("sendMessage").iter().map(|m| m["text"].as_str().unwrap_or_default().to_string()).collect();
    assert!(plain.starts_with("bbbb") && plain.ends_with("The end, with bold and a link (https://example.org/x)."), "{}", &plain[plain.len().saturating_sub(80)..]);
    assert_eq!(plain.matches('b').count(), 12_000 + 1, "the whole part");
    assert!(!plain.contains("aaaa"), "the first part is not sent again");
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
                    let ok = telegram_app::approve_pending_pair(&state_for_agent, id, 1_010).unwrap();
                    json!({ "ok": ok })
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
    let renderer = Renderer::new(api);

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

    // A forged callback nonce is ignored, and plain text ("yes") never approves a LaunchPair.
    poller.handle(&callback(111, "a:0000000000000000:0")).await;
    while let Ok(msg) = inbound_rx.try_recv() {
        hub.on_inbound(msg).await;
    }
    tokio::time::sleep(std::time::Duration::from_millis(30)).await;
    assert_eq!(
        telegram_app::claim_pending_pair(&state, &created.id, 111, "init-data-1", 1_020),
        ClaimOutcome::Pending
    );

    // Pressing Approve in Telegram (even when Telegram attaches a topic id) marks the pending pair
    // approved; the device is minted only when the launching webview claims it.
    let topic_cb = json!({
        "update_id": 2,
        "callback_query": {
            "id": "cb-topic",
            "from": { "id": 111 },
            "message": { "message_id": 7, "message_thread_id": 10, "is_topic_message": true, "chat": { "id": 111, "type": "private" } },
            "data": approve_cb,
        }
    });
    poller.handle(&topic_cb).await;
    while let Ok(msg) = inbound_rx.try_recv() {
        hub.on_inbound(msg).await;
    }
    while let Ok(out) = out_rx.try_recv() {
        renderer.render(out).await;
    }
    tokio::time::sleep(std::time::Duration::from_millis(30)).await;
    assert!(auth::load_devices(&state).is_empty(), "device is minted only on claim");

    // The launching webview claims the device token once, minting `telegram-app` with Control scope.
    match telegram_app::claim_pending_pair(&state, &created.id, 111, "init-data-1", 1_030) {
        ClaimOutcome::Approved(token) => assert!(auth::device_for_token(&state, &token).is_some()),
        other => panic!("expected Approved, got {other:?}"),
    }
    let devices = auth::load_devices(&state);
    assert_eq!(devices.len(), 1);
    assert_eq!(devices[0].name, "telegram-app");
    assert_eq!(devices[0].kind, DeviceKind::Browser);
    assert_eq!(devices[0].scope, Scope::Control);

    // Reusing the same callback nonce or claiming a second time finds nothing and mints no second device.
    poller.handle(&callback(111, &approve_cb)).await;
    while let Ok(msg) = inbound_rx.try_recv() {
        hub.on_inbound(msg).await;
    }
    tokio::time::sleep(std::time::Duration::from_millis(30)).await;
    assert_eq!(
        telegram_app::claim_pending_pair(&state, &created.id, 111, "init-data-1", 1_031),
        ClaimOutcome::NotFound
    );
    assert_eq!(auth::load_devices(&state).len(), 1);
}

/// End-to-end repository notices to Telegram (`specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md` §2, §2.1,
/// §2.2, §5.3, §12.2 G14): runs a scripted turn on a Telegram-recorded checkout with sentinels in
/// every channel (approval command, command output, filename, file body, diff, agent answer, thread
/// title, commit message, denylist line), followed by a commit, an anonymity-refused push, and a
/// clean push and merge against a local bare repo + fake Forgejo, while the real `Hub` polls
/// `ling web` and renders to the Telegram Bot API stand-in. Verifies that no sentinel ever reaches
/// Telegram, that all six counts-only notices arrive in the repository's forum topic with the
/// `Open` `web_app` button, that a typed Telegram message in the repository's topic is answered
/// with `"Repo work happens in the app."` without reaching the app-server, and that the same
/// scripted turn on a Matrix-recorded checkout produces `SENTINEL_ANSWER` at the Matrix stand-in.
#[tokio::test(flavor = "multi_thread")]
async fn repo_turns_send_no_content_to_telegram_or_discord() {
    use futures::{SinkExt, StreamExt};
    use ling_chat::agent::{AgentHandle, WebTarget};
    use ling_chat::hub::{Hub, HubConfig};
    use ling_chat::matrix::MatrixApi;
    use ling_web_server::app_server::Launch;
    use ling_web_server::auth::{self, DeviceKind};
    use ling_web_server::server::{self, Config, Server};
    use tokio_tungstenite::tungstenite::Message;
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    use tokio_tungstenite::tungstenite::http::{HeaderValue, header};

    const SENTINELS: &[&str] = &[
        "SENTINEL_CMD",
        "SENTINEL_OUT",
        "SENTINEL_NAME",
        "SENTINEL_BODY",
        "SENTINEL_DIFF",
        "SENTINEL_ANSWER",
        "SENTINEL_TITLE",
        "SENTINEL_COMMIT",
        "SENTINEL_DENY",
    ];
    const FORGE_TOKEN: &str = "forge-secret-token";

    fn git_sync(cwd: &std::path::Path, args: &[&str]) {
        let status = std::process::Command::new("git")
            .env_clear()
            .env("PATH", std::env::var_os("PATH").unwrap_or_default())
            .env("HOME", cwd)
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .args(["-c", "protocol.file.allow=always"])
            .current_dir(cwd)
            .args(args)
            .status()
            .unwrap();
        assert!(status.success(), "git {args:?} failed in {}", cwd.display());
    }

    fn make_bare_repo(root: &std::path::Path, org_repo: &str) -> std::path::PathBuf {
        let bare = root.join("forge-bares").join(format!("{org_repo}.git"));
        std::fs::create_dir_all(&bare).unwrap();
        git_sync(&bare, &["init", "--bare", "--initial-branch=main"]);

        let work = root.join("seed-work").join(org_repo.replace('/', "_"));
        std::fs::create_dir_all(&work).unwrap();
        git_sync(&work, &["init", "--initial-branch=main"]);
        git_sync(&work, &["config", "user.email", "dev@127.0.0.1"]);
        git_sync(&work, &["config", "user.name", "dev"]);
        std::fs::write(work.join("README.md"), "# Demo\nline two\n").unwrap();
        git_sync(&work, &["add", "README.md"]);
        git_sync(&work, &["commit", "-m", "initial commit"]);
        git_sync(&work, &["remote", "add", "origin", bare.to_str().unwrap()]);
        git_sync(&work, &["push", "origin", "main"]);
        bare
    }

    async fn start_fake_forge(repos_json: Value) -> String {
        let pulls: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
        let p_list = pulls.clone();
        let p_create = pulls.clone();
        let p_get = pulls.clone();
        let p_merge = pulls.clone();

        let app = Router::new()
            .route(
                "/api/v1/version",
                axum::routing::get(|| async {
                    (StatusCode::OK, [("content-type", "application/json")], "{\"version\":\"1.22.0\"}")
                }),
            )
            .route(
                "/api/v1/repos/search",
                axum::routing::get(move |axum::extract::Query(q): axum::extract::Query<HashMap<String, String>>| {
                    let repos = repos_json.clone();
                    async move {
                        let page = q.get("page").and_then(|p| p.parse::<usize>().ok()).unwrap_or(1);
                        let body = if page == 1 {
                            json!({ "ok": true, "data": repos })
                        } else {
                            json!({ "ok": true, "data": [] })
                        };
                        (StatusCode::OK, [("content-type", "application/json")], body.to_string())
                    }
                }),
            )
            .route(
                "/api/v1/repos/{owner}/{repo}/pulls",
                axum::routing::get(move || {
                    let pulls = p_list.clone();
                    async move {
                        let list: Vec<Value> = pulls
                            .lock()
                            .unwrap()
                            .iter()
                            .filter(|p| p.get("merged").and_then(Value::as_bool) != Some(true))
                            .cloned()
                            .collect();
                        (StatusCode::OK, [("content-type", "application/json")], Value::Array(list).to_string())
                    }
                })
                .post(move |axum::extract::Path((owner, repo)): axum::extract::Path<(String, String)>, body: axum::body::Bytes| {
                    let pulls = p_create.clone();
                    async move {
                        let payload: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
                        let mut list = pulls.lock().unwrap();
                        let number = (list.len() as u64) + 1;
                        let pr = json!({
                            "number": number,
                            "mergeable": true,
                            "merged": false,
                            "html_url": format!("http://127.0.0.1:3200/{owner}/{repo}/pulls/{number}"),
                            "title": payload.get("title").and_then(Value::as_str).unwrap_or(""),
                            "head": { "ref": payload.get("head").and_then(Value::as_str).unwrap_or("") },
                            "base": { "ref": payload.get("base").and_then(Value::as_str).unwrap_or("main") },
                        });
                        list.push(pr.clone());
                        (StatusCode::CREATED, [("content-type", "application/json")], pr.to_string())
                    }
                }),
            )
            .route(
                "/api/v1/repos/{owner}/{repo}/pulls/{index}",
                axum::routing::get(move |axum::extract::Path((_owner, _repo, index)): axum::extract::Path<(String, String, u64)>| {
                    let pulls = p_get.clone();
                    async move {
                        let list = pulls.lock().unwrap();
                        let pr = list.iter().find(|p| p.get("number").and_then(Value::as_u64) == Some(index)).cloned().unwrap();
                        (StatusCode::OK, [("content-type", "application/json")], pr.to_string())
                    }
                }),
            )
            .route(
                "/api/v1/repos/{owner}/{repo}/commits/{sha}/status",
                axum::routing::get(|| async {
                    (StatusCode::OK, [("content-type", "application/json")], "{\"state\":\"success\",\"total_count\":0}")
                }),
            )
            .route(
                "/api/v1/repos/{owner}/{repo}/pulls/{index}/merge",
                axum::routing::post(move |axum::extract::Path((_owner, _repo, index)): axum::extract::Path<(String, String, u64)>| {
                    let pulls = p_merge.clone();
                    async move {
                        let mut list = pulls.lock().unwrap();
                        if let Some(pr) = list.iter_mut().find(|p| p.get("number").and_then(Value::as_u64) == Some(index)) {
                            pr["merged"] = json!(true);
                        }
                        (StatusCode::OK, [("content-type", "application/json")], "{\"sha\":\"merged\"}")
                    }
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        url
    }

    type Frames = mpsc::UnboundedReceiver<(Value, mpsc::UnboundedSender<Value>)>;

    async fn fake_app_server(socket: &std::path::Path) -> Frames {
        ling_web_server::app_server::prepare_socket_dir(socket).unwrap();
        let listener = tokio::net::UnixListener::bind(socket).unwrap();
        let (frames, received) = mpsc::unbounded_channel();
        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let frames = frames.clone();
                tokio::spawn(async move {
                    let websocket = tokio_tungstenite::accept_async(stream).await.unwrap();
                    let (mut sink, mut stream) = websocket.split();
                    let (reply, mut replies) = mpsc::unbounded_channel::<Value>();
                    tokio::spawn(async move {
                        while let Some(value) = replies.recv().await {
                            if sink.send(Message::Text(value.to_string().into())).await.is_err() {
                                break;
                            }
                        }
                    });
                    while let Some(Ok(Message::Text(text))) = stream.next().await {
                        let _ = frames.send((serde_json::from_str::<Value>(text.as_str()).unwrap(), reply.clone()));
                    }
                });
            }
        });
        received
    }

    async fn next_frame(frames: &mut Frames) -> (Value, mpsc::UnboundedSender<Value>) {
        tokio::time::timeout(Duration::from_secs(10), frames.recv())
            .await
            .expect("the app-server heard nothing")
            .unwrap()
    }

    async fn next_frame_at(frames: &mut Frames, line: u32) -> (Value, mpsc::UnboundedSender<Value>) {
        tokio::time::timeout(Duration::from_secs(10), frames.recv())
            .await
            .unwrap_or_else(|_| panic!("the app-server heard nothing at line {line}"))
            .unwrap()
    }

    #[allow(clippy::too_many_arguments)]
    fn emit_scripted_turn_approval(
        replies: &[&mpsc::UnboundedSender<Value>],
        thread_id: &str,
        turn_id: &str,
        approval_id: u64,
        worktree_dir: &std::path::Path,
        include_deny_line: bool,
    ) {
        let body = if include_deny_line {
            "SENTINEL_BODY\nSENTINEL_DENY\n"
        } else {
            "SENTINEL_BODY\n"
        };
        std::fs::write(worktree_dir.join("SENTINEL_NAME.txt"), body).unwrap();
        let req = json!({
            "id": approval_id,
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "itemId": "cmd-1",
                "startedAtMs": 0,
                "command": "SENTINEL_CMD"
            }
        });
        for reply in replies {
            let _ = reply.send(req.clone());
        }
    }

    fn emit_scripted_turn_rest(
        replies: &[&mpsc::UnboundedSender<Value>],
        thread_id: &str,
        turn_id: &str,
    ) {
        let events = [
            json!({
                "method": "item/commandExecution/outputDelta",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": "cmd-1",
                    "delta": "SENTINEL_OUT"
                }
            }),
            json!({
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "id": "fc-1",
                        "type": "fileChange",
                        "changes": [{ "path": "SENTINEL_NAME.txt", "diff": "SENTINEL_DIFF" }]
                    }
                }
            }),
            json!({
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": "msg-1",
                    "delta": "SENTINEL_ANSWER"
                }
            }),
            json!({
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "id": "msg-1",
                        "type": "agentMessage",
                        "text": "SENTINEL_ANSWER"
                    }
                }
            }),
            json!({
                "method": "thread/name/updated",
                "params": {
                    "threadId": thread_id,
                    "threadName": "SENTINEL_TITLE"
                }
            }),
            json!({
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": { "id": turn_id, "status": "completed" }
                }
            }),
        ];
        for ev in events {
            for reply in replies {
                let _ = reply.send(ev.clone());
            }
        }
    }

    let root_dir = scratch("g14-e2e");
    let bare = make_bare_repo(&root_dir, "org/app");
    let forge_url = start_fake_forge(json!([
        {
            "full_name": "org/app",
            "default_branch": "main",
            "private": false,
            "archived": false,
            "mirror": false,
            "updated_at": "2026-10-20T12:00:00Z",
            "permissions": { "push": true },
            "clone_url": format!("file://{}", bare.display())
        }
    ]))
    .await;

    let codex_home = root_dir.join("home");
    let state_dir = auth::state_dir(&codex_home);
    std::fs::create_dir_all(&state_dir).unwrap();
    auth::write_private(&state_dir.join("forge-phone-token"), &format!("{FORGE_TOKEN}\n")).unwrap();

    let user_home = root_dir.join("user");
    let user_config = user_home.join(".config/dreamference/config.toml");
    std::fs::create_dir_all(user_config.parent().unwrap()).unwrap();
    std::fs::write(&user_config, "").unwrap();
    std::fs::write(
        user_home.join(".gitconfig"),
        "[user]\n\tname = owner\n\temail = owner@127.0.0.1\n",
    )
    .unwrap();
    std::fs::write(
        user_home.join(".config/dreamference/anonymity-denylist.txt"),
        "SENTINEL_DENY\n",
    )
    .unwrap();
    let codex_denylist = codex_home.join(".config/dreamference/anonymity-denylist.txt");
    std::fs::create_dir_all(codex_denylist.parent().unwrap()).unwrap();
    std::fs::write(&codex_denylist, "SENTINEL_DENY\n").unwrap();

    let fake_ling = root_dir.join("ling");
    std::fs::write(
        &fake_ling,
        "#!/bin/sh\nif [ \"$1\" = prompt ] && [ \"$2\" = show ] && [ \"$4\" = --composed ]; then printf 'COMPOSED:%s' \"$3\"; exit 0; fi\nexit 0\n",
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&fake_ling, std::fs::Permissions::from_mode(0o755)).unwrap();
    }

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let socket_path = root_dir.join("run").join("s.sock");
    let mut app_server = fake_app_server(&socket_path).await;
    let config = Config {
        codex_home: codex_home.clone(),
        socket: socket_path,
        launch: Launch { ling: fake_ling, env: Vec::new(), log: state_dir.join("app-server.log") },
        images_dir: root_dir.join("images"),
        speech_addr: "127.0.0.1:9".to_string(),
        user_config: Some(user_config),
        forge_url: Some(forge_url),
        state_dir: state_dir.clone(),
        port,
        lan_names: Vec::new(),
        public_names: Vec::new(),
    };
    let server = Server::new(config).unwrap();
    /// A host with plenty of disk and no night running, whatever the real machine says.
    struct RoomyHost;
    impl ling_web_server::chat_repos::HostProbe for RoomyHost {
        fn free_bytes(&self, _path: &std::path::Path) -> Result<u64, String> {
            Ok(1 << 50)
        }
        fn swe_night_running(&self) -> bool {
            false
        }
    }
    server.chat_repos.set_probe(Arc::new(RoomyHost));
    server.chat_repos.set_allow_file_protocol(true);
    std::fs::write(state_dir.join("server.json"), json!({ "port": port }).to_string()).unwrap();
    let (_stop, stopped) = tokio::sync::oneshot::channel::<()>();
    tokio::spawn(server::serve(server.clone(), vec![listener], async move {
        let _ = stopped.await;
    }));

    // Telegram stand-in with forum topics enabled.
    let tg_answer: Answer = Arc::new(|method, body, _| match method {
        "createForumTopic" => ok(json!({ "message_thread_id": 42, "name": body["name"] })),
        "sendMessage" => ok(json!({ "message_id": 900 })),
        _ => ok(json!(true)),
    });
    let (tg_stand, tg_api) = stand_in(tg_answer).await;

    // Matrix stand-in for the positive control.
    let mx_calls: Arc<Mutex<Vec<(String, Value)>>> = Arc::new(Mutex::new(Vec::new()));
    let mx_calls_handler = mx_calls.clone();
    let mx_app = Router::new().fallback(move |uri: axum::http::Uri, body: axum::body::Bytes| {
        let calls = mx_calls_handler.clone();
        async move {
            let path = uri.path().to_string();
            let parsed = serde_json::from_slice::<Value>(&body).unwrap_or(Value::Null);
            calls.lock().unwrap().push((path, parsed));
            Json(json!({ "event_id": "$mx1" }))
        }
    });
    let mx_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mx_root = format!("http://{}", mx_listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(mx_listener, mx_app).await.unwrap() });

    // Start the real `ling-chat` link and `Hub`.
    let (agent, commands) = AgentHandle::channel();
    let (events_tx, events) = mpsc::unbounded_channel();
    let (link_tx, mut from_link) = mpsc::unbounded_channel::<Value>();
    let (connected_tx, mut connected) = mpsc::unbounded_channel::<()>();
    tokio::spawn(async move {
        while let Some(event) = from_link.recv().await {
            let is_connected = event["method"] == ling_chat::agent::CONNECTED;
            if events_tx.send(event).is_err() {
                break;
            }
            if is_connected {
                let _ = connected_tx.send(());
            }
        }
    });
    tokio::spawn(ling_chat::agent::run_link(
        WebTarget { state: state_dir.clone(), ling: None },
        commands,
        link_tx,
    ));

    let hub_dir = root_dir.clone();
    let mut hub_cfg = HubConfig::new(hub_dir.clone());
    hub_cfg.repo_notice_interval = Duration::from_millis(50);
    let mut hub = Hub::new(hub_cfg, agent, Box::new(|| false));

    let (tg_out_tx, mut tg_out_rx) = mpsc::unbounded_channel();
    hub.add_adapter("telegram", tg_out_tx);
    let tg_renderer = Renderer::new(tg_api.clone())
        .with_dir(hub_dir.clone())
        .with_topics(true)
        .with_mini_app_url(Some(MINI_APP.to_string()));
    tokio::spawn(async move {
        while let Some(out) = tg_out_rx.recv().await {
            tg_renderer.render(out).await;
        }
    });

    let (mx_out_tx, mut mx_out_rx) = mpsc::unbounded_channel();
    hub.add_adapter("matrix", mx_out_tx);
    let (mx_dummy_tx, _mx_dummy_rx) = mpsc::unbounded_channel();
    let (_, mx_renderer) = ling_chat::matrix::pair_for_tests(
        MatrixApi::new(&mx_root, "bot-token"),
        root_dir.join("mx-state"),
        "@mightling:node.tail.ts.net",
        mx_dummy_tx,
    );
    tokio::spawn(async move {
        while let Some(out) = mx_out_rx.recv().await {
            mx_renderer.render(out).await;
        }
    });

    let (inbound_tx, inbound_rx) = mpsc::unbounded_channel();
    let mut poller = Poller::new(tg_api, hub_dir.clone(), inbound_tx.clone());
    poller.topics = true;
    poller.mini_app_url = Some(MINI_APP.to_string());
    tokio::spawn(hub.run(inbound_rx, events));

    // Wait for `ling-chat` link to connect and initialize.
    let (frame, bridge_reply) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "initialize");
    assert_eq!(frame["params"]["clientInfo"]["name"], "ling-chat");
    bridge_reply.send(json!({ "id": frame["id"], "result": {} })).unwrap();
    let (frame, _) = next_frame(&mut app_server).await;
    assert_eq!(frame["method"], "initialized");
    tokio::time::timeout(Duration::from_secs(10), connected.recv()).await.unwrap().unwrap();
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Issue a browser device cookie for the page tab.
    let (_dev_id, dev_token) =
        auth::add_device_of(&state_dir, "page", DeviceKind::Browser).unwrap();
    let page_cookie = format!("{}={dev_token}", auth::DEVICE_COOKIE);
    let origin = format!("http://127.0.0.1:{port}");
    let web_root = format!("http://127.0.0.1:{port}");
    let http = reqwest::Client::new();

    // 1. Open a checkout recorded with `bridge: telegram`.
    let open_tg: Value = http
        .post(format!("{web_root}/api/repos/open"))
        .header("Origin", &origin)
        .header("Cookie", &page_cookie)
        .json(&json!({ "repo": "org/app", "bridge": "telegram", "chat": "111" }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let tg_thread = open_tg["thread"].as_str().unwrap_or_else(|| panic!("open: {open_tg}")).to_string();
    let tg_wt = std::path::PathBuf::from(open_tg["cwd"].as_str().unwrap());

    // 2. Connect a page tab on `/ws` and start a thread in the Telegram checkout.
    let tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
    let mut ws_req = format!("ws://127.0.0.1:{port}/ws").into_client_request().unwrap();
    ws_req.headers_mut().insert(header::ORIGIN, HeaderValue::from_str(&origin).unwrap());
    ws_req.headers_mut().insert(header::COOKIE, HeaderValue::from_str(&page_cookie).unwrap());
    let (mut page_ws, _) = tokio_tungstenite::client_async(ws_req, tcp).await.unwrap();

    page_ws
        .send(Message::Text(json!({ "call": 1, "message": { "type": "work/start" } }).to_string().into()))
        .await
        .unwrap();
    // Wait for `{"answer": 1}`.
    while let Some(Ok(Message::Text(txt))) = page_ws.next().await {
        let v: Value = serde_json::from_str(&txt).unwrap();
        if v.get("answer").and_then(Value::as_u64) == Some(1) {
            break;
        }
    }

    page_ws
        .send(Message::Text(
            json!({
                "call": 2,
                "message": {
                    "type": "work/send",
                    "message": { "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {} }
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    let (frame, page_reply) = next_frame_at(&mut app_server, 1300).await;
    assert_eq!(frame["method"], "initialize");
    page_reply.send(json!({ "id": frame["id"], "result": {} })).unwrap();

    page_ws
        .send(Message::Text(
            json!({
                "call": 3,
                "message": {
                    "type": "work/send",
                    "message": { "jsonrpc": "2.0", "method": "initialized", "params": {} }
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    let (frame, _) = next_frame_at(&mut app_server, 1318).await;
    assert_eq!(frame["method"], "initialized");

    let tg_app_thread = "0195f0a0-1111";
    page_ws
        .send(Message::Text(
            json!({
                "call": 4,
                "message": {
                    "type": "work/send",
                    "message": {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "thread/start",
                        "params": { "prompt": "chat-work", "cwd": tg_wt.to_str().unwrap() }
                    }
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    let (frame, _) = next_frame_at(&mut app_server, 1341).await;
    assert_eq!(frame["method"], "thread/start");
    page_reply
        .send(json!({ "id": frame["id"], "result": { "thread": { "id": tg_app_thread } } }))
        .unwrap();
    while let Some(Ok(Message::Text(txt))) = page_ws.next().await {
        let v: Value = serde_json::from_str(&txt).unwrap();
        if v.pointer("/event/payload/id").and_then(Value::as_u64) == Some(2) {
            break;
        }
    }

    page_ws
        .send(Message::Text(
            json!({
                "call": 5,
                "message": {
                    "type": "work/send",
                    "message": {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "turn/start",
                        "params": {
                            "threadId": tg_app_thread,
                            "input": [{ "type": "text", "text": "fix the bug" }]
                        }
                    }
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    let (frame, _) = next_frame_at(&mut app_server, 1375).await;
    assert_eq!(frame["method"], "turn/start");
    page_reply
        .send(json!({ "id": frame["id"], "result": { "turn": { "id": "turn-tg-1" } } }))
        .unwrap();

    // 3. Run the scripted turn on the Telegram checkout (sending to both the page tab and the bridge).
    emit_scripted_turn_approval(
        &[&page_reply, &bridge_reply],
        tg_app_thread,
        "turn-tg-1",
        901,
        &tg_wt,
        true,
    );
    while let Some(Ok(Message::Text(txt))) = page_ws.next().await {
        let v: Value = serde_json::from_str(&txt).unwrap();
        if v.pointer("/event/payload/id").and_then(Value::as_u64) == Some(901) {
            break;
        }
    }
    page_ws
        .send(Message::Text(
            json!({
                "call": 6,
                "message": {
                    "type": "work/send",
                    "message": { "jsonrpc": "2.0", "id": 901, "result": { "decision": "accept" } }
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    let (frame, _) = next_frame_at(&mut app_server, 1410).await;
    assert_eq!(frame["id"], 901);

    emit_scripted_turn_rest(&[&page_reply, &bridge_reply], tg_app_thread, "turn-tg-1");
    while let Some(Ok(Message::Text(txt))) = page_ws.next().await {
        let v: Value = serde_json::from_str(&txt).unwrap();
        if v.pointer("/event/payload/method").and_then(Value::as_str) == Some("turn/completed") {
            break;
        }
    }

    let get_nonce = |thread: String, intent: &'static str| {
        let http = http.clone();
        let web_root = web_root.clone();
        let page_cookie = page_cookie.clone();
        async move {
            let state: Value = http
                .get(format!("{web_root}/api/repos/state?thread={thread}&intent={intent}"))
                .header("Cookie", &page_cookie)
                .send()
                .await
                .unwrap()
                .json()
                .await
                .unwrap();
            state["confirm"].as_str().unwrap().to_string()
        }
    };

    // 4. Commit with `SENTINEL_COMMIT`, then attempt a push refused by the anonymity check (`SENTINEL_DENY`).
    let c_nonce = get_nonce(tg_thread.clone(), "commit").await;
    let commit_resp = http
        .post(format!("{web_root}/api/repos/commit"))
        .header("Origin", &origin)
        .header("Cookie", &page_cookie)
        .json(&json!({ "thread": tg_thread, "message": "SENTINEL_COMMIT", "confirm": c_nonce }))
        .send()
        .await
        .unwrap();
    assert_eq!(commit_resp.status(), StatusCode::OK);

    let p_nonce_refused = get_nonce(tg_thread.clone(), "push").await;
    let refused_push = http
        .post(format!("{web_root}/api/repos/push"))
        .header("Origin", &origin)
        .header("Cookie", &page_cookie)
        .json(&json!({ "thread": tg_thread, "confirm": p_nonce_refused }))
        .send()
        .await
        .unwrap();
    assert_eq!(refused_push.status(), StatusCode::UNPROCESSABLE_ENTITY);

    // Replace the commit so `SENTINEL_NAME.txt` holds `SENTINEL_BODY` without `SENTINEL_DENY` while
    // keeping `SENTINEL_COMMIT` as the commit message and `SENTINEL_DENY` in the node's denylist,
    // then push and merge against the local bare repo and fake forge.
    git_sync(&tg_wt, &["reset", "--soft", "origin/main"]);
    std::fs::write(tg_wt.join("SENTINEL_NAME.txt"), "SENTINEL_BODY\n").unwrap();
    let c_nonce_clean = get_nonce(tg_thread.clone(), "commit").await;
    let commit_clean_resp = http
        .post(format!("{web_root}/api/repos/commit"))
        .header("Origin", &origin)
        .header("Cookie", &page_cookie)
        .json(&json!({ "thread": tg_thread, "message": "SENTINEL_COMMIT", "confirm": c_nonce_clean }))
        .send()
        .await
        .unwrap();
    assert_eq!(commit_clean_resp.status(), StatusCode::OK);

    let p_nonce_ok = get_nonce(tg_thread.clone(), "push").await;
    let ok_push = http
        .post(format!("{web_root}/api/repos/push"))
        .header("Origin", &origin)
        .header("Cookie", &page_cookie)
        .json(&json!({ "thread": tg_thread, "confirm": p_nonce_ok }))
        .send()
        .await
        .unwrap();
    assert_eq!(ok_push.status(), StatusCode::OK);

    let m_nonce = get_nonce(tg_thread.clone(), "merge").await;
    let ok_merge = http
        .post(format!("{web_root}/api/repos/merge"))
        .header("Origin", &origin)
        .header("Cookie", &page_cookie)
        .json(&json!({ "thread": tg_thread, "confirm": m_nonce }))
        .send()
        .await
        .unwrap();
    assert_eq!(ok_merge.status(), StatusCode::OK);

    // 5. Wait for the Hub to drain and render all 6 notices to the Telegram stand-in.
    let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    loop {
        if tg_stand.bodies("sendMessage").len() >= 6 {
            break;
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "timed out waiting for 6 Telegram repo notices; got {:?}",
            tg_stand.bodies("sendMessage")
        );
        tokio::time::sleep(Duration::from_millis(30)).await;
    }

    let tg_messages = tg_stand.bodies("sendMessage");
    assert_eq!(tg_messages.len(), 6);
    let tg_texts: Vec<&str> = tg_messages.iter().map(|b| b["text"].as_str().unwrap()).collect();
    let expected_branch = format!("phone/{tg_thread}");
    let expected_switched = format!("Working in org/app on {expected_branch}.");
    let expected_merged = format!("Merged {expected_branch} into main in org/app.");
    assert_eq!(
        tg_texts,
        vec![
            expected_switched.as_str(),
            "Approval waiting in org/app.",
            "Turn finished in org/app: 1 files changed.",
            "Push refused in org/app; open the app.",
            "Pushed 1 commits of org/app.",
            expected_merged.as_str(),
        ]
    );

    // Topic creation and Open web_app buttons.
    let topics_created = tg_stand.bodies("createForumTopic");
    assert_eq!(topics_created.len(), 1);
    assert_eq!(topics_created[0]["name"], "org/app");
    for (idx, msg) in tg_messages.iter().enumerate() {
        assert_eq!(msg["chat_id"], 111);
        assert_eq!(msg["message_thread_id"], 42);
        let btn = &msg["reply_markup"]["inline_keyboard"][0][0];
        assert_eq!(btn["text"], "Open");
        let url = btn["web_app"]["url"].as_str().unwrap();
        if idx == 0 {
            assert_eq!(url, format!("{MINI_APP}?open=thread:{tg_thread}"));
        } else if idx == 1 {
            assert_eq!(url, format!("{MINI_APP}?open=thread:{tg_app_thread}&approval=1"));
        } else {
            assert_eq!(url, format!("{MINI_APP}?open=thread:{tg_app_thread}"));
        }
    }

    // 6. A typed Telegram message in a repository's topic is answered with the fixed text,
    // and no `turn/start` reaches the fake app-server.
    let typed_in_repo_topic = json!({
        "update_id": 77,
        "message": {
            "message_id": 78,
            "message_thread_id": 42,
            "is_topic_message": true,
            "from": { "id": 111 },
            "chat": { "id": 111, "type": "private" },
            "text": "please run cargo test"
        }
    });
    poller.handle(&typed_in_repo_topic).await;
    let refusal_deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    loop {
        if tg_stand.bodies("sendMessage").len() >= 7 {
            break;
        }
        assert!(tokio::time::Instant::now() < refusal_deadline, "timed out waiting for typed-text refusal: {:?} dir {}", tg_stand.bodies("sendMessage").iter().map(|b| b["text"].clone()).collect::<Vec<_>>(), root_dir.display());
        tokio::time::sleep(Duration::from_millis(30)).await;
    }
    let refusal_msg = tg_stand.bodies("sendMessage").pop().unwrap();
    assert_eq!(refusal_msg["chat_id"], 111);
    assert_eq!(refusal_msg["message_thread_id"], 42);
    assert_eq!(refusal_msg["text"], "Repo work happens in the app.");
    assert_eq!(
        refusal_msg["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],
        format!("{MINI_APP}?open=repo:org/app")
    );
    assert!(app_server.try_recv().is_err(), "no turn/start may reach the fake app-server");

    // 7. Positive control: the same scripted turn on a Matrix-recorded checkout through the Matrix
    // stand-in shows `SENTINEL_ANSWER`, so the sentinels were really produced.
    let open_mx: Value = http
        .post(format!("{web_root}/api/repos/open"))
        .header("Origin", &origin)
        .header("Cookie", &page_cookie)
        .json(&json!({ "repo": "org/app", "bridge": "matrix", "chat": "!dm:node.tail.ts.net" }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let mx_wt = std::path::PathBuf::from(open_mx["cwd"].as_str().unwrap());
    let mx_app_thread = "0195f0a0-2222";

    // Attach `mx_app_thread` to the Matrix checkout record via the page tab.
    page_ws
        .send(Message::Text(
            json!({
                "call": 7,
                "message": {
                    "type": "work/send",
                    "message": {
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "thread/start",
                        "params": { "prompt": "chat-work", "cwd": mx_wt.to_str().unwrap() }
                    }
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    let (frame, _) = next_frame_at(&mut app_server, 1619).await;
    assert_eq!(frame["method"], "thread/start");
    page_reply
        .send(json!({ "id": frame["id"], "result": { "thread": { "id": mx_app_thread } } }))
        .unwrap();
    while let Some(Ok(Message::Text(txt))) = page_ws.next().await {
        let v: Value = serde_json::from_str(&txt).unwrap();
        if v.pointer("/event/payload/id").and_then(Value::as_u64) == Some(10) {
            break;
        }
    }

    // Start a turn from Matrix through the Hub.
    inbound_tx
        .send(Inbound::Text {
            chat: "matrix:!dm:node.tail.ts.net".into(),
            text: "run matrix turn".into(),
        })
        .unwrap();
    let (frame, _) = next_frame_at(&mut app_server, 1638).await;
    assert_eq!(frame["method"], "thread/start");
    bridge_reply
        .send(json!({ "id": frame["id"], "result": { "thread": { "id": mx_app_thread } } }))
        .unwrap();
    let (frame, _) = next_frame_at(&mut app_server, 1643).await;
    assert_eq!(frame["method"], "thread/name/set");
    bridge_reply.send(json!({ "id": frame["id"], "result": {} })).unwrap();
    let (frame, _) = next_frame_at(&mut app_server, 1646).await;
    assert_eq!(frame["method"], "turn/start");
    bridge_reply
        .send(json!({ "id": frame["id"], "result": { "turn": { "id": "turn-mx-1" } } }))
        .unwrap();

    emit_scripted_turn_approval(&[&bridge_reply], mx_app_thread, "turn-mx-1", 902, &mx_wt, false);
    emit_scripted_turn_rest(&[&bridge_reply], mx_app_thread, "turn-mx-1");

    let mx_deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    loop {
        let dump = serde_json::to_string(&*mx_calls.lock().unwrap()).unwrap();
        if dump.contains("SENTINEL_ANSWER") && dump.contains("SENTINEL_CMD") {
            break;
        }
        assert!(
            tokio::time::Instant::now() < mx_deadline,
            "timed out waiting for SENTINEL_ANSWER on Matrix stand-in; got {dump}"
        );
        tokio::time::sleep(Duration::from_millis(30)).await;
    }

    // 8. Final sentinel check across every request body the Telegram stand-in received
    // (text, reply_markup, callback_data, topic names, drafts).
    tokio::time::sleep(Duration::from_millis(120)).await;
    let tg_all_calls = serde_json::to_string(&*tg_stand.calls.lock().unwrap()).unwrap();
    for sentinel in SENTINELS {
        assert!(
            !tg_all_calls.contains(sentinel),
            "sentinel {sentinel} leaked to Telegram stand-in: {tg_all_calls}"
        );
    }
    assert_eq!(
        tg_stand.bodies("sendMessage").len(),
        7,
        "Matrix checkout events must not drain to Telegram"
    );
}


