//! `ling docs read <doc-id> [--page N | --lines A-B]` and `docs_read` (spec §8.1, §8.2): a
//! document's text from the index, by page or line range, a page of output at a time.
//!
//! The text comes from the `units` table, so reading needs no extractor and runs inside the
//! agent's sandbox like a search. Output stays under the launcher's 8,000-token cap per tool call
//! ([`BUDGET_TOKENS`] by a conservative estimate) and names the locator to ask for next.

use std::collections::BTreeMap;

use anyhow::{anyhow, bail, Result};
use serde::Serialize;

use crate::collections::Collection;
use crate::store;

/// Estimated tokens one answer may hold: under the 8,000-token cap with room for the wrapping.
pub const BUDGET_TOKENS: usize = 6000;

/// A rough token count that never underestimates much: a Chinese, Japanese or Korean character
/// is about a token, other text about 3.5 characters a token.
pub fn estimate_tokens(text: &str) -> usize {
    let mut wide = 0;
    let mut other = 0;
    for c in text.chars() {
        if store::is_cjk(c) || ('\u{AC00}'..='\u{D7AF}').contains(&c) {
            wide += 1;
        } else {
            other += 1;
        }
    }
    wide + other * 2 / 7 + 1
}

/// Where to read.
#[derive(Debug, Clone, PartialEq)]
pub enum Locator {
    Page(u32),
    Lines(u32, u32),
}

/// `p.7`, `page 7`, `7`, `lines 120-180`, `120-180`, `L120-L180`, `lines 120`.
pub fn parse_locator(text: &str) -> Result<Locator> {
    let t = text.trim().to_lowercase();
    let t = t.split(" .. ").next().unwrap_or(&t).trim().to_string();
    let page = t.strip_prefix("p.").or_else(|| t.strip_prefix("page")).or_else(|| t.strip_prefix('p'));
    if let Some(n) = page.and_then(|p| p.trim().parse::<u32>().ok()) {
        return Ok(Locator::Page(n));
    }
    if let Ok(n) = t.parse::<u32>() {
        return Ok(Locator::Page(n));
    }
    let lines = t.strip_prefix("lines").or_else(|| t.strip_prefix("line")).unwrap_or(&t).trim().replace('l', "");
    let (a, b) = match lines.split_once('-') {
        Some((a, b)) => (a.trim().parse::<u32>(), b.trim().parse::<u32>()),
        None => (lines.trim().parse::<u32>(), lines.trim().parse::<u32>().map(|n| n + 199)),
    };
    match (a, b) {
        (Ok(a), Ok(b)) if a >= 1 && b >= a => Ok(Locator::Lines(a, b)),
        _ => bail!("`{text}` is not a locator: use p.7 or lines 120-180"),
    }
}

/// What a read returns.
#[derive(Debug, Serialize)]
pub struct ReadOut {
    pub doc_id: String,
    pub path: String,
    pub title: String,
    pub kind: String,
    pub pages: Option<u32>,
    /// What this answer covers.
    pub loc: String,
    pub text: String,
    /// The locator to ask for next, when the document goes on.
    pub next: Option<String>,
}

/// Splits `collection:N`.
pub fn parse_doc_id(id: &str) -> Result<(String, i64)> {
    let (collection, n) = id.trim().rsplit_once(':').ok_or_else(|| anyhow!("`{id}` is not a document id (they look like documents:42)"))?;
    let n: i64 = n.parse().map_err(|_| anyhow!("`{id}` is not a document id (they look like documents:42)"))?;
    Ok((collection.to_string(), n))
}

pub fn read(collections: &[Collection], doc_id: &str, locator: Option<&str>, budget: usize) -> Result<ReadOut> {
    let (name, id) = parse_doc_id(doc_id)?;
    let collection = collections.iter().find(|c| c.name == name).ok_or_else(|| anyhow!("no collection named `{name}`"))?;
    let conn = store::open_ro(&crate::config::db_path(&name))?.ok_or_else(|| anyhow!("the collection `{name}` has not been indexed yet"))?;
    let (rel, title, kind, pages, status): (String, String, String, Option<u32>, String) = conn
        .query_row("SELECT path, title, kind, pages, status FROM documents WHERE id = ?", [id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))
        .map_err(|_| anyhow!("no document {doc_id}"))?;
    if status != "ok" {
        bail!("{doc_id} ({rel}) is not indexed: {status}");
    }
    let locator = locator.filter(|l| !l.trim().is_empty()).map(parse_locator).transpose()?;
    let mut stmt = conn.prepare("SELECT page, line_first, text FROM units WHERE doc_id = ? ORDER BY ord")?;
    let units: Vec<(Option<u32>, Option<u32>, String)> = stmt.query_map([id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.filter_map(|r| r.ok()).collect();
    let path = collection.root.join(&rel).to_string_lossy().into_owned();
    let mut out = ReadOut { doc_id: doc_id.to_string(), path, title, kind: kind.clone(), pages, loc: String::new(), text: String::new(), next: None };
    if kind == "pdf" {
        let by_page: BTreeMap<u32, &str> = units.iter().filter_map(|(p, _, t)| p.map(|p| (p, t.as_str()))).collect();
        let first = match locator {
            Some(Locator::Page(p)) => p,
            None => 1,
            Some(Locator::Lines(..)) => bail!("{doc_id} is a PDF: read it by page (p.7)"),
        };
        if pages.is_some_and(|n| first > n) {
            bail!("{doc_id} has {} pages", pages.unwrap_or(0));
        }
        let mut used = 0;
        let mut last = None;
        for (page, text) in by_page.range(first..) {
            let cost = estimate_tokens(text);
            if last.is_some() && used + cost > budget {
                out.next = Some(format!("p.{page}"));
                break;
            }
            if !out.text.is_empty() {
                out.text.push_str("\n\n");
            }
            out.text.push_str(&format!("[p.{page}]\n"));
            out.text.push_str(&clip(text, budget));
            used += cost;
            last = Some(*page);
            // A page asked for by number is answered alone.
            if matches!(locator, Some(Locator::Page(_))) {
                if let Some((next, _)) = by_page.range(page + 1..).next() {
                    out.next = Some(format!("p.{next}"));
                }
                break;
            }
        }
        out.loc = match last {
            Some(l) if l != first => format!("p.{first} .. p.{l}"),
            Some(_) => format!("p.{first}"),
            None => {
                out.text = format!("p.{first} has no text layer (it needs OCR, which is not built yet)");
                format!("p.{first}")
            }
        };
        return Ok(out);
    }
    let mut lines: BTreeMap<u32, &str> = BTreeMap::new();
    for (_, first, text) in &units {
        if let Some(first) = first {
            for (i, line) in text.split('\n').enumerate() {
                lines.insert(first + i as u32, line);
            }
        }
    }
    let (a, b) = match locator {
        Some(Locator::Lines(a, b)) => (a, b),
        None => (1, u32::MAX),
        Some(Locator::Page(_)) => bail!("{doc_id} is a text file: read it by lines (lines 120-180)"),
    };
    let mut used = 0;
    let mut shown: Option<(u32, u32)> = None;
    for (n, line) in lines.range(a..=b) {
        let cost = estimate_tokens(line) + 1;
        if shown.is_some() && used + cost > budget {
            out.next = Some(format!("lines {n}-{}", if b == u32::MAX { n + 199 } else { b }));
            break;
        }
        out.text.push_str(line);
        out.text.push('\n');
        used += cost;
        shown = Some((shown.map(|s| s.0).unwrap_or(*n), *n));
    }
    if out.next.is_none() && b != u32::MAX {
        if let Some((n, _)) = lines.range(b + 1..).next() {
            out.next = Some(format!("lines {n}-{}", n + (b - a)));
        }
    }
    out.loc = shown.map(|(a, b)| format!("lines {a}-{b}")).unwrap_or_else(|| format!("lines {a}-{a}"));
    if shown.is_none() {
        out.text = format!("{doc_id} has no lines in that range");
    }
    Ok(out)
}

/// Cuts a text that alone exceeds the budget.
fn clip(text: &str, budget: usize) -> String {
    if estimate_tokens(text) <= budget {
        return text.to_string();
    }
    let mut out = String::new();
    for c in text.chars() {
        out.push(c);
        if out.len() % 256 == 0 && estimate_tokens(&out) > budget {
            break;
        }
    }
    out.push_str("\n[… page cut at the output limit]");
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn locators_parse_in_the_forms_people_and_models_write() {
        assert_eq!(parse_locator("p.7").unwrap(), Locator::Page(7));
        assert_eq!(parse_locator("page 12").unwrap(), Locator::Page(12));
        assert_eq!(parse_locator("3").unwrap(), Locator::Page(3));
        assert_eq!(parse_locator("lines 120-180").unwrap(), Locator::Lines(120, 180));
        assert_eq!(parse_locator("L5-L9").unwrap(), Locator::Lines(5, 9));
        assert_eq!(parse_locator("p.7 .. p.8").unwrap(), Locator::Page(7));
        assert!(parse_locator("chapter two").is_err());
        assert_eq!(parse_doc_id("documents:42").unwrap(), ("documents".to_string(), 42));
        assert!(parse_doc_id("42").is_err());
    }

    #[test]
    fn the_estimate_counts_cjk_as_a_token_a_character() {
        assert!(estimate_tokens("咖啡咖啡") >= 4);
        assert!(estimate_tokens(&"word ".repeat(100)) >= 100);
    }
}
