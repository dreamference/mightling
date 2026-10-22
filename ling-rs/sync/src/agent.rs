//! The sync agent (specs/DREAMFERENCE_MIGHTLING_SYNC.md §3, §5): one cycle a second, on every
//! enabled device, the node included.
//!
//! Each cycle it:
//! 1. scans `sessions/` and `archived_sessions/` and uploads what each of this device's interactive
//!    rollouts gained since the last cycle, whole lines only, by offset and prefix hash. `ling exec`
//!    sessions stay out unless `[sync] exec`, and a thread whose folder is under `[sync] exclude`
//!    never leaves the device (one published before its folder was excluded is withdrawn);
//! 2. publishes the archive state (where the thread's newest rollout lies) and the name
//!    (`session_index.jsonl`) of each published thread when they change, with its clock's reading;
//! 3. deletes a thread on the hub once every rollout of it has been gone from this device for two
//!    scans in a row (`ling delete`, or the TUI's `/delete`);
//! 4. reads the hub's manifest and applies what other devices decided about this device's threads:
//!    a deletion with `ling delete --force <thread>` and an archive state with `ling archive` or
//!    `ling unarchive`, Codex's own commands, never by moving files (Phase 0 found that moving a
//!    rollout breaks Codex's reads of it).
//!
//! It never writes another device's thread into this device's store (that is `[sync] replicate`,
//! Phase 4), and it keeps its own state in `~/.mightling/sync/agent.json`.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use ling_web_server::auth;
use ling_web_server::sync_store;
use ling_web_server::sync_store::Chain;
use ling_web_server::sync_store::Clock;
use ling_web_server::sync_store::Hlc;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;

use crate::client::HubClient;
use crate::client::UploadParams;
use crate::client::Uploaded;
use crate::config::DeviceFile;
use crate::config::Settings;
use crate::config::sync_dir;
use crate::scan;
use crate::scan::Local;

/// How often a running agent looks.
pub const INTERVAL: Duration = Duration::from_secs(1);
/// How many scans a thread must be missing in before it counts as deleted here.
const MISSING_SCANS: u32 = 2;
/// How long one of Codex's commands may take.
const COMMAND_TIMEOUT: Duration = Duration::from_secs(120);

/// What the agent remembers of one of this device's rollouts.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RolloutState {
    pub thread: String,
    /// The ids the hub keeps it under: its own, or a continuation's when another device owned
    /// them first (§8).
    pub hub_rollout: String,
    pub hub_thread: String,
    pub fork_of: Option<String>,
    pub file: String,
    /// How much the hub holds, and its hash; the chain at the last chunk boundary.
    pub offset: u64,
    pub hash: String,
    pub chain: String,
    pub chain_len: u64,
    /// The file's size and modification time when it was last read to its end.
    pub stamp: (u64, u128),
    /// `session_meta.source` and `cwd`, read once.
    pub source: String,
    pub cwd: String,
    /// Why it is not published, when it is not.
    pub held: Option<String>,
}

/// What the agent remembers of one of this device's threads.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ThreadState {
    pub hub_thread: String,
    /// The archive state and name last published.
    pub archived: Option<bool>,
    pub name: Option<String>,
    /// The newest archive reading from another device that was applied here.
    pub applied_archive: String,
    /// Scans in a row in which no rollout of it was found.
    pub missing: u32,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct State {
    pub cursor: u64,
    pub rollouts: BTreeMap<String, RolloutState>,
    pub threads: BTreeMap<String, ThreadState>,
    /// Threads deleted on the hub: never published again from here.
    pub deleted: BTreeSet<String>,
    pub clock: String,
}

/// What one cycle did, for `ling sync status` and the tests.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct Report {
    pub uploads: usize,
    pub bytes: u64,
    pub published: Vec<String>,
    pub deleted_on_hub: Vec<String>,
    pub applied: Vec<String>,
    pub errors: Vec<String>,
}

pub struct Agent {
    codex_home: PathBuf,
    ling: PathBuf,
    pub device: DeviceFile,
    pub settings: Settings,
    pub state: State,
    clock: Clock,
    /// The state as last written, so an idle cycle writes nothing.
    saved: String,
    /// The status as last written, and when.
    status_written: (String, std::time::Instant),
}

fn state_path(codex_home: &Path) -> PathBuf {
    sync_dir(codex_home).join("agent.json")
}

pub fn status_path(codex_home: &Path) -> PathBuf {
    sync_dir(codex_home).join("agent-status.json")
}

impl Agent {
    /// The agent of a paired device; `None` before `ling sync enable`.
    pub fn load(codex_home: &Path, ling: &Path, settings: Settings) -> Option<Agent> {
        let device = DeviceFile::load(codex_home)?;
        let state: State = std::fs::read_to_string(state_path(codex_home)).ok().and_then(|text| serde_json::from_str(&text).ok()).unwrap_or_default();
        let clock = Clock::default();
        if let Some(seen) = Hlc::parse(&state.clock) {
            clock.observe(&seen);
        }
        let saved = serde_json::to_string(&State { clock: String::new(), ..state.clone() }).unwrap_or_default();
        let status_written = (String::new(), std::time::Instant::now());
        Some(Agent { codex_home: codex_home.to_path_buf(), ling: ling.to_path_buf(), device, settings, state, clock, saved, status_written })
    }

    /// Keeps the state, when a cycle changed it: an idle agent writes nothing.
    pub fn save(&mut self) -> std::io::Result<()> {
        let now = serde_json::to_string(&State { clock: String::new(), ..self.state.clone() }).unwrap_or_default();
        if now == self.saved {
            return Ok(());
        }
        self.state.clock = self.clock.tick(&self.device.device).to_string();
        auth::write_private(&state_path(&self.codex_home), &serde_json::to_string(&self.state).unwrap_or_default())?;
        self.saved = now;
        Ok(())
    }

    fn reading(&self) -> String {
        self.clock.tick(&self.device.device).to_string()
    }

    /// One cycle against the hub.
    pub async fn cycle(&mut self, hub: &HubClient) -> Report {
        let mut report = Report::default();
        let locals = scan::scan(&self.codex_home);
        self.publish(hub, &locals, &mut report).await;
        self.publish_meta(hub, &locals, &mut report).await;
        self.notice_deletions(hub, &locals, &mut report).await;
        if let Err(err) = self.apply_manifest(hub, &mut report).await {
            report.errors.push(err);
        }
        if let Err(err) = self.save() {
            report.errors.push(format!("could not keep the agent's state: {err}"));
        }
        report
    }

    /// Whether a rollout stays on this device, and why.
    fn hold_reason(&self, state: &RolloutState) -> Option<String> {
        if self.state.deleted.contains(&state.thread) {
            return Some("deleted on the hub".to_string());
        }
        if state.source == "exec" && !self.settings.exec {
            return Some("a `ling exec` session ([sync] exec is off)".to_string());
        }
        if !state.cwd.is_empty() && self.settings.excluded(&state.cwd) {
            return Some("its folder is excluded ([sync] exclude)".to_string());
        }
        None
    }

    async fn publish(&mut self, hub: &HubClient, locals: &[Local], report: &mut Report) {
        for local in locals {
            let entry = self.state.rollouts.entry(local.rollout.clone()).or_insert_with(|| {
                let meta = scan::session_meta(local).unwrap_or_default();
                RolloutState {
                    thread: local.thread.clone(),
                    hub_rollout: local.rollout.clone(),
                    hub_thread: local.thread.clone(),
                    file: local.file.clone(),
                    hash: sync_store::prefix_hash(b""),
                    chain: Chain::new().saved().0,
                    source: meta.get("source").map(sync_store::source_name).unwrap_or_default(),
                    cwd: meta.get("cwd").and_then(Value::as_str).unwrap_or_default().to_string(),
                    ..RolloutState::default()
                }
            });
            if entry.source.is_empty() && entry.offset == 0 {
                // Codex writes `session_meta` first; a file caught before it is read again.
                let meta = scan::session_meta(local).unwrap_or_default();
                entry.source = meta.get("source").map(sync_store::source_name).unwrap_or_default();
                entry.cwd = meta.get("cwd").and_then(Value::as_str).unwrap_or_default().to_string();
            }
            let state = entry.clone();
            let held = self.hold_reason(&state);
            let excluded_now = held.as_deref().is_some_and(|why| why.contains("exclude")) && state.held.is_none() && state.offset > 0;
            if let Some(entry) = self.state.rollouts.get_mut(&local.rollout) {
                entry.held = held.clone();
            }
            if excluded_now {
                // Published before its folder was excluded: off the hub, kept here.
                match hub.withdraw(&state.hub_thread).await {
                    Ok(_) => {
                        if let Some(entry) = self.state.rollouts.get_mut(&local.rollout) {
                            (entry.offset, entry.hash, entry.chain, entry.chain_len, entry.stamp) =
                                (0, sync_store::prefix_hash(b""), Chain::new().saved().0, 0, (0, 0));
                        }
                        self.state.threads.remove(&state.thread);
                    }
                    Err(err) => report.errors.push(err),
                }
                continue;
            }
            if held.is_some() || state.source.is_empty() || state.stamp == local.stamp {
                continue;
            }
            match self.upload(hub, local, report).await {
                Ok(()) => {}
                Err(err) => report.errors.push(format!("{}: {err}", local.file)),
            }
        }
    }

    /// Uploads what one rollout gained, from what the hub holds to its last whole line.
    async fn upload(&mut self, hub: &HubClient, local: &Local, report: &mut Report) -> Result<(), String> {
        let Some(mut state) = self.state.rollouts.get(&local.rollout).cloned() else { return Ok(()) };
        // Read from the last chunk boundary: the bytes before `offset` resume the chain.
        let bytes = scan::read_from(local, state.chain_len).map_err(|err| format!("could not read it: {err}"))?;
        let whole = state.chain_len + scan::whole_lines(&bytes) as u64;
        if whole < state.offset {
            return Err("it is shorter than what the hub holds; it was rewritten".to_string());
        }
        let tail_len = (state.offset - state.chain_len) as usize;
        let mut chain = Chain::resume(&state.chain, state.chain_len, bytes[..tail_len].to_vec()).ok_or("the agent's state is unreadable")?;
        let mut new = &bytes[tail_len..(whole - state.chain_len) as usize];
        while !new.is_empty() {
            let piece = &new[..new.len().min(sync_store::UPLOAD_CAP)];
            let params = UploadParams {
                offset: state.offset,
                prefix: chain.digest(),
                thread: (state.offset == 0).then(|| state.hub_thread.clone()),
                file: (state.offset == 0).then(|| state.file.clone()),
                archived: local.archived,
                fork_of: state.fork_of.clone().filter(|_| state.offset == 0),
            };
            match hub.upload(&state.hub_rollout, &params, piece.to_vec()).await? {
                Uploaded::Taken { length, hash } => {
                    chain.push(piece);
                    if length != chain.len() || hash != chain.digest() {
                        return Err("the hub's hash differs from this device's".to_string());
                    }
                    state.offset = length;
                    state.hash = hash;
                    new = &new[piece.len()..];
                    report.uploads += 1;
                    report.bytes += piece.len() as u64;
                }
                Uploaded::Mismatch { length, hash } => {
                    // The hub holds more or other than this agent remembers (its state was lost,
                    // or an upload's answer was): resume where the hub is, if it holds this
                    // device's own bytes.
                    let all = scan::read_from(local, 0).map_err(|err| err.to_string())?;
                    if length as usize > all.len() || sync_store::prefix_hash(&all[..length as usize]) != hash {
                        state.held = Some("the hub's copy differs from this device's file".to_string());
                        self.state.rollouts.insert(local.rollout.clone(), state);
                        return Err("the hub's copy differs from this device's file".to_string());
                    }
                    let boundary = length - length % sync_store::CHUNK as u64;
                    chain = Chain::new();
                    chain.push(&all[..boundary as usize]);
                    (state.chain, state.chain_len) = chain.saved();
                    chain.push(&all[boundary as usize..length as usize]);
                    (state.offset, state.hash) = (length, hash);
                    self.state.rollouts.insert(local.rollout.clone(), state);
                    // The next cycle continues from there.
                    return Ok(());
                }
                Uploaded::Owned { fork, thread } => {
                    state.fork_of = Some(state.hub_rollout.clone());
                    state.hub_rollout = fork;
                    state.hub_thread = thread;
                    (state.offset, state.hash, state.chain, state.chain_len) = (0, sync_store::prefix_hash(b""), Chain::new().saved().0, 0);
                    self.state.rollouts.insert(local.rollout.clone(), state);
                    return Ok(());
                }
                Uploaded::Gone => {
                    self.state.deleted.insert(state.thread.clone());
                    state.held = Some("deleted on the hub".to_string());
                    self.state.rollouts.insert(local.rollout.clone(), state);
                    return Ok(());
                }
            }
        }
        // Keep the chain at its last boundary, and the file's stamp once it is read to its end.
        let boundary = state.offset - state.offset % sync_store::CHUNK as u64;
        if boundary > state.chain_len {
            let mut fresh = Chain::new();
            let all = scan::read_from(local, 0).map_err(|err| err.to_string())?;
            fresh.push(&all[..boundary as usize]);
            (state.chain, state.chain_len) = fresh.saved();
        }
        if whole == state.chain_len + bytes.len() as u64 {
            state.stamp = local.stamp;
        }
        if state.offset > 0 {
            report.published.push(state.hub_thread.clone());
            self.state.threads.entry(state.thread.clone()).or_default().hub_thread = state.hub_thread.clone();
        }
        self.state.rollouts.insert(local.rollout.clone(), state);
        Ok(())
    }

    /// Archive state and names of the threads this device has published.
    async fn publish_meta(&mut self, hub: &HubClient, locals: &[Local], report: &mut Report) {
        // A few hundred bytes a thread: read whole each cycle (a deletion rewrites it).
        let names = scan::names(&self.codex_home);
        let threads: Vec<String> = self.state.threads.keys().cloned().collect();
        for thread in threads {
            let mine: Vec<&Local> = locals.iter().filter(|local| local.thread == thread).collect();
            let Some(newest) = mine.iter().max_by(|a, b| a.file.cmp(&b.file)) else { continue };
            let Some(state) = self.state.threads.get(&thread).cloned() else { continue };
            let mut body = serde_json::Map::new();
            if state.archived != Some(newest.archived) {
                body.insert("archived".to_string(), json!(newest.archived));
                body.insert("archived_hlc".to_string(), json!(self.reading()));
            }
            let name = names.get(&thread).cloned();
            if name.is_some() && name != state.name {
                body.insert("name".to_string(), json!(name));
                body.insert("name_hlc".to_string(), json!(self.reading()));
            }
            if body.is_empty() {
                continue;
            }
            match hub.set_meta(&state.hub_thread, Value::Object(body.clone())).await {
                Ok(_) => {
                    let entry = self.state.threads.entry(thread.clone()).or_default();
                    if body.contains_key("archived") {
                        entry.archived = Some(newest.archived);
                    }
                    if body.contains_key("name") {
                        entry.name = name;
                    }
                }
                Err(err) => report.errors.push(err),
            }
        }
    }

    /// A thread with no rollout left on this device was deleted here: delete it on the hub.
    async fn notice_deletions(&mut self, hub: &HubClient, locals: &[Local], report: &mut Report) {
        let present: BTreeSet<&str> = locals.iter().map(|local| local.thread.as_str()).collect();
        let threads: Vec<String> = self.state.threads.keys().cloned().collect();
        for thread in threads {
            if present.contains(thread.as_str()) {
                if let Some(state) = self.state.threads.get_mut(&thread) {
                    state.missing = 0;
                }
                continue;
            }
            let Some(state) = self.state.threads.get_mut(&thread) else { continue };
            state.missing += 1;
            if state.missing < MISSING_SCANS {
                continue;
            }
            let hub_thread = state.hub_thread.clone();
            match hub.delete(&hub_thread).await {
                Ok(_) => {
                    report.deleted_on_hub.push(hub_thread);
                    self.forget_thread(&thread);
                }
                Err(err) if err.contains("404") => self.forget_thread(&thread),
                Err(err) => report.errors.push(err),
            }
        }
    }

    fn forget_thread(&mut self, thread: &str) {
        self.state.threads.remove(thread);
        self.state.rollouts.retain(|_, rollout| rollout.thread != thread);
    }

    /// The local thread a hub thread id is, when it is one of this device's.
    fn local_thread(&self, hub_thread: &str) -> Option<String> {
        self.state.threads.iter().find(|(_, state)| state.hub_thread == hub_thread).map(|(thread, _)| thread.clone())
    }

    /// What other devices decided about this device's threads.
    async fn apply_manifest(&mut self, hub: &HubClient, report: &mut Report) -> Result<(), String> {
        let manifest = hub.manifest(self.state.cursor, 0).await?;
        for tombstone in manifest.get("tombstones").and_then(Value::as_array).cloned().unwrap_or_default() {
            let by = tombstone.get("by").and_then(Value::as_str).unwrap_or_default();
            let withdrawn = tombstone.get("withdrawn").and_then(Value::as_bool).unwrap_or(false);
            let Some(hub_thread) = tombstone.get("thread").and_then(Value::as_str) else { continue };
            if by == self.device.device || withdrawn {
                continue;
            }
            let Some(thread) = self.local_thread(hub_thread) else { continue };
            match self.codex(&["delete", "--force", &thread]).await {
                Ok(()) => report.applied.push(format!("deleted {thread}")),
                Err(err) => report.errors.push(format!("could not delete {thread} here: {err}")),
            }
            self.state.deleted.insert(thread.clone());
            self.forget_thread(&thread);
        }
        for record in manifest.get("rollouts").and_then(Value::as_array).cloned().unwrap_or_default() {
            for key in ["archived_hlc", "name_hlc"] {
                if let Some(seen) = record.get(key).and_then(Value::as_str).and_then(Hlc::parse) {
                    self.clock.observe(&seen);
                }
            }
            if record.get("owner").and_then(Value::as_str) != Some(self.device.device.as_str()) {
                continue;
            }
            let Some(hub_thread) = record.get("thread").and_then(Value::as_str) else { continue };
            let Some(thread) = self.local_thread(hub_thread) else { continue };
            let Some(reading) = record.get("archived_hlc").and_then(Value::as_str).and_then(Hlc::parse) else { continue };
            let archived = record.get("archived").and_then(Value::as_bool).unwrap_or(false);
            let Some(state) = self.state.threads.get(&thread).cloned() else { continue };
            let applied = Hlc::parse(&state.applied_archive);
            if reading.device == self.device.device || applied.as_ref().is_some_and(|applied| reading <= *applied) {
                continue;
            }
            if state.archived != Some(archived) {
                let command = if archived { "archive" } else { "unarchive" };
                match self.codex(&[command, &thread]).await {
                    Ok(()) => report.applied.push(format!("{command}d {thread}")),
                    Err(err) => {
                        report.errors.push(format!("could not {command} {thread} here: {err}"));
                        continue;
                    }
                }
            }
            if let Some(entry) = self.state.threads.get_mut(&thread) {
                entry.archived = Some(archived);
                entry.applied_archive = reading.to_string();
            }
        }
        if let Some(cursor) = manifest.get("cursor").and_then(Value::as_u64) {
            self.state.cursor = cursor;
        }
        Ok(())
    }

    /// One of Codex's own commands through this `ling`, as the user would run it.
    async fn codex(&self, args: &[&str]) -> Result<(), String> {
        let mut command = tokio::process::Command::new(&self.ling);
        command.args(args).stdin(std::process::Stdio::null()).kill_on_drop(true);
        let output = tokio::time::timeout(COMMAND_TIMEOUT, command.output())
            .await
            .map_err(|_| format!("`ling {}` took too long", args.join(" ")))?
            .map_err(|err| format!("could not run ling: {err}"))?;
        if output.status.success() {
            Ok(())
        } else {
            let said = String::from_utf8_lossy(&output.stderr);
            Err(format!("`ling {}` failed: {}", args.join(" "), said.trim()))
        }
    }

    /// How many rollouts have bytes the hub does not hold yet.
    pub fn pending(&self, locals: &[Local]) -> usize {
        locals
            .iter()
            .filter(|local| self.state.rollouts.get(&local.rollout).is_none_or(|state| state.held.is_none() && state.stamp != local.stamp))
            .count()
    }

    /// What `ling sync status` reads: written when it changes, and at least every 30 seconds so
    /// its time says the agent is alive.
    pub fn write_status(&mut self, hub: &str, report: &Report, pending: usize) {
        let body = json!({
            "hub": hub,
            "device": self.device.device,
            "cursor": self.state.cursor,
            "published": self.state.threads.len(),
            "pending": pending,
            "errors": report.errors,
        });
        let text = body.to_string();
        if text == self.status_written.0 && self.status_written.1.elapsed() < Duration::from_secs(30) {
            return;
        }
        let mut status = body;
        status["at"] = json!(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0));
        let _ = auth::write_private(&status_path(&self.codex_home), &status.to_string());
        self.status_written = (text, std::time::Instant::now());
    }
}
