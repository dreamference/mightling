//! Indexing a collection (spec §6, §7, §9): outside the agent's sandbox only.
//!
//! 1. **Discovery and the change scan**, here: the walk reads metadata only (§15.8); a file whose
//!    size and modification time are unchanged is not opened; a new or changed file is sniffed
//!    and hashed, and a copy of a file already indexed is recorded as its duplicate. Files gone
//!    from disk lose their chunks in the same pass.
//! 2. **Extraction**, in a scope of `puffin-index.slice` admitted against the host-wide budget,
//!    capped at 1 GiB, under `choom -n 1000`, `nice` and `ionice`, inside bwrap with no network,
//!    the home folder hidden and the collection read-only. The worker reports each file before
//!    reading it; a file that takes longer than `docs_extract_timeout_s` or kills the worker is
//!    recorded `failed: …` and a new worker goes on with the rest.
//! 3. **Chunking and embedding**, in a second admitted scope with no network: chunks are written
//!    first (keyword search works at once), then embedded one at a time.
//!
//! A run that cannot be admitted, or meets a Night Shift or benchmark run, is deferred and says
//! why; `server start` stops these scopes before a model load, and the run is retried later.

use std::collections::HashMap;
use std::io::{BufRead, Seek};
use std::path::{Path, PathBuf};
use std::process::Child;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::collections::Collection;
use crate::config::{self, Settings};
use crate::discover;
use crate::extract::{Event, Job};
use crate::host::{self, Host, Kind};
use crate::sandbox::{self, Spec};
use crate::store;

/// How a collection's run ended.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Outcome {
    Ok,
    /// Not run now, and why: memory, a Night Shift run, the size cap, a missing model.
    Deferred(String),
    Failed(String),
}

impl Outcome {
    pub fn status(&self) -> String {
        match self {
            Outcome::Ok => "ok".into(),
            Outcome::Deferred(why) => format!("deferred: {why}"),
            Outcome::Failed(why) => format!("failed: {why}"),
        }
    }
}

/// What a run needs from its surroundings; tests swap the host and run without bwrap.
pub struct Env<'a> {
    pub host: &'a dyn Host,
    pub settings: Settings,
    pub user_home: PathBuf,
    /// This executable, which the scopes run as their worker.
    pub exe: PathBuf,
    pub lib_dir: PathBuf,
    pub model_dir: PathBuf,
    /// Wrap the workers in choom, nice, ionice and bwrap (false only in tests).
    pub sandbox: bool,
    /// Extra arguments for the index worker (tests: `--test-embedder`).
    pub worker_args: Vec<String>,
    /// How often a scope is polled.
    pub poll: Duration,
}

impl Env<'_> {
    pub fn real(host: &dyn Host) -> Result<Env<'_>> {
        Ok(Env {
            host,
            settings: Settings::load(),
            user_home: config::user_home(),
            exe: std::env::current_exe()?.canonicalize()?,
            lib_dir: config::lib_dir(),
            model_dir: config::model_dir(),
            sandbox: true,
            worker_args: Vec::new(),
            poll: Duration::from_millis(200),
        })
    }
}

/// Whether a Night Shift or SWE-bench run holds `runner.lock` (§9). Read from `/proc/locks`
/// rather than by trying the lock, which for that instant would keep a starting run from taking it.
pub fn night_shift_active(night_dir: &Path) -> bool {
    lock_held(&night_dir.join("runner.lock"))
}

/// Whether any process holds a lock on `path`, from `/proc/locks`, without touching the lock.
pub fn lock_held(path: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    let Ok(meta) = std::fs::metadata(path) else { return false };
    let inode = meta.ino().to_string();
    std::fs::read_to_string("/proc/locks").unwrap_or_default().lines().any(|line| {
        // `1: FLOCK  ADVISORY  WRITE 1234 fd:01:5678 0 EOF`
        line.split_whitespace().nth(5).and_then(|id| id.rsplit(':').next()).is_some_and(|ino| ino == inode)
    })
}

/// Indexes every enabled collection, or one. Holds `docs/index.lock` for the whole run, so two
/// runs never write one database at once.
pub fn run(env: &Env, docs: &crate::collections::DocsToml, only: Option<&str>, rebuild: bool) -> Result<Vec<(String, Outcome)>> {
    store::create_private_dir(&config::docs_dir())?;
    let lock = std::fs::File::create(config::docs_dir().join("index.lock"))?;
    if host::flock(&lock, false).is_err() {
        bail!("another ling-docs index run is in progress");
    }
    let mut out = Vec::new();
    for collection in docs.enabled().filter(|c| only.is_none_or(|n| n == c.name)) {
        if rebuild {
            remove_database(&collection.name)?;
        }
        let outcome = index_collection(env, collection).unwrap_or_else(|e| Outcome::Failed(format!("{e:#}")));
        if let Ok(conn) = store::open_rw(&config::db_path(&collection.name)) {
            let _ = store::set_meta(&conn, "last_status", &outcome.status());
            let _ = store::set_meta(&conn, "last_run", &now().to_string());
        }
        out.push((collection.name.clone(), outcome));
    }
    if let Some(name) = only {
        if docs.get(name).is_none() {
            bail!("no collection named `{name}`");
        }
    }
    Ok(out)
}

/// Deletes a collection's database and its journal.
pub fn remove_database(name: &str) -> Result<()> {
    for suffix in ["", "-journal", "-wal", "-shm"] {
        let path = PathBuf::from(format!("{}{suffix}", config::db_path(name).display()));
        match std::fs::remove_file(&path) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e).with_context(|| format!("removing {}", path.display())),
        }
    }
    Ok(())
}

pub fn now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// What the change scan decided.
#[derive(Debug, Default)]
pub struct Scan {
    pub new: usize,
    pub changed: usize,
    pub unchanged: usize,
    pub removed: usize,
    pub duplicates: usize,
    pub skipped: usize,
    /// Documents to extract: id, file to read, kind.
    pub jobs: Vec<Job>,
}

/// Brings the `documents` table in line with the disk (step 1).
pub fn scan(conn: &mut Connection, found: &[discover::Candidate]) -> Result<Scan> {
    let known = store::documents(conn)?;
    let mut out = Scan::default();
    let tx = conn.transaction()?;
    let on_disk: HashMap<&str, &discover::Candidate> = found.iter().map(|c| (c.rel.as_str(), c)).collect();
    for (path, row) in &known {
        if !on_disk.contains_key(path.as_str()) {
            store::delete_document(&tx, row.id)?;
            out.removed += 1;
        }
    }
    // Hashes already present, for duplicates: originals only.
    let mut by_hash: HashMap<String, i64> = HashMap::new();
    {
        let mut stmt = tx.prepare("SELECT sha256, id FROM documents WHERE sha256 IS NOT NULL AND dup_of IS NULL AND status IN ('ok', 'pending') ORDER BY id")?;
        for row in stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?.flatten() {
            by_hash.entry(row.0).or_insert(row.1);
        }
    }
    // Originals before their copies: a browser's `x (1).pdf` or a `Copy of x` is the duplicate,
    // whichever sorts first.
    let mut order: Vec<&discover::Candidate> = found.iter().collect();
    order.sort_by_key(|c| (looks_like_a_copy(&c.rel), c.rel.len(), c.rel.clone()));
    for candidate in order {
        let previous = known.get(&candidate.rel);
        // Re-read the row: a deletion above may have turned it pending.
        let current: Option<(i64, i64, i64, Option<String>, String)> = tx
            .query_row("SELECT id, size, mtime_ns, sha256, status FROM documents WHERE path = ?", [&candidate.rel], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))
            .ok();
        if let Some((id, size, mtime, _, status)) = &current {
            if *size == candidate.size as i64 && *mtime == candidate.mtime_ns && status != "pending" {
                out.unchanged += 1;
                continue;
            }
            if *size == candidate.size as i64 && *mtime == candidate.mtime_ns && status == "pending" {
                out.jobs.push(Job { id: *id, path: candidate.abs.clone(), kind: candidate.kind });
                continue;
            }
        }
        if let Err(why) = discover::sniff(&candidate.abs, candidate.kind) {
            upsert(&tx, candidate, None, &format!("skipped: {why}"), None)?;
            out.skipped += 1;
            continue;
        }
        let sha = discover::hash(&candidate.abs)?;
        if let Some((id, _, _, Some(old_sha), status)) = &current {
            if *old_sha == sha && status == "ok" {
                tx.execute("UPDATE documents SET size = ?, mtime_ns = ? WHERE id = ?", params![candidate.size as i64, candidate.mtime_ns, id])?;
                out.unchanged += 1;
                continue;
            }
        }
        let own_id = current.as_ref().map(|c| c.0);
        if let Some(original) = by_hash.get(&sha).copied().filter(|o| Some(*o) != own_id) {
            if let Some(id) = own_id {
                store::clear_document(&tx, id)?;
            }
            upsert(&tx, candidate, Some(&sha), "duplicate", Some(original))?;
            out.duplicates += 1;
            continue;
        }
        let id = upsert(&tx, candidate, Some(&sha), "pending", None)?;
        by_hash.entry(sha).or_insert(id);
        if previous.is_some() { out.changed += 1 } else { out.new += 1 }
        out.jobs.push(Job { id, path: candidate.abs.clone(), kind: candidate.kind });
    }
    tx.commit()?;
    Ok(out)
}

/// `x (1).pdf`, `x(2).md`, `x - Copy.txt`, `Copy of x.pdf`.
fn looks_like_a_copy(rel: &str) -> bool {
    let name = Path::new(rel).file_stem().map(|s| s.to_string_lossy().to_lowercase()).unwrap_or_default();
    let numbered = name.ends_with(')') && name.rfind('(').is_some_and(|i| name[i + 1..name.len() - 1].chars().all(|c| c.is_ascii_digit()) && i + 2 < name.len());
    numbered || name.ends_with(" - copy") || name.ends_with(" copy") || name.starts_with("copy of ")
}

fn upsert(conn: &Connection, c: &discover::Candidate, sha: Option<&str>, status: &str, dup_of: Option<i64>) -> Result<i64> {
    conn.execute(
        "INSERT INTO documents(path, kind, size, mtime_ns, sha256, status, dup_of) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
         ON CONFLICT(path) DO UPDATE SET kind = ?2, size = ?3, mtime_ns = ?4, sha256 = ?5, status = ?6, dup_of = ?7",
        params![c.rel, c.kind.as_str(), c.size as i64, c.mtime_ns, sha, status, dup_of],
    )?;
    Ok(conn.query_row("SELECT id FROM documents WHERE path = ?", [&c.rel], |r| r.get(0))?)
}

/// Indexes one collection.
pub fn index_collection(env: &Env, collection: &Collection) -> Result<Outcome> {
    if night_shift_active(&config::night_dir()) {
        return Ok(Outcome::Deferred("a Night Shift or benchmark run holds the runner lock".into()));
    }
    let db = config::db_path(&collection.name);
    let size_gb = std::fs::metadata(&db).map(|m| m.len() as f64 / (1u64 << 30) as f64).unwrap_or(0.0);
    if size_gb > env.settings.max_index_gb {
        return Ok(Outcome::Deferred(format!("the index is {size_gb:.1} GB, over docs_max_index_gb ({})", env.settings.max_index_gb)));
    }
    let missing = if env.worker_args.iter().any(|a| a == "--test-embedder") { None } else { config::missing_runtime_at(&env.lib_dir, &env.model_dir) };
    if let Some(why) = missing {
        return Ok(Outcome::Deferred(why));
    }
    if !collection.root.is_dir() {
        return Ok(Outcome::Failed(format!("{} is not there", collection.root.display())));
    }
    let mut conn = store::open_rw(&db)?;
    let found = discover::walk(collection, &env.user_home);
    let scan = scan(&mut conn, &found.candidates)?;
    store::set_meta(&conn, "skipped_unopened", &serde_json::to_string(&found.skipped)?)?;
    let missing_vectors: i64 = conn.query_row("SELECT count(*) FROM chunks WHERE vector IS NULL", [], |r| r.get(0))?;
    let pending: i64 = conn.query_row("SELECT count(*) FROM documents WHERE status = 'pending'", [], |r| r.get(0))?;
    drop(conn);
    if scan.jobs.is_empty() && missing_vectors == 0 && pending == 0 {
        return Ok(Outcome::Ok);
    }
    if !env.host.can_create_scopes() {
        return Ok(Outcome::Deferred("no user systemd to cap the run's memory (indexing runs only where it can be capped)".into()));
    }
    let scratch = config::docs_dir().join(format!("run-{}-{}", collection.name, std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    store::create_private_dir(&scratch)?;
    let result = extract_and_embed(env, collection, &db, &scratch, &scan.jobs);
    // The workers' logs are kept (the last run's), for `status` to point at when a run fails.
    let logs = config::docs_dir().join("logs");
    if std::fs::create_dir_all(&logs).is_ok() {
        for entry in std::fs::read_dir(&scratch).into_iter().flatten().flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.ends_with(".log") {
                let _ = std::fs::copy(entry.path(), logs.join(format!("{}-{name}", collection.name)));
            }
        }
    }
    let _ = std::fs::remove_dir_all(&scratch);
    result
}

fn extract_and_embed(env: &Env, collection: &Collection, db: &Path, scratch: &Path, jobs: &[Job]) -> Result<Outcome> {
    let events = scratch.join("events.jsonl");
    std::fs::write(&events, b"")?;
    let conn = store::open_rw(db)?;
    let recorded = |key: &str| store::get_meta(&conn, key).ok().flatten().and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
    let (extract_peak, embed_peak) = (recorded("extract_peak_mb"), recorded("embed_peak_mb"));
    drop(conn);
    if !jobs.is_empty() {
        match extract_all(env, collection, scratch, jobs, &events, extract_peak)? {
            Ok((peak, cap)) => record_peak(db, "extract", peak, cap)?,
            Err(outcome) => return Ok(outcome),
        }
    }
    match embed(env, collection, db, scratch, &events, embed_peak)? {
        Ok((peak, cap)) => record_peak(db, "embed", peak, cap)?,
        Err(outcome) => return Ok(outcome),
    }
    Ok(Outcome::Ok)
}

fn record_peak(db: &Path, phase: &str, peak: u64, cap: u64) -> Result<()> {
    let conn = store::open_rw(db)?;
    if peak > 0 {
        store::set_meta(&conn, &format!("{phase}_peak_mb"), &(peak >> 20).to_string())?;
    }
    store::set_meta(&conn, &format!("{phase}_cap_mb"), &(cap >> 20).to_string())?;
    Ok(())
}

/// Read-only paths a worker needs: the executable and the libraries, with the targets of any
/// symlinks among them (an installed tree may link into a shared cache).
fn runtime_paths(env: &Env, with_model: bool) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    let mut add = |p: &Path| {
        if let Ok(c) = p.canonicalize() {
            if !dirs.iter().any(|d| c.starts_with(d)) {
                dirs.push(c);
            }
        }
    };
    if let Some(dir) = env.exe.parent() {
        add(dir);
    }
    let mut roots = vec![env.lib_dir.clone()];
    if with_model {
        roots.push(env.model_dir.clone());
    }
    for root in roots {
        add(&root);
        // Every directory a link passes through (a Hugging Face cache links twice: the snapshot,
        // then the blob), so the chain resolves inside the sandbox too.
        for entry in std::fs::read_dir(&root).into_iter().flatten().flatten() {
            let mut hop = entry.path();
            for _ in 0..8 {
                let Ok(target) = std::fs::read_link(&hop) else { break };
                let target = if target.is_absolute() { target } else { hop.parent().unwrap_or(Path::new("/")).join(target) };
                if let Some(parent) = target.parent() {
                    add(parent);
                }
                hop = target;
            }
        }
    }
    dirs
}

/// The command a scope runs: the worker, inside bwrap, under choom, nice and ionice, writing the
/// cgroup's peak itself before it exits (the cgroup is gone once it has).
fn scope_argv(env: &Env, spec: Spec, peak_file: &Path) -> Vec<String> {
    if !env.sandbox {
        return spec.argv;
    }
    let mut spec = spec;
    let quoted: Vec<String> = spec.argv.iter().map(|a| sh_quote(a)).collect();
    spec.argv = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!(
            "{}; rc=$?; cat /sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.peak > {} 2>/dev/null; exit $rc",
            quoted.join(" "),
            sh_quote(&peak_file.to_string_lossy())
        ),
    ];
    let mut argv: Vec<String> = ["choom", "-n", "1000", "--", "nice", "-n", "10", "ionice", "-c3"].iter().map(|s| s.to_string()).collect();
    argv.extend(sandbox::bwrap_args(&spec));
    argv
}

pub fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn base_env() -> Vec<(String, String)> {
    vec![("PATH".into(), "/usr/bin:/bin".into()), ("LANG".into(), "C.UTF-8".into()), ("OMP_NUM_THREADS".into(), "4".into())]
}

fn read_peak(path: &Path) -> u64 {
    std::fs::read_to_string(path).ok().and_then(|t| t.trim().parse().ok()).unwrap_or(0)
}

fn unit_name(collection: &str, phase: &str, n: usize) -> String {
    format!("puffin-index-docs-{collection}-{phase}{n}-{}", std::process::id())
}

/// Step 2: every job's file through the extractor, restarted after a file that hangs or kills it.
/// Returns the highest peak and the cap, or the outcome that stopped the run.
fn extract_all(env: &Env, collection: &Collection, scratch: &Path, jobs: &[Job], events: &Path, recorded_peak_mb: u64) -> Result<std::result::Result<(u64, u64), Outcome>> {
    let mut remaining: Vec<Job> = jobs.to_vec();
    let mut peak_max = 0u64;
    let mut cap_used = 0u64;
    let mut attempt = 0usize;
    let mut offset = 0u64;
    let timeout = Duration::from_secs(env.settings.extract_timeout_s);
    while !remaining.is_empty() {
        attempt += 1;
        let admitted = match host::admit(env.host, host::need(Kind::Extract, recorded_peak_mb.max(peak_max >> 20)), Kind::Extract.ceiling())? {
            Ok(admitted) => admitted,
            Err(_) => return Ok(Err(Outcome::Deferred("memory: the host-wide budget cannot cover the extractor now".into()))),
        };
        cap_used = admitted.cap;
        let jobs_file = scratch.join(format!("jobs-{attempt}.jsonl"));
        let lines: Vec<String> = remaining.iter().map(serde_json::to_string).collect::<std::result::Result<_, _>>()?;
        std::fs::write(&jobs_file, lines.join("\n"))?;
        let peak_file = scratch.join(format!("peak-x{attempt}"));
        let spec = Spec {
            home: env.user_home.clone(),
            scratch: scratch.to_path_buf(),
            read_only: runtime_paths(env, false).into_iter().chain([collection.root.clone()]).collect(),
            writable: Vec::new(),
            writable_outside_scratch: Vec::new(),
            env: base_env(),
            cwd: scratch.to_path_buf(),
            argv: vec![
                env.exe.to_string_lossy().into_owned(),
                "extract-worker".into(),
                jobs_file.to_string_lossy().into_owned(),
                events.to_string_lossy().into_owned(),
                "--lib-dir".into(),
                env.lib_dir.to_string_lossy().into_owned(),
            ],
        };
        let unit = unit_name(&collection.name, "x", attempt);
        let argv = scope_argv(env, spec, &peak_file);
        let log = scratch.join(format!("extract-{attempt}.log"));
        if std::env::var_os("LING_DOCS_TRACE_SCOPES").is_some() {
            eprintln!("scope {unit}: {argv:?}");
        }
        let mut child = env.host.start_scope(&unit, admitted.cap, &argv, &log)?;
        admitted.release();
        let (finished, current, status) = watch(env, &mut child, &unit, events, &mut offset, timeout)?;
        peak_max = peak_max.max(read_peak(&peak_file));
        remaining.retain(|j| !finished.contains(&j.id));
        if remaining.is_empty() {
            break;
        }
        // The worker stopped with files left: the one it was reading is the cause.
        match current {
            Some((id, why)) => {
                append_event(events, &Event::Failed { id, error: why })?;
                remaining.retain(|j| j.id != id);
            }
            None => {
                // It stopped before reading anything: the sandbox itself failed, or the scope
                // was stopped from outside (a model load). Nothing is blamed on a file.
                let tail = std::fs::read_to_string(&log).unwrap_or_default();
                let tail: String = tail.lines().rev().take(3).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join(" | ");
                return Ok(Err(match status {
                    Stop::Outside => Outcome::Deferred("stopped (a model load?)".into()),
                    _ => Outcome::Failed(format!("the extraction sandbox did not start: {tail}")),
                }));
            }
        }
        if matches!(status, Stop::Outside) {
            return Ok(Err(Outcome::Deferred("stopped (a model load?)".into())));
        }
    }
    Ok(Ok((peak_max, cap_used)))
}

/// How a worker ended.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Stop {
    Done,
    Killed,
    Crashed(i32),
    TimedOut,
    Outside,
}

/// Follows a worker's events until it exits or a file takes too long. Returns the ids it
/// finished, the id it was reading when it stopped (with why), and how it stopped.
fn watch(env: &Env, child: &mut Child, unit: &str, events: &Path, offset: &mut u64, timeout: Duration) -> Result<(Vec<i64>, Option<(i64, String)>, Stop)> {
    let mut finished = Vec::new();
    let mut current: Option<i64> = None;
    let mut since = Instant::now();
    loop {
        for event in read_new(events, offset)? {
            match event {
                Event::Start { id } => {
                    current = Some(id);
                    since = Instant::now();
                }
                Event::Done { id, .. } | Event::Failed { id, .. } => {
                    finished.push(id);
                    current = None;
                    since = Instant::now();
                }
            }
        }
        if let Some(status) = child.try_wait()? {
            // Events written just before the exit.
            for event in read_new(events, offset)? {
                match event {
                    Event::Start { id } => current = Some(id),
                    Event::Done { id, .. } | Event::Failed { id, .. } => {
                        finished.push(id);
                        current = None;
                    }
                }
            }
            use std::os::unix::process::ExitStatusExt;
            if std::env::var_os("LING_DOCS_TRACE_SCOPES").is_some() {
                eprintln!("scope {unit} ended: {status:?}, reading {current:?}, finished {}", finished.len());
            }
            let stop = match (status.code(), status.signal()) {
                (Some(0), _) => Stop::Done,
                (Some(143), _) | (_, Some(15)) => Stop::Outside,
                (Some(137), _) | (_, Some(9)) => Stop::Killed,
                (Some(code), _) => Stop::Crashed(code),
                (None, Some(sig)) => Stop::Crashed(128 + sig),
                (None, None) => Stop::Crashed(-1),
            };
            let why = match stop {
                Stop::Killed => "killed: over the extractor's memory cap (1 GiB), or out of memory".to_string(),
                Stop::Crashed(code) => format!("the extractor crashed on it (exit {code})"),
                _ => "the extractor stopped while reading it".to_string(),
            };
            return Ok((finished, current.map(|id| (id, why)), stop));
        }
        if current.is_some() && since.elapsed() > timeout {
            let _ = env.host.stop(unit);
            let _ = child.kill();
            let _ = child.wait();
            for event in read_new(events, offset)? {
                if let Event::Done { id, .. } | Event::Failed { id, .. } = event {
                    finished.push(id);
                    if current == Some(id) {
                        current = None;
                    }
                }
            }
            let why = format!("timed out after {} s", timeout.as_secs());
            return Ok((finished, current.map(|id| (id, why)), Stop::TimedOut));
        }
        std::thread::sleep(env.poll);
    }
}

/// Events appended since `offset` (whole lines only).
fn read_new(path: &Path, offset: &mut u64) -> Result<Vec<Event>> {
    let mut file = std::fs::File::open(path)?;
    file.seek(std::io::SeekFrom::Start(*offset))?;
    let mut reader = std::io::BufReader::new(file);
    let mut out = Vec::new();
    loop {
        let mut line = String::new();
        let n = reader.read_line(&mut line)?;
        if n == 0 || !line.ends_with('\n') {
            break;
        }
        *offset += n as u64;
        if let Ok(event) = serde_json::from_str::<Event>(line.trim_end()) {
            out.push(event);
        }
    }
    Ok(out)
}

fn append_event(path: &Path, event: &Event) -> Result<()> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new().append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(event)?)?;
    Ok(())
}

/// Step 3: the index worker writes chunks and vectors.
fn embed(env: &Env, collection: &Collection, db: &Path, scratch: &Path, events: &Path, recorded_peak_mb: u64) -> Result<std::result::Result<(u64, u64), Outcome>> {
    let admitted = match host::admit(env.host, host::need(Kind::Embed, recorded_peak_mb), Kind::Embed.ceiling())? {
        Ok(admitted) => admitted,
        Err(_) => return Ok(Err(Outcome::Deferred("memory: the host-wide budget cannot cover the embedder now".into()))),
    };
    let peak_file = scratch.join("peak-e");
    let docs_dir = db.parent().unwrap_or(Path::new("/")).to_path_buf();
    let mut argv = vec![
        env.exe.to_string_lossy().into_owned(),
        "index-worker".into(),
        "--collection".into(),
        collection.name.clone(),
        "--db".into(),
        db.to_string_lossy().into_owned(),
        "--events".into(),
        events.to_string_lossy().into_owned(),
        "--threads".into(),
        env.settings.embed_threads.to_string(),
        "--lib-dir".into(),
        env.lib_dir.to_string_lossy().into_owned(),
        "--model-dir".into(),
        env.model_dir.to_string_lossy().into_owned(),
    ];
    argv.extend(env.worker_args.iter().cloned());
    let spec = Spec {
        home: env.user_home.clone(),
        scratch: scratch.to_path_buf(),
        read_only: runtime_paths(env, true),
        writable: Vec::new(),
        writable_outside_scratch: vec![(docs_dir, "the collection's database".into())],
        env: base_env(),
        cwd: scratch.to_path_buf(),
        argv,
    };
    let unit = unit_name(&collection.name, "e", 1);
    let argv = scope_argv(env, spec, &peak_file);
    let log = scratch.join("embed.log");
    let cap = admitted.cap;
    let mut child = env.host.start_scope(&unit, cap, &argv, &log)?;
    admitted.release();
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        std::thread::sleep(env.poll);
    };
    let peak = read_peak(&peak_file);
    use std::os::unix::process::ExitStatusExt;
    if status.success() {
        return Ok(Ok((peak, cap)));
    }
    let tail = std::fs::read_to_string(&log).unwrap_or_default();
    let tail: String = tail.lines().rev().take(3).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join(" | ");
    Ok(Err(match (status.code(), status.signal()) {
        (Some(143), _) | (_, Some(15)) => Outcome::Deferred("stopped (a model load?)".into()),
        (Some(137), _) | (_, Some(9)) => Outcome::Failed("the embedder was killed: over its memory cap, or out of memory".into()),
        _ => Outcome::Failed(format!("the embedder failed: {tail}")),
    }))
}

/// `ling-docs index-worker`: inside the embedder's sandbox. Writes each extracted document's
/// units and chunks, records failures, then embeds every chunk that has no vector yet, one at a
/// time, committing every few chunks so a stopped run keeps what it did.
pub fn index_worker(collection: &str, db: &Path, events: &Path, embedder: &dyn crate::embed::Embed, counter: &dyn crate::chunk::TokenCount, log: &mut dyn std::io::Write) -> Result<(usize, usize)> {
    let mut conn = store::open_rw(db)?;
    let now = now();
    let mut written = 0usize;
    let file = std::fs::File::open(events)?;
    for line in std::io::BufReader::new(file).lines() {
        let Ok(event) = serde_json::from_str::<Event>(&line?) else { continue };
        match event {
            Event::Start { .. } => {}
            Event::Failed { id, error } => store::set_status(&conn, id, &format!("failed: {error}"))?,
            Event::Done { id, result } => {
                let Ok(path) = conn.query_row("SELECT path FROM documents WHERE id = ?", [id], |r| r.get::<_, String>(0)) else { continue };
                let title = Path::new(&path).file_stem().map(|s| s.to_string_lossy().replace('_', " ")).unwrap_or_default();
                let chunks = crate::chunk::chunk(&result.units, counter, config::CHUNK_TOKENS, 0.15);
                store::write_document(&mut conn, id, &title, result.pages, &result.needs_ocr, &result.units, &chunks, now)?;
                written += 1;
            }
        }
    }
    writeln!(log, "{collection}: {written} documents written")?;
    // Vectors of another model are never mixed with this one's.
    let model = store::get_meta(&conn, "model")?;
    if model.as_deref() != Some(embedder.name()) {
        conn.execute("UPDATE chunks SET vector = NULL", [])?;
        store::set_meta(&conn, "model", embedder.name())?;
        store::set_meta(&conn, "dim", &embedder.dim().to_string())?;
    }
    let todo: Vec<(i64, String, String, String)> = conn
        .prepare("SELECT c.id, d.title, c.heading, c.text FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.vector IS NULL ORDER BY c.id")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
        .collect::<rusqlite::Result<_>>()?;
    let started = Instant::now();
    let mut done = 0usize;
    let mut tx = conn.transaction()?;
    for (id, title, heading, text) in &todo {
        let chunk = crate::chunk::Chunk { loc: String::new(), page_first: None, page_last: None, line_first: None, line_last: None, heading: heading.clone(), text: text.clone() };
        let vector = embedder.embed(&crate::chunk::embed_text(title, &chunk))?;
        tx.execute("UPDATE chunks SET vector = ? WHERE id = ?", params![crate::embed::to_blob(&vector), id])?;
        done += 1;
        if done % 32 == 0 {
            tx.commit()?;
            tx = conn.transaction()?;
        }
    }
    tx.commit()?;
    let seconds = started.elapsed().as_secs_f64();
    if done > 0 {
        store::set_meta(&conn, "embed_rate", &format!("{:.1}", done as f64 / seconds.max(1e-6)))?;
    }
    writeln!(log, "{collection}: {done} chunks embedded in {seconds:.1} s")?;
    Ok((written, done))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::discover::{Candidate, Kind as FileKind};

    fn candidate(dir: &Path, rel: &str, body: &[u8]) -> Candidate {
        let abs = dir.join(rel);
        std::fs::write(&abs, body).unwrap();
        use std::os::unix::fs::MetadataExt;
        let meta = std::fs::metadata(&abs).unwrap();
        Candidate { rel: rel.into(), abs, size: meta.len(), mtime_ns: meta.mtime() * 1_000_000_000 + meta.mtime_nsec(), kind: FileKind::of(rel).unwrap() }
    }

    #[test]
    fn the_scan_finds_new_changed_removed_duplicate_and_lying_files() {
        let dir = tempfile::tempdir().unwrap();
        let mut conn = store::open_rw(&dir.path().join("db/c.db")).unwrap();
        let a = candidate(dir.path(), "a.md", b"# A\nalpha\n");
        let copy = candidate(dir.path(), "a (1).md", b"# A\nalpha\n");
        let fake = candidate(dir.path(), "movie.pdf", b"\x00\x00\x00ftyp");
        let s = scan(&mut conn, &[a.clone(), copy.clone(), fake.clone()]).unwrap();
        assert_eq!((s.new, s.duplicates, s.skipped, s.jobs.len()), (1, 1, 1, 1));
        // A pending document is retried by the next scan; one marked ok is left alone.
        let id = s.jobs[0].id;
        assert_eq!(scan(&mut conn, &[a.clone(), copy.clone(), fake.clone()]).unwrap().jobs.len(), 1);
        store::set_status(&conn, id, "ok").unwrap();
        let s = scan(&mut conn, &[a.clone(), copy.clone(), fake.clone()]).unwrap();
        assert_eq!((s.unchanged, s.jobs.len()), (3, 0));
        // The original goes: its copy becomes the document and is read.
        let s = scan(&mut conn, &[copy.clone(), fake.clone()]).unwrap();
        assert_eq!(s.removed, 1);
        assert_eq!(s.jobs.len(), 1);
        assert_eq!(s.jobs[0].path, copy.abs);
        // A changed file is read again.
        store::set_status(&conn, s.jobs[0].id, "ok").unwrap();
        std::thread::sleep(Duration::from_millis(20));
        let changed = candidate(dir.path(), "a (1).md", b"# A\nalpha beta\n");
        let s = scan(&mut conn, &[changed, fake]).unwrap();
        assert_eq!((s.changed, s.jobs.len()), (1, 1));
    }

    #[test]
    fn the_worker_argv_is_sandboxed_without_network_and_writes_its_peak() {
        let dir = tempfile::tempdir().unwrap();
        let host = crate::host::fake::FakeHost::new(dir.path(), 100);
        let env = Env {
            host: &host,
            settings: Settings::default(),
            user_home: dir.path().join("home"),
            exe: PathBuf::from("/usr/bin/true"),
            lib_dir: dir.path().join("lib"),
            model_dir: dir.path().join("model"),
            sandbox: true,
            worker_args: Vec::new(),
            poll: Duration::from_millis(1),
        };
        let spec = Spec { home: env.user_home.clone(), scratch: dir.path().join("s"), cwd: dir.path().join("s"), argv: vec!["/usr/bin/true".into(), "extract-worker".into()], ..Spec::default() };
        let argv = scope_argv(&env, spec.clone(), &dir.path().join("s/peak"));
        assert_eq!(&argv[..4], ["choom", "-n", "1000", "--"]);
        assert!(argv.contains(&"--unshare-net".to_string()));
        assert!(argv.last().unwrap().contains("memory.peak"));
        assert!(sandbox::unexpected_writable(&argv, &spec).is_empty());
        assert!(unit_name("documents", "x", 1).starts_with("puffin-index-docs-documents-x1-"), "server start stops puffin-index-*");
    }

    #[test]
    fn a_night_shift_lock_is_seen_without_taking_it() {
        let dir = tempfile::tempdir().unwrap();
        assert!(!night_shift_active(dir.path()));
        let file = std::fs::File::create(dir.path().join("runner.lock")).unwrap();
        assert!(!night_shift_active(dir.path()));
        host::flock(&file, false).unwrap();
        assert!(night_shift_active(dir.path()));
        drop(file);
        assert!(!night_shift_active(dir.path()));
    }
}
