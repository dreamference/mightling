//! Settings, read from the same files the launcher reads (spec §4.2), and project trust, read only
//! from `$CODEX_HOME/config.toml` (spec §9.1).

use std::path::{Path, PathBuf};

use crate::paths;

/// The `code_index_*` / `puffin_code_*` keys, with their defaults.
#[derive(Debug, Clone)]
pub struct Settings {
    /// The model server's base URL, for the idle check of §9.2.
    pub vllm_host: String,
    /// Changed files searched by text per query (§7.3).
    pub scan_max_files: usize,
    /// Bytes read by that search per query (§7.3).
    pub scan_max_bytes: u64,
    /// Rows printed by default (§7.2).
    pub row_limit: usize,
    /// Files above which a repository is never indexed (§9.1).
    pub max_files: usize,
    /// Memory ceiling for executing indexers, MiB (§6.4).
    pub memory_ceiling_mb: u64,
    /// Memory ceiling for the universal and static indexers, MiB (§6.4).
    pub small_ceiling_mb: u64,
    /// Minimum seconds between two coalesced runs of one kind (§9.2).
    pub min_interval_s: u64,
    /// Commits the exact Rust index may fall behind before a run is wanted (§6.3).
    pub stale_commits: u64,
    /// Whether search by meaning is offered (§4); off by default.
    pub semantic: bool,
    /// Whether indexing is on at all.
    pub enabled: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            vllm_host: "http://localhost:8000".to_string(),
            scan_max_files: 2000,
            scan_max_bytes: 64 << 20,
            row_limit: 40,
            max_files: 50_000,
            memory_ceiling_mb: 40 << 10,
            small_ceiling_mb: 4 << 10,
            min_interval_s: 15 * 60,
            stale_commits: 20,
            semantic: false,
            enabled: true,
        }
    }
}

impl Settings {
    /// Reads the settings: `DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then
    /// `~/.config/dreamference/config.toml`, the first that exists; every key is optional.
    pub fn load(repo_root: &Path) -> Settings {
        let mut settings = Settings::default();
        let Some(table) = config_file(repo_root).and_then(|p| std::fs::read_to_string(p).ok()).and_then(|t| t.parse::<toml::Table>().ok()) else {
            return settings;
        };
        let int = |key: &str| table.get(key).and_then(toml::Value::as_integer).filter(|v| *v >= 0).map(|v| v as u64);
        if let Some(host) = table.get("vllm_host").and_then(toml::Value::as_str) {
            settings.vllm_host = host.trim_end_matches('/').to_string();
        }
        if let Some(v) = int("code_index_scan_max_files") { settings.scan_max_files = v as usize }
        if let Some(v) = int("code_index_scan_max_bytes") { settings.scan_max_bytes = v }
        if let Some(v) = int("code_index_row_limit") { settings.row_limit = (v as usize).clamp(1, 200) }
        if let Some(v) = int("code_index_max_files") { settings.max_files = v as usize }
        if let Some(v) = int("code_index_memory_ceiling_mb") { settings.memory_ceiling_mb = v }
        if let Some(v) = int("code_index_small_ceiling_mb") { settings.small_ceiling_mb = v }
        if let Some(v) = int("code_index_min_interval_s") { settings.min_interval_s = v }
        if let Some(v) = int("code_index_stale_commits") { settings.stale_commits = v }
        if let Some(v) = table.get("puffin_code_semantic").and_then(toml::Value::as_bool) { settings.semantic = v }
        if let Some(v) = table.get("code_index_enabled").and_then(toml::Value::as_bool) { settings.enabled = v }
        if let Ok(host) = std::env::var("DREAMFERENCE_VLLM_HOST") {
            settings.vllm_host = host.trim_end_matches('/').to_string();
        }
        settings
    }
}

fn config_file(repo_root: &Path) -> Option<PathBuf> {
    if let Ok(path) = std::env::var("DREAMFERENCE_CONFIG_PATH") {
        return Some(PathBuf::from(path));
    }
    let local = repo_root.join("dreamference.toml");
    if local.is_file() {
        return Some(local);
    }
    let global = paths::home().join(".config/dreamference/config.toml");
    global.is_file().then_some(global)
}

/// Whether the user has marked `root` trusted in Codex's own per-project trust.
///
/// Nothing inside the repository can grant trust (spec §9.1): a file there can be shipped in a
/// clone, and the agent can write it from inside the workspace-write sandbox. Only
/// `[projects."<path>"] trust_level = "trusted"` in `$CODEX_HOME/config.toml` counts.
pub fn is_trusted(root: &Path) -> bool {
    is_trusted_in(&paths::codex_home(), root)
}

/// [`is_trusted`] against a given `$CODEX_HOME`.
pub fn is_trusted_in(codex_home: &Path, root: &Path) -> bool {
    let Ok(text) = std::fs::read_to_string(codex_home.join("config.toml")) else { return false };
    let Ok(table) = text.parse::<toml::Table>() else { return false };
    let Some(projects) = table.get("projects").and_then(toml::Value::as_table) else { return false };
    let canonical = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    projects.iter().any(|(path, entry)| {
        let listed = Path::new(path);
        let listed = listed.canonicalize().unwrap_or_else(|_| listed.to_path_buf());
        listed == canonical
            && entry.get("trust_level").and_then(toml::Value::as_str) == Some("trusted")
    })
}
