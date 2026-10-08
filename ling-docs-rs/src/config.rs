//! Where things are and the global settings (spec §11).
//!
//! The user-level state lives in the agent's home: `docs.toml` (the collections, §5.1) and
//! `docs/<collection>.db` (§6). Settings come from the same files the launcher reads, then from
//! `DREAMFERENCE_MIGHTLING_DOCS_*` in the environment, which wins.
//!
//! **Names during the rename.** Shared runtime names are today's, so ling-docs and the running
//! code index agree: the home is `$CODEX_HOME`, else `~/.mightling`, and `scripts/rename_mightling.py`
//! turns that into `~/.mightling` with the rest of the tree. New names (the crate, the binary, the
//! `DREAMFERENCE_MIGHTLING_DOCS_*` variables, the `mightling_docs` key) are already final.

use std::path::{Path, PathBuf};

/// The embedding model this build knows how to run (§7.3), and the folder name it is installed
/// under.
pub const MODEL_NAME: &str = "snowflake-arctic-embed-m-v2.0-int8";

/// Target chunk size in the model's tokens (§7.2, §15.4).
pub const CHUNK_TOKENS: usize = 512;

/// The agent's home: `$CODEX_HOME`, else `~/.mightling`.
pub fn home() -> PathBuf {
    if let Some(dir) = std::env::var_os("CODEX_HOME").filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    user_home().join(".mightling")
}

/// The user's home directory.
pub fn user_home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

/// `docs.toml`: the collections. User-level only, never read from a repository or a collection.
pub fn docs_toml() -> PathBuf {
    home().join("docs.toml")
}

/// The databases' directory, mode 700.
pub fn docs_dir() -> PathBuf {
    home().join("docs")
}

/// One collection's database.
pub fn db_path(collection: &str) -> PathBuf {
    docs_dir().join(format!("{collection}.db"))
}

/// The directory of the running executable, symlinks resolved.
fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe().ok()?.canonicalize().ok()?.parent().map(Path::to_path_buf)
}

/// Where PDFium and ONNX Runtime are: `MIGHTLING_DOCS_LIB_DIR`, else `../lib/ling-docs` beside
/// the binary (`~/.local/share/dreamference/mightling/lib/ling-docs` for the installed one).
pub fn lib_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("MIGHTLING_DOCS_LIB_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    exe_dir().map(|d| d.join("../lib/ling-docs")).unwrap_or_else(|| PathBuf::from("lib/ling-docs"))
}

/// Where the embedding model is: `MIGHTLING_DOCS_MODEL_DIR`, else `../models/<MODEL_NAME>`
/// beside the binary. It holds `model.onnx` (the int8 export) and `tokenizer.json`.
pub fn model_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("MIGHTLING_DOCS_MODEL_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    exe_dir().map(|d| d.join("../models").join(MODEL_NAME)).unwrap_or_else(|| PathBuf::from(MODEL_NAME))
}

/// What is missing for indexing, if anything: indexing waits and says why (§7.3).
pub fn missing_runtime() -> Option<String> {
    missing_runtime_at(&lib_dir(), &model_dir())
}

/// [`missing_runtime`] for given directories.
pub fn missing_runtime_at(lib: &Path, model: &Path) -> Option<String> {
    let mut missing = Vec::new();
    for (path, what) in [
        (lib.join("libpdfium.so"), "PDFium"),
        (lib.join("libonnxruntime.so"), "ONNX Runtime"),
        (model.join("model.onnx"), "the embedding model"),
        (model.join("tokenizer.json"), "the embedding model's tokenizer"),
    ] {
        if !path.is_file() {
            missing.push(what);
        }
    }
    (!missing.is_empty()).then(|| format!("{} not installed (run `ling-admin docs setup`)", missing.join(", ")))
}

/// The global settings (§11).
#[derive(Debug, Clone)]
pub struct Settings {
    /// `mightling_docs`: the index at all.
    pub enabled: bool,
    /// `docs_bm25_weight`: BM25's weight in the fusion, dense being 1 (§7.4, §15.3).
    pub bm25_weight: f64,
    /// `docs_scan_interval_min`: minutes between two change scans while a session runs (§9).
    pub scan_interval_min: u64,
    /// `docs_max_index_gb`: a collection's database above this defers further indexing (§9).
    pub max_index_gb: f64,
    /// `docs_extract_timeout_s`: one file's extraction time limit (§7.1).
    pub extract_timeout_s: u64,
    /// `docs_embedding_model`: only [`MODEL_NAME`] is known to this build.
    pub embedding_model: String,
    /// ONNX Runtime threads for indexing (the slice's four cores).
    pub embed_threads: usize,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            enabled: true,
            bm25_weight: 0.25,
            scan_interval_min: 10,
            max_index_gb: 10.0,
            extract_timeout_s: 30,
            embedding_model: MODEL_NAME.to_string(),
            embed_threads: 4,
        }
    }
}

/// The launcher's settings file: `DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then
/// `~/.config/dreamference/config.toml`, the first that exists.
pub fn config_file() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("DREAMFERENCE_CONFIG_PATH").filter(|p| !p.is_empty()) {
        return Some(PathBuf::from(path)).filter(|p| p.is_file());
    }
    [PathBuf::from("dreamference.toml"), user_home().join(".config/dreamference/config.toml")].into_iter().find(|p| p.is_file())
}

impl Settings {
    pub fn load() -> Settings {
        let table = config_file().and_then(|p| std::fs::read_to_string(p).ok()).and_then(|t| t.parse::<toml::Table>().ok()).unwrap_or_default();
        Settings::from_sources(&table, &|key| std::env::var(key).ok().filter(|v| !v.is_empty()))
    }

    /// The settings from a parsed config file and an environment lookup; the environment wins.
    pub fn from_sources(table: &toml::Table, env: &dyn Fn(&str) -> Option<String>) -> Settings {
        let mut s = Settings::default();
        let number = |key: &str, env_key: &str| -> Option<f64> {
            env(env_key).and_then(|v| v.trim().parse::<f64>().ok()).or_else(|| match table.get(key) {
                Some(toml::Value::Integer(i)) => Some(*i as f64),
                Some(toml::Value::Float(f)) => Some(*f),
                _ => None,
            })
        };
        let flag = |key: &str, env_key: &str| -> Option<bool> {
            env(env_key).map(|v| !matches!(v.to_lowercase().as_str(), "0" | "false" | "no" | "off")).or_else(|| table.get(key).and_then(toml::Value::as_bool))
        };
        if let Some(v) = flag("mightling_docs", "DREAMFERENCE_MIGHTLING_DOCS") {
            s.enabled = v;
        }
        if let Some(v) = number("docs_bm25_weight", "DREAMFERENCE_MIGHTLING_DOCS_BM25_WEIGHT").filter(|v| (0.0..=10.0).contains(v)) {
            s.bm25_weight = v;
        }
        if let Some(v) = number("docs_scan_interval_min", "DREAMFERENCE_MIGHTLING_DOCS_SCAN_INTERVAL_MIN").filter(|v| *v >= 1.0) {
            s.scan_interval_min = v as u64;
        }
        if let Some(v) = number("docs_max_index_gb", "DREAMFERENCE_MIGHTLING_DOCS_MAX_INDEX_GB").filter(|v| *v > 0.0) {
            s.max_index_gb = v;
        }
        if let Some(v) = number("docs_extract_timeout_s", "DREAMFERENCE_MIGHTLING_DOCS_EXTRACT_TIMEOUT_S").filter(|v| *v >= 1.0) {
            s.extract_timeout_s = v as u64;
        }
        if let Some(v) = env("DREAMFERENCE_MIGHTLING_DOCS_EMBEDDING_MODEL").or_else(|| table.get("docs_embedding_model").and_then(toml::Value::as_str).map(str::to_string)) {
            s.embedding_model = v;
        }
        s
    }
}

/// `XDG_RUNTIME_DIR`, else the temporary directory: where the host-wide locks live.
pub fn runtime_dir() -> PathBuf {
    match std::env::var_os("XDG_RUNTIME_DIR") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => std::env::temp_dir(),
    }
}

/// Night Shift's queue directory, whose `runner.lock` a night or benchmark run holds (§9).
pub fn night_dir() -> PathBuf {
    home().join("night")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_environment_wins_over_the_file_and_bad_values_are_ignored() {
        let table: toml::Table = "mightling_docs = false\ndocs_bm25_weight = 0.5\ndocs_scan_interval_min = 0\ndocs_extract_timeout_s = 12".parse().unwrap();
        let none = |_: &str| None;
        let s = Settings::from_sources(&table, &none);
        assert!(!s.enabled);
        assert_eq!(s.bm25_weight, 0.5);
        assert_eq!(s.scan_interval_min, 10, "0 minutes is refused");
        assert_eq!(s.extract_timeout_s, 12);
        let env = |key: &str| match key {
            "DREAMFERENCE_MIGHTLING_DOCS" => Some("1".to_string()),
            "DREAMFERENCE_MIGHTLING_DOCS_BM25_WEIGHT" => Some("0.25".to_string()),
            _ => None,
        };
        let s = Settings::from_sources(&table, &env);
        assert!(s.enabled);
        assert_eq!(s.bm25_weight, 0.25);
    }

    #[test]
    fn defaults_are_the_specs() {
        let s = Settings::default();
        assert_eq!((s.bm25_weight, s.scan_interval_min, s.extract_timeout_s), (0.25, 10, 30));
        assert_eq!(s.max_index_gb, 10.0);
        assert_eq!(CHUNK_TOKENS, 512);
    }
}
