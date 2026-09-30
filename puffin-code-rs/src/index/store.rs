//! Installing a finished exact-layer run: post-process the converted store, move it and its
//! `.scip` into place atomically, and record the snapshot in the manifest (spec §6.2, §7.5).
//!
//! Nothing here runs inside the indexing sandbox. The indexer writes only its scratch directory;
//! this code, in the supervisor outside the sandbox, checks the output and moves it into
//! `<repo>/.dreamference/scip/`, so a run killed half-way leaves the last good store in place.

use std::collections::BTreeMap;
use std::path::Path;

use anyhow::{Context, Result};

use crate::changed::GitView;
use crate::manifest::{now_rfc3339, FileStamp, Manifest, RunEntry};
use crate::paths::Repo;
use crate::scip_store;

/// The file-name stem of a run's store: `scip-python-shapes`.
pub fn slug(indexer: &str, root: &str) -> String {
    let root = if root.is_empty() { "root".to_string() } else { root.replace(['/', ' '], "-") };
    format!("{indexer}-{root}")
}

/// The manifest key of a run: `scip-python:shapes`.
pub fn key(indexer: &str, root: &str) -> String {
    format!("{indexer}:{root}")
}

/// Stamps every file under `prefix` that git knows (tracked, or untracked and not ignored).
pub fn stamp_root(repo: &Repo, prefix: &str) -> BTreeMap<String, FileStamp> {
    let mut git = GitView::new(repo);
    let files: Vec<String> = if repo.is_git {
        git.all_files().iter().filter(|f| f.starts_with(prefix)).cloned().collect()
    } else {
        walk(&repo.root.join(prefix), prefix)
    };
    files.into_iter().filter_map(|f| FileStamp::of(&repo.abs(&f)).map(|s| (f, s))).collect()
}

fn walk(dir: &Path, prefix: &str) -> Vec<String> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(dir) else { return out };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with('.') || name == "target" || name == "node_modules" {
            continue;
        }
        let rel = format!("{prefix}{name}");
        match entry.file_type() {
            Ok(t) if t.is_dir() => out.extend(walk(&entry.path(), &format!("{rel}/"))),
            Ok(t) if t.is_file() => out.push(rel),
            _ => {}
        }
    }
    out
}

/// The working tree's changes at the start of a run (they are part of what the run saw).
pub fn dirty_files(repo: &Repo) -> Vec<String> {
    let mut git = GitView::new(repo);
    git.status().iter().cloned().collect()
}

/// Installs a converted store and its `.scip` for `entry`, and records it in the manifest.
///
/// Args:
/// - `converted`: `expt-convert`'s output, anywhere (it is post-processed there, then moved);
/// - `scip_file`: the index it was converted from;
/// - `entry`: the run, with its snapshot fields (commit, dirty files, hashes) already filled.
pub fn install(repo: &Repo, mut entry: RunEntry, converted: &Path, scip_file: &Path) -> Result<RunEntry> {
    let dir = repo.scip_dir();
    std::fs::create_dir_all(&dir)?;
    scip_store::postprocess(converted, scip_file).context("post-processing the converted store")?;
    let stem = slug(&entry.indexer, &entry.root);
    let staged_db = dir.join(format!(".{stem}.db.new"));
    let staged_scip = dir.join(format!(".{stem}.scip.new"));
    std::fs::copy(converted, &staged_db)?;
    std::fs::copy(scip_file, &staged_scip)?;
    std::fs::rename(&staged_scip, dir.join(format!("{stem}.scip")))?;
    std::fs::rename(&staged_db, dir.join(format!("{stem}.db")))?;
    entry.store = format!("{stem}.db");
    entry.status = "ok".to_string();
    entry.run_id = format!("{}-{}", now_rfc3339(), std::process::id());
    let mut manifest = Manifest::load(&dir);
    manifest.runs.insert(key(&entry.indexer, &entry.root), entry.clone());
    manifest.save(&dir)?;
    Ok(entry)
}

/// Records a run that produced no store (failed or deferred), keeping the last good snapshot's
/// fields so queries keep answering from it.
pub fn record_outcome(repo: &Repo, indexer: &str, root: &str, status: &str, update: impl FnOnce(&mut RunEntry)) -> Result<()> {
    let dir = repo.scip_dir();
    let mut manifest = Manifest::load(&dir);
    let entry = manifest.runs.entry(key(indexer, root)).or_insert_with(|| RunEntry {
        indexer: indexer.to_string(),
        root: root.to_string(),
        path_prefix: if root.is_empty() { String::new() } else { format!("{root}/") },
        ..RunEntry::default()
    });
    entry.status = status.to_string();
    update(entry);
    manifest.save(&dir)
}
