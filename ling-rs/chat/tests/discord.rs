//! The Discord adapter against a stand-in REST API and Gateway (axum servers on loopback): never
//! Discord.
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use axum::Json;
use axum::Router;
use axum::body::Bytes;
use axum::extract::State;
use axum::extract::ws::CloseFrame;
use axum::extract::ws::Message;
use axum::extract::ws::WebSocket;
use axum::extract::ws::WebSocketUpgrade;
use axum::http::HeaderMap;
use axum::http::Method;
use axum::http::StatusCode;
use axum::http::Uri;
use axum::response::IntoResponse;
use ling_chat::discord::DiscordApi;
use ling_chat::discord::Events;
use ling_chat::discord::Renderer;
use ling_chat::discord_gateway;
use ling_chat::discord_gateway::Ended;
use ling_chat::hub::Inbound;
use ling_chat::hub::Outbound;
use ling_chat::repo_notice::AppThreadId;
use ling_chat::repo_notice::RepoName;
use ling_chat::repo_notice::RepoNotice;
use ling_chat::repo_notice::RepoNoticeKind;
use ling_chat::store;
use ling_chat::store::DiscordConfig;
use ling_chat::store::DiscordState;
use ling_chat::telegram::UNPAIRED_REPLY;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;

const TOKEN: &str = "MTIz.discord-SECRET.token";
const FRONT: &str = "https://dc.example.ts.net/";

type Answer = Arc<dyn Fn(&str, usize) -> (StatusCode, Value) + Send + Sync>;

/// The REST stand-in: every call recorded as `"METHOD /path"` and its JSON body.
#[derive(Clone)]
struct Stand {
    calls: Arc<Mutex<Vec<(String, Value)>>>,
    answer: Answer,
}

impl Stand {
    fn calls(&self) -> Vec<String> {
        self.calls.lock().unwrap().iter().map(|(call, _)| call.clone()).collect()
    }
    fn bodies(&self, call: &str) -> Vec<Value> {
        self.calls.lock().unwrap().iter().filter(|(c, _)| c == call).map(|(_, body)| body.clone()).collect()
    }
}

async fn rest(State(stand): State<Stand>, method: Method, uri: Uri, headers: HeaderMap, body: Bytes) -> (StatusCode, Json<Value>) {
    assert_eq!(headers.get("authorization").and_then(|value| value.to_str().ok()), Some(format!("Bot {TOKEN}").as_str()));
    let call = format!("{method} {}", uri.path());
    let index = {
        let mut calls = stand.calls.lock().unwrap();
        calls.push((call.clone(), serde_json::from_slice(&body).unwrap_or(Value::Null)));
        calls.iter().filter(|(c, _)| *c == call).count() - 1
    };
    let (status, value) = (stand.answer)(&call, index);
    (status, Json(value))
}

fn default_answer() -> Answer {
    Arc::new(|call, _| {
        if call.starts_with("POST /channels/") && call.ends_with("/messages") {
            (StatusCode::OK, json!({ "id": "900" }))
        } else {
            (StatusCode::OK, json!({}))
        }
    })
}

async fn stand_in(answer: Answer) -> (Stand, DiscordApi) {
    let stand = Stand { calls: Arc::default(), answer };
    let app = Router::new().fallback(rest).with_state(stand.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let root = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (stand, DiscordApi::new(&root, TOKEN))
}

/// A bridge set up with user 111 paired, whose DM is channel 222.
fn scratch(tag: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("ling-chat-dc-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let mut config = DiscordConfig::new(TOKEN.to_string(), "123".to_string());
    config.users.insert("111".to_string(), "222".to_string());
    config.save(&dir).unwrap();
    dir
}

fn dm(user: &str, channel: &str, content: &str) -> Value {
    json!({ "id": "1", "type": 0, "channel_id": channel, "author": { "id": user }, "content": content, "attachments": [] })
}

fn interaction(user: &str, channel: &str, kind: u64, data: Value) -> Value {
    json!({ "id": "31", "token": "tok-en.1", "type": kind, "context": 1, "user": { "id": user }, "channel_id": channel, "data": data })
}

fn drained(rx: &mut mpsc::UnboundedReceiver<Inbound>) -> Vec<Inbound> {
    std::iter::from_fn(|| rx.try_recv().ok()).collect()
}

#[tokio::test]
async fn a_typed_pair_code_pairs_the_dm_once_and_then_its_text_reaches_the_hub() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("pair");
    let code = store::issue_discord_pairing_code(&dir).unwrap();
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir.clone(), tx, None);
    events.handle("MESSAGE_CREATE", &dm("333", "444", &format!("/pair {code}"))).await;
    assert_eq!(DiscordConfig::load(&dir).unwrap().users.get("333").map(String::as_str), Some("444"));
    let replies = stand.bodies("POST /channels/444/messages");
    assert!(replies[0]["content"].as_str().unwrap().starts_with("Paired."), "{replies:?}");
    assert_eq!(replies[0]["allowed_mentions"], json!({ "parse": [] }));
    // The code worked once.
    assert!(!store::take_discord_pairing_code(&dir, &code));
    events.handle("MESSAGE_CREATE", &dm("333", "444", "hello")).await;
    assert_eq!(drained(&mut rx), vec![Inbound::Text { chat: "discord:444".to_string(), text: "hello".to_string() }]);
}

#[tokio::test]
async fn the_pair_command_pairs_with_an_ephemeral_answer() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("pair-command");
    let code = store::issue_discord_pairing_code(&dir).unwrap();
    let (tx, _rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir.clone(), tx, None);
    let data = json!({ "name": "pair", "options": [{ "name": "code", "type": 3, "value": code }] });
    events.handle("INTERACTION_CREATE", &interaction("333", "444", 2, data)).await;
    assert!(DiscordConfig::load(&dir).unwrap().users.contains_key("333"));
    let answer = &stand.bodies("POST /interactions/31/tok-en.1/callback")[0];
    assert_eq!(answer["type"], 4);
    assert_eq!(answer["data"]["flags"], 64);
    assert!(answer["data"]["content"].as_str().unwrap().starts_with("Paired."), "{answer}");
}

#[tokio::test]
async fn ten_wrong_codes_withdraw_the_code_that_was_issued() {
    let (_stand, api) = stand_in(default_answer()).await;
    let dir = scratch("wrong-codes");
    let code = store::issue_discord_pairing_code(&dir).unwrap();
    let (tx, _rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir.clone(), tx, None);
    for n in 0..10 {
        events.handle("MESSAGE_CREATE", &dm("333", "444", &format!("/pair WRONG{n}"))).await;
    }
    assert!(!store::take_discord_pairing_code(&dir, &code));
    assert!(!DiscordConfig::load(&dir).unwrap().users.contains_key("333"));
}

#[tokio::test]
async fn a_stranger_is_told_once_and_reaches_nothing() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("stranger");
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir, tx, None);
    events.handle("MESSAGE_CREATE", &dm("555", "666", "hi")).await;
    events.handle("MESSAGE_CREATE", &dm("555", "666", "hello?")).await;
    // A paired user writing from another DM channel is a stranger there too.
    events.handle("MESSAGE_CREATE", &dm("111", "777", "hi")).await;
    let replies = stand.bodies("POST /channels/666/messages");
    assert_eq!(replies.len(), 1);
    assert_eq!(replies[0]["content"], UNPAIRED_REPLY);
    assert_eq!(stand.bodies("POST /channels/777/messages").len(), 1);
    assert!(drained(&mut rx).is_empty());
}

#[tokio::test]
async fn server_messages_bots_and_odd_ids_are_never_read() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("ignored");
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir, tx, None);
    let mut in_server = dm("111", "222", "in a server");
    in_server["guild_id"] = json!("9");
    events.handle("MESSAGE_CREATE", &in_server).await;
    let mut from_bot = dm("111", "222", "a bot");
    from_bot["author"]["bot"] = json!(true);
    events.handle("MESSAGE_CREATE", &from_bot).await;
    let mut system = dm("111", "222", "joined");
    system["type"] = json!(7);
    events.handle("MESSAGE_CREATE", &system).await;
    events.handle("MESSAGE_CREATE", &dm("111", "../222", "path")).await;
    assert!(drained(&mut rx).is_empty());
    assert!(stand.calls().is_empty(), "{:?}", stand.calls());
}

#[tokio::test]
async fn every_server_is_left_but_the_owners_home_server() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("guilds");
    let (tx, _rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir, tx, None).with_home_server(Some("78".to_string()));
    events.handle("GUILD_CREATE", &json!({ "id": "77" })).await;
    events.handle("GUILD_CREATE", &json!({ "id": "78" })).await;
    events.handle("GUILD_CREATE", &json!({ "id": "7/8" })).await;
    assert_eq!(stand.calls(), vec!["DELETE /users/@me/guilds/77"]);
}

#[tokio::test]
async fn an_interaction_outside_the_bots_dm_is_refused_privately() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("outside");
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir, tx, None);
    let mut in_server = interaction("111", "222", 2, json!({ "name": "help" }));
    in_server["context"] = json!(0);
    in_server["guild_id"] = json!("9");
    events.handle("INTERACTION_CREATE", &in_server).await;
    let answer = &stand.bodies("POST /interactions/31/tok-en.1/callback")[0];
    assert_eq!(answer["type"], 4);
    assert_eq!(answer["data"]["flags"], 64);
    // A stranger's command in its own DM with the bot: refused the same way.
    events.handle("INTERACTION_CREATE", &interaction("555", "666", 2, json!({ "name": "help" }))).await;
    assert_eq!(stand.bodies("POST /interactions/31/tok-en.1/callback").len(), 2);
    // A token that could leave the callback path is not used at all.
    let mut odd = interaction("111", "222", 2, json!({ "name": "help" }));
    odd["token"] = json!("../../x");
    events.handle("INTERACTION_CREATE", &odd).await;
    assert_eq!(stand.calls().len(), 2, "{:?}", stand.calls());
    assert!(drained(&mut rx).is_empty());
}

#[tokio::test]
async fn a_paired_command_is_deferred_and_reaches_the_hub_as_text() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("command");
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir, tx, Some(FRONT.to_string()));
    events.handle("INTERACTION_CREATE", &interaction("111", "222", 2, json!({ "name": "model", "options": [{ "value": " fast " }] }))).await;
    assert_eq!(stand.bodies("POST /interactions/31/tok-en.1/callback"), vec![json!({ "type": 5 })]);
    assert_eq!(drained(&mut rx), vec![Inbound::Text { chat: "discord:222".to_string(), text: "/model fast".to_string() }]);
    // `/app` is answered by the bridge with the front's link button, and reaches no agent.
    events.handle("INTERACTION_CREATE", &interaction("111", "222", 2, json!({ "name": "app" }))).await;
    let app = &stand.bodies("POST /interactions/31/tok-en.1/callback")[1];
    assert_eq!(app["data"]["components"][0]["components"][0]["url"], FRONT);
    assert!(drained(&mut rx).is_empty());
}

#[tokio::test]
async fn the_stop_and_answer_buttons_disable_their_buttons_and_reach_the_hub() {
    let (stand, api) = stand_in(default_answer()).await;
    let dir = scratch("buttons");
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut events = Events::new(api, dir, tx, None);
    let mut stop = interaction("111", "222", 3, json!({ "custom_id": "stop" }));
    stop["message"] = json!({ "components": [{ "type": 1, "components": [
        { "type": 2, "style": 4, "label": "Stop", "custom_id": "stop" },
        { "type": 2, "style": 5, "label": "Open Mightling", "url": FRONT },
    ] }] });
    events.handle("INTERACTION_CREATE", &stop).await;
    events.handle("INTERACTION_CREATE", &interaction("111", "222", 3, json!({ "custom_id": "a:a1b2:1" }))).await;
    let answers = stand.bodies("POST /interactions/31/tok-en.1/callback");
    assert_eq!(answers[0]["type"], 7);
    let buttons = &answers[0]["data"]["components"][0]["components"];
    assert_eq!(buttons[0]["disabled"], true);
    assert!(buttons[1].get("disabled").is_none(), "a link button stays usable: {buttons}");
    assert_eq!(
        drained(&mut rx),
        vec![
            Inbound::Stop { chat: "discord:222".to_string() },
            Inbound::Answer { chat: "discord:222".to_string(), approval: "a1b2".to_string(), choice: 1 },
        ]
    );
}

fn notice(kind: RepoNoticeKind) -> RepoNotice {
    RepoNotice {
        seq: 1,
        chat_key: "discord:222".to_string(),
        repo: RepoName::parse("org/mightling").unwrap(),
        app_thread: Some(AppThreadId::parse("0195f0a0-1234").unwrap()),
        kind,
    }
}

#[tokio::test]
async fn repo_notices_and_the_typed_refusal_carry_an_open_button_and_no_content() {
    let (stand, api) = stand_in(default_answer()).await;
    let mut renderer = Renderer::new(api, "123").with_front_url(Some(FRONT.to_string()));
    let chat = "discord:222".to_string();
    renderer.render(Outbound::RepoNotice { chat: chat.clone(), notice: notice(RepoNoticeKind::TurnFinished { files: 3 }) }).await;
    renderer.render(Outbound::RepoNotice { chat: chat.clone(), notice: notice(RepoNoticeKind::ApprovalWaiting) }).await;
    renderer.render(Outbound::RepoTypedRefusal { chat: chat.clone(), repo: RepoName::parse("org/mightling").unwrap() }).await;
    let sent = stand.bodies("POST /channels/222/messages");
    assert_eq!(sent.len(), 3);
    assert_eq!(sent[0]["content"], "Turn finished in org/mightling: 3 files changed.");
    let open = |n: usize| sent[n]["components"][0]["components"][0].clone();
    assert_eq!(open(0)["style"], 5);
    assert_eq!(open(0)["url"], format!("{FRONT}?open=thread:0195f0a0-1234"));
    assert_eq!(open(1)["url"], format!("{FRONT}?open=thread:0195f0a0-1234&approval=1"));
    assert_eq!(sent[2]["content"], "Repo work happens in the app.");
    assert!(open(2)["url"].as_str().unwrap().starts_with(&format!("{FRONT}?open=repo:")), "{}", sent[2]);
    for body in &sent {
        assert_eq!(body["allowed_mentions"], json!({ "parse": [] }));
    }
}

#[tokio::test]
async fn without_a_front_a_repo_notice_has_no_button() {
    let (stand, api) = stand_in(default_answer()).await;
    let mut renderer = Renderer::new(api, "123");
    renderer.render(Outbound::RepoNotice { chat: "discord:222".to_string(), notice: notice(RepoNoticeKind::TurnStopped) }).await;
    let sent = stand.bodies("POST /channels/222/messages");
    assert_eq!(sent[0]["content"], "Turn stopped in org/mightling.");
    assert!(sent[0].get("components").is_none(), "{}", sent[0]);
}

#[tokio::test]
async fn an_approval_pings_no_one_and_its_buttons_close_with_the_outcome() {
    let (stand, api) = stand_in(default_answer()).await;
    let mut renderer = Renderer::new(api, "123");
    let chat = "discord:222".to_string();
    let options = vec!["Yes".to_string(), "Always".to_string(), "No".to_string()];
    renderer.render(Outbound::Approval { chat: chat.clone(), id: "a1b2".to_string(), text: "Run it? @everyone".to_string(), options }).await;
    let approval = &stand.bodies("POST /channels/222/messages")[0];
    assert_eq!(approval["allowed_mentions"], json!({ "parse": [] }));
    assert!(!approval["content"].as_str().unwrap().contains("@everyone"), "{approval}");
    let buttons = approval["components"][0]["components"].as_array().unwrap();
    let ids: Vec<&str> = buttons.iter().filter_map(|button| button["custom_id"].as_str()).collect();
    assert_eq!(ids, vec!["a:a1b2:0", "a:a1b2:1", "a:a1b2:2"]);
    assert_eq!((buttons[0]["style"].as_u64(), buttons[2]["style"].as_u64()), (Some(3), Some(4)));
    renderer.render(Outbound::ApprovalClosed { chat: chat.clone(), id: "a1b2".to_string(), outcome: "Declined".to_string() }).await;
    let closed = &stand.bodies("PATCH /channels/222/messages/900")[0];
    let done = &closed["components"][0]["components"][0];
    assert_eq!((done["label"].as_str(), done["disabled"].as_bool()), (Some("Declined"), Some(true)));
    // Closed once: a second close changes nothing.
    renderer.render(Outbound::ApprovalClosed { chat, id: "a1b2".to_string(), outcome: "Declined".to_string() }).await;
    assert_eq!(stand.bodies("PATCH /channels/222/messages/900").len(), 1);
}

#[tokio::test]
async fn a_turn_shows_one_status_line_with_stop_and_the_answer_replaces_it() {
    let (stand, api) = stand_in(default_answer()).await;
    let mut renderer = Renderer::new(api, "123").with_front_url(Some(FRONT.to_string())).with_status_interval(Duration::ZERO);
    let chat = "discord:222".to_string();
    renderer.render(Outbound::Draft { chat: chat.clone(), text: "draft one".to_string() }).await;
    renderer.render(Outbound::Draft { chat: chat.clone(), text: "draft two".to_string() }).await;
    let status = stand.bodies("POST /channels/222/messages");
    assert_eq!(status.len(), 1, "a draft's text never reaches Discord: {status:?}");
    assert_eq!(status[0]["content"], "Working on it…");
    let buttons = &status[0]["components"][0]["components"];
    assert_eq!((buttons[0]["custom_id"].as_str(), buttons[0]["style"].as_u64()), (Some("stop"), Some(4)));
    assert_eq!(buttons[1]["url"], FRONT);
    renderer.tick().await;
    let edited = stand.bodies("PATCH /channels/222/messages/900");
    assert!(edited[0]["content"].as_str().unwrap().starts_with("Working on it… "), "{edited:?}");
    renderer.render(Outbound::Message { chat: chat.clone(), markdown: "**done**".to_string() }).await;
    let calls = stand.calls();
    let deleted = calls.iter().position(|call| call == "DELETE /channels/222/messages/900").expect("the status line is deleted");
    assert_eq!(calls.last().map(String::as_str), Some("POST /channels/222/messages"));
    assert!(deleted < calls.len() - 1);
    assert_eq!(stand.bodies("POST /channels/222/messages")[1]["content"], "**done**");
}

#[tokio::test]
async fn output_for_another_messenger_or_an_odd_channel_is_dropped() {
    let (stand, api) = stand_in(default_answer()).await;
    let mut renderer = Renderer::new(api, "123");
    renderer.render(Outbound::Notice { chat: "telegram:222".to_string(), text: "x".to_string() }).await;
    renderer.render(Outbound::Notice { chat: "discord:22/2".to_string(), text: "x".to_string() }).await;
    assert!(stand.calls().is_empty(), "{:?}", stand.calls());
}

#[tokio::test]
async fn a_rate_limit_is_waited_out_and_retried() {
    let answer: Answer = Arc::new(|_, index| {
        if index == 0 { (StatusCode::TOO_MANY_REQUESTS, json!({ "retry_after": 0.01 })) } else { (StatusCode::OK, json!({ "id": "900" })) }
    });
    let (stand, api) = stand_in(answer).await;
    let sent = api.send("222", json!({ "content": "x" })).await.unwrap();
    assert_eq!(sent["id"], "900");
    assert_eq!(stand.calls().len(), 2);
}

/// The Gateway stand-in: each connection plays its script and records the frames it is sent.
#[derive(Clone)]
struct Gateway {
    url: String,
    frames: Arc<Mutex<Vec<Value>>>,
    connections: Arc<Mutex<usize>>,
    /// The first connection drops after its events, with no op 7 to ask for a resume.
    drops: bool,
}

async fn gateway(State(gateway): State<Gateway>, upgrade: WebSocketUpgrade) -> impl IntoResponse {
    upgrade.on_upgrade(move |socket| play(gateway, socket))
}

async fn play(gateway: Gateway, mut socket: WebSocket) {
    let connection = {
        let mut connections = gateway.connections.lock().unwrap();
        *connections += 1;
        *connections - 1
    };
    let send = |frame: Value| Message::Text(frame.to_string().into());
    let _ = socket.send(send(json!({ "op": 10, "d": { "heartbeat_interval": 45000 } }))).await;
    if let Some(Ok(Message::Text(text))) = socket.recv().await {
        gateway.frames.lock().unwrap().push(serde_json::from_str(&text).unwrap());
    }
    if connection == 0 {
        let ready = json!({ "session_id": "sess-1", "resume_gateway_url": gateway.url });
        let _ = socket.send(send(json!({ "op": 0, "s": 1, "t": "READY", "d": ready }))).await;
        let _ = socket.send(send(json!({ "op": 0, "s": 2, "t": "MESSAGE_CREATE", "d": dm("111", "222", "hi") }))).await;
        if gateway.drops {
            return;
        }
        let _ = socket.send(send(json!({ "op": 7, "d": null }))).await;
    } else {
        let _ = socket.send(Message::Close(Some(CloseFrame { code: 4004, reason: "Authentication failed.".into() }))).await;
    }
    while let Some(Ok(_)) = socket.recv().await {}
}

#[tokio::test]
async fn the_gateway_identifies_resumes_after_a_reconnect_and_stops_on_a_bad_token() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}", listener.local_addr().unwrap());
    let stand = Gateway { url: url.clone(), frames: Arc::default(), connections: Arc::default(), drops: false };
    let app = Router::new().route("/", axum::routing::get(gateway)).with_state(stand.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let gateway_url = url.clone();
    let (rest, api) = stand_in(Arc::new(move |call, _| match call {
        "GET /gateway/bot" => (StatusCode::OK, json!({ "url": gateway_url })),
        _ => (StatusCode::OK, json!({})),
    }))
    .await;
    let dir = scratch("gateway");
    let (tx, mut rx) = mpsc::unbounded_channel();

    let first = tokio::time::timeout(Duration::from_secs(20), discord_gateway::connect_once(&api, &dir, &tx, 100)).await.unwrap();
    assert_eq!(first, Ended::Resume, "op 7 asks for a resume");
    let identify = stand.frames.lock().unwrap()[0].clone();
    assert_eq!(identify["op"], 2);
    assert_eq!(identify["d"]["intents"], 4097);
    assert_eq!(identify["d"]["token"], TOKEN);
    let (name, data) = rx.try_recv().unwrap();
    assert_eq!((name.as_str(), data["content"].as_str()), ("MESSAGE_CREATE", Some("hi")));
    assert!(rx.try_recv().is_err(), "READY stays in the Gateway");
    let state = DiscordState::load(&dir);
    assert_eq!((state.session_id.as_deref(), state.seq, state.resume_url.as_deref()), (Some("sess-1"), Some(2), Some(url.as_str())));
    assert_eq!(state.identifies.len(), 1);

    let second = tokio::time::timeout(Duration::from_secs(20), discord_gateway::connect_once(&api, &dir, &tx, 100)).await.unwrap();
    assert!(matches!(second, Ended::Fatal(_)), "4004 is fatal: {second:?}");
    let resume = stand.frames.lock().unwrap()[1].clone();
    assert_eq!(resume, json!({ "op": 6, "d": { "token": TOKEN, "session_id": "sess-1", "seq": 2 } }));
    assert_eq!(DiscordState::load(&dir).identifies.len(), 1, "a resume is not an identify");
    // The resume went to the session's own address: the Gateway address was asked for once.
    assert_eq!(rest.calls(), vec!["GET /gateway/bot"]);
}

/// A connection that drops resumes from the last event it passed on, not from the last one saved,
/// or Discord replays the events between and the hub runs them twice.
#[tokio::test]
async fn a_dropped_connection_resumes_from_the_last_event() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}", listener.local_addr().unwrap());
    let stand = Gateway { url: url.clone(), frames: Arc::default(), connections: Arc::default(), drops: true };
    let app = Router::new().route("/", axum::routing::get(gateway)).with_state(stand.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let gateway_url = url.clone();
    let (_rest, api) = stand_in(Arc::new(move |call, _| match call {
        "GET /gateway/bot" => (StatusCode::OK, json!({ "url": gateway_url })),
        _ => (StatusCode::OK, json!({})),
    }))
    .await;
    let dir = scratch("gateway-drop");
    let (tx, _rx) = mpsc::unbounded_channel();
    let first = tokio::time::timeout(Duration::from_secs(20), discord_gateway::connect_once(&api, &dir, &tx, 100)).await.unwrap();
    assert_eq!(first, Ended::Resume);
    assert_eq!(DiscordState::load(&dir).seq, Some(2));
    let _ = tokio::time::timeout(Duration::from_secs(20), discord_gateway::connect_once(&api, &dir, &tx, 100)).await.unwrap();
    assert_eq!(stand.frames.lock().unwrap()[1]["d"]["seq"], 2);
}

#[tokio::test]
async fn the_identify_limit_stops_the_gateway_before_it_identifies() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}", listener.local_addr().unwrap());
    let stand = Gateway { url: url.clone(), frames: Arc::default(), connections: Arc::default(), drops: false };
    let app = Router::new().route("/", axum::routing::get(gateway)).with_state(stand.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let (_rest, api) = stand_in(Arc::new(move |_, _| (StatusCode::OK, json!({ "url": url })))).await;
    let dir = scratch("identify-limit");
    let mut state = DiscordState::load(&dir);
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
    state.identifies = vec![now; 3];
    state.save(&dir).unwrap();
    let (tx, _rx) = mpsc::unbounded_channel();
    let ended = tokio::time::timeout(Duration::from_secs(20), discord_gateway::connect_once(&api, &dir, &tx, 3)).await.unwrap();
    assert!(matches!(ended, Ended::Fatal(_)), "{ended:?}");
    assert!(stand.frames.lock().unwrap().is_empty(), "no identify was sent");
}

#[tokio::test]
async fn a_gateway_address_that_is_not_wss_is_refused() {
    let (_rest, api) = stand_in(Arc::new(|_, _| (StatusCode::OK, json!({ "url": "ws://gateway.example.org" })))).await;
    let dir = scratch("not-wss");
    let (tx, _rx) = mpsc::unbounded_channel();
    let ended = discord_gateway::connect_once(&api, &dir, &tx, 100).await;
    assert!(matches!(ended, Ended::Fatal(_)), "{ended:?}");
}
