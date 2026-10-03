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

/// A `--path` as rows carry it: relative to the repository. The model often passes the absolute
/// path it sees in its working directory (`/testbed/astropy`), which as a prefix matched no row and
/// turned a search with results into "0 results" (measured in the SWE-bench arm, 2026-10-03).
pub fn repository_relative(pattern: &str, root: &std::path::Path) -> String {
    let pattern = pattern.strip_prefix("./").unwrap_or(pattern);
    if !pattern.starts_with('/') {
        return pattern.to_string();
    }
    let roots = [Some(root.to_path_buf()), root.canonicalize().ok()];
    for root in roots.into_iter().flatten() {
        if let Ok(rest) = std::path::Path::new(pattern).strip_prefix(&root) {
            return rest.to_string_lossy().into_owned();
        }
    }
    pattern.to_string()
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

/// Lines of a definition `show` prints by default. SWE-agent measured a 100-line window as the
/// best trade; 26 of the 207 `code_show` calls in the SWE-bench arm were longer, and the whole body
/// cost 16% more of its output (specs/DREAMFERENCE_PUFFIN_CONTEXT_BUDGET.md §4.3).
pub const SHOW_LINES: usize = 100;

/// A docstring longer than this many lines is folded after them.
pub const DOCSTRING_LINES: usize = 12;

/// Folds a Python definition's docstring after [`DOCSTRING_LINES`] lines, in `show`'s numbered
/// lines (`{:>5}  {source}`). The fold line names the lines it hides, so `sed -n` can still reach
/// them. A body with no docstring, or a short one, comes back unchanged.
pub fn fold_docstring(lines: Vec<String>) -> Vec<String> {
    let source = |line: &str| line.get(7..).unwrap_or("").trim_start().to_string();
    let opens = |text: &str| {
        let text = text.trim_start_matches(['r', 'R', 'u', 'U', 'b', 'B']);
        ["\"\"\"", "'''"].into_iter().find(|quote| text.starts_with(quote))
    };
    // The docstring is the first statement after the header, which ends at the first line ending
    // in `:` (a signature may span lines); decorators and comments come before it.
    let Some(header_end) = lines.iter().position(|line| source(line).trim_end().ends_with(':')) else { return lines };
    let Some(first) = lines.iter().skip(header_end + 1).position(|line| !source(line).is_empty()).map(|i| i + header_end + 1) else {
        return lines;
    };
    let Some(quote) = opens(&source(&lines[first])) else { return lines };
    let after_open = source(&lines[first]);
    let after_open = &after_open[after_open.find(quote).map_or(0, |at| at + quote.len())..];
    let last = if after_open.contains(quote) {
        first
    } else {
        match lines.iter().skip(first + 1).position(|line| source(line).contains(quote)) {
            Some(i) => i + first + 1,
            None => return lines,
        }
    };
    let length = last + 1 - first;
    if length <= DOCSTRING_LINES {
        return lines;
    }
    let number = |line: &str| line.get(..5).unwrap_or("").trim().to_string();
    let hidden_from = first + DOCSTRING_LINES;
    let mut out: Vec<String> = lines[..hidden_from].to_vec();
    let indent: String = lines[first].get(7..).unwrap_or("").chars().take_while(|c| c.is_whitespace()).collect();
    out.push(format!(
        "{:>5}  {indent}… {} docstring lines folded ({}-{})",
        "",
        last + 1 - hidden_from,
        number(&lines[hidden_from]),
        number(&lines[last])
    ));
    out.extend(lines[last + 1..].iter().cloned());
    out
}

/// One page of `show`'s lines, and a last line naming the rest and the offset that shows it.
fn show_page(body: &str, page: &Page) -> String {
    let lines: Vec<&str> = body.lines().collect();
    let limit = page.limit.max(1);
    let start = page.offset.min(lines.len());
    let end = (start + limit).min(lines.len());
    let mut out = lines[start..end].join("\n");
    if end < lines.len() {
        let numbered = |line: &&str| line.get(..5).and_then(|n| n.trim().parse::<usize>().ok());
        let first = lines[end..].iter().find_map(numbered);
        let last = lines[end..].iter().rev().find_map(numbered);
        let range = match (first, last) {
            (Some(first), Some(last)) => format!("lines {first}-{last}"),
            _ => format!("{} more lines", lines.len() - end),
        };
        out.push_str(&format!("\n… {range} not shown; next: offset {end}"));
    }
    out
}

/// Renders an answer as text.
pub fn render(answer: &Answer, page: &Page, body: Option<&str>) -> String {
    let mut out = Vec::new();
    let total = answer.rows.len();
    let limit = page.limit.clamp(1, 200);
    let files = per_file(&answer.rows);
    // `show` pages its body, not rows (see `show_page`).
    let cut = body.is_none() && (total > page.offset + limit || page.offset > 0);
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
    // `impact` keeps the order its rows were found in: nearest first.
    if cut && answer.op != "impact" {
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
        out.push(show_page(body, page));
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
    use super::{fold_docstring, path_matches, render, repository_relative, Page, DOCSTRING_LINES, SHOW_LINES};
    use crate::router::Answer;

    fn numbered(source: &[&str], from: usize) -> Vec<String> {
        source.iter().enumerate().map(|(i, l)| format!("{:>5}  {l}", i + from)).collect()
    }

    #[test]
    fn a_long_docstring_folds_after_twelve_lines_and_names_what_it_hides() {
        let mut source = vec!["def solveset(f, symbol=None,", "             domain=None):", "    r\"\"\"Solves a given inequality."];
        let doc: Vec<String> = (0..33).map(|n| format!("    doc line {n}")).collect();
        source.extend(doc.iter().map(String::as_str));
        source.extend(["    \"\"\"", "    return f"]);
        let folded = fold_docstring(numbered(&source, 1962));
        assert_eq!(folded.len(), 2 + DOCSTRING_LINES + 1 + 1);
        let fold = &folded[2 + DOCSTRING_LINES];
        assert!(fold.ends_with("… 23 docstring lines folded (1976-1998)"), "{fold}");
        assert!(folded.last().is_some_and(|l| l.ends_with("return f")));
    }

    #[test]
    fn short_or_missing_docstrings_are_left_alone() {
        let short = numbered(&["def f():", "    \"\"\"One line.\"\"\"", "    return 1"], 1);
        assert_eq!(fold_docstring(short.clone()), short);
        let none = numbered(&["def f():", "    x = 1", "    return x"], 1);
        assert_eq!(fold_docstring(none.clone()), none);
    }

    #[test]
    fn show_prints_a_hundred_lines_and_names_the_rest() {
        let body = numbered(&vec!["    x = 1"; 165], 1962).join("\n");
        let answer = Answer { op: "show".into(), query: "solveset  (sympy/solvers/solveset.py:1962-2126)".into(), ..Answer::default() };
        let page = Page { limit: SHOW_LINES, ..Page::default() };
        let text = render(&answer, &page, Some(&body));
        assert!(text.starts_with("show solveset  (sympy/solvers/solveset.py:1962-2126)\n"), "{text}");
        assert_eq!(text.lines().filter(|l| l.ends_with("x = 1")).count(), SHOW_LINES);
        assert!(text.contains("… lines 2062-2126 not shown; next: offset 100"), "{text}");
        let next = render(&answer, &Page { limit: SHOW_LINES, offset: 100, ..Page::default() }, Some(&body));
        assert_eq!(next.lines().filter(|l| l.ends_with("x = 1")).count(), 65);
        assert!(!next.contains("not shown") && !next.contains("results"));
    }

    #[test]
    fn globs() {
        assert!(path_matches("codex-rs/core/src/lib.rs", "codex-rs/core"));
        assert!(path_matches("codex-rs/core/src/lib.rs", "codex-rs/**/*.rs"));
        assert!(path_matches("a/b.py", "a/*.py"));
        assert!(!path_matches("a/c/b.py", "a/*.py"));
    }

    #[test]
    fn an_absolute_path_inside_the_repository_narrows_like_a_relative_one() {
        let root = std::path::Path::new("/testbed");
        assert_eq!(repository_relative("/testbed/astropy/io", root), "astropy/io");
        assert_eq!(repository_relative("./astropy", root), "astropy");
        assert_eq!(repository_relative("astropy", root), "astropy");
        assert_eq!(repository_relative("/testbed", root), "");
        assert!(path_matches("astropy/io/ascii/html.py", &repository_relative("/testbed/astropy", root)));
        // Outside the repository nothing matches, as before.
        assert_eq!(repository_relative("/elsewhere/x", root), "/elsewhere/x");
    }
}
