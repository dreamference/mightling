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
    /// A range of numbered lines: lines, paragraphs or rows, as the kind numbers them.
    Lines(u32, u32),
    /// A part named by its locator: a message (`msg 3`) or an attachment (`msg 3 · a.pdf`).
    Part(String),
}

/// `p.7`, `page 7`, `7`, `lines 120-180`, `120-180`, `L120-L180`, `lines 120`, `para 12-30`,
/// `rows 2-21`, `msg 3`, `msg 3 · invoice.pdf`, `msg 3, lines 40-60`.
pub fn parse_locator(text: &str) -> Result<Locator> {
    let original = text
        .trim()
        .split(" .. ")
        .next()
        .unwrap_or("")
        .trim()
        .to_string();
    let t = original.to_lowercase();
    if t.starts_with("msg ") || t.starts_with("slide ") || t.starts_with("sheet ") {
        // A message's or a slide's lines are the file's own; a sheet's rows are the sheet's.
        return match crate::chunk::prefixed(&t) {
            Some((_, "lines", ..)) => {
                parse_locator(t.rsplit_once(", ").map(|(_, lines)| lines).unwrap_or(&t))
            }
            _ => Ok(Locator::Part(original)),
        };
    }
    let page = t
        .strip_prefix("p.")
        .or_else(|| t.strip_prefix("page"))
        .or_else(|| t.strip_prefix('p'));
    if let Some(n) = page.and_then(|p| p.trim().parse::<u32>().ok()) {
        if n >= 1 {
            return Ok(Locator::Page(n));
        }
    }
    if let Ok(n) = t.parse::<u32>() {
        if n >= 1 {
            return Ok(Locator::Page(n));
        }
    }
    let words = [
        "lines",
        "line",
        "paragraphs",
        "paragraph",
        "para",
        "rows",
        "row",
    ];
    let lines = words
        .iter()
        .find_map(|w| t.strip_prefix(w))
        .unwrap_or(&t)
        .trim()
        .replace('l', "");
    let (a, b) = match lines.split_once('-') {
        Some((a, b)) => (a.trim().parse::<u32>(), b.trim().parse::<u32>()),
        None => (
            lines.trim().parse::<u32>(),
            lines.trim().parse::<u32>().map(|n| n + 199),
        ),
    };
    match (a, b) {
        (Ok(a), Ok(b)) if a >= 1 && b >= a => Ok(Locator::Lines(a, b)),
        _ => bail!("`{text}` is not a locator: use p.7, lines 120-180 (para, rows), msg 3, slide 4 or sheet Budget"),
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
    let (collection, n) = id
        .trim()
        .rsplit_once(':')
        .ok_or_else(|| anyhow!("`{id}` is not a document id (they look like documents:42)"))?;
    let n: i64 = n
        .parse()
        .map_err(|_| anyhow!("`{id}` is not a document id (they look like documents:42)"))?;
    Ok((collection.to_string(), n))
}

pub fn read(
    collections: &[Collection],
    doc_id: &str,
    locator: Option<&str>,
    budget: usize,
) -> Result<ReadOut> {
    let (name, id) = parse_doc_id(doc_id)?;
    let collection = collections
        .iter()
        .find(|c| c.name == name)
        .ok_or_else(|| anyhow!("no collection named `{name}`"))?;
    let conn = store::open_ro(&crate::config::db_path(&name))?
        .ok_or_else(|| anyhow!("the collection `{name}` has not been indexed yet"))?;
    let (rel, title, kind, pages, status): (String, String, String, Option<u32>, String) = conn
        .query_row(
            "SELECT path, title, kind, pages, status FROM documents WHERE id = ?",
            [id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
        )
        .map_err(|_| anyhow!("no document {doc_id}"))?;
    if status != "ok" {
        bail!("{doc_id} ({rel}) is not indexed: {status}");
    }
    let locator = locator
        .filter(|l| !l.trim().is_empty())
        .map(parse_locator)
        .transpose()?;
    let mut stmt = conn.prepare(
        "SELECT page, line_first, text, loc, line_last FROM units WHERE doc_id = ? ORDER BY ord",
    )?;
    // a one-off row tuple.
    #[allow(clippy::type_complexity)]
    let rows: Vec<(Option<u32>, Option<u32>, String, String, Option<u32>)> = stmt
        .query_map([id], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
        })?
        .filter_map(|r| r.ok())
        .collect();
    let word = crate::discover::Kind::parse(&kind)
        .map(|k| k.line_word())
        .unwrap_or("lines");
    // A part named by its locator becomes the range of its units' lines.
    let locator = match locator {
        // A sheet's rows: shifted by where the sheet's first unit starts.
        Some(Locator::Part(part))
            if crate::chunk::prefixed(&part).is_some_and(|(_, word, ..)| word != "lines") =>
        {
            let (prefix, _, a, b) = crate::chunk::prefixed(&part).unwrap();
            let prefix = prefix.to_lowercase();
            let unit = rows.iter().find(|r| {
                crate::chunk::prefixed(&r.3).is_some_and(|(p, ..)| p.to_lowercase() == prefix)
            });
            match unit.and_then(|r| Some((r.1?, crate::chunk::prefixed(&r.3)?.2))) {
                Some((line, start)) => Some(Locator::Lines(
                    (a + line).saturating_sub(start).max(1),
                    (b + line).saturating_sub(start).max(1),
                )),
                None => bail!("{doc_id} has no part `{part}`"),
            }
        }
        Some(Locator::Part(part)) => {
            let lower = part.to_lowercase();
            let inside = |loc: &str| {
                let loc = loc.to_lowercase();
                loc == lower
                    || loc
                        .strip_prefix(lower.as_str())
                        .is_some_and(|rest| rest.starts_with(' ') || rest.starts_with(','))
            };
            let first = rows
                .iter()
                .filter(|r| inside(&r.3))
                .filter_map(|r| r.1)
                .min();
            let last = rows
                .iter()
                .filter(|r| inside(&r.3))
                .filter_map(|r| r.4)
                .max();
            match (first, last) {
                (Some(a), Some(b)) => Some(Locator::Lines(a, b)),
                _ => bail!("{doc_id} has no part `{part}`"),
            }
        }
        other => other,
    };
    let units: Vec<(Option<u32>, Option<u32>, String)> =
        rows.into_iter().map(|(p, l, t, _, _)| (p, l, t)).collect();
    let path = collection.root.join(&rel).to_string_lossy().into_owned();
    let out = ReadOut {
        doc_id: doc_id.to_string(),
        path,
        title,
        kind: kind.clone(),
        pages,
        loc: String::new(),
        text: String::new(),
        next: None,
    };
    render(out, &units, pages, locator, word, budget)
}

/// The text of `units` (page, first line, text) a locator asks for, within the budget.
fn render(
    mut out: ReadOut,
    units: &[(Option<u32>, Option<u32>, String)],
    pages: Option<u32>,
    locator: Option<Locator>,
    word: &str,
    budget: usize,
) -> Result<ReadOut> {
    let doc_id = out.doc_id.clone();
    let kind = out.kind.clone();
    if kind == "image" {
        // An image's text, read by OCR (§20), is one unit.
        out.loc = crate::ocr::worker::IMAGE_LOC.to_string();
        out.text = match units.first() {
            Some((_, _, text)) => clip(text, budget),
            None => format!(
                "{doc_id} is an image in which OCR found no text, or which OCR has not read yet"
            ),
        };
        return Ok(out);
    }
    if kind == "pdf" {
        let by_page: BTreeMap<u32, &str> = units
            .iter()
            .filter_map(|(p, _, t)| p.map(|p| (p, t.as_str())))
            .collect();
        let first = match locator {
            Some(Locator::Page(p)) => p,
            None => 1,
            Some(Locator::Lines(..)) | Some(Locator::Part(_)) => {
                bail!("{doc_id} is a PDF: read it by page (p.7)")
            }
        };
        if pages.is_some_and(|n| first > n) {
            bail!("{doc_id} has {} pages", pages.unwrap_or(0));
        }
        let mut used = 0;
        let mut last = None;
        let asked = matches!(locator, Some(Locator::Page(_)));
        for (page, text) in by_page.range(first..) {
            // A page asked for that has no text is said so below, not answered by a later one.
            if asked && *page != first {
                out.next = Some(format!("p.{page}"));
                break;
            }
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
            if asked {
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
                out.text = format!("p.{first} has no text: a scanned page that OCR has not read (it may not be installed, the page may be past docs_ocr_max_pages, or it may not have been readable)");
                format!("p.{first}")
            }
        };
        return Ok(out);
    }
    let mut lines: BTreeMap<u32, &str> = BTreeMap::new();
    for (_, first, text) in units {
        if let Some(first) = first {
            for (i, line) in text.split('\n').enumerate() {
                lines.insert(first + i as u32, line);
            }
        }
    }
    let (a, b) = match locator {
        Some(Locator::Lines(a, b)) => (a, b),
        None => (1, u32::MAX),
        Some(Locator::Page(_)) => {
            bail!("{doc_id} is not paged: read it by {word} ({word} 120-180)")
        }
        Some(Locator::Part(_)) => unreachable!("resolved above"),
    };
    let mut used = 0;
    let mut shown: Option<(u32, u32)> = None;
    for (n, line) in lines.range(a..=b) {
        let cost = estimate_tokens(line) + 1;
        if shown.is_some() && used + cost > budget {
            out.next = Some(format!(
                "{word} {n}-{}",
                if b == u32::MAX { n + 199 } else { b }
            ));
            break;
        }
        out.text.push_str(&clip(line, budget));
        out.text.push('\n');
        used += cost;
        shown = Some((shown.map(|s| s.0).unwrap_or(*n), *n));
    }
    if out.next.is_none() && b != u32::MAX {
        if let Some((n, _)) = lines.range(b + 1..).next() {
            out.next = Some(format!("{word} {n}-{}", n + (b - a)));
        }
    }
    out.loc = shown
        .map(|(a, b)| format!("{word} {a}-{b}"))
        .unwrap_or_else(|| format!("{word} {a}-{a}"));
    if shown.is_none() {
        out.text = format!("{doc_id} has no {word} in that range");
    }
    Ok(out)
}

/// Cuts a text that alone exceeds the budget (a page, or one long line).
fn clip(text: &str, budget: usize) -> String {
    if estimate_tokens(text) <= budget {
        return text.to_string();
    }
    let mut out = String::new();
    for (n, c) in text.chars().enumerate() {
        out.push(c);
        if (n + 1).is_multiple_of(256) && estimate_tokens(&out) > budget {
            break;
        }
    }
    out.push_str("\n[… cut at the output limit]");
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_zero_is_not_a_locator() {
        for text in ["p.0", "page 0", "0", "p0"] {
            assert!(parse_locator(text).is_err(), "{text}");
        }
        assert_eq!(parse_locator("p.1").unwrap(), Locator::Page(1));
    }

    #[test]
    fn locators_parse_in_the_forms_people_and_models_write() {
        assert_eq!(parse_locator("p.7").unwrap(), Locator::Page(7));
        assert_eq!(parse_locator("page 12").unwrap(), Locator::Page(12));
        assert_eq!(parse_locator("3").unwrap(), Locator::Page(3));
        assert_eq!(
            parse_locator("lines 120-180").unwrap(),
            Locator::Lines(120, 180)
        );
        assert_eq!(parse_locator("L5-L9").unwrap(), Locator::Lines(5, 9));
        assert_eq!(parse_locator("p.7 .. p.8").unwrap(), Locator::Page(7));
        assert!(parse_locator("chapter two").is_err());
        assert_eq!(parse_locator("para 12-30").unwrap(), Locator::Lines(12, 30));
        assert_eq!(
            parse_locator("rows 2-21 .. rows 22-41").unwrap(),
            Locator::Lines(2, 21)
        );
        assert_eq!(
            parse_locator("msg 3").unwrap(),
            Locator::Part("msg 3".into())
        );
        assert_eq!(
            parse_locator("msg 3 · Invoice.pdf p.2").unwrap(),
            Locator::Part("msg 3 · Invoice.pdf p.2".into())
        );
        assert_eq!(
            parse_locator("msg 3, lines 40-60").unwrap(),
            Locator::Lines(40, 60)
        );
        assert_eq!(
            parse_locator("sheet Budget, rows 5-9").unwrap(),
            Locator::Part("sheet Budget, rows 5-9".into())
        );
        assert_eq!(
            parse_locator("slide 4").unwrap(),
            Locator::Part("slide 4".into())
        );
        assert_eq!(
            parse_doc_id("documents:42").unwrap(),
            ("documents".to_string(), 42)
        );
        assert!(parse_doc_id("42").is_err());
    }

    #[test]
    fn the_estimate_counts_cjk_as_a_token_a_character() {
        assert!(estimate_tokens("咖啡咖啡") >= 4);
        assert!(estimate_tokens(&"word ".repeat(100)) >= 100);
    }

    #[test]
    fn single_line_locators_reverse_ranges_and_clipping_respect_bounds() {
        assert_eq!(parse_locator("lines 10").unwrap(), Locator::Lines(10, 209));
        assert_eq!(parse_locator("L10").unwrap(), Locator::Lines(10, 209));
        assert_eq!(
            parse_locator("slide 4, lines 10-20").unwrap(),
            Locator::Lines(10, 20)
        );
        assert!(parse_locator("lines 20-10").is_err(), "reversed range");
        assert!(parse_locator("lines 0-5").is_err(), "0-based line range");
        assert!(parse_doc_id("documents:not_a_number").is_err());

        let short = "short text";
        assert_eq!(clip(short, 50), short);
        let long = "word ".repeat(500);
        let clipped = clip(&long, 50);
        assert!(clipped.ends_with("[… cut at the output limit]"));
        assert!(clipped.len() < long.len());
    }

    fn blank(kind: &str) -> ReadOut {
        ReadOut {
            doc_id: "docs:1".into(),
            path: String::new(),
            title: String::new(),
            kind: kind.into(),
            pages: None,
            loc: String::new(),
            text: String::new(),
            next: None,
        }
    }

    #[test]
    fn a_page_without_text_is_said_so_not_answered_with_a_later_page() {
        let units = [
            (Some(1), None, "one".to_string()),
            (Some(2), None, "two".to_string()),
            (Some(5), None, "five".to_string()),
        ];
        let out = render(
            blank("pdf"),
            &units,
            Some(5),
            Some(Locator::Page(3)),
            "lines",
            6000,
        )
        .unwrap();
        assert!(out.text.starts_with("p.3 has no text"), "{}", out.text);
        assert_eq!(out.loc, "p.3");
        assert_eq!(out.next.as_deref(), Some("p.5"));
        let out = render(
            blank("pdf"),
            &units,
            Some(5),
            Some(Locator::Page(2)),
            "lines",
            6000,
        )
        .unwrap();
        assert_eq!(out.text, "[p.2]\ntwo");
        assert_eq!(out.next.as_deref(), Some("p.5"));
    }

    #[test]
    fn one_line_longer_than_the_budget_is_cut() {
        let units = [(None, Some(1), "word ".repeat(20_000))];
        let out = render(blank("json"), &units, None, None, "lines", 500).unwrap();
        assert!(estimate_tokens(&out.text) < 600, "{}", out.text.len());
        assert!(out.text.contains("cut at the output limit"));
    }
}
