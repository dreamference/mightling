//! Hybrid search (spec §7.4): BM25 over two FTS5 tables plus exact dense similarity, merged by
//! weighted reciprocal-rank fusion.
//!
//! - Keyword: the query's words (English stop words dropped) against `chunks_fts`
//!   (`porter unicode61`), title weighted 2; its Chinese and Japanese runs as 3-character grams
//!   against `chunks_cjk` (`trigram`); the two rankings fused by plain RRF (§15.5).
//! - Meaning: the query embedded with `query: ` and compared by dot product with every chunk's
//!   unit vector (exact; 5 ms for 50k chunks of 768-d, §15.6).
//! - Fusion: RRF with k = 60, BM25 at `docs_bm25_weight` (0.25) of the dense ranking's weight.
//!
//! Every collection is searched (§14.2) unless the caller names one; lists from several
//! collections are merged by score before the fusion, so a collection's size does not decide its
//! share. Adjacent chunks of one document are merged in the results.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use anyhow::Result;
use rusqlite::Connection;
use serde::Serialize;

use crate::collections::Collection;
use crate::embed::{from_blob, Embed, QUERY_PREFIX};
use crate::store::{self, is_cjk};

/// RRF's k.
pub const RRF_K: f64 = 60.0;
/// How deep each ranking is read.
pub const DEPTH: usize = 100;
/// Results by default (§7.4).
pub const DEFAULT_K: usize = 8;

const STOP: &str = "a an the of to in on for and or is are was were be been being by with as at from that this these \
those it its what which who whom whose when where why how did does do done can could would should will shall may \
might must about into than then there their they them he she his her we our you your i me my not no yes if but so \
such any all some each per via vs also only more most less least very much many one two";

/// A query's words in any script (English stop words and one-letter words dropped), and its
/// Chinese and Japanese runs apart.
pub fn query_terms(query: &str) -> (Vec<String>, Vec<String>) {
    let stop: HashSet<&str> = STOP.split_whitespace().collect();
    let mut runs = Vec::new();
    let mut run = String::new();
    let mut rest = String::new();
    for c in query.chars() {
        if is_cjk(c) {
            run.push(c);
            rest.push(' ');
        } else {
            if !run.is_empty() {
                runs.push(std::mem::take(&mut run));
            }
            rest.push(c);
        }
    }
    if !run.is_empty() {
        runs.push(run);
    }
    let lower = rest.to_lowercase();
    let mut words = Vec::new();
    for word in lower.split(|c: char| !c.is_alphanumeric()) {
        if word.chars().count() > 1 && !stop.contains(word) && !words.iter().any(|w| w == word) {
            words.push(word.to_string());
        }
    }
    (words, runs)
}

/// The trigrams of the CJK runs.
fn grams(runs: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for run in runs {
        let chars: Vec<char> = run.chars().collect();
        for window in chars.windows(3) {
            let gram: String = window.iter().collect();
            if !out.contains(&gram) {
                out.push(gram);
            }
        }
    }
    out.sort();
    out
}

/// Plain or weighted RRF over rankings of keys, best first.
pub fn rrf<K: Clone + Eq + std::hash::Hash>(rankings: &[(&[K], f64)], top: usize) -> Vec<(K, f64)> {
    let mut score: HashMap<K, f64> = HashMap::new();
    let mut order: Vec<K> = Vec::new();
    for (ranking, weight) in rankings {
        for (rank, key) in ranking.iter().enumerate() {
            let entry = score.entry(key.clone()).or_insert_with(|| {
                order.push(key.clone());
                0.0
            });
            *entry += weight / (RRF_K + rank as f64 + 1.0);
        }
    }
    let mut out: Vec<(K, f64)> = order.into_iter().map(|k| { let s = score[&k]; (k, s) }).collect();
    out.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    out.truncate(top);
    out
}

/// Optional filters (§7.4).
#[derive(Debug, Clone, Default)]
pub struct Filters {
    pub collection: Option<String>,
    /// A glob over the document's path in its collection.
    pub path_glob: Option<String>,
    /// A file kind (`pdf`, `markdown`, …) or extension.
    pub kind: Option<String>,
    /// Documents modified at or after this time, Unix seconds.
    pub modified_after: Option<i64>,
}

/// A chunk, named across collections.
type Key = (usize, i64);

/// One result.
#[derive(Debug, Clone, Serialize)]
pub struct Hit {
    pub collection: String,
    /// `collection:document`, what `read` takes.
    pub doc_id: String,
    pub path: String,
    pub rel: String,
    pub loc: String,
    pub page: Option<u32>,
    pub line_first: Option<u32>,
    pub line_last: Option<u32>,
    pub score: f64,
    pub snippet: String,
    pub text: String,
    #[serde(skip)]
    pub chunk_ids: Vec<i64>,
}

/// What a search returns.
#[derive(Debug, Default, Serialize)]
pub struct Answer {
    pub hits: Vec<Hit>,
    /// Why the answer may be incomplete: no vectors yet, an index being built, a missing model.
    pub notes: Vec<String>,
}

/// A collection opened for searching, with its vectors loaded on first use.
pub struct Opened {
    pub collection: Collection,
    pub conn: Connection,
    vectors: Option<Vec<(i64, Vec<f32>)>>,
    stamp: (u64, i64),
}

fn stamp(path: &std::path::Path) -> (u64, i64) {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata(path).map(|m| (m.size(), m.mtime_nsec() + m.mtime() * 1_000_000_000)).unwrap_or((0, 0))
}

/// The collections to search, kept open between queries (the MCP server keeps one).
pub struct Searcher {
    opened: Vec<Opened>,
    /// Adjacent chunks of one document become one result (on by default; the evaluation turns it
    /// off to count chunks as Phase 0 did).
    pub merge_adjacent: bool,
}

impl Default for Searcher {
    fn default() -> Self {
        Searcher { opened: Vec::new(), merge_adjacent: true }
    }
}

impl Searcher {
    /// Opens the databases of `collections` that exist; a database rewritten since it was opened
    /// is reopened.
    pub fn refresh(&mut self, collections: &[Collection]) -> Result<()> {
        let mut kept = Vec::new();
        for collection in collections {
            let path = crate::config::db_path(&collection.name);
            let now = stamp(&path);
            if let Some(index) = self.opened.iter().position(|o| o.collection.name == collection.name && o.stamp == now) {
                let mut opened = self.opened.swap_remove(index);
                opened.collection = collection.clone();
                kept.push(opened);
                continue;
            }
            if let Some(conn) = store::open_ro(&path)? {
                kept.push(Opened { collection: collection.clone(), conn, vectors: None, stamp: now });
            }
        }
        self.opened = kept;
        Ok(())
    }

    pub fn opened(&self) -> &[Opened] {
        &self.opened
    }

    /// Searches every opened collection (or the one the filters name).
    pub fn search(&mut self, query: &str, k: usize, filters: &Filters, embedder: Option<&dyn Embed>, bm25_weight: f64) -> Result<Answer> {
        let mut answer = Answer::default();
        let (words, runs) = query_terms(query);
        let chosen: Vec<usize> = (0..self.opened.len()).filter(|i| filters.collection.as_ref().is_none_or(|c| *c == self.opened[*i].collection.name)).collect();
        if chosen.is_empty() {
            answer.notes.push(match &filters.collection {
                Some(name) => format!("no indexed collection named `{name}`"),
                None => "no collection has been indexed yet".to_string(),
            });
            return Ok(answer);
        }
        let allowed: HashMap<usize, Option<HashSet<i64>>> = chosen.iter().map(|i| Ok((*i, allowed_docs(&self.opened[*i].conn, filters)?))).collect::<Result<_>>()?;
        let depth = if filters.path_glob.is_some() || filters.kind.is_some() || filters.modified_after.is_some() { DEPTH * 10 } else { DEPTH };
        let keep = |i: usize, doc: i64| allowed[&i].as_ref().is_none_or(|set| set.contains(&doc));

        // Keyword lists, merged across collections by BM25 score (lower is better in SQLite).
        let mut lexical: Vec<(Key, f64)> = Vec::new();
        let mut trigram: Vec<(Key, f64)> = Vec::new();
        let words_query = words.iter().map(|w| format!("\"{w}\"")).collect::<Vec<_>>().join(" OR ");
        let grams_query = grams(&runs).iter().map(|g| format!("\"{}\"", g.replace('"', "\"\""))).collect::<Vec<_>>().join(" OR ");
        for &i in &chosen {
            let conn = &self.opened[i].conn;
            if !words_query.is_empty() {
                for (id, doc, score) in bm25(conn, "chunks_fts", &words_query, depth)? {
                    if keep(i, doc) {
                        lexical.push(((i, id), score));
                    }
                }
            }
            if !grams_query.is_empty() {
                for (id, doc, score) in bm25(conn, "chunks_cjk", &grams_query, depth)? {
                    if keep(i, doc) {
                        trigram.push(((i, id), score));
                    }
                }
            }
        }
        lexical.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
        trigram.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
        let lexical: Vec<Key> = lexical.into_iter().take(DEPTH).map(|(k, _)| k).collect();
        let trigram: Vec<Key> = trigram.into_iter().take(DEPTH).map(|(k, _)| k).collect();
        let keyword: Vec<Key> = match (lexical.is_empty(), trigram.is_empty()) {
            (_, true) => lexical,
            (true, false) => trigram,
            (false, false) => rrf(&[(&lexical[..], 1.0), (&trigram[..], 1.0)], DEPTH).into_iter().map(|(k, _)| k).collect(),
        };

        // Dense list, merged across collections by cosine.
        let mut dense: Vec<(Key, f32)> = Vec::new();
        match embedder {
            Some(embedder) => {
                let q = embedder.embed(&format!("{QUERY_PREFIX}{query}"))?;
                let mut missing = 0i64;
                for &i in &chosen {
                    let opened = &mut self.opened[i];
                    if opened.vectors.is_none() {
                        opened.vectors = Some(load_vectors(&opened.conn, q.len())?);
                    }
                    let docs = chunk_docs_if(&opened.conn, allowed[&i].is_some())?;
                    for (id, v) in opened.vectors.as_ref().unwrap() {
                        if let Some(doc) = docs.get(id) {
                            if !keep(i, *doc) {
                                continue;
                            }
                        }
                        let score: f32 = v.iter().zip(&q).map(|(a, b)| a * b).sum();
                        dense.push(((i, *id), score));
                    }
                    missing += opened.conn.query_row("SELECT count(*) FROM chunks WHERE vector IS NULL", [], |r| r.get::<_, i64>(0))?;
                }
                if missing > 0 {
                    answer.notes.push(format!("{missing} chunks are not embedded yet: they are found by keyword only"));
                }
            }
            None => answer.notes.push("search by meaning is unavailable (the embedding model is not installed); keyword search only".to_string()),
        }
        dense.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        let dense: Vec<Key> = dense.into_iter().take(DEPTH).map(|(k, _)| k).collect();

        let fused = rrf(&[(&dense[..], 1.0), (&keyword[..], bm25_weight)], DEPTH);
        answer.hits = self.hits(&fused, k, &words)?;
        Ok(answer)
    }

    /// Turns fused keys into results, adjacent chunks of one document merged.
    fn hits(&self, fused: &[(Key, f64)], k: usize, words: &[String]) -> Result<Vec<Hit>> {
        let mut out: Vec<(Hit, i64, i64, usize)> = Vec::new(); // hit, doc, ord range end, collection
        for ((i, id), score) in fused {
            let opened = &self.opened[*i];
            let row = opened.conn.query_row(
                "SELECT c.doc_id, c.ord, c.loc, c.page_first, c.line_first, c.line_last, c.text, d.path FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.id = ?",
                [id],
                |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?, r.get::<_, String>(2)?, r.get::<_, Option<u32>>(3)?, r.get::<_, Option<u32>>(4)?, r.get::<_, Option<u32>>(5)?, r.get::<_, String>(6)?, r.get::<_, String>(7)?)),
            );
            let Ok((doc, ord, loc, page, line_first, line_last, text, rel)) = row else { continue };
            // Adjacent to a result already taken: joined to it.
            let merge = self.merge_adjacent;
            if let Some(entry) = out.iter_mut().find(|(h, d, last, c)| merge && *c == *i && *d == doc && (ord == *last + 1 || ord + 1 == first_ord(h, *last))) {
                let (hit, _, last, _) = entry;
                if ord == *last + 1 {
                    hit.text = join_overlapping(&hit.text, &text);
                    hit.loc = span(&hit.loc, &loc);
                    hit.line_last = line_last.or(hit.line_last);
                    *last = ord;
                } else {
                    hit.text = join_overlapping(&text, &hit.text);
                    hit.loc = span(&loc, &hit.loc);
                    hit.line_first = line_first.or(hit.line_first);
                    hit.page = page.or(hit.page);
                }
                hit.chunk_ids.push(*id);
                continue;
            }
            if out.len() >= k {
                continue;
            }
            let root = &opened.collection.root;
            let hit = Hit {
                collection: opened.collection.name.clone(),
                doc_id: format!("{}:{doc}", opened.collection.name),
                path: root.join(&rel).to_string_lossy().into_owned(),
                rel,
                loc,
                page,
                line_first,
                line_last,
                score: *score,
                snippet: snippet(&text, words),
                text,
                chunk_ids: vec![*id],
            };
            out.push((hit, doc, ord, *i));
        }
        Ok(out.into_iter().map(|(h, ..)| h).collect())
    }
}

/// The ord of a hit's first chunk, given its last and how many chunks it holds.
fn first_ord(hit: &Hit, last: i64) -> i64 {
    last - hit.chunk_ids.len() as i64 + 1
}

/// `a .. b` from two locators, collapsing the inner ends.
fn span(first: &str, second: &str) -> String {
    let start = first.split(" .. ").next().unwrap_or(first);
    let end = second.rsplit(" .. ").next().unwrap_or(second);
    if start == end { start.to_string() } else { format!("{start} .. {end}") }
}

/// Joins two consecutive chunks, dropping the overlap the second repeats.
pub fn join_overlapping(first: &str, second: &str) -> String {
    let lines: Vec<&str> = second.lines().collect();
    for take in (1..=lines.len().min(60)).rev() {
        let prefix = lines[..take].join("\n");
        if prefix.len() > 20 && first.ends_with(&prefix) {
            return format!("{first}\n{}", lines[take..].join("\n"));
        }
    }
    format!("{first}\n{second}")
}

fn bm25(conn: &Connection, table: &str, query: &str, depth: usize) -> Result<Vec<(i64, i64, f64)>> {
    let sql = format!("SELECT f.rowid, c.doc_id, bm25({table}, 2.0, 1.0, 1.0) AS s FROM {table} f JOIN chunks c ON c.id = f.rowid WHERE {table} MATCH ? ORDER BY s LIMIT ?");
    let mut stmt = conn.prepare_cached(&sql)?;
    let rows = stmt.query_map(rusqlite::params![query, depth as i64], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
    Ok(rows.filter_map(|r| r.ok()).collect())
}

fn load_vectors(conn: &Connection, dim: usize) -> Result<Vec<(i64, Vec<f32>)>> {
    let mut stmt = conn.prepare("SELECT id, vector FROM chunks WHERE vector IS NOT NULL")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, Vec<u8>>(1)?)))?;
    Ok(rows.filter_map(|r| r.ok()).map(|(id, b)| (id, from_blob(&b))).filter(|(_, v)| v.len() == dim).collect())
}

fn chunk_docs_if(conn: &Connection, wanted: bool) -> Result<HashMap<i64, i64>> {
    if !wanted {
        return Ok(HashMap::new());
    }
    let mut stmt = conn.prepare("SELECT id, doc_id FROM chunks")?;
    let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
    Ok(rows.filter_map(|r| r.ok()).collect())
}

/// The documents the filters allow, or None when there are no filters.
fn allowed_docs(conn: &Connection, filters: &Filters) -> Result<Option<HashSet<i64>>> {
    if filters.path_glob.is_none() && filters.kind.is_none() && filters.modified_after.is_none() {
        return Ok(None);
    }
    let glob = match &filters.path_glob {
        Some(pattern) => Some(globset::Glob::new(pattern)?.compile_matcher()),
        None => None,
    };
    let kind = filters.kind.as_ref().map(|k| k.trim_start_matches('.').to_lowercase());
    let mut stmt = conn.prepare("SELECT id, path, kind, mtime_ns FROM documents WHERE status = 'ok'")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, i64>(3)?)))?;
    let mut set = HashSet::new();
    for row in rows.flatten() {
        let (id, path, doc_kind, mtime_ns) = row;
        if glob.as_ref().is_some_and(|g| !g.is_match(&path)) {
            continue;
        }
        if let Some(kind) = &kind {
            let by_ext = crate::discover::Kind::of(&format!("x.{kind}")).map(|k| k.as_str().to_string());
            if doc_kind != *kind && by_ext.as_deref() != Some(doc_kind.as_str()) {
                continue;
            }
        }
        if filters.modified_after.is_some_and(|t| mtime_ns / 1_000_000_000 < t) {
            continue;
        }
        set.insert(id);
    }
    Ok(Some(set))
}

/// About 300 characters of `text` around the first query word it contains, whitespace collapsed.
pub fn snippet(text: &str, words: &[String]) -> String {
    let flat: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let lower = flat.to_lowercase();
    let at = words.iter().filter_map(|w| lower.find(w.as_str())).min().unwrap_or(0);
    // `lower` may differ in byte length from `flat` for a few scripts; fall back to the start.
    let at = if flat.is_char_boundary(at) && lower.len() == flat.len() { at } else { 0 };
    let start = flat[..at].char_indices().rev().nth(120).map(|(i, _)| i).unwrap_or(0);
    let mut out: String = flat[start..].chars().take(300).collect();
    if start + out.len() < flat.len() {
        out.push('…');
    }
    if start > 0 {
        out.insert(0, '…');
    }
    out
}

/// Opens the collections to search: every enabled one in `docs.toml`.
pub fn collections() -> Result<Vec<Collection>> {
    Ok(crate::collections::DocsToml::load()?.enabled().cloned().collect())
}

/// The absolute path of a document of a collection.
pub fn document_path(collection: &Collection, rel: &str) -> PathBuf {
    collection.root.join(rel)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terms_split_words_and_cjk_runs() {
        let (words, runs) = query_terms("What is the notice period in the Acme 合同期限 contract?");
        assert_eq!(words, ["notice", "period", "acme", "contract"]);
        assert_eq!(runs, ["合同期限"]);
        assert_eq!(grams(&runs), ["合同期", "同期限"]);
        let (words, _) = query_terms("Как работает фотосинтез");
        assert_eq!(words, ["как", "работает", "фотосинтез"]);
    }

    #[test]
    fn weighted_fusion_keeps_the_dense_order_unless_keywords_agree() {
        let dense = [1, 2, 3, 4];
        let keyword = [4, 9];
        let fused: Vec<i32> = rrf(&[(&dense[..], 1.0), (&keyword[..], 0.25)], 10).into_iter().map(|(k, _)| k).collect();
        // A keyword-only hit stays below every dense hit at a quarter weight…
        assert_eq!(fused.last(), Some(&9));
        let equal: Vec<i32> = rrf(&[(&dense[..], 1.0), (&keyword[..], 1.0)], 10).into_iter().map(|(k, _)| k).collect();
        // …and with equal weights it passes the dense tail.
        assert!(equal.iter().position(|k| *k == 9) < equal.iter().position(|k| *k == 3), "{equal:?}");
    }

    #[test]
    fn overlap_is_dropped_when_chunks_are_joined() {
        let a = "one\ntwo\nthe shared tail line here";
        let b = "the shared tail line here\nthree";
        assert_eq!(join_overlapping(a, b), "one\ntwo\nthe shared tail line here\nthree");
        assert_eq!(span("p.1", "p.2"), "p.1 .. p.2");
        assert_eq!(span("p.1 .. p.2", "p.2 .. p.3"), "p.1 .. p.3");
    }

    #[test]
    fn a_snippet_is_centred_on_a_query_word() {
        let text = format!("{} the notice period is ninety days {}", "x ".repeat(200), "y ".repeat(200));
        let s = snippet(&text, &["notice".to_string()]);
        assert!(s.contains("notice period is ninety days"), "{s}");
        assert!(s.chars().count() <= 302);
    }
}
