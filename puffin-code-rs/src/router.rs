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
use crate::textscan;

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
}

impl Context {
    /// Opens both layers and computes their changed sets.
    pub fn load(repo: Repo, settings: Settings) -> Result<Context> {
        let mut git = GitView::new(&repo);
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
                let submodules = repo.submodules();
                let is_excluded = |path: &str| {
                    is_state(path) || excluded.iter().any(|dir| path == dir || path.starts_with(&format!("{dir}/")))
                };
                // Tracked files an ignore rule dropped: covered by text instead (§4.1).
                for file in git.all_files().clone() {
                    if !is_state(&file) && is_excluded(&file) && !submodules.iter().any(|s| file.starts_with(&format!("{s}/"))) {
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
        Ok(Context {
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
        })
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

    /// Restricts the header's source line to the stores an answer actually drew on.
    fn set_sources(&self, answer: &mut Answer, stores: &BTreeSet<usize>) {
        answer.sources = self.sources(Some(stores));
    }

    /// The header's source line: which snapshot each tag comes from.
    fn sources(&self, only: Option<&BTreeSet<usize>>) -> Vec<String> {
        let mut exact: Vec<String> = self
            .stores
            .iter()
            .enumerate()
            .filter(|(i, _)| only.map(|set| set.contains(i)).unwrap_or(true))
            .map(|(_, s)| {
                let commit = s.entry.commit.as_deref().map(|c| &c[..c.len().min(7)]).unwrap_or("no commit");
                format!("{} {}@ {commit}", s.entry.indexer, if s.entry.root.is_empty() { String::new() } else { format!("{} ", s.entry.root) })
            })
            .collect();
        exact.dedup();
        let mut out = Vec::new();
        if !exact.is_empty() {
            out.push(format!("exact = SCIP {}", exact.join(", ")));
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

    /// Files to search by text: every changed file, plus tracked files no layer indexes.
    fn text_files(&self) -> Vec<String> {
        let mut files: BTreeSet<String> = self.changed.clone();
        for file in &self.not_indexed_files {
            if !self.any_scip_fresh(file) {
                files.insert(file.clone());
            }
        }
        files.into_iter().collect()
    }

    /// Runs the text search and records its outcome on the answer.
    fn text_rows(&self, name: &str, answer: &mut Answer, keep: &dyn Fn(&str, u32) -> bool) -> Vec<Row> {
        let files = self.text_files();
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
                let why = if self.changed.contains(&hit.path) { "changed since snapshot" } else { "not indexed" };
                Row { tag: Some(Tag::Text), path: hit.path, line: hit.line, detail: why.to_string() }
            })
            .collect()
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
        }
        self.set_sources(&mut answer, &exact_stores);
        rows.extend(self.text_rows(&candidate.name, &mut answer, &|path, _| !covered_exactly(path) || self.changed.contains(path)));
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
        rows.extend(self.text_rows(&trait_name, &mut answer, &|path, _| self.changed.contains(path)));
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
        if !self.changed.is_empty() {
            answer.notes.push(format!("{} files changed since the graph's snapshot are ranked as they were then", self.changed.len()));
        }
        Ok(answer)
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
        let submodules = self.repo.submodules();
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
        if !submodules.is_empty() {
            lines.push(format!("submodules excluded: {} (`puffin-code index --include-submodules`)", submodules.join(", ")));
        }
        if !crate::config::is_trusted(&self.repo.main_root) {
            lines.push("untrusted: executing indexers (Rust, Java, .NET) do not run here; trust the project in puffin to enable them".to_string());
        }
        lines
    }
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
    format!("no exact index for {path}: the graph finds {recall} of the files that reference a {language} symbol; confirm with rg")
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
