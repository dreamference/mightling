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

/// Scans a list of touched file paths and appends findings with `line: 0`.
pub fn scan_paths(patterns: &[DenylistPattern], paths: &[String], findings: &mut Vec<Finding>) {
    for path in paths {
        for entry in matching_entries(patterns, path) {
            push_unique(
                findings,
                Finding {
                    file: path.clone(),
                    line: 0,
                    entry,
                },
            );
        }
    }
}

/// Scans unified diff output (`git show --format= --unified=0 --no-renames <sha>`) for matches in
/// touched paths and added lines, recording the 1-based line number in the new file.
pub fn scan_unified_patch(patterns: &[DenylistPattern], patch: &str, findings: &mut Vec<Finding>) {
    let mut current_file: Option<String> = None;
    let mut new_line: usize = 0;

    for raw_line in patch.lines() {
        if let Some(rest) = raw_line.strip_prefix("+++ b/") {
            let file = rest.trim().to_string();
            for entry in matching_entries(patterns, &file) {
                push_unique(
                    findings,
                    Finding {
                        file: file.clone(),
                        line: 0,
                        entry,
                    },
                );
            }
            current_file = Some(file);
            new_line = 0;
            continue;
        }
        if raw_line.starts_with("--- ") || raw_line.starts_with("diff --git ") {
            continue;
        }
        if let Some(hunk) = raw_line.strip_prefix("@@ ") {
            new_line = parse_hunk_new_start(hunk).unwrap_or(0);
            continue;
        }
        let Some(ref file) = current_file else {
            continue;
        };
        if new_line == 0 {
            continue;
        }
        if let Some(added) = raw_line.strip_prefix('+') {
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
            new_line += 1;
        } else if raw_line.starts_with(' ') {
            new_line += 1;
        }
    }
}

fn parse_hunk_new_start(after_at: &str) -> Option<usize> {
    let plus_idx = after_at.find('+')?;
    let rest = &after_at[plus_idx + 1..];
    let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
    digits.parse::<usize>().ok()
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
}
