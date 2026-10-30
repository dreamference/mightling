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

/// The share of a window a long unit's next window repeats (§7.2).
pub const CHUNK_OVERLAP: f64 = 0.15;

/// The chunker's own version: bumped only when a change to `chunk.rs` moves chunk boundaries.
pub const CHUNKER_VERSION: u32 = 2;

/// What decides where chunks end, recorded with a collection (§7.5): when it differs, the
/// documents are chunked again from their stored text and only chunks whose text changed are
/// embedded again. A new binary on its own never changes it.
pub fn chunking_key() -> String {
    format!("tokens={CHUNK_TOKENS} overlap={CHUNK_OVERLAP} chunker={CHUNKER_VERSION}")
}

/// The agent's home: `$CODEX_HOME`, else `~/.mightling`.
pub fn home() -> PathBuf {
    if let Some(dir) = std::env::var_os("CODEX_HOME").filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    user_home().join(".mightling")
}

/// The user's home directory.
pub fn user_home() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

/// Whether ling-docs may add the default collections and index without being asked (§5.1, §9):
/// only in the user's own agent home, `~/.mightling`. A scratch `CODEX_HOME` (a test, a check, an
/// audit, a Night Shift task) would otherwise index the real `~/Documents` and `~/Downloads` from
/// nothing into a throwaway database: found 2026-10-26, two such runs embedding all of
/// `~/Downloads` for half an hour each. `DREAMFERENCE_MIGHTLING_DOCS=1`, set explicitly, asks
/// for it in another home; `ling docs add` and `reindex` are asked for in any home.
pub fn indexes_unasked() -> bool {
    indexes_unasked_for(
        &home(),
        &user_home(),
        std::env::var("DREAMFERENCE_MIGHTLING_DOCS").ok().as_deref(),
    )
}

/// [`indexes_unasked`] for a given agent home, user home and `DREAMFERENCE_MIGHTLING_DOCS`.
pub fn indexes_unasked_for(agent_home: &Path, user_home: &Path, setting: Option<&str>) -> bool {
    if let Some(value) = setting.map(str::trim).filter(|v| !v.is_empty()) {
        if !matches!(value.to_lowercase().as_str(), "0" | "false" | "no" | "off") {
            return true;
        }
    }
    let canonical = |p: &Path| p.canonicalize().unwrap_or_else(|_| p.to_path_buf());
    canonical(agent_home) == canonical(&user_home.join(".mightling"))
}

/// `docs.toml`: the collections. User-level only, never read from a repository or a collection.
pub fn docs_toml() -> PathBuf {
    home().join("docs.toml")
}

/// The databases' directory, mode 700.
pub fn docs_dir() -> PathBuf {
    home().join("docs")
}

/// One collection's database. A PDF's ephemeral index (`pdf-<hash>`, PDF_SEARCH §3.4) is a
/// collection of one document whose database lives in [`pdf_cache_dir`].
pub fn db_path(collection: &str) -> PathBuf {
    if is_pdf_cache_name(collection) {
        return pdf_cache_dir().join(format!("{collection}.db"));
    }
    docs_dir().join(format!("{collection}.db"))
}

/// The ephemeral indexes of `pdf_search` (PDF_SEARCH §3.4), mode 700.
pub fn pdf_cache_dir() -> PathBuf {
    docs_dir().join("pdf")
}

/// Hex digits of a PDF's SHA-256 that name its cache entry.
pub const PDF_HASH_DIGITS: usize = 12;

/// Whether `name` is a cache entry's (`pdf-` and 12 lower-case hex digits): never a user's
/// collection.
pub fn is_pdf_cache_name(name: &str) -> bool {
    name.strip_prefix("pdf-").is_some_and(|h| {
        h.len() == PDF_HASH_DIGITS
            && h.chars()
                .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
    })
}

/// The directory of the running executable, symlinks resolved.
fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()?
        .canonicalize()
        .ok()?
        .parent()
        .map(Path::to_path_buf)
}

/// Where PDFium and ONNX Runtime are: `MIGHTLING_DOCS_LIB_DIR`, else `../lib/ling-docs` beside
/// the binary (`~/.local/share/dreamference/mightling/lib/ling-docs` for the installed one).
pub fn lib_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("MIGHTLING_DOCS_LIB_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    exe_dir()
        .map(|d| d.join("../lib/ling-docs"))
        .unwrap_or_else(|| PathBuf::from("lib/ling-docs"))
}

/// Where the embedding model is: `MIGHTLING_DOCS_MODEL_DIR`, else `../models/<MODEL_NAME>`
/// beside the binary. It holds `model.onnx` (the int8 export) and `tokenizer.json`.
pub fn model_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("MIGHTLING_DOCS_MODEL_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    exe_dir()
        .map(|d| d.join("../models").join(MODEL_NAME))
        .unwrap_or_else(|| PathBuf::from(MODEL_NAME))
}

/// The OCR models' folder (spec §20): `MIGHTLING_DOCS_OCR_DIR`, else `../models/rapidocr-3.9.2`
/// beside the binary. It holds the PP-OCR ONNX models and `tessdata/osd.traineddata`.
pub fn ocr_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("MIGHTLING_DOCS_OCR_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(dir);
    }
    let name = format!("rapidocr-{}", crate::ocr::RAPIDOCR_VERSION);
    exe_dir()
        .map(|d| d.join("../models").join(&name))
        .unwrap_or_else(|| PathBuf::from(name))
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
        (
            model.join("tokenizer.json"),
            "the embedding model's tokenizer",
        ),
    ] {
        if !path.is_file() {
            missing.push(what);
        }
    }
    (!missing.is_empty()).then(|| {
        format!(
            "{} not installed (run `ling-admin docs setup`)",
            missing.join(", ")
        )
    })
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
    /// `pdf_search` (PDF_SEARCH §3.9): the largest PDF it reads, in MiB.
    pub pdf_max_mb: u64,
    /// The pages of one PDF it indexes, from the first.
    pub pdf_max_pages: u32,
    /// One PDF's extraction time limit (collections use `extract_timeout_s` per file).
    pub pdf_timeout_s: u64,
    /// How long a call waits for the embedding before answering from what is there.
    pub pdf_wait_s: u64,
    /// The cache's size before the least recently used entries go.
    pub pdf_cache_mb: u64,
    /// Days an unused cache entry is kept.
    pub pdf_cache_days: u64,
    /// `docs_ocr`: scanned PDF pages and images read by OCR (§20).
    pub ocr: bool,
    /// `docs_ocr_max_pages`: the scanned pages of one document OCR reads, from the first.
    pub ocr_max_pages: u32,
    /// `docs_ocr_timeout_s`: one page's OCR time limit.
    pub ocr_timeout_s: u64,
    /// `docs_ocr_send_below`: a page read with a lower mean confidence counts as poorly read
    /// (the vision queue, not built, would take it, §15.7).
    pub ocr_send_below: f64,
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
            pdf_max_mb: 64,
            pdf_max_pages: 2000,
            pdf_timeout_s: 120,
            pdf_wait_s: 45,
            pdf_cache_mb: 1024,
            pdf_cache_days: 1,
            ocr: true,
            ocr_max_pages: 500,
            ocr_timeout_s: 120,
            ocr_send_below: crate::ocr::SEND_BELOW as f64,
        }
    }
}

/// The launcher's settings file: `DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then
/// `~/.config/dreamference/config.toml`, the first that exists.
pub fn config_file() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("DREAMFERENCE_CONFIG_PATH").filter(|p| !p.is_empty()) {
        return Some(PathBuf::from(path)).filter(|p| p.is_file());
    }
    [
        PathBuf::from("dreamference.toml"),
        user_home().join(".config/dreamference/config.toml"),
    ]
    .into_iter()
    .find(|p| p.is_file())
}

impl Settings {
    /// The wait between two change scans.
    pub fn scan_interval(&self) -> std::time::Duration {
        std::time::Duration::from_secs(self.scan_interval_min.saturating_mul(60))
    }

    pub fn load() -> Settings {
        let table = config_file()
            .and_then(|p| std::fs::read_to_string(p).ok())
            .and_then(|t| t.parse::<toml::Table>().ok())
            .unwrap_or_default();
        Settings::from_sources(&table, &|key| {
            std::env::var(key).ok().filter(|v| !v.is_empty())
        })
    }

    /// The settings from a parsed config file and an environment lookup; the environment wins.
    pub fn from_sources(table: &toml::Table, env: &dyn Fn(&str) -> Option<String>) -> Settings {
        let mut s = Settings::default();
        let number = |key: &str, env_key: &str| -> Option<f64> {
            env(env_key)
                .and_then(|v| v.trim().parse::<f64>().ok())
                .or_else(|| match table.get(key) {
                    Some(toml::Value::Integer(i)) => Some(*i as f64),
                    Some(toml::Value::Float(f)) => Some(*f),
                    _ => None,
                })
        };
        let flag = |key: &str, env_key: &str| -> Option<bool> {
            env(env_key)
                .map(|v| {
                    !matches!(
                        v.trim().to_lowercase().as_str(),
                        "0" | "false" | "no" | "off"
                    )
                })
                .or_else(|| table.get(key).and_then(toml::Value::as_bool))
        };
        if let Some(v) = flag("mightling_docs", "DREAMFERENCE_MIGHTLING_DOCS") {
            s.enabled = v;
        }
        if let Some(v) = number(
            "docs_bm25_weight",
            "DREAMFERENCE_MIGHTLING_DOCS_BM25_WEIGHT",
        )
        .filter(|v| (0.0..=10.0).contains(v))
        {
            s.bm25_weight = v;
        }
        if let Some(v) = number(
            "docs_scan_interval_min",
            "DREAMFERENCE_MIGHTLING_DOCS_SCAN_INTERVAL_MIN",
        )
        .filter(|v| *v >= 1.0)
        {
            s.scan_interval_min = v as u64;
        }
        if let Some(v) = number(
            "docs_max_index_gb",
            "DREAMFERENCE_MIGHTLING_DOCS_MAX_INDEX_GB",
        )
        .filter(|v| *v > 0.0)
        {
            s.max_index_gb = v;
        }
        if let Some(v) = number(
            "docs_extract_timeout_s",
            "DREAMFERENCE_MIGHTLING_DOCS_EXTRACT_TIMEOUT_S",
        )
        .filter(|v| *v >= 1.0)
        {
            s.extract_timeout_s = v as u64;
        }
        if let Some(v) = number("docs_pdf_max_mb", "DREAMFERENCE_MIGHTLING_DOCS_PDF_MAX_MB")
            .filter(|v| *v >= 1.0)
        {
            s.pdf_max_mb = v as u64;
        }
        if let Some(v) = number(
            "docs_pdf_max_pages",
            "DREAMFERENCE_MIGHTLING_DOCS_PDF_MAX_PAGES",
        )
        .filter(|v| *v >= 1.0)
        {
            s.pdf_max_pages = v.min(u32::MAX as f64) as u32;
        }
        if let Some(v) = number(
            "docs_pdf_timeout_s",
            "DREAMFERENCE_MIGHTLING_DOCS_PDF_TIMEOUT_S",
        )
        .filter(|v| *v >= 1.0)
        {
            s.pdf_timeout_s = v as u64;
        }
        if let Some(v) = number("docs_pdf_wait_s", "DREAMFERENCE_MIGHTLING_DOCS_PDF_WAIT_S")
            .filter(|v| *v >= 0.0)
        {
            s.pdf_wait_s = v as u64;
        }
        if let Some(v) = number(
            "docs_pdf_cache_mb",
            "DREAMFERENCE_MIGHTLING_DOCS_PDF_CACHE_MB",
        )
        .filter(|v| *v >= 0.0)
        {
            s.pdf_cache_mb = v as u64;
        }
        if let Some(v) = number(
            "docs_pdf_cache_days",
            "DREAMFERENCE_MIGHTLING_DOCS_PDF_CACHE_DAYS",
        )
        .filter(|v| *v >= 0.0)
        {
            s.pdf_cache_days = v as u64;
        }
        if let Some(v) = flag("docs_ocr", "DREAMFERENCE_MIGHTLING_DOCS_OCR") {
            s.ocr = v;
        }
        if let Some(v) = number(
            "docs_ocr_max_pages",
            "DREAMFERENCE_MIGHTLING_DOCS_OCR_MAX_PAGES",
        )
        .filter(|v| *v >= 0.0)
        {
            s.ocr_max_pages = v.min(u32::MAX as f64) as u32;
        }
        if let Some(v) = number(
            "docs_ocr_timeout_s",
            "DREAMFERENCE_MIGHTLING_DOCS_OCR_TIMEOUT_S",
        )
        .filter(|v| *v >= 1.0)
        {
            s.ocr_timeout_s = v as u64;
        }
        if let Some(v) = number(
            "docs_ocr_send_below",
            "DREAMFERENCE_MIGHTLING_DOCS_OCR_SEND_BELOW",
        )
        .filter(|v| (0.0..=1.0).contains(v))
        {
            s.ocr_send_below = v;
        }
        if let Some(v) = env("DREAMFERENCE_MIGHTLING_DOCS_EMBEDDING_MODEL").or_else(|| {
            table
                .get("docs_embedding_model")
                .and_then(toml::Value::as_str)
                .map(str::to_string)
        }) {
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
    fn a_scan_interval_too_long_to_count_in_seconds_is_the_longest_wait() {
        let s = Settings {
            scan_interval_min: u64::MAX / 2,
            ..Settings::default()
        };
        assert_eq!(s.scan_interval(), std::time::Duration::from_secs(u64::MAX));
        assert_eq!(
            Settings::default().scan_interval(),
            std::time::Duration::from_secs(600)
        );
    }

    #[test]
    fn a_switch_written_with_spaces_around_it_still_switches_off() {
        let table = toml::Table::new();
        for value in [" off", "off ", " 0 ", "False\n"] {
            let env = |key: &str| (key == "DREAMFERENCE_MIGHTLING_DOCS").then(|| value.to_string());
            assert!(
                !Settings::from_sources(&table, &env).enabled,
                "{value:?} turns ling-docs off"
            );
        }
    }

    #[test]
    fn only_the_users_own_home_indexes_unasked() {
        let dir = tempfile::tempdir().unwrap();
        let user = dir.path().join("user");
        std::fs::create_dir_all(user.join(".mightling")).unwrap();
        assert!(indexes_unasked_for(&user.join(".mightling"), &user, None));
        assert!(
            indexes_unasked_for(&user.join(".mightling/"), &user, Some("")),
            "the same folder, written another way"
        );
        let scratch = dir.path().join("scratch/codexhome");
        assert!(
            !indexes_unasked_for(&scratch, &user, None),
            "a scratch CODEX_HOME beside the real HOME"
        );
        assert!(!indexes_unasked_for(&scratch, &user, Some("0")));
        assert!(
            indexes_unasked_for(&scratch, &user, Some("1")),
            "asked for explicitly"
        );
        assert!(indexes_unasked_for(&scratch, &user, Some("on")));
    }

    #[test]
    fn every_word_for_off_keeps_a_scratch_home_from_indexing() {
        let dir = tempfile::tempdir().unwrap();
        let user = dir.path().join("user");
        let scratch = dir.path().join("scratch/codexhome");
        for off in ["0", "false", "FALSE", "no", "No", "off", " off "] {
            assert!(!indexes_unasked_for(&scratch, &user, Some(off)), "{off:?}");
        }
    }

    #[test]
    fn defaults_are_the_specs() {
        let s = Settings::default();
        assert_eq!(
            (s.bm25_weight, s.scan_interval_min, s.extract_timeout_s),
            (0.25, 10, 30)
        );
        assert_eq!(s.max_index_gb, 10.0);
        assert_eq!(CHUNK_TOKENS, 512);
        assert_eq!(
            (
                s.pdf_max_mb,
                s.pdf_max_pages,
                s.pdf_timeout_s,
                s.pdf_wait_s,
                s.pdf_cache_mb,
                s.pdf_cache_days
            ),
            (64, 2000, 120, 45, 1024, 1)
        );
    }

    #[test]
    fn a_pdf_cache_entry_has_its_own_folder_and_name_pattern() {
        assert!(is_pdf_cache_name("pdf-3f2a9c01d4e7"));
        for name in [
            "pdf-papers",
            "pdf-3F2A9C01D4E7",
            "pdf-3f2a9c01d4e",
            "documents",
        ] {
            assert!(!is_pdf_cache_name(name), "{name}");
        }
        assert_eq!(
            db_path("pdf-3f2a9c01d4e7"),
            pdf_cache_dir().join("pdf-3f2a9c01d4e7.db")
        );
        assert_eq!(db_path("pdf-papers"), docs_dir().join("pdf-papers.db"));
    }
}
