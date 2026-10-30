//! The Matrix adapter against a stand-in homeserver (an axum server on loopback): never tuwunel.

use std::collections::VecDeque;
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use axum::Json;
use axum::Router;
use axum::body::Bytes;
use axum::extract::State;
use axum::http::Method;
use axum::http::Uri;
use ling_chat::hub::Inbound;
use ling_chat::hub::Outbound;
use ling_chat::matrix::MatrixApi;
use ling_chat::store::MatrixConfig;
use ling_chat::store::MatrixState;
use ling_chat::store::write_private;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;

const BOT: &str = "@mightling:node.tail.ts.net";
const OWNER: &str = "@owner:node.tail.ts.net";

#[derive(Clone, Default)]
struct Stand {
    calls: Arc<Mutex<Vec<(String, String, Value)>>>,
    syncs: Arc<Mutex<VecDeque<Value>>>,
    events: Arc<Mutex<u32>>,
}

impl Stand {
    fn calls(&self, method: &str, path_part: &str) -> Vec<(String, Value)> {
        self.calls
            .lock()
            .unwrap()
            .iter()
            .filter(|(m, p, _)| m == method && p.contains(path_part))
            .map(|(_, p, b)| (p.clone(), b.clone()))
            .collect()
    }
}

fn decode(path: &str) -> String {
    let bytes = path.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            out.push(u8::from_str_radix(&path[i + 1..i + 3], 16).unwrap());
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).unwrap()
}

async fn handle(State(stand): State<Stand>, method: Method, uri: Uri, body: Bytes) -> Json<Value> {
    let path = decode(uri.path());
    let body: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    stand.calls.lock().unwrap().push((method.to_string(), path.clone(), body));
    let answer = if path.ends_with("/sync") {
        let next = stand.syncs.lock().unwrap().pop_front();
        match next {
            Some(body) => body,
            None => {
                tokio::time::sleep(Duration::from_millis(50)).await;
                json!({ "next_batch": "idle" })
            }
        }
    } else if path.ends_with("/createRoom") {
        json!({ "room_id": "!dm:node.tail.ts.net" })
    } else if path.contains("/send/") {
        let mut n = stand.events.lock().unwrap();
        *n += 1;
        json!({ "event_id": format!("$e{n}") })
    } else if path.starts_with("/_matrix/media/v3/upload") {
        json!({ "content_uri": "mxc://node.tail.ts.net/up1" })
    } else if path.ends_with("/whoami") {
        json!({ "user_id": BOT })
    } else {
        json!({})
    };
    Json(answer)
}

/// Authenticated media: the bytes name the file, for the bot's token only.
async fn media(axum::extract::Path((server, media)): axum::extract::Path<(String, String)>, headers: axum::http::HeaderMap) -> (axum::http::StatusCode, Vec<u8>) {
    if headers.get("authorization").and_then(|v| v.to_str().ok()) != Some("Bearer bot-token") {
        return (axum::http::StatusCode::UNAUTHORIZED, Vec::new());
    }
    (axum::http::StatusCode::OK, format!("MEDIA:{server}/{media}").into_bytes())
}

async fn stand_in() -> (Stand, String) {
    let stand = Stand::default();
    let app = Router::new()
        .route("/_matrix/client/v1/media/download/{server}/{media}", axum::routing::get(media))
        .fallback(handle)
        .with_state(stand.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let root = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (stand, root)
}

fn setup(tag: &str, root: &str) -> (std::path::PathBuf, MatrixConfig) {
    let dir = std::env::temp_dir().join(format!("ling-chat-mx-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let config = json!({ "homeserver": root, "server_name": "node.tail.ts.net", "user_id": BOT, "access_token": "bot-token", "allowed": [OWNER] });
    write_private(&MatrixConfig::path(&dir), &config.to_string()).unwrap();
    (dir.clone(), MatrixConfig::load(&dir).unwrap())
}

fn invite(room: &str, sender: &str, encrypted: bool) -> Value {
    let mut events = vec![json!({ "type": "m.room.member", "state_key": BOT, "sender": sender, "content": { "membership": "invite" } })];
    if encrypted {
        events.push(json!({ "type": "m.room.encryption", "state_key": "", "sender": sender, "content": { "algorithm": "m.megolm.v1.aes-sha2" } }));
    }
    json!({ "next_batch": "s2", "rooms": { "invite": { room: { "invite_state": { "events": events } } } } })
}

fn message(room: &str, sender: &str, body: &str) -> Value {
    json!({ "next_batch": "s3", "rooms": { "join": { room: {
        "summary": { "m.joined_member_count": 2 },
        "timeline": { "events": [{ "type": "m.room.message", "sender": sender, "event_id": "$m", "content": { "msgtype": "m.text", "body": body } }] },
    } } } })
}

#[tokio::test]
async fn invites_are_accepted_from_allowed_users_only_and_encrypted_rooms_refused() {
    let (stand, root) = stand_in().await;
    let (dir, config) = setup("invites", &root);
    let (hub, _inbound) = mpsc::unbounded_channel();
    let (syncer, _) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir, BOT, hub);
    syncer.handle(&config, &invite("!ok:x", OWNER, false), false).await;
    syncer.handle(&config, &invite("!stranger:x", "@eve:elsewhere", false), false).await;
    syncer.handle(&config, &invite("!secret:x", OWNER, true), false).await;
    let joins: Vec<String> = stand.calls("POST", "/join/").into_iter().map(|(p, _)| p).collect();
    assert_eq!(joins, vec!["/_matrix/client/v3/join/!ok:x", "/_matrix/client/v3/join/!secret:x"]);
    let leaves: Vec<String> = stand.calls("POST", "/leave").into_iter().map(|(p, _)| p).collect();
    assert_eq!(leaves, vec!["/_matrix/client/v3/rooms/!stranger:x/leave", "/_matrix/client/v3/rooms/!secret:x/leave"]);
    let notices = stand.calls("PUT", "/send/");
    assert_eq!(notices.len(), 1);
    assert!(notices[0].0.contains("!secret:x"));
    assert!(notices[0].1["body"].as_str().unwrap().contains("encrypted"));
}

/// Who invited the bot is read from the bot's own invite only: an allowed user's name on another
/// member event, on the bot's earlier leave, or on a state event of another type, never lets a
/// stranger's invite in.
#[tokio::test]
async fn only_the_bots_own_invite_names_the_inviter() {
    let (stand, root) = stand_in().await;
    let (dir, config) = setup("inviter", &root);
    let (hub, _inbound) = mpsc::unbounded_channel();
    let (syncer, _) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir, BOT, hub);
    let from_eve = json!({ "type": "m.room.member", "state_key": BOT, "sender": "@eve:elsewhere", "content": { "membership": "invite" } });
    let rooms = [
        ("!other-member:x", json!({ "type": "m.room.member", "state_key": "@friend:x", "sender": OWNER, "content": { "membership": "invite" } })),
        ("!old-leave:x", json!({ "type": "m.room.member", "state_key": BOT, "sender": OWNER, "content": { "membership": "leave" } })),
        ("!other-type:x", json!({ "type": "org.example.note", "state_key": BOT, "sender": OWNER, "content": { "membership": "invite" } })),
    ];
    for (room, decoy) in rooms {
        let body = json!({ "next_batch": "s2", "rooms": { "invite": { room: { "invite_state": { "events": [decoy, from_eve.clone()] } } } } });
        syncer.handle(&config, &body, false).await;
    }
    assert!(stand.calls("POST", "/join/").is_empty());
    let leaves: Vec<String> = stand.calls("POST", "/leave").into_iter().map(|(p, _)| p).collect();
    assert_eq!(leaves, vec!["/_matrix/client/v3/rooms/!other-member:x/leave", "/_matrix/client/v3/rooms/!old-leave:x/leave", "/_matrix/client/v3/rooms/!other-type:x/leave"]);
}

#[tokio::test]
async fn messages_from_allowed_users_reach_the_hub_after_the_first_sync() {
    let (stand, root) = stand_in().await;
    let (dir, config) = setup("messages", &root);
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let (syncer, _) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir, BOT, hub);
    // The first sync is history: not answered.
    syncer.handle(&config, &message("!dm:x", OWNER, "old question"), true).await;
    assert!(inbound.try_recv().is_err());
    syncer.handle(&config, &message("!dm:x", OWNER, "new question"), false).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "matrix:!dm:x".into(), text: "new question".into() });
    // The bot's own messages and other senders are ignored.
    syncer.handle(&config, &message("!dm:x", BOT, "an answer"), false).await;
    syncer.handle(&config, &message("!dm:x", "@eve:x", "hi"), false).await;
    assert!(inbound.try_recv().is_err());
    let _ = stand;
}

/// The bot never answers itself, even when its own account is on the allowed list by mistake: its
/// answers would come back as questions and it would talk to itself without end.
#[tokio::test]
async fn the_bots_own_messages_are_ignored_even_when_it_is_on_the_allowed_list() {
    let (stand, root) = stand_in().await;
    let (dir, mut config) = setup("own-echo", &root);
    config.allowed.push(BOT.to_string());
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let (syncer, _) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir, BOT, hub);
    syncer.handle(&config, &message("!dm:x", BOT, "an answer"), false).await;
    assert!(inbound.try_recv().is_err());
    syncer.handle(&config, &message("!dm:x", OWNER, "a question"), false).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Text { chat: "matrix:!dm:x".into(), text: "a question".into() });
    let _ = stand;
}

/// An image (with a caption, Matrix 1.10 style) is downloaded and reaches the hub as media; a
/// voice message is named as dropped.
#[tokio::test]
async fn images_and_audio_reach_the_hub_downloaded_and_video_is_named_as_dropped() {
    let (stand, root) = stand_in().await;
    let (dir, config) = setup("media", &root);
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let (syncer, _) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir, BOT, hub);
    let mut image = message("!dm:x", OWNER, "");
    image["rooms"]["join"]["!dm:x"]["timeline"]["events"][0]["content"] =
        json!({ "msgtype": "m.image", "body": "is this one quiet?", "filename": "shot.png", "url": "mxc://node.tail.ts.net/abc123", "info": { "mimetype": "image/png" } });
    syncer.handle(&config, &image, false).await;
    let Inbound::Media { chat, text, attachments, dropped } = inbound.try_recv().unwrap() else { panic!("not media") };
    assert_eq!((chat.as_str(), text.as_str()), ("matrix:!dm:x", "is this one quiet?"));
    assert_eq!((attachments[0].name.as_str(), attachments[0].mime.as_str()), ("shot.png", "image/png"));
    assert_eq!(attachments[0].bytes, b"MEDIA:node.tail.ts.net/abc123");
    assert!(dropped.is_empty());
    let mut audio = message("!dm:x", OWNER, "");
    audio["rooms"]["join"]["!dm:x"]["timeline"]["events"][0]["content"] = json!({ "msgtype": "m.audio", "body": "voice.ogg", "url": "mxc://node.tail.ts.net/v1" });
    syncer.handle(&config, &audio, false).await;
    let Inbound::Media { attachments, dropped, .. } = inbound.try_recv().unwrap() else { panic!("not media") };
    assert_eq!((attachments[0].name.as_str(), attachments[0].mime.as_str()), ("voice.ogg", "audio/ogg"));
    assert!(dropped.is_empty());
    let mut video = message("!dm:x", OWNER, "");
    video["rooms"]["join"]["!dm:x"]["timeline"]["events"][0]["content"] = json!({ "msgtype": "m.video", "body": "clip.mp4", "url": "mxc://node.tail.ts.net/c1" });
    syncer.handle(&config, &video, false).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Media { chat: "matrix:!dm:x".into(), text: String::new(), attachments: vec![], dropped: vec!["a video".into()] });
    let _ = stand;
}

#[tokio::test]
async fn a_room_with_a_third_member_or_encryption_is_left() {
    let (stand, root) = stand_in().await;
    let (dir, config) = setup("crowd", &root);
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let (syncer, _) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir, BOT, hub);
    let mut crowded = message("!crowd:x", OWNER, "hello all");
    crowded["rooms"]["join"]["!crowd:x"]["summary"]["m.joined_member_count"] = json!(3);
    syncer.handle(&config, &crowded, false).await;
    let mut encrypted = message("!enc:x", OWNER, "secret");
    encrypted["rooms"]["join"]["!enc:x"]["state"] = json!({ "events": [{ "type": "m.room.encryption", "state_key": "", "content": {} }] });
    syncer.handle(&config, &encrypted, false).await;
    assert!(inbound.try_recv().is_err());
    let leaves: Vec<String> = stand.calls("POST", "/leave").into_iter().map(|(p, _)| p).collect();
    assert_eq!(leaves, vec!["/_matrix/client/v3/rooms/!crowd:x/leave", "/_matrix/client/v3/rooms/!enc:x/leave"]);
}

#[tokio::test]
async fn answers_are_formatted_typing_is_renewed_and_reactions_answer_approvals() {
    let (stand, root) = stand_in().await;
    let (dir, config) = setup("render", &root);
    let (hub, mut inbound) = mpsc::unbounded_channel();
    let (syncer, renderer) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir, BOT, hub);
    let chat = "matrix:!dm:x".to_string();
    renderer.render(Outbound::Draft { chat: chat.clone(), text: String::new() }).await;
    renderer.render(Outbound::Draft { chat: chat.clone(), text: "more".into() }).await;
    let typing = stand.calls("PUT", "/typing/");
    assert_eq!(typing.len(), 1, "typing is renewed every 20 s, not on every draft");
    assert_eq!(typing[0].0, format!("/_matrix/client/v3/rooms/!dm:x/typing/{BOT}"));
    assert_eq!(typing[0].1["typing"], true);
    renderer.render(Outbound::TurnEnded { chat: chat.clone() }).await;
    assert_eq!(stand.calls("PUT", "/typing/")[1].1, json!({ "typing": false }));

    renderer.render(Outbound::Message { chat: chat.clone(), markdown: "**Yes**: `ls`".into() }).await;
    let sent = stand.calls("PUT", "/send/m.room.message/");
    assert_eq!(sent[0].1["msgtype"], "m.text");
    assert_eq!(sent[0].1["body"], "**Yes**: `ls`");
    assert_eq!(sent[0].1["format"], "org.matrix.custom.html");
    assert_eq!(sent[0].1["formatted_body"], "<b>Yes</b>: <code>ls</code>");

    renderer
        .render(Outbound::Approval { chat: chat.clone(), id: "3".into(), text: "Run `make`?".into(), options: vec!["Approve once".into(), "Decline".into()] })
        .await;
    let approval_event = format!("$e{}", stand.calls("PUT", "/send/").len());
    let reaction = |key: &str| {
        json!({ "next_batch": "s9", "rooms": { "join": { "!dm:x": {
            "summary": { "m.joined_member_count": 2 },
            "timeline": { "events": [{ "type": "m.reaction", "sender": OWNER, "content": { "m.relates_to": { "rel_type": "m.annotation", "event_id": approval_event, "key": key } } }] },
        } } } })
    };
    syncer.handle(&config, &reaction("🎉"), false).await;
    assert!(inbound.try_recv().is_err());
    syncer.handle(&config, &reaction("❌"), false).await;
    assert_eq!(inbound.try_recv().unwrap(), Inbound::Answer { chat: chat.clone(), approval: "3".into(), choice: 1 });
    renderer.render(Outbound::ApprovalClosed { chat: chat.clone(), id: "3".into(), outcome: "Declined.".into() }).await;
    // Closed: a late reaction finds nothing.
    syncer.handle(&config, &reaction("✅"), false).await;
    assert!(inbound.try_recv().is_err());
}

#[tokio::test]
async fn the_bridge_opens_a_room_per_allowed_user_and_keeps_its_place() {
    let (stand, root) = stand_in().await;
    let (dir, _config) = setup("run", &root);
    // A room left over from a user who is no longer allowed.
    let mut stale = MatrixState::load(&dir);
    stale.rooms.insert("@gone:node.tail.ts.net".to_string(), "!old:node.tail.ts.net".to_string());
    stale.save(&dir).unwrap();
    stand.syncs.lock().unwrap().push_back(json!({ "next_batch": "s1" }));
    let (hub, _inbound) = mpsc::unbounded_channel();
    let (syncer, _) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir.clone(), BOT, hub);
    let task = tokio::spawn(syncer.run());
    for _ in 0..100 {
        if MatrixState::load(&dir).next_batch.is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    // Turning Matrix off ends the loop.
    std::fs::remove_file(MatrixConfig::path(&dir)).unwrap();
    tokio::time::timeout(Duration::from_secs(5), task).await.unwrap().unwrap();
    let state = MatrixState::load(&dir);
    assert_eq!(state.rooms.get(OWNER).map(String::as_str), Some("!dm:node.tail.ts.net"));
    assert!(!state.rooms.contains_key("@gone:node.tail.ts.net"), "a user no longer allowed loses their room");
    assert!(state.next_batch.is_some());
    let created = stand.calls("POST", "/createRoom");
    assert_eq!(created.len(), 1, "one room per user, not one per sync");
    assert_eq!(created[0].1, json!({ "is_direct": true, "preset": "private_chat", "invite": [OWNER], "name": "Mightling" }));
    // The second sync continues from where the first ended.
    let syncs = stand.calls("GET", "/sync");
    assert!(syncs.len() >= 2);
    let auth_ok = stand.calls.lock().unwrap().iter().all(|(_, p, _)| !p.contains("bot-token"));
    assert!(auth_ok, "the token goes in a header, never in a path");
}

#[tokio::test]
async fn a_file_is_uploaded_and_sent_as_a_file_message() {
    let (stand, root) = stand_in().await;
    let (dir, _config) = setup("file", &root);
    let (hub, _inbound) = mpsc::unbounded_channel();
    let (_, renderer) = ling_chat::matrix::pair_for_tests(MatrixApi::new(&root, "bot-token"), dir, BOT, hub);
    renderer.render(Outbound::File { chat: "matrix:!dm:x".into(), name: "report.md".into(), bytes: b"# r".to_vec() }).await;
    assert_eq!(stand.calls("POST", "/_matrix/media/v3/upload").len(), 1);
    let sent = stand.calls("PUT", "/send/m.room.message/");
    assert_eq!(sent[0].1, json!({ "msgtype": "m.file", "body": "report.md", "filename": "report.md", "url": "mxc://node.tail.ts.net/up1", "info": { "size": 3 } }));
}

