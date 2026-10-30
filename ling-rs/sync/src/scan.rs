//! The device's own rollouts, as Codex keeps them (Phase 0, specs/DREAMFERENCE_MIGHTLING_SYNC.md §16):
//!
//! - `sessions/YYYY/MM/DD/rollout-<time>-<thread>.jsonl`, and after a revert
//!   `rollout-<time>-<thread>_<rollout>.jsonl`; archived ones flat in `archived_sessions/`.
//! - Compressed with `.zst` appended, when `rollout/compress` ran: the same bytes, decompressed.
//! - Only whole lines count: a scan can catch Codex mid-line, and the part after the last newline
//!   waits for the next scan.

use std::io::Read;
use std::io::Seek;
use std::path::Path;
use std::path::PathBuf;
use std::time::UNIX_EPOCH;

use serde_json::Value;

/// One rollout file on this device.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Local {
    pub path: PathBuf,
    /// The file name without `.zst`.
    pub file: String,
    pub thread: String,
    pub rollout: String,
    pub archived: bool,
    pub compressed: bool,
    /// Size and modification time on disk: when both are as before, nothing was added.
    pub stamp: (u64, u128),
}

/// The thread and rollout ids in a rollout's file name.
pub fn parse_name(name: &str) -> Option<(String, String)> {
    let core = name.strip_suffix(".zst").unwrap_or(name).strip_prefix("rollout-")?.strip_suffix(".jsonl")?;
    if core.get(19..20)? != "-" {
        return None;
    }
    let ids = core.get(20..)?;
    let (thread, rollout) = ids.split_once('_').unwrap_or((ids, ids));
    let valid = ling_web_server::sync_store::valid_id;
    (valid(thread) && valid(rollout)).then(|| (thread.to_string(), rollout.to_string()))
}

fn walk(dir: &Path, archived: bool, found: &mut Vec<Local>, depth: usize) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let Ok(kind) = entry.file_type() else { continue };
        let path = entry.path();
        if kind.is_dir() {
            if depth < 4 {
                walk(&path, archived, found, depth + 1);
            }
            continue;
        }
        if !kind.is_file() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some((thread, rollout)) = parse_name(&name) else { continue };
        let Ok(metadata) = entry.metadata() else { continue };
        let modified = metadata.modified().ok().and_then(|time| time.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_nanos()).unwrap_or(0);
        let compressed = name.ends_with(".zst");
        found.push(Local {
            file: name.strip_suffix(".zst").unwrap_or(&name).to_string(),
            path,
            thread,
            rollout,
            archived,
            compressed,
            stamp: (metadata.len(), modified),
        });
    }
}

/// Every rollout under `sessions/` and `archived_sessions/`. A rollout present both plain and
/// compressed (Codex between writing the one and deleting the other) is counted once, plain.
pub fn scan(codex_home: &Path) -> Vec<Local> {
    let mut found = Vec::new();
    walk(&codex_home.join("sessions"), false, &mut found, 0);
    walk(&codex_home.join("archived_sessions"), true, &mut found, 0);
    found.sort_by(|a, b| (a.rollout.as_str(), a.compressed).cmp(&(b.rollout.as_str(), b.compressed)));
    found.dedup_by(|later, earlier| later.rollout == earlier.rollout);
    found
}

/// A rollout's bytes from `offset`, decompressed when it is compressed.
pub fn read_from(local: &Local, offset: u64) -> std::io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    if local.compressed {
        let mut decoder = zstd::stream::read::Decoder::new(std::fs::File::open(&local.path)?)?;
        std::io::copy(&mut (&mut decoder).take(offset), &mut std::io::sink())?;
        decoder.read_to_end(&mut bytes)?;
    } else {
        let mut file = std::fs::File::open(&local.path)?;
        file.seek(std::io::SeekFrom::Start(offset))?;
        file.read_to_end(&mut bytes)?;
    }
    Ok(bytes)
}

/// How many of `bytes` are whole lines.
pub fn whole_lines(bytes: &[u8]) -> usize {
    bytes.iter().rposition(|byte| *byte == b'\n').map_or(0, |index| index + 1)
}

/// The first line's `session_meta` payload, when it has one.
pub fn session_meta(local: &Local) -> Option<Value> {
    let bytes = if local.compressed {
        let mut decoder = zstd::stream::read::Decoder::new(std::fs::File::open(&local.path).ok()?).ok()?;
        let mut head = Vec::new();
        (&mut decoder).take(1 << 20).read_to_end(&mut head).ok()?;
        head
    } else {
        let mut head = Vec::new();
        std::fs::File::open(&local.path).ok()?.take(1 << 20).read_to_end(&mut head).ok()?;
        head
    };
    let line = bytes.split(|byte| *byte == b'\n').next()?;
    let value: Value = serde_json::from_slice(line).ok()?;
    (value.get("type")?.as_str()? == "session_meta").then(|| value.get("payload").cloned()).flatten()
}

/// `session_index.jsonl`: the latest name of each thread (a rename appends; a deletion rewrites
/// the file without the thread's entries).
pub fn names(codex_home: &Path) -> std::collections::BTreeMap<String, String> {
    let mut names = std::collections::BTreeMap::new();
    let Ok(text) = std::fs::read_to_string(codex_home.join("session_index.jsonl")) else { return names };
    for line in text.lines() {
        let Ok(value) = serde_json::from_str::<Value>(line) else { continue };
        if let (Some(id), Some(name)) = (value.get("id").and_then(Value::as_str), value.get("thread_name").and_then(Value::as_str)) {
            names.insert(id.to_string(), name.to_string());
        }
    }
    names
}

#[cfg(test)]
mod tests {
    use super::*;

    const THREAD: &str = "01a0f1ae-2da4-7942-99ae-c3151dc137cc";
    const OTHER: &str = "01a0f1ae-2da4-7942-99ae-c3151dc1ffff";

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-sync-scan-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn names_are_codexs_and_nothing_else() {
        let plain = format!("rollout-2026-09-30T10-38-40-{THREAD}.jsonl");
        assert_eq!(parse_name(&plain), Some((THREAD.to_string(), THREAD.to_string())));
        assert_eq!(parse_name(&format!("{plain}.zst")), Some((THREAD.to_string(), THREAD.to_string())));
        let reverted = format!("rollout-2026-09-30T11-00-00-{THREAD}_{OTHER}.jsonl");
        assert_eq!(parse_name(&reverted), Some((THREAD.to_string(), OTHER.to_string())));
        assert_eq!(parse_name("rollout-2026-09-30T10-38-40-../../x.jsonl"), None);
        assert_eq!(parse_name(&format!("rollout-2026-09-30T10-38-40-{THREAD}.json")), None);
        assert_eq!(parse_name("history.jsonl"), None);
    }

    #[test]
    fn a_scan_finds_both_folders_and_reads_compressed_rollouts_as_their_bytes() {
        let home = scratch("scan");
        let day = home.join("sessions/2026/09/30");
        std::fs::create_dir_all(&day).unwrap();
        std::fs::create_dir_all(home.join("archived_sessions")).unwrap();
        let bytes = b"{\"type\":\"session_meta\",\"payload\":{\"id\":\"x\",\"source\":\"cli\"}}\n{\"a\":1}\n{\"b\":";
        std::fs::write(day.join(format!("rollout-2026-09-30T10-38-40-{THREAD}.jsonl")), bytes).unwrap();
        let compressed = zstd::encode_all(&bytes[..], 3).unwrap();
        std::fs::write(home.join("archived_sessions").join(format!("rollout-2026-09-30T11-00-00-{OTHER}.jsonl.zst")), compressed).unwrap();
        std::fs::write(day.join("notes.txt"), "x").unwrap();
        let found = scan(&home);
        assert_eq!(found.len(), 2);
        let archived = found.iter().find(|local| local.archived).unwrap();
        assert!(archived.compressed && archived.file.ends_with(".jsonl"));
        for local in &found {
            assert_eq!(read_from(local, 0).unwrap(), bytes.to_vec());
            assert_eq!(read_from(local, 10).unwrap(), bytes[10..].to_vec());
            assert_eq!(session_meta(local).unwrap()["source"], "cli");
        }
        assert_eq!(whole_lines(bytes), bytes.len() - 5);
        assert_eq!(whole_lines(b"no newline"), 0);
    }

    #[test]
    fn a_name_is_whole_ids_after_the_time() {
        assert_eq!(parse_name(&format!("rollout-2026-09-30T10-38-40x{THREAD}.jsonl")), None, "the time ends with a dash");
        assert_eq!(parse_name(&format!("rollout-2026-09-30T10-38-40-{THREAD}_../../x.jsonl")), None, "the rollout half is an id");
        assert_eq!(parse_name(&format!("rollout-2026-09-30T10-38-40-../../x_{OTHER}.jsonl")), None, "the thread half is an id");
    }

    #[test]
    fn a_rollout_on_disk_both_ways_is_counted_once_plain() {
        let home = scratch("twice");
        let day = home.join("sessions/2026/09/30");
        std::fs::create_dir_all(&day).unwrap();
        let name = format!("rollout-2026-09-30T10-38-40-{THREAD}.jsonl");
        std::fs::write(day.join(&name), b"{}\n").unwrap();
        std::fs::write(day.join(format!("{name}.zst")), zstd::encode_all(&b"{}\n"[..], 3).unwrap()).unwrap();
        let found = scan(&home);
        assert_eq!(found.len(), 1);
        assert!(!found[0].compressed);
    }

    #[cfg(unix)]
    #[test]
    fn a_link_named_like_a_rollout_is_not_read() {
        let home = scratch("link");
        let day = home.join("sessions/2026/09/30");
        std::fs::create_dir_all(&day).unwrap();
        let elsewhere = home.join("private.txt");
        std::fs::write(&elsewhere, b"not a rollout\n").unwrap();
        std::os::unix::fs::symlink(&elsewhere, day.join(format!("rollout-2026-09-30T10-38-40-{THREAD}.jsonl"))).unwrap();
        assert!(scan(&home).is_empty());
    }

    #[test]
    fn only_a_first_line_of_type_session_meta_is_the_meta() {
        let home = scratch("meta");
        let path = home.join(format!("rollout-2026-09-30T10-38-40-{THREAD}.jsonl"));
        std::fs::write(&path, b"{\"type\":\"response_item\",\"payload\":{\"source\":\"cli\"}}\n").unwrap();
        let local = Local {
            path,
            file: String::new(),
            thread: THREAD.to_string(),
            rollout: THREAD.to_string(),
            archived: false,
            compressed: false,
            stamp: (0, 0),
        };
        assert_eq!(session_meta(&local), None);
    }

    #[test]
    fn the_latest_name_of_each_thread_is_read() {
        let home = scratch("names");
        assert!(names(&home).is_empty());
        std::fs::write(
            home.join("session_index.jsonl"),
            format!("{{\"id\":\"{THREAD}\",\"thread_name\":\"First\",\"updated_at\":\"t\"}}\n{{\"id\":\"{THREAD}\",\"thread_name\":\"Second\",\"updated_at\":\"t\"}}\nnot json\n"),
        )
        .unwrap();
        assert_eq!(names(&home).get(THREAD).map(String::as_str), Some("Second"));
    }
}
