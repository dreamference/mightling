//! `pdf_search` (specs/DREAMFERENCE_PDF_SEARCH.md §3): the agent searches inside a PDF it has not
//! indexed in a collection, a URL, a Drive file or a file in its workspace, and gets passages
//! with their pages, read back with `docs_read`.
//!
//! Nothing here reads, ranks or stores PDFs on its own: a PDF becomes a collection of one
//! document (`pdf-<12 hex digits of its SHA-256>`, its database in `docs/pdf/`), built by
//! [`index::index_file`] through the collections' own extractor scope, admission, chunker and
//! embedder, and searched by [`Searcher`] like any collection. This module only decides where
//! the bytes come from and whether they may be read:
//! - a URL is downloaded by `ling-fetch --download` (this crate opens no socket), and never at
//!   `/airgapped on`, asked as the Drive app asks it (the configured level, or a seal its parent
//!   process, the session, wrote);
//! - a local path must lie under the session's working directory (Codex's
//!   `codex/sandbox-state-meta`, or this process's own) or a collection's root, and outside the
//!   folders the index never reads; the server runs outside the command sandbox, so it applies
//!   that rule itself;
//! - a Drive file waits for the Drive app's download (§3.3.2).
//!
//! The bytes are copied into a private temporary file, checked (`%PDF-`, the size cap), hashed
//! and handed to the extractor; the cache keeps text only and is evicted by size and age.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime};

use anyhow::{bail, Context, Result};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::collections::{secret_paths, Collection};
use crate::config::{self, Settings};
use crate::embed::Embed;
use crate::index::{self, Background, Env, FileIndexed, Outcome};
use crate::search::{Filters, Searcher};
use crate::store;

/// Passages by default, and at most.
pub const DEFAULT_K: usize = 6;
pub const MAX_K: usize = 12;

/// The download's wall-clock limit (`ling-fetch`'s own limit is per read).
pub const FETCH_TIMEOUT: Duration = Duration::from_secs(120);

/// The capability whose `_meta` gives each call the session's working directory.
pub const SANDBOX_STATE_META: &str = "codex/sandbox-state-meta";

/// What `ling-fetch` and the apps say at `/airgapped on`.
pub const AIRGAPPED_ON: &str = "Web access is off in this session (/airgapped on). Only the user can change that, with /airgapped. A PDF in the workspace can still be searched.";

/// Where a PDF comes from.
#[derive(Debug, Clone, PartialEq)]
pub enum Source {
    Url(String),
    Drive(String),
    Local(String),
}

impl Source {
    pub fn parse(text: &str) -> Result<Source> {
        let text = text.trim();
        if text.is_empty() {
            bail!("pdf_search needs a source: a URL, drive:<file id> or a path");
        }
        let lower = text.to_ascii_lowercase();
        if lower.starts_with("http://") || lower.starts_with("https://") {
            return Ok(Source::Url(text.to_string()));
        }
        if let Some(id) = lower.strip_prefix("drive:").map(|_| text[6..].trim()) {
            if id.is_empty() {
                bail!("drive: needs a file id, as in drive:<file id>");
            }
            return Ok(Source::Drive(id.to_string()));
        }
        if lower.contains("://") && !lower.starts_with("file://") {
            bail!("only http and https URLs, drive:<file id> and local paths are sources");
        }
        if lower.starts_with("file://") {
            return Ok(Source::Local(percent_decode(&text[7..])));
        }
        Ok(Source::Local(text.to_string()))
    }
}

/// The session's working directory from a call's `_meta` (`sandbox_cwd`, a `file:` URI).
pub fn sandbox_cwd(meta: &Value) -> Option<PathBuf> {
    let uri = meta.get(SANDBOX_STATE_META)?.get("sandbox_cwd")?.as_str()?;
    let path = match uri.strip_prefix("file://") {
        Some(rest) => percent_decode(rest.trim_start_matches("localhost")),
        None => uri.to_string(),
    };
    let path = PathBuf::from(path);
    path.is_absolute().then_some(path)
}

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Some(byte) = std::str::from_utf8(&bytes[i + 1..i + 3])
                .ok()
                .and_then(|h| u8::from_str_radix(h, 16).ok())
            {
                out.push(byte);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Resolves a local source under the rule of §3.3.3: under `cwd` or a collection's root, outside
/// the folders never read. Returns the file, resolved, or why not.
pub fn resolve_local(
    given: &str,
    cwd: &Path,
    collections: &[Collection],
    user_home: &Path,
) -> std::result::Result<PathBuf, String> {
    let expanded = match given.strip_prefix("~/") {
        Some(rest) => user_home.join(rest),
        None => PathBuf::from(given),
    };
    let path = if expanded.is_absolute() {
        expanded
    } else {
        cwd.join(expanded)
    };
    let resolved = path
        .canonicalize()
        .map_err(|_| format!("{} does not exist", path.display()))?;
    let home = user_home
        .canonicalize()
        .unwrap_or_else(|_| user_home.to_path_buf());
    if let Some(secret) = secret_paths(&home)
        .into_iter()
        .find(|s| resolved.starts_with(s))
    {
        return Err(format!(
            "{} is inside {}, which is never read",
            resolved.display(),
            secret.display()
        ));
    }
    let mut roots: Vec<PathBuf> = cwd.canonicalize().into_iter().collect();
    roots.extend(
        collections
            .iter()
            .filter(|c| c.enabled)
            .filter_map(|c| c.root.canonicalize().ok()),
    );
    if !roots.iter().any(|r| resolved.starts_with(r)) {
        return Err(format!(
            "{} is outside this session's workspace ({}): copy it into the workspace first (cp in a shell command, which the sandbox allows or refuses)",
            resolved.display(),
            cwd.display()
        ));
    }
    if !resolved.is_file() {
        return Err(format!("{} is not a file", resolved.display()));
    }
    Ok(resolved)
}

/// Whether the session this server serves may not use the network: the configured level, as the
/// shared reader resolves it (environment, then the configuration files, strictest wins, and what
/// cannot be read is `on`), or a seal written by `parent` (AIRGAPPED §6.5; the same question
/// `ling-rs/apps/src/lib.rs::air_gapped_for` asks).
pub fn air_gapped(parent: u32) -> bool {
    ling_airgapped::resolve(&[]).level == ling_airgapped::Level::On
        || ling_airgapped::sealed_by(parent)
}

/// `ling-fetch`: `MIGHTLING_FETCH_BIN`, else beside this binary (both are installed in one `bin/`).
pub fn fetch_binary() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("MIGHTLING_FETCH_BIN").filter(|p| !p.is_empty()) {
        return Some(PathBuf::from(path));
    }
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    let path = exe.parent()?.join("ling-fetch");
    path.is_file().then_some(path)
}

/// Downloads `url` into `dest` with `ling-fetch --download`, within the size cap and
/// [`FETCH_TIMEOUT`]. Returns the URL after redirects.
pub fn download(url: &str, dest: &Path, max_bytes: u64) -> std::result::Result<String, String> {
    let Some(binary) = fetch_binary() else {
        return Err("downloading needs ling-fetch, which is not installed beside ling-docs".into());
    };
    let mut child = Command::new(&binary)
        .args([
            "--download",
            &dest.to_string_lossy(),
            "--max-bytes",
            &max_bytes.to_string(),
            url,
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("cannot run {}: {e}", binary.display()))?;
    let started = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() > FETCH_TIMEOUT => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = std::fs::remove_file(dest);
                return Err(format!(
                    "the download took longer than {} s",
                    FETCH_TIMEOUT.as_secs()
                ));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
            Err(e) => return Err(e.to_string()),
        }
    };
    let mut out = String::new();
    if let Some(mut stdout) = child.stdout.take() {
        let _ = stdout.read_to_string(&mut out);
    }
    let line = out.lines().last().unwrap_or("").trim();
    if !status.success() {
        let _ = std::fs::remove_file(dest);
        let why = line.trim_start_matches("❌").trim();
        return Err(if why.is_empty() {
            "the download failed".to_string()
        } else {
            why.to_string()
        });
    }
    let info: Value = serde_json::from_str(line).unwrap_or_default();
    Ok(info["final_url"].as_str().unwrap_or(url).to_string())
}

/// Copies `from` into `dest` (mode 600), at most `max_bytes`, and returns the SHA-256 in hex.
/// The file must start like a PDF.
pub fn copy_checked(
    from: &Path,
    dest: &Path,
    max_bytes: u64,
) -> std::result::Result<String, String> {
    let size = std::fs::metadata(from).map_err(|e| e.to_string())?.len();
    if size == 0 {
        return Err("the file is empty".into());
    }
    if size > max_bytes {
        return Err(format!(
            "larger than {} MiB ({} MiB): over docs_pdf_max_mb",
            max_bytes >> 20,
            size >> 20
        ));
    }
    let mut input = std::fs::File::open(from).map_err(|e| e.to_string())?;
    let mut head = vec![0u8; 1024];
    let n = read_full(&mut input, &mut head).map_err(|e| e.to_string())?;
    head.truncate(n);
    if !head.windows(5).any(|w| w == b"%PDF-") {
        return Err("not a PDF (no %PDF- header)".into());
    }
    let mut output = private_file(dest).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    hasher.update(&head);
    output.write_all(&head).map_err(|e| e.to_string())?;
    let mut buffer = vec![0u8; 1 << 16];
    let mut total = head.len() as u64;
    loop {
        let n = input.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        total += n as u64;
        if total > max_bytes {
            return Err(format!(
                "larger than {} MiB: over docs_pdf_max_mb",
                max_bytes >> 20
            ));
        }
        hasher.update(&buffer[..n]);
        output.write_all(&buffer[..n]).map_err(|e| e.to_string())?;
    }
    Ok(hex(&hasher.finalize()))
}

fn read_full(input: &mut std::fs::File, buffer: &mut [u8]) -> std::io::Result<usize> {
    let mut read = 0;
    while read < buffer.len() {
        match input.read(&mut buffer[read..])? {
            0 => break,
            n => read += n,
        }
    }
    Ok(read)
}

fn private_file(path: &Path) -> std::io::Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// The cache entry's name for a SHA-256.
pub fn entry_name(sha: &str) -> String {
    format!("pdf-{}", &sha[..config::PDF_HASH_DIGITS])
}

/// Marks an entry used now (eviction goes by the database's modification time).
pub fn touch(name: &str) {
    let path = config::db_path(name);
    if let Ok(file) = std::fs::File::options().append(true).open(&path) {
        let _ = file.set_modified(SystemTime::now());
    }
}

/// The cache's entries: name, bytes (database and journal), last use.
pub fn entries() -> Vec<(String, u64, SystemTime)> {
    let mut out = Vec::new();
    for entry in std::fs::read_dir(config::pdf_cache_dir())
        .into_iter()
        .flatten()
        .flatten()
    {
        let file = entry.file_name().to_string_lossy().into_owned();
        let Some(name) = file.strip_suffix(".db") else {
            continue;
        };
        if !config::is_pdf_cache_name(name) {
            continue;
        }
        let Ok(meta) = entry.metadata() else { continue };
        let journal = std::fs::metadata(entry.path().with_extension("db-journal"))
            .map(|m| m.len())
            .unwrap_or(0);
        out.push((
            name.to_string(),
            meta.len() + journal,
            meta.modified().unwrap_or(SystemTime::UNIX_EPOCH),
        ));
    }
    out
}

/// Removes entries unused for `days`, then the least recently used until the cache is under
/// `max_mb`, never one named in `keep` or one a session is building; and run folders a dead
/// process left. Returns how many
/// entries went.
pub fn evict(max_mb: u64, days: u64, keep: &[String]) -> usize {
    let dir = config::pdf_cache_dir();
    for entry in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let pid = name.rsplit('-').next().and_then(|p| p.parse::<u32>().ok());
        if (name.starts_with("run-") || name.starts_with("fetch-"))
            && pid.is_some_and(|p| !Path::new(&format!("/proc/{p}")).exists())
        {
            let _ = std::fs::remove_dir_all(entry.path());
        }
    }
    let mut list = entries();
    list.sort_by_key(|(_, _, used)| *used);
    let now = SystemTime::now();
    let mut total: u64 = list.iter().map(|(_, size, _)| size).sum();
    let mut removed = 0;
    for (name, size, used) in list {
        // Named by the caller, or being built or embedded by a session now (its lock is held).
        if keep.contains(&name) || in_use(&name) {
            continue;
        }
        let stale = now
            .duration_since(used)
            .map(|age| age > Duration::from_secs(days * 86400))
            .unwrap_or(false);
        if stale || total > max_mb << 20 {
            remove_entry(&name);
            total = total.saturating_sub(size);
            removed += 1;
        }
    }
    removed
}

/// Whether a process holds the entry's lock (it is indexing or embedding it).
fn in_use(name: &str) -> bool {
    let path = config::pdf_cache_dir().join(format!("{name}.lock"));
    match std::fs::File::open(&path) {
        Ok(file) => crate::host::flock(&file, false).is_err(),
        Err(_) => false,
    }
}

fn remove_entry(name: &str) {
    let db = config::db_path(name);
    for suffix in ["", "-journal"] {
        let _ = std::fs::remove_file(format!("{}{suffix}", db.display()));
    }
    let _ = std::fs::remove_file(config::pdf_cache_dir().join(format!("{name}.lock")));
}

/// A title for a source before the PDF names its own: the file name without `.pdf`.
pub fn title_of(label: &str) -> String {
    let last = label
        .split(['?', '#'])
        .next()
        .unwrap_or(label)
        .trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or(label);
    let stem = last
        .strip_suffix(".pdf")
        .or_else(|| last.strip_suffix(".PDF"))
        .unwrap_or(last);
    percent_decode(stem).replace(['_', '-'], " ")
}

/// The collection a cache entry is searched and read as: root empty, so a document's path is
/// its source.
pub fn collection(name: &str) -> Collection {
    Collection::new(name, PathBuf::new())
}

/// How the search server builds an [`Env`] for a PDF run, so tests can swap the host.
pub type EnvFor<'a> = &'a dyn Fn(&Settings) -> Result<Env<'static>>;

/// `pdf_search`'s state for a session: the URLs already downloaded and the embeddings running.
#[derive(Default)]
pub struct PdfSearch {
    urls: HashMap<String, (String, String)>,
    running: Vec<(String, Background)>,
    swept: bool,
}

impl PdfSearch {
    /// Collects finished background embeddings.
    pub fn reap(&mut self) {
        self.running.retain_mut(|(_, b)| !b.finished());
    }

    /// Stops what still runs (the session is ending).
    pub fn stop(&mut self) {
        for (_, background) in self.running.drain(..) {
            background.stop();
        }
    }

    /// One `pdf_search` call. `parent` is the session's pid, `cwd` the session's working
    /// directory.
    #[allow(clippy::too_many_arguments)]
    pub fn call(
        &mut self,
        args: &Value,
        cwd: &Path,
        parent: u32,
        collections: &[Collection],
        settings: &Settings,
        env_for: EnvFor,
        embedder: Option<&dyn Embed>,
    ) -> Result<String> {
        self.reap();
        if !self.swept {
            self.swept = true;
            evict(settings.pdf_cache_mb, settings.pdf_cache_days, &[]);
        }
        let source = Source::parse(args["source"].as_str().unwrap_or(""))?;
        let query = args["query"].as_str().unwrap_or("").trim().to_string();
        if query.is_empty() {
            bail!("pdf_search needs a query");
        }
        let k = args["k"]
            .as_u64()
            .map(|k| k as usize)
            .unwrap_or(DEFAULT_K)
            .clamp(1, MAX_K);
        let max_bytes = settings.pdf_max_mb << 20;
        let dir = config::pdf_cache_dir();
        store::create_private_dir(&dir)?;
        let work = dir.join(format!("fetch-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&work);
        store::create_private_dir(&work)?;
        let copy = work.join("source.pdf");
        // A URL is remembered once its index exists, so a failed one is downloaded again.
        let mut remember: Option<String> = None;
        let result = (|| -> Result<String> {
            let (name, label) = match &source {
                Source::Drive(_) => bail!("Drive PDFs cannot be searched yet: drive_read refuses PDFs (PDF_SEARCH §3.3.2)."),
                Source::Url(url) => {
                    if let Some((name, label)) = self.urls.get(url).filter(|(name, _)| config::db_path(name).is_file()) {
                        (name.clone(), label.clone())
                    } else {
                        if air_gapped(parent) {
                            bail!("{AIRGAPPED_ON}");
                        }
                        let fetched = work.join("download.pdf");
                        let final_url = download(url, &fetched, max_bytes).map_err(anyhow::Error::msg)?;
                        let sha = copy_checked(&fetched, &copy, max_bytes).map_err(anyhow::Error::msg)?;
                        remember = Some(url.clone());
                        (entry_name(&sha), final_url)
                    }
                }
                Source::Local(given) => {
                    let file = resolve_local(given, cwd, collections, &config::user_home()).map_err(anyhow::Error::msg)?;
                    let sha = copy_checked(&file, &copy, max_bytes).map_err(anyhow::Error::msg)?;
                    (entry_name(&sha), file.to_string_lossy().into_owned())
                }
            };
            let building = self.running.iter().any(|(n, _)| *n == name);
            if !building {
                let mut env_settings = settings.clone();
                env_settings.extract_timeout_s = settings.pdf_timeout_s;
                let env = env_for(&env_settings)?;
                if !copy.is_file() && !index_ready(&name) {
                    // A remembered URL whose entry lost its text: fetch again next time.
                    self.urls.retain(|_, (n, _)| *n != name);
                    bail!("this PDF's index was removed from the cache: ask again to download it once more");
                }
                let file = if copy.is_file() {
                    copy.clone()
                } else {
                    PathBuf::from("/nonexistent")
                };
                match index::index_file(
                    &env,
                    &name,
                    &label,
                    &title_of(&label),
                    &file,
                    Some(settings.pdf_max_pages),
                    Duration::from_secs(settings.pdf_wait_s),
                )? {
                    FileIndexed::Ready(Some(background)) => {
                        self.running.push((name.clone(), background))
                    }
                    FileIndexed::Ready(None) => {}
                    FileIndexed::Stopped(Outcome::Failed(why)) => bail!("failed: {why}"),
                    FileIndexed::Stopped(other) => bail!("{}", other.status()),
                }
            }
            if let Some(url) = remember.take() {
                self.urls.insert(url, (name.clone(), label.clone()));
            }
            touch(&name);
            let keep: Vec<String> = self
                .running
                .iter()
                .map(|(n, _)| n.clone())
                .chain([name.clone()])
                .collect();
            evict(settings.pdf_cache_mb, settings.pdf_cache_days, &keep);
            render(&name, &query, k, settings, embedder)
        })();
        let _ = std::fs::remove_dir_all(&work);
        result
    }
}

/// Whether an entry's text is in its database.
fn index_ready(name: &str) -> bool {
    store::open_ro(&config::db_path(name))
        .ok()
        .flatten()
        .is_some_and(|conn| {
            conn.query_row(
                "SELECT count(*) FROM documents WHERE status = 'ok'",
                [],
                |r| r.get::<_, i64>(0),
            )
            .unwrap_or(0)
                > 0
        })
}

/// The answer: a header on the document, the passages as `docs_search` renders them, and how to
/// read a page.
pub fn render(
    name: &str,
    query: &str,
    k: usize,
    settings: &Settings,
    embedder: Option<&dyn Embed>,
) -> Result<String> {
    let conn = store::open_ro(&config::db_path(name))?.context("the PDF's index is missing")?;
    let (id, label, title, pages, needs_ocr, status): (
        i64,
        String,
        String,
        Option<u32>,
        String,
        String,
    ) = conn.query_row(
        "SELECT id, path, title, pages, needs_ocr, status FROM documents ORDER BY id LIMIT 1",
        [],
        |r| {
            Ok((
                r.get(0)?,
                r.get(1)?,
                r.get(2)?,
                r.get(3)?,
                r.get(4)?,
                r.get(5)?,
            ))
        },
    )?;
    if status != "ok" {
        bail!("{status}");
    }
    let pages_read: Option<u32> =
        store::get_meta(&conn, "pages_read")?.and_then(|v| v.parse().ok());
    let chunks: i64 = conn.query_row("SELECT count(*) FROM chunks", [], |r| r.get(0))?;
    drop(conn);
    let doc_id = format!("{name}:{id}");
    let mut header = format!("PDF: {label}");
    if !title.is_empty() {
        header.push_str(&format!(" — \"{}\"", title.replace(['\n', '"'], " ")));
    }
    if let Some(n) = pages {
        header.push_str(&format!(", {n} pages"));
        if let Some(read) = pages_read {
            header.push_str(&format!(" (pages 1-{read} indexed, docs_pdf_max_pages)"));
        }
    }
    header.push('.');
    let scanned: Vec<String> = needs_ocr
        .split(',')
        .filter(|p| !p.is_empty())
        .map(|p| format!("p.{p}"))
        .collect();
    if chunks == 0 {
        return Ok(format!("{header}\nThis PDF has no text layer (a scan, or only images): it cannot be searched, because OCR is not offered yet."));
    }
    if !scanned.is_empty() {
        let shown: Vec<&str> = scanned.iter().take(20).map(String::as_str).collect();
        let more = if scanned.len() > shown.len() {
            format!(" and {} more", scanned.len() - shown.len())
        } else {
            String::new()
        };
        header.push_str(&format!("\nNo text layer (scanned?) on {}{more}: OCR is not offered yet, so those pages are not searched.", shown.join(", ")));
    }
    // One document: every hit is a neighbour of another, and merging would turn k passages into
    // a few long ones with wide locators. Each passage stays a chunk, about a page.
    let mut searcher = Searcher::default();
    searcher.merge_adjacent = false;
    searcher.refresh(&[collection(name)])?;
    let answer = searcher.search(
        query,
        k,
        &Filters::default(),
        embedder,
        settings.bm25_weight,
    )?;
    let body = crate::mcp::render_search(&answer);
    let first_page = answer.hits.iter().find_map(|h| h.page).unwrap_or(1);
    Ok(format!(
        "{header}\n\n{body}\n\nRead a page: docs_read(doc_id=\"{doc_id}\", locator=\"p.{first_page}\"). Cite as {label}, p.N.",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_drive_source_needs_a_file_id() {
        assert!(Source::parse("drive:").is_err());
        assert!(Source::parse("drive:   ").is_err());
        assert!(matches!(Source::parse("drive:abc"), Ok(Source::Drive(id)) if id == "abc"));
    }

    #[test]
    fn sources_are_told_apart() {
        assert_eq!(
            Source::parse("https://arxiv.org/pdf/1706.03762").unwrap(),
            Source::Url("https://arxiv.org/pdf/1706.03762".into())
        );
        assert_eq!(
            Source::parse("HTTP://x.org/a.pdf").unwrap(),
            Source::Url("HTTP://x.org/a.pdf".into())
        );
        assert_eq!(
            Source::parse("drive:1AbC").unwrap(),
            Source::Drive("1AbC".into())
        );
        assert_eq!(
            Source::parse("./docs/a.pdf").unwrap(),
            Source::Local("./docs/a.pdf".into())
        );
        assert_eq!(
            Source::parse("file:///tmp/a%20b.pdf").unwrap(),
            Source::Local("/tmp/a b.pdf".into())
        );
        assert!(Source::parse("ftp://x/a.pdf").is_err());
        assert!(Source::parse("  ").is_err());
    }

    #[test]
    fn the_working_directory_comes_from_the_calls_meta() {
        let meta = serde_json::json!({ SANDBOX_STATE_META: { "sandbox_cwd": "file:///home/u/My%20Thesis" } });
        assert_eq!(sandbox_cwd(&meta), Some(PathBuf::from("/home/u/My Thesis")));
        assert_eq!(sandbox_cwd(&serde_json::json!({})), None);
        assert_eq!(
            sandbox_cwd(&serde_json::json!({ SANDBOX_STATE_META: { "sandbox_cwd": "relative" } })),
            None
        );
    }

    #[test]
    fn a_local_path_must_be_in_the_workspace_or_a_collection_and_never_in_a_secret_folder() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().canonicalize().unwrap();
        let work = home.join("work");
        let docs = home.join("Documents");
        let ssh = home.join(".ssh");
        for d in [&work, &docs, &ssh, &home.join("elsewhere")] {
            std::fs::create_dir_all(d).unwrap();
        }
        for f in [
            work.join("a.pdf"),
            docs.join("b.pdf"),
            ssh.join("c.pdf"),
            home.join("elsewhere/d.pdf"),
        ] {
            std::fs::write(f, b"%PDF-1.4").unwrap();
        }
        let collections = vec![Collection::new("documents", docs.clone())];
        assert_eq!(
            resolve_local("a.pdf", &work, &collections, &home).unwrap(),
            work.join("a.pdf")
        );
        assert_eq!(
            resolve_local("~/Documents/b.pdf", &work, &collections, &home).unwrap(),
            docs.join("b.pdf")
        );
        let outside = resolve_local("../elsewhere/d.pdf", &work, &collections, &home).unwrap_err();
        assert!(
            outside.contains("outside this session's workspace") && outside.contains("cp"),
            "{outside}"
        );
        // The home folder as the workspace still never opens ~/.ssh.
        assert!(resolve_local(".ssh/c.pdf", &home, &collections, &home)
            .unwrap_err()
            .contains("never read"));
        assert!(resolve_local("missing.pdf", &work, &collections, &home)
            .unwrap_err()
            .contains("does not exist"));
        // A link out of the workspace is judged where it leads.
        std::os::unix::fs::symlink(home.join("elsewhere/d.pdf"), work.join("link.pdf")).unwrap();
        assert!(resolve_local("link.pdf", &work, &collections, &home).is_err());
    }

    #[test]
    fn a_workspace_reached_through_a_link_is_judged_where_it_leads() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().canonicalize().unwrap().join("home");
        let work = home.join("work");
        std::fs::create_dir_all(&work).unwrap();
        std::fs::write(work.join("a.pdf"), b"%PDF-1.4").unwrap();
        let work_link = dir.path().join("work-link");
        std::os::unix::fs::symlink(&work, &work_link).unwrap();
        assert_eq!(
            resolve_local("a.pdf", &work_link, &[], &home).unwrap(),
            work.join("a.pdf")
        );
    }

    #[test]
    fn only_a_pdf_within_the_cap_is_copied_and_it_is_hashed() {
        let dir = tempfile::tempdir().unwrap();
        let pdf = dir.path().join("a.pdf");
        std::fs::write(&pdf, b"%PDF-1.4\nhello").unwrap();
        let sha = copy_checked(&pdf, &dir.path().join("copy"), 1 << 20).unwrap();
        assert_eq!(sha.len(), 64);
        assert_eq!(
            std::fs::read(dir.path().join("copy")).unwrap(),
            b"%PDF-1.4\nhello"
        );
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(dir.path().join("copy"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        std::fs::write(dir.path().join("html"), b"<html>404</html>").unwrap();
        assert!(
            copy_checked(&dir.path().join("html"), &dir.path().join("c2"), 1 << 20)
                .unwrap_err()
                .contains("not a PDF")
        );
        assert!(copy_checked(&pdf, &dir.path().join("c3"), 4)
            .unwrap_err()
            .contains("larger than"));
        assert_eq!(entry_name(&sha), format!("pdf-{}", &sha[..12]));
        assert!(config::is_pdf_cache_name(&entry_name(&sha)));
    }

    #[test]
    fn the_air_gap_is_the_shared_reader_s() {
        // The reader is ling-rs/airgapped, whose own tests and cases.json pin the rules; this
        // checks the docs index takes its verdict, including fail-closed for an invalid value.
        use ling_airgapped::{resolve_from, Level};
        let file = |text: &str| (PathBuf::from("dreamference.toml"), text.to_string());
        let on = file("mightling_airgapped = \"on\"\n");
        let off = file("mightling_airgapped = \"off\"\n");
        let level = |env: Option<&str>, configs: &[(PathBuf, String)]| {
            resolve_from(None, env, configs).level
        };
        assert_eq!(
            level(None, &[off.clone(), on.clone()]),
            Level::On,
            "strictest file wins"
        );
        assert_eq!(level(None, std::slice::from_ref(&off)), Level::Off);
        assert_eq!(
            level(Some("off"), std::slice::from_ref(&on)),
            Level::Off,
            "the environment wins"
        );
        assert_eq!(
            level(Some("bogus"), std::slice::from_ref(&off)),
            Level::On,
            "an invalid value is on"
        );
        assert_eq!(
            level(None, &[file("{\"mightling_airgapped\": \"off\"}")]),
            Level::On,
            "not TOML is on"
        );
    }

    #[test]
    fn titles_come_from_the_source_until_the_pdf_names_its_own() {
        assert_eq!(
            title_of("https://x.org/papers/attention_is_all.pdf?dl=1"),
            "attention is all"
        );
        assert_eq!(
            title_of("/home/u/work/Master%20Agreement.pdf"),
            "Master Agreement"
        );
        assert_eq!(title_of("https://arxiv.org/pdf/1706.03762"), "1706.03762");
    }

    #[test]
    fn disabled_collections_and_directories_are_refused_by_resolve_local() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().canonicalize().unwrap();
        let work = home.join("work");
        let disabled_root = home.join("Archive");
        std::fs::create_dir_all(work.join("subdir")).unwrap();
        std::fs::create_dir_all(&disabled_root).unwrap();
        std::fs::write(disabled_root.join("old.pdf"), b"%PDF-1.4").unwrap();
        let mut col = Collection::new("archive", disabled_root.clone());
        col.enabled = false;
        assert!(
            resolve_local(
                &disabled_root.join("old.pdf").to_string_lossy(),
                &work,
                &[col],
                &home
            )
            .is_err(),
            "disabled collection root does not grant access"
        );
        assert!(resolve_local("subdir", &work, &[], &home)
            .unwrap_err()
            .contains("not a file"));
    }
}
