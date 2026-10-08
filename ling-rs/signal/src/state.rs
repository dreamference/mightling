//! The bridge's files (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §7), all under the state folder the
//! unit gives it (`/var/lib/mightling-signal`, 0700, owned by `mightling-signal`), each replaced
//! whole: written under a temporary name, then renamed. `status.json` is the one file meant to be
//! read by the user, and it goes to the runtime folder and holds no secret.

use std::path::Path;
use std::path::PathBuf;

use serde_json::Value;
use serde_json::json;

use crate::bridge::ThreadRef;
use crate::gate::Binding;
use crate::gate::Mode;
use crate::gate::Owner;

/// Writes a file whole, private to the bridge's account.
pub fn write_private(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let dir = path.parent().ok_or_else(|| std::io::Error::other("no parent folder"))?;
    std::fs::create_dir_all(dir)?;
    let temporary = dir.join(format!(".{}.{}", path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(), std::process::id()));
    {
        use std::io::Write;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
    }
    std::fs::rename(&temporary, path)
}

/// `bridge.json`: how the bridge is set up.
#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub mode: Mode,
    /// The account signal-cli runs (E.164).
    pub account: String,
    /// The bridge account's own id: in linked mode, the owner's; needed to recognise Note to Self.
    pub own_uuid: Option<String>,
    pub owner: Option<Owner>,
    /// A pairing in progress (written by `setup`, removed once the code arrives).
    pub binding: Option<Binding>,
    /// `ling web`'s port on loopback.
    pub port: u16,
    /// The node's name, for messages.
    pub node: String,
    /// signal-cli's launcher script, and the environment it needs (JAVA_HOME, library path).
    pub signal_cli: PathBuf,
    pub signal_cli_env: Vec<(String, String)>,
    pub signal_cli_version: String,
    /// Whether the served model takes images (`ModelSpec.supports_vision`); without it images are
    /// saved in the thread's folder and named in the text instead.
    pub vision: bool,
}

impl Config {
    pub fn to_json(&self) -> Value {
        let mode = match &self.mode {
            Mode::Dedicated => json!({ "kind": "dedicated" }),
            Mode::Linked { own_device } => json!({ "kind": "linked", "ownDevice": own_device }),
        };
        json!({
            "mode": mode,
            "account": self.account,
            "ownUuid": self.own_uuid,
            "owner": self.owner.as_ref().map(|o| json!({ "aci": o.aci, "fingerprint": o.fingerprint })),
            "binding": self.binding.as_ref().map(|b| json!({ "code": b.code, "expiresMs": b.expires_ms })),
            "port": self.port,
            "node": self.node,
            "signalCli": self.signal_cli,
            "signalCliEnv": self.signal_cli_env.iter().map(|(k, v)| json!([k, v])).collect::<Vec<_>>(),
            "signalCliVersion": self.signal_cli_version,
            "vision": self.vision,
        })
    }

    pub fn from_json(value: &Value) -> Result<Config, String> {
        let text = |key: &str| value.get(key).and_then(Value::as_str).map(str::to_string);
        let mode = match value.pointer("/mode/kind").and_then(Value::as_str) {
            Some("dedicated") | None => Mode::Dedicated,
            Some("linked") => Mode::Linked { own_device: value.pointer("/mode/ownDevice").and_then(Value::as_u64).ok_or("linked mode names no device")? },
            Some(other) => return Err(format!("unknown mode {other}")),
        };
        let owner = value.get("owner").filter(|o| !o.is_null()).map(|o| -> Result<Owner, String> {
            Ok(Owner {
                aci: o.get("aci").and_then(Value::as_str).ok_or("the owner has no account id")?.to_string(),
                fingerprint: o.get("fingerprint").and_then(Value::as_str).ok_or("the owner has no fingerprint")?.to_string(),
            })
        });
        let binding = value.get("binding").filter(|b| !b.is_null()).and_then(|b| {
            Some(Binding { code: b.get("code")?.as_str()?.to_string(), expires_ms: b.get("expiresMs")?.as_u64()? })
        });
        Ok(Config {
            mode,
            account: text("account").ok_or("bridge.json names no account")?,
            own_uuid: text("ownUuid"),
            owner: owner.transpose()?,
            binding,
            port: value.get("port").and_then(Value::as_u64).and_then(|p| u16::try_from(p).ok()).unwrap_or(3100),
            node: text("node").unwrap_or_default(),
            signal_cli: PathBuf::from(text("signalCli").ok_or("bridge.json names no signal-cli")?),
            signal_cli_env: value
                .get("signalCliEnv")
                .and_then(Value::as_array)
                .map(|pairs| {
                    pairs
                        .iter()
                        .filter_map(|pair| Some((pair.get(0)?.as_str()?.to_string(), pair.get(1)?.as_str()?.to_string())))
                        .collect()
                })
                .unwrap_or_default(),
            signal_cli_version: text("signalCliVersion").unwrap_or_default(),
            vision: value.get("vision").and_then(Value::as_bool).unwrap_or(false),
        })
    }

    pub fn load(state: &Path) -> Result<Config, String> {
        let path = state.join("bridge.json");
        let text = std::fs::read_to_string(&path).map_err(|err| format!("{}: {err} (run `ling signal setup`)", path.display()))?;
        Config::from_json(&serde_json::from_str(&text).map_err(|err| format!("{}: {err}", path.display()))?)
    }

    pub fn save(&self, state: &Path) -> std::io::Result<()> {
        write_private(&state.join("bridge.json"), serde_json::to_string_pretty(&self.to_json()).unwrap_or_default().as_bytes())
    }
}

/// `conversation.json`: the thread in use, the recent ones, and what the gate remembers.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Conversation {
    pub current: Option<String>,
    pub recent: Vec<ThreadRef>,
    pub seen: Vec<u64>,
    pub ignored: u64,
    pub identity_refusals: u64,
}

impl Conversation {
    pub fn to_json(&self) -> Value {
        json!({
            "current": self.current,
            "recent": self.recent.iter().map(|t| json!({ "id": t.id, "title": t.title })).collect::<Vec<_>>(),
            "seen": self.seen,
            "ignored": self.ignored,
            "identityRefusals": self.identity_refusals,
        })
    }

    pub fn from_json(value: &Value) -> Conversation {
        Conversation {
            current: value.get("current").and_then(Value::as_str).map(str::to_string),
            recent: value
                .get("recent")
                .and_then(Value::as_array)
                .map(|list| {
                    list.iter()
                        .filter_map(|t| Some(ThreadRef { id: t.get("id")?.as_str()?.to_string(), title: t.get("title")?.as_str()?.to_string() }))
                        .collect()
                })
                .unwrap_or_default(),
            seen: value.get("seen").and_then(Value::as_array).map(|l| l.iter().filter_map(Value::as_u64).collect()).unwrap_or_default(),
            ignored: value.get("ignored").and_then(Value::as_u64).unwrap_or(0),
            identity_refusals: value.get("identityRefusals").and_then(Value::as_u64).unwrap_or(0),
        }
    }

    /// A missing or unreadable file is an empty conversation: nothing in it is worth refusing to start.
    pub fn load(state: &Path) -> Conversation {
        std::fs::read_to_string(state.join("conversation.json"))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .map(|value| Conversation::from_json(&value))
            .unwrap_or_default()
    }

    pub fn save(&self, state: &Path) -> std::io::Result<()> {
        write_private(&state.join("conversation.json"), self.to_json().to_string().as_bytes())
    }
}

/// The `ling web` device cookie (`mightling_device=…`).
pub fn load_cookie(state: &Path) -> Option<String> {
    std::fs::read_to_string(state.join("device-cookie")).ok().map(|c| c.trim().to_string()).filter(|c| !c.is_empty())
}

pub fn save_cookie(state: &Path, cookie: &str) -> std::io::Result<()> {
    write_private(&state.join("device-cookie"), cookie.as_bytes())
}

/// Writes `status.json` for `ling signal status`, readable by anyone: no secret, no message text.
pub fn write_status(runtime: &Path, status: &Value) -> std::io::Result<()> {
    std::fs::create_dir_all(runtime)?;
    let path = runtime.join("status.json");
    let temporary = runtime.join(".status.json.tmp");
    std::fs::write(&temporary, status.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o644))?;
    }
    std::fs::rename(temporary, path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-signal-state-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn sample() -> Config {
        Config {
            mode: Mode::Linked { own_device: 4 },
            account: "+15550000".to_string(),
            own_uuid: Some("me".to_string()),
            owner: Some(Owner { aci: "me".to_string(), fingerprint: "05ab".to_string() }),
            binding: Some(Binding { code: "123456".to_string(), expires_ms: 9 }),
            port: 3100,
            node: "gx10".to_string(),
            signal_cli: PathBuf::from("/opt/mightling/signal-cli-0.14.9/bin/signal-cli"),
            signal_cli_env: vec![("JAVA_HOME".to_string(), "/usr/lib/jvm/java-25-openjdk-arm64".to_string())],
            signal_cli_version: "0.14.9".to_string(),
            vision: true,
        }
    }

    #[test]
    fn the_config_round_trips_and_is_private() {
        let dir = scratch("config");
        sample().save(&dir).unwrap();
        assert_eq!(Config::load(&dir).unwrap(), sample());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(dir.join("bridge.json")).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }
        assert!(Config::load(&dir.join("missing")).unwrap_err().contains("ling signal setup"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_conversation_round_trips_and_a_bad_file_is_empty() {
        let dir = scratch("conversation");
        let conversation = Conversation {
            current: Some("t1".to_string()),
            recent: vec![ThreadRef { id: "t1".to_string(), title: "hello".to_string() }],
            seen: vec![1, 2, 3],
            ignored: 4,
            identity_refusals: 1,
        };
        conversation.save(&dir).unwrap();
        assert_eq!(Conversation::load(&dir), conversation);
        std::fs::write(dir.join("conversation.json"), "{broken").unwrap();
        assert_eq!(Conversation::load(&dir), Conversation::default());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_cookie_and_the_status() {
        let dir = scratch("cookie");
        assert_eq!(load_cookie(&dir), None);
        save_cookie(&dir, "mightling_device=abc\n").unwrap();
        assert_eq!(load_cookie(&dir).as_deref(), Some("mightling_device=abc"));
        write_status(&dir.join("run"), &json!({"running": true})).unwrap();
        assert_eq!(std::fs::read_to_string(dir.join("run/status.json")).unwrap(), r#"{"running":true}"#);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
