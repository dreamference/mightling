//! Settings, read from the same files the launcher reads (spec §4.2), and project trust, read only
//! from `$CODEX_HOME/config.toml` (spec §9.1).

use std::path::{Path, PathBuf};

use crate::paths;

/// Which layers answer queries and are built (`mightling_code_layers`, `MIGHTLING_CODE_LAYERS`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layers {
    /// The universal graph and the exact SCIP stores (the default).
    All,
    /// The SCIP stores alone: the graph is neither built nor read, and where no store has an
    /// answer the text search of the changed and unindexed files stands in for it.
    Exact,
}

impl Layers {
    /// `all` or `exact`, in any case; anything else is not a value.
    pub fn parse(value: &str) -> Option<Layers> {
        match value.trim().to_ascii_lowercase().as_str() {
            "all" => Some(Layers::All),
            "exact" => Some(Layers::Exact),
            _ => None,
        }
    }
}

/// The `code_index_*` / `mightling_code_*` keys, with their defaults.
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
    /// Which layers answer and are built.
    pub layers: Layers,
    /// Tracked files above which a submodule that is yours is not indexed automatically (§4.3).
    /// Honoured only from a file outside the repository: see `submodules::evaluate`.
    pub submodule_max_files: usize,
    /// The file the settings were read from, if any.
    pub config_path: Option<PathBuf>,
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
            layers: Layers::All,
            submodule_max_files: 5000,
            config_path: None,
        }
    }
}

impl Settings {
    /// Reads the settings: `DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then
    /// `~/.config/dreamference/config.toml`, the first that exists; every key is optional.
    pub fn load(repo_root: &Path) -> Settings {
        Settings::load_with(config_file(repo_root), |key| std::env::var(key).ok())
    }

    /// The settings from `config_path`, then the variables `env` gives, which win over the file
    /// and apply without one.
    fn load_with(config_path: Option<PathBuf>, env: impl Fn(&str) -> Option<String>) -> Settings {
        let mut settings = Settings {
            config_path,
            ..Settings::default()
        };
        settings.read_file();
        if let Some(host) = env("DREAMFERENCE_VLLM_HOST") {
            settings.vllm_host = host.trim_end_matches('/').to_string();
        }
        if let Some(layers) = env("MIGHTLING_CODE_LAYERS")
            .as_deref()
            .and_then(Layers::parse)
        {
            settings.layers = layers;
        }
        settings
    }

    /// Whether the graph is off (`layers = exact`).
    pub fn exact_only(&self) -> bool {
        self.layers == Layers::Exact
    }

    fn read_file(&mut self) {
        let settings = self;
        let Some(table) = settings
            .config_path
            .as_ref()
            .and_then(|p| std::fs::read_to_string(p).ok())
            .and_then(|t| t.parse::<toml::Table>().ok())
        else {
            return;
        };
        let int = |key: &str| {
            table
                .get(key)
                .and_then(toml::Value::as_integer)
                .filter(|v| *v >= 0)
                .map(|v| v as u64)
        };
        if let Some(host) = table.get("vllm_host").and_then(toml::Value::as_str) {
            settings.vllm_host = host.trim_end_matches('/').to_string();
        }
        if let Some(v) = int("code_index_scan_max_files") {
            settings.scan_max_files = v as usize
        }
        if let Some(v) = int("code_index_scan_max_bytes") {
            settings.scan_max_bytes = v
        }
        if let Some(v) = int("code_index_row_limit") {
            settings.row_limit = (v as usize).clamp(1, 200)
        }
        if let Some(v) = int("code_index_max_files") {
            settings.max_files = v as usize
        }
        if let Some(v) = int("code_index_memory_ceiling_mb") {
            settings.memory_ceiling_mb = v
        }
        if let Some(v) = int("code_index_small_ceiling_mb") {
            settings.small_ceiling_mb = v
        }
        if let Some(v) = int("code_index_min_interval_s") {
            settings.min_interval_s = v
        }
        if let Some(v) = int("code_index_stale_commits") {
            settings.stale_commits = v
        }
        if let Some(v) = int("code_index_submodule_max_files") {
            settings.submodule_max_files = v as usize
        }
        if let Some(v) = table
            .get("mightling_code_semantic")
            .and_then(toml::Value::as_bool)
        {
            settings.semantic = v
        }
        if let Some(v) = table
            .get("code_index_enabled")
            .and_then(toml::Value::as_bool)
        {
            settings.enabled = v
        }
        if let Some(v) = table
            .get("mightling_code_layers")
            .and_then(toml::Value::as_str)
            .and_then(Layers::parse)
        {
            settings.layers = v
        }
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
    let Ok(text) = std::fs::read_to_string(codex_home.join("config.toml")) else {
        return false;
    };
    let Ok(table) = text.parse::<toml::Table>() else {
        return false;
    };
    let Some(projects) = table.get("projects").and_then(toml::Value::as_table) else {
        return false;
    };
    let canonical = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    projects.iter().any(|(path, entry)| {
        let listed = Path::new(path);
        let listed = listed
            .canonicalize()
            .unwrap_or_else(|_| listed.to_path_buf());
        listed == canonical
            && entry.get("trust_level").and_then(toml::Value::as_str) == Some("trusted")
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_host_variable_wins_with_or_without_a_settings_file() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("config.toml");
        let env = |key: &str| {
            (key == "DREAMFERENCE_VLLM_HOST").then(|| "http://gpu-node:8001/".to_string())
        };
        let s = Settings::load_with(Some(file.clone()), env);
        assert_eq!(s.vllm_host, "http://gpu-node:8001");
        std::fs::write(&file, "vllm_host = \"http://127.0.0.1:9000\"\n").unwrap();
        assert_eq!(
            Settings::load_with(Some(file.clone()), env).vllm_host,
            "http://gpu-node:8001"
        );
        assert_eq!(
            Settings::load_with(Some(file), |_| None).vllm_host,
            "http://127.0.0.1:9000"
        );
    }

    #[test]
    fn layers_parse_handles_case_whitespace_and_unknown_values() {
        assert_eq!(Layers::parse("all"), Some(Layers::All));
        assert_eq!(Layers::parse("  EXACT\n"), Some(Layers::Exact));
        assert_eq!(Layers::parse("both"), None);
        assert_eq!(Layers::parse(""), None);
        let mut s = Settings::default();
        assert!(!s.exact_only());
        s.layers = Layers::Exact;
        assert!(s.exact_only());
    }

    #[test]
    fn settings_read_file_parses_fields_and_clamps_row_limit() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("dreamference.toml");
        std::fs::write(
            &file,
            r#"
vllm_host = "http://127.0.0.1:9000/"
code_index_scan_max_files = 500
code_index_scan_max_bytes = 1048576
code_index_row_limit = 999
code_index_max_files = 10000
code_index_memory_ceiling_mb = 8192
code_index_small_ceiling_mb = 2048
code_index_min_interval_s = 120
code_index_stale_commits = 5
code_index_submodule_max_files = 300
mightling_code_semantic = true
code_index_enabled = false
mightling_code_layers = "exact"
"#,
        )
        .unwrap();
        let mut s = Settings {
            config_path: Some(file.clone()),
            ..Settings::default()
        };
        s.read_file();
        assert_eq!(s.vllm_host, "http://127.0.0.1:9000");
        assert_eq!(s.scan_max_files, 500);
        assert_eq!(s.scan_max_bytes, 1_048_576);
        assert_eq!(s.row_limit, 200, "clamped to 200");
        assert_eq!(s.max_files, 10_000);
        assert_eq!(s.memory_ceiling_mb, 8192);
        assert_eq!(s.small_ceiling_mb, 2048);
        assert_eq!(s.min_interval_s, 120);
        assert_eq!(s.stale_commits, 5);
        assert_eq!(s.submodule_max_files, 300);
        assert!(s.semantic);
        assert!(!s.enabled);
        assert_eq!(s.layers, Layers::Exact);

        std::fs::write(
            &file,
            "code_index_row_limit = 0\ncode_index_memory_ceiling_mb = -5\n",
        )
        .unwrap();
        let mut s2 = Settings {
            config_path: Some(file),
            ..Settings::default()
        };
        s2.read_file();
        assert_eq!(s2.row_limit, 1, "clamped to 1");
        assert_eq!(
            s2.memory_ceiling_mb,
            Settings::default().memory_ceiling_mb,
            "negative values are ignored"
        );
    }

    #[test]
    fn is_trusted_in_requires_matching_canonical_path_and_trusted_level() {
        let dir = tempfile::tempdir().unwrap();
        let codex = dir.path().join("codex");
        let repo = dir.path().join("repo");
        let link = dir.path().join("repo-link");
        let untrusted = dir.path().join("untrusted");
        std::fs::create_dir_all(&codex).unwrap();
        std::fs::create_dir_all(&repo).unwrap();
        std::fs::create_dir_all(&untrusted).unwrap();
        std::os::unix::fs::symlink(&repo, &link).unwrap();

        assert!(!is_trusted_in(&codex, &repo), "missing config.toml");

        let config = format!(
            "[projects.{:?}]\ntrust_level = \"trusted\"\n[projects.{:?}]\ntrust_level = \"untrusted\"\n",
            repo.to_string_lossy(),
            untrusted.to_string_lossy()
        );
        std::fs::write(codex.join("config.toml"), config).unwrap();

        assert!(is_trusted_in(&codex, &repo));
        assert!(
            is_trusted_in(&codex, &link),
            "symlink resolves to canonical trusted root"
        );
        assert!(!is_trusted_in(&codex, &untrusted));
        assert!(!is_trusted_in(&codex, &dir.path().join("other")));
    }
}
