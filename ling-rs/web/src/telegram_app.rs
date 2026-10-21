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

use serde_json::Value;
use sha2::Digest;
use sha2::Sha256;

use crate::auth;

/// How old a launch may be: Telegram signs `auth_date` when the menu button is pressed.
pub const MAX_LAUNCH_AGE_SECS: u64 = 24 * 60 * 60;

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
    let rest = url.strip_prefix("https://")?;
    let authority = rest.split(['/', '?']).next().unwrap_or_default();
    let valid = !authority.is_empty()
        && !authority.contains('@')
        && !url.contains('#')
        && authority.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | ':' | '[' | ']'));
    valid.then_some(url)
}

/// The `Host` a browser sends for the Mini App's address (`name`, or `name:port` off 443).
pub fn authority(url: &str) -> Option<String> {
    let rest = url.strip_prefix("https://")?;
    let authority = rest.split(['/', '?']).next()?.to_ascii_lowercase();
    Some(authority.strip_suffix(":443").map(str::to_string).unwrap_or(authority))
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
}
