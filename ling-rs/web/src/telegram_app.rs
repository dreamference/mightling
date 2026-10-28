//! `ling web` as a Telegram Mini App (specs/DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md §3.6, §15).
//!
//! The bot's menu button opens the page the user named in `[chat] telegram_mini_app_url`: `ling
//! web` behind an HTTPS front on the user's own overlay (`tailscale serve`, or the remote-access
//! overlay), which the phone's Telegram app loads **directly**. Everything typed into it travels
//! phone → overlay → node, never through Telegram's servers; Telegram learns only that the app was
//! opened, when, and its address.
//!
//! The device credential stays what lets a page in (auth.rs): a Telegram webview pairs with `ling
//! web pair` like any browser. What Telegram adds is checked on top: the page sends the launch
//! parameters (`initData`) it was opened with, and the server answers whether they are signed by
//! this node's bot (Telegram's HMAC, keyed by the bot token) for a Telegram user paired with `ling
//! chat`, and recent. The page refuses to start otherwise. That check never lets anything in on
//! its own.

use std::path::Path;
use std::path::PathBuf;

use rand::Rng;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use sha2::Digest;
use sha2::Sha256;

use crate::auth;
use crate::chat_repos;

/// How old a launch may be: Telegram signs `auth_date` when the menu button is pressed.
pub const MAX_LAUNCH_AGE_SECS: u64 = 24 * 60 * 60;

/// How long a pending launch-pair request waits for the bot's Approve before expiring (G2).
pub const LAUNCH_PAIR_TTL_SECS: u64 = 10 * 60;

/// At most three pending launch-pair requests exist at once; a fourth drops the oldest (G2).
pub const MAX_PENDING_LAUNCH_PAIRS: usize = 3;

/// The header in which the launching webview re-sends its `initData` when polling `GET /api/telegram/launch-pair`.
pub const INIT_DATA_HEADER: &str = "x-telegram-init-data";

const PENDING_PAIRS_FILE: &str = "telegram-pending-pairs.json";
const USED_LAUNCHES_FILE: &str = "telegram-used-launches.json";

/// HMAC-SHA-256 (RFC 2104), on the `sha2` crate this server already has.
pub fn hmac_sha256(key: &[u8], message: &[u8]) -> [u8; 32] {
    const BLOCK: usize = 64;
    let mut block = [0u8; BLOCK];
    if key.len() > BLOCK {
        block[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let mut inner = Sha256::new();
    inner.update(block.map(|b| b ^ 0x36));
    inner.update(message);
    let mut outer = Sha256::new();
    outer.update(block.map(|b| b ^ 0x5c));
    outer.update(inner.finalize());
    outer.finalize().into()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn percent_decode(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' => {
                let pair = text.get(i + 1..i + 3)?;
                out.push(u8::from_str_radix(pair, 16).ok()?);
                i += 3;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            byte => {
                out.push(byte);
                i += 1;
            }
        }
    }
    String::from_utf8(out).ok()
}

/// Why a launch was refused, in words the page shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refusal {
    /// `ling chat telegram setup` was not done here, so there is no bot to check against.
    NoBot,
    /// Not Telegram's launch parameters, or not signed by this node's bot.
    BadSignature,
    /// Signed, but older than [`MAX_LAUNCH_AGE_SECS`] (or from the future).
    Stale,
    /// Signed for a Telegram user who is not paired with `ling chat`.
    NotPaired,
}

impl Refusal {
    pub fn reason(self) -> &'static str {
        match self {
            Refusal::NoBot => "Telegram is not set up on this Mightling (`ling chat telegram setup`).",
            Refusal::BadSignature => "This page was not opened from this Mightling's bot.",
            Refusal::Stale => "This launch is too old: close the app and open it again from the bot.",
            Refusal::NotPaired => "Your Telegram account is not paired with this Mightling: send the bot /pair with a code from `ling chat telegram pair`.",
        }
    }
}

/// Checks Telegram's launch parameters (the query string Telegram puts in `tgWebAppData`), as
/// Telegram's Mini Apps documentation gives it: the fields but `hash`, sorted, `key=value` joined
/// by newlines, signed with HMAC-SHA-256 under the key HMAC-SHA-256("WebAppData", bot token).
/// Returns the Telegram user id.
pub fn check_launch(init_data: &str, bot_token: &str, paired: &[i64], now: u64) -> Result<i64, Refusal> {
    let mut fields = Vec::new();
    let mut hash = None;
    for pair in init_data.split('&').filter(|pair| !pair.is_empty()) {
        let (key, value) = pair.split_once('=').ok_or(Refusal::BadSignature)?;
        let key = percent_decode(key).ok_or(Refusal::BadSignature)?;
        let value = percent_decode(value).ok_or(Refusal::BadSignature)?;
        if key == "hash" {
            hash = Some(value);
        } else {
            fields.push((key, value));
        }
    }
    let hash = hash.ok_or(Refusal::BadSignature)?;
    fields.sort();
    let check_string = fields.iter().map(|(key, value)| format!("{key}={value}")).collect::<Vec<_>>().join("\n");
    let secret = hmac_sha256(b"WebAppData", bot_token.as_bytes());
    if !auth::same_secret(&hex(&hmac_sha256(&secret, check_string.as_bytes())), &hash.to_ascii_lowercase()) {
        return Err(Refusal::BadSignature);
    }
    let field = |name: &str| fields.iter().find(|(key, _)| key == name).map(|(_, value)| value.as_str());
    let signed_at: u64 = field("auth_date").and_then(|date| date.parse().ok()).ok_or(Refusal::BadSignature)?;
    if signed_at > now + 60 || now.saturating_sub(signed_at) > MAX_LAUNCH_AGE_SECS {
        return Err(Refusal::Stale);
    }
    let user: Value = field("user").and_then(|user| serde_json::from_str(user).ok()).ok_or(Refusal::BadSignature)?;
    let id = user.get("id").and_then(Value::as_i64).ok_or(Refusal::BadSignature)?;
    if paired.contains(&id) { Ok(id) } else { Err(Refusal::NotPaired) }
}

/// The bot token and paired users `ling chat` keeps in `<codex home>/chat/telegram.json`.
pub fn bot(codex_home: &Path) -> Option<(String, Vec<i64>)> {
    let text = std::fs::read_to_string(codex_home.join("chat").join("telegram.json")).ok()?;
    let value: Value = serde_json::from_str(&text).ok()?;
    let token = value.get("token")?.as_str()?.to_string();
    if token.is_empty() {
        return None;
    }
    let users = value.get("users").and_then(Value::as_array).map(|users| users.iter().filter_map(Value::as_i64).collect()).unwrap_or_default();
    Some((token, users))
}

/// `[chat] telegram_mini_app_url` from a configuration file's text, when it is a plain `https://`
/// address (Telegram opens nothing else): no user name or password, no fragment.
pub fn mini_app_url(text: &str) -> Option<String> {
    let document = toml::from_str::<toml::Table>(text).ok()?;
    let url = document.get("chat")?.as_table()?.get("telegram_mini_app_url")?.as_str()?.trim().to_string();
    valid_front_url(&url).then_some(url)
}

/// Whether `url` is a plain `https://` front address with no userinfo or fragment.
pub fn valid_front_url(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else {
        return false;
    };
    let authority = rest.split(['/', '?']).next().unwrap_or_default();
    !authority.is_empty()
        && !authority.contains('@')
        && !url.contains('#')
        && authority.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | ':' | '[' | ']'))
}

/// The `Host` a browser sends for the Mini App's address (`name`, or `name:port` off 443).
pub fn authority(url: &str) -> Option<String> {
    let rest = url.strip_prefix("https://")?;
    let authority = rest.split(['/', '?']).next()?.to_ascii_lowercase();
    Some(authority.strip_suffix(":443").map(str::to_string).unwrap_or(authority))
}

/// The `Host` a browser sends for the configured `[chat] telegram_mini_app_url` in `user_config`.
pub fn mini_app_host(user_config: &Path) -> Option<String> {
    let text = std::fs::read_to_string(user_config).ok()?;
    let url = mini_app_url(&text)?;
    authority(&url)
}

/// Whether `id` is a valid deep-link thread id (G3): 8 lowercase hex characters (a `chat-repos`
/// thread) or an app-server thread id (uuid-like: lowercase hex and dashes, at most 64 characters).
pub fn valid_open_thread_id(id: &str) -> bool {
    if id.is_empty() || id.len() > 64 || id.starts_with('-') || id.ends_with('-') || id.contains("--") {
        return false;
    }
    if id.len() == 8 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
        return true;
    }
    id.contains('-') && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f' | b'-'))
}

/// Where a Mini App deep link (`?open=…`) lands (CHAT_REPOS §10 G3).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DeepLink<'a> {
    /// `?open=repos` — the repository picker.
    Repos,
    /// `?open=repo:<org>/<repo>` — the repository picker with `<org>/<repo>` preselected.
    Repo(&'a str),
    /// `?open=thread:<id>` (and `&approval=1` when `approval` is true) — a thread in Work.
    Thread { id: &'a str, approval: bool },
}

/// Builds a deep-link URL from a configured front URL (`https://…`) and a [`DeepLink`] target.
/// Returns `None` when the front URL, repository name, or thread id is invalid.
pub fn deep_link_url(front_url: &str, target: DeepLink<'_>) -> Option<String> {
    let front = front_url.trim();
    if !valid_front_url(front) {
        return None;
    }
    let query = match target {
        DeepLink::Repos => "open=repos".to_string(),
        DeepLink::Repo(repo) => {
            if !chat_repos::valid_repo_name(repo) {
                return None;
            }
            format!("open=repo:{repo}")
        }
        DeepLink::Thread { id, approval } => {
            if !valid_open_thread_id(id) {
                return None;
            }
            if approval {
                format!("open=thread:{id}&approval=1")
            } else {
                format!("open=thread:{id}")
            }
        }
    };
    let without_query = front.split('?').next().unwrap_or(front);
    let rest = without_query.strip_prefix("https://")?;
    let base = if rest.contains('/') {
        without_query.to_string()
    } else {
        format!("{without_query}/")
    };
    Some(format!("{base}?{query}"))
}

/// Builds a deep-link URL directly from a configuration file's text (`[chat] telegram_mini_app_url`).
pub fn mini_app_deep_link(config_text: &str, target: DeepLink<'_>) -> Option<String> {
    let front = mini_app_url(config_text)?;
    deep_link_url(&front, target)
}

/// Convenience helper for `<front>/?open=repos`.
pub fn open_repos_url(front_url: &str) -> Option<String> {
    deep_link_url(front_url, DeepLink::Repos)
}

/// Convenience helper for `<front>/?open=repo:<org>/<repo>`.
pub fn open_repo_url(front_url: &str, repo: &str) -> Option<String> {
    deep_link_url(front_url, DeepLink::Repo(repo))
}

/// Convenience helper for `<front>/?open=thread:<id>` (with `&approval=1` when `approval` is true).
pub fn open_thread_url(front_url: &str, thread_id: &str, approval: bool) -> Option<String> {
    deep_link_url(front_url, DeepLink::Thread { id: thread_id, approval })
}

/// A pending first-launch device pairing request (CHAT_REPOS §10 G2).
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct PendingLaunchPair {
    pub id: String,
    pub code: String,
    #[serde(rename = "user", alias = "user_id")]
    pub user_id: i64,
    pub created_at: u64,
    pub expires_at: u64,
    pub init_data_sha256: String,
    #[serde(default)]
    pub approved: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct UsedLaunch {
    init_data_sha256: String,
    expires_at: u64,
}

/// What `GET /api/telegram/launch-pair?pending=<id>` answers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ClaimOutcome {
    /// Still waiting for the bot's Approve.
    Pending,
    /// Approved by the bot; hands out the device cookie token once.
    Approved(String),
    /// Unknown, expired, declined, or already collected.
    NotFound,
    /// The caller's `initData` does not match the webview that created this request.
    Forbidden,
}

fn pending_pairs_file(state: &Path) -> PathBuf {
    state.join(PENDING_PAIRS_FILE)
}

fn used_launches_file(state: &Path) -> PathBuf {
    state.join(USED_LAUNCHES_FILE)
}

fn random_six_digit_code() -> String {
    let number = rand::rng().random_range(0..1_000_000u32);
    format!("{:03} {:03}", number / 1_000, number % 1_000)
}

/// What one launch is remembered by: the digest of its signed `hash` field, as [`check_launch`]
/// reads it (the last `hash`, decoded, lowercased). The raw query string will not do: the same
/// signed fields re-ordered or re-encoded pass the signature check under a different string.
fn launch_digest(init_data: &str) -> String {
    let hash = init_data
        .split('&')
        .filter_map(|pair| pair.split_once('='))
        .filter(|(key, _)| percent_decode(key).as_deref() == Some("hash"))
        .filter_map(|(_, value)| percent_decode(value))
        .last();
    match hash {
        Some(hash) => auth::sha256_hex(&format!("hash:{}", hash.to_ascii_lowercase())),
        None => auth::sha256_hex(init_data),
    }
}

fn init_data_stale_at(init_data: &str, now: u64) -> u64 {
    let signed_at = init_data
        .split('&')
        .filter(|pair| !pair.is_empty())
        .find_map(|pair| {
            let (key, value) = pair.split_once('=')?;
            (percent_decode(key)? == "auth_date").then(|| percent_decode(value)?.parse::<u64>().ok())?
        })
        .unwrap_or(now);
    signed_at.saturating_add(MAX_LAUNCH_AGE_SECS)
}

fn load_pending_pairs_raw(state: &Path, now: u64) -> Vec<PendingLaunchPair> {
    let Ok(text) = std::fs::read_to_string(pending_pairs_file(state)) else {
        return Vec::new();
    };
    let Ok(entries) = serde_json::from_str::<Vec<PendingLaunchPair>>(&text) else {
        return Vec::new();
    };
    entries.into_iter().filter(|entry| now <= entry.expires_at).collect()
}

fn save_pending_pairs_raw(state: &Path, entries: &[PendingLaunchPair]) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(entries).map_err(std::io::Error::other)?;
    auth::write_private(&pending_pairs_file(state), &text)
}

fn load_used_launches_raw(state: &Path, now: u64) -> Vec<UsedLaunch> {
    let Ok(text) = std::fs::read_to_string(used_launches_file(state)) else {
        return Vec::new();
    };
    let Ok(entries) = serde_json::from_str::<Vec<UsedLaunch>>(&text) else {
        return Vec::new();
    };
    entries.into_iter().filter(|entry| now <= entry.expires_at).collect()
}

fn save_used_launches_raw(state: &Path, entries: &[UsedLaunch]) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(entries).map_err(std::io::Error::other)?;
    auth::write_private(&used_launches_file(state), &text)
}

fn record_used_init_data(state: &Path, digest: &str, expires_at: u64, now: u64) -> std::io::Result<()> {
    let mut used = load_used_launches_raw(state, now);
    if !used.iter().any(|entry| auth::same_secret(&entry.init_data_sha256, digest)) {
        used.push(UsedLaunch {
            init_data_sha256: digest.to_string(),
            expires_at,
        });
    }
    save_used_launches_raw(state, &used)
}

/// Creates a pending device request for `user_id` and `init_data`, expiring after 10 minutes,
/// keeping at most one pending request per Telegram user (a new launch replaces the old) and at
/// most [`MAX_PENDING_LAUNCH_PAIRS`] pending requests overall (oldest dropped). Used or claimed
/// `initData` digests are remembered until they would be stale (`auth_date + MAX_LAUNCH_AGE_SECS`)
/// so the same `initData` cannot pair twice.
pub fn create_pending_pair(state: &Path, user_id: i64, init_data: &str, now: u64) -> std::io::Result<PendingLaunchPair> {
    let digest = launch_digest(init_data);
    let used = load_used_launches_raw(state, now);
    if used.iter().any(|entry| auth::same_secret(&entry.init_data_sha256, &digest)) {
        let _ = save_used_launches_raw(state, &used);
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "This launch was already used to pair: close the app and open it again from the bot.",
        ));
    }
    let mut entries = load_pending_pairs_raw(state, now);
    if entries.iter().any(|entry| auth::same_secret(&entry.init_data_sha256, &digest)) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "This launch was already used to pair: close the app and open it again from the bot.",
        ));
    }
    entries.retain(|entry| entry.user_id != user_id);
    while entries.len() >= MAX_PENDING_LAUNCH_PAIRS {
        entries.remove(0);
    }
    let entry = PendingLaunchPair {
        id: auth::random_hex(8),
        code: random_six_digit_code(),
        user_id,
        created_at: now,
        expires_at: now + LAUNCH_PAIR_TTL_SECS,
        init_data_sha256: digest.clone(),
        approved: false,
    };
    record_used_init_data(state, &digest, init_data_stale_at(init_data, now), now)?;
    entries.push(entry.clone());
    save_pending_pairs_raw(state, &entries)?;
    Ok(entry)
}

/// Lists unapproved, non-expired pending device requests for the bot to prompt the user on.
pub fn list_pending_pairs(state: &Path, now: u64) -> Vec<PendingLaunchPair> {
    let entries = load_pending_pairs_raw(state, now);
    let _ = save_pending_pairs_raw(state, &entries);
    entries.into_iter().filter(|entry| !entry.approved).collect()
}

/// Marks a pending request approved by `id`. The `telegram-app` device credential is minted only
/// when the launching webview claims the request in [`claim_pending_pair`], so no plaintext token
/// is written to disk and an unclaimed approval expires with the request.
pub fn approve_pending_pair(state: &Path, id: &str, now: u64) -> std::io::Result<bool> {
    let mut entries = load_pending_pairs_raw(state, now);
    let Some(entry) = entries.iter_mut().find(|entry| entry.id == id && !entry.approved) else {
        let _ = save_pending_pairs_raw(state, &entries);
        return Ok(false);
    };
    entry.approved = true;
    save_pending_pairs_raw(state, &entries)?;
    Ok(true)
}

/// Declines (deletes) a pending request by `id`.
pub fn decline_pending_pair(state: &Path, id: &str, now: u64) -> std::io::Result<bool> {
    let mut entries = load_pending_pairs_raw(state, now);
    let before = entries.len();
    entries.retain(|entry| entry.id != id);
    let removed = entries.len() < before;
    save_pending_pairs_raw(state, &entries)?;
    Ok(removed)
}

/// Polls or collects a pending request for the launching webview. Once approved, removes the
/// request, mints a `Browser` device with `control` scope labelled `telegram-app`, and hands out
/// the device token exactly once.
pub fn claim_pending_pair(state: &Path, id: &str, user_id: i64, init_data: &str, now: u64) -> ClaimOutcome {
    let mut entries = load_pending_pairs_raw(state, now);
    let Some(index) = entries.iter().position(|entry| entry.id == id) else {
        let _ = save_pending_pairs_raw(state, &entries);
        return ClaimOutcome::NotFound;
    };
    let digest = launch_digest(init_data);
    if entries[index].user_id != user_id || !auth::same_secret(&digest, &entries[index].init_data_sha256) {
        return ClaimOutcome::Forbidden;
    }
    if !entries[index].approved {
        let _ = save_pending_pairs_raw(state, &entries);
        return ClaimOutcome::Pending;
    }
    entries.remove(index);
    let _ = save_pending_pairs_raw(state, &entries);
    let _ = record_used_init_data(state, &digest, init_data_stale_at(init_data, now), now);
    let Ok((_, token)) = auth::add_device(state, "telegram-app") else {
        return ClaimOutcome::NotFound;
    };
    ClaimOutcome::Approved(token)
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &str = "123456:test-token";

    fn sign(fields: &[(&str, &str)], token: &str) -> String {
        let mut sorted: Vec<(String, String)> = fields.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        sorted.sort();
        let check = sorted.iter().map(|(k, v)| format!("{k}={v}")).collect::<Vec<_>>().join("\n");
        let hash = hex(&hmac_sha256(&hmac_sha256(b"WebAppData", token.as_bytes()), check.as_bytes()));
        let encode = |text: &str| text.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect::<String>();
        let mut pairs: Vec<String> = fields.iter().map(|(k, v)| format!("{}={}", encode(k), encode(v))).collect();
        pairs.push(format!("hash={hash}"));
        pairs.join("&")
    }

    #[test]
    fn hmac_matches_rfc_4231() {
        // RFC 4231 test case 2.
        let mac = hmac_sha256(b"Jefe", b"what do ya want for nothing?");
        assert_eq!(hex(&mac), "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
        // Test case 6: a key longer than a block is hashed first.
        let mac = hmac_sha256(&[0xaa; 131], b"Test Using Larger Than Block-Size Key - Hash Key First");
        assert_eq!(hex(&mac), "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");
    }

    #[test]
    fn a_signed_recent_launch_of_a_paired_user_passes() {
        let data = sign(&[("auth_date", "1000"), ("query_id", "AAH"), ("user", r#"{"id":42,"first_name":"A"}"#)], TOKEN);
        assert_eq!(check_launch(&data, TOKEN, &[42], 1000 + 3600), Ok(42));
    }

    #[test]
    fn anything_else_is_refused_and_says_why() {
        let fields = [("auth_date", "1000"), ("user", r#"{"id":42}"#)];
        let data = sign(&fields, TOKEN);
        assert_eq!(check_launch(&data, TOKEN, &[7], 1000), Err(Refusal::NotPaired));
        assert_eq!(check_launch(&data, "123456:other", &[42], 1000), Err(Refusal::BadSignature));
        assert_eq!(check_launch(&data, TOKEN, &[42], 1000 + MAX_LAUNCH_AGE_SECS + 1), Err(Refusal::Stale));
        assert_eq!(check_launch(&data, TOKEN, &[42], 100), Err(Refusal::Stale));
        let tampered = data.replace("%22id%22%3A42", "%22id%22%3A43");
        assert_ne!(tampered, data);
        assert_eq!(check_launch(&tampered, TOKEN, &[42, 43], 1000), Err(Refusal::BadSignature));
        let unsigned = data.split("&hash=").next().unwrap_or_default();
        assert_eq!(check_launch(unsigned, TOKEN, &[42], 1000), Err(Refusal::BadSignature));
        assert_eq!(check_launch("", TOKEN, &[42], 1000), Err(Refusal::BadSignature));
        assert_eq!(check_launch("%zz=1&hash=00", TOKEN, &[42], 1000), Err(Refusal::BadSignature));
    }

    #[test]
    fn the_address_must_be_plain_https() {
        let url = |value: &str| mini_app_url(&format!("[chat]\ntelegram_mini_app_url = \"{value}\"\n"));
        assert_eq!(url("https://node.example.ts.net/"), Some("https://node.example.ts.net/".to_string()));
        assert_eq!(url("https://node.example.ts.net:8443/"), Some("https://node.example.ts.net:8443/".to_string()));
        assert_eq!(url("http://node.example.ts.net/"), None);
        assert_eq!(url("https://user:pw@node.example.ts.net/"), None);
        assert_eq!(url("https://node.example.ts.net/#x"), None);
        assert_eq!(url("https:///"), None);
        assert_eq!(mini_app_url(""), None);
        assert_eq!(mini_app_url("[chat]\ntelegram_mini_app_url = 3\n"), None);
    }

    #[test]
    fn the_host_a_browser_sends_drops_the_default_port() {
        assert_eq!(authority("https://Node.example.ts.net/").as_deref(), Some("node.example.ts.net"));
        assert_eq!(authority("https://node.example.ts.net:443/x").as_deref(), Some("node.example.ts.net"));
        assert_eq!(authority("https://node.example.ts.net:8443").as_deref(), Some("node.example.ts.net:8443"));
    }

    #[test]
    fn the_bot_comes_from_ling_chats_file() {
        let home = std::env::temp_dir().join(format!("ling-web-tg-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        assert_eq!(bot(&home), None);
        std::fs::create_dir_all(home.join("chat")).unwrap_or_default();
        std::fs::write(home.join("chat").join("telegram.json"), r#"{"token":"1:a","consented":1,"users":[42]}"#).unwrap_or_default();
        assert_eq!(bot(&home), Some(("1:a".to_string(), vec![42])));
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn deep_links_build_valid_query_addresses_and_refuse_bad_targets() {
        let front = "https://node.example.ts.net/";
        let config = "[chat]\ntelegram_mini_app_url = \"https://node.example.ts.net\"\n";
        assert_eq!(open_repos_url(front).as_deref(), Some("https://node.example.ts.net/?open=repos"));
        assert_eq!(mini_app_deep_link(config, DeepLink::Repos).as_deref(), Some("https://node.example.ts.net/?open=repos"));
        assert_eq!(open_repo_url(front, "dreamference/mightling").as_deref(), Some("https://node.example.ts.net/?open=repo:dreamference/mightling"));
        assert_eq!(open_thread_url(front, "3f2a9c1e", false).as_deref(), Some("https://node.example.ts.net/?open=thread:3f2a9c1e"));
        assert_eq!(
            open_thread_url(front, "01a0f1ae-2da4-7942-99ae-c3151dc137cc", true).as_deref(),
            Some("https://node.example.ts.net/?open=thread:01a0f1ae-2da4-7942-99ae-c3151dc137cc&approval=1")
        );
        // Invalid front, repository names, and thread ids are refused.
        assert_eq!(open_repos_url("http://node.example.ts.net/"), None);
        for bad_repo in ["", "onlyone", "a/b/c", "../secret", "org/..", "https://a/b", "org/repo with space"] {
            assert_eq!(open_repo_url(front, bad_repo), None, "{bad_repo}");
        }
        for bad_thread in ["", "3f2a9c1", "3f2a9c1ee", "3F2A9C1E", "../etc", "-abc-def", "abc-def-", "abc--def", "thr_1", &"a".repeat(65)] {
            assert_eq!(open_thread_url(front, bad_thread, false), None, "{bad_thread}");
        }
    }

    #[test]
    fn launch_pairing_keeps_three_expires_in_ten_minutes_and_hands_the_cookie_out_once() {
        let state = std::env::temp_dir().join(format!("ling-web-tg-pair-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&state);
        auth::private_dir(&state).unwrap();

        let p1 = create_pending_pair(&state, 41, "init-1", 1000).unwrap();
        let p2 = create_pending_pair(&state, 42, "init-2", 1001).unwrap();
        let p3 = create_pending_pair(&state, 43, "init-3", 1002).unwrap();
        let p4 = create_pending_pair(&state, 44, "init-4", 1003).unwrap();
        assert_eq!(p1.code.len(), 7);
        assert_eq!(p1.code.as_bytes()[3], b' ');
        // Oldest dropped once more than 3 are pending.
        let ids: Vec<String> = list_pending_pairs(&state, 1003).into_iter().map(|p| p.id).collect();
        assert_eq!(ids, vec![p2.id.clone(), p3.id.clone(), p4.id.clone()]);

        // Not approved yet: polling answers Pending, and another webview's initData is Forbidden.
        assert_eq!(claim_pending_pair(&state, &p2.id, 42, "init-2", 1005), ClaimOutcome::Pending);
        assert_eq!(claim_pending_pair(&state, &p2.id, 42, "init-other", 1005), ClaimOutcome::Forbidden);
        assert_eq!(claim_pending_pair(&state, &p2.id, 99, "init-2", 1005), ClaimOutcome::Forbidden);

        // Decline deletes the request.
        assert!(decline_pending_pair(&state, &p3.id, 1005).unwrap());
        assert_eq!(claim_pending_pair(&state, &p3.id, 43, "init-3", 1005), ClaimOutcome::NotFound);

        // Expiry after 10 minutes drops the request.
        assert!(list_pending_pairs(&state, 1003 + LAUNCH_PAIR_TTL_SECS + 1).is_empty());

        // Approve sets the flag; claiming mints a Browser device with control scope labelled telegram-app once.
        let p5 = create_pending_pair(&state, 42, "init-5", 2000).unwrap();
        assert!(approve_pending_pair(&state, &p5.id, 2010).unwrap());
        assert!(auth::load_devices(&state).is_empty(), "no device minted before claim");
        assert!(list_pending_pairs(&state, 2010).is_empty(), "approved request is no longer pending");
        let ClaimOutcome::Approved(token) = claim_pending_pair(&state, &p5.id, 42, "init-5", 2011) else {
            panic!("expected Approved");
        };
        let devices = auth::load_devices(&state);
        assert_eq!(devices.len(), 1);
        assert_eq!(devices[0].name, "telegram-app");
        assert_eq!(devices[0].kind, auth::DeviceKind::Browser);
        assert_eq!(devices[0].scope, auth::Scope::Control);
        assert!(auth::device_for_token(&state, &token).is_some());
        assert_eq!(claim_pending_pair(&state, &p5.id, 42, "init-5", 2012), ClaimOutcome::NotFound);
        assert!(!auth::revoke_device(&state, "telegram-app").unwrap().is_empty());
        assert!(auth::device_for_token(&state, &token).is_none());

        let _ = std::fs::remove_dir_all(&state);
    }

    #[test]
    fn approved_pair_stores_only_the_approved_flag_and_leaves_no_device_when_unclaimed() {
        let state = std::env::temp_dir().join(format!("ling-web-tg-unclaimed-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&state);
        auth::private_dir(&state).unwrap();

        let pending = create_pending_pair(&state, 42, "init-unclaimed", 1000).unwrap();
        assert!(approve_pending_pair(&state, &pending.id, 1005).unwrap());
        let on_disk = std::fs::read_to_string(state.join(PENDING_PAIRS_FILE)).unwrap();
        assert!(on_disk.contains("\"approved\": true"), "{on_disk}");
        assert!(!on_disk.contains("approved_token"), "{on_disk}");
        assert!(auth::load_devices(&state).is_empty(), "device must not exist before claim");

        // Unclaimed approval expires with the request after 10 minutes, leaving no device.
        let expired_now = 1000 + LAUNCH_PAIR_TTL_SECS + 1;
        assert_eq!(
            claim_pending_pair(&state, &pending.id, 42, "init-unclaimed", expired_now),
            ClaimOutcome::NotFound
        );
        assert!(auth::load_devices(&state).is_empty(), "expired unclaimed approval must leave no device");

        let _ = std::fs::remove_dir_all(&state);
    }

    #[test]
    fn one_pending_pair_per_user_replaces_old_and_used_init_data_cannot_pair_twice_until_stale() {
        let state = std::env::temp_dir().join(format!("ling-web-tg-dedup-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&state);
        auth::private_dir(&state).unwrap();

        let init1 = "auth_date=1000&user=%7B%22id%22%3A42%7D&hash=aa";
        let init2 = "auth_date=1010&user=%7B%22id%22%3A42%7D&hash=bb";

        let first = create_pending_pair(&state, 42, init1, 1005).unwrap();
        // Same initData cannot be used a second time while pending.
        assert!(create_pending_pair(&state, 42, init1, 1006).is_err());

        // A new launch from the same user replaces the old pending request.
        let second = create_pending_pair(&state, 42, init2, 1012).unwrap();
        assert_ne!(first.id, second.id);
        let open = list_pending_pairs(&state, 1012);
        assert_eq!(open.len(), 1);
        assert_eq!(open[0].id, second.id);
        assert_eq!(claim_pending_pair(&state, &first.id, 42, init1, 1012), ClaimOutcome::NotFound);

        // Claiming the new request succeeds once, and neither init1 nor init2 can pair again.
        assert!(approve_pending_pair(&state, &second.id, 1015).unwrap());
        assert!(matches!(
            claim_pending_pair(&state, &second.id, 42, init2, 1016),
            ClaimOutcome::Approved(_)
        ));
        assert!(create_pending_pair(&state, 42, init1, 1020).is_err());
        assert!(create_pending_pair(&state, 42, init2, 1020).is_err());

        // Once past auth_date + MAX_LAUNCH_AGE_SECS (when check_launch refuses it as Stale anyway),
        // the remembered digest is pruned.
        let after_stale = 1000 + MAX_LAUNCH_AGE_SECS + 1;
        assert!(create_pending_pair(&state, 42, init1, after_stale).is_ok());

        let _ = std::fs::remove_dir_all(&state);
    }

    #[test]
    fn a_reordered_or_reencoded_copy_of_a_used_launch_cannot_pair_again() {
        let state = std::env::temp_dir().join(format!("ling-web-tg-reencode-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&state);
        auth::private_dir(&state).unwrap();

        let data = sign(&[("auth_date", "1000"), ("query_id", "AAH"), ("user", r#"{"id":42}"#)], TOKEN);
        // The same signed fields, re-ordered, with one byte percent-encoded and the hash in capitals:
        // a different string that check_launch still accepts.
        let mut pairs: Vec<&str> = data.split('&').collect();
        pairs.reverse();
        let hash = pairs[0].trim_start_matches("hash=").to_ascii_uppercase();
        let copy = format!("hash={hash}&{}", pairs[1..].join("&").replacen("query_id=AAH", "query%5Fid=AAH", 1));
        assert_ne!(copy, data);
        assert_eq!(check_launch(&copy, TOKEN, &[42], 1001), Ok(42));

        let first = create_pending_pair(&state, 42, &data, 1001).unwrap();
        assert!(create_pending_pair(&state, 42, &copy, 1002).is_err(), "a re-encoded copy pairs again while pending");
        // The webview that launched with the copy is the same launch: it may collect the request.
        assert_eq!(claim_pending_pair(&state, &first.id, 42, &copy, 1003), ClaimOutcome::Pending);
        assert!(approve_pending_pair(&state, &first.id, 1004).unwrap());
        assert!(matches!(claim_pending_pair(&state, &first.id, 42, &data, 1005), ClaimOutcome::Approved(_)));
        assert!(create_pending_pair(&state, 42, &copy, 1006).is_err(), "a re-encoded copy pairs again once used");

        let _ = std::fs::remove_dir_all(&state);
    }
}
