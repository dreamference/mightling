//! A collection's database (spec §6, §7.4): `docs/<collection>.db`, SQLite, mode 600.
//!
//! - `documents`: one row per file, with its size, modification time and SHA-256 (the change
//!   scan's keys), its status (`pending`, `ok`, `duplicate`, `failed: …`, `skipped: …`) and the
//!   PDF pages that need OCR;
//! - `units`: the extracted text by page or section, which `read` serves;
//! - `chunks`: the 512-token chunks with their locators and, once embedded, a float32 vector;
//! - `chunks_fts` (`porter unicode61`) and `chunks_cjk` (`trigram`, only for chunks with Chinese
//!   or Japanese: `unicode61` makes a run of them one token, §15.5), both contentless, keyed by
//!   the chunk's id.
//!
//! The journal is SQLite's default rollback journal, not WAL: queries run in the agent's
//! read-only sandbox, and a WAL reader needs to write the `-shm` file.

use std::collections::HashMap;
use std::path::Path;

use anyhow::{Context, Result};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};

use crate::chunk::Chunk;
use crate::extract::Unit;

pub const SCHEMA_VERSION: i64 = 1;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT,
    title TEXT NOT NULL DEFAULT '',
    pages INTEGER,
    status TEXT NOT NULL,
    needs_ocr TEXT NOT NULL DEFAULT '',
    dup_of INTEGER,
    indexed_at INTEGER
);
CREATE INDEX IF NOT EXISTS documents_sha ON documents(sha256);
CREATE TABLE IF NOT EXISTS units(
    doc_id INTEGER NOT NULL,
    ord INTEGER NOT NULL,
    loc TEXT NOT NULL,
    page INTEGER,
    line_first INTEGER,
    line_last INTEGER,
    heading TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY(doc_id, ord)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL,
    ord INTEGER NOT NULL,
    loc TEXT NOT NULL,
    page_first INTEGER,
    page_last INTEGER,
    line_first INTEGER,
    line_last INTEGER,
    heading TEXT NOT NULL,
    text TEXT NOT NULL,
    vector BLOB
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id, ord);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(title, heading, text, content='', contentless_delete=1, tokenize='porter unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_cjk USING fts5(title, heading, text, content='', contentless_delete=1, tokenize='trigram');
";

/// Opens (and creates) a database for writing: outside the sandbox only.
pub fn open_rw(path: &Path) -> Result<Connection> {
    if let Some(dir) = path.parent() {
        create_private_dir(dir)?;
    }
    let fresh = !path.exists();
    let conn = Connection::open(path).with_context(|| format!("opening {}", path.display()))?;
    if fresh {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    conn.busy_timeout(std::time::Duration::from_secs(30))?;
    conn.execute_batch("PRAGMA journal_mode=DELETE; PRAGMA synchronous=NORMAL;")?;
    conn.execute_batch(SCHEMA)?;
    let version: i64 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
    if version == 0 {
        conn.execute_batch(&format!("PRAGMA user_version = {SCHEMA_VERSION}"))?;
    } else if version != SCHEMA_VERSION {
        anyhow::bail!("{} has schema {version}, this ling-docs knows {SCHEMA_VERSION}: `ling docs reindex` rebuilds it", path.display());
    }
    Ok(conn)
}

/// Opens a database read-only, as queries do. None when it does not exist yet.
pub fn open_ro(path: &Path) -> Result<Option<Connection>> {
    if !path.is_file() {
        return Ok(None);
    }
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .with_context(|| format!("opening {}", path.display()))?;
    conn.busy_timeout(std::time::Duration::from_secs(5))?;
    Ok(Some(conn))
}

/// `mkdir -p` with mode 700 on the last component.
pub fn create_private_dir(dir: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::create_dir_all(dir)?;
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    Ok(())
}

/// Chinese and Japanese characters: what `unicode61` cannot segment (§15.5).
pub fn is_cjk(c: char) -> bool {
    matches!(c as u32, 0x3040..=0x30FF | 0x3400..=0x4DBF | 0x4E00..=0x9FFF | 0xF900..=0xFAFF)
}

pub fn get_meta(conn: &Connection, key: &str) -> Result<Option<String>> {
    Ok(conn.query_row("SELECT value FROM meta WHERE key = ?", [key], |r| r.get(0)).optional()?)
}

pub fn set_meta(conn: &Connection, key: &str, value: &str) -> Result<()> {
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", [key, value])?;
    Ok(())
}

/// A document's change-scan keys.
#[derive(Debug, Clone, PartialEq)]
pub struct DocRow {
    pub id: i64,
    pub size: i64,
    pub mtime_ns: i64,
    pub sha256: Option<String>,
    pub status: String,
    pub dup_of: Option<i64>,
}

pub fn documents(conn: &Connection) -> Result<HashMap<String, DocRow>> {
    let mut stmt = conn.prepare("SELECT path, id, size, mtime_ns, sha256, status, dup_of FROM documents")?;
    let rows = stmt.query_map([], |r| {
        Ok((r.get::<_, String>(0)?, DocRow { id: r.get(1)?, size: r.get(2)?, mtime_ns: r.get(3)?, sha256: r.get(4)?, status: r.get(5)?, dup_of: r.get(6)? }))
    })?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// Removes a document's units, chunks and their search rows (not the document).
pub fn clear_document(conn: &Connection, id: i64) -> Result<()> {
    let ids: Vec<i64> = conn.prepare("SELECT id FROM chunks WHERE doc_id = ?")?.query_map([id], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    let mut fts = conn.prepare("DELETE FROM chunks_fts WHERE rowid = ?")?;
    let mut cjk = conn.prepare("DELETE FROM chunks_cjk WHERE rowid = ?")?;
    for chunk in ids {
        fts.execute([chunk])?;
        cjk.execute([chunk])?;
    }
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", [id])?;
    conn.execute("DELETE FROM units WHERE doc_id = ?", [id])?;
    Ok(())
}

/// Removes a document entirely; its duplicates lose their original and are read again.
pub fn delete_document(conn: &Connection, id: i64) -> Result<()> {
    clear_document(conn, id)?;
    conn.execute("UPDATE documents SET dup_of = NULL, status = 'pending' WHERE dup_of = ?", [id])?;
    conn.execute("DELETE FROM documents WHERE id = ?", [id])?;
    Ok(())
}

/// Writes one document's extraction: its units and chunks (without vectors yet), its title and
/// pages, status `ok`.
pub fn write_document(conn: &mut Connection, id: i64, title: &str, pages: Option<u32>, needs_ocr: &[u32], units: &[Unit], chunks: &[Chunk], now: i64) -> Result<()> {
    let tx = conn.transaction()?;
    clear_document(&tx, id)?;
    {
        let mut insert_unit = tx.prepare("INSERT INTO units(doc_id, ord, loc, page, line_first, line_last, heading, text) VALUES (?,?,?,?,?,?,?,?)")?;
        for (ord, u) in units.iter().enumerate() {
            insert_unit.execute(params![id, ord as i64, u.loc, u.page, u.line_first, u.line_last, u.heading, u.text])?;
        }
        let mut insert_chunk = tx.prepare("INSERT INTO chunks(doc_id, ord, loc, page_first, page_last, line_first, line_last, heading, text) VALUES (?,?,?,?,?,?,?,?,?)")?;
        let mut insert_fts = tx.prepare("INSERT INTO chunks_fts(rowid, title, heading, text) VALUES (?,?,?,?)")?;
        let mut insert_cjk = tx.prepare("INSERT INTO chunks_cjk(rowid, title, heading, text) VALUES (?,?,?,?)")?;
        for (ord, c) in chunks.iter().enumerate() {
            insert_chunk.execute(params![id, ord as i64, c.loc, c.page_first, c.page_last, c.line_first, c.line_last, c.heading, c.text])?;
            let rowid = tx.last_insert_rowid();
            insert_fts.execute(params![rowid, title, c.heading, c.text])?;
            if title.chars().chain(c.heading.chars()).chain(c.text.chars()).any(is_cjk) {
                insert_cjk.execute(params![rowid, title, c.heading, c.text])?;
            }
        }
    }
    let ocr = needs_ocr.iter().map(|p| p.to_string()).collect::<Vec<_>>().join(",");
    tx.execute("UPDATE documents SET title = ?, pages = ?, needs_ocr = ?, status = 'ok', indexed_at = ? WHERE id = ?", params![title, pages, ocr, now, id])?;
    tx.commit()?;
    Ok(())
}

pub fn set_status(conn: &Connection, id: i64, status: &str) -> Result<()> {
    conn.execute("UPDATE documents SET status = ? WHERE id = ?", params![status, id])?;
    Ok(())
}

/// Counts for `status` (§8.3).
#[derive(Debug, Default, Clone, serde::Serialize)]
pub struct Counts {
    pub documents: i64,
    pub indexed: i64,
    pub pending: i64,
    pub failed: i64,
    pub duplicates: i64,
    pub skipped: i64,
    pub chunks: i64,
    pub embedded: i64,
    pub needs_ocr_pages: i64,
    pub needs_ocr_documents: i64,
}

pub fn counts(conn: &Connection) -> Result<Counts> {
    let one = |sql: &str| -> Result<i64> { Ok(conn.query_row(sql, [], |r| r.get(0))?) };
    let ocr: Vec<String> = conn.prepare("SELECT needs_ocr FROM documents WHERE needs_ocr != ''")?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    Ok(Counts {
        documents: one("SELECT count(*) FROM documents")?,
        indexed: one("SELECT count(*) FROM documents WHERE status = 'ok'")?,
        pending: one("SELECT count(*) FROM documents WHERE status = 'pending'")?,
        failed: one("SELECT count(*) FROM documents WHERE status LIKE 'failed%'")?,
        duplicates: one("SELECT count(*) FROM documents WHERE status = 'duplicate'")?,
        skipped: one("SELECT count(*) FROM documents WHERE status LIKE 'skipped%'")?,
        chunks: one("SELECT count(*) FROM chunks")?,
        embedded: one("SELECT count(*) FROM chunks WHERE vector IS NOT NULL")?,
        needs_ocr_pages: ocr.iter().map(|s| s.split(',').count() as i64).sum(),
        needs_ocr_documents: ocr.len() as i64,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_document_round_trips_and_its_search_rows_go_with_it() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("docs/c.db");
        let mut conn = open_rw(&path).unwrap();
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(std::fs::metadata(path.parent().unwrap()).unwrap().permissions().mode() & 0o777, 0o700);
        conn.execute("INSERT INTO documents(path, kind, size, mtime_ns, status) VALUES ('a.md', 'markdown', 1, 1, 'pending')", []).unwrap();
        let id = conn.last_insert_rowid();
        let unit = Unit { loc: "lines 1-1".into(), page: None, line_first: Some(1), line_last: Some(1), heading: String::new(), text: "咖啡 coffee beans".into() };
        let chunk = Chunk { loc: "lines 1-1".into(), page_first: None, page_last: None, line_first: Some(1), line_last: Some(1), heading: String::new(), text: unit.text.clone() };
        write_document(&mut conn, id, "a", None, &[], &[unit], &[chunk], 0).unwrap();
        let n = |sql: &str| conn.query_row(sql, [], |r| r.get::<_, i64>(0)).unwrap();
        assert_eq!(n("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'bean'"), 1, "porter stems");
        assert_eq!(n("SELECT count(*) FROM chunks_cjk WHERE chunks_cjk MATCH 'coffee'"), 1);
        assert_eq!(counts(&conn).unwrap().indexed, 1);
        delete_document(&conn, id).unwrap();
        assert_eq!(n("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'bean'"), 0);
        assert_eq!(n("SELECT count(*) FROM units"), 0);
        drop(conn);
        let ro = open_ro(&path).unwrap().unwrap();
        assert!(ro.execute("DELETE FROM meta", []).is_err(), "read-only");
    }
}
