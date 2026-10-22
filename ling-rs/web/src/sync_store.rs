//! The sync hub's store (specs/DREAMFERENCE_MIGHTLING_SYNC.md §5, §10): the node's copy of every
//! synced thread, in `~/.mightling/sync/hub/` (0700). Plain files and functions, so it is tested
//! without a server; `sync_hub.rs` puts it behind `ling web`'s routes.
//!
//! - **Off until `ling sync enable`.** Nothing here exists until then: no folder, no file, and every
//!   `/api/sync/` route answers as if it did not exist (`hub.json` says whether it is on).
//! - **A thread is its rollouts.** Codex writes one rollout file per thread, and a new one each time
//!   the thread is reverted (`rollout-<time>-<thread>_<rollout>.jsonl`), which starts from a byte
//!   offset of the one before (`session_meta.history_base`; Phase 0, §16). The store keeps every
//!   rollout under its rollout id (`rollouts/<id>.jsonl`, the owner's bytes as they are,
//!   uncompressed) with a record (`rollouts/<id>.json`) that names its thread. The record is the
//!   commit point: its `length` is what counts, and a longer file (a write cut short) is cut back
//!   to it when the store opens.
//! - **Append-only by offset.** An upload is accepted only at the hub's length, with the hash of the
//!   prefix it extends ([`prefix_hash`], 1 MiB chunks chained), so an offset and its hash name a
//!   prefix exactly and a rollout never conflicts with itself (§5.2, §8).
//! - **One writer per thread.** The first device to publish a thread owns it. Another device that
//!   publishes into it (two devices extending one prefix: split brain) gets a thread of its own,
//!   "continued on" that device, with a link back; two transcripts are never merged (§8).
//! - **The manifest's cursor is the hub's own sequence number**, never a device clock; it is kept
//!   in a file and in every record, so a restart never issues a number twice.
//! - **Tombstones** stay 90 days (§5.4), so a device that was off for a month still learns of a
//!   deletion.
//! - **Names and archive state** belong to the thread and are last writer wins, ordered by a hybrid
//!   logical clock ([`Hlc`]), so a device with a wrong clock cannot win forever (§8).

use std::collections::BTreeMap;
use std::io::Read;
use std::io::Seek;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;
use sha2::Digest;
use sha2::Sha256;

use crate::auth;

/// The hub's folder, under `ling`'s home.
pub fn hub_dir(codex_home: &Path) -> PathBuf {
    codex_home.join("sync").join("hub")
}

/// The chunk size of the prefix hash.
pub const CHUNK: usize = 1 << 20;
/// How long a tombstone is kept.
pub const TOMBSTONE_DAYS: u64 = 90;
/// The largest single upload: an agent sends at most this much per request.
pub const UPLOAD_CAP: usize = 8 << 20;
/// A transcript text longer than this is shown cut, with a note (§10: a phone gets the start).
pub const SHOWN_TEXT_CAP: usize = 64 << 10;
/// How many reverts deep a transcript is followed back.
const MAX_LINEAGE: usize = 32;

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Whether the hub is on: `ling sync enable` on the node wrote `hub.json` saying so.
pub fn enabled(codex_home: &Path) -> bool {
    std::fs::read_to_string(hub_dir(codex_home).join("hub.json"))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .is_some_and(|value| value.get("enabled") == Some(&Value::Bool(true)))
}

/// Turns the hub on or off; off keeps the store (`ling sync forget` deletes it).
pub fn set_enabled(codex_home: &Path, on: bool) -> std::io::Result<()> {
    let dir = hub_dir(codex_home);
    auth::private_dir(&dir)?;
    auth::write_private(&dir.join("hub.json"), &json!({ "enabled": on, "changed": now_secs() }).to_string())
}

/// `ling sync forget` on the node: the store, for real.
pub fn forget(codex_home: &Path) -> std::io::Result<()> {
    match std::fs::remove_dir_all(hub_dir(codex_home)) {
        Err(err) if err.kind() != std::io::ErrorKind::NotFound => Err(err),
        _ => Ok(()),
    }
}

/// How many threads and bytes the hub holds, read from the records without opening the store, which
/// repairs files and so is for the running server alone (`ling sync status`).
pub fn summary(codex_home: &Path) -> (usize, u64) {
    let mut threads = std::collections::BTreeSet::new();
    let mut bytes = 0;
    for entry in std::fs::read_dir(hub_dir(codex_home).join("rollouts")).into_iter().flatten().flatten() {
        if entry.path().extension().is_some_and(|extension| extension == "json")
            && let Some(record) = std::fs::read_to_string(entry.path()).ok().and_then(|text| serde_json::from_str::<Record>(&text).ok())
        {
            threads.insert(record.thread);
            bytes += record.length;
        }
    }
    (threads.len(), bytes)
}

/// A thread or rollout id as Codex writes it: a UUID in lowercase hex. Nothing else names a file here.
pub fn valid_id(id: &str) -> bool {
    let bytes = id.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => *byte == b'-',
            _ => byte.is_ascii_digit() || (b'a'..=b'f').contains(byte),
        })
}

/// The id a device publishes under when another device owns the one it has (§8): the same for
/// the same id and device, so a retry lands in the same place.
pub fn fork_id(id: &str, device: &str) -> String {
    let digest = hex(&Sha256::digest(format!("{id}:{device}").as_bytes()));
    format!("{}-{}-{}-{}-{}", &digest[0..8], &digest[8..12], &digest[12..16], &digest[16..20], &digest[20..32])
}

/// A rollout's file name as Codex writes it, and nothing that could leave its folder.
pub fn valid_file_name(name: &str) -> bool {
    name.starts_with("rollout-")
        && name.ends_with(".jsonl")
        && name.len() <= 200
        && name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
}

/// The prefix hash, built as bytes arrive: each full 1 MiB chunk is folded into a chain
/// (`h = sha256(h ‖ sha256(chunk))`), and a partial last chunk is folded in only for the digest.
/// The digest of a prefix depends on its bytes alone, never on how they were uploaded.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Chain {
    full: [u8; 32],
    full_len: u64,
    tail: Vec<u8>,
}

impl Chain {
    pub fn new() -> Chain {
        Chain::default()
    }

    /// Resumes from a saved chain at a chunk boundary, with the bytes after it.
    pub fn resume(full_hex: &str, full_len: u64, tail: Vec<u8>) -> Option<Chain> {
        let mut full = [0u8; 32];
        if full_hex.len() != 64 || !full_len.is_multiple_of(CHUNK as u64) || tail.len() >= CHUNK {
            return None;
        }
        for (index, slot) in full.iter_mut().enumerate() {
            *slot = u8::from_str_radix(full_hex.get(index * 2..index * 2 + 2)?, 16).ok()?;
        }
        Some(Chain { full, full_len, tail })
    }

    fn fold(previous: &[u8; 32], chunk: &[u8]) -> [u8; 32] {
        let mut hasher = Sha256::new();
        hasher.update(previous);
        hasher.update(Sha256::digest(chunk));
        hasher.finalize().into()
    }

    pub fn push(&mut self, mut bytes: &[u8]) {
        while !bytes.is_empty() {
            let take = (CHUNK - self.tail.len()).min(bytes.len());
            self.tail.extend_from_slice(&bytes[..take]);
            bytes = &bytes[take..];
            if self.tail.len() == CHUNK {
                self.full = Chain::fold(&self.full, &self.tail);
                self.full_len += CHUNK as u64;
                self.tail.clear();
            }
        }
    }

    pub fn len(&self) -> u64 {
        self.full_len + self.tail.len() as u64
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn digest(&self) -> String {
        if self.tail.is_empty() { hex(&self.full) } else { hex(&Chain::fold(&self.full, &self.tail)) }
    }

    /// The chain at the last chunk boundary, as a record keeps it.
    pub fn saved(&self) -> (String, u64) {
        (hex(&self.full), self.full_len)
    }
}

/// The hash of a whole prefix.
pub fn prefix_hash(bytes: &[u8]) -> String {
    let mut chain = Chain::new();
    chain.push(bytes);
    chain.digest()
}

/// A hybrid logical clock's reading: wall time in milliseconds, a counter for readings in the same
/// millisecond or behind one already seen, and the device, which breaks the last tie. Ordered as a
/// tuple. Written `<ms>.<counter>.<device>`.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Default)]
pub struct Hlc {
    pub ms: u64,
    pub counter: u32,
    pub device: String,
}

impl Hlc {
    pub fn parse(text: &str) -> Option<Hlc> {
        let mut parts = text.splitn(3, '.');
        let ms = parts.next()?.parse().ok()?;
        let counter = parts.next()?.parse().ok()?;
        let device = parts.next()?.to_string();
        (!device.is_empty() && device.len() <= 64).then_some(Hlc { ms, counter, device })
    }
}

impl std::fmt::Display for Hlc {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}.{}.{}", self.ms, self.counter, self.device)
    }
}

/// A hybrid logical clock: never behind wall time, never behind any reading it has seen.
#[derive(Debug, Default)]
pub struct Clock {
    last: Mutex<(u64, u32)>,
}

impl Clock {
    pub fn tick(&self, device: &str) -> Hlc {
        self.tick_at(now_ms(), device)
    }

    pub fn tick_at(&self, wall: u64, device: &str) -> Hlc {
        let mut last = self.last.lock().unwrap_or_else(|p| p.into_inner());
        *last = if wall > last.0 { (wall, 0) } else { (last.0, last.1 + 1) };
        Hlc { ms: last.0, counter: last.1, device: device.to_string() }
    }

    /// Takes in a reading from elsewhere, so the next tick is after it.
    pub fn observe(&self, seen: &Hlc) {
        let mut last = self.last.lock().unwrap_or_else(|p| p.into_inner());
        if (seen.ms, seen.counter) > *last {
            *last = (seen.ms, seen.counter);
        }
    }
}

/// Where a reverted thread's rollout starts: the end of an earlier rollout's bytes.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Base {
    pub rollout: String,
    pub end: u64,
}

/// One rollout, as its record keeps it and the manifest lists it. The thread's own state (owner,
/// name, archive state) is on every rollout of the thread, the same on each.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Record {
    pub rollout: String,
    pub thread: String,
    /// The device whose rollout this is.
    pub owner: String,
    pub owner_name: String,
    /// The rollout's file name on its owner (`rollout-<time>-<thread>[_<rollout>].jsonl`).
    pub file: String,
    pub length: u64,
    pub hash: String,
    /// The chain at the last chunk boundary, so an append rehashes at most one chunk.
    pub chain: String,
    pub chain_len: u64,
    pub archived: bool,
    pub archived_hlc: String,
    pub name: Option<String>,
    pub name_hlc: String,
    /// The first thing the user asked, for a title until the thread has a name.
    pub preview: Option<String>,
    pub cwd: Option<String>,
    /// `session_meta.source` (`cli`, `vscode`, …).
    pub source: Option<String>,
    pub base: Option<Base>,
    pub created: u64,
    pub updated: u64,
    pub seq: u64,
    /// The thread this one continues, when two devices extended the same prefix (§8).
    pub fork_of: Option<String>,
}

impl Record {
    /// The manifest's view: everything but the chain's inner state.
    pub fn listed(&self) -> Value {
        json!({
            "rollout": self.rollout,
            "thread": self.thread,
            "owner": self.owner,
            "owner_name": self.owner_name,
            "file": self.file,
            "length": self.length,
            "hash": self.hash,
            "archived": self.archived,
            "archived_hlc": self.archived_hlc,
            "name": self.name,
            "name_hlc": self.name_hlc,
            "preview": self.preview,
            "cwd": self.cwd,
            "source": self.source,
            "base": self.base,
            "created": self.created,
            "updated": self.updated,
            "seq": self.seq,
            "fork_of": self.fork_of,
        })
    }
}

/// A thread as a device lists it: its newest rollout's record, with the whole thread's times.
#[derive(Clone, Debug, PartialEq)]
pub struct ThreadView {
    pub current: Record,
    pub rollouts: usize,
    pub created: u64,
    pub updated: u64,
    pub preview: Option<String>,
}

impl ThreadView {
    pub fn title(&self) -> String {
        self.current
            .name
            .clone()
            .filter(|name| !name.trim().is_empty())
            .or_else(|| self.preview.clone())
            .unwrap_or_else(|| "Untitled thread".to_string())
    }

    pub fn listed(&self) -> Value {
        let current = &self.current;
        json!({
            "thread": current.thread,
            "owner": current.owner,
            "owner_name": current.owner_name,
            "title": self.title(),
            "name": current.name,
            "preview": self.preview,
            "archived": current.archived,
            "cwd": current.cwd,
            "source": current.source,
            "created": self.created,
            "updated": self.updated,
            "rollouts": self.rollouts,
            "fork_of": current.fork_of,
        })
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Tombstone {
    pub rollout: String,
    pub thread: String,
    pub seq: u64,
    pub at: u64,
    pub by: String,
    /// Taken off the hub by its owner (`[sync] exclude`), not deleted: every other device drops
    /// its view of it, and the owner keeps the thread.
    #[serde(default)]
    pub withdrawn: bool,
}

/// What an upload says besides its bytes.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Upload {
    /// Where the bytes go: the hub's length.
    pub offset: u64,
    /// The hash of the prefix they extend.
    pub prefix: String,
    /// The thread the rollout belongs to; with the first bytes (default: the rollout's own id).
    pub thread: Option<String>,
    /// The rollout's file name on its owner; with the first bytes.
    pub file: Option<String>,
    /// Whether the thread is archived on its owner, when the thread is new.
    pub archived: bool,
    /// The rollout this one continues (§8), when the hub said to publish it under a new id.
    pub fork_of: Option<String>,
}

impl Upload {
    pub fn at(offset: u64, prefix: &str) -> Upload {
        Upload { offset, prefix: prefix.to_string(), ..Upload::default() }
    }

    pub fn first(file: &str) -> Upload {
        Upload { prefix: prefix_hash(b""), file: Some(file.to_string()), ..Upload::default() }
    }
}

/// Who is asking: a sync agent, a paired browser, or this machine's own session.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Caller {
    pub device: String,
    pub name: String,
    pub agent: bool,
    pub scope: auth::Scope,
}

impl Caller {
    /// This machine's own session (`ling web open`): the user at the node.
    pub fn owner_session() -> Caller {
        Caller { device: "this-machine".to_string(), name: "this machine".to_string(), agent: false, scope: auth::Scope::Control }
    }

    pub fn can_control(&self) -> bool {
        self.scope == auth::Scope::Control
    }
}

#[derive(Debug, PartialEq)]
pub enum Refusal {
    /// Not an id, or nothing under it.
    NotFound,
    /// The thread was deleted; nothing is taken for it.
    Gone,
    /// The upload does not extend what the hub holds: here is what it holds.
    Mismatch { length: u64, hash: String },
    /// Another device owns the thread: publish this rollout under `fork`, in thread `thread`.
    Owned { fork: String, thread: String },
    /// The caller may not do that (a `watch` device, or a browser uploading).
    Forbidden(&'static str),
    BadRequest(&'static str),
    Io(String),
}

impl From<std::io::Error> for Refusal {
    fn from(err: std::io::Error) -> Refusal {
        Refusal::Io(err.to_string())
    }
}

/// A manifest: every change after a cursor.
#[derive(Debug, Default)]
pub struct Manifest {
    pub cursor: u64,
    pub rollouts: Vec<Record>,
    pub tombstones: Vec<Tombstone>,
}

#[derive(Debug, Default)]
struct Inner {
    seq: u64,
    rollouts: BTreeMap<String, Record>,
    tombstones: BTreeMap<String, Tombstone>,
}

impl Inner {
    fn of_thread(&self, thread: &str) -> Vec<Record> {
        self.rollouts.values().filter(|record| record.thread == thread).cloned().collect()
    }
}

/// The store. One lock around everything: it holds a few hundred rollouts, changed about once a
/// second while a turn runs.
#[derive(Debug)]
pub struct Store {
    dir: PathBuf,
    inner: Mutex<Inner>,
    pub clock: Clock,
    changed: tokio::sync::watch::Sender<u64>,
}

impl Store {
    /// Opens the store, creating its folders; cuts back any rollout longer than its record and
    /// drops tombstones older than [`TOMBSTONE_DAYS`].
    pub fn open(codex_home: &Path) -> std::io::Result<Store> {
        let dir = hub_dir(codex_home);
        auth::private_dir(&dir.join("rollouts"))?;
        auth::private_dir(&dir.join("tombstones"))?;
        let mut inner = Inner {
            seq: std::fs::read_to_string(dir.join("seq")).ok().and_then(|text| text.trim().parse().ok()).unwrap_or(0),
            ..Inner::default()
        };
        for entry in std::fs::read_dir(dir.join("rollouts"))?.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let Some(id) = name.strip_suffix(".jsonl").filter(|id| valid_id(id)) else { continue };
            let Some(record) = std::fs::read_to_string(dir.join("rollouts").join(format!("{id}.json")))
                .ok()
                .and_then(|text| serde_json::from_str::<Record>(&text).ok())
            else {
                // A rollout whose first upload never committed.
                let _ = std::fs::remove_file(entry.path());
                continue;
            };
            if entry.metadata().map(|m| m.len()).unwrap_or(0) > record.length {
                std::fs::OpenOptions::new().write(true).open(entry.path())?.set_len(record.length)?;
            }
            inner.seq = inner.seq.max(record.seq);
            inner.rollouts.insert(id.to_string(), record);
        }
        let oldest = now_secs().saturating_sub(TOMBSTONE_DAYS * 86_400);
        for entry in std::fs::read_dir(dir.join("tombstones"))?.flatten() {
            let Some(tombstone) = std::fs::read_to_string(entry.path())
                .ok()
                .and_then(|text| serde_json::from_str::<Tombstone>(&text).ok())
            else {
                continue;
            };
            inner.seq = inner.seq.max(tombstone.seq);
            if tombstone.at < oldest {
                let _ = std::fs::remove_file(entry.path());
                continue;
            }
            inner.tombstones.insert(tombstone.rollout.clone(), tombstone);
        }
        let (changed, _) = tokio::sync::watch::channel(inner.seq);
        Ok(Store { dir, inner: Mutex::new(inner), clock: Clock::default(), changed })
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn rollout_path(&self, rollout: &str) -> PathBuf {
        self.dir.join("rollouts").join(format!("{rollout}.jsonl"))
    }

    /// Takes the next sequence number and keeps it on disk before anything carries it.
    fn next_seq(&self, inner: &mut Inner) -> std::io::Result<u64> {
        inner.seq += 1;
        auth::write_private(&self.dir.join("seq"), &inner.seq.to_string())?;
        Ok(inner.seq)
    }

    fn save(&self, record: &Record) -> std::io::Result<()> {
        auth::write_private(&self.dir.join("rollouts").join(format!("{}.json", record.rollout)), &serde_json::to_string(record).unwrap_or_default())
    }

    fn announce(&self, seq: u64) {
        let _ = self.changed.send(seq);
    }

    /// Waits until the cursor passes `since`, or `wait` runs out.
    pub async fn wait_past(&self, since: u64, wait: std::time::Duration) {
        let mut receiver = self.changed.subscribe();
        if *receiver.borrow() > since {
            return;
        }
        let _ = tokio::time::timeout(wait, async {
            while receiver.changed().await.is_ok() {
                if *receiver.borrow() > since {
                    return;
                }
            }
        })
        .await;
    }

    pub fn cursor(&self) -> u64 {
        self.lock().seq
    }

    pub fn record(&self, rollout: &str) -> Option<Record> {
        self.lock().rollouts.get(rollout).cloned()
    }

    /// Every change after `since`: rollouts whose record changed, and tombstones.
    pub fn manifest(&self, since: u64) -> Manifest {
        let inner = self.lock();
        Manifest {
            cursor: inner.seq,
            rollouts: inner.rollouts.values().filter(|record| record.seq > since).cloned().collect(),
            tombstones: inner.tombstones.values().filter(|tombstone| tombstone.seq > since).cloned().collect(),
        }
    }

    /// Every thread, newest first.
    pub fn threads(&self) -> Vec<ThreadView> {
        let inner = self.lock();
        let mut grouped: BTreeMap<&str, Vec<&Record>> = BTreeMap::new();
        for record in inner.rollouts.values() {
            grouped.entry(record.thread.as_str()).or_default().push(record);
        }
        let mut threads: Vec<ThreadView> = grouped
            .into_values()
            .filter_map(|records| {
                let current = (*records.iter().max_by(|a, b| a.file.cmp(&b.file))?).clone();
                let oldest = records.iter().min_by(|a, b| a.file.cmp(&b.file))?;
                Some(ThreadView {
                    rollouts: records.len(),
                    created: records.iter().map(|record| record.created).min().unwrap_or(0),
                    updated: records.iter().map(|record| record.updated).max().unwrap_or(0),
                    preview: oldest.preview.clone().or_else(|| current.preview.clone()),
                    current,
                })
            })
            .collect();
        threads.sort_by_key(|view| std::cmp::Reverse(view.updated));
        threads
    }

    /// An upload from the thread's owner: `bytes` at `upload.offset` of rollout `rollout`,
    /// extending the prefix `upload.prefix` names.
    pub fn append(&self, rollout: &str, caller: &Caller, upload: &Upload, bytes: &[u8]) -> Result<Record, Refusal> {
        if !valid_id(rollout) {
            return Err(Refusal::NotFound);
        }
        if !caller.agent {
            return Err(Refusal::Forbidden("only a device's sync agent publishes its threads"));
        }
        if bytes.len() > UPLOAD_CAP {
            return Err(Refusal::BadRequest("an upload is at most 8 MiB"));
        }
        let mut inner = self.lock();
        if inner.tombstones.get(rollout).is_some_and(|tombstone| !tombstone.withdrawn || tombstone.by != caller.device) {
            return Err(Refusal::Gone);
        }
        if inner.tombstones.remove(rollout).is_some() {
            // Its owner publishes a withdrawn thread again: the withdrawal is over.
            let _ = std::fs::remove_file(self.dir.join("tombstones").join(format!("{rollout}.json")));
        }
        let mut record = match inner.rollouts.get(rollout).cloned() {
            Some(record) if record.owner != caller.device => {
                return Err(Refusal::Owned { fork: fork_id(rollout, &caller.device), thread: fork_id(&record.thread, &caller.device) });
            }
            Some(record) => record,
            None => self.new_record(&inner, rollout, caller, upload)?,
        };
        if upload.offset != record.length || upload.prefix != record.hash {
            return Err(Refusal::Mismatch { length: record.length, hash: record.hash.clone() });
        }
        if bytes.is_empty() {
            return Ok(record);
        }
        let path = self.rollout_path(rollout);
        // The tail since the last chunk boundary, to resume the chain from.
        let mut tail = Vec::new();
        if record.length > record.chain_len {
            let mut file = std::fs::File::open(&path)?;
            file.seek(std::io::SeekFrom::Start(record.chain_len))?;
            file.take(record.length - record.chain_len).read_to_end(&mut tail)?;
        }
        let mut chain = Chain::resume(&record.chain, record.chain_len, tail).ok_or(Refusal::Io("the record's chain is unreadable".to_string()))?;
        chain.push(bytes);
        {
            #[cfg(unix)]
            use std::os::unix::fs::OpenOptionsExt;
            let mut options = std::fs::OpenOptions::new();
            options.create(true).write(true);
            #[cfg(unix)]
            options.mode(0o600);
            let mut file = options.open(&path)?;
            file.set_len(record.length)?;
            file.seek(std::io::SeekFrom::Start(record.length))?;
            file.write_all(bytes)?;
            file.sync_data()?;
        }
        if record.preview.is_none() || record.cwd.is_none() {
            let facts = facts_of(&read_prefix(&path, 256 << 10).unwrap_or_default());
            record.preview = record.preview.take().or(facts.preview);
            record.cwd = record.cwd.take().or(facts.cwd);
            record.source = record.source.take().or(facts.source);
            record.base = record.base.take().or(facts.base);
        }
        record.length = chain.len();
        record.hash = chain.digest();
        (record.chain, record.chain_len) = chain.saved();
        record.updated = now_secs();
        record.seq = self.next_seq(&mut inner)?;
        self.save(&record)?;
        inner.rollouts.insert(rollout.to_string(), record.clone());
        drop(inner);
        self.announce(record.seq);
        Ok(record)
    }

    /// The record of a rollout's first upload: in its thread, which it joins only if the caller
    /// owns that thread; a continuation only under the id the hub gave it.
    fn new_record(&self, inner: &Inner, rollout: &str, caller: &Caller, upload: &Upload) -> Result<Record, Refusal> {
        let file = upload.file.as_deref().filter(|file| valid_file_name(file)).ok_or(Refusal::BadRequest("the first upload names the rollout's file"))?;
        let thread = upload.thread.clone().unwrap_or_else(|| rollout.to_string());
        if !valid_id(&thread) {
            return Err(Refusal::BadRequest("not a thread id"));
        }
        if inner.tombstones.values().any(|tombstone| tombstone.thread == thread && !(tombstone.withdrawn && tombstone.by == caller.device)) {
            return Err(Refusal::Gone);
        }
        let siblings = inner.of_thread(&thread);
        if let Some(sibling) = siblings.iter().find(|record| record.owner != caller.device) {
            return Err(Refusal::Owned { fork: fork_id(rollout, &caller.device), thread: fork_id(&sibling.thread, &caller.device) });
        }
        let mut record = Record {
            rollout: rollout.to_string(),
            thread: thread.clone(),
            owner: caller.device.clone(),
            owner_name: caller.name.clone(),
            file: file.to_string(),
            hash: Chain::new().digest(),
            chain: Chain::new().saved().0,
            archived: upload.archived,
            created: now_secs(),
            ..Record::default()
        };
        if let Some(sibling) = siblings.first() {
            // A later rollout of a thread (a revert) carries the thread's name and archive state.
            (record.archived, record.archived_hlc) = (sibling.archived, sibling.archived_hlc.clone());
            (record.name, record.name_hlc) = (sibling.name.clone(), sibling.name_hlc.clone());
        }
        if let Some(original) = upload.fork_of.as_deref() {
            let Some(source) = inner.rollouts.get(original).filter(|_| fork_id(original, &caller.device) == rollout) else {
                return Err(Refusal::BadRequest("not this device's continuation of that thread"));
            };
            let title = inner.of_thread(&source.thread).iter().find_map(|r| r.name.clone().or_else(|| r.preview.clone())).unwrap_or_else(|| "A thread".to_string());
            record.fork_of = Some(source.thread.clone());
            record.name = Some(format!("{title} (continued on {})", caller.name));
            record.name_hlc = self.clock.tick(&caller.device).to_string();
        }
        Ok(record)
    }

    /// Changes a thread's archive state or name, last writer wins by the clock. The owner may
    /// always; another device needs `control`. A reading not given is taken from the hub's clock.
    pub fn set_meta(&self, thread: &str, caller: &Caller, archived: Option<(bool, Option<Hlc>)>, name: Option<(String, Option<Hlc>)>) -> Result<Record, Refusal> {
        let mut inner = self.lock();
        let records = inner.of_thread(thread);
        let Some(first) = records.first().cloned() else { return Err(Refusal::NotFound) };
        if first.owner != caller.device && !caller.can_control() {
            return Err(Refusal::Forbidden("this device may watch threads, not change them"));
        }
        let mut changed = first.clone();
        let mut any = false;
        if let Some((value, hlc)) = archived {
            let hlc = self.reading(hlc, caller);
            if Hlc::parse(&changed.archived_hlc).is_none_or(|current| hlc > current) {
                (changed.archived, changed.archived_hlc) = (value, hlc.to_string());
                any = true;
            }
        }
        if let Some((value, hlc)) = name {
            let value: String = value.chars().filter(|c| !c.is_control()).take(200).collect();
            let hlc = self.reading(hlc, caller);
            if Hlc::parse(&changed.name_hlc).is_none_or(|current| hlc > current) {
                changed.name = Some(value.trim().to_string()).filter(|name| !name.is_empty());
                changed.name_hlc = hlc.to_string();
                any = true;
            }
        }
        if !any {
            return Ok(first);
        }
        let seq = self.next_seq(&mut inner)?;
        for mut record in records {
            (record.archived, record.archived_hlc) = (changed.archived, changed.archived_hlc.clone());
            (record.name, record.name_hlc) = (changed.name.clone(), changed.name_hlc.clone());
            record.seq = seq;
            self.save(&record)?;
            inner.rollouts.insert(record.rollout.clone(), record);
        }
        drop(inner);
        self.announce(seq);
        Ok(Record { seq, ..changed })
    }

    /// The caller's reading, or the hub's for a browser; either way the hub's clock moves past it.
    fn reading(&self, given: Option<Hlc>, caller: &Caller) -> Hlc {
        match given {
            Some(hlc) if caller.agent && hlc.device == caller.device => {
                self.clock.observe(&hlc);
                hlc
            }
            _ => self.clock.tick(&caller.device),
        }
    }

    /// Deletes a thread everywhere (§5.4): every rollout of it here now, every device's copy on
    /// its next manifest.
    pub fn delete(&self, thread: &str, caller: &Caller) -> Result<Vec<Tombstone>, Refusal> {
        self.remove(thread, caller, false)
    }

    /// Takes a thread off the hub at its owner's request (`[sync] exclude`, §4): gone from every
    /// other device's list, kept on the owner. Only the owner may.
    pub fn withdraw(&self, thread: &str, caller: &Caller) -> Result<Vec<Tombstone>, Refusal> {
        if self.lock().of_thread(thread).first().is_some_and(|record| record.owner != caller.device) {
            return Err(Refusal::Forbidden("only the device a thread is on takes it off the hub"));
        }
        self.remove(thread, caller, true)
    }

    fn remove(&self, thread: &str, caller: &Caller, withdrawn: bool) -> Result<Vec<Tombstone>, Refusal> {
        if !valid_id(thread) {
            return Err(Refusal::NotFound);
        }
        let mut inner = self.lock();
        let records = inner.of_thread(thread);
        if records.is_empty() {
            let gone: Vec<Tombstone> = inner.tombstones.values().filter(|t| t.thread == thread).cloned().collect();
            return if gone.is_empty() { Err(Refusal::NotFound) } else { Ok(gone) };
        }
        if records[0].owner != caller.device && !caller.can_control() {
            return Err(Refusal::Forbidden("this device may watch threads, not delete them"));
        }
        let seq = self.next_seq(&mut inner)?;
        let mut tombstones = Vec::new();
        for record in records {
            let tombstone = Tombstone { rollout: record.rollout.clone(), thread: thread.to_string(), seq, at: now_secs(), by: caller.device.clone(), withdrawn };
            auth::write_private(&self.dir.join("tombstones").join(format!("{}.json", record.rollout)), &serde_json::to_string(&tombstone).unwrap_or_default())?;
            let _ = std::fs::remove_file(self.rollout_path(&record.rollout));
            let _ = std::fs::remove_file(self.dir.join("rollouts").join(format!("{}.json", record.rollout)));
            inner.rollouts.remove(&record.rollout);
            inner.tombstones.insert(record.rollout.clone(), tombstone.clone());
            tombstones.push(tombstone);
        }
        drop(inner);
        self.announce(seq);
        Ok(tombstones)
    }

    /// The bytes of a rollout from `offset` (at most `limit`), with its record.
    pub fn read(&self, rollout: &str, offset: u64, limit: usize) -> Result<(Vec<u8>, Record), Refusal> {
        let record = self.record(rollout).ok_or(Refusal::NotFound)?;
        if offset > record.length {
            return Err(Refusal::BadRequest("past the end of the rollout"));
        }
        let mut file = std::fs::File::open(self.rollout_path(rollout))?;
        file.seek(std::io::SeekFrom::Start(offset))?;
        let mut bytes = Vec::new();
        file.take((record.length - offset).min(limit as u64)).read_to_end(&mut bytes)?;
        Ok((bytes, record))
    }

    /// A thread's transcript as a device shows it ([`items_of`]): its newest rollout, after the
    /// part of each earlier one it starts from.
    pub fn items(&self, thread: &str) -> Result<(Vec<Value>, ThreadView), Refusal> {
        let view = self.threads().into_iter().find(|view| view.current.thread == thread).ok_or(Refusal::NotFound)?;
        let mut chain = vec![(view.current.rollout.clone(), view.current.length)];
        let mut base = view.current.base.clone();
        while let Some(Base { rollout, end }) = base.take() {
            if chain.len() >= MAX_LINEAGE || chain.iter().any(|(seen, _)| *seen == rollout) {
                break;
            }
            let Some(record) = self.record(&rollout) else { break };
            chain.push((rollout, end.min(record.length)));
            base = record.base.clone();
        }
        let mut items = Vec::new();
        for (rollout, length) in chain.iter().rev() {
            items.extend(items_of(&read_prefix(&self.rollout_path(rollout), *length)?));
        }
        Ok((items, view))
    }

    /// The bytes the store holds, for `ling sync status`.
    pub fn bytes(&self) -> u64 {
        self.lock().rollouts.values().map(|record| record.length).sum()
    }
}

fn read_prefix(path: &Path, limit: u64) -> std::io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)?.take(limit).read_to_end(&mut bytes)?;
    Ok(bytes)
}

#[derive(Debug, Default, PartialEq)]
pub struct Facts {
    pub preview: Option<String>,
    pub cwd: Option<String>,
    pub source: Option<String>,
    pub base: Option<Base>,
}

/// What a rollout's first lines say about it: the folder, the source and the revert base from
/// `session_meta`, and the first thing the user asked.
pub fn facts_of(bytes: &[u8]) -> Facts {
    let mut facts = Facts::default();
    for line in bytes.split(|byte| *byte == b'\n') {
        let Ok(value) = serde_json::from_slice::<Value>(line) else { continue };
        if value.get("type").and_then(Value::as_str) == Some("session_meta")
            && let Some(payload) = value.get("payload")
        {
            facts.cwd = payload.get("cwd").and_then(Value::as_str).map(str::to_string);
            facts.source = payload.get("source").map(source_name);
            facts.base = payload.get("history_base").and_then(|base| {
                Some(Base { rollout: base.get("thread_id")?.as_str()?.to_string(), end: base.get("end_byte_offset")?.as_u64()? })
            });
        }
        if facts.preview.is_none()
            && let Some(text) = user_text(&value)
        {
            let first = text.lines().map(str::trim).find(|line| !line.is_empty()).unwrap_or_default();
            facts.preview = Some(first.chars().take(120).collect::<String>()).filter(|text| !text.is_empty());
        }
        if facts.preview.is_some() && facts.cwd.is_some() {
            break;
        }
    }
    facts
}

/// `session_meta.source` as one word: `"exec"`, `"cli"`, `"vscode"`, or an object's key
/// (`{"subagent": …}`).
pub fn source_name(source: &Value) -> String {
    match source {
        Value::String(name) => name.clone(),
        Value::Object(map) => map.keys().next().cloned().unwrap_or_default(),
        _ => String::new(),
    }
}

/// The user's own words on a line, from whichever record carries them.
fn user_text(value: &Value) -> Option<String> {
    let payload = value.get("payload")?;
    match (value.get("type")?.as_str()?, payload.get("type").and_then(Value::as_str)) {
        ("event_msg", Some("item_completed")) => {
            let item = payload.get("item")?;
            (item.get("type")?.as_str()? == "UserMessage").then(|| content_text(item.get("content").unwrap_or(&Value::Null)))
        }
        ("event_msg", Some("user_message")) => payload.get("message")?.as_str().map(str::to_string),
        _ => None,
    }
}

fn content_text(content: &Value) -> String {
    match content {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts.iter().filter_map(|part| part.get("text").and_then(Value::as_str)).collect::<Vec<_>>().join("\n"),
        _ => String::new(),
    }
}

fn capped(text: &str) -> Value {
    if text.len() <= SHOWN_TEXT_CAP {
        return json!(text);
    }
    let mut end = SHOWN_TEXT_CAP;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    json!(format!("{}\n… ({} more bytes on the device)", &text[..end], text.len() - end))
}

fn command_text(item: &Value) -> String {
    match item.get("command") {
        Some(Value::Array(parts)) => {
            let parts: Vec<&str> = parts.iter().filter_map(Value::as_str).collect();
            // `bash -lc "<script>"`: the script is what was asked for.
            if parts.len() == 3 && parts[1] == "-lc" { parts[2].to_string() } else { parts.join(" ") }
        }
        Some(Value::String(text)) => text.clone(),
        _ => String::new(),
    }
}

/// A rollout as a transcript a device can show without Codex: the user's and the agent's messages,
/// commands with their exit, tool calls, and where each turn ended. Rollouts from 0.158 carry the
/// app-server's items (`item_completed`); older ones only the event messages, which are read when
/// a rollout has no items. A partial last line (an upload cut mid-line) is skipped.
pub fn items_of(bytes: &[u8]) -> Vec<Value> {
    let lines: Vec<Value> = bytes.split(|byte| *byte == b'\n').filter_map(|line| serde_json::from_slice::<Value>(line).ok()).collect();
    let has_items = lines.iter().any(|value| value.pointer("/payload/type").and_then(Value::as_str) == Some("item_completed"));
    let mut items = Vec::new();
    for value in &lines {
        if value.get("type").and_then(Value::as_str) != Some("event_msg") {
            continue;
        }
        let at = value.get("timestamp").cloned().unwrap_or(Value::Null);
        let Some(payload) = value.get("payload") else { continue };
        match payload.get("type").and_then(Value::as_str).unwrap_or_default() {
            "item_completed" => {
                let Some(item) = payload.get("item") else { continue };
                let text = |key: &str| content_text(item.get(key).unwrap_or(&Value::Null));
                match item.get("type").and_then(Value::as_str).unwrap_or_default() {
                    "UserMessage" => items.push(json!({ "kind": "user", "text": capped(&text("content")), "at": at })),
                    "AgentMessage" => items.push(json!({ "kind": "agent", "text": capped(&text("content")), "at": at })),
                    "CommandExecution" => {
                        let output = item.get("aggregated_output").or_else(|| item.get("stdout")).and_then(Value::as_str).unwrap_or_default();
                        let lines: Vec<&str> = output.lines().collect();
                        let tail = lines[lines.len().saturating_sub(20)..].join("\n");
                        items.push(json!({
                            "kind": "command",
                            "text": capped(&command_text(item)),
                            "status": item.get("status").cloned().unwrap_or(Value::Null),
                            "exit_code": item.get("exit_code").cloned().unwrap_or(Value::Null),
                            "output": capped(&tail),
                            "at": at,
                        }));
                    }
                    "FileChange" => {
                        let files: Vec<String> = match item.get("changes") {
                            Some(Value::Array(changes)) => changes.iter().filter_map(|c| c.get("path").and_then(Value::as_str).map(str::to_string)).collect(),
                            Some(Value::Object(changes)) => changes.keys().cloned().collect(),
                            _ => Vec::new(),
                        };
                        items.push(json!({ "kind": "files", "text": files.join("\n"), "at": at }));
                    }
                    "McpToolCall" => {
                        let server = item.get("server").and_then(Value::as_str).unwrap_or_default();
                        let tool = item.get("tool").and_then(Value::as_str).unwrap_or_default();
                        items.push(json!({ "kind": "tool", "text": format!("{server}.{tool}"), "status": item.get("status").cloned().unwrap_or(Value::Null), "at": at }));
                    }
                    "WebSearch" => {
                        let query = item.get("query").and_then(Value::as_str).unwrap_or_default();
                        items.push(json!({ "kind": "tool", "text": format!("web search: {query}"), "at": at }));
                    }
                    _ => {}
                }
            }
            "user_message" if !has_items => {
                items.push(json!({ "kind": "user", "text": capped(payload.get("message").and_then(Value::as_str).unwrap_or_default()), "at": at }))
            }
            "agent_message" if !has_items => {
                items.push(json!({ "kind": "agent", "text": capped(payload.get("message").and_then(Value::as_str).unwrap_or_default()), "at": at }))
            }
            "task_complete" => items.push(json!({ "kind": "turn", "text": "Turn finished", "at": at })),
            "turn_aborted" => {
                let reason = payload.get("reason").and_then(Value::as_str).unwrap_or("interrupted");
                items.push(json!({ "kind": "turn", "text": format!("Turn stopped ({reason})"), "at": at }))
            }
            _ => {}
        }
    }
    items
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-sync-store-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn agent(device: &str) -> Caller {
        Caller { device: device.to_string(), name: format!("{device}'s machine"), agent: true, scope: auth::Scope::Control }
    }

    const ID: &str = "01a0f1ae-2da4-7942-99ae-c3151dc137cc";
    const FILE: &str = "rollout-2026-09-30T10-38-40-01a0f1ae-2da4-7942-99ae-c3151dc137cc.jsonl";

    #[test]
    fn the_prefix_hash_depends_on_the_bytes_alone() {
        let bytes: Vec<u8> = (0..(3 * CHUNK + 777)).map(|i| (i % 251) as u8).collect();
        let whole = prefix_hash(&bytes);
        for split in [1, 1000, CHUNK - 1, CHUNK, CHUNK + 1, 2 * CHUNK + 5] {
            let mut chain = Chain::new();
            chain.push(&bytes[..split]);
            chain.push(&bytes[split..]);
            assert_eq!(chain.digest(), whole, "split at {split}");
        }
        let mut chain = Chain::new();
        chain.push(&bytes[..2 * CHUNK + 10]);
        let (full, full_len) = chain.saved();
        let mut resumed = Chain::resume(&full, full_len, bytes[full_len as usize..2 * CHUNK + 10].to_vec()).unwrap();
        resumed.push(&bytes[2 * CHUNK + 10..]);
        assert_eq!(resumed.digest(), whole);
        assert_ne!(prefix_hash(&bytes[..10]), prefix_hash(&bytes[..11]));
    }

    #[test]
    fn an_upload_is_taken_only_at_the_hubs_length_with_a_matching_prefix() {
        let home = scratch("append");
        let store = Store::open(&home).unwrap();
        let laptop = agent("lap1");
        let first = b"{\"type\":\"session_meta\",\"payload\":{\"cwd\":\"/w\",\"source\":\"vscode\"}}\n";
        let record = store.append(ID, &laptop, &Upload::first(FILE), first).unwrap();
        assert_eq!((record.length, record.cwd.as_deref(), record.source.as_deref()), (first.len() as u64, Some("/w"), Some("vscode")));
        assert_eq!(record.thread, ID);
        // Wrong offset, wrong prefix: refused with what the hub holds.
        let more = b"{\"type\":\"event_msg\",\"payload\":{\"type\":\"user_message\",\"message\":\"Fix the login\"}}\n";
        let held = Refusal::Mismatch { length: first.len() as u64, hash: prefix_hash(first) };
        assert_eq!(store.append(ID, &laptop, &Upload::at(0, &prefix_hash(b"")), more).unwrap_err(), held);
        assert_eq!(store.append(ID, &laptop, &Upload::at(first.len() as u64, &prefix_hash(b"x")), more).unwrap_err(), held);
        let record = store.append(ID, &laptop, &Upload::at(first.len() as u64, &prefix_hash(first)), more).unwrap();
        assert_eq!(record.preview.as_deref(), Some("Fix the login"));
        let mut whole = first.to_vec();
        whole.extend_from_slice(more);
        assert_eq!(record.hash, prefix_hash(&whole));
        assert_eq!(store.read(ID, 0, usize::MAX).unwrap().0, whole);
        assert_eq!(store.read(ID, first.len() as u64, usize::MAX).unwrap().0, more.to_vec());
        assert_eq!(store.threads()[0].title(), "Fix the login");
        // A browser never uploads; a bad id names nothing.
        let browser = Caller { agent: false, ..laptop.clone() };
        assert!(matches!(store.append(ID, &browser, &Upload::first(FILE), b"x"), Err(Refusal::Forbidden(_))));
        assert_eq!(store.append("../etc", &laptop, &Upload::first(FILE), b"x").unwrap_err(), Refusal::NotFound);
    }

    #[test]
    fn a_second_device_extending_a_thread_gets_its_own_continued_thread() {
        let home = scratch("split");
        let store = Store::open(&home).unwrap();
        let (laptop, desktop) = (agent("lap1"), agent("desk"));
        let opening = b"{\"type\":\"event_msg\",\"payload\":{\"type\":\"user_message\",\"message\":\"Plan the trip\"}}\n";
        store.append(ID, &laptop, &Upload::first(FILE), opening).unwrap();
        let (fork, thread) = match store.append(ID, &desktop, &Upload::first(FILE), opening) {
            Err(Refusal::Owned { fork, thread }) => (fork, thread),
            other => panic!("{other:?}"),
        };
        assert!(valid_id(&fork) && fork == fork_id(ID, "desk") && thread == fork);
        let upload = Upload { thread: Some(thread.clone()), fork_of: Some(ID.to_string()), ..Upload::first(FILE) };
        let record = store.append(&fork, &desktop, &upload, b"{\"a\":1}\n{\"b\":2}\n").unwrap();
        assert_eq!(record.fork_of.as_deref(), Some(ID));
        assert_eq!(record.name.as_deref(), Some("Plan the trip (continued on desk's machine)"));
        assert_eq!(record.owner, "desk");
        // The original is untouched; a continuation that is not the caller's own is refused.
        assert_eq!(store.record(ID).unwrap().length, opening.len() as u64);
        let wrong = Upload { fork_of: Some(ID.to_string()), ..Upload::first(FILE) };
        assert!(matches!(store.append(&fork_id(ID, "x"), &desktop, &wrong, b"y\n"), Err(Refusal::BadRequest(_))));
        // A new rollout in a thread another device owns is a continuation too.
        let other = "01a0f1ae-2da4-7942-99ae-c3151dc13700";
        let into = Upload { thread: Some(ID.to_string()), ..Upload::first(FILE) };
        assert!(matches!(store.append(other, &desktop, &into, b"z\n"), Err(Refusal::Owned { .. })));
    }

    #[test]
    fn a_reverted_thread_reads_on_from_the_part_of_the_rollout_it_keeps() {
        let home = scratch("revert");
        let store = Store::open(&home).unwrap();
        let laptop = agent("lap1");
        let say = |who: &str, text: &str| format!("{{\"type\":\"event_msg\",\"payload\":{{\"type\":\"item_completed\",\"item\":{{\"type\":\"{who}\",\"content\":[{{\"type\":\"text\",\"text\":\"{text}\"}}]}}}}}}\n");
        let kept = format!("{}{}", say("UserMessage", "one"), say("AgentMessage", "first"));
        let original = format!("{kept}{}", say("UserMessage", "taken back"));
        store.append(ID, &laptop, &Upload::first(FILE), original.as_bytes()).unwrap();
        store.set_meta(ID, &laptop, None, Some(("Named".to_string(), None))).unwrap();
        let reverted = "01a0f1ae-2da4-7942-99ae-c3151dc1ffff";
        let meta = format!("{{\"type\":\"session_meta\",\"payload\":{{\"id\":\"{ID}\",\"history_base\":{{\"thread_id\":\"{ID}\",\"end_ordinal_exclusive\":2,\"end_byte_offset\":{}}}}}}}\n", kept.len());
        let second = format!("{meta}{}", say("UserMessage", "two"));
        let upload = Upload { thread: Some(ID.to_string()), ..Upload::first(&format!("rollout-2026-09-30T11-00-00-{ID}_{reverted}.jsonl")) };
        let record = store.append(reverted, &laptop, &upload, second.as_bytes()).unwrap();
        assert_eq!(record.base, Some(Base { rollout: ID.to_string(), end: kept.len() as u64 }));
        assert_eq!(record.name.as_deref(), Some("Named"), "a revert keeps the thread's name");
        let (items, view) = store.items(ID).unwrap();
        let texts: Vec<&str> = items.iter().map(|item| item["text"].as_str().unwrap()).collect();
        assert_eq!(texts, ["one", "first", "two"]);
        assert_eq!((view.rollouts, view.current.rollout.as_str()), (2, reverted));
        assert_eq!(store.threads().len(), 1);
        // Deleting the thread deletes both.
        assert_eq!(store.delete(ID, &laptop).unwrap().len(), 2);
        assert!(store.threads().is_empty());
    }

    #[test]
    fn cursors_never_skip_or_repeat_across_restarts_and_a_cut_write_is_undone() {
        let home = scratch("restart");
        let laptop = agent("lap1");
        let (first_cursor, length) = {
            let store = Store::open(&home).unwrap();
            store.append(ID, &laptop, &Upload::first(FILE), b"{\"a\":1}\n").unwrap();
            let manifest = store.manifest(0);
            assert_eq!(manifest.rollouts.len(), 1);
            (manifest.cursor, store.record(ID).unwrap().length)
        };
        // A write that never reached its record.
        let rollout = hub_dir(&home).join("rollouts").join(format!("{ID}.jsonl"));
        std::fs::OpenOptions::new().append(true).open(&rollout).unwrap().write_all(b"{\"cut").unwrap();
        let store = Store::open(&home).unwrap();
        assert_eq!(std::fs::metadata(&rollout).unwrap().len(), length);
        assert_eq!(store.cursor(), first_cursor);
        assert!(store.manifest(first_cursor).rollouts.is_empty());
        store.delete(ID, &laptop).unwrap();
        let after = store.manifest(first_cursor);
        assert_eq!((after.cursor, after.tombstones.len()), (first_cursor + 1, 1));
        // The deleted rollout's record is gone, but its number is not issued again.
        drop(store);
        let store = Store::open(&home).unwrap();
        assert_eq!(store.cursor(), first_cursor + 1);
        assert_eq!(store.append(ID, &laptop, &Upload::first(FILE), b"x\n").unwrap_err(), Refusal::Gone);
    }

    #[test]
    fn tombstones_outlive_a_months_absence_and_go_after_ninety_days() {
        let home = scratch("tombstones");
        let store = Store::open(&home).unwrap();
        let laptop = agent("lap1");
        store.append(ID, &laptop, &Upload::first(FILE), b"{}\n").unwrap();
        store.delete(ID, &laptop).unwrap();
        let path = hub_dir(&home).join("tombstones").join(format!("{ID}.json"));
        let mut tombstone: Tombstone = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        tombstone.at = now_secs() - 31 * 86_400;
        std::fs::write(&path, serde_json::to_string(&tombstone).unwrap()).unwrap();
        drop(store);
        assert_eq!(Store::open(&home).unwrap().manifest(0).tombstones.len(), 1, "a device back after 31 days learns of it");
        tombstone.at = now_secs() - 91 * 86_400;
        std::fs::write(&path, serde_json::to_string(&tombstone).unwrap()).unwrap();
        assert!(Store::open(&home).unwrap().manifest(0).tombstones.is_empty());
        assert!(!path.exists());
    }

    #[test]
    fn a_watch_device_reads_but_changes_nothing_and_an_owner_always_may() {
        let home = scratch("scopes");
        let store = Store::open(&home).unwrap();
        let laptop = Caller { scope: auth::Scope::Watch, ..agent("lap1") };
        store.append(ID, &laptop, &Upload::first(FILE), b"{}\n").unwrap();
        let phone = Caller { device: "ph1".to_string(), name: "phone".to_string(), agent: false, scope: auth::Scope::Watch };
        assert!(matches!(store.set_meta(ID, &phone, Some((true, None)), None), Err(Refusal::Forbidden(_))));
        assert!(matches!(store.delete(ID, &phone), Err(Refusal::Forbidden(_))));
        assert!(store.read(ID, 0, 10).is_ok());
        // The owner's own thread, though its scope is watch.
        assert!(store.set_meta(ID, &laptop, Some((true, None)), None).unwrap().archived);
        let phone = Caller { scope: auth::Scope::Control, ..phone };
        store.set_meta(ID, &phone, None, Some(("Login fix".to_string(), None))).unwrap();
        assert_eq!(store.threads()[0].title(), "Login fix");
        assert!(store.delete(ID, &phone).is_ok());
    }

    #[test]
    fn the_clock_orders_a_device_whose_clock_is_wrong() {
        let home = scratch("clock");
        let store = Store::open(&home).unwrap();
        let ahead = agent("fast");
        store.append(ID, &ahead, &Upload::first(FILE), b"{}\n").unwrap();
        // A day ahead: its rename wins over a correct device's earlier one...
        let tomorrow = Hlc { ms: now_ms() + 86_400_000, counter: 0, device: "fast".to_string() };
        store.set_meta(ID, &ahead, None, Some(("From the future".to_string(), Some(tomorrow.clone())))).unwrap();
        let laptop = Caller { device: "lap1".to_string(), ..agent("lap1") };
        let stale = Hlc { ms: now_ms(), counter: 0, device: "lap1".to_string() };
        assert_eq!(store.set_meta(ID, &laptop, None, Some(("Now".to_string(), Some(stale)))).unwrap().name.as_deref(), Some("From the future"));
        // ...but a device that has seen it ticks past it, so it cannot win forever.
        let clock = Clock::default();
        clock.observe(&Hlc::parse(&store.record(ID).unwrap().name_hlc).unwrap());
        let next = clock.tick("lap1");
        assert!(next > tomorrow);
        assert_eq!(store.set_meta(ID, &laptop, None, Some(("Now".to_string(), Some(next)))).unwrap().name.as_deref(), Some("Now"));
        // A browser's change is read on the hub's clock, which has seen both.
        let phone = Caller { device: "ph1".to_string(), name: "phone".to_string(), agent: false, scope: auth::Scope::Control };
        assert_eq!(store.set_meta(ID, &phone, None, Some(("Phone".to_string(), None))).unwrap().name.as_deref(), Some("Phone"));
        assert_eq!(Hlc::parse("12.3.dev"), Some(Hlc { ms: 12, counter: 3, device: "dev".to_string() }));
        assert!(Hlc::parse("12.x.dev").is_none() && Hlc::parse("12.3.").is_none());
    }

    #[test]
    fn a_transcript_reads_items_or_falls_back_to_event_messages() {
        let items = items_of(
            br#"{"timestamp":"t0","type":"session_meta","payload":{"cwd":"/w","source":"cli"}}
{"timestamp":"t1","type":"event_msg","payload":{"type":"item_completed","item":{"type":"UserMessage","content":[{"type":"text","text":"hi!"}]}}}
{"timestamp":"t2","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","command":["/bin/bash","-lc","ls -la"],"status":"completed","exit_code":0,"stdout":"a\nb\n"}}}
{"timestamp":"t3","type":"event_msg","payload":{"type":"item_completed","item":{"type":"AgentMessage","content":[{"type":"Text","text":"Hello"}]}}}
{"timestamp":"t4","type":"event_msg","payload":{"type":"agent_message","message":"Hello"}}
{"timestamp":"t5","type":"event_msg","payload":{"type":"task_complete"}}
{"timestamp":"t6","type":"event_msg","payload":{"type":"item_compl"#,
        );
        let kinds: Vec<&str> = items.iter().map(|item| item["kind"].as_str().unwrap()).collect();
        assert_eq!(kinds, ["user", "command", "agent", "turn"]);
        assert_eq!((items[1]["text"].as_str(), items[1]["output"].as_str()), (Some("ls -la"), Some("a\nb")));
        let old = items_of(b"{\"type\":\"event_msg\",\"payload\":{\"type\":\"user_message\",\"message\":\"q\"}}\n{\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"message\":\"a\"}}\n");
        assert_eq!(old.iter().map(|item| item["text"].as_str().unwrap()).collect::<Vec<_>>(), ["q", "a"]);
        let long = "x".repeat(SHOWN_TEXT_CAP + 10);
        assert!(capped(&long).as_str().unwrap().ends_with("(10 more bytes on the device)"));
    }

    #[test]
    fn a_withdrawn_thread_leaves_the_hub_and_only_its_owner_may_withdraw_it() {
        let home = scratch("withdraw");
        let store = Store::open(&home).unwrap();
        let laptop = agent("lap1");
        store.append(ID, &laptop, &Upload::first(FILE), b"{}\n").unwrap();
        let phone = Caller { device: "ph1".to_string(), name: "phone".to_string(), agent: false, scope: auth::Scope::Control };
        assert!(matches!(store.withdraw(ID, &phone), Err(Refusal::Forbidden(_))));
        let tombstones = store.withdraw(ID, &laptop).unwrap();
        assert!(tombstones[0].withdrawn);
        assert!(store.threads().is_empty());
        // Should the owner publish it again (its folder no longer excluded), it may.
        assert!(store.append(ID, &laptop, &Upload::first(FILE), b"{}\n").is_ok());
    }

    #[test]
    fn ids_and_names_are_checked() {
        assert!(valid_id(ID));
        assert!(!valid_id("01A0F1AE-2DA4-7942-99AE-C3151DC137CC") && !valid_id("../x") && !valid_id(""));
        assert!(valid_file_name(FILE));
        assert!(!valid_file_name("rollout-../../x.jsonl") && !valid_file_name("x.jsonl"));
    }

    #[test]
    fn the_hub_is_off_until_enabled_and_off_keeps_the_store() {
        let home = scratch("enabled");
        assert!(!enabled(&home));
        assert!(!hub_dir(&home).exists());
        set_enabled(&home, true).unwrap();
        assert!(enabled(&home));
        let store = Store::open(&home).unwrap();
        store.append(ID, &agent("lap1"), &Upload::first(FILE), b"{}\n").unwrap();
        assert_eq!(summary(&home), (1, 3));
        set_enabled(&home, false).unwrap();
        assert!(!enabled(&home) && hub_dir(&home).join("rollouts").exists());
        forget(&home).unwrap();
        assert!(!hub_dir(&home).exists());
    }
}
