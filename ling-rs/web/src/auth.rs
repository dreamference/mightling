//! Who may use `ling web` (specs/DREAMFERENCE_MIGHTLING_ASK.md §4.3): a credential on every request,
//! loopback included, because at `/airgapped off` a command the agent runs can reach loopback too.
//!
//! - **This machine:** `ling web open` writes a one-time login code into the state folder and opens
//!   the browser on `/login?code=…`; the server trades it for a session cookie and deletes it. The
//!   owner token (`token`, 0600) works as `Authorization: Bearer …` on `/healthz` alone, for
//!   `ling web status`.
//! - **What a sandboxed command can read is no credential.** Codex's sandbox limits writes, not
//!   reads, so a command the agent runs can read everything here. The token therefore opens
//!   nothing but the health check, pending codes are kept under their SHA-256 (a listing shows no
//!   code), and devices are kept as hashes. Signing in needs a code, and writing one needs the
//!   user's own shell: the sandbox cannot write this folder.
//! - **Another device:** `ling web pair` writes an 8-digit code valid for ten minutes, for one
//!   device. The device enters it on `/pair` and gets a long-lived device cookie; only its hash is
//!   kept, in `devices.json`. `ling web revoke <device>` ends it.
//! - **Every request** carries one of those. The `Host` header must be an address this server
//!   serves (DNS rebinding), and a WebSocket upgrade or a POST must also carry an `Origin` that is
//!   one of the server's own.
//!
//! Everything here is plain files and functions, so it is tested without a server.

use std::collections::BTreeSet;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use rand::Rng;
use serde_json::Value;
use serde_json::json;
use sha2::Digest;
use sha2::Sha256;

/// How long a pairing code is good for.
pub const PAIRING_LIFETIME: Duration = Duration::from_secs(10 * 60);
/// How long a one-time login code from `ling web open` is good for.
pub const LOGIN_LIFETIME: Duration = Duration::from_secs(2 * 60);
/// Wrong pairing codes tolerated before every pending code is withdrawn.
pub const PAIRING_ATTEMPTS: u32 = 10;
/// The cookie a browser on this machine holds after `ling web open`.
pub const SESSION_COOKIE: &str = "mightling_session";
/// The cookie a paired device holds.
pub const DEVICE_COOKIE: &str = "mightling_device";

/// The state folder: `<home>/web`, private to the user.
pub fn state_dir(codex_home: &Path) -> PathBuf {
    codex_home.join("web")
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// Random bytes as lowercase hex.
pub fn random_hex(bytes: usize) -> String {
    let mut buffer = vec![0u8; bytes];
    rand::rng().fill(buffer.as_mut_slice());
    buffer.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn sha256_hex(text: &str) -> String {
    Sha256::digest(text.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

/// Compares two secrets in time that does not depend on where they differ.
pub fn same_secret(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Creates a folder private to the user (0700), parents included.
pub fn private_dir(path: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

/// Writes a file readable only by the user, replacing it whole.
pub fn write_private(path: &Path, text: &str) -> std::io::Result<()> {
    let staging = stage_private(path, text)?;
    std::fs::rename(&staging, path)
}

/// `text` in a private file beside `path`, under a name unique to this call: two threads of one
/// server may write the same file at once.
fn stage_private(path: &Path, text: &str) -> std::io::Result<PathBuf> {
    if let Some(parent) = path.parent() {
        private_dir(parent)?;
    }
    let staging = path.with_extension(format!("tmp-{}-{}", std::process::id(), random_hex(4)));
    #[cfg(unix)]
    use std::os::unix::fs::OpenOptionsExt;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    options.mode(0o600);
    use std::io::Write;
    let mut file = options.open(&staging)?;
    file.write_all(text.as_bytes())?;
    file.sync_all()?;
    Ok(staging)
}

/// The owner token, created on first use. Callers creating it at once all get the first one
/// published: it is linked into place, which fails when another caller's is already there.
pub fn owner_token(state: &Path) -> std::io::Result<String> {
    let path = state.join("token");
    if let Ok(text) = std::fs::read_to_string(&path)
        && !text.trim().is_empty()
    {
        return Ok(text.trim().to_string());
    }
    let token = random_hex(32);
    let staging = stage_private(&path, &token)?;
    let linked = std::fs::hard_link(&staging, &path);
    match linked {
        Ok(()) => {
            let _ = std::fs::remove_file(&staging);
            Ok(token)
        }
        Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {
            match std::fs::read_to_string(&path) {
                Ok(text) if !text.trim().is_empty() => {
                    let _ = std::fs::remove_file(&staging);
                    Ok(text.trim().to_string())
                }
                // An empty file (an earlier write cut short) is replaced.
                _ => std::fs::rename(&staging, &path).map(|()| token),
            }
        }
        Err(err) => {
            let _ = std::fs::remove_file(&staging);
            Err(err)
        }
    }
}

/// A code waiting in `logins/` or `pairing/`, kept under its hash, with when it stops being good.
fn issue_code(dir: &Path, code: &str, lifetime: Duration) -> std::io::Result<()> {
    write_private(&dir.join(sha256_hex(code)), &json!({ "expires": now_secs() + lifetime.as_secs() }).to_string())
}

/// Takes a code: true when it existed and had not expired. It is deleted either way (one use).
fn take_code(dir: &Path, code: &str) -> bool {
    if code.is_empty() || !code.chars().all(|c| c.is_ascii_alphanumeric()) {
        return false;
    }
    let path = dir.join(sha256_hex(code));
    let Ok(text) = std::fs::read_to_string(&path) else { return false };
    // Removing the file is what takes the code: of requests that read it at once, one removes it.
    if std::fs::remove_file(&path).is_err() {
        return false;
    }
    serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|value| value.get("expires").and_then(Value::as_u64))
        .is_some_and(|expires| now_secs() <= expires)
}

/// `ling web open`: a fresh one-time login code.
pub fn issue_login_code(state: &Path) -> std::io::Result<String> {
    let code = random_hex(16);
    issue_code(&state.join("logins"), &code, LOGIN_LIFETIME)?;
    Ok(code)
}

pub fn take_login_code(state: &Path, code: &str) -> bool {
    take_code(&state.join("logins"), code)
}

/// `ling web pair`: a fresh 8-digit pairing code.
pub fn issue_pairing_code(state: &Path) -> std::io::Result<String> {
    let code = format!("{:08}", rand::rng().random_range(0..100_000_000u32));
    issue_code(&state.join("pairing"), &code, PAIRING_LIFETIME)?;
    Ok(code)
}

pub fn take_pairing_code(state: &Path, code: &str) -> bool {
    code.len() == 8 && code.chars().all(|c| c.is_ascii_digit()) && take_code(&state.join("pairing"), code)
}

/// Withdraws every pending pairing code: what too many wrong guesses lead to.
pub fn withdraw_pairing_codes(state: &Path) {
    let _ = std::fs::remove_dir_all(state.join("pairing"));
}

/// What a paired device is: a browser (its token is a cookie that opens the UI), or a sync agent
/// (`ling sync enable` on another machine, specs/DREAMFERENCE_MIGHTLING_SYNC.md §5.1), whose token
/// is a bearer secret that opens `/api/sync/` and nothing else.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeviceKind {
    Browser,
    Sync,
}

impl DeviceKind {
    pub fn name(self) -> &'static str {
        match self {
            DeviceKind::Browser => "browser",
            DeviceKind::Sync => "sync",
        }
    }
}

/// What a paired device may do with other devices' threads (SYNC §5.1, the owner's answer O4):
/// `control` is every new pairing's, phones included; `watch` reads and follows only. A device may
/// always publish its own threads, whatever its scope.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scope {
    Control,
    Watch,
}

impl Scope {
    pub fn name(self) -> &'static str {
        match self {
            Scope::Control => "control",
            Scope::Watch => "watch",
        }
    }

    pub fn parse(text: &str) -> Option<Scope> {
        match text {
            "control" => Some(Scope::Control),
            "watch" => Some(Scope::Watch),
            _ => None,
        }
    }
}

/// One paired device, as `devices.json` keeps it: never the token itself. A file written before
/// sync existed has neither `kind` nor `scope`: its devices are browsers with `control`.
#[derive(Clone, Debug, PartialEq)]
pub struct Device {
    pub id: String,
    pub name: String,
    pub token_sha256: String,
    pub created: u64,
    pub last_used: u64,
    pub kind: DeviceKind,
    pub scope: Scope,
}

fn devices_path(state: &Path) -> PathBuf {
    state.join("devices.json")
}

pub fn load_devices(state: &Path) -> Vec<Device> {
    let Ok(text) = std::fs::read_to_string(devices_path(state)) else { return Vec::new() };
    let Ok(Value::Array(items)) = serde_json::from_str::<Value>(&text) else { return Vec::new() };
    items
        .iter()
        .filter_map(|item| {
            Some(Device {
                id: item.get("id")?.as_str()?.to_string(),
                name: item.get("name")?.as_str()?.to_string(),
                token_sha256: item.get("token_sha256")?.as_str()?.to_string(),
                created: item.get("created")?.as_u64()?,
                last_used: item.get("last_used")?.as_u64()?,
                kind: match item.get("kind").and_then(Value::as_str) {
                    Some("sync") => DeviceKind::Sync,
                    _ => DeviceKind::Browser,
                },
                scope: item.get("scope").and_then(Value::as_str).and_then(Scope::parse).unwrap_or(Scope::Control),
            })
        })
        .collect()
}

fn save_devices(state: &Path, devices: &[Device]) -> std::io::Result<()> {
    let items: Vec<Value> = devices
        .iter()
        .map(|d| {
            json!({"id": d.id, "name": d.name, "token_sha256": d.token_sha256, "created": d.created, "last_used": d.last_used,
                "kind": d.kind.name(), "scope": d.scope.name()})
        })
        .collect();
    write_private(&devices_path(state), &serde_json::to_string_pretty(&items).unwrap_or_default())
}

/// Changes `devices.json` under its lock (`devices.lock`), read and written whole while it is held:
/// the server's requests and `ling web revoke` in another process change the one file, and a change
/// made between another's read and its write would otherwise be lost (a pairing dropped, a revoked
/// device back). `change` says whether it changed anything to write.
fn change_devices<T>(state: &Path, change: impl FnOnce(&mut Vec<Device>) -> (T, bool)) -> std::io::Result<T> {
    private_dir(state)?;
    let lock = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(state.join("devices.lock"))?;
    lock.lock()?;
    let mut devices = load_devices(state);
    let (result, changed) = change(&mut devices);
    if changed {
        save_devices(state, &devices)?;
    }
    Ok(result)
}

/// A name a person typed, made safe to keep and show: printable, at most 64 characters.
pub fn clean_device_name(name: &str) -> String {
    let cleaned: String = name.chars().filter(|c| !c.is_control()).take(64).collect();
    let cleaned = cleaned.trim().to_string();
    if cleaned.is_empty() { "unnamed device".to_string() } else { cleaned }
}

/// Pairs a browser: returns its id and the token its cookie carries.
pub fn add_device(state: &Path, name: &str) -> std::io::Result<(String, String)> {
    add_device_of(state, name, DeviceKind::Browser)
}

/// Pairs a device of either kind, with `control` (O4): returns its id and its token, 256 bits.
pub fn add_device_of(state: &Path, name: &str, kind: DeviceKind) -> std::io::Result<(String, String)> {
    let token = random_hex(32);
    let id = random_hex(4);
    change_devices(state, |devices| {
        devices.push(Device {
            id: id.clone(),
            name: clean_device_name(name),
            token_sha256: sha256_hex(&token),
            created: now_secs(),
            last_used: now_secs(),
            kind,
            scope: Scope::Control,
        });
        ((), true)
    })?;
    Ok((id, token))
}

/// `ling web devices --scope <device> watch|control`: by id or exact name. Returns the devices changed.
pub fn set_scope(state: &Path, which: &str, scope: Scope) -> std::io::Result<Vec<Device>> {
    change_devices(state, |devices| {
        let mut changed = Vec::new();
        for device in devices.iter_mut().filter(|d| d.id == which || d.name == which) {
            device.scope = scope;
            changed.push(device.clone());
        }
        let any = !changed.is_empty();
        (changed, any)
    })
}

/// The browser a cookie's token belongs to. A sync agent's secret is no cookie: it opens only
/// `/api/sync/` ([`sync_device_for_token`]).
pub fn device_for_token(state: &Path, token: &str) -> Option<Device> {
    device_of_kind(state, token, DeviceKind::Browser)
}

/// The sync agent a bearer secret belongs to.
pub fn sync_device_for_token(state: &Path, token: &str) -> Option<Device> {
    device_of_kind(state, token, DeviceKind::Sync)
}

/// The device of `kind` a token belongs to, its last use brought up to date (at most once a minute).
fn device_of_kind(state: &Path, token: &str, kind: DeviceKind) -> Option<Device> {
    let hash = sha256_hex(token);
    let mut device = load_devices(state).into_iter().find(|d| d.kind == kind && same_secret(&d.token_sha256, &hash))?;
    let now = now_secs();
    if now.saturating_sub(device.last_used) >= 60 {
        device.last_used = now;
        // Read again under the lock: a device revoked meanwhile is not written back.
        let _ = change_devices(state, |devices| match devices.iter_mut().find(|d| d.id == device.id && d.token_sha256 == device.token_sha256) {
            Some(kept) => {
                kept.last_used = now;
                ((), true)
            }
            None => ((), false),
        });
    }
    Some(device)
}

/// `ling web revoke <device>`: by id or by exact name. Returns the devices removed.
pub fn revoke_device(state: &Path, which: &str) -> std::io::Result<Vec<Device>> {
    revoke_devices_where(state, |d| d.id == which || d.name == which)
}

/// Revokes the one device with id `id`; a device merely named so stays.
pub fn revoke_device_by_id(state: &Path, id: &str) -> std::io::Result<Vec<Device>> {
    revoke_devices_where(state, |d| d.id == id)
}

fn revoke_devices_where(
    state: &Path,
    matches: impl Fn(&Device) -> bool,
) -> std::io::Result<Vec<Device>> {
    change_devices(state, |devices| {
        let (gone, kept): (Vec<Device>, Vec<Device>) =
            std::mem::take(devices).into_iter().partition(matches);
        *devices = kept;
        let any = !gone.is_empty();
        (gone, any)
    })
}

/// The `host:port` values a request may name in `Host`, and the origins it may come from.
#[derive(Clone, Debug)]
pub struct Addresses {
    pub hosts: BTreeSet<String>,
}

impl Addresses {
    /// Loopback names always; on the LAN, also every given address and name.
    pub fn new(port: u16, lan_names: &[String]) -> Addresses {
        let mut hosts: BTreeSet<String> =
            ["127.0.0.1", "localhost", "[::1]"].iter().map(|name| format!("{name}:{port}")).collect();
        for name in lan_names {
            let name = if name.contains(':') && !name.starts_with('[') { format!("[{name}]") } else { name.clone() };
            hosts.insert(format!("{}:{port}", name.to_ascii_lowercase()));
        }
        Addresses { hosts }
    }

    /// Also the names an HTTPS front on the user's overlay forwards here, as browsers send them
    /// (no port appended: the front listens on its own).
    pub fn with_public(mut self, names: &[String]) -> Addresses {
        self.hosts.extend(names.iter().map(|name| name.trim().to_ascii_lowercase()).filter(|name| !name.is_empty()));
        self
    }

    /// Whether a `Host` header names this server.
    pub fn host_ok(&self, host: Option<&str>) -> bool {
        host.is_some_and(|host| self.hosts.contains(&host.trim().to_ascii_lowercase()))
    }

    /// Whether an `Origin` header is one of this server's own.
    pub fn origin_ok(&self, origin: Option<&str>) -> bool {
        let Some(origin) = origin else { return false };
        let origin = origin.trim().to_ascii_lowercase();
        origin.strip_prefix("http://").or_else(|| origin.strip_prefix("https://")).is_some_and(|host| self.hosts.contains(host))
    }
}

/// The value of one cookie in a `Cookie` header.
pub fn cookie<'a>(header: Option<&'a str>, name: &str) -> Option<&'a str> {
    cookies(header, name).next()
}

/// Every value a `Cookie` header carries for `name`. Cookies ignore the port, so another service
/// on this host can set one of ours ahead of the real one: a check tries each value, not the first.
pub fn cookies<'a>(header: Option<&'a str>, name: &str) -> impl Iterator<Item = &'a str> {
    header.unwrap_or("").split(';').map(str::trim).filter_map(move |pair| {
        let (key, value) = pair.split_once('=')?;
        (key.trim() == name).then_some(value.trim())
    })
}

/// The paired device whose token one of the request's device cookies carries.
pub fn device_for_cookie(state: &Path, header: Option<&str>) -> Option<Device> {
    device_cookie(state, header).map(|(_, device)| device)
}

/// The first of the request's device cookies that is a paired device's token, with that device.
pub fn device_cookie<'a>(state: &Path, header: Option<&'a str>) -> Option<(&'a str, Device)> {
    cookies(header, DEVICE_COOKIE).find_map(|token| Some((token, device_for_token(state, token)?)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-web-auth-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[cfg(unix)]
    fn mode(path: &Path) -> u32 {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(path).unwrap().permissions().mode() & 0o777
    }

    #[test]
    fn the_owner_token_is_private_and_stable() {
        let state = scratch("token");
        let first = owner_token(&state).unwrap();
        assert_eq!(first.len(), 64);
        assert_eq!(owner_token(&state).unwrap(), first);
        #[cfg(unix)]
        {
            assert_eq!(mode(&state.join("token")), 0o600);
            assert_eq!(mode(&state), 0o700);
        }
    }

    /// Processes starting at once (the server, `ling web open`, a bridge) all get the one token.
    #[test]
    fn callers_creating_the_owner_token_at_once_get_the_same_one() {
        for round in 0..20 {
            let state = scratch(&format!("token-race-{round}"));
            let start = std::sync::Arc::new(std::sync::Barrier::new(8));
            let callers: Vec<_> = (0..8)
                .map(|_| {
                    let (state, start) = (state.clone(), start.clone());
                    std::thread::spawn(move || {
                        start.wait();
                        owner_token(&state).unwrap()
                    })
                })
                .collect();
            let tokens: BTreeSet<String> = callers
                .into_iter()
                .map(|caller| caller.join().unwrap())
                .collect();
            assert_eq!(tokens.len(), 1, "round {round}: {tokens:?}");
            assert_eq!(
                std::fs::read_to_string(state.join("token")).unwrap().trim(),
                tokens.first().unwrap()
            );
            let _ = std::fs::remove_dir_all(&state);
        }
    }

    #[test]
    fn a_login_code_works_once() {
        let state = scratch("login");
        let code = issue_login_code(&state).unwrap();
        assert!(take_login_code(&state, &code));
        assert!(!take_login_code(&state, &code));
        assert!(!take_login_code(&state, "../token"));
        assert!(!take_login_code(&state, ""));
    }

    #[test]
    fn an_expired_code_is_refused_and_deleted() {
        let state = scratch("expired");
        let dir = state.join("pairing");
        let file = dir.join(sha256_hex("12345678"));
        write_private(&file, &json!({"expires": now_secs() - 1}).to_string()).unwrap();
        assert!(!take_pairing_code(&state, "12345678"));
        assert!(!file.exists());
    }

    #[test]
    fn a_listing_of_the_pending_codes_shows_no_code() {
        // A sandboxed command can read this folder; it must not find a code it could use.
        let state = scratch("listing");
        let login = issue_login_code(&state).unwrap();
        let pairing = issue_pairing_code(&state).unwrap();
        for dir in ["logins", "pairing"] {
            for entry in std::fs::read_dir(state.join(dir)).unwrap() {
                let name = entry.unwrap().file_name().to_string_lossy().into_owned();
                assert!(name != login && name != pairing && !name.contains(&pairing), "{name}");
            }
        }
        assert!(take_login_code(&state, &login) && take_pairing_code(&state, &pairing));
    }

    #[test]
    fn pairing_codes_are_eight_digits_and_one_use() {
        let state = scratch("pair");
        let code = issue_pairing_code(&state).unwrap();
        assert_eq!(code.len(), 8);
        assert!(code.chars().all(|c| c.is_ascii_digit()));
        assert!(!take_pairing_code(&state, "abcdefgh"));
        assert!(take_pairing_code(&state, &code));
        assert!(!take_pairing_code(&state, &code));
        let other = issue_pairing_code(&state).unwrap();
        withdraw_pairing_codes(&state);
        assert!(!take_pairing_code(&state, &other));
    }

    #[test]
    fn a_code_of_the_wrong_shape_is_refused_even_when_one_like_it_is_pending() {
        let state = scratch("shape");
        let (logins, pairing) = (state.join("logins"), state.join("pairing"));
        for code in ["", "ab/cd"] {
            issue_code(&logins, code, LOGIN_LIFETIME).unwrap();
            assert!(!take_login_code(&state, code), "{code:?}");
        }
        for code in ["1234567", "123456789", "1234567a"] {
            issue_code(&pairing, code, PAIRING_LIFETIME).unwrap();
            assert!(!take_pairing_code(&state, code), "{code:?}");
        }
    }

    #[test]
    fn a_pairing_code_is_no_login_code() {
        let state = scratch("kinds-of-code");
        let code = issue_pairing_code(&state).unwrap();
        assert!(!take_login_code(&state, &code));
        assert!(take_pairing_code(&state, &code), "the refused login left the pairing code pending");
    }

    #[test]
    fn a_code_lasts_its_own_lifetime_and_no_longer() {
        let state = scratch("lifetimes");
        issue_login_code(&state).unwrap();
        issue_pairing_code(&state).unwrap();
        for (dir, lifetime) in [("logins", LOGIN_LIFETIME), ("pairing", PAIRING_LIFETIME)] {
            let entry = std::fs::read_dir(state.join(dir)).unwrap().next().unwrap().unwrap();
            let text = std::fs::read_to_string(entry.path()).unwrap();
            let expires = serde_json::from_str::<Value>(&text).unwrap()["expires"].as_u64().unwrap();
            let left = expires.saturating_sub(now_secs());
            assert!(left <= lifetime.as_secs() && left + 30 >= lifetime.as_secs(), "{dir}: {left}s left");
        }
    }

    #[test]
    fn an_empty_owner_token_file_is_replaced() {
        let state = scratch("empty-token");
        write_private(&state.join("token"), "\n").unwrap();
        let token = owner_token(&state).unwrap();
        assert_eq!(token.len(), 64);
        assert_eq!(std::fs::read_to_string(state.join("token")).unwrap().trim(), token);
    }

    #[test]
    fn a_code_sent_by_many_requests_at_once_works_for_one() {
        // Whoever read a code over the owner's shoulder must not pair beside them unnoticed.
        let state = scratch("race");
        for _ in 0..50 {
            let code = issue_pairing_code(&state).unwrap();
            let barrier = std::sync::Barrier::new(8);
            let taken = std::thread::scope(|scope| {
                let tries: Vec<_> = (0..8)
                    .map(|_| {
                        scope.spawn(|| {
                            barrier.wait();
                            take_pairing_code(&state, &code)
                        })
                    })
                    .collect();
                tries.into_iter().map(|attempt| attempt.join().unwrap()).filter(|taken| *taken).count()
            });
            assert_eq!(taken, 1);
        }
    }

    #[test]
    fn revoking_by_id_leaves_a_device_named_like_that_id() {
        let state = scratch("revoke-by-id");
        let (phone, _) = add_device(&state, "phone").unwrap();
        let (_, agent_token) = add_device_of(&state, &phone, DeviceKind::Sync).unwrap();
        assert_eq!(revoke_device_by_id(&state, &phone).unwrap().len(), 1);
        assert!(
            sync_device_for_token(&state, &agent_token).is_some(),
            "the agent named after the phone's id is still paired"
        );
    }

    #[test]
    fn devices_keep_only_a_hash_and_can_be_revoked() {
        let state = scratch("devices");
        let (id, token) = add_device(&state, "Owner's phone\u{7}").unwrap();
        let text = std::fs::read_to_string(state.join("devices.json")).unwrap();
        assert!(!text.contains(&token), "the token itself is never stored");
        let device = device_for_token(&state, &token).unwrap();
        assert_eq!((device.id.as_str(), device.name.as_str()), (id.as_str(), "Owner's phone"));
        assert!(device_for_token(&state, "wrong").is_none());
        assert_eq!((device.kind, device.scope), (DeviceKind::Browser, Scope::Control));
        assert_eq!(revoke_device(&state, &id).unwrap().len(), 1);
        assert!(device_for_token(&state, &token).is_none());
        #[cfg(unix)]
        assert_eq!(mode(&state.join("devices.json")), 0o600);
    }

    #[test]
    fn a_device_name_is_cut_to_sixty_four_characters() {
        assert_eq!(clean_device_name(&"x".repeat(100)).chars().count(), 64);
        assert_eq!(clean_device_name("\u{7}\n"), "unnamed device");
    }

    #[test]
    fn a_device_used_after_a_minute_has_its_last_use_written_to_it_alone() {
        let state = scratch("last-used");
        let (_, first) = add_device(&state, "first").unwrap();
        let (_, second) = add_device(&state, "second").unwrap();
        // Two devices under one id (ids are short): the use is written to the one whose token it was.
        change_devices(&state, |devices| {
            let id = devices[0].id.clone();
            for device in devices.iter_mut() {
                device.id = id.clone();
                device.last_used = 0;
            }
            ((), true)
        })
        .unwrap();
        assert!(device_for_token(&state, &second).is_some());
        let devices = load_devices(&state);
        assert_eq!(devices[0].last_used, 0, "the other device's last use is untouched");
        assert!(devices[1].last_used + 30 >= now_secs(), "the use was written");
        assert!(device_for_token(&state, &first).is_some());
        assert!(load_devices(&state)[0].last_used + 30 >= now_secs());
    }

    #[test]
    fn devices_paired_and_revoked_at_once_are_none_of_them_lost() {
        let state = scratch("devices-at-once");
        let (kept_id, _) = add_device(&state, "kept").unwrap();
        let doomed: Vec<String> = (0..8).map(|n| add_device(&state, &format!("doomed-{n}")).unwrap().0).collect();
        let threads: Vec<_> = (0..8)
            .map(|n| {
                let state = state.clone();
                let doomed = doomed[n].clone();
                std::thread::spawn(move || {
                    for i in 0..10 {
                        add_device(&state, &format!("new-{n}-{i}")).unwrap();
                    }
                    assert_eq!(revoke_device(&state, &doomed).unwrap().len(), 1);
                })
            })
            .collect();
        for thread in threads {
            thread.join().unwrap();
        }
        let devices = load_devices(&state);
        assert_eq!(devices.len(), 1 + 80, "every pairing kept");
        assert!(devices.iter().any(|d| d.id == kept_id));
        assert!(devices.iter().all(|d| !doomed.contains(&d.id)), "every revoked device stays revoked");
    }

    #[test]
    fn a_sync_secret_is_no_cookie_and_a_cookie_no_sync_secret() {
        let state = scratch("kinds");
        let (_, browser) = add_device(&state, "phone").unwrap();
        let (agent_id, agent) = add_device_of(&state, "laptop", DeviceKind::Sync).unwrap();
        assert!(device_for_token(&state, &browser).is_some());
        assert!(device_for_token(&state, &agent).is_none(), "an agent's secret opens no page");
        assert!(sync_device_for_token(&state, &browser).is_none(), "a cookie is no agent");
        let device = sync_device_for_token(&state, &agent).unwrap();
        assert_eq!((device.id.as_str(), device.kind, device.scope), (agent_id.as_str(), DeviceKind::Sync, Scope::Control));
    }

    #[test]
    fn every_pairing_gets_control_and_a_scope_can_be_narrowed() {
        let state = scratch("scopes");
        let (id, token) = add_device(&state, "phone").unwrap();
        assert_eq!(set_scope(&state, "phone", Scope::Watch).unwrap().len(), 1);
        assert_eq!(device_for_token(&state, &token).unwrap().scope, Scope::Watch);
        assert_eq!(set_scope(&state, &id, Scope::Control).unwrap().len(), 1);
        assert_eq!(device_for_token(&state, &token).unwrap().scope, Scope::Control);
        assert!(set_scope(&state, "nobody", Scope::Watch).unwrap().is_empty());
    }

    #[test]
    fn a_devices_file_from_before_sync_reads_as_browsers_with_control() {
        let state = scratch("old-file");
        write_private(
            &state.join("devices.json"),
            r#"[{"id":"ab12","name":"phone","token_sha256":"x","created":1,"last_used":2}]"#,
        )
        .unwrap();
        let devices = load_devices(&state);
        assert_eq!((devices[0].kind, devices[0].scope), (DeviceKind::Browser, Scope::Control));
    }

    #[test]
    fn hosts_and_origins_must_be_the_servers_own() {
        let local = Addresses::new(3100, &[]);
        assert!(local.host_ok(Some("127.0.0.1:3100")));
        assert!(local.host_ok(Some("LOCALHOST:3100")));
        assert!(!local.host_ok(Some("evil.example:3100")), "DNS rebinding");
        assert!(!local.host_ok(Some("127.0.0.1:3000")));
        assert!(!local.host_ok(None));
        assert!(local.origin_ok(Some("http://localhost:3100")));
        assert!(!local.origin_ok(Some("http://evil.example")));
        assert!(!local.origin_ok(Some("null")));
        assert!(!local.origin_ok(None));
        let lan = Addresses::new(3100, &["192.168.50.105".to_string(), "gx10-1a2b.local".to_string()]);
        assert!(lan.host_ok(Some("192.168.50.105:3100")));
        assert!(lan.origin_ok(Some("http://gx10-1a2b.local:3100")));
        assert!(!local.host_ok(Some("192.168.50.105:3100")), "loopback-only refuses the LAN address");
    }

    #[test]
    fn an_ipv6_address_is_named_in_brackets() {
        let lan = Addresses::new(3100, &["2001:DB8::5".to_string()]);
        assert!(lan.host_ok(Some("[2001:db8::5]:3100")));
        assert!(lan.origin_ok(Some("http://[2001:db8::5]:3100")));
    }

    #[test]
    fn a_blank_public_name_lets_no_empty_host_in() {
        let addresses = Addresses::new(3100, &[]).with_public(&[String::new(), "  ".to_string()]);
        assert!(!addresses.host_ok(Some("")));
        assert!(!addresses.host_ok(Some("  ")));
        assert!(!addresses.origin_ok(Some("http://")));
    }

    #[test]
    fn an_origin_counts_only_over_http_or_https() {
        let local = Addresses::new(3100, &[]);
        assert!(local.origin_ok(Some("https://localhost:3100")));
        for origin in ["ws://localhost:3100", "ftp://127.0.0.1:3100", "chrome-extension://localhost:3100"] {
            assert!(!local.origin_ok(Some(origin)), "{origin}");
        }
    }

    #[test]
    fn cookies_are_read_by_name() {
        let header = Some("a=1; mightling_session=abc; mightling_device=def");
        assert_eq!(cookie(header, SESSION_COOKIE), Some("abc"));
        assert_eq!(cookie(header, DEVICE_COOKIE), Some("def"));
        assert_eq!(cookie(header, "missing"), None);
        assert_eq!(cookie(None, SESSION_COOKIE), None);
        let longer = Some("mightling_sessionx=abc; mightling_device_old=def");
        assert_eq!(cookie(longer, SESSION_COOKIE), None, "a longer name is another cookie");
        assert_eq!(cookie(longer, DEVICE_COOKIE), None);
    }

    #[test]
    fn secrets_compare_whole() {
        assert!(same_secret("abc", "abc"));
        assert!(!same_secret("abc", "abd"));
        assert!(!same_secret("abc", "abcd"));
    }
}
