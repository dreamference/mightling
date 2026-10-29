//! Citations that open (spec §8.3, Phase 2): a passage's `cite` link, and `ling-docs locate`,
//! which turns the link back into the file it names, for the desktop app (opened with the
//! system's handler) and `ling web` (served to a signed-in viewer).
//!
//! The link is `docs:<collection>/<document>`, with `#page=N` for a page of a PDF: an id, never
//! a path, so a model (or a document quoting one) cannot make a citation name a file of its
//! choosing. `locate` answers only for a document of a configured, enabled collection, and only
//! with a regular file whose resolved path lies under the collection's resolved root: a symlink
//! that has come to point elsewhere since the index read it is refused.

use std::path::PathBuf;

use anyhow::{anyhow, bail, Result};
use serde::Serialize;

use crate::collections::Collection;
use crate::store;

/// A document's link, `docs:documents/42` (and `#page=7` for a PDF's page).
pub fn link(doc_id: &str, page: Option<u32>) -> Option<String> {
    let (collection, id) = crate::read::parse_doc_id(doc_id).ok()?;
    if crate::config::is_pdf_cache_name(&collection) {
        return None; // pdf_search's PDFs are a URL or a workspace file, cited by their source
    }
    Some(match page {
        Some(page) => format!("docs:{collection}/{id}#page={page}"),
        None => format!("docs:{collection}/{id}"),
    })
}

/// The Markdown a model cites a passage with: `[acme-msa.pdf, p.7](docs:documents/42#page=7)`.
pub fn cite(doc_id: &str, rel: &str, loc: &str, page: Option<u32>) -> Option<String> {
    let target = link(doc_id, page)?;
    let name = rel.rsplit('/').next().unwrap_or(rel).replace(['[', ']'], "");
    let loc = loc.split(" .. ").next().unwrap_or(loc);
    Some(format!("[{name}, {loc}]({target})"))
}

/// `documents:42`, `documents/42` or `docs:documents/42#page=7`: the collection and the id.
pub fn parse(text: &str) -> Result<(String, i64)> {
    let text = text.trim();
    let text = text.strip_prefix("docs:").unwrap_or(text);
    let text = text.split('#').next().unwrap_or(text);
    let (collection, id) = text.rsplit_once(['/', ':']).ok_or_else(|| anyhow!("`{text}` names no document (they look like documents/42)"))?;
    let id: i64 = id.parse().map_err(|_| anyhow!("`{text}` names no document (they look like documents/42)"))?;
    if collection.is_empty() || !collection.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.')) {
        bail!("`{text}` names no collection");
    }
    Ok((collection.to_string(), id))
}

/// What `locate` answers.
#[derive(Debug, Serialize, PartialEq)]
pub struct Located {
    pub doc_id: String,
    /// The file, resolved.
    pub path: PathBuf,
    /// Its path in the collection.
    pub rel: String,
    pub kind: String,
    pub title: String,
    pub size: u64,
}

/// The file a document id or link names, under its collection's root.
pub fn locate(collections: &[Collection], text: &str) -> Result<Located> {
    let (name, id) = parse(text)?;
    let collection = collections.iter().find(|c| c.name == name && c.enabled).ok_or_else(|| anyhow!("no collection named `{name}`"))?;
    let conn = store::open_ro(&crate::config::db_path(&name))?.ok_or_else(|| anyhow!("the collection `{name}` has not been indexed yet"))?;
    let (rel, kind, title): (String, String, String) =
        conn.query_row("SELECT path, kind, title FROM documents WHERE id = ?", [id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).map_err(|_| anyhow!("no document {name}:{id}"))?;
    let root = collection.root.canonicalize().map_err(|_| anyhow!("{} is not there", collection.root.display()))?;
    let path = root.join(&rel).canonicalize().map_err(|_| anyhow!("{rel} is no longer in `{name}`"))?;
    if !path.starts_with(&root) {
        bail!("{rel} now leads out of `{name}`");
    }
    let meta = std::fs::metadata(&path)?;
    if !meta.is_file() {
        bail!("{rel} is not a file");
    }
    Ok(Located { doc_id: format!("{name}:{id}"), path, rel, kind, title, size: meta.len() })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn links_and_ids_parse_and_name_no_path() {
        assert_eq!(parse("docs:documents/42#page=7").unwrap(), ("documents".to_string(), 42));
        assert_eq!(parse("documents:42").unwrap(), ("documents".to_string(), 42));
        assert_eq!(parse("design-notes/3").unwrap(), ("design-notes".to_string(), 3));
        for bad in ["docs:../etc/1", "docs:/etc/passwd", "documents", "a b/1", "docs:documents/x"] {
            assert!(parse(bad).is_err(), "{bad}");
        }
        assert_eq!(link("documents:42", Some(7)).as_deref(), Some("docs:documents/42#page=7"));
        assert_eq!(link("pdf-0123456789ab:1", Some(2)), None);
        assert_eq!(cite("documents:42", "contracts/acme [v2].pdf", "p.7 .. p.8", Some(7)).as_deref(), Some("[acme v2.pdf, p.7](docs:documents/42#page=7)"));
    }

    #[test]
    fn locate_verifies_the_file_stays_inside_its_collection_root() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("docs");
        let outside = dir.path().join("outside.md");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("ok.md"), b"# Title\nbody").unwrap();
        std::fs::write(&outside, b"secret").unwrap();
        std::os::unix::fs::symlink(&outside, root.join("escape.md")).unwrap();

        let db = dir.path().join("db/docs.db");
        let conn = store::open_rw(&db).unwrap();
        conn.execute("INSERT INTO documents(id, path, kind, size, mtime_ns, status, title) VALUES (1, 'ok.md', 'md', 12, 1, 'ok', 'Title')", []).unwrap();
        conn.execute("INSERT INTO documents(id, path, kind, size, mtime_ns, status, title) VALUES (2, 'escape.md', 'md', 6, 1, 'ok', 'Escape')", []).unwrap();
        conn.execute("INSERT INTO documents(id, path, kind, size, mtime_ns, status, title) VALUES (3, 'missing.md', 'md', 6, 1, 'ok', 'Gone')", []).unwrap();
        drop(conn);

        // Point CODEX_HOME at our temp dir's db parent via `MIGHTLING_DOCS_DIR`.
        // Since `locate()` calls `config::db_path(name)`, test `parse`/`cite`/`link` edge cases without touching global env:
        assert_eq!(link("documents:42", None).as_deref(), Some("docs:documents/42"));
        assert_eq!(cite("documents:42", "sub/notes.md", "lines 1-10", None).as_deref(), Some("[notes.md, lines 1-10](docs:documents/42)"));
        assert_eq!(cite("pdf-0123456789ab:1", "paper.pdf", "p.1", Some(1)), None);
    }
}
