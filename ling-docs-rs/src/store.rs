//! A collection's database (spec §6, §7.4): `docs/<collection>.db`, SQLite, mode 600.
//!
//! - `documents`: one row per file, with its size, modification time and SHA-256 (the change
//!   scan's keys), its status (`pending`, `ok`, `duplicate`, `failed: …`, `skipped: …`), the
//!   pages without a text layer that OCR has not read, and what OCR did (`ocr`, JSON, §20: empty
//!   until OCR has run on the document, so a newly installed OCR finds what is left to read);
//! - `units`: the extracted text by page or section, which `read` serves;
//! - `chunks`: the 512-token chunks with their locators and, once embedded, a float32 vector;
//! - `chunks_fts` (`porter unicode61`) and `chunks_cjk` (`trigram`, only for chunks with Chinese
//!   or Japanese: `unicode61` makes a run of them one token, §15.5), both contentless, keyed by
//!   the chunk's id.
//!
//! A chunk's vector is kept for as long as what is embedded for it (its title, heading and text)
//! is unchanged: writing a document again reuses the vectors of chunks with the same text hash
//! (§7.5). The schema is migrated in place ([`MIGRATIONS`]), never by embedding again.
//!
//! The journal is SQLite's default rollback journal, not WAL: queries run in the agent's
//! read-only sandbox, and a WAL reader needs to write the `-shm` file.

use std::collections::HashMap;
use std::path::Path;

use anyhow::{Context, Result};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};

use crate::chunk::Chunk;
use crate::extract::Unit;

/// Migrations of the stored metadata, in order: `MIGRATIONS[i]` takes a database from schema
/// `i + 1` to `i + 2`, in place and in one transaction. They never touch `chunks.vector`: a new
/// schema is not a reason to embed again (§7.5). None yet: schema 1 is the first.
pub const MIGRATIONS: &[&str] = &[];

/// The schema this build writes: the first, plus one per migration.
pub const SCHEMA_VERSION: i64 = 1 + MIGRATIONS.len() as i64;

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
    indexed_at INTEGER,
    ocr TEXT NOT NULL DEFAULT ''
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
    } else if version > SCHEMA_VERSION {
        anyhow::bail!("{} has schema {version}, written by a newer ling-docs than this one (schema {SCHEMA_VERSION}); it is left as it is", path.display());
    } else if version < SCHEMA_VERSION {
        migrate(&conn, version, MIGRATIONS)?;
    }
    // Added by the OCR phase (§20) to databases written before it: their documents read as not
    // yet seen by OCR.
    if !has_ocr_column(&conn) {
        conn.execute_batch("ALTER TABLE documents ADD COLUMN ocr TEXT NOT NULL DEFAULT ''")?;
    }
    Ok(conn)
}

/// Applies `migrations` from schema `from` on, in one transaction, and records the schema reached.
pub fn migrate(conn: &Connection, from: i64, migrations: &[&str]) -> Result<i64> {
    let to = 1 + migrations.len() as i64;
    conn.execute_batch("BEGIN IMMEDIATE")?;
    let applied = (|| -> Result<()> {
        for sql in migrations.iter().skip((from.max(1) - 1) as usize) {
            conn.execute_batch(sql)?;
        }
        conn.execute_batch(&format!("PRAGMA user_version = {to}"))?;
        Ok(())
    })();
    match applied {
        Ok(()) => conn.execute_batch("COMMIT")?,
        Err(e) => {
            let _ = conn.execute_batch("ROLLBACK");
            return Err(e.context(format!("migrating the index from schema {from} to {to}")));
        }
    }
    Ok(to)
}

/// Whether `documents` has the `ocr` column (a database from before §20 opened read-only does not).
pub fn has_ocr_column(conn: &Connection) -> bool {
    conn.prepare("SELECT ocr FROM documents LIMIT 0").is_ok()
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
    clear_chunks(conn, id)?;
    conn.execute("DELETE FROM units WHERE doc_id = ?", [id])?;
    Ok(())
}

/// Removes a document's chunks and their search rows.
fn clear_chunks(conn: &Connection, id: i64) -> Result<()> {
    let ids: Vec<i64> = conn.prepare("SELECT id FROM chunks WHERE doc_id = ?")?.query_map([id], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    let mut fts = conn.prepare("DELETE FROM chunks_fts WHERE rowid = ?")?;
    let mut cjk = conn.prepare("DELETE FROM chunks_cjk WHERE rowid = ?")?;
    for chunk in ids {
        fts.execute([chunk])?;
        cjk.execute([chunk])?;
    }
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", [id])?;
    Ok(())
}

/// A document's embedded chunks' vectors by text hash ([`crate::chunk::text_hash`]), before
/// its chunks are written again.
fn vectors_by_text(conn: &Connection, id: i64) -> Result<HashMap<[u8; 32], Vec<u8>>> {
    let mut stmt = conn.prepare("SELECT d.title, c.heading, c.text, c.vector FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.doc_id = ? AND c.vector IS NOT NULL")?;
    let rows = stmt.query_map([id], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, Vec<u8>>(3)?)))?;
    let mut out = HashMap::new();
    for row in rows {
        let (title, heading, text, vector) = row?;
        out.insert(crate::chunk::text_hash(&title, &heading, &text), vector);
    }
    Ok(out)
}

/// Writes a document's chunks and their search rows; a chunk whose text hash is in `kept` takes
/// that vector, the others wait for the embedder. Returns how many vectors were kept.
fn insert_chunks(tx: &Connection, id: i64, title: &str, chunks: &[Chunk], kept: &HashMap<[u8; 32], Vec<u8>>) -> Result<usize> {
    let mut insert_chunk = tx.prepare("INSERT INTO chunks(doc_id, ord, loc, page_first, page_last, line_first, line_last, heading, text, vector) VALUES (?,?,?,?,?,?,?,?,?,?)")?;
    let mut insert_fts = tx.prepare("INSERT INTO chunks_fts(rowid, title, heading, text) VALUES (?,?,?,?)")?;
    let mut insert_cjk = tx.prepare("INSERT INTO chunks_cjk(rowid, title, heading, text) VALUES (?,?,?,?)")?;
    let mut reused = 0;
    for (ord, c) in chunks.iter().enumerate() {
        let vector = kept.get(&crate::chunk::text_hash(title, &c.heading, &c.text));
        reused += usize::from(vector.is_some());
        insert_chunk.execute(params![id, ord as i64, c.loc, c.page_first, c.page_last, c.line_first, c.line_last, c.heading, c.text, vector])?;
        let rowid = tx.last_insert_rowid();
        insert_fts.execute(params![rowid, title, c.heading, c.text])?;
        if title.chars().chain(c.heading.chars()).chain(c.text.chars()).any(is_cjk) {
            insert_cjk.execute(params![rowid, title, c.heading, c.text])?;
        }
    }
    Ok(reused)
}

/// Replaces a document's chunks (chunked again from its stored units), keeping the vectors of
/// chunks whose text is unchanged. Returns how many vectors were kept.
pub fn replace_chunks(conn: &mut Connection, id: i64, title: &str, chunks: &[Chunk]) -> Result<usize> {
    let tx = conn.transaction()?;
    let kept = vectors_by_text(&tx, id)?;
    clear_chunks(&tx, id)?;
    let reused = insert_chunks(&tx, id, title, chunks, &kept)?;
    tx.commit()?;
    Ok(reused)
}

/// Marks for reading again the documents of each kind whose extractor's recorded version differs
/// from `current` (`extract::extractor_versions`), failed ones included, then records `current`.
/// A database with no record, or no entry for a kind, takes the current versions as its own: an
/// older ling-docs wrote it, and its text is not read again for that (§7.5). Returns how many
/// documents were marked.
pub fn mark_stale_extractors(conn: &Connection, current: &std::collections::BTreeMap<String, String>) -> Result<usize> {
    let recorded: Option<std::collections::BTreeMap<String, String>> = get_meta(conn, "extractors")?.and_then(|v| serde_json::from_str(&v).ok());
    let mut marked = 0;
    if let Some(recorded) = recorded {
        for (kind, version) in current {
            if recorded.get(kind).is_some_and(|old| old != version) {
                marked += conn.execute("UPDATE documents SET status = 'pending' WHERE kind = ? AND (status = 'ok' OR status LIKE 'failed%')", [kind])?;
            }
        }
    }
    set_meta(conn, "extractors", &serde_json::to_string(current)?)?;
    Ok(marked)
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
    // The vectors of chunks whose text comes back unchanged are kept (§7.5).
    let kept = vectors_by_text(&tx, id)?;
    clear_document(&tx, id)?;
    {
        let mut insert_unit = tx.prepare("INSERT INTO units(doc_id, ord, loc, page, line_first, line_last, heading, text) VALUES (?,?,?,?,?,?,?,?)")?;
        for (ord, u) in units.iter().enumerate() {
            insert_unit.execute(params![id, ord as i64, u.loc, u.page, u.line_first, u.line_last, u.heading, u.text])?;
        }
    }
    insert_chunks(&tx, id, title, chunks, &kept)?;
    let ocr = needs_ocr.iter().map(|p| p.to_string()).collect::<Vec<_>>().join(",");
    // Freshly extracted: OCR has not seen this version of the file.
    tx.execute("UPDATE documents SET title = ?, pages = ?, needs_ocr = ?, status = 'ok', indexed_at = ?, ocr = '' WHERE id = ?", params![title, pages, ocr, now, id])?;
    tx.commit()?;
    Ok(())
}

/// A document's units and chunks after OCR (§20): the text layer's units with the read pages'
/// units among them, the pages still without text, and the record of what OCR did.
pub fn write_ocr(conn: &mut Connection, id: i64, title: &str, units: &[Unit], chunks: &[Chunk], needs_ocr: &[u32], record: &str) -> Result<()> {
    let pages: Option<u32> = conn.query_row("SELECT pages FROM documents WHERE id = ?", [id], |r| r.get(0)).ok().flatten();
    let indexed: Option<i64> = conn.query_row("SELECT indexed_at FROM documents WHERE id = ?", [id], |r| r.get(0)).ok().flatten();
    write_document(conn, id, title, pages, needs_ocr, units, chunks, indexed.unwrap_or(0))?;
    conn.execute("UPDATE documents SET ocr = ? WHERE id = ?", params![record, id])?;
    Ok(())
}

/// A document's stored units, in order (what `read` serves; OCR merges into them).
pub fn units(conn: &Connection, id: i64) -> Result<Vec<Unit>> {
    let mut stmt = conn.prepare("SELECT loc, page, line_first, line_last, heading, text FROM units WHERE doc_id = ? ORDER BY ord")?;
    let rows = stmt.query_map([id], |r| Ok(Unit { loc: r.get(0)?, page: r.get(1)?, line_first: r.get(2)?, line_last: r.get(3)?, heading: r.get(4)?, group: None, text: r.get(5)? }))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// Documents with pages OCR has not read and no record of OCR yet: id, path, kind, the pages.
pub fn awaiting_ocr(conn: &Connection) -> Result<Vec<(i64, String, String, Vec<u32>)>> {
    if !has_ocr_column(conn) {
        return Ok(Vec::new());
    }
    let mut stmt = conn.prepare("SELECT id, path, kind, needs_ocr FROM documents WHERE status = 'ok' AND needs_ocr != '' AND ocr = '' ORDER BY id")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, String>(3)?)))?;
    Ok(rows.filter_map(|r| r.ok()).map(|(id, path, kind, pages)| (id, path, kind, pages.split(',').filter_map(|p| p.parse().ok()).collect())).collect())
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
    /// Pages OCR read, and how many of them below `docs_ocr_send_below` (§20).
    pub ocr_pages: i64,
    pub ocr_low_pages: i64,
    /// Documents OCR could not read, and documents whose OCR is still to come.
    pub ocr_failed_documents: i64,
    pub ocr_waiting_documents: i64,
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
        ..ocr_counts(conn)?
    })
}

/// The OCR part of [`Counts`], from each document's `ocr` record.
fn ocr_counts(conn: &Connection) -> Result<Counts> {
    let mut out = Counts::default();
    if !has_ocr_column(conn) {
        return Ok(out);
    }
    let rows: Vec<(String, String)> = conn.prepare("SELECT ocr, needs_ocr FROM documents WHERE status = 'ok'")?.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
    for (record, needs) in rows {
        if record.is_empty() {
            out.ocr_waiting_documents += i64::from(!needs.is_empty());
            continue;
        }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&record) else { continue };
        out.ocr_pages += value["read"].as_i64().unwrap_or(0);
        out.ocr_low_pages += value["low"].as_i64().unwrap_or(0);
        out.ocr_failed_documents += i64::from(value["failed"].is_string());
    }
    Ok(out)
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
        let unit = Unit { loc: "lines 1-1".into(), page: None, line_first: Some(1), line_last: Some(1), heading: String::new(), group: None, text: "咖啡 coffee beans".into() };
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

    fn unit(text: &str) -> Unit {
        Unit { loc: "lines 1-1".into(), page: None, line_first: Some(1), line_last: Some(1), heading: String::new(), group: None, text: text.into() }
    }

    fn chunk_of(text: &str) -> Chunk {
        Chunk { loc: "lines 1-1".into(), page_first: None, page_last: None, line_first: Some(1), line_last: Some(1), heading: String::new(), text: text.into() }
    }

    /// Every chunk's text and whether it has a vector, in order.
    fn embedded(conn: &Connection) -> Vec<(String, bool)> {
        conn.prepare("SELECT text, vector IS NOT NULL FROM chunks ORDER BY doc_id, ord").unwrap().query_map([], |r| Ok((r.get(0)?, r.get(1)?))).unwrap().map(|r| r.unwrap()).collect()
    }

    #[test]
    fn a_document_written_again_keeps_the_vectors_of_its_unchanged_chunks() {
        let dir = tempfile::tempdir().unwrap();
        let mut conn = open_rw(&dir.path().join("c.db")).unwrap();
        conn.execute("INSERT INTO documents(path, kind, size, mtime_ns, status) VALUES ('a.md', 'markdown', 1, 1, 'pending')", []).unwrap();
        let id = conn.last_insert_rowid();
        let texts = ["alpha one", "beta two", "gamma three"];
        write_document(&mut conn, id, "a", None, &[], &[unit("x")], &texts.map(chunk_of), 0).unwrap();
        conn.execute("UPDATE chunks SET vector = X'0000803F'", []).unwrap();
        // Read again (a new extractor, OCR): one chunk's text changed, one is new.
        let again = [chunk_of("alpha one"), chunk_of("beta TWO"), chunk_of("gamma three"), chunk_of("delta four")];
        write_document(&mut conn, id, "a", None, &[], &[unit("x")], &again, 0).unwrap();
        assert_eq!(embedded(&conn), vec![("alpha one".into(), true), ("beta TWO".into(), false), ("gamma three".into(), true), ("delta four".into(), false)]);
        // A new title is embedded with every chunk, so all of them change.
        write_document(&mut conn, id, "b", None, &[], &[unit("x")], &again, 0).unwrap();
        assert!(embedded(&conn).iter().all(|(_, has)| !has));
        // Chunked again from the same text: nothing to embed.
        conn.execute("UPDATE chunks SET vector = X'0000803F'", []).unwrap();
        assert_eq!(replace_chunks(&mut conn, id, "b", &again).unwrap(), 4);
        assert!(embedded(&conn).iter().all(|(_, has)| *has));
        assert_eq!(conn.query_row("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'delta'", [], |r| r.get::<_, i64>(0)).unwrap(), 1, "search rows follow");
    }

    #[test]
    fn an_older_schema_is_migrated_in_place_with_its_vectors() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("c.db");
        let mut conn = open_rw(&path).unwrap();
        conn.execute("INSERT INTO documents(path, kind, size, mtime_ns, status) VALUES ('a.md', 'markdown', 1, 1, 'pending')", []).unwrap();
        let id = conn.last_insert_rowid();
        write_document(&mut conn, id, "a", None, &[], &[unit("x")], &[chunk_of("alpha")], 0).unwrap();
        conn.execute("UPDATE chunks SET vector = X'0000803F'", []).unwrap();
        let migrations = ["ALTER TABLE documents ADD COLUMN later TEXT NOT NULL DEFAULT ''", "CREATE TABLE extra(x)"];
        assert_eq!(migrate(&conn, 1, &migrations).unwrap(), 3);
        assert_eq!(conn.query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0)).unwrap(), 3);
        assert_eq!(embedded(&conn), vec![("alpha".into(), true)]);
        assert!(conn.prepare("SELECT later FROM documents").is_ok());
        // From the middle: only the rest runs.
        conn.execute_batch("PRAGMA user_version = 2; DROP TABLE extra;").unwrap();
        assert_eq!(migrate(&conn, 2, &migrations).unwrap(), 3);
        assert!(conn.prepare("SELECT x FROM extra").is_ok());
        // A migration that fails leaves the database as it was.
        conn.execute_batch("PRAGMA user_version = 2").unwrap();
        assert!(migrate(&conn, 2, &["CREATE TABLE fine(y)", "NOT SQL"]).is_err());
        assert_eq!(conn.query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0)).unwrap(), 2);
        assert!(conn.prepare("SELECT y FROM fine").is_err());
        // A newer database than this build knows is left alone, not rebuilt.
        conn.execute_batch(&format!("PRAGMA user_version = {}", SCHEMA_VERSION + 1)).unwrap();
        drop(conn);
        assert!(open_rw(&path).unwrap_err().to_string().contains("newer ling-docs"));
    }

    #[test]
    fn only_kinds_whose_extractor_changed_are_read_again() {
        let dir = tempfile::tempdir().unwrap();
        let conn = open_rw(&dir.path().join("c.db")).unwrap();
        for (path, kind, status) in [("a.md", "markdown", "ok"), ("b.pdf", "pdf", "ok"), ("c.pdf", "pdf", "failed: damaged"), ("d.txt", "text", "ok")] {
            conn.execute("INSERT INTO documents(path, kind, size, mtime_ns, status) VALUES (?, ?, 1, 1, ?)", [path, kind, status]).unwrap();
        }
        let status = |path: &str| conn.query_row("SELECT status FROM documents WHERE path = ?", [path], |r| r.get::<_, String>(0)).unwrap();
        let versions = |pdf: &str| -> std::collections::BTreeMap<String, String> { [("markdown", "1"), ("pdf", pdf), ("text", "1")].iter().map(|(k, v)| (k.to_string(), v.to_string())).collect() };
        // No record (an older ling-docs wrote it): the current versions are taken as its own.
        assert_eq!(mark_stale_extractors(&conn, &versions("1")).unwrap(), 0);
        assert_eq!(mark_stale_extractors(&conn, &versions("1")).unwrap(), 0, "the same versions: nothing");
        assert_eq!(mark_stale_extractors(&conn, &versions("2")).unwrap(), 2, "the PDFs, the failed one too");
        assert_eq!((status("a.md"), status("b.pdf"), status("c.pdf"), status("d.txt")), ("ok".into(), "pending".into(), "pending".into(), "ok".into()));
    }
}
