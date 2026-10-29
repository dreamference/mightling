//! Discord's Gateway (MIGHTLING_DISCORD §3.3): one outbound WebSocket that brings the bot's events.
//!
//! Hello starts the heartbeat (the first one jittered); a heartbeat Discord does not acknowledge
//! means a dead connection, closed and resumed. Identify asks for direct messages only (intents
//! `GUILDS | DIRECT_MESSAGES`: the first so a server join is seen and left). A session is resumed
//! where it can be (op 7, a non-fatal close, op 9 with `d: true`), and identified again where it
//! cannot. Identifies are counted in `discord-state.json`: Discord resets a bot's token after 1,000
//! in a day, so at the user's limit (100 by default) the adapter stops instead. A close code that
//! means the token or the intents are wrong stops it too.
use std::path::PathBuf;
use std::time::Duration;
use futures::SinkExt;
use futures::StreamExt;
use reqwest::Method;
use serde_json::Value;
use serde_json::json;
use tokio::sync::mpsc;
use tokio::time::Instant;
use tokio_tungstenite::tungstenite::Message;
use crate::discord::DiscordApi;
use crate::store;
use crate::store::DiscordState;

/// `GUILDS` (1) and `DIRECT_MESSAGES` (4096).
pub const INTENTS: u64 = 1 | (1 << 12);
const BACKOFF_CAP: Duration = Duration::from_secs(300);
/// Close codes after which reconnecting cannot help: authentication failed, an invalid shard,
/// sharding required, an invalid API version, invalid or disallowed intents.
const FATAL_CLOSES: [u16; 6] = [4004, 4010, 4011, 4012, 4013, 4014];
/// Close codes after which the session cannot be resumed: an invalid sequence, a timed-out session.
const FRESH_CLOSES: [u16; 2] = [4007, 4009];

/// How a connection ended.
#[derive(Debug, PartialEq)]
pub enum Ended {
    /// Reconnect and resume the session.
    Resume,
    /// Reconnect and identify again.
    Identify,
    /// Stop: the reason is logged.
    Fatal(String),
}

/// Runs the Gateway until a fatal close or the identify limit; events go to `events` as
/// `(name, data)`.
pub async fn run(api: DiscordApi, dir: PathBuf, events: mpsc::UnboundedSender<(String, Value)>, identify_limit: usize) {
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
    let mut backoff = Duration::from_secs(1);
    loop {
        // `ling chat discord off` deleted the token: do not connect again.
        if store::DiscordConfig::load(&dir).is_none() {
            eprintln!("ling chat: Discord is off; the Gateway is not reconnected.");
            return;
        }
        let started = Instant::now();
        match connect_once(&api, &dir, &events, identify_limit).await {
            Ended::Fatal(reason) => {
                eprintln!("ling chat: Discord stopped: {reason}");
                return;
            }
            Ended::Identify => {
                let mut state = DiscordState::load(&dir);
                state.session_id = None;
                state.seq = None;
                state.resume_url = None;
                let _ = state.save(&dir);
            }
            Ended::Resume => {}
        }
        if events.is_closed() {
            return;
        }
        // A connection that lasted resets the wait; one that failed at once doubles it.
        if started.elapsed() > Duration::from_secs(60) {
            backoff = Duration::from_secs(1);
        }
        let jitter = Duration::from_millis(u64::from(jitter_ms(1000)));
        tokio::time::sleep(backoff + jitter).await;
        backoff = (backoff * 2).min(BACKOFF_CAP);
    }
}

/// A pseudo-random wait in `0..limit` milliseconds.
fn jitter_ms(limit: u32) -> u32 {
    use std::hash::BuildHasher;
    (std::collections::hash_map::RandomState::new().hash_one(std::time::Instant::now()) % u64::from(limit.max(1))) as u32
}

/// One connection, from the URL to its end.
pub async fn connect_once(api: &DiscordApi, dir: &PathBuf, events: &mpsc::UnboundedSender<(String, Value)>, identify_limit: usize) -> Ended {
    let mut state = DiscordState::load(dir);
    // Without a resume address (one that stopped answering) the session resumes through `/gateway/bot`'s.
    let resuming = state.session_id.is_some();
    let base = match &state.resume_url {
        Some(url) if resuming => url.clone(),
        _ => match api.request(Method::GET, "/gateway/bot", None).await {
            Ok(gateway) => match gateway.get("url").and_then(Value::as_str) {
                Some(url) => url.to_string(),
                None => return Ended::Resume,
            },
            Err(err) if err.status == 401 => return Ended::Fatal("Discord refused the token (HTTP 401)".to_string()),
            Err(err) => {
                eprintln!("ling chat: Discord: no Gateway address ({err})");
                return Ended::Resume;
            }
        },
    };
    if !(base.starts_with("wss://") || base.starts_with("ws://127.0.0.1")) {
        return Ended::Fatal("Discord named a Gateway address that is not wss://".to_string());
    }
    let url = format!("{}/?v=10&encoding=json", base.trim_end_matches('/'));
    let mut socket = match tokio_tungstenite::connect_async(url.as_str()).await {
        Ok((socket, _)) => socket,
        Err(err) => {
            eprintln!("ling chat: Discord: the Gateway did not answer ({err})");
            // A resume address that does not answer is not tried again: the next try asks `/gateway/bot`.
            if resuming && state.resume_url.take().is_some() {
                let _ = state.save(dir);
            }
            return if resuming { Ended::Resume } else { Ended::Identify };
        }
    };
    // Hello first, with the heartbeat interval.
    let interval = loop {
        match tokio::time::timeout(Duration::from_secs(30), socket.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                let frame: Value = serde_json::from_str(&text).unwrap_or_default();
                if frame.get("op").and_then(Value::as_u64) == Some(10) {
                    break Duration::from_millis(frame.pointer("/d/heartbeat_interval").and_then(Value::as_u64).unwrap_or(41_250).max(1_000));
                }
            }
            Ok(Some(Ok(Message::Close(frame)))) => return closed(frame.map(|frame| u16::from(frame.code))),
            Ok(Some(Ok(_))) => {}
            _ => return Ended::Resume,
        }
    };
    let identify = |state: &mut DiscordState| -> Result<Value, Ended> {
        if state.identifies_today(store::now_secs()) >= identify_limit {
            return Err(Ended::Fatal(format!("{identify_limit} connections in 24 hours (`[chat] discord_identify_limit`); `ling chat restart` after checking the network")));
        }
        state.identifies.push(store::now_secs());
        let _ = state.save(dir);
        Ok(json!({ "op": 2, "d": { "token": api.token(), "intents": INTENTS, "properties": { "os": std::env::consts::OS, "browser": "mightling", "device": "mightling" } } }))
    };
    let opening = if resuming {
        json!({ "op": 6, "d": { "token": api.token(), "session_id": state.session_id, "seq": state.seq } })
    } else {
        match identify(&mut state) {
            Ok(frame) => frame,
            Err(ended) => return ended,
        }
    };
    if socket.send(Message::text(opening.to_string())).await.is_err() {
        return Ended::Resume;
    }
    let mut next_beat = Instant::now() + interval.mul_f64(f64::from(jitter_ms(1000)) / 1000.0);
    let mut acked = true;
    // A reconnect reloads the state, so the sequence is saved on every way out, not only now and then.
    let ended = async {
        loop {
            tokio::select! {
                _ = tokio::time::sleep_until(next_beat) => {
                    if !acked {
                        // A zombie connection: close it (not with 1000, which ends the session) and resume.
                        let _ = socket.close(Some(tokio_tungstenite::tungstenite::protocol::CloseFrame { code: 4000.into(), reason: "".into() })).await;
                        return Ended::Resume;
                    }
                    acked = false;
                    next_beat = Instant::now() + interval;
                    if socket.send(Message::text(json!({ "op": 1, "d": state.seq }).to_string())).await.is_err() {
                        return Ended::Resume;
                    }
                }
                frame = socket.next() => {
                    let text = match frame {
                        Some(Ok(Message::Text(text))) => text,
                        Some(Ok(Message::Close(frame))) => return closed(frame.map(|frame| u16::from(frame.code))),
                        Some(Ok(_)) => continue,
                        _ => return Ended::Resume,
                    };
                    let frame: Value = serde_json::from_str(&text).unwrap_or_default();
                    match frame.get("op").and_then(Value::as_u64) {
                        Some(0) => {
                            if let Some(seq) = frame.get("s").and_then(Value::as_u64) {
                                state.seq = Some(seq);
                            }
                            let name = frame.get("t").and_then(Value::as_str).unwrap_or_default().to_string();
                            let data = frame.get("d").cloned().unwrap_or_default();
                            if name == "READY" {
                                state.session_id = data.get("session_id").and_then(Value::as_str).map(str::to_string);
                                state.resume_url = data.get("resume_gateway_url").and_then(Value::as_str).map(str::to_string);
                                let _ = state.save(dir);
                            } else if name == "RESUMED" {
                                let _ = state.save(dir);
                            } else if events.send((name, data)).is_err() {
                                return Ended::Fatal("the bridge stopped".to_string());
                            }
                            // The sequence is saved now and then, so a restart resumes close to here.
                            if state.seq.is_some_and(|seq| seq % 20 == 0) {
                                let _ = state.save(dir);
                            }
                        }
                        Some(1) => {
                            let _ = socket.send(Message::text(json!({ "op": 1, "d": state.seq }).to_string())).await;
                        }
                        Some(7) => {
                            let _ = state.save(dir);
                            let _ = socket.close(Some(tokio_tungstenite::tungstenite::protocol::CloseFrame { code: 4000.into(), reason: "".into() })).await;
                            return Ended::Resume;
                        }
                        Some(9) => {
                            if frame.get("d") == Some(&Value::Bool(true)) {
                                return Ended::Resume;
                            }
                            tokio::time::sleep(Duration::from_millis(1000 + u64::from(jitter_ms(4000)))).await;
                            return Ended::Identify;
                        }
                        Some(11) => acked = true,
                        _ => {}
                    }
                }
            }
        }
    }
    .await;
    let _ = state.save(dir);
    ended
}

fn closed(code: Option<u16>) -> Ended {
    match code {
        Some(code) if FATAL_CLOSES.contains(&code) => Ended::Fatal(format!("Discord closed the Gateway with code {code} (check the token and the bot's settings)")),
        Some(code) if FRESH_CLOSES.contains(&code) => Ended::Identify,
        _ => Ended::Resume,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn close_codes_decide_what_follows() {
        assert!(matches!(closed(Some(4004)), Ended::Fatal(_)));
        assert!(matches!(closed(Some(4014)), Ended::Fatal(_)));
        assert_eq!(closed(Some(4009)), Ended::Identify);
        assert_eq!(closed(Some(4000)), Ended::Resume);
        assert_eq!(closed(None), Ended::Resume);
        assert_eq!(INTENTS, 4097);
    }
}
