//! The sync hub's routes (specs/DREAMFERENCE_MIGHTLING_SYNC.md §3, §5): `/api/sync/` on the node's
//! `ling web`, over the store in `sync_store.rs`.
//!
//! - **Off until enabled.** Until `ling sync enable` on this machine says the hub is on, every route
//!   here answers 404, exactly as a path that does not exist, and nothing is written.
//! - **Who may call.** A sync agent sends its device secret as `Authorization: Bearer …`; it opens
//!   these routes and nothing else (auth.rs). A paired browser and this machine's own session read
//!   with their cookies, and change other devices' threads only with `control` (§5.1). Only an
//!   agent uploads, and only its own threads.
//! - **Pairing an agent** takes the same eight-digit code a browser does (`ling web pair`), at
//!   `/api/sync/pair`, under the same ten-guesses rule.
//! - **Nothing here runs a turn or opens a working tree**; nothing reaches the app-server.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use axum::body::Bytes;
use axum::extract::DefaultBodyLimit;
use axum::extract::Path;
use axum::extract::Query;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::http::HeaderValue;
use axum::http::StatusCode;
use axum::http::header;
use axum::response::IntoResponse;
use axum::response::Response;
use axum::routing::get;
use axum::routing::post;
use serde_json::Value;
use serde_json::json;

use crate::auth;
use crate::server::Server;
use crate::server::header_str;
use crate::sync_live;
use crate::sync_store;
use crate::sync_store::Caller;
use crate::sync_store::Hlc;
use crate::sync_store::Refusal;
use crate::sync_store::Store;
use crate::sync_store::Upload;

/// The longest a manifest request waits for a change.
pub const MAX_WAIT: Duration = Duration::from_secs(30);
/// The most a rollout read returns at once.
pub const READ_CAP: usize = 16 << 20;

pub fn routes() -> Router<Arc<Server>> {
    Router::new()
        .route("/api/sync/status", get(status))
        .route("/api/sync/pair", post(pair))
        .route("/api/sync/manifest", get(manifest))
        .route(
            "/api/sync/rollouts/{rollout}",
            get(read_rollout).put(upload).layer(DefaultBodyLimit::max(sync_store::UPLOAD_CAP + 4096)),
        )
        .route("/api/sync/threads", get(threads))
        .route("/api/sync/threads/{thread}", get(thread))
        .route("/api/sync/threads/{thread}/meta", post(set_meta))
        .route("/api/sync/threads/{thread}/delete", post(delete))
        .route("/api/sync/threads/{thread}/withdraw", post(withdraw))
        .route("/api/sync/forget", post(forget))
        .route("/api/sync/host", post(host).layer(DefaultBodyLimit::max(HOST_POST_CAP)))
        .route("/api/sync/live/{thread}", get(follow))
        .route("/api/sync/commands", post(command))
        .route("/api/sync/commands/{id}", get(command_state))
        .route("/api/sync/notices", get(notices))
}

/// The largest host post: a batch of events, each at most `sync_live::EVENT_CAP`.
pub const HOST_POST_CAP: usize = 16 << 20;

fn json_response(status: StatusCode, value: Value) -> Response {
    (status, [(header::CONTENT_TYPE, "application/json")], value.to_string()).into_response()
}

fn not_found() -> Response {
    (StatusCode::NOT_FOUND, [(header::CONTENT_TYPE, "text/plain; charset=utf-8")], "Not found.\n").into_response()
}

fn refused(refusal: Refusal) -> Response {
    match refusal {
        Refusal::NotFound => json_response(StatusCode::NOT_FOUND, json!({ "error": "no such thread" })),
        Refusal::Gone => json_response(StatusCode::GONE, json!({ "error": "the thread was deleted" })),
        Refusal::Mismatch { length, hash } => {
            json_response(StatusCode::CONFLICT, json!({ "error": "mismatch", "length": length, "hash": hash }))
        }
        Refusal::Owned { fork, thread } => json_response(StatusCode::CONFLICT, json!({ "error": "owned", "fork": fork, "thread": thread })),
        Refusal::Forbidden(why) => json_response(StatusCode::FORBIDDEN, json!({ "error": why })),
        Refusal::BadRequest(why) => json_response(StatusCode::BAD_REQUEST, json!({ "error": why })),
        Refusal::Io(why) => json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": why })),
    }
}

/// The caller of a sync route: an agent by its bearer secret, else a browser or this machine's
/// session by cookie. The guard has already checked that one of them is valid.
pub fn caller(server: &Server, headers: &HeaderMap) -> Option<Caller> {
    let state = &server.config.state_dir;
    if let Some(secret) = header_str(headers, header::AUTHORIZATION).and_then(|value| value.strip_prefix("Bearer ")) {
        let device = auth::sync_device_for_token(state, secret.trim())?;
        return Some(Caller { device: device.id, name: device.name, agent: true, scope: device.scope });
    }
    if server.owner_session(headers) {
        return Some(Caller::owner_session());
    }
    let cookie = auth::cookie(header_str(headers, header::COOKIE), auth::DEVICE_COOKIE)?;
    let device = auth::device_for_token(state, cookie)?;
    Some(Caller { device: device.id, name: device.name, agent: false, scope: device.scope })
}

/// The store when the hub is on; `None` (every route a 404) when it is not.
fn hub(server: &Server) -> Option<Arc<Store>> {
    server.sync_store()
}

/// This machine's own sync agent, if it has one: its threads are the page's own, not "elsewhere".
fn local_device(server: &Server) -> Option<String> {
    let text = std::fs::read_to_string(server.config.codex_home.join("sync").join("device.toml")).ok()?;
    let table: toml::Table = toml::from_str(&text).ok()?;
    table.get("device")?.as_str().map(str::to_string)
}

async fn status(State(server): State<Arc<Server>>, headers: HeaderMap) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    let Some(caller) = caller(&server, &headers) else { return not_found() };
    let devices: Vec<Value> = auth::load_devices(&server.config.state_dir)
        .iter()
        .map(|device| {
            let presence = server.sync_live().map(|live| live.presence(&device.id)).unwrap_or_default();
            json!({ "id": device.id, "name": device.name, "kind": device.kind.name(), "scope": device.scope.name(), "last_used": device.last_used,
                "online": presence.online, "last_seen": presence.last_seen, "projects": presence.projects, "models": presence.models })
        })
        .collect();
    json_response(
        StatusCode::OK,
        json!({
            "enabled": true,
            "cursor": store.cursor(),
            "this_device": local_device(&server),
            "caller": { "device": caller.device, "agent": caller.agent, "scope": caller.scope.name() },
            "devices": devices,
        }),
    )
}

/// A sync agent pairs with the code `ling web pair` shows (`{"code": …, "name": …}`), and gets its
/// device id and secret, once.
async fn pair(State(server): State<Arc<Server>>, body: Bytes) -> Response {
    if hub(&server).is_none() {
        return not_found();
    }
    let request: Value = serde_json::from_slice(&body).unwrap_or_default();
    let code = request.get("code").and_then(Value::as_str).unwrap_or_default().replace(' ', "");
    let name = request.get("name").and_then(Value::as_str).unwrap_or_default();
    let state = &server.config.state_dir;
    if !auth::take_pairing_code(state, &code) {
        server.pairing_failed();
        tokio::time::sleep(Duration::from_millis(500)).await;
        return json_response(
            StatusCode::UNAUTHORIZED,
            json!({ "error": "That code is wrong or has expired. Run `ling web pair` on the node for a new one." }),
        );
    }
    server.pairing_succeeded();
    match auth::add_device_of(state, name, auth::DeviceKind::Sync) {
        Ok((device, secret)) => json_response(StatusCode::OK, json!({ "device": device, "secret": secret, "name": auth::clean_device_name(name) })),
        Err(err) => json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({ "error": format!("Could not record the device: {err}") })),
    }
}

/// Every change after `?since=<cursor>`; with `wait=<seconds>`, held until there is one.
async fn manifest(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    let since = query.get("since").and_then(|value| value.parse::<u64>().ok()).unwrap_or(0);
    let wait = query.get("wait").and_then(|value| value.parse::<u64>().ok()).map(Duration::from_secs).unwrap_or_default().min(MAX_WAIT);
    if !wait.is_zero() {
        store.wait_past(since, wait).await;
    }
    let manifest = store.manifest(since);
    json_response(
        StatusCode::OK,
        json!({
            "cursor": manifest.cursor,
            "rollouts": manifest.rollouts.iter().map(sync_store::Record::listed).collect::<Vec<_>>(),
            "tombstones": manifest.tombstones,
        }),
    )
}

/// An agent's upload: `PUT /api/sync/rollouts/<rollout>?offset=&prefix=[&thread=&file=&archived=&fork_of=]`.
async fn upload(
    State(server): State<Arc<Server>>,
    Path(rollout): Path<String>,
    Query(query): Query<HashMap<String, String>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    let Some(caller) = caller(&server, &headers) else { return not_found() };
    let Some(offset) = query.get("offset").and_then(|value| value.parse::<u64>().ok()) else {
        return refused(Refusal::BadRequest("say where the bytes go: ?offset="));
    };
    let upload = Upload {
        offset,
        prefix: query.get("prefix").cloned().unwrap_or_default(),
        thread: query.get("thread").cloned(),
        file: query.get("file").cloned(),
        archived: query.get("archived").map(String::as_str) == Some("1"),
        fork_of: query.get("fork_of").cloned(),
    };
    let result = tokio::task::spawn_blocking(move || store.append(&rollout, &caller, &upload, &body)).await;
    match result {
        Ok(Ok(record)) => json_response(StatusCode::OK, json!({ "length": record.length, "hash": record.hash, "seq": record.seq })),
        Ok(Err(refusal)) => refused(refusal),
        Err(err) => refused(Refusal::Io(err.to_string())),
    }
}

/// A rollout's bytes from `?offset=`, with the hub's length and hash in `x-sync-length`/`x-sync-hash`.
async fn read_rollout(State(server): State<Arc<Server>>, Path(rollout): Path<String>, Query(query): Query<HashMap<String, String>>) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    let offset = query.get("offset").and_then(|value| value.parse::<u64>().ok()).unwrap_or(0);
    match tokio::task::spawn_blocking(move || store.read(&rollout, offset, READ_CAP)).await {
        Ok(Ok((bytes, meta))) => {
            let mut response = (StatusCode::OK, [(header::CONTENT_TYPE, "application/x-ndjson")], bytes).into_response();
            let headers = response.headers_mut();
            if let Ok(value) = HeaderValue::from_str(&meta.length.to_string()) {
                headers.insert("x-sync-length", value);
            }
            if let Ok(value) = HeaderValue::from_str(&meta.hash) {
                headers.insert("x-sync-hash", value);
            }
            response
        }
        Ok(Err(refusal)) => refused(refusal),
        Err(err) => refused(Refusal::Io(err.to_string())),
    }
}

/// Every synced thread, newest first, with the cursor a page waits on for the next change.
async fn threads(State(server): State<Arc<Server>>) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    let cursor = store.cursor();
    let threads: Vec<Value> = store.threads().iter().map(sync_store::ThreadView::listed).collect();
    json_response(StatusCode::OK, json!({ "cursor": cursor, "this_device": local_device(&server), "threads": threads }))
}

/// A thread as a device shows it: its listing and its transcript.
async fn thread(State(server): State<Arc<Server>>, Path(thread): Path<String>) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    match tokio::task::spawn_blocking(move || store.items(&thread)).await {
        Ok(Ok((items, view))) => json_response(StatusCode::OK, json!({ "thread": view.listed(), "items": items })),
        Ok(Err(refusal)) => refused(refusal),
        Err(err) => refused(Refusal::Io(err.to_string())),
    }
}

/// `{"archived": bool, "archived_hlc"?: …, "name": str, "name_hlc"?: …}`: either or both.
async fn set_meta(State(server): State<Arc<Server>>, Path(thread): Path<String>, headers: HeaderMap, body: Bytes) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    let Some(caller) = caller(&server, &headers) else { return not_found() };
    let Ok(request) = serde_json::from_slice::<Value>(&body) else {
        return refused(Refusal::BadRequest("send JSON"));
    };
    let reading = |key: &str| request.get(key).and_then(Value::as_str).and_then(Hlc::parse);
    let archived = request.get("archived").and_then(Value::as_bool).map(|value| (value, reading("archived_hlc")));
    let name = request.get("name").and_then(Value::as_str).map(|value| (value.to_string(), reading("name_hlc")));
    if archived.is_none() && name.is_none() {
        return refused(Refusal::BadRequest("send archived or name"));
    }
    match tokio::task::spawn_blocking(move || store.set_meta(&thread, &caller, archived, name)).await {
        Ok(Ok(record)) => json_response(StatusCode::OK, json!({ "rollout": record.listed() })),
        Ok(Err(refusal)) => refused(refusal),
        Err(err) => refused(Refusal::Io(err.to_string())),
    }
}

/// Deletes a thread everywhere (§5.4).
async fn delete(State(server): State<Arc<Server>>, Path(thread): Path<String>, headers: HeaderMap) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    let Some(caller) = caller(&server, &headers) else { return not_found() };
    let live = server.sync_live();
    let deleted = thread.clone();
    match tokio::task::spawn_blocking(move || store.delete(&thread, &caller)).await {
        Ok(Ok(tombstones)) => {
            if let Some(live) = live {
                live.forget_thread(&deleted);
            }
            json_response(StatusCode::OK, json!({ "tombstones": tombstones }))
        }
        Ok(Err(refusal)) => refused(refusal),
        Err(err) => refused(Refusal::Io(err.to_string())),
    }
}

/// Takes a thread off the hub at its owner's request, without deleting it (`[sync] exclude`).
async fn withdraw(State(server): State<Arc<Server>>, Path(thread): Path<String>, headers: HeaderMap) -> Response {
    let Some(store) = hub(&server) else { return not_found() };
    let Some(caller) = caller(&server, &headers) else { return not_found() };
    match tokio::task::spawn_blocking(move || store.withdraw(&thread, &caller)).await {
        Ok(Ok(tombstones)) => json_response(StatusCode::OK, json!({ "tombstones": tombstones })),
        Ok(Err(refusal)) => refused(refusal),
        Err(err) => refused(Refusal::Io(err.to_string())),
    }
}

/// `ling sync forget` on a device: its agent's credential ends here too.
async fn forget(State(server): State<Arc<Server>>, headers: HeaderMap) -> Response {
    if hub(&server).is_none() {
        return not_found();
    }
    match caller(&server, &headers) {
        Some(caller) if caller.agent => match auth::revoke_device(&server.config.state_dir, &caller.device) {
            Ok(_) => json_response(StatusCode::OK, json!({ "forgotten": caller.device })),
            Err(err) => refused(Refusal::Io(err.to_string())),
        },
        _ => refused(Refusal::Forbidden("only a sync agent forgets itself")),
    }
}

/// Each thread's owner and title, as the store has them.
fn owners(store: &Store) -> HashMap<String, (String, String, String)> {
    store.threads().into_iter().map(|view| (view.current.thread.clone(), (view.current.owner.clone(), view.current.owner_name.clone(), view.title()))).collect()
}

/// A host's post (`sync_live::HostPost`): its events and answers in, its commands out; with
/// `?wait=<seconds>` and nothing to hand over yet, held until a command comes.
async fn host(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>, headers: HeaderMap, body: Bytes) -> Response {
    let (Some(store), Some(live)) = (hub(&server), server.sync_live()) else { return not_found() };
    let Some(caller) = caller(&server, &headers) else { return not_found() };
    if !caller.agent {
        return refused(Refusal::Forbidden("only a device's own sync credential hosts its threads"));
    }
    let Ok(post) = serde_json::from_slice::<sync_live::HostPost>(&body) else {
        return refused(Refusal::BadRequest("send a host post as JSON"));
    };
    let wait = query.get("wait").and_then(|value| value.parse::<u64>().ok()).map(Duration::from_secs).unwrap_or_default().min(MAX_WAIT);
    let threads = owners(&store);
    let owner_of = |thread: &str| threads.get(thread).map(|(owner, _, _)| owner.clone());
    let title_of = |thread: &str| threads.get(thread).map(|(_, _, title)| title.clone());
    let idle = sync_live::HostPost { events: Vec::new(), acks: Vec::new(), ..post.clone() };
    let mut commands = live.host_post(&caller, post, &owner_of, &title_of);
    let deadline = tokio::time::Instant::now() + wait;
    while commands.is_empty() && tokio::time::Instant::now() < deadline {
        let seen = live.seq();
        live.wait_past(seen, deadline - tokio::time::Instant::now()).await;
        commands = live.host_post(&caller, idle.clone(), &owner_of, &title_of);
    }
    json_response(StatusCode::OK, json!({ "commands": commands }))
}

/// A thread live: `?after=<seq>&wait=<seconds>`, its events after that number, whether a turn runs,
/// the approvals waiting, the messages queued, and whether its device is online.
async fn follow(State(server): State<Arc<Server>>, Path(thread): Path<String>, Query(query): Query<HashMap<String, String>>) -> Response {
    let (Some(store), Some(live)) = (hub(&server), server.sync_live()) else { return not_found() };
    let after = query.get("after").and_then(|value| value.parse::<u64>().ok()).unwrap_or(0);
    let wait = query.get("wait").and_then(|value| value.parse::<u64>().ok()).map(Duration::from_secs).unwrap_or_default().min(MAX_WAIT);
    // Only a thread the store holds is followed: one kept on its device (`[sync] exclude`) never is.
    let Some((owner, owner_name, _)) = owners(&store).get(&thread).cloned() else { return refused(Refusal::NotFound) };
    let seen = live.seq();
    let mut following = live.follow(&thread, after);
    if following.events.is_empty() && !following.reset && !wait.is_zero() {
        live.wait_past(seen.max(after), wait).await;
        following = live.follow(&thread, after);
    }
    let presence = live.presence(&owner);
    let events: Vec<Value> = following.events.into_iter().map(|(seq, message)| json!({ "seq": seq, "message": message })).collect();
    json_response(
        StatusCode::OK,
        json!({
            "events": events,
            "next": following.next,
            "reset": following.reset,
            "running": following.running,
            "turn": following.turn,
            "pending": following.pending,
            "queued": following.queued,
            "owner": owner,
            "owner_name": owner_name,
            "online": presence.online,
            "last_seen": presence.last_seen,
            "models": presence.models,
        }),
    )
}

/// A following device's command (`sync_live::Ask`): to the thread's owner, or for `start` to the
/// device named.
async fn command(State(server): State<Arc<Server>>, headers: HeaderMap, body: Bytes) -> Response {
    let (Some(store), Some(live)) = (hub(&server), server.sync_live()) else { return not_found() };
    let Some(caller) = caller(&server, &headers) else { return not_found() };
    let Ok(ask) = serde_json::from_slice::<sync_live::Ask>(&body) else {
        return refused(Refusal::BadRequest("send a command as JSON"));
    };
    let owner = match (&ask.thread, &ask.device) {
        (Some(thread), _) if ask.kind != "start" => match owners(&store).get(thread) {
            Some((owner, _, _)) => owner.clone(),
            None => return refused(Refusal::NotFound),
        },
        (_, Some(device)) if ask.kind == "start" => {
            if !auth::load_devices(&server.config.state_dir).iter().any(|known| known.id == *device && known.kind == auth::DeviceKind::Sync) {
                return refused(Refusal::NotFound);
            }
            device.clone()
        }
        _ => return refused(Refusal::BadRequest("name the thread, or for a new thread the device")),
    };
    match live.command(&caller, ask, &owner) {
        Ok(command) => json_response(StatusCode::OK, json!({ "command": command })),
        Err(sync_live::Refused::Forbidden(why)) => refused(Refusal::Forbidden(why)),
        Err(sync_live::Refused::BadRequest(why)) => json_response(StatusCode::BAD_REQUEST, json!({ "error": why })),
        Err(sync_live::Refused::Offline(why)) => json_response(StatusCode::CONFLICT, json!({ "error": why, "offline": true })),
        Err(sync_live::Refused::QueueFull) => json_response(
            StatusCode::TOO_MANY_REQUESTS,
            json!({ "error": format!("{} messages already wait for this thread", sync_live::QUEUE_PER_THREAD) }),
        ),
    }
}

/// How a command went: queued, waiting (for an offline device), delivered, done, failed or expired.
async fn command_state(State(server): State<Arc<Server>>, Path(id): Path<String>) -> Response {
    let Some(live) = server.sync_live() else { return not_found() };
    match id.parse::<u64>().ok().and_then(|id| live.command_state(id)) {
        Some(command) => json_response(StatusCode::OK, json!({ "command": command })),
        None => refused(Refusal::NotFound),
    }
}

/// The notices after `?after=<seq>`, for the phone bridges to pass on, and the newest number.
async fn notices(State(server): State<Arc<Server>>, Query(query): Query<HashMap<String, String>>) -> Response {
    let Some(live) = server.sync_live() else { return not_found() };
    let after = query.get("after").and_then(|value| value.parse::<u64>().ok()).unwrap_or(0);
    let (notices, latest) = live.notices(after);
    json_response(StatusCode::OK, json!({ "notices": notices, "latest": latest }))
}
