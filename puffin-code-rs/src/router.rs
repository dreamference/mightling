//! The router: answers each question from the better layer, tags every row, and says what it could
//! not see (spec §7).
//!
//! The rules it keeps:
//! - **Freshness is decided for the repository** (§7.3): each layer's changed set is computed on
//!   every query, SCIP rows are served only for files fresh for their store, graph rows only for
//!   files fresh for the graph and not covered by a fresh SCIP store, and every changed file is
//!   searched for the name by text, so a reference added since the snapshot is never silently
//!   missing.
//! - **Identity by location** (§7.4): a name is resolved to candidate definitions through both
//!   layers; several candidates are listed, never guessed between.
//! - **Queries only read** (§4): the one write is a fixed-word re-index request, when the
//!   directory is writable.

use std::collections::{BTreeMap, BTreeSet, HashMap};

use anyhow::{bail, Result};
use serde::Serialize;

use crate::changed::{layer_changes, ChangeSet, GitView, Method};
use crate::config::Settings;
use crate::graph::{GraphStore, Node, CALL_EDGES, REFERENCE_EDGES};
use crate::manifest::{GraphSnapshot, Manifest};
use crate::paths::Repo;
use crate::requests;
use crate::scip_store::{ScipStore, ROLE_DEFINITION, ROLE_IMPORT, ROLE_READ, ROLE_WRITE};
use crate::scip_symbol::{self, query_segments};
use crate::submodules::{self, Submodule};
use crate::textscan;

/// Definitions `search` adds from body matches, after the graph's own ranking.
const BODY_ROWS: usize = 30;
/// At most this many text matches are added to an answer the graph alone gave ([`Context::graph_gap_rows`]).
const GRAPH_GAP_ROWS: usize = 100;

/// Whether `search` reads a file's text for body matches: source, not prose or data. Prose is
/// already in the graph as sections, and data files would bury the code under their matches.
fn is_code_path(path: &str) -> bool {
    const SKIP: &[&str] = &["md", "rst", "txt", "json", "lock", "csv", "svg", "html", "xml", "yaml", "yml", "toml", "ipynb", "patch", "tex", "bib"];
    let extension = path.rsplit_once('.').map(|(_, e)| e.to_ascii_lowercase());
    extension.is_some_and(|e| !SKIP.contains(&e.as_str()))
}

/// Whether `word` occurs in `text` at the start of a word: `time` matches `time_s` and
/// `wall_time` (an underscore separates), not `runtime`. Both are lower case.
fn starts_a_word(text: &str, word: &str) -> bool {
    let mut from = 0;
    while let Some(found) = text[from..].find(word) {
        let start = from + found;
        if !text[..start].chars().next_back().is_some_and(char::is_alphanumeric) {
            return true;
        }
        from = start + word.len();
    }
    false
}

/// A test file: its bodies mention everything the code does, so they rank below the code.
fn is_test_path(path: &str) -> bool {
    path.split('/').any(|part| part == "tests" || part == "test" || part.starts_with("test_") || part.ends_with("_test.go") || part.contains(".test.") || part.contains(".spec."))
}

/// How far a row can be trusted.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub enum Tag {
    /// From a fresh SCIP snapshot: compiler-exact.
    #[serde(rename = "exact")]
    Exact,
    /// From the graph, or a SCIP definition located by name rather than position.
    #[serde(rename = "heuristic")]
    Heuristic,
    /// A whole-word text match in a file changed since the snapshots, or not indexed.
    #[serde(rename = "heuristic (text)")]
    Text,
}

impl Tag {
    pub fn label(self) -> &'static str {
        match self {
            Tag::Exact => "exact",
            Tag::Heuristic => "heuristic",
            Tag::Text => "heuristic (text)",
        }
    }
}

/// One result.
#[derive(Debug, Clone, Serialize)]
pub struct Row {
    /// None for `search` and `outline`, whose rows are not claims about references.
    pub tag: Option<Tag>,
    pub path: String,
    pub line: u32,
    pub detail: String,
}

/// Everything an answer prints (spec §7.2).
#[derive(Debug, Clone, Default, Serialize)]
pub struct Answer {
    pub op: String,
    pub query: String,
    /// What `exact` and `heuristic` mean for this answer: the stores and their commits.
    pub sources: Vec<String>,
    pub rows: Vec<Row>,
    /// Files changed since the older snapshot, and whether they were searched.
    pub changed_files: usize,
    /// Whether the changed files were searched; None when this operation searches none.
    pub changed_searched: Option<bool>,
    /// Set when the changed set was too large to search (§7.3): the mandatory `not checked` line.
    pub not_checked: Option<String>,
    pub not_indexed: Vec<String>,
    /// The submodules left out of the index and why (§4.3): a definition that lives in one is
    /// reported as not found, and this line is what says why.
    pub submodules_not_indexed: Option<String>,
    pub unresolved: Vec<String>,
    pub notes: Vec<String>,
    /// Rows dropped because their file was deleted since the snapshot.
    pub deleted_dropped: usize,
    /// When the name is ambiguous: the candidates, and no rows.
    pub candidates: Vec<String>,
    /// Identifies the index state, so a later page can tell it changed (§7.2's `--offset`).
    pub cursor: String,
    /// Whether the answer claims anything about references (search and outline do not).
    #[serde(skip)]
    pub tagged: bool,
    /// The changed and deleted files of the layers this answer draws on: the graph and the stores
    /// that hold its symbol. A store that holds nothing of it (a Rust index for a Python name)
    /// neither widens the text search nor the count.
    #[serde(skip)]
    pub scope: Option<(BTreeSet<String>, BTreeSet<String>)>,
}

/// A definition the query may mean.
#[derive(Debug, Clone)]
struct Candidate {
    display: String,
    name: String,
    path: String,
    line: u32,
    span: (u32, u32),
    node: Option<Node>,
    /// `(store index, symbol id, symbol)`.
    scip: Vec<(usize, i64, String)>,
    /// True when the SCIP identity was found by name, not by position (§7.4 step 4).
    by_name: bool,
}

/// The stores and their freshness, loaded once per query.
pub struct Context {
    pub repo: Repo,
    pub settings: Settings,
    pub graph: Option<GraphStore>,
    pub graph_error: Option<String>,
    pub graph_changes: Option<ChangeSet>,
    pub stores: Vec<ScipStore>,
    pub store_changes: Vec<ChangeSet>,
    pub store_errors: Vec<String>,
    /// Union of every layer's changed files (present on disk).
    pub changed: BTreeSet<String>,
    /// Union of every layer's deleted files.
    pub deleted: BTreeSet<String>,
    /// Tracked files the graph leaves out (ignore rules), searched by text instead (§4.1).
    pub not_indexed_files: BTreeSet<String>,
    pub not_indexed_dirs: Vec<String>,
    pub method: Method,
    /// `store_meta.mutation_gen` when the graph was opened (§7.5's concurrency rule).
    pub graph_generation: Option<String>,
    /// Every submodule with its decision (§4.3), recomputed for this query.
    pub submodules: Vec<Submodule>,
}

impl Context {
    /// Opens both layers and computes their changed sets.
    pub fn load(repo: Repo, settings: Settings) -> Result<Context> {
        let decisions = submodules::evaluate(&repo, &settings);
        let left_out: Vec<String> = decisions.iter().filter(|s| !s.indexed).map(|s| s.path.clone()).collect();
        let mut git = GitView::with_included(&repo, submodules::included(&decisions));
        // puffin-code's own state (stores, manifests, requests) is never part of a changed set,
        // whether or not the repository ignores it.
        let state_prefix = repo
            .state_dir()
            .strip_prefix(&repo.root)
            .ok()
            .map(|p| format!("{}/", p.to_string_lossy()))
            .unwrap_or_else(|| "\0".to_string());
        let is_state = |path: &str| path.starts_with(&state_prefix);
        let (graph, graph_error) = match GraphStore::open(&repo) {
            Ok(graph) => (graph, None),
            Err(error) => (None, Some(error.to_string())),
        };
        let scip_dir = repo.scip_dir();
        let mut not_indexed_dirs = Vec::new();
        let mut not_indexed_files = BTreeSet::new();
        let mut method = Method::Git;
        let graph_changes = match &graph {
            Some(graph) => {
                let excluded: Vec<String> = graph.not_indexed()?.into_iter().collect();
                let is_excluded = |path: &str| {
                    is_state(path) || excluded.iter().any(|dir| path == dir || path.starts_with(&format!("{dir}/")))
                };
                // Tracked files an ignore rule dropped: covered by text instead (§4.1).
                for file in git.all_files().clone() {
                    if !is_state(&file) && is_excluded(&file) && !left_out.iter().any(|s| file == *s || file.starts_with(&format!("{s}/"))) {
                        not_indexed_files.insert(file);
                    }
                }
                for dir in &excluded {
                    if not_indexed_files.contains(dir) {
                        // A single file the graph skipped (often binary); it is searched like the rest.
                        not_indexed_dirs.push(dir.clone());
                    } else if not_indexed_files.iter().any(|f| f.starts_with(&format!("{dir}/"))) {
                        not_indexed_dirs.push(format!("{dir}/"));
                    }
                }
                let snapshot = GraphSnapshot::load(&scip_dir);
                let commit = snapshot.as_ref().and_then(|s| s.commit.clone());
                let dirty = snapshot.map(|s| s.dirty_files).unwrap_or_default();
                let stamps = graph.file_hashes()?;
                let changes = layer_changes(&repo, &mut git, commit.as_deref(), &dirty, &stamps, None, &is_excluded);
                if changes.method == Method::Stat {
                    method = Method::Stat;
                }
                Some(changes)
            }
            None => None,
        };
        let manifest = Manifest::load(&scip_dir);
        let mut stores = Vec::new();
        let mut store_changes = Vec::new();
        let mut store_errors = Vec::new();
        for (key, entry) in manifest.runs {
            if entry.store.is_empty() {
                continue;
            }
            let path = scip_dir.join(&entry.store);
            if !path.is_file() {
                continue;
            }
            match ScipStore::open(&path, entry) {
                Ok(store) => {
                    // No scope: a store has documents outside its root (what the root imports),
                    // and its snapshot stamps every file of the repository.
                    let stamps: HashMap<_, _> = store.entry.file_hashes.clone().into_iter().collect();
                    let changes = layer_changes(
                        &repo, &mut git, store.entry.commit.as_deref(), &store.entry.dirty_files, &stamps, None, &is_state,
                    );
                    if changes.method == Method::Stat {
                        method = Method::Stat;
                    }
                    stores.push(store);
                    store_changes.push(changes);
                }
                Err(error) => store_errors.push(format!("{key}: {error}")),
            }
        }
        let mut changed = BTreeSet::new();
        let mut deleted = BTreeSet::new();
        for set in graph_changes.iter().chain(store_changes.iter()) {
            changed.extend(set.changed.iter().cloned());
            deleted.extend(set.deleted.iter().cloned());
        }
        let graph_generation = graph.as_ref().map(|g| g.mutation_gen());
        Ok(Context {
            graph_generation,
            repo,
            settings,
            graph,
            graph_error,
            graph_changes,
            stores,
            store_changes,
            store_errors,
            changed,
            deleted,
            not_indexed_files,
            not_indexed_dirs,
            method,
            submodules: decisions,
        })
    }

    /// Whether codebase-memory committed a re-index while this context was answering: its
    /// database uses a rollback journal, so a query that straddles a commit may have read both
    /// sides of it. The caller answers again, once (§7.5).
    pub fn graph_changed_since_load(&self) -> bool {
        let Some(before) = &self.graph_generation else { return false };
        match GraphStore::open(&self.repo) {
            Ok(Some(graph)) => graph.mutation_gen() != *before,
            _ => false,
        }
    }

    /// Whether any layer can answer at all.
    pub fn has_index(&self) -> bool {
        self.graph.is_some() || !self.stores.is_empty()
    }

    /// Whether store `i` covers `path` and the file is unchanged since its snapshot.
    fn scip_fresh(&self, i: usize, path: &str) -> bool {
        self.store_changes[i].is_fresh(path) && self.stores[i].covers(path)
    }

    /// Whether any store covering `path` is fresh for it.
    fn any_scip_fresh(&self, path: &str) -> bool {
        (0..self.stores.len()).any(|i| self.scip_fresh(i, path))
    }

    fn graph_fresh(&self, path: &str) -> bool {
        self.graph_changes.as_ref().map(|c| c.is_fresh(path)).unwrap_or(false)
    }

    /// The cursor of §7.2: the graph's mutation generation, each store's run id, and the changed set.
    pub fn cursor(&self) -> String {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        if let Some(graph) = &self.graph {
            hasher.update(graph.mutation_gen().as_bytes());
        }
        for store in &self.stores {
            hasher.update(store.entry.run_id.as_bytes());
        }
        for path in self.changed.iter().chain(self.deleted.iter()) {
            hasher.update(path.as_bytes());
        }
        crate::manifest::hex(&hasher.finalize()[..4])
    }

    /// Restricts the header's source line, the changed count and the text search to the stores an
    /// answer draws on, and the graph.
    fn set_sources(&self, answer: &mut Answer, stores: &BTreeSet<usize>) {
        answer.sources = self.sources(Some(stores));
        let mut changed = BTreeSet::new();
        let mut deleted = BTreeSet::new();
        for set in self.graph_changes.iter().chain(stores.iter().map(|i| &self.store_changes[*i])) {
            changed.extend(set.changed.iter().cloned());
            deleted.extend(set.deleted.iter().cloned());
        }
        // Without a graph, a store-less answer still has to see every change.
        if self.graph_changes.is_none() && stores.is_empty() {
            changed = self.changed.clone();
            deleted = self.deleted.clone();
        }
        answer.changed_files = changed.len() + deleted.len();
        answer.scope = Some((changed, deleted));
    }

    fn answer_changed<'a>(&'a self, answer: &'a Answer) -> &'a BTreeSet<String> {
        answer.scope.as_ref().map(|(c, _)| c).unwrap_or(&self.changed)
    }

    /// The header's source line: which snapshot each tag comes from.
    fn sources(&self, only: Option<&BTreeSet<usize>>) -> Vec<String> {
        let stores = self
            .stores
            .iter()
            .enumerate()
            .filter(|(i, _)| only.map(|set| set.contains(i)).unwrap_or(true))
            .map(|(_, s)| (s.entry.indexer.as_str(), s.entry.root.as_str(), s.entry.commit.as_deref()));
        let mut out = Vec::new();
        if let Some(line) = sources_line(stores) {
            out.push(line);
        }
        out.push("heuristic = codebase-memory or text search".to_string());
        out
    }

    fn new_answer(&self, op: &str, query: &str) -> Answer {
        Answer {
            op: op.to_string(),
            query: query.to_string(),
            sources: self.sources(None),
            changed_files: self.changed.len() + self.deleted.len(),
            cursor: self.cursor(),
            submodules_not_indexed: submodules::not_indexed_line(&self.submodules),
            tagged: true,
            ..Answer::default()
        }
    }

    /// Resolves what the agent typed to candidate definitions (§7.4 steps 1, 2 and 4).
    ///
    /// A query of the form `path:line` names the definition on that line.
    fn resolve(&self, query: &str) -> Result<Vec<Candidate>> {
        if let Some((path, line)) = query.rsplit_once(':').and_then(|(p, l)| Some((p, l.parse::<u32>().ok()?))) {
            if self.repo.abs(path).is_file() {
                return self.resolve_location(path, line);
            }
        }
        let segments = query_segments(query);
        let Some(name) = segments.last().cloned() else { bail!("an empty name") };
        let mut out: Vec<Candidate> = Vec::new();
        if let Some(graph) = &self.graph {
            for node in graph.candidates(&segments)? {
                let mut candidate = Candidate {
                    display: node.display(&graph.project),
                    name: node.name.clone(),
                    path: node.file_path.clone(),
                    line: node.start_line,
                    span: (node.start_line, node.end_line.max(node.start_line)),
                    node: Some(node),
                    scip: Vec::new(),
                    by_name: false,
                };
                for (i, store) in self.stores.iter().enumerate() {
                    if self.scip_fresh(i, &candidate.path) {
                        if let Some((id, symbol)) = store.symbol_defined_at(&candidate.path, candidate.span, &name)? {
                            candidate.scip.push((i, id, symbol));
                        }
                    }
                }
                out.push(candidate);
            }
        }
        for (i, store) in self.stores.iter().enumerate() {
            for def in store.definitions_named(&segments)? {
                let fresh = self.scip_fresh(i, &def.path);
                if let Some(existing) = out.iter_mut().find(|c| c.path == def.path && c.span.0 <= def.line && def.line <= c.span.1) {
                    if !existing.scip.iter().any(|(j, id, _)| *j == i && *id == def.symbol_id) {
                        existing.scip.push((i, def.symbol_id, def.symbol.clone()));
                        existing.by_name |= !fresh;
                    }
                    continue;
                }
                // The graph does not have it: a file it leaves out, a submodule, or no graph at all.
                let span = def.span.unwrap_or((def.line, def.line));
                out.push(Candidate {
                    display: def.name.display(),
                    name: def.name.name().to_string(),
                    path: def.path.clone(),
                    line: def.line,
                    span,
                    node: None,
                    scip: vec![(i, def.symbol_id, def.symbol.clone())],
                    by_name: !fresh,
                });
            }
        }
        // A graph node whose own file changed may point at the wrong lines; the SCIP one (if any)
        // then carries the identity.
        out.sort_by(|a, b| (&a.path, a.line).cmp(&(&b.path, b.line)));
        out.dedup_by(|a, b| a.path == b.path && a.line == b.line);
        Ok(out)
    }

    fn resolve_location(&self, path: &str, line: u32) -> Result<Vec<Candidate>> {
        let mut out = Vec::new();
        if let Some(graph) = &self.graph {
            let node = graph.outline(path)?.into_iter().filter(|n| n.start_line == line).min_by_key(|n| n.end_line - n.start_line);
            let node = match node {
                Some(node) => Some(node),
                None => graph.enclosing(path, line)?,
            };
            if let Some(node) = node {
                let mut candidate = Candidate {
                    display: node.display(&graph.project),
                    name: node.name.clone(),
                    path: path.to_string(),
                    line: node.start_line,
                    span: (node.start_line, node.end_line.max(node.start_line)),
                    node: Some(node.clone()),
                    scip: Vec::new(),
                    by_name: false,
                };
                for (i, store) in self.stores.iter().enumerate() {
                    if self.scip_fresh(i, path) {
                        if let Some((id, symbol)) = store.symbol_defined_at(path, candidate.span, &node.name)? {
                            candidate.scip.push((i, id, symbol));
                        }
                    }
                }
                out.push(candidate);
                return Ok(out);
            }
        }
        for (i, store) in self.stores.iter().enumerate() {
            if !self.scip_fresh(i, path) {
                continue;
            }
            if let Some(symbol) = store.enclosing_definition(path, line)? {
                let Some(name) = scip_symbol::parse(&symbol) else { continue };
                for def in store.definitions_named(&[name.name().to_string()])? {
                    if def.symbol == symbol && def.path == path {
                        out.push(Candidate {
                            display: name.display(),
                            name: name.name().to_string(),
                            path: def.path.clone(),
                            line: def.line,
                            span: def.span.unwrap_or((def.line, def.line)),
                            node: None,
                            scip: vec![(i, def.symbol_id, symbol.clone())],
                            by_name: false,
                        });
                        return Ok(out);
                    }
                }
            }
        }
        Ok(out)
    }

    /// Resolves to exactly one candidate, or fills `answer.candidates` and returns None.
    fn resolve_one(&self, query: &str, answer: &mut Answer) -> Result<Option<Candidate>> {
        let mut candidates = self.resolve(query)?;
        match candidates.len() {
            0 => {
                answer.notes.push(format!("no definition named `{query}` in the index"));
                Ok(None)
            }
            1 => Ok(candidates.pop()),
            _ => {
                answer.candidates = candidates
                    .iter()
                    .map(|c| format!("{}:{}  {}", c.path, c.line, c.display))
                    .collect();
                answer.notes.push(format!(
                    "`{query}` names {} definitions; qualify it (`Type.{name}`, `module.{name}`) or name one by `path:line`",
                    candidates.len(),
                    name = query_segments(query).last().cloned().unwrap_or_default()
                ));
                Ok(None)
            }
        }
    }

    /// Files to search by text: every changed file of the answer's layers, plus tracked files no
    /// layer indexes.
    fn text_files(&self, changed: &BTreeSet<String>) -> Vec<String> {
        let mut files: BTreeSet<String> = changed.clone();
        for file in &self.not_indexed_files {
            if !self.any_scip_fresh(file) {
                files.insert(file.clone());
            }
        }
        files.into_iter().collect()
    }

    /// Runs the text search and records its outcome on the answer.
    fn text_rows(&self, name: &str, answer: &mut Answer, keep: &dyn Fn(&str, u32) -> bool) -> Vec<Row> {
        let changed = self.answer_changed(answer).clone();
        let files = self.text_files(&changed);
        let scan = textscan::scan(&self.repo.root, &files, name, self.settings.scan_max_files, self.settings.scan_max_bytes);
        answer.changed_searched = Some(scan.over_limit.is_none());
        if let Some(why) = &scan.over_limit {
            answer.not_checked = Some(format!(
                "{} changed files were not searched ({why}); the answer may be missing references from them",
                files.len()
            ));
        }
        let text_dirs: Vec<&String> = self.not_indexed_dirs.iter().filter(|d| d.ends_with('/')).collect();
        if !text_dirs.is_empty() {
            let shown: Vec<&str> = text_dirs.iter().take(5).map(|d| d.as_str()).collect();
            let more = if text_dirs.len() > 5 { format!(" and {} more", text_dirs.len() - 5) } else { String::new() };
            answer.not_indexed.push(format!("{}{more} (ignored by the graph; searched by text)", shown.join(", ")));
        }
        scan.hits
            .into_iter()
            .filter(|hit| keep(&hit.path, hit.line))
            .map(|hit| {
                let why = if changed.contains(&hit.path) { "changed since snapshot" } else { "not indexed" };
                Row { tag: Some(Tag::Text), path: hit.path, line: hit.line, detail: why.to_string() }
            })
            .collect()
    }

    /// Whole-word matches of the name in the tracked files of the definition's language, for an
    /// answer that rests on the graph alone.
    ///
    /// The graph misses about half of the files that reference a Python method (a call through an
    /// attribute, `self.query.get_related_updates()`, names no type it could resolve), and an answer
    /// of "0 results" reads as "nothing uses this": in the SWE-bench arm of 2026-10-03, `refs`,
    /// `impact` and `callers` answered 0 for methods the repository calls, and the note's advice to
    /// confirm with `rg` named a tool that is not installed there. The rows over-report (another
    /// definition of the same name matches too), the safe direction, and say `heuristic (text)`.
    fn graph_gap_rows(&self, candidate: &Candidate, found: &[Row], answer: &mut Answer) -> Vec<Row> {
        let extension = std::path::Path::new(&candidate.path).extension().and_then(|e| e.to_str());
        let (Some(extension), true) = (extension, self.repo.is_git) else { return Vec::new() };
        // `safe.directory`: the files may belong to another user (a SWE-bench image's root), and
        // an MCP server is not given the agent's git configuration.
        let output = std::process::Command::new("git")
            .args(["-c", "safe.directory=*", "-C"])
            .arg(&self.repo.root)
            .args(["grep", "-n", "-I", "-w", "-F", "--no-color", "-e", &candidate.name, "--", &format!("*.{extension}")])
            .output();
        let Ok(output) = output else { return Vec::new() };
        let mut rows = Vec::new();
        let mut matches = 0;
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            let mut parts = line.splitn(3, ':');
            let (Some(path), Some(Ok(number))) = (parts.next(), parts.next().map(str::parse::<u32>)) else { continue };
            if (path == candidate.path && number == candidate.line) || found.iter().any(|r| r.path == path && r.line == number) {
                continue;
            }
            matches += 1;
            if rows.len() < GRAPH_GAP_ROWS {
                rows.push(Row { tag: Some(Tag::Text), path: path.to_string(), line: number, detail: "text match of the name".to_string() });
            }
        }
        if matches > rows.len() {
            answer.notes.push(format!(
                "{} more text matches of `{}` are not listed; narrow with `path`",
                matches - rows.len(),
                candidate.name
            ));
        }
        rows
    }

    /// Asks the session process for a re-index when anything changed (§7.3).
    fn request_reindex(&self, answer: &mut Answer) {
        if self.changed.is_empty() && self.deleted.is_empty() {
            return;
        }
        if requests::append(&self.repo.state_dir(), requests::Request::Index).is_ok() {
            answer.notes.push("re-index requested".to_string());
        } else {
            answer.notes.push("index is behind the working tree (this sandbox cannot request a re-index; the next launch refreshes it)".to_string());
        }
    }

    fn note_missing_layers(&self, answer: &mut Answer) {
        if let Some(error) = &self.graph_error {
            answer.notes.push(format!("universal layer unavailable: {error}"));
        } else if self.graph.is_none() {
            answer.notes.push("universal layer not built yet; run `puffin-code index`".to_string());
        }
        for error in &self.store_errors {
            answer.notes.push(format!("exact layer unavailable: {error}"));
        }
        if self.method == Method::Stat {
            answer.notes.push("a snapshot's commit is unknown here; freshness was checked file by file".to_string());
        }
    }

    // ---- operations (§7.1) ----

    /// `refs <symbol>`.
    pub fn refs(&self, query: &str) -> Result<Answer> {
        let mut answer = self.new_answer("refs", query);
        self.note_missing_layers(&mut answer);
        let Some(candidate) = self.resolve_one(query, &mut answer)? else { return Ok(answer) };
        answer.query = format!("{}  ({}:{})", candidate.display, candidate.path, candidate.line);
        let mut rows = Vec::new();
        let mut exact_stores: BTreeSet<usize> = BTreeSet::new();
        for (i, id, symbol) in &candidate.scip {
            exact_stores.insert(*i);
            for occ in self.stores[*i].occurrences_of(*id, symbol)? {
                if self.deleted.contains(&occ.path) {
                    answer.deleted_dropped += 1;
                    continue;
                }
                if !self.scip_fresh(*i, &occ.path) {
                    continue;
                }
                // An identity found by name (§7.4 step 4) is still one symbol: its references in
                // fresh files are exact. Only a definition located that way is not.
                let tag = if candidate.by_name && occ.roles & ROLE_DEFINITION != 0 { Tag::Heuristic } else { Tag::Exact };
                rows.push(Row { tag: Some(tag), path: occ.path, line: occ.line, detail: role_label(occ.roles).to_string() });
            }
        }
        let covered_exactly = |path: &str| exact_stores.iter().any(|i| self.scip_fresh(*i, path));
        if let (Some(graph), Some(node)) = (&self.graph, &candidate.node) {
            for edge in graph.incoming(node.id, REFERENCE_EDGES)? {
                let file = edge.other.file_path.clone();
                if covered_exactly(&file) || !self.graph_fresh(&file) {
                    continue;
                }
                let detail = format!("{} in {}", edge.kind.to_lowercase(), edge.other.display(&graph.project));
                for line in self.edge_lines(&edge.other, edge.line, &candidate.name) {
                    rows.push(Row { tag: Some(Tag::Heuristic), path: file.clone(), line, detail: detail.clone() });
                }
                if edge.candidates > 1 {
                    answer.unresolved.push(format!(
                        "{}:{} resolved among {} candidates by name",
                        file, edge.line.unwrap_or(edge.other.start_line), edge.candidates
                    ));
                }
            }
        }
        if candidate.scip.is_empty() {
            answer.notes.push(graph_only_note(&candidate.path));
            let gap = self.graph_gap_rows(&candidate, &rows, &mut answer);
            rows.extend(gap);
        }
        self.set_sources(&mut answer, &exact_stores);
        // Text files are changed or not indexed, so none of them is covered exactly.
        rows.extend(self.text_rows(&candidate.name, &mut answer, &|_, _| true));
        answer.rows = dedup(rows);
        self.request_reindex(&mut answer);
        Ok(answer)
    }

    /// `def <symbol>`.
    pub fn def(&self, query: &str) -> Result<Answer> {
        let mut answer = self.new_answer("def", query);
        self.note_missing_layers(&mut answer);
        let candidates = self.resolve(query)?;
        let name = query_segments(query).last().cloned().unwrap_or_default();
        let mut rows = Vec::new();
        let used: BTreeSet<usize> = candidates.iter().flat_map(|c| c.scip.iter().map(|(i, _, _)| *i)).collect();
        self.set_sources(&mut answer, &used);
        for c in &candidates {
            let fresh = c.scip.iter().any(|(i, _, _)| self.scip_fresh(*i, &c.path));
            let tag = if fresh && !c.by_name { Tag::Exact } else { Tag::Heuristic };
            rows.push(Row { tag: Some(tag), path: c.path.clone(), line: c.line, detail: c.display.clone() });
        }
        // A definition written since the snapshots: a text hit on a line that defines the name.
        rows.extend(self.text_rows(&name, &mut answer, &|path, line| defines_on_line(&self.repo, path, line, &name)));
        answer.rows = dedup(rows);
        self.request_reindex(&mut answer);
        Ok(answer)
    }

    /// `callers <symbol>`: references mapped to their innermost enclosing definition.
    pub fn callers(&self, query: &str) -> Result<Answer> {
        let mut answer = self.refs(query)?;
        answer.op = "callers".to_string();
        let mut rows = Vec::new();
        for row in std::mem::take(&mut answer.rows) {
            if row.detail == "definition" || row.detail == "import" {
                continue;
            }
            let caller = match row.tag {
                Some(Tag::Exact) => self.stores.iter().find_map(|s| s.enclosing_definition(&row.path, row.line).ok().flatten())
                    .and_then(|s| scip_symbol::parse(&s)).map(|n| n.display()),
                Some(Tag::Heuristic) => row.detail.split_once(" in ").map(|(_, c)| c.to_string()),
                _ => None,
            };
            let caller = caller.or_else(|| {
                let graph = self.graph.as_ref()?;
                if !self.graph_fresh(&row.path) {
                    return None;
                }
                graph.enclosing(&row.path, row.line).ok().flatten().map(|n| n.display(&graph.project))
            });
            let detail = match caller {
                Some(caller) => format!("in {caller}"),
                None => row.detail.clone(),
            };
            rows.push(Row { detail, ..row });
        }
        answer.rows = rows;
        if answer.rows.iter().all(|r| r.tag != Some(Tag::Exact)) && !answer.rows.is_empty() {
            answer.notes.push("no exact callers: the graph finds about 58% of Python and 78% of Rust calling files (spec §2)".to_string());
        }
        Ok(answer)
    }

    /// `impact <symbol>`: what breaks if the definition changes. Its references, then the
    /// references of each definition that encloses one, to `depth` levels (§7.1).
    ///
    /// A text hit in a changed file is listed at the level it was found and never followed: the
    /// text search cannot say which definition it belongs to. A row is as trustworthy as the
    /// weakest link of the chain that led to it.
    pub fn impact(&self, query: &str, depth: usize) -> Result<Answer> {
        let mut answer = self.refs(query)?;
        answer.op = "impact".to_string();
        if !answer.candidates.is_empty() {
            return Ok(answer);
        }
        let mut probe = Answer::default();
        let Some(root) = self.resolve_one(query, &mut probe)? else { return Ok(answer) };
        let first = std::mem::take(&mut answer.rows);
        let mut walk = ImpactWalk::new(depth.clamp(1, IMPACT_MAX_DEPTH));
        walk.seen_definitions.insert((root.path.clone(), root.line));
        self.impact_level(&mut walk, first, 1, Tag::Exact);
        self.impact_follow(&mut walk, &mut answer)?;
        walk.finish(&mut answer);
        Ok(answer)
    }

    /// `impact --diff [<rev>]`: the same walk from every definition a diff touches.
    pub fn impact_of_diff(&self, rev: &str, depth: usize) -> Result<Answer> {
        let mut answer = self.new_answer("impact", &format!("--diff {rev}"));
        self.note_missing_layers(&mut answer);
        if !self.repo.is_git {
            bail!("`impact --diff` needs a git repository");
        }
        let diff = crate::paths::git(&self.repo.root, &["diff", "-U0", "--no-renames", "--no-ext-diff", rev, "--"])?;
        let mut touched: Vec<Candidate> = Vec::new();
        for (path, start, end) in diff_ranges(&diff) {
            // The definition enclosing the hunk, and every definition that begins inside it.
            let mut lines = vec![start];
            if let Some(graph) = &self.graph {
                lines.extend(graph.outline(&path)?.into_iter().map(|n| n.start_line).filter(|l| *l > start && *l <= end));
            }
            for line in lines {
                for candidate in self.resolve_location(&path, line)? {
                    if !touched.iter().any(|c| c.path == candidate.path && c.line == candidate.line) {
                        touched.push(candidate);
                    }
                }
            }
        }
        if touched.is_empty() {
            answer.notes.push(format!("the diff against {rev} touches no definition the index knows"));
            return Ok(answer);
        }
        let shown: Vec<String> = touched.iter().take(IMPACT_DIFF_DEFINITIONS).map(|c| c.display.clone()).collect();
        let more = touched.len().saturating_sub(shown.len());
        answer.query = format!("--diff {rev}  ({} changed definitions: {}{})", touched.len(), shown.join(", "), if more > 0 { format!(" +{more} more, not followed") } else { String::new() });
        let mut walk = ImpactWalk::new(depth.clamp(1, IMPACT_MAX_DEPTH));
        for candidate in &touched {
            walk.seen_definitions.insert((candidate.path.clone(), candidate.line));
        }
        let mut stores: BTreeSet<usize> = BTreeSet::new();
        for candidate in touched.iter().take(IMPACT_DIFF_DEFINITIONS) {
            stores.extend(candidate.scip.iter().map(|(i, _, _)| *i));
            let refs = self.refs(&format!("{}:{}", candidate.path, candidate.line))?;
            walk.absorb(&mut answer, &refs);
            self.impact_level(&mut walk, refs.rows, 1, Tag::Exact);
        }
        self.set_sources(&mut answer, &stores);
        self.impact_follow(&mut walk, &mut answer)?;
        walk.finish(&mut answer);
        // The changed lines belong to changed files: where the snapshot is behind them, the
        // definitions were located by the old line numbers.
        answer.notes.push("the diff's files changed since the snapshots: definitions were located by the index's line numbers, which may have moved".to_string());
        self.request_reindex(&mut answer);
        Ok(answer)
    }

    /// Records one level's rows and queues the definitions that enclose them.
    fn impact_level(&self, walk: &mut ImpactWalk, rows: Vec<Row>, level: usize, via: Tag) {
        for row in rows {
            if row.detail == "definition" || !walk.seen_rows.insert((row.path.clone(), row.line)) {
                continue;
            }
            // The chain is as good as its weakest link.
            let tag = row.tag.unwrap_or(Tag::Heuristic).max(via);
            let followable = row.tag != Some(Tag::Text) && row.detail != "import";
            let caller = if followable { self.resolve_location(&row.path, row.line).ok().and_then(|mut c| c.pop()) } else { None };
            let detail = match (&caller, row.tag) {
                (Some(caller), _) => format!("depth {level}: in {}", caller.display),
                (None, Some(Tag::Text)) => format!("depth {level}: {} (not followed)", row.detail),
                (None, _) if row.detail == "import" => format!("depth {level}: import"),
                (None, _) => format!("depth {level}: at module level"),
            };
            walk.rows.push(Row { tag: Some(tag), path: row.path, line: row.line, detail });
            if let Some(caller) = caller {
                if level < walk.depth && walk.seen_definitions.insert((caller.path.clone(), caller.line)) {
                    walk.queue.push_back((caller.path, caller.line, tag, level + 1));
                }
            }
        }
    }

    /// Follows the queued definitions, breadth first, within the walk's budget.
    fn impact_follow(&self, walk: &mut ImpactWalk, answer: &mut Answer) -> Result<()> {
        while let Some((path, line, via, level)) = walk.queue.pop_front() {
            if walk.followed >= IMPACT_MAX_DEFINITIONS {
                walk.cut = walk.queue.len() + 1;
                break;
            }
            walk.followed += 1;
            let refs = self.refs(&format!("{path}:{line}"))?;
            walk.absorb(answer, &refs);
            self.impact_level(walk, refs.rows, level, via);
        }
        Ok(())
    }

    /// `callees <symbol>`: what the definition's body refers to.
    pub fn callees(&self, query: &str) -> Result<Answer> {
        let mut answer = self.new_answer("callees", query);
        self.note_missing_layers(&mut answer);
        let Some(candidate) = self.resolve_one(query, &mut answer)? else { return Ok(answer) };
        answer.query = format!("{}  ({}:{})", candidate.display, candidate.path, candidate.line);
        self.set_sources(&mut answer, &candidate.scip.iter().map(|(i, _, _)| *i).collect());
        let mut rows = Vec::new();
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let exact = candidate.scip.iter().find(|(i, _, _)| self.scip_fresh(*i, &candidate.path));
        if let Some((i, _, _)) = exact {
            for occ in self.stores[*i].occurrences_within(&candidate.path, candidate.span)? {
                if occ.roles & ROLE_IMPORT != 0 {
                    continue;
                }
                let Some(name) = scip_symbol::parse(&occ.symbol) else { continue };
                if matches!(name.kind, scip_symbol::Kind::Parameter | scip_symbol::Kind::TypeParameter) || !seen.insert(occ.symbol.clone()) {
                    continue;
                }
                rows.push(Row { tag: Some(Tag::Exact), path: occ.path, line: occ.line, detail: name.display() });
            }
        } else if let (Some(graph), Some(node)) = (&self.graph, &candidate.node) {
            if self.graph_fresh(&candidate.path) {
                for edge in graph.outgoing(node.id, CALL_EDGES)? {
                    let target = edge.other.display(&graph.project);
                    if !seen.insert(target.clone()) {
                        continue;
                    }
                    rows.push(Row { tag: Some(Tag::Heuristic), path: candidate.path.clone(), line: edge.line.unwrap_or(candidate.line), detail: target });
                }
            }
        }
        if self.changed.contains(&candidate.path) {
            answer.not_checked = Some(format!("{} changed since the snapshots; read its body for current callees", candidate.path));
        }
        answer.rows = rows;
        self.request_reindex(&mut answer);
        Ok(answer)
    }

    /// `impl <trait-or-interface>`.
    pub fn implementations(&self, query: &str) -> Result<Answer> {
        let mut answer = self.new_answer("impl", query);
        self.note_missing_layers(&mut answer);
        let Some(candidate) = self.resolve_one(query, &mut answer)? else { return Ok(answer) };
        answer.query = format!("{}  ({}:{})", candidate.display, candidate.path, candidate.line);
        self.set_sources(&mut answer, &candidate.scip.iter().map(|(i, _, _)| *i).collect());
        let mut rows = Vec::new();
        for (i, _, symbol) in &candidate.scip {
            let store = &self.stores[*i];
            for (id, implementation) in store.implementations(symbol)? {
                let Some(name) = scip_symbol::parse(&implementation) else {
                    // `impl#[Type][Trait]`: the impl block itself.
                    for occ in store.occurrences_of(id, &implementation)? {
                        if occ.roles & ROLE_DEFINITION != 0 && self.scip_fresh(*i, &occ.path) {
                            rows.push(Row { tag: Some(Tag::Exact), path: occ.path, line: occ.line, detail: impl_block_label(&implementation) });
                        }
                    }
                    continue;
                };
                for def in store.definitions_of(id, &implementation, &name)? {
                    if self.scip_fresh(*i, &def.path) {
                        rows.push(Row { tag: Some(Tag::Exact), path: def.path, line: def.line, detail: name.display() });
                    }
                }
            }
        }
        if let (Some(graph), Some(node)) = (&self.graph, &candidate.node) {
            for edge in graph.incoming(node.id, &["IMPLEMENTS", "OVERRIDE", "INHERITS"])? {
                let file = edge.other.file_path.clone();
                if !self.graph_fresh(&file) {
                    continue;
                }
                rows.push(Row { tag: Some(Tag::Heuristic), path: file, line: edge.other.start_line, detail: edge.other.display(&graph.project) });
            }
        }
        let trait_name = if candidate.node.as_ref().map(|n| n.label == "Method").unwrap_or(false) {
            candidate.display.rsplit('.').nth(1).unwrap_or(&candidate.name).to_string()
        } else {
            candidate.name.clone()
        };
        let changed = self.answer_changed(&answer).clone();
        rows.extend(self.text_rows(&trait_name, &mut answer, &|path, _| changed.contains(path)));
        answer.rows = dedup(rows);
        self.request_reindex(&mut answer);
        Ok(answer)
    }

    /// `show <symbol>`: one definition's source.
    pub fn show(&self, query: &str) -> Result<(Answer, Option<String>)> {
        let mut answer = self.new_answer("show", query);
        answer.tagged = false;
        self.note_missing_layers(&mut answer);
        let Some(candidate) = self.resolve_one(query, &mut answer)? else { return Ok((answer, None)) };
        let text = std::fs::read_to_string(self.repo.abs(&candidate.path)).unwrap_or_default();
        let (start, end) = candidate.span;
        let end = end.min(start + 400);
        let body: Vec<String> = text
            .lines()
            .enumerate()
            .skip(start.saturating_sub(1) as usize)
            .take((end + 1 - start) as usize)
            .map(|(i, l)| format!("{:>5}  {l}", i + 1))
            .collect();
        let body = if candidate.path.ends_with(".py") { crate::output::fold_docstring(body) } else { body };
        answer.query = format!("{}  ({}:{}-{})", candidate.display, candidate.path, start, end);
        if self.changed.contains(&candidate.path) {
            answer.notes.push(format!("{} changed since the snapshot; the lines above may have moved", candidate.path));
        }
        Ok((answer, Some(body.join("\n"))))
    }

    /// `outline <file>`.
    pub fn outline(&self, file: &str) -> Result<Answer> {
        let mut answer = self.new_answer("outline", file);
        answer.tagged = false;
        self.note_missing_layers(&mut answer);
        let Some(graph) = &self.graph else { return Ok(answer) };
        for node in graph.outline(file)? {
            answer.rows.push(Row {
                tag: None,
                path: file.to_string(),
                line: node.start_line,
                detail: format!("{} {} (to {})", node.label.to_lowercase(), node.display(&graph.project), node.end_line),
            });
        }
        if !self.graph_fresh(file) {
            answer.notes.push(format!("{file} changed since the graph's snapshot; lines may have moved"));
        }
        Ok(answer)
    }

    /// `search <text>`.
    pub fn search(&self, text: &str) -> Result<Answer> {
        let mut answer = self.new_answer("search", text);
        answer.tagged = false;
        self.note_missing_layers(&mut answer);
        let Some(graph) = &self.graph else { return Ok(answer) };
        for node in graph.search(text, 200)? {
            answer.rows.push(Row {
                tag: None,
                path: node.file_path.clone(),
                line: node.start_line,
                detail: format!("{} {}", node.label.to_lowercase(), node.display(&graph.project)),
            });
        }
        self.search_bodies(graph, text, &mut answer);
        if !self.changed.is_empty() {
            answer.notes.push(format!("{} files changed since the graph's snapshot are ranked as they were then", self.changed.len()));
        }
        Ok(answer)
    }

    /// Adds the definitions whose *body* holds the words of `text` (in any case), after the code
    /// rows the graph ranked by name and documentation.
    ///
    /// codebase-memory's full-text table does not reach into bodies and wants every word:
    /// `search wall time` found no code although one method prints "wall time", and a sentence
    /// from a bug report found nothing at all, so the first thing an agent has, the report's own
    /// words, led nowhere and it went back to `grep`. Here the files the graph hashed are read as
    /// they are now (an edit since the snapshot is seen) and each matching line is attributed to
    /// its innermost definition. A definition must hold at least half of the words (all of them
    /// for one or two); it is ranked by the rarity of the words it holds, and a line holding all
    /// of them counts extra. Code comes first in the answer: the graph's code rows, these, then
    /// its documentation sections. Above the text bounds nothing is read and a note says so.
    fn search_bodies(&self, graph: &GraphStore, text: &str, answer: &mut Answer) {
        let mut words: Vec<String> = text.split_whitespace().map(str::to_lowercase).filter(|w| w.len() > 1).collect();
        words.sort();
        words.dedup();
        if words.is_empty() {
            return;
        }
        let Ok(hashes) = graph.file_hashes() else { return };
        let files: Vec<&String> = hashes.keys().filter(|f| is_code_path(f) && !self.deleted.contains(*f)).collect();
        let bytes: u64 = files.iter().filter_map(|f| hashes.get(*f)).map(|stamp| stamp.size).sum();
        if bytes > self.settings.scan_max_bytes {
            answer.notes.push(format!(
                "bodies were not searched ({} MiB of code, above the {} MiB bound): rows are name and documentation matches only",
                bytes >> 20,
                self.settings.scan_max_bytes >> 20
            ));
            return;
        }
        let needed = if words.len() <= 2 { words.len() } else { words.len().div_ceil(2) };
        let listed: BTreeSet<(String, u32)> = answer.rows.iter().map(|r| (r.path.clone(), r.line)).collect();
        // Files holding each word (its rarity), and per definition: the words it holds and, for
        // each of its matching lines, the words on that line.
        let mut in_files = vec![0usize; words.len()];
        let mut found: Vec<(BTreeSet<usize>, Vec<(u32, Vec<usize>)>, String, u32, String)> = Vec::new();
        for file in &files {
            let Ok(raw) = std::fs::read(self.repo.abs(file)) else { continue };
            let lower = String::from_utf8_lossy(&raw).to_lowercase();
            let present: Vec<usize> = (0..words.len()).filter(|i| starts_a_word(&lower, &words[*i])).collect();
            for i in &present {
                in_files[*i] += 1;
            }
            if present.len() < needed {
                continue;
            }
            let outline = graph.outline(file).unwrap_or_default();
            let mut spans: BTreeMap<Option<usize>, (BTreeSet<usize>, Vec<(u32, Vec<usize>)>)> = BTreeMap::new();
            for (index, line) in lower.lines().enumerate() {
                let hits: Vec<usize> = present.iter().copied().filter(|i| starts_a_word(line, &words[*i])).collect();
                if hits.is_empty() {
                    continue;
                }
                let number = index as u32 + 1;
                let innermost = outline
                    .iter()
                    .enumerate()
                    .filter(|(_, n)| n.start_line <= number && number <= n.end_line.max(n.start_line))
                    .min_by_key(|(_, n)| n.end_line.saturating_sub(n.start_line))
                    .map(|(i, _)| i);
                let entry = spans.entry(innermost).or_default();
                entry.0.extend(hits.iter().copied());
                entry.1.push((number, hits));
            }
            for (node, (seen, lines)) in spans {
                // Outside any definition the words must share a line: a file's top level is not a
                // unit, and its scattered words say nothing.
                if seen.len() < needed || (node.is_none() && !lines.iter().any(|(_, hits)| hits.len() >= needed)) {
                    continue;
                }
                let (line, detail) = match node {
                    Some(i) => (outline[i].start_line, format!("{} {}", outline[i].label.to_lowercase(), outline[i].display(&graph.project))),
                    None => (lines[0].0, "top level".to_string()),
                };
                if listed.contains(&((*file).clone(), line)) {
                    continue;
                }
                found.push((seen, lines, (*file).clone(), line, detail));
            }
        }
        // A word in few files says more than one in many; words that share a line say more than
        // words scattered over a body, which is what a test file's hundred mentions are.
        let rarity = |i: usize| (1.0 + files.len() as f64 / in_files[i].max(1) as f64).ln();
        let mut scored: Vec<(f64, String, u32, String)> = found
            .into_iter()
            .map(|(seen, lines, path, line, detail)| {
                let on_line = |hits: &Vec<usize>| hits.iter().map(|i| rarity(*i)).sum::<f64>();
                let best = lines.iter().max_by(|a, b| on_line(&a.1).total_cmp(&on_line(&b.1))).map(|l| (l.0, on_line(&l.1))).unwrap_or((line, 0.0));
                let mut score = 2.0 * best.1 + seen.iter().map(|i| rarity(*i)).sum::<f64>();
                if is_test_path(&path) {
                    score *= 0.6;
                }
                (score, path, line, format!("{detail}  (body: line {})", best.0))
            })
            .collect();
        scored.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.1.cmp(&b.1)).then(a.2.cmp(&b.2)));
        let body = scored.into_iter().take(BODY_ROWS).map(|(_, path, line, detail)| Row { tag: None, path, line, detail });
        let (sections, mut rows): (Vec<Row>, Vec<Row>) = std::mem::take(&mut answer.rows).into_iter().partition(|r| r.detail.starts_with("section "));
        rows.extend(body);
        rows.extend(sections);
        answer.rows = rows;
    }

    /// Lines of a graph edge's source: its recorded call-site line, or the whole-word matches of
    /// the name inside the source's span (codebase-memory records no line for `USAGE`/`IMPORTS`).
    fn edge_lines(&self, source: &Node, line: Option<u32>, name: &str) -> Vec<u32> {
        if let Some(line) = line {
            return vec![line];
        }
        let Ok(text) = std::fs::read_to_string(self.repo.abs(&source.file_path)) else { return vec![source.start_line.max(1)] };
        let (start, end) = if source.start_line == 0 { (1, u32::MAX) } else { (source.start_line, source.end_line.max(source.start_line)) };
        let found: Vec<u32> = text
            .lines()
            .enumerate()
            .map(|(i, l)| (i as u32 + 1, l))
            .filter(|(n, l)| *n >= start && *n <= end && !textscan::word_matches(l, name).is_empty())
            .map(|(n, _)| n)
            .collect();
        if found.is_empty() {
            vec![source.start_line.max(1)]
        } else {
            found
        }
    }

    /// `status`.
    pub fn status(&self) -> Vec<String> {
        let mut lines = Vec::new();
        match (&self.graph, &self.graph_error) {
            (Some(graph), _) => lines.push(format!(
                "universal: codebase-memory, project {} (indexed {}), {} files changed since",
                graph.project,
                graph.indexed_at(),
                self.graph_changes.as_ref().map(|c| c.changed.len() + c.deleted.len()).unwrap_or(0)
            )),
            (None, Some(error)) => lines.push(format!("universal: unavailable: {error}")),
            (None, None) => lines.push("universal: not built yet (`puffin-code index`)".to_string()),
        }
        if !self.changed.is_empty() || !self.deleted.is_empty() {
            let listed: Vec<String> = self.changed.iter().chain(self.deleted.iter()).take(8).cloned().collect();
            let more = (self.changed.len() + self.deleted.len()).saturating_sub(listed.len());
            lines.push(format!(
                "changed since the snapshots: {}{}",
                listed.join(", "),
                if more > 0 { format!(" and {more} more") } else { String::new() }
            ));
        }
        let manifest = Manifest::load(&self.repo.scip_dir());
        if manifest.runs.is_empty() {
            lines.push("exact: no SCIP index yet".to_string());
        }
        for (key, entry) in &manifest.runs {
            let commit = entry.commit.as_deref().map(|c| &c[..c.len().min(7)]).unwrap_or("-");
            let changed = self
                .stores
                .iter()
                .position(|s| s.entry.indexer == entry.indexer && s.entry.root == entry.root)
                .map(|i| self.store_changes[i].changed.len() + self.store_changes[i].deleted.len());
            lines.push(format!(
                "exact: {key}: {} @ {commit}{}{}",
                entry.status,
                changed.map(|n| format!(", {n} files changed since")).unwrap_or_default(),
                if entry.peak_rss_mb > 0 { format!(", peak {} MiB{}", entry.peak_rss_mb, if entry.peak_cap_bounded { " (cap-bounded)" } else { "" }) } else { String::new() }
            ));
        }
        for error in &self.store_errors {
            lines.push(format!("exact: unreadable: {error}"));
        }
        // The languages the repository has that no exact index covers yet, and why.
        let tools = crate::index::plan::Tools::find();
        let trusted = crate::config::is_trusted(&self.repo.main_root);
        let (in_submodules, skipped_in_submodules) = crate::index::plan::detect_in_submodules(&self.repo, &self.submodules);
        for target in crate::index::plan::detect(&self.repo).into_iter().chain(in_submodules) {
            let key = crate::index::store::key(target.indexer, &target.root);
            if manifest.runs.contains_key(&key) {
                continue;
            }
            let executing = target.kind == crate::index::host::Kind::Executing;
            let untrusted = if executing { crate::index::plan::untrusted_reason(&self.repo, trusted, &self.submodules, &target) } else { None };
            let why = match tools.unavailable(target.indexer) {
                Some(why) => why,
                None if untrusted.is_some() && target.submodule.is_empty() => continue, // the untrusted line below says it
                None if untrusted.is_some() => untrusted.unwrap_or_default(),
                None if executing => "not built yet (`puffin-code index --exact`)".to_string(),
                None if target.on_demand => "not built yet; runs on demand (`puffin-code index`)".to_string(),
                None => "not built yet (`puffin-code index`)".to_string(),
            };
            lines.push(format!("exact: {} for {}: {why}", target.indexer, crate::index::plan::display_root(&target.root)));
        }
        for why in crate::index::plan::root_python_note(crate::index::plan::root_python_files(&self.repo).1, "").into_iter().chain(skipped_in_submodules) {
            lines.push(format!("exact: {why}"));
        }
        let submodules: Vec<String> = self.submodules.iter().filter(|s| !s.indexed).map(|s| s.path.clone()).collect();
        let dirs: Vec<&String> = self
            .not_indexed_dirs
            .iter()
            .filter(|d| d.ends_with('/') && !submodules.iter().any(|s| d.trim_end_matches('/') == s))
            .collect();
        let files = self.not_indexed_dirs.iter().filter(|d| !d.ends_with('/') && !submodules.contains(d)).count();
        if !dirs.is_empty() || files > 0 {
            lines.push(format!(
                "not indexed by the graph (searched by text): {}{}",
                dirs.iter().map(|d| d.as_str()).collect::<Vec<_>>().join(", "),
                if files > 0 { format!("{}{files} single files (mostly binary)", if dirs.is_empty() { "" } else { "; " }) } else { String::new() }
            ));
        }
        for submodule in &self.submodules {
            lines.push(format!(
                "submodule {}: {} ({})",
                submodule.path,
                if submodule.indexed { "indexed" } else { "not indexed" },
                submodule.reason.label()
            ));
        }
        if !self.submodules.is_empty() {
            lines.push("submodules: `puffin-code submodules` shows the evidence; `include|exclude|auto <path>` changes it".to_string());
        }
        if !crate::config::is_trusted(&self.repo.main_root) {
            lines.push("untrusted: executing indexers (Rust, Java, .NET) do not run here; trust the project in puffin to enable them".to_string());
        }
        lines
    }
}

/// Roots named per indexer and snapshot in an answer's header; the rest are counted.
const SOURCE_ROOTS_NAMED: usize = 4;

/// `exact = SCIP scip-python dreamference, tests @ c673c5e; rust-analyzer puffin-code-rs @ e240c2d`:
/// the stores an answer's `exact` rows come from, grouped by indexer and snapshot. A repository
/// has a store per root, and a Python file at its root is a root of its own, so naming every one
/// on every answer would cost more than the answer; past [`SOURCE_ROOTS_NAMED`] they are counted.
fn sources_line<'a>(stores: impl Iterator<Item = (&'a str, &'a str, Option<&'a str>)>) -> Option<String> {
    let mut groups: Vec<((&str, &str), Vec<&str>)> = Vec::new();
    for (indexer, root, commit) in stores {
        let commit = commit.map(|c| &c[..c.len().min(7)]).unwrap_or("no commit");
        match groups.iter_mut().find(|(key, _)| *key == (indexer, commit)) {
            Some((_, roots)) if !roots.contains(&root) => roots.push(root),
            Some(_) => {}
            None => groups.push(((indexer, commit), vec![root])),
        }
    }
    if groups.is_empty() {
        return None;
    }
    let parts: Vec<String> = groups
        .into_iter()
        .map(|((indexer, commit), roots)| {
            let named: Vec<&str> = roots.iter().copied().filter(|r| !r.is_empty()).take(SOURCE_ROOTS_NAMED).collect();
            let more = roots.iter().filter(|r| !r.is_empty()).count() - named.len();
            let roots = match (named.is_empty(), more) {
                (true, _) => String::new(),
                (false, 0) => format!("{} ", named.join(", ")),
                (false, more) => format!("{} and {more} more ", named.join(", ")),
            };
            format!("{indexer} {roots}@ {commit}")
        })
        .collect();
    Some(format!("exact = SCIP {}", parts.join("; ")))
}

/// Levels `impact` follows by default, and at most.
pub const IMPACT_DEPTH: usize = 3;
const IMPACT_MAX_DEPTH: usize = 6;
/// Definitions one `impact` follows before it stops and says so.
const IMPACT_MAX_DEFINITIONS: usize = 200;
/// Changed definitions `impact --diff` starts from.
const IMPACT_DIFF_DEFINITIONS: usize = 25;

/// The state of one `impact` walk.
struct ImpactWalk {
    depth: usize,
    rows: Vec<Row>,
    seen_rows: BTreeSet<(String, u32)>,
    seen_definitions: BTreeSet<(String, u32)>,
    /// `(path, line of the definition, weakest tag so far, level of its references)`.
    queue: std::collections::VecDeque<(String, u32, Tag, usize)>,
    followed: usize,
    /// Definitions left unfollowed when the budget ran out.
    cut: usize,
}

impl ImpactWalk {
    fn new(depth: usize) -> ImpactWalk {
        ImpactWalk {
            depth,
            rows: Vec::new(),
            seen_rows: BTreeSet::new(),
            seen_definitions: BTreeSet::new(),
            queue: Default::default(),
            followed: 0,
            cut: 0,
        }
    }

    /// Carries a level's caveats onto the answer: what it could not resolve, what it dropped, and
    /// whether its changed files could be searched.
    fn absorb(&self, answer: &mut Answer, level: &Answer) {
        for unresolved in &level.unresolved {
            if !answer.unresolved.contains(unresolved) {
                answer.unresolved.push(unresolved.clone());
            }
        }
        for not_indexed in &level.not_indexed {
            if !answer.not_indexed.contains(not_indexed) {
                answer.not_indexed.push(not_indexed.clone());
            }
        }
        answer.deleted_dropped += level.deleted_dropped;
        if answer.not_checked.is_none() {
            answer.not_checked = level.not_checked.clone();
        }
        if answer.changed_searched != Some(false) {
            answer.changed_searched = level.changed_searched.or(answer.changed_searched);
        }
    }

    fn finish(self, answer: &mut Answer) {
        // Rows keep the order they were found in: nearest first.
        answer.rows = self.rows;
        answer.sources.push(format!(
            "impact = references of the definition, then of each definition enclosing one, to depth {}; text hits and imports are not followed",
            self.depth
        ));
        if self.cut > 0 {
            answer.notes.push(format!(
                "stopped after {IMPACT_MAX_DEFINITIONS} definitions: {} more were not followed, so deeper rows are missing",
                self.cut
            ));
        }
    }
}

/// The new-side line ranges of a unified diff with no context: `(path, first line, last line)`.
/// A pure deletion names the line it was removed after.
fn diff_ranges(diff: &str) -> Vec<(String, u32, u32)> {
    let mut out = Vec::new();
    let mut path: Option<String> = None;
    for line in diff.lines() {
        if let Some(rest) = line.strip_prefix("+++ ") {
            path = rest.strip_prefix("b/").map(str::to_string);
        } else if line.starts_with("@@") {
            let Some(path) = &path else { continue };
            let Some(new_side) = line.split_whitespace().find(|part| part.starts_with('+')) else { continue };
            let mut numbers = new_side[1..].split(',');
            let Some(start) = numbers.next().and_then(|n| n.parse::<u32>().ok()) else { continue };
            let count = numbers.next().and_then(|n| n.parse::<u32>().ok()).unwrap_or(1);
            let start = start.max(1);
            out.push((path.clone(), start, start + count.saturating_sub(1)));
        }
    }
    out
}

/// Keeps one row per `(path, line)`, the most trusted, ordered exact → heuristic → text.
fn dedup(mut rows: Vec<Row>) -> Vec<Row> {
    rows.sort_by(|a, b| (&a.path, a.line, a.tag).cmp(&(&b.path, b.line, b.tag)));
    rows.dedup_by(|later, first| later.path == first.path && later.line == first.line);
    rows.sort_by(|a, b| (a.tag, &a.path, a.line).cmp(&(b.tag, &b.path, b.line)));
    rows
}

fn role_label(roles: i32) -> &'static str {
    if roles & ROLE_DEFINITION != 0 {
        "definition"
    } else if roles & ROLE_IMPORT != 0 {
        "import"
    } else if roles & ROLE_WRITE != 0 {
        "write"
    } else if roles & ROLE_READ != 0 {
        "read"
    } else {
        "reference"
    }
}

fn graph_only_note(path: &str) -> String {
    let language = if path.ends_with(".py") { "Python" } else if path.ends_with(".rs") { "Rust" } else { "this language" };
    let recall = match language {
        "Python" => "about 58% (52% for methods)",
        "Rust" => "about 78%",
        _ => "an unmeasured share",
    };
    format!(
        "no exact index for {path}: the graph finds {recall} of the files that reference a {language} symbol, so text matches of the name are listed too (`heuristic (text)`)"
    )
}

fn impl_block_label(symbol: &str) -> String {
    // `…/impl#[Circle][Shape]` → `impl Shape for Circle`.
    let parts: Vec<&str> = symbol.rsplit("impl#").next().unwrap_or("").trim_matches(|c| c == '[' || c == ']').split("][").collect();
    match parts.as_slice() {
        [owner, trait_name] => format!("impl {trait_name} for {owner}"),
        [owner] => format!("impl {owner}"),
        _ => "impl".to_string(),
    }
}

/// Whether line `line` of `path` defines `name`: the text before it ends in a defining keyword.
fn defines_on_line(repo: &Repo, path: &str, line: u32, name: &str) -> bool {
    const KEYWORDS: &[&str] = &[
        "def", "class", "fn", "struct", "enum", "trait", "type", "const", "static", "let", "mod", "function", "interface", "var", "impl",
    ];
    let Ok(text) = std::fs::read_to_string(repo.abs(path)) else { return false };
    let Some(content) = text.lines().nth(line.saturating_sub(1) as usize) else { return false };
    textscan::word_matches(content, name).into_iter().any(|at| {
        let before = content[..at].trim_end();
        let word = before.rsplit(|c: char| !c.is_alphanumeric() && c != '_').next().unwrap_or("");
        KEYWORDS.contains(&word) || before.ends_with("async def") || before.ends_with(':') && before.contains("let")
    })
}

#[cfg(test)]
mod tests {
    use super::diff_ranges;

    #[test]
    fn the_header_groups_stores_by_indexer_and_snapshot() {
        let c = Some("c673c5ea0000");
        let stores = [
            ("rust-analyzer", "puffin-code-rs", Some("e240c2d50000")),
            ("scip-python", "dreamference", c),
            ("scip-python", "tests", c),
            ("scip-python", "setup.py", c),
            ("scip-python", "fano/main.py", c),
            ("scip-python", "fano/make_figures.py", c),
            ("scip-python", "scripts", Some("0f9868400000")),
            ("scip-typescript", "", None),
        ];
        assert_eq!(
            super::sources_line(stores.into_iter()).unwrap(),
            "exact = SCIP rust-analyzer puffin-code-rs @ e240c2d; scip-python dreamference, tests, setup.py, fano/main.py and 1 more @ c673c5e; \
             scip-python scripts @ 0f98684; scip-typescript @ no commit"
        );
        assert_eq!(super::sources_line(std::iter::empty()), None);
    }

    #[test]
    fn diff_hunks_give_new_side_ranges() {
        let diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3 +3,2 @@ def f():\n+x\n+y\n@@ -10,2 +11,0 @@\n-gone\n-gone\n\
                    diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-x\n";
        assert_eq!(diff_ranges(diff), vec![("a.py".to_string(), 3, 4), ("a.py".to_string(), 11, 11)]);
    }
}

/// Groups rows by file (for §7.2's per-file summary).
pub fn per_file(rows: &[Row]) -> Vec<(String, usize)> {
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for row in rows {
        *counts.entry(row.path.as_str()).or_default() += 1;
    }
    let mut list: Vec<(String, usize)> = counts.into_iter().map(|(p, c)| (p.to_string(), c)).collect();
    list.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    list
}
