//! The whole-word search over the files changed since a snapshot (spec §7.3): the only step that
//! can see a reference added after the snapshot. It over-reports (another symbol of the same name
//! matches too), which is the safe direction; the rows it produces say `heuristic (text)`.

use std::path::Path;

/// A match: 1-based line and column.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    pub path: String,
    pub line: u32,
    pub column: u32,
}

/// The outcome of one search.
#[derive(Debug, Clone, Default)]
pub struct Scan {
    pub hits: Vec<Hit>,
    pub files_searched: usize,
    /// Set when the files exceeded the bounds, in which case nothing was searched.
    pub over_limit: Option<String>,
}

/// Searches `files` (repository-relative, under `root`) for `name` as a whole word.
///
/// At most `max_files` files and `max_bytes` bytes are read; above either, nothing is searched and
/// `over_limit` says why, so the caller prints the mandatory `not checked` line.
pub fn scan(root: &Path, files: &[String], name: &str, max_files: usize, max_bytes: u64) -> Scan {
    if name.is_empty() || files.is_empty() {
        return Scan::default();
    }
    if files.len() > max_files {
        return Scan { over_limit: Some(format!("{} files, above the {max_files}-file bound", files.len())), ..Scan::default() };
    }
    let total: u64 = files.iter().filter_map(|f| std::fs::metadata(root.join(f)).ok()).map(|m| m.len()).sum();
    if total > max_bytes {
        return Scan { over_limit: Some(format!("{} MiB, above the {} MiB bound", total >> 20, max_bytes >> 20)), ..Scan::default() };
    }
    let mut result = Scan::default();
    for file in files {
        let Ok(bytes) = std::fs::read(root.join(file)) else { continue };
        result.files_searched += 1;
        if bytes[..bytes.len().min(8192)].contains(&0) {
            continue;
        }
        let text = String::from_utf8_lossy(&bytes);
        for (index, line) in text.lines().enumerate() {
            for column in word_matches(line, name) {
                result.hits.push(Hit { path: file.clone(), line: index as u32 + 1, column: column as u32 + 1 });
            }
        }
    }
    result
}

/// Byte offsets of `name` in `line` where it is a whole word.
pub fn word_matches(line: &str, name: &str) -> Vec<usize> {
    let is_word = |c: char| c.is_alphanumeric() || c == '_';
    let mut out = Vec::new();
    let mut from = 0;
    while let Some(found) = line[from..].find(name) {
        let start = from + found;
        let end = start + name.len();
        let before = line[..start].chars().next_back();
        let after = line[end..].chars().next();
        if !before.map(is_word).unwrap_or(false) && !after.map(is_word).unwrap_or(false) {
            out.push(start);
        }
        from = start + name.len().max(1);
        if from >= line.len() {
            break;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::word_matches;

    #[test]
    fn whole_words_only() {
        assert_eq!(word_matches("area() + square.area() + areas + _area", "area"), vec![0, 16]);
        assert_eq!(word_matches("make_circle(1)", "circle"), Vec::<usize>::new());
        assert_eq!(word_matches("x", ""), Vec::<usize>::new());
    }
}
