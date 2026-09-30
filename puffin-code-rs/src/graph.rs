//! The universal layer: codebase-memory-mcp's graph, read in place and read-only (spec §7.5).
//!
//! Its CLI costs 6.6 s a call, so the router never runs it for a query; a direct SQL read of the
//! same database answers in milliseconds. The schema is pinned by fingerprint: a store written by
//! a codebase-memory this build does not know is not read (§7.5, "Compatibility").
//!
//! Lines here are 1-based, as codebase-memory writes them (§2).

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};

use crate::manifest::FileStamp;
use crate::paths::{self, Repo};
use crate::scip_store::schema_fingerprint;
use crate::scip_symbol::SymbolName;

/// The schema of codebase-memory-mcp v0.11.0's project database (see [`schema_fingerprint`]).
pub const SCHEMA_FINGERPRINT: &str = "6a416735d685a1510e5e3b2c4a3d34beb2f3f8b3d916eb10a5fcd66dde0634ae";

/// Node labels that are definitions a query can name.
const DEFINITION_LABELS: &[&str] =
    &["Function", "Method", "Class", "Struct", "Enum", "Interface", "Type", "Macro", "Field", "Variable"];

/// Edge types that are references to their target.
pub const REFERENCE_EDGES: &[&str] = &[
    "CALLS", "ASYNC_CALLS", "USAGE", "CALL_REFERENCE", "IMPORTS", "INHERITS", "DECORATES", "RAISES", "THROWS", "WRITES",
];

/// Edge types that are calls or uses, for `callers`/`callees`.
pub const CALL_EDGES: &[&str] = &["CALLS", "ASYNC_CALLS", "USAGE", "CALL_REFERENCE"];

/// A node of the graph.
#[derive(Debug, Clone)]
pub struct Node {
    pub id: i64,
    pub label: String,
    pub name: String,
    pub qualified_name: String,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
}

impl Node {
    /// The qualified name without the project prefix.
    pub fn display(&self, project: &str) -> String {
        self.qualified_name.strip_prefix(project).map(|s| s.trim_start_matches('.')).unwrap_or(&self.qualified_name).to_string()
    }
}

/// An edge and the node at its other end.
#[derive(Debug, Clone)]
pub struct Edge {
    pub kind: String,
    pub other: Node,
    /// The call-site line, when codebase-memory recorded one.
    pub line: Option<u32>,
    /// How many candidates codebase-memory weighed when it resolved the call (1 = unambiguous).
    pub candidates: u32,
}

/// An open graph database for one project.
pub struct GraphStore {
    conn: Connection,
    pub project: String,
    pub path: PathBuf,
}

/// Where codebase-memory keeps its databases.
pub fn cache_dir() -> PathBuf {
    match std::env::var_os("CBM_CACHE_DIR") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => paths::home().join(".cache/codebase-memory-mcp"),
    }
}

/// codebase-memory's project name for a root: the path without its leading slash, `/` → `-`,
/// runs of `-` collapsed (`/tmp/-x/repo` is `tmp-x-repo`).
pub fn project_name(root: &Path) -> String {
    let dashed = root.to_string_lossy().trim_start_matches('/').replace('/', "-");
    let mut out = String::with_capacity(dashed.len());
    for c in dashed.chars() {
        if !(c == '-' && out.ends_with('-')) {
            out.push(c);
        }
    }
    out
}

/// The database whose `projects` table names `root`, when the name derived from the path does not
/// match (a naming rule of codebase-memory's this code does not know).
fn find_by_root(cache: &Path, root: &Path) -> Option<(PathBuf, String)> {
    let root = root.to_string_lossy();
    for entry in std::fs::read_dir(cache).ok()?.flatten() {
        let path = entry.path();
        if path.extension().map(|e| e != "db").unwrap_or(true) || path.file_name()? == "_config.db" {
            continue;
        }
        let Ok(conn) = Connection::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_ONLY) else { continue };
        let name: Option<String> =
            conn.query_row("SELECT name FROM projects WHERE root_path = ?1", [root.as_ref()], |r| r.get(0)).optional().ok().flatten();
        if let Some(name) = name {
            return Some((path, name));
        }
    }
    None
}

/// The graph database and project for a repository: `PUFFIN_CODE_GRAPH_DB` and
/// `PUFFIN_CODE_PROJECT` when set (tests, and any caller that knows better), else
/// `<cache>/<project>.db` for the main worktree.
pub fn locate(repo: &Repo) -> (PathBuf, String) {
    let project = std::env::var("PUFFIN_CODE_PROJECT").unwrap_or_else(|_| project_name(&repo.main_root));
    if let Ok(path) = std::env::var("PUFFIN_CODE_GRAPH_DB") {
        return (PathBuf::from(path), project);
    }
    let path = cache_dir().join(format!("{project}.db"));
    if !path.is_file() {
        if let Some(found) = find_by_root(&cache_dir(), &repo.main_root) {
            return found;
        }
    }
    (path, project)
}

impl GraphStore {
    /// Opens the repository's graph read-only, with a 2 s busy timeout, and checks its schema.
    pub fn open(repo: &Repo) -> Result<Option<GraphStore>> {
        let (path, project) = locate(repo);
        if !path.is_file() {
            return Ok(None);
        }
        let conn = Connection::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
            .with_context(|| format!("opening {}", path.display()))?;
        conn.busy_timeout(Duration::from_secs(2))?;
        let fingerprint = schema_fingerprint(&conn)?;
        if fingerprint != SCHEMA_FINGERPRINT {
            bail!(
                "{} was written by a codebase-memory this puffin-code does not know (schema {}); \
                 update puffin-code with `puffin update`, or answer with rg meanwhile",
                path.display(),
                &fingerprint[..12]
            );
        }
        let known: Option<String> =
            conn.query_row("SELECT name FROM projects WHERE name = ?1", [&project], |r| r.get(0)).optional()?;
        if known.is_none() {
            return Ok(None);
        }
        Ok(Some(GraphStore { conn, project, path }))
    }

    /// `store_meta.mutation_gen`: changes whenever codebase-memory commits a re-index.
    pub fn mutation_gen(&self) -> String {
        self.conn
            .query_row("SELECT v FROM store_meta WHERE k = 'mutation_gen'", [], |r| r.get(0))
            .unwrap_or_default()
    }

    /// When the project was last indexed.
    pub fn indexed_at(&self) -> String {
        self.conn
            .query_row("SELECT indexed_at FROM projects WHERE name = ?1", [&self.project], |r| r.get(0))
            .unwrap_or_default()
    }

    fn node_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Node> {
        Ok(Node {
            id: row.get(0)?,
            label: row.get(1)?,
            name: row.get(2)?,
            qualified_name: row.get(3)?,
            file_path: row.get(4)?,
            start_line: row.get::<_, i64>(5)?.max(0) as u32,
            end_line: row.get::<_, i64>(6)?.max(0) as u32,
        })
    }

    /// Definitions named by the query's segments (§7.4 step 1): by `name`, then filtered by the
    /// qualified name's path.
    pub fn candidates(&self, query: &[String]) -> Result<Vec<Node>> {
        let Some(last) = query.last() else { return Ok(Vec::new()) };
        let mut stmt = self.conn.prepare_cached(
            "SELECT id, label, name, qualified_name, file_path, start_line, end_line FROM nodes
             WHERE project = ?1 AND name = ?2 AND file_path NOT LIKE '<%' ORDER BY file_path, start_line",
        )?;
        let nodes: Vec<Node> = stmt.query_map(params![self.project, last], Self::node_from_row)?.collect::<rusqlite::Result<_>>()?;
        Ok(nodes
            .into_iter()
            .filter(|n| DEFINITION_LABELS.contains(&n.label.as_str()))
            .filter(|n| {
                let segments: Vec<String> = n.display(&self.project).split('.').map(str::to_string).collect();
                SymbolName { segments, trait_impl: None, kind: crate::scip_symbol::Kind::Term }.matches(query)
            })
            .collect())
    }

    /// Edges into `node` of the given types, with their source nodes.
    pub fn incoming(&self, node: i64, types: &[&str]) -> Result<Vec<Edge>> {
        self.edges(node, types, true)
    }

    /// Edges out of `node` of the given types, with their target nodes.
    pub fn outgoing(&self, node: i64, types: &[&str]) -> Result<Vec<Edge>> {
        self.edges(node, types, false)
    }

    fn edges(&self, node: i64, types: &[&str], incoming: bool) -> Result<Vec<Edge>> {
        let (this_end, other_end) = if incoming { ("target_id", "source_id") } else { ("source_id", "target_id") };
        let sql = format!(
            "SELECT n.id, n.label, n.name, n.qualified_name, n.file_path, n.start_line, n.end_line, e.type, e.properties
             FROM edges e JOIN nodes n ON n.id = e.{other_end} WHERE e.{this_end} = ?1"
        );
        let mut stmt = self.conn.prepare_cached(&sql)?;
        let rows = stmt.query_map([node], |row| {
            let other = Self::node_from_row(row)?;
            let kind: String = row.get(7)?;
            let properties: String = row.get::<_, Option<String>>(8)?.unwrap_or_default();
            Ok((other, kind, properties))
        })?;
        let mut out = Vec::new();
        for row in rows {
            let (other, kind, properties) = row?;
            if !types.contains(&kind.as_str()) || other.file_path.starts_with('<') {
                continue;
            }
            let props: serde_json::Value = serde_json::from_str(&properties).unwrap_or_default();
            let line = props.get("line").and_then(serde_json::Value::as_u64).map(|l| l as u32).filter(|l| *l > 0);
            let candidates = props.get("candidates").and_then(serde_json::Value::as_u64).unwrap_or(1) as u32;
            out.push(Edge { kind, other, line, candidates });
        }
        Ok(out)
    }

    /// The definitions of a file, in order (`outline`).
    pub fn outline(&self, file: &str) -> Result<Vec<Node>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT id, label, name, qualified_name, file_path, start_line, end_line FROM nodes
             WHERE project = ?1 AND file_path = ?2 ORDER BY start_line, end_line DESC",
        )?;
        let nodes: Vec<Node> = stmt.query_map(params![self.project, file], Self::node_from_row)?.collect::<rusqlite::Result<_>>()?;
        Ok(nodes.into_iter().filter(|n| DEFINITION_LABELS.contains(&n.label.as_str()) && n.label != "Variable").collect())
    }

    /// The innermost definition of a file enclosing a line.
    pub fn enclosing(&self, file: &str, line: u32) -> Result<Option<Node>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT id, label, name, qualified_name, file_path, start_line, end_line FROM nodes
             WHERE project = ?1 AND file_path = ?2 AND start_line <= ?3 AND end_line >= ?3
               AND label IN ('Function', 'Method', 'Class', 'Struct', 'Enum', 'Interface', 'Type', 'Macro')
             ORDER BY (end_line - start_line) LIMIT 1",
        )?;
        Ok(stmt.query_row(params![self.project, file, line], Self::node_from_row).optional()?)
    }

    /// FTS5 over names, qualified names and bodies, ranked by BM25 (`search`).
    pub fn search(&self, text: &str, limit: usize) -> Result<Vec<Node>> {
        // The table is contentless: it yields rowids, which are node ids.
        let query = fts_query(text);
        if query.is_empty() {
            return Ok(Vec::new());
        }
        let mut stmt = self.conn.prepare_cached(
            "SELECT n.id, n.label, n.name, n.qualified_name, n.file_path, n.start_line, n.end_line
             FROM (SELECT rowid AS id, rank FROM nodes_fts WHERE nodes_fts MATCH ?1 ORDER BY rank LIMIT ?3) f
             JOIN nodes n ON n.id = f.id WHERE n.project = ?2 AND n.file_path NOT LIKE '<%' ORDER BY f.rank",
        )?;
        let nodes = stmt.query_map(params![query, self.project, (limit * 4) as i64], Self::node_from_row)?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(nodes.into_iter().filter(|n| !matches!(n.label.as_str(), "File" | "Folder" | "Project")).take(limit).collect())
    }

    /// Every file codebase-memory hashed, with its stamp.
    pub fn file_hashes(&self) -> Result<HashMap<String, FileStamp>> {
        // `.codebase-memory/…` rows are the tool's own inputs (git context, extension config), not
        // files of the repository.
        let mut stmt = self.conn.prepare_cached(
            "SELECT rel_path, sha256, mtime_ns, size FROM file_hashes WHERE project = ?1 AND rel_path NOT LIKE '.codebase-memory/%'",
        )?;
        let rows = stmt.query_map([&self.project], |r| {
            Ok((r.get::<_, String>(0)?, FileStamp { sha256: r.get(1)?, mtime_ns: r.get(2)?, size: r.get::<_, i64>(3)?.max(0) as u64 }))
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Paths codebase-memory left out (`index_coverage`: `not_indexed_dir` / `not_indexed_file`).
    pub fn not_indexed(&self) -> Result<BTreeSet<String>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT rel_path FROM index_coverage WHERE project = ?1 AND kind IN ('not_indexed_dir', 'not_indexed_file')",
        )?;
        let paths = stmt.query_map([&self.project], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<BTreeSet<_>>>()?;
        Ok(paths)
    }
}

/// Turns free text into an FTS5 query: each word a prefix term, all of them required.
fn fts_query(text: &str) -> String {
    text.split(|c: char| !c.is_alphanumeric() && c != '_')
        .filter(|w| !w.is_empty())
        .map(|w| format!("\"{}\"*", w.replace('"', "")))
        .collect::<Vec<_>>()
        .join(" ")
}
