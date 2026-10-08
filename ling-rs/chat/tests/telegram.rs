//! The Telegram adapter against a stand-in Bot API (an axum server on loopback): never Telegram.

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

fn ok(result: Value) -> (StatusCode, Value) {
    (StatusCode::OK, json!({ "ok": true, "result": result }))
}

async fn stand_in(answer: Answer) -> (Stand, TelegramApi) {
    let stand = Stand { calls: Arc::default(), answer };
    let app = Router::new().route("/{bot}/{method}", post(handle)).with_state(stand.clone());
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
        json!({ "update_id": 3, "callback_query": { "id": "cb1", "from": { "id": user }, "data": "a:4:1", "message": { "message_id": 9, "chat": { "id": 111, "type": "private" } } } })
    };
    poller.handle(&press(999)).await;
    assert!(inbound.try_recv().is_err());
    poller.handle(&press(111)).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Answer { chat: "telegram:111".into(), approval: 4, choice: 1 });
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
async fn approvals_are_buttons_and_closing_one_edits_it() {
    let (stand, api) = stand_in(default_answer()).await;
    let renderer = Renderer::new(api);
    renderer
        .render(Outbound::Approval { chat: "telegram:111".into(), id: 7, text: "Run `ls`?".into(), options: vec!["Approve once".into(), "Decline".into()] })
        .await;
    let sent = &stand.bodies("sendMessage")[0];
    assert_eq!(sent["reply_markup"]["inline_keyboard"][0][0], json!({ "text": "Approve once", "callback_data": "a:7:0" }));
    assert_eq!(sent["reply_markup"]["inline_keyboard"][1][0]["callback_data"], "a:7:1");
    renderer.render(Outbound::ApprovalClosed { chat: "telegram:111".into(), id: 7, outcome: "Approved.".into() }).await;
    let edit = &stand.bodies("editMessageText")[0];
    assert_eq!(edit["message_id"], 900);
    assert!(edit["text"].as_str().unwrap().ends_with("<i>Approved.</i>"));
    assert!(edit.get("reply_markup").is_none());
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
