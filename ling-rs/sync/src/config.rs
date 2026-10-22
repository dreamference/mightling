//! What a device keeps for sync (specs/DREAMFERENCE_MIGHTLING_SYNC.md §5.1, §11):
//!
//! - `~/.mightling/sync/device.toml` (0600): the device's id, name and secret from pairing, and the
//!   hub's port. The secret opens the hub's `/api/sync/` routes on the node and nothing else. The
//!   node's address is never kept: it is found by the node tiers each time the agent starts
//!   (`ling node`), and on the node itself the hub is on loopback.
//! - `[sync]` in the user's own `~/.config/dreamference/config.toml`, never a repository's file:
//!   `exec` (publish `ling exec` sessions too, off), `exclude` (folders whose threads never leave
//!   this device) and `replicate` (Phase 4, not built).

use std::path::Path;
use std::path::PathBuf;

use ling_web_server::auth;

/// The sync folder under `ling`'s home: the device file, the agent's state, and on the node the hub.
pub fn sync_dir(codex_home: &Path) -> PathBuf {
    codex_home.join("sync")
}

pub fn device_path(codex_home: &Path) -> PathBuf {
    sync_dir(codex_home).join("device.toml")
}

/// This device's pairing with the hub.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DeviceFile {
    pub device: String,
    pub name: String,
    pub secret: String,
    /// The hub's port: `ling web`'s.
    pub port: u16,
    /// The node itself: its hub is on loopback.
    pub local: bool,
}

impl DeviceFile {
    pub fn load(codex_home: &Path) -> Option<DeviceFile> {
        let text = std::fs::read_to_string(device_path(codex_home)).ok()?;
        let table: toml::Table = toml::from_str(&text).ok()?;
        let text_of = |key: &str| table.get(key).and_then(toml::Value::as_str).map(str::to_string);
        Some(DeviceFile {
            device: text_of("device")?,
            name: text_of("name").unwrap_or_default(),
            secret: text_of("secret")?,
            port: table.get("port").and_then(toml::Value::as_integer).and_then(|port| u16::try_from(port).ok()).unwrap_or(3100),
            local: table.get("local").and_then(toml::Value::as_bool).unwrap_or(false),
        })
    }

    pub fn save(&self, codex_home: &Path) -> std::io::Result<()> {
        let mut table = toml::Table::new();
        table.insert("device".to_string(), toml::Value::String(self.device.clone()));
        table.insert("name".to_string(), toml::Value::String(self.name.clone()));
        table.insert("secret".to_string(), toml::Value::String(self.secret.clone()));
        table.insert("port".to_string(), toml::Value::Integer(i64::from(self.port)));
        table.insert("local".to_string(), toml::Value::Boolean(self.local));
        let text = format!(
            "# Written by `ling sync enable`; `ling sync forget` removes it. The secret opens the sync\n\
             # hub's routes on the node (`/api/sync/`) and nothing else; `ling web revoke` ends it.\n{}",
            toml::to_string(&table).unwrap_or_default()
        );
        auth::write_private(&device_path(codex_home), &text)
    }
}

/// `[sync]` in the user's configuration.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Settings {
    /// Publish `ling exec` sessions too (O2: they are not threads a user browses).
    pub exec: bool,
    /// Folders whose threads never leave this device, absolute.
    pub exclude: Vec<PathBuf>,
    /// Copy other devices' threads into this device's own store (Phase 4; not built).
    pub replicate: bool,
}

impl Settings {
    /// Reads `[sync]` from the user's own configuration file; nothing set is the defaults.
    pub fn load() -> Settings {
        ling_airgapped::user_config_file()
            .and_then(|path| std::fs::read_to_string(path).ok())
            .map(|text| Settings::parse(&text, home().as_deref()))
            .unwrap_or_default()
    }

    pub fn parse(text: &str, home: Option<&Path>) -> Settings {
        let Ok(table) = toml::from_str::<toml::Table>(text) else { return Settings::default() };
        let Some(sync) = table.get("sync").and_then(toml::Value::as_table) else { return Settings::default() };
        let flag = |key: &str| sync.get(key).and_then(toml::Value::as_bool).unwrap_or(false);
        let exclude = sync
            .get("exclude")
            .and_then(toml::Value::as_array)
            .map(|paths| paths.iter().filter_map(toml::Value::as_str).filter_map(|path| expand(path, home)).collect())
            .unwrap_or_default();
        Settings { exec: flag("exec"), exclude, replicate: flag("replicate") }
    }

    /// Whether a thread that ran in `cwd` stays on this device.
    pub fn excluded(&self, cwd: &str) -> bool {
        let cwd = Path::new(cwd);
        self.exclude.iter().any(|folder| cwd.starts_with(folder))
    }
}

fn home() -> Option<PathBuf> {
    std::env::var_os("HOME").filter(|home| !home.is_empty()).map(PathBuf::from)
}

/// `~/x` and absolute paths; anything relative names no folder.
fn expand(path: &str, home: Option<&Path>) -> Option<PathBuf> {
    let path = path.trim();
    if let Some(rest) = path.strip_prefix("~/") {
        return home.map(|home| home.join(rest));
    }
    if path == "~" {
        return home.map(Path::to_path_buf);
    }
    let path = PathBuf::from(path);
    path.is_absolute().then_some(path)
}

/// `ling sync exclude <folder>`: adds the folder to `[sync] exclude` in the user's configuration,
/// keeping the rest of the file as it is. Returns false when it was already there.
pub fn add_exclude(config: &Path, folder: &Path) -> Result<bool, String> {
    let text = std::fs::read_to_string(config).unwrap_or_default();
    let mut document: toml_edit::DocumentMut = text.parse().map_err(|err| format!("{} is not valid TOML: {err}", config.display()))?;
    let sync = document.entry("sync").or_insert(toml_edit::table());
    let Some(sync) = sync.as_table_mut() else { return Err("`sync` in the configuration is not a table".to_string()) };
    let list = sync.entry("exclude").or_insert(toml_edit::value(toml_edit::Array::new()));
    let Some(list) = list.as_array_mut() else { return Err("`[sync] exclude` is not a list".to_string()) };
    let wanted = folder.to_string_lossy();
    if list.iter().any(|value| value.as_str() == Some(&wanted)) {
        return Ok(false);
    }
    list.push(wanted.as_ref());
    if let Some(parent) = config.parent() {
        std::fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    std::fs::write(config, document.to_string()).map_err(|err| format!("could not write {}: {err}", config.display()))?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-sync-config-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn the_device_file_is_private_and_reads_back() {
        let home = scratch("device");
        assert!(DeviceFile::load(&home).is_none());
        let device = DeviceFile { device: "ab12".to_string(), name: "laptop".to_string(), secret: "s".repeat(64), port: 3101, local: false };
        device.save(&home).unwrap();
        assert_eq!(DeviceFile::load(&home), Some(device));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(device_path(&home)).unwrap().permissions().mode() & 0o777, 0o600);
            assert_eq!(std::fs::metadata(sync_dir(&home)).unwrap().permissions().mode() & 0o777, 0o700);
        }
    }

    #[test]
    fn settings_are_off_by_default_and_read_from_the_sync_table() {
        assert_eq!(Settings::parse("", None), Settings::default());
        assert_eq!(Settings::parse("[chat]\nx = 1\n", None), Settings::default());
        let home = Path::new("/home/u");
        let settings = Settings::parse("[sync]\nexec = true\nexclude = [\"~/work/secret\", \"/srv/x\", \"relative\"]\n", Some(home));
        assert!(settings.exec && !settings.replicate);
        assert_eq!(settings.exclude, [PathBuf::from("/home/u/work/secret"), PathBuf::from("/srv/x")]);
        assert!(settings.excluded("/home/u/work/secret/app"));
        assert!(settings.excluded("/home/u/work/secret"));
        assert!(!settings.excluded("/home/u/work/secretive"), "a folder, not a prefix of a name");
        assert!(!settings.excluded("/home/u/work"));
    }

    #[test]
    fn exclude_adds_a_folder_once_and_keeps_the_rest_of_the_file() {
        let dir = scratch("exclude");
        let config = dir.join("config.toml");
        std::fs::write(&config, "# mine\nvllm_host = \"http://n:8000\"\n\n[chat]\nnight_repos = []\n").unwrap();
        assert_eq!(add_exclude(&config, Path::new("/w/secret")), Ok(true));
        assert_eq!(add_exclude(&config, Path::new("/w/secret")), Ok(false));
        let text = std::fs::read_to_string(&config).unwrap();
        assert!(text.starts_with("# mine\nvllm_host = \"http://n:8000\"\n"));
        assert_eq!(Settings::parse(&text, None).exclude, [PathBuf::from("/w/secret")]);
    }
}
