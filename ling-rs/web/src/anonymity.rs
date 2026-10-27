//! Pre-push anonymity check for non-private repositories (`specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md` §4.5):
//! compiles the node's `$HOME/.config/dreamference/anonymity-denylist.txt` entries with the same
//! whole-word, case-insensitive rule and 1-based entry numbering as `tests/test_anonymity.py`,
//! and reports only `{file, line, entry}` so matched text and denylist patterns never appear in
//! responses or logs.

use std::path::Path;

use regex::Regex;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;

/// Relative path of the node's anonymity denylist under the user's home directory.
pub const DENYLIST_REL_PATH: &str = ".config/dreamference/anonymity-denylist.txt";

/// One compiled denylist entry with its 1-based entry number.
#[derive(Clone, Debug)]
pub struct DenylistPattern {
    pub entry: usize,
    pub raw: String,
    pub regex: Regex,
}

/// One anonymity match reported by a pre-push scan; never carries the matched text or the pattern.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Finding {
    pub file: String,
    pub line: usize,
    pub entry: usize,
}

impl Finding {
    pub fn to_json(&self) -> Value {
        json!({
            "file": self.file,
            "line": self.line,
            "entry": self.entry,
        })
    }
}

/// Reads the denylist file from `home` (`<home>/.config/dreamference/anonymity-denylist.txt`),
/// returning `None` when the file does not exist.
pub fn read_denylist(home: &Path) -> Option<String> {
    std::fs::read_to_string(home.join(DENYLIST_REL_PATH)).ok()
}

/// Reads the denylist file from `home`, distinguishing a missing file (`Ok(None)`) from a read error (`Err`).
pub fn load_denylist(home: &Path) -> Result<Option<String>, std::io::Error> {
    match std::fs::read_to_string(home.join(DENYLIST_REL_PATH)) {
        Ok(text) => Ok(Some(text)),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(err) => Err(err),
    }
}

/// Compiles the denylist's non-empty, non-comment lines into whole-word, case-insensitive regular
/// expressions numbered from 1 in file order. An entry that does not compile as a regular
/// expression falls back to `regex::escape(entry)`.
pub fn denylist_patterns(text: &str) -> Vec<DenylistPattern> {
    let mut patterns = Vec::new();
    let mut index = 0usize;
    for line in text.lines() {
        let entry = line.trim();
        if entry.is_empty() || entry.starts_with('#') {
            continue;
        }
        index += 1;
        let body = if Regex::new(entry).is_ok() {
            entry.to_string()
        } else {
            regex::escape(entry)
        };
        let wrapped = format!("(?i)(?:^|[^A-Za-z0-9])(?:{body})(?:$|[^A-Za-z0-9])");
        let regex = Regex::new(&wrapped).unwrap_or_else(|_| {
            let escaped = regex::escape(entry);
            Regex::new(&format!("(?i)(?:^|[^A-Za-z0-9])(?:{escaped})(?:$|[^A-Za-z0-9])"))
                .expect("escaped literal regex must compile")
        });
        patterns.push(DenylistPattern {
            entry: index,
            raw: entry.to_string(),
            regex,
        });
    }
    patterns
}

/// Returns the 1-based entry numbers of all patterns matching `line`.
pub fn matching_entries(patterns: &[DenylistPattern], line: &str) -> Vec<usize> {
    patterns
        .iter()
        .filter(|p| p.regex.is_match(line))
        .map(|p| p.entry)
        .collect()
}

/// Scans a metadata field (e.g. `"author"` or `"commit message"`) line by line and appends findings
/// with `line: 0`.
pub fn scan_metadata(patterns: &[DenylistPattern], label: &str, text: &str, findings: &mut Vec<Finding>) {
    for line in text.lines() {
        for entry in matching_entries(patterns, line) {
            push_unique(
                findings,
                Finding {
                    file: label.to_string(),
                    line: 0,
                    entry,
                },
            );
        }
    }
}

/// Scans a list of touched file paths and appends findings with `file: "(a path)"` and `line: 0`.
pub fn scan_paths(patterns: &[DenylistPattern], paths: &[String], findings: &mut Vec<Finding>) {
    for path in paths {
        for entry in matching_entries(patterns, path) {
            push_unique(
                findings,
                Finding {
                    file: "(a path)".to_string(),
                    line: 0,
                    entry,
                },
            );
        }
    }
}

/// Scans unified diff output (`git diff-tree -m -r -p -U0 --text --no-renames --no-textconv --no-ext-diff --root <sha>`)
/// for matches in touched paths and added lines, recording the 1-based line number in the new file.
pub fn scan_unified_patch(patterns: &[DenylistPattern], patch: &str, findings: &mut Vec<Finding>) {
    let mut current_file: Option<String> = None;
    let mut new_line: usize = 0;
    let mut old_remaining: usize = 0;
    let mut new_remaining: usize = 0;

    for raw_line in patch.lines() {
        if old_remaining > 0 || new_remaining > 0 {
            if let Some(added) = raw_line.strip_prefix('+') {
                if let Some(ref file) = current_file
                    && new_line > 0
                {
                    for entry in matching_entries(patterns, added) {
                        push_unique(
                            findings,
                            Finding {
                                file: file.clone(),
                                line: new_line,
                                entry,
                            },
                        );
                    }
                }
                new_line += 1;
                new_remaining = new_remaining.saturating_sub(1);
                continue;
            } else if raw_line.starts_with('-') {
                old_remaining = old_remaining.saturating_sub(1);
                continue;
            } else if raw_line.starts_with(' ') {
                new_line += 1;
                old_remaining = old_remaining.saturating_sub(1);
                new_remaining = new_remaining.saturating_sub(1);
                continue;
            } else if raw_line.starts_with('\\') {
                continue;
            }
            old_remaining = 0;
            new_remaining = 0;
        }

        if let Some(file) = parse_plus_header_path(raw_line) {
            let path_matches = matching_entries(patterns, &file);
            for entry in &path_matches {
                push_unique(
                    findings,
                    Finding {
                        file: "(a path)".to_string(),
                        line: 0,
                        entry: *entry,
                    },
                );
            }
            current_file = Some(if path_matches.is_empty() { file } else { "(a path)".to_string() });
            new_line = 0;
            continue;
        }
        if raw_line.starts_with("+++ ") {
            current_file = None;
            new_line = 0;
            continue;
        }
        if raw_line.starts_with("--- ") || raw_line.starts_with("diff --git ") {
            continue;
        }
        if let Some(hunk) = raw_line.strip_prefix("@@ ") {
            if let Some((old_count, new_start, new_count)) = parse_hunk_header(hunk) {
                old_remaining = old_count;
                new_line = new_start;
                new_remaining = new_count;
            } else {
                old_remaining = 0;
                new_line = 0;
                new_remaining = 0;
            }
            continue;
        }
    }
}

fn parse_plus_header_path(raw_line: &str) -> Option<String> {
    let rest = raw_line.strip_prefix("+++ ")?.trim();
    if rest == "/dev/null" {
        return None;
    }
    if rest.starts_with('"') && rest.ends_with('"') && rest.len() >= 2 {
        let unquoted = unquote_c_path(&rest[1..rest.len() - 1]);
        return unquoted.strip_prefix("b/").map(str::to_string);
    }
    rest.strip_prefix("b/").map(str::to_string)
}

fn unquote_c_path(inner: &str) -> String {
    let mut bytes = Vec::with_capacity(inner.len());
    let mut chars = inner.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '\\' {
            let mut buf = [0u8; 4];
            bytes.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
            continue;
        }
        let Some(&next) = chars.peek() else {
            bytes.push(b'\\');
            break;
        };
        if ('0'..='7').contains(&next) {
            let mut val: u8 = 0;
            for _ in 0..3 {
                if let Some(&digit) = chars.peek()
                    && ('0'..='7').contains(&digit)
                {
                    chars.next();
                    val = val.wrapping_mul(8).wrapping_add((digit as u8) - b'0');
                } else {
                    break;
                }
            }
            bytes.push(val);
            continue;
        }
        chars.next();
        match next {
            '\\' => bytes.push(b'\\'),
            '"' => bytes.push(b'"'),
            'n' => bytes.push(b'\n'),
            'r' => bytes.push(b'\r'),
            't' => bytes.push(b'\t'),
            'a' => bytes.push(0x07),
            'b' => bytes.push(0x08),
            'f' => bytes.push(0x0c),
            'v' => bytes.push(0x0b),
            other => {
                let mut buf = [0u8; 4];
                bytes.extend_from_slice(other.encode_utf8(&mut buf).as_bytes());
            }
        }
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

fn parse_hunk_range(spec: &str) -> Option<(usize, usize)> {
    let rest = spec.strip_prefix('-').or_else(|| spec.strip_prefix('+'))?;
    match rest.split_once(',') {
        Some((start, count)) => Some((start.parse().ok()?, count.parse().ok()?)),
        None => Some((rest.parse().ok()?, 1)),
    }
}

fn parse_hunk_header(after_at: &str) -> Option<(usize, usize, usize)> {
    let header = after_at.split("@@").next()?.trim();
    let mut parts = header.split_whitespace();
    let old_spec = parts.next()?;
    let new_spec = parts.next()?;
    if !old_spec.starts_with('-') || !new_spec.starts_with('+') {
        return None;
    }
    let (_, old_count) = parse_hunk_range(old_spec)?;
    let (new_start, new_count) = parse_hunk_range(new_spec)?;
    Some((old_count, new_start, new_count))
}

fn push_unique(findings: &mut Vec<Finding>, finding: Finding) {
    if !findings.contains(&finding) {
        findings.push(finding);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn denylist_entries_match_whole_words_and_fall_back_to_literals() {
        let compiled = denylist_patterns("# a comment\n\nnode-q\n\\w*zed\\w*\n[unclosed\n");
        assert_eq!(compiled.len(), 3);
        assert_eq!(compiled[0].entry, 1);
        assert_eq!(compiled[0].raw, "node-q");
        assert_eq!(compiled[1].entry, 2);
        assert_eq!(compiled[1].raw, "\\w*zed\\w*");
        assert_eq!(compiled[2].entry, 3);
        assert_eq!(compiled[2].raw, "[unclosed");

        let by_raw = |name: &str| -> &Regex {
            &compiled.iter().find(|p| p.raw == name).unwrap().regex
        };

        assert!(by_raw("node-q").is_match("tag NODE-Q.txt"));
        assert!(!by_raw("node-q").is_match("node-q2"));
        assert!(by_raw("\\w*zed\\w*").is_match("realized"));
        assert!(by_raw("[unclosed").is_match("a [unclosed bracket"));
        assert!(compiled.iter().all(|p| p.raw != "# a comment"));
    }

    #[test]
    fn unified_patch_and_metadata_report_file_line_and_entry_only() {
        let patterns = denylist_patterns("# comment\nsecret-token\n\\w*internal\\w*\n");
        let patch = "\
diff --git a/src/lib.rs b/src/lib.rs
index 1111111..2222222 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -10,0 +11,3 @@
+let ok = 1;
+let bad = \"SECRET-TOKEN\";
+let sub = \"my_internal_var\";
";
        let mut findings = Vec::new();
        scan_unified_patch(&patterns, patch, &mut findings);
        scan_metadata(&patterns, "commit message", "fix secret-token handling\n", &mut findings);
        assert_eq!(
            findings,
            vec![
                Finding {
                    file: "src/lib.rs".to_string(),
                    line: 12,
                    entry: 1,
                },
                Finding {
                    file: "src/lib.rs".to_string(),
                    line: 13,
                    entry: 2,
                },
                Finding {
                    file: "commit message".to_string(),
                    line: 0,
                    entry: 1,
                },
            ]
        );
    }

    #[test]
    fn hunk_line_counting_prevents_header_injection_and_handles_quoted_and_redacted_paths() {
        let patterns = denylist_patterns("secret-token\nforbidden-dir\n");
        // (c) An added line starting with `++ b/fake.rs` inside a hunk is counted as hunk body, not a header.
        // (d) A quoted file path `+++ "b/..."` is unquoted and scanned.
        // (f) A matching path is reported as `(a path)` at line 0, never leaking the path string.
        let patch = "\
diff --git a/src/lib.rs b/src/lib.rs
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -1,0 +1,3 @@
+++ b/fake.rs
+let leak = \"secret-token\";
+let clean = 1;
diff --git \"a/docs/\\303\\251tude.md\" \"b/docs/\\303\\251tude.md\"
--- /dev/null
+++ \"b/docs/\\303\\251tude.md\"
@@ -0,0 +1 @@
+contains secret-token here
diff --git a/forbidden-dir/note.txt b/forbidden-dir/note.txt
--- /dev/null
+++ b/forbidden-dir/note.txt
@@ -0,0 +1 @@
+plain line
";
        let mut findings = Vec::new();
        scan_unified_patch(&patterns, patch, &mut findings);
        scan_paths(&patterns, &["forbidden-dir/other.txt".to_string()], &mut findings);
        assert_eq!(
            findings,
            vec![
                Finding {
                    file: "src/lib.rs".to_string(),
                    line: 2,
                    entry: 1,
                },
                Finding {
                    file: "docs/\u{00e9}tude.md".to_string(),
                    line: 1,
                    entry: 1,
                },
                Finding {
                    file: "(a path)".to_string(),
                    line: 0,
                    entry: 2,
                },
            ]
        );
        for f in &findings {
            assert!(!f.file.contains("forbidden-dir"), "matched path leaked in finding: {f:?}");
        }
    }
}
