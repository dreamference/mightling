//! The exact layer: a SCIP index converted to SQLite by the scip CLI's `expt-convert` (spec §7.5).
//!
//! The store's tables are `documents`, `chunks` (occurrences in zstd-compressed protobuf blobs of
//! about 200 each), `global_symbols`, `mentions(chunk_id, symbol_id, role)` and
//! `defn_enclosing_ranges`. `expt-convert` stores no relationships, and `display_name` is empty for
//! scip-python and rust-analyzer, so [`postprocess`] adds two tables of its own after conversion:
//! `puffin_names` (each symbol's descriptor name, for lookup by name) and `puffin_relationships`
//! (read from the `.scip` file, for `impl`). Objects named `puffin_*` are left out of the schema
//! fingerprint, which pins what `expt-convert` itself writes.
//!
//! Positions in SCIP are 0-based; everything this module returns is 1-based (spec §7.4 step 5).

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};

use crate::manifest::{hex, RunEntry};
use crate::scip_symbol::{self, SymbolName};

/// SHA-256 of the ordered `sql` column of `sqlite_master` of an `expt-convert` store, scip CLI
/// v0.10.0, `puffin_*` objects excluded. A store whose schema differs is not read (§7.5).
pub const SCHEMA_FINGERPRINT: &str = "5ac15027d79d86e9d43ec1489ca13345f75a6d937b9e254a11bf2ff0d9d0c68c";

/// Role bits of a SCIP occurrence.
pub const ROLE_DEFINITION: i32 = 1;
pub const ROLE_IMPORT: i32 = 2;
pub const ROLE_WRITE: i32 = 4;
pub const ROLE_READ: i32 = 8;

/// One occurrence, 1-based, with a repository-relative path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Occurrence {
    pub path: String,
    pub line: u32,
    pub column: u32,
    pub roles: i32,
    pub symbol: String,
}

/// A symbol's definition.
#[derive(Debug, Clone)]
pub struct Definition {
    pub symbol_id: i64,
    pub symbol: String,
    pub name: SymbolName,
    pub path: String,
    /// The definition's name line.
    pub line: u32,
    /// The enclosing range of the definition (its whole body), when the indexer reports one.
    pub span: Option<(u32, u32)>,
}

/// An open query store and the run that produced it.
pub struct ScipStore {
    conn: Connection,
    pub entry: RunEntry,
    pub path: PathBuf,
    /// Repository path → document path. An indexer also writes documents for files outside its
    /// root that the root imports (scip-python: `../dreamference/…` in the `tests` store).
    docs: HashMap<String, String>,
}

/// Joins a root's prefix and a document path, resolving `.` and `..`.
pub fn join_normalized(prefix: &str, doc: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for part in prefix.split('/').chain(doc.split('/')) {
        match part {
            "" | "." => {}
            ".." => {
                parts.pop();
            }
            other => parts.push(other),
        }
    }
    parts.join("/")
}

impl ScipStore {
    /// Opens a store read-only and checks its fingerprint.
    pub fn open(path: &Path, entry: RunEntry) -> Result<ScipStore> {
        let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
            .with_context(|| format!("opening {}", path.display()))?;
        conn.busy_timeout(Duration::from_secs(2))?;
        let fingerprint = schema_fingerprint(&conn)?;
        if fingerprint != SCHEMA_FINGERPRINT {
            bail!(
                "{} was written by a scip CLI this puffin-code does not know (schema {}); run `puffin-code index` to rebuild it",
                path.display(),
                &fingerprint[..12]
            );
        }
        let has_names: bool = conn
            .query_row("SELECT count(*) FROM sqlite_master WHERE name = 'puffin_names'", [], |r| r.get::<_, i64>(0))
            .map(|n| n > 0)?;
        if !has_names {
            bail!("{} has not been post-processed; run `puffin-code index` to rebuild it", path.display());
        }
        let docs = {
            let mut stmt = conn.prepare("SELECT relative_path FROM documents")?;
            let docs = stmt.query_map([], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
            docs.into_iter().map(|d| (join_normalized(&entry.path_prefix, &d), d)).collect()
        };
        Ok(ScipStore { conn, entry, path: path.to_path_buf(), docs })
    }

    /// The store's document path for a repository-relative path, if it has one.
    pub fn doc_path(&self, repo_path: &str) -> Option<&str> {
        self.docs.get(repo_path).map(String::as_str)
    }

    /// The repository-relative path of a document path.
    pub fn repo_path(&self, doc_path: &str) -> String {
        join_normalized(&self.entry.path_prefix, doc_path)
    }

    /// Whether the store has a document for this repository-relative path.
    pub fn covers(&self, repo_path: &str) -> bool {
        self.docs.contains_key(repo_path)
    }

    /// Definitions of symbols whose descriptor name is `query`'s last segment and whose path the
    /// query's earlier segments match.
    pub fn definitions_named(&self, query: &[String]) -> Result<Vec<Definition>> {
        let Some(last) = query.last() else { return Ok(Vec::new()) };
        let mut stmt = self.conn.prepare_cached(
            "SELECT g.id, g.symbol FROM puffin_names n JOIN global_symbols g ON g.id = n.symbol_id WHERE n.name = ?1",
        )?;
        let symbols: Vec<(i64, String)> =
            stmt.query_map([last], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
        let mut out = Vec::new();
        for (id, symbol) in symbols {
            let Some(name) = scip_symbol::parse(&symbol) else { continue };
            if !name.matches(query) {
                continue;
            }
            out.extend(self.definitions_of(id, &symbol, &name)?);
        }
        Ok(out)
    }

    /// The definition sites of one symbol.
    pub fn definitions_of(&self, symbol_id: i64, symbol: &str, name: &SymbolName) -> Result<Vec<Definition>> {
        let spans = self.enclosing_spans(symbol_id)?;
        let mut out = Vec::new();
        for occ in self.occurrences_of(symbol_id, symbol)? {
            if occ.roles & ROLE_DEFINITION == 0 {
                continue;
            }
            let span = spans.get(&occ.path).and_then(|list| {
                list.iter().copied().filter(|(s, e)| *s <= occ.line && occ.line <= *e).min_by_key(|(s, e)| e - s)
            });
            out.push(Definition { symbol_id, symbol: symbol.to_string(), name: name.clone(), path: occ.path, line: occ.line, span });
        }
        Ok(out)
    }

    /// The symbol defined at a location: the definition occurrence on `line` (1-based) of
    /// `repo_path` whose name is `name`, if the store has one (spec §7.4 step 2).
    pub fn symbol_defined_at(&self, repo_path: &str, lines: (u32, u32), name: &str) -> Result<Option<(i64, String)>> {
        let Some(doc) = self.doc_path(repo_path) else { return Ok(None) };
        let (first, last) = (lines.0.saturating_sub(1), lines.1.saturating_sub(1));
        let mut stmt = self.conn.prepare_cached(
            "SELECT c.occurrences FROM chunks c JOIN documents d ON d.id = c.document_id
             WHERE d.relative_path = ?1 AND c.start_line <= ?3 AND c.end_line >= ?2 ORDER BY c.chunk_index",
        )?;
        let blobs: Vec<Vec<u8>> = stmt.query_map(params![doc, first, last], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        let mut best: Option<(u32, String)> = None;
        for blob in blobs {
            for occ in decode_chunk(&blob)? {
                if occ.roles & ROLE_DEFINITION == 0 || occ.line < first || occ.line > last {
                    continue;
                }
                if scip_symbol::parse(&occ.symbol).map(|n| n.name() == name).unwrap_or(false)
                    && best.as_ref().map(|(l, _)| occ.line < *l).unwrap_or(true)
                {
                    best = Some((occ.line, occ.symbol));
                }
            }
        }
        let Some((_, symbol)) = best else { return Ok(None) };
        let id: Option<i64> = self
            .conn
            .query_row("SELECT id FROM global_symbols WHERE symbol = ?1", [&symbol], |r| r.get(0))
            .optional()?;
        Ok(id.map(|id| (id, symbol)))
    }

    /// Every occurrence of a symbol, from the chunks `mentions` points at.
    pub fn occurrences_of(&self, symbol_id: i64, symbol: &str) -> Result<Vec<Occurrence>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT DISTINCT c.id, c.occurrences, d.relative_path FROM mentions m
             JOIN chunks c ON c.id = m.chunk_id JOIN documents d ON d.id = c.document_id
             WHERE m.symbol_id = ?1",
        )?;
        let rows: Vec<(i64, Vec<u8>, String)> =
            stmt.query_map([symbol_id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<rusqlite::Result<_>>()?;
        let mut out = Vec::new();
        for (_, blob, doc) in rows {
            for occ in decode_chunk(&blob)? {
                if occ.symbol == symbol {
                    out.push(Occurrence {
                        path: self.repo_path(&doc),
                        line: occ.line + 1,
                        column: occ.column + 1,
                        roles: occ.roles,
                        symbol: occ.symbol,
                    });
                }
            }
        }
        out.sort_by(|a, b| (&a.path, a.line, a.column).cmp(&(&b.path, b.line, b.column)));
        out.dedup_by(|a, b| a.path == b.path && a.line == b.line && a.column == b.column);
        Ok(out)
    }

    /// Enclosing ranges of a symbol's definitions, by repository path, 1-based and inclusive.
    fn enclosing_spans(&self, symbol_id: i64) -> Result<HashMap<String, Vec<(u32, u32)>>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT d.relative_path, r.start_line, r.end_line FROM defn_enclosing_ranges r
             JOIN documents d ON d.id = r.document_id WHERE r.symbol_id = ?1",
        )?;
        let mut out: HashMap<String, Vec<(u32, u32)>> = HashMap::new();
        for row in stmt.query_map([symbol_id], |r| Ok((r.get::<_, String>(0)?, r.get::<_, u32>(1)?, r.get::<_, u32>(2)?)))? {
            let (doc, start, end) = row?;
            out.entry(self.repo_path(&doc)).or_default().push((start + 1, end + 1));
        }
        Ok(out)
    }

    /// The innermost definition enclosing a line (1-based) of a repository path (spec §7.1's
    /// `callers`).
    pub fn enclosing_definition(&self, repo_path: &str, line: u32) -> Result<Option<String>> {
        let Some(doc) = self.doc_path(repo_path) else { return Ok(None) };
        let line0 = line.saturating_sub(1);
        let mut stmt = self.conn.prepare_cached(
            "SELECT g.symbol FROM defn_enclosing_ranges r JOIN documents d ON d.id = r.document_id
             JOIN global_symbols g ON g.id = r.symbol_id
             WHERE d.relative_path = ?1 AND r.start_line <= ?2 AND r.end_line >= ?2
             ORDER BY (r.end_line - r.start_line), r.start_line DESC LIMIT 1",
        )?;
        Ok(stmt.query_row(params![doc, line0], |r| r.get(0)).optional()?)
    }

    /// The occurrences inside a span (1-based, inclusive) of a repository path, definitions
    /// excluded: what the code there refers to (`callees`).
    pub fn occurrences_within(&self, repo_path: &str, span: (u32, u32)) -> Result<Vec<Occurrence>> {
        let Some(doc) = self.doc_path(repo_path) else { return Ok(Vec::new()) };
        let (first, last) = (span.0.saturating_sub(1), span.1.saturating_sub(1));
        let mut stmt = self.conn.prepare_cached(
            "SELECT c.occurrences FROM chunks c JOIN documents d ON d.id = c.document_id
             WHERE d.relative_path = ?1 AND c.start_line <= ?3 AND c.end_line >= ?2 ORDER BY c.chunk_index",
        )?;
        let blobs: Vec<Vec<u8>> = stmt.query_map(params![doc, first, last], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        let mut out = Vec::new();
        for blob in blobs {
            for occ in decode_chunk(&blob)? {
                if occ.line >= first && occ.line <= last && occ.roles & ROLE_DEFINITION == 0 && !occ.symbol.starts_with("local ") {
                    out.push(Occurrence { path: repo_path.to_string(), line: occ.line + 1, column: occ.column + 1, roles: occ.roles, symbol: occ.symbol });
                }
            }
        }
        Ok(out)
    }

    /// Symbols that implement `symbol` (from the `.scip`'s relationships, spec §7.1's `impl`).
    pub fn implementations(&self, symbol: &str) -> Result<Vec<(i64, String)>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT g.id, g.symbol FROM puffin_relationships r JOIN global_symbols g ON g.id = r.symbol_id
             WHERE r.target = ?1 AND r.is_implementation = 1",
        )?;
        let mut out: Vec<(i64, String)> =
            stmt.query_map([symbol], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
        // rust-analyzer emits no relationships; its trait implementations are named
        // `impl#[Type][Trait]member`, and a trait's own implementations `impl#[Type][Trait]`.
        if let Some(target) = scip_symbol::parse(symbol) {
            let mut stmt = self.conn.prepare_cached(
                "SELECT g.id, g.symbol FROM puffin_names n JOIN global_symbols g ON g.id = n.symbol_id WHERE n.name = ?1",
            )?;
            let trait_name = if target.kind == scip_symbol::Kind::Type { target.name().to_string() } else {
                target.segments.iter().rev().nth(1).cloned().unwrap_or_default()
            };
            let member = (target.kind != scip_symbol::Kind::Type).then(|| target.name().to_string());
            let lookup = member.clone().unwrap_or_else(|| trait_name.clone());
            let candidates: Vec<(i64, String)> =
                stmt.query_map([&lookup], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
            for (id, candidate) in candidates {
                let Some(parsed) = scip_symbol::parse(&candidate) else { continue };
                if parsed.trait_impl.as_deref() == Some(trait_name.as_str()) && member.is_some() {
                    out.push((id, candidate));
                }
            }
            if member.is_none() {
                // The impl block has no symbol of its own; its members are `…/impl#[Type][Trait]m().`,
                // and the implementing type is `…/Type#` in the same namespace.
                let mut stmt = self.conn.prepare_cached(
                    "SELECT symbol FROM global_symbols WHERE symbol LIKE '%impl#[%][' || ?1 || ']%'",
                )?;
                let members: Vec<String> = stmt.query_map([&trait_name], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
                let mut owners = BTreeSet::new();
                for member in members {
                    let Some((namespace, rest)) = member.rsplit_once("impl#[") else { continue };
                    let Some((owner, after)) = rest.split_once("][") else { continue };
                    if after.starts_with(&format!("{trait_name}]")) {
                        owners.insert(format!("{namespace}{owner}#"));
                    }
                }
                let mut lookup = self.conn.prepare_cached("SELECT id FROM global_symbols WHERE symbol = ?1")?;
                for owner in owners {
                    if let Some(id) = lookup.query_row([&owner], |r| r.get::<_, i64>(0)).optional()? {
                        out.push((id, owner));
                    }
                }
            }
        }
        out.sort();
        out.dedup();
        Ok(out)
    }

    /// The definitions of one file that have an enclosing range, outermost first: `(start, end,
    /// symbol)`, 1-based and inclusive. What `outline` lists, and what a body match of `search`
    /// is attributed to, when the graph is off (`layers = exact`).
    pub fn definitions_in(&self, repo_path: &str) -> Result<Vec<(u32, u32, String)>> {
        let Some(doc) = self.doc_path(repo_path) else { return Ok(Vec::new()) };
        let mut stmt = self.conn.prepare_cached(
            "SELECT r.start_line, r.end_line, g.symbol FROM defn_enclosing_ranges r
             JOIN documents d ON d.id = r.document_id JOIN global_symbols g ON g.id = r.symbol_id
             WHERE d.relative_path = ?1 ORDER BY r.start_line, r.end_line DESC",
        )?;
        let rows = stmt.query_map([doc], |r| Ok((r.get::<_, u32>(0)? + 1, r.get::<_, u32>(1)? + 1, r.get::<_, String>(2)?)))?;
        let mut out: Vec<(u32, u32, String)> = rows.collect::<rusqlite::Result<_>>()?;
        out.retain(|(_, _, symbol)| !symbol.starts_with("local "));
        out.dedup_by(|a, b| a.0 == b.0 && a.1 == b.1 && a.2 == b.2);
        Ok(out)
    }

    /// Symbols whose descriptor name contains `word` (ASCII case ignored), at most `limit`.
    pub fn symbols_containing(&self, word: &str, limit: usize) -> Result<Vec<(i64, String)>> {
        let pattern = format!("%{}%", word.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_"));
        let mut stmt = self.conn.prepare_cached(
            "SELECT g.id, g.symbol FROM puffin_names n JOIN global_symbols g ON g.id = n.symbol_id
             WHERE n.name LIKE ?1 ESCAPE '\\' LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![pattern, limit as i64], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// The store's documents, repository-relative.
    pub fn documents(&self) -> BTreeSet<String> {
        self.docs.keys().cloned().collect()
    }
}

/// SHA-256 of the ordered `sql` of `sqlite_master`, `puffin_*` objects and SQLite's own excluded.
pub fn schema_fingerprint(conn: &Connection) -> Result<String> {
    use sha2::{Digest, Sha256};
    let mut stmt = conn.prepare(
        "SELECT coalesce(sql, '') FROM sqlite_master
         WHERE name NOT LIKE 'puffin\\_%' ESCAPE '\\' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'
         ORDER BY type, name",
    )?;
    let mut hasher = Sha256::new();
    for sql in stmt.query_map([], |r| r.get::<_, String>(0))? {
        hasher.update(sql?.as_bytes());
        hasher.update(b"\n");
    }
    Ok(hex(&hasher.finalize()))
}

/// Adds puffin-code's own tables to a freshly converted store: descriptor names and, from the
/// `.scip` file itself, relationships. Run once, at index time, on the `.new` file before it is
/// renamed into place.
pub fn postprocess(db: &Path, scip_file: &Path) -> Result<()> {
    let mut conn = Connection::open(db)?;
    let tx = conn.transaction()?;
    tx.execute_batch(
        "DROP TABLE IF EXISTS puffin_names; DROP TABLE IF EXISTS puffin_relationships; DROP TABLE IF EXISTS puffin_meta;
         CREATE TABLE puffin_names (symbol_id INTEGER NOT NULL, name TEXT NOT NULL);
         CREATE TABLE puffin_relationships (symbol_id INTEGER NOT NULL, target TEXT NOT NULL,
             is_reference INTEGER NOT NULL, is_implementation INTEGER NOT NULL, is_type_definition INTEGER NOT NULL);
         CREATE TABLE puffin_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);",
    )?;
    {
        let mut select = tx.prepare("SELECT id, symbol FROM global_symbols")?;
        let mut insert = tx.prepare("INSERT INTO puffin_names (symbol_id, name) VALUES (?1, ?2)")?;
        let rows: Vec<(i64, String)> = select.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
        for (id, symbol) in rows {
            if let Some(name) = scip_symbol::parse(&symbol) {
                insert.execute(params![id, name.name()])?;
            }
        }
        let ids: HashMap<String, i64> = {
            let mut stmt = tx.prepare("SELECT symbol, id FROM global_symbols")?;
            let pairs = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?.collect::<rusqlite::Result<Vec<_>>>()?;
            pairs.into_iter().collect()
        };
        let bytes = std::fs::read(scip_file).with_context(|| format!("reading {}", scip_file.display()))?;
        let mut insert = tx.prepare(
            "INSERT INTO puffin_relationships (symbol_id, target, is_reference, is_implementation, is_type_definition) VALUES (?1, ?2, ?3, ?4, ?5)",
        )?;
        for (symbol, rel) in relationships(&bytes)? {
            if let Some(id) = ids.get(&symbol) {
                insert.execute(params![id, rel.symbol, rel.is_reference, rel.is_implementation, rel.is_type_definition])?;
            }
        }
    }
    tx.execute_batch(
        "CREATE INDEX puffin_names_name ON puffin_names(name);
         CREATE INDEX puffin_relationships_target ON puffin_relationships(target);",
    )?;
    tx.execute("INSERT INTO puffin_meta (k, v) VALUES ('postprocessed', '1')", [])?;
    tx.commit()?;
    // expt-convert writes WAL mode, and a WAL database cannot be opened read-only where its
    // directory is not writable (the read-only sandbox): the reader must create `-shm`.
    conn.pragma_update(None, "journal_mode", "DELETE")?;
    Ok(())
}

// ---- A minimal protobuf reader for the two SCIP messages puffin-code reads. ----
//
// The scip crate would parse a whole `Index` into memory (2.1 GB for Codex's 355 MB index, §2);
// reading the wire format directly walks it document by document and keeps only what is needed.

/// An occurrence as stored: 0-based.
#[derive(Debug, Clone)]
pub struct RawOccurrence {
    pub line: u32,
    pub column: u32,
    pub roles: i32,
    pub symbol: String,
}

/// A relationship of a symbol.
#[derive(Debug, Clone, Default)]
pub struct Relationship {
    pub symbol: String,
    pub is_reference: bool,
    pub is_implementation: bool,
    pub is_type_definition: bool,
}

/// Decodes one chunk blob: zstd-compressed `scip.Document { occurrences }`.
pub fn decode_chunk(blob: &[u8]) -> Result<Vec<RawOccurrence>> {
    let bytes = zstd::decode_all(blob).context("decompressing an occurrence chunk")?;
    let mut out = Vec::new();
    let mut reader = Wire::new(&bytes);
    while let Some((field, value)) = reader.next()? {
        if let (2, Value::Bytes(occ)) = (field, value) {
            out.push(decode_occurrence(occ)?);
        }
    }
    Ok(out)
}

fn decode_occurrence(bytes: &[u8]) -> Result<RawOccurrence> {
    let mut occ = RawOccurrence { line: 0, column: 0, roles: 0, symbol: String::new() };
    let mut legacy: Vec<i64> = Vec::new();
    let mut typed: Option<(u32, u32)> = None;
    let mut reader = Wire::new(bytes);
    while let Some((field, value)) = reader.next()? {
        match (field, value) {
            (1, Value::Bytes(packed)) => {
                let mut r = Wire::new(packed);
                while !r.done() {
                    legacy.push(r.varint()? as i64);
                }
            }
            (1, Value::Varint(v)) => legacy.push(v as i64),
            (2, Value::Bytes(s)) => occ.symbol = String::from_utf8_lossy(s).into_owned(),
            (3, Value::Varint(v)) => occ.roles = v as i32,
            // SingleLineRange { line = 1, start_character = 2, end_character = 3 } and
            // MultiLineRange { start_line = 1, start_character = 2, … }: the start is enough.
            (8 | 9, Value::Bytes(range)) => {
                let (mut line, mut column) = (0u32, 0u32);
                let mut r = Wire::new(range);
                while let Some((f, v)) = r.next()? {
                    match (f, v) {
                        (1, Value::Varint(x)) => line = x as u32,
                        (2, Value::Varint(x)) => column = x as u32,
                        _ => {}
                    }
                }
                typed = Some((line, column));
            }
            _ => {}
        }
    }
    let (line, column) = typed.unwrap_or_else(|| (legacy.first().copied().unwrap_or(0) as u32, legacy.get(1).copied().unwrap_or(0) as u32));
    occ.line = line;
    occ.column = column;
    Ok(occ)
}

/// Every `(symbol, relationship)` of a `.scip` index: `Index.documents[].symbols[]` and
/// `Index.external_symbols[]`.
pub fn relationships(index: &[u8]) -> Result<Vec<(String, Relationship)>> {
    let mut out = Vec::new();
    let mut reader = Wire::new(index);
    while let Some((field, value)) = reader.next()? {
        match (field, value) {
            (2, Value::Bytes(document)) => {
                let mut r = Wire::new(document);
                while let Some((f, v)) = r.next()? {
                    if let (3, Value::Bytes(info)) = (f, v) {
                        symbol_relationships(info, &mut out)?;
                    }
                }
            }
            (3, Value::Bytes(info)) => symbol_relationships(info, &mut out)?,
            _ => {}
        }
    }
    Ok(out)
}

fn symbol_relationships(info: &[u8], out: &mut Vec<(String, Relationship)>) -> Result<()> {
    let mut symbol = String::new();
    let mut rels = Vec::new();
    let mut reader = Wire::new(info);
    while let Some((field, value)) = reader.next()? {
        match (field, value) {
            (1, Value::Bytes(s)) => symbol = String::from_utf8_lossy(s).into_owned(),
            (4, Value::Bytes(rel)) => {
                let mut relationship = Relationship::default();
                let mut r = Wire::new(rel);
                while let Some((f, v)) = r.next()? {
                    match (f, v) {
                        (1, Value::Bytes(s)) => relationship.symbol = String::from_utf8_lossy(s).into_owned(),
                        (2, Value::Varint(x)) => relationship.is_reference = x != 0,
                        (3, Value::Varint(x)) => relationship.is_implementation = x != 0,
                        (4, Value::Varint(x)) => relationship.is_type_definition = x != 0,
                        _ => {}
                    }
                }
                rels.push(relationship);
            }
            _ => {}
        }
    }
    out.extend(rels.into_iter().map(|r| (symbol.clone(), r)));
    Ok(())
}

/// Adds a minimal `SymbolInformation` for every symbol a document defines without one.
///
/// scip-python emits such definitions (on this repository's `tests/`), and `expt-convert` stops on
/// the first with "has definition occurrence, but no SymbolInformation", leaving the root without
/// an exact layer. The repair keeps every other byte of the index as it was. Returns the repaired
/// index and how many entries were added.
///
/// A document with no path gets the path `.`: scip-python pointed at a single file
/// (`--target-only setup.py`, how a Python file at a repository's root is indexed) makes the file
/// itself the project root and leaves its document's `relative_path` empty, which `expt-convert`
/// refuses ("relative path must not be empty"). `.` is that file relative to itself, and
/// [`join_normalized`] maps it, under the run's prefix `setup.py/`, back to `setup.py`.
pub fn repair_missing_symbol_information(index: &[u8]) -> Result<(Vec<u8>, usize)> {
    let mut out = Vec::with_capacity(index.len() + 1024);
    let mut added = 0;
    let mut reader = Wire::new(index);
    while let Some((field, value, raw)) = reader.next_raw()? {
        match (field, value) {
            (2, Value::Bytes(document)) => {
                let (fixed, n) = repair_document(document)?;
                added += n;
                put_len_field(&mut out, 2, &fixed);
            }
            _ => out.extend_from_slice(raw),
        }
    }
    Ok((out, added))
}

fn repair_document(document: &[u8]) -> Result<(Vec<u8>, usize)> {
    let mut out = Vec::with_capacity(document.len() + 64);
    let mut defined: Vec<String> = Vec::new();
    let mut described: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut has_path = false;
    let mut reader = Wire::new(document);
    while let Some((field, value, raw)) = reader.next_raw()? {
        if let (1, Value::Bytes(path)) = (field, &value) {
            if path.is_empty() {
                continue; // an explicit empty path: replaced below
            }
            has_path = true;
        }
        out.extend_from_slice(raw);
        match (field, value) {
            (2, Value::Bytes(occurrence)) => {
                let occ = decode_occurrence(occurrence)?;
                if occ.roles & ROLE_DEFINITION != 0 && !occ.symbol.is_empty() && !occ.symbol.starts_with("local ") {
                    defined.push(occ.symbol);
                }
            }
            (3, Value::Bytes(info)) => {
                let mut r = Wire::new(info);
                while let Some((f, v)) = r.next()? {
                    if let (1, Value::Bytes(symbol)) = (f, v) {
                        described.insert(String::from_utf8_lossy(symbol).into_owned());
                    }
                }
            }
            _ => {}
        }
    }
    if !has_path {
        put_len_field(&mut out, 1, b".");
    }
    let mut added = 0;
    for symbol in defined {
        if described.insert(symbol.clone()) {
            let mut info = Vec::new();
            put_len_field(&mut info, 1, symbol.as_bytes());
            put_len_field(&mut out, 3, &info);
            added += 1;
        }
    }
    Ok((out, added))
}

fn put_varint(out: &mut Vec<u8>, mut value: u64) {
    loop {
        let byte = (value & 0x7f) as u8;
        value >>= 7;
        if value == 0 {
            out.push(byte);
            return;
        }
        out.push(byte | 0x80);
    }
}

fn put_len_field(out: &mut Vec<u8>, field: u32, bytes: &[u8]) {
    put_varint(out, (u64::from(field) << 3) | 2);
    put_varint(out, bytes.len() as u64);
    out.extend_from_slice(bytes);
}

enum Value<'a> {
    Varint(u64),
    Bytes(&'a [u8]),
    Other,
}

struct Wire<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl<'a> Wire<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Wire { bytes, pos: 0 }
    }

    fn done(&self) -> bool {
        self.pos >= self.bytes.len()
    }

    fn varint(&mut self) -> Result<u64> {
        let mut value = 0u64;
        for shift in (0..64).step_by(7) {
            let Some(&byte) = self.bytes.get(self.pos) else { bail!("truncated varint") };
            self.pos += 1;
            value |= u64::from(byte & 0x7f) << shift;
            if byte & 0x80 == 0 {
                return Ok(value);
            }
        }
        bail!("varint too long")
    }

    /// Like [`Wire::next`], with the field's raw bytes (key and value) for copying it unchanged.
    fn next_raw(&mut self) -> Result<Option<(u32, Value<'a>, &'a [u8])>> {
        let start = self.pos;
        let bytes = self.bytes;
        Ok(self.next()?.map(|(field, value)| (field, value, &bytes[start..self.pos])))
    }

    fn next(&mut self) -> Result<Option<(u32, Value<'a>)>> {
        if self.done() {
            return Ok(None);
        }
        let key = self.varint()?;
        let (field, wire_type) = ((key >> 3) as u32, key & 7);
        let value = match wire_type {
            0 => Value::Varint(self.varint()?),
            1 => {
                self.pos += 8;
                Value::Other
            }
            2 => {
                let len = self.varint()? as usize;
                let end = self.pos.checked_add(len).filter(|e| *e <= self.bytes.len()).context("truncated field")?;
                let slice = &self.bytes[self.pos..end];
                self.pos = end;
                Value::Bytes(slice)
            }
            5 => {
                self.pos += 4;
                Value::Other
            }
            other => bail!("unsupported wire type {other}"),
        };
        if self.pos > self.bytes.len() {
            bail!("truncated message");
        }
        Ok(Some((field, value)))
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn documents_outside_the_root_map_back_into_the_repository() {
        assert_eq!(super::join_normalized("tests/", "../dreamference/a.py"), "dreamference/a.py");
        assert_eq!(super::join_normalized("tests/", "test_a.py"), "tests/test_a.py");
        assert_eq!(super::join_normalized("", "./src/lib.rs"), "src/lib.rs");
        // A single-file root: the file itself, and what it imports from beside it.
        assert_eq!(super::join_normalized("fano/main.py/", "."), "fano/main.py");
        assert_eq!(super::join_normalized("fano/main.py/", "../make_figures.py"), "fano/make_figures.py");
    }

    /// The `relative_path` fields of an index's documents, in order.
    fn document_paths(index: &[u8]) -> Vec<String> {
        let mut out = Vec::new();
        let mut reader = super::Wire::new(index);
        while let Some((field, value)) = reader.next().unwrap() {
            if let (2, super::Value::Bytes(document)) = (field, value) {
                let mut path = None;
                let mut r = super::Wire::new(document);
                while let Some((f, v)) = r.next().unwrap() {
                    if let (1, super::Value::Bytes(p)) = (f, v) {
                        path = Some(String::from_utf8_lossy(p).into_owned());
                    }
                }
                out.push(path.unwrap_or_else(|| "<none>".into()));
            }
        }
        out
    }

    #[test]
    fn a_document_without_a_path_is_the_file_itself() {
        let mut index = Vec::new();
        // No path at all (what scip-python writes for `--target-only <file>`), an explicit empty
        // one, and a document that has its path.
        let mut language_only = Vec::new();
        super::put_len_field(&mut language_only, 4, b"python");
        super::put_len_field(&mut index, 2, &language_only);
        let mut empty = Vec::new();
        super::put_len_field(&mut empty, 1, b"");
        super::put_len_field(&mut index, 2, &empty);
        let mut named = Vec::new();
        super::put_len_field(&mut named, 1, b"../pkg/__init__.py");
        super::put_len_field(&mut index, 2, &named);
        let (fixed, added) = super::repair_missing_symbol_information(&index).unwrap();
        assert_eq!(added, 0);
        assert_eq!(document_paths(&fixed), [".", ".", "../pkg/__init__.py"]);
        // An index that needs nothing is returned byte for byte.
        let mut whole = Vec::new();
        super::put_len_field(&mut whole, 2, &named);
        assert_eq!(super::repair_missing_symbol_information(&whole).unwrap().0, whole);
    }
}
