//! Plain, compact, bounded output (spec §7.2): the header states the sources and what was cut,
//! the mandatory lines say what the agent must verify, and no answer exceeds its row budget.

use anyhow::{bail, Result};

use crate::router::{per_file, Answer, Tag};

/// Paging and narrowing options.
#[derive(Debug, Clone, Default)]
pub struct Page {
    pub limit: usize,
    pub offset: usize,
    /// The cursor printed with the previous page; a mismatch means the index changed.
    pub cursor: Option<String>,
    /// Keep only rows whose path starts with this prefix, or matches this `*` glob.
    pub path: Option<String>,
    pub exact_only: bool,
    /// `def`, `read`, `write`, `import`, `reference`.
    pub kind: Option<String>,
}

/// Applies narrowing and paging in place, and checks the cursor.
pub fn narrow(answer: &mut Answer, page: &Page) -> Result<()> {
    if let Some(cursor) = &page.cursor {
        if *cursor != answer.cursor {
            bail!("the index changed since the first page (cursor {cursor}, now {}); run the query again without --offset", answer.cursor);
        }
    }
    if let Some(pattern) = &page.path {
        answer.rows.retain(|r| path_matches(&r.path, pattern));
    }
    if page.exact_only {
        answer.rows.retain(|r| r.tag == Some(Tag::Exact));
    }
    if let Some(kind) = &page.kind {
        let kind = if kind == "def" { "definition" } else { kind.as_str() };
        answer.rows.retain(|r| r.detail == kind || r.tag != Some(Tag::Exact) && r.detail.starts_with(kind));
    }
    Ok(())
}

fn path_matches(path: &str, pattern: &str) -> bool {
    if !pattern.contains('*') {
        return path.starts_with(pattern);
    }
    // A minimal glob: `*` matches within a segment, `**` across segments.
    fn go(p: &[u8], s: &[u8]) -> bool {
        match (p.first(), s.first()) {
            (None, None) => true,
            (Some(b'*'), _) if p.get(1) == Some(&b'*') => {
                let rest = p[2..].strip_prefix(b"/").unwrap_or(&p[2..]);
                (0..=s.len()).any(|i| go(rest, &s[i..]))
            }
            (Some(b'*'), _) => (0..=s.len()).take_while(|i| *i == 0 || s[i - 1] != b'/').any(|i| go(&p[1..], &s[i..])),
            (Some(a), Some(b)) if a == b => go(&p[1..], &s[1..]),
            _ => false,
        }
    }
    go(pattern.as_bytes(), path.as_bytes())
}

/// Renders an answer as text.
pub fn render(answer: &Answer, page: &Page, body: Option<&str>) -> String {
    let mut out = Vec::new();
    let total = answer.rows.len();
    let limit = page.limit.clamp(1, 200);
    let files = per_file(&answer.rows);
    let cut = total > page.offset + limit || page.offset > 0;
    let counts = if answer.tagged {
        let exact = answer.rows.iter().filter(|r| r.tag == Some(Tag::Exact)).count();
        format!("{total} results; {exact} exact, {} heuristic", total - exact)
    } else {
        format!("{total} results")
    };
    if cut {
        let shown = total.saturating_sub(page.offset).min(limit);
        let next = page.offset + shown;
        let more = if next < total { format!("; next: --offset {next} --cursor {}", answer.cursor) } else { String::new() };
        out.push(format!("{} {}   ({} in {} files; showing {shown} from {}{more})", answer.op, answer.query, counts, files.len(), page.offset));
    } else if answer.op == "show" {
        out.push(format!("{} {}", answer.op, answer.query));
    } else if answer.candidates.is_empty() {
        out.push(format!("{} {}   ({counts})", answer.op, answer.query));
    } else {
        out.push(format!("{} {}   (ambiguous)", answer.op, answer.query));
    }
    if answer.tagged {
        out.extend(answer.sources.iter().cloned());
        let searched = match (answer.changed_files, answer.changed_searched) {
            (0, _) | (_, None) => String::new(),
            (_, Some(true)) => ", all searched".to_string(),
            (_, Some(false)) => ", not searched".to_string(),
        };
        out.push(format!("changed since snapshot: {} files{searched}", answer.changed_files));
    }
    if !answer.candidates.is_empty() {
        out.push("candidates:".to_string());
        out.extend(answer.candidates.iter().map(|c| format!("  {c}")));
    }
    // Rows keep one order on every page: exact before heuristic before text, and within a tag
    // the files with the most results first, so a later page never repeats or skips a row.
    let rank: std::collections::HashMap<&str, usize> = files.iter().enumerate().map(|(i, (p, _))| (p.as_str(), i)).collect();
    let mut rows: Vec<_> = answer.rows.iter().collect();
    if cut {
        rows.sort_by_key(|r| (r.tag, rank.get(r.path.as_str()).copied().unwrap_or(usize::MAX), r.line));
    }
    if cut && page.offset == 0 {
        // Grouping first, rows second: the 15 files with the most results.
        let top: Vec<&(String, usize)> = files.iter().take(15).collect();
        out.push(format!("by file (top {} of {}):", top.len(), files.len()));
        out.extend(top.iter().map(|(path, count)| format!("  {count:>4}  {path}")));
    }
    for row in rows.into_iter().skip(page.offset).take(limit) {
        let location = format!("{}:{}", row.path, row.line);
        // No column padding: every space is a token the model re-reads on later turns.
        let line = match row.tag {
            Some(tag) => format!("{} {location}  {}", tag.label(), row.detail),
            None => format!("{location}  {}", row.detail),
        };
        out.push(line.trim_end().to_string());
    }
    if let Some(body) = body {
        out.push(body.to_string());
    }
    if answer.deleted_dropped > 0 {
        out.push(format!("dropped {} rows in files deleted since the snapshot", answer.deleted_dropped));
    }
    for unresolved in &answer.unresolved {
        out.push(format!("unresolved {unresolved}"));
    }
    for not_indexed in &answer.not_indexed {
        out.push(format!("not indexed {not_indexed}"));
    }
    if let Some(submodules) = &answer.submodules_not_indexed {
        out.push(format!("submodules not indexed: {submodules}"));
    }
    if let Some(not_checked) = &answer.not_checked {
        out.push(format!("not checked {not_checked}"));
    }
    for note in &answer.notes {
        out.push(format!("note: {note}"));
    }
    out.join("\n")
}

#[cfg(test)]
mod tests {
    use super::path_matches;

    #[test]
    fn globs() {
        assert!(path_matches("codex-rs/core/src/lib.rs", "codex-rs/core"));
        assert!(path_matches("codex-rs/core/src/lib.rs", "codex-rs/**/*.rs"));
        assert!(path_matches("a/b.py", "a/*.py"));
        assert!(!path_matches("a/c/b.py", "a/*.py"));
    }
}
