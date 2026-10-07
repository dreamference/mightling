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
/// At most `max_files` files and `max_bytes` bytes of text are read; above either, nothing is
/// searched and `over_limit` says why, so the caller prints the mandatory `not checked` line.
/// A binary file (a NUL in its first 8 KiB) holds no reference and does not count against the
/// bytes: a submodule of papers with its PDFs would otherwise put every answer over the bound.
pub fn scan(root: &Path, files: &[String], name: &str, max_files: usize, max_bytes: u64) -> Scan {
    if name.is_empty() || files.is_empty() {
        return Scan::default();
    }
    if files.len() > max_files {
        return Scan { over_limit: Some(format!("{} files, above the {max_files}-file bound", files.len())), ..Scan::default() };
    }
    let text_files: Vec<(&String, u64)> = files
        .iter()
        .filter_map(|file| {
            let path = root.join(file);
            let size = std::fs::metadata(&path).ok().filter(|m| m.is_file())?.len();
            (!is_binary(&path)).then_some((file, size))
        })
        .collect();
    let total: u64 = text_files.iter().map(|(_, size)| size).sum();
    if total > max_bytes {
        return Scan { over_limit: Some(format!("{} MiB, above the {} MiB bound", total >> 20, max_bytes >> 20)), ..Scan::default() };
    }
    let mut result = Scan::default();
    for (file, _) in text_files {
        let Ok(bytes) = std::fs::read(root.join(file)) else { continue };
        result.files_searched += 1;
        let text = String::from_utf8_lossy(&bytes);
        for (index, line) in text.lines().enumerate() {
            for column in word_matches(line, name) {
                result.hits.push(Hit { path: file.clone(), line: index as u32 + 1, column: column as u32 + 1 });
            }
        }
    }
    result
}

/// Whether a file's first 8 KiB hold a NUL byte. An unreadable file is treated as text, so it is
/// still counted and attempted.
fn is_binary(path: &Path) -> bool {
    use std::io::Read;
    let mut head = [0u8; 8192];
    let Ok(mut file) = std::fs::File::open(path) else { return false };
    let mut filled = 0;
    while filled < head.len() {
        match file.read(&mut head[filled..]) {
            Ok(0) | Err(_) => break,
            Ok(n) => filled += n,
        }
    }
    head[..filled].contains(&0)
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
    use super::{scan, word_matches};

    #[test]
    fn binary_files_do_not_count_against_the_byte_bound() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("paper.pdf"), [b"%PDF-1.7\0".as_slice(), &vec![7u8; 4096]].concat()).unwrap();
        std::fs::write(dir.path().join("a.py"), "x = area()\n").unwrap();
        let files = vec!["paper.pdf".to_string(), "a.py".to_string(), "gone.py".to_string()];
        // 4 KiB of PDF beside 11 bytes of text, under a 1 KiB bound: the text is still searched.
        let found = scan(dir.path(), &files, "area", 10, 1024);
        assert_eq!(found.over_limit, None);
        assert_eq!((found.hits.len(), found.files_searched), (1, 1));
        // Text above the bound is not searched, and says so.
        assert!(scan(dir.path(), &files, "area", 10, 5).over_limit.is_some());
    }

    #[test]
    fn whole_words_only() {
        assert_eq!(word_matches("area() + square.area() + areas + _area", "area"), vec![0, 16]);
        assert_eq!(word_matches("make_circle(1)", "circle"), Vec::<usize>::new());
        assert_eq!(word_matches("x", ""), Vec::<usize>::new());
    }
}
