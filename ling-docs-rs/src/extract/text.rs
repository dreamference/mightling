//! Text formats (spec §7.1): a document becomes located units, by structure first.
//!
//! Markdown and reStructuredText follow the Phase 0 extractor (`eval/evallib.py`) line for line,
//! since its chunks are what §15 measured; Org and LaTeX add their own heading syntax; plain text
//! is cut into 40-line blocks. The locator is the line range, 1-based and inclusive, counted by
//! `\n` as an editor counts (Python's `splitlines` also breaks at form feeds).

use super::Unit;

/// The lines of a text, as `str::lines` gives them.
fn lines(text: &str) -> Vec<&str> {
    text.lines().collect()
}

fn unit(first: usize, last: usize, heading: String, buf: &[&str]) -> Unit {
    Unit { loc: format!("lines {first}-{last}"), page: None, line_first: Some(first as u32), line_last: Some(last as u32), heading, text: buf.join("\n") }
}

fn has_text(buf: &[&str]) -> bool {
    buf.iter().any(|l| !l.trim().is_empty())
}

/// Markdown: a unit per ATX section (`#` to `######`), the heading path kept.
pub fn markdown(text: &str) -> Vec<Unit> {
    let mut units = Vec::new();
    let mut heading: Vec<String> = Vec::new();
    let mut start = 1;
    let mut buf: Vec<&str> = Vec::new();
    let all = lines(text);
    let mut in_fence = false;
    for (i, line) in all.iter().enumerate() {
        let n = i + 1;
        let trimmed = line.trim_start();
        if trimmed.starts_with("```") || trimmed.starts_with("~~~") {
            in_fence = !in_fence;
        }
        if !in_fence {
            if let Some((level, title)) = atx_heading(line) {
                if has_text(&buf) {
                    units.push(unit(start, n - 1, heading.join(" > "), &buf));
                }
                heading.truncate(level - 1);
                heading.push(title.to_string());
                start = n;
                buf = vec![line];
                continue;
            }
        }
        buf.push(line);
    }
    if has_text(&buf) {
        units.push(unit(start, all.len(), heading.join(" > "), &buf));
    }
    units
}

/// `^(#{1,6})\s+(.*)`: the level and the trimmed title.
fn atx_heading(line: &str) -> Option<(usize, &str)> {
    let level = line.chars().take_while(|c| *c == '#').count();
    if !(1..=6).contains(&level) {
        return None;
    }
    let rest = &line[level..];
    if !rest.starts_with(|c: char| c.is_whitespace()) {
        return None;
    }
    Some((level, rest.trim()))
}

/// reStructuredText: a unit per section, a section title being a line underlined by a run of one
/// punctuation character, three or more long.
pub fn rst(text: &str) -> Vec<Unit> {
    let all = lines(text);
    let mut units = Vec::new();
    let mut heading = String::new();
    let mut start = 1;
    let mut buf: Vec<&str> = Vec::new();
    let mut i = 0;
    while i < all.len() {
        if i + 1 < all.len() && !all[i].trim().is_empty() && underline(all[i + 1].trim()) {
            if has_text(&buf) {
                units.push(unit(start, i, heading.clone(), &buf));
            }
            heading = all[i].trim().to_string();
            start = i + 1;
            buf = vec![all[i]];
            i += 2;
            continue;
        }
        buf.push(all[i]);
        i += 1;
    }
    if has_text(&buf) {
        units.push(unit(start, all.len(), heading, &buf));
    }
    units
}

fn underline(line: &str) -> bool {
    let mut chars = line.chars();
    let Some(first) = chars.next() else { return false };
    "=-~^\"'`#*+".contains(first) && line.chars().count() >= 3 && line.chars().all(|c| c == first)
}

/// Org: a unit per headline (`*`, `**`, … followed by a space), the outline path kept.
pub fn org(text: &str) -> Vec<Unit> {
    let mut units = Vec::new();
    let mut heading: Vec<String> = Vec::new();
    let mut start = 1;
    let mut buf: Vec<&str> = Vec::new();
    let all = lines(text);
    for (i, line) in all.iter().enumerate() {
        let n = i + 1;
        let level = line.chars().take_while(|c| *c == '*').count();
        if level > 0 && line[level..].starts_with(' ') {
            if has_text(&buf) {
                units.push(unit(start, n - 1, heading.join(" > "), &buf));
            }
            heading.truncate(level - 1);
            heading.push(line[level..].trim().to_string());
            start = n;
            buf = vec![line];
            continue;
        }
        buf.push(line);
    }
    if has_text(&buf) {
        units.push(unit(start, all.len(), heading.join(" > "), &buf));
    }
    units
}

/// LaTeX: a unit per `\chapter`, `\section`, `\subsection` or `\subsubsection` (starred or not).
pub fn tex(text: &str) -> Vec<Unit> {
    const LEVELS: &[&str] = &["\\chapter", "\\section", "\\subsection", "\\subsubsection"];
    let mut units = Vec::new();
    let mut heading: Vec<String> = Vec::new();
    let mut start = 1;
    let mut buf: Vec<&str> = Vec::new();
    let all = lines(text);
    for (i, line) in all.iter().enumerate() {
        let n = i + 1;
        let trimmed = line.trim_start();
        let found = LEVELS.iter().enumerate().find_map(|(level, cmd)| {
            let rest = trimmed.strip_prefix(cmd)?;
            let rest = rest.strip_prefix('*').unwrap_or(rest);
            let rest = rest.strip_prefix('{')?;
            let title = rest.split('}').next().unwrap_or(rest).trim().to_string();
            Some((level + 1, title))
        });
        if let Some((level, title)) = found {
            if has_text(&buf) {
                units.push(unit(start, n - 1, heading.join(" > "), &buf));
            }
            heading.truncate(level - 1);
            while heading.len() < level - 1 {
                heading.push(String::new());
            }
            heading.push(title);
            start = n;
            buf = vec![line];
            continue;
        }
        buf.push(line);
    }
    if has_text(&buf) {
        let path: Vec<&String> = heading.iter().filter(|h| !h.is_empty()).collect();
        units.push(unit(start, all.len(), path.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(" > "), &buf));
    }
    units
}

/// Plain text: 40-line blocks.
pub fn plain(text: &str) -> Vec<Unit> {
    const BLOCK: usize = 40;
    let all = lines(text);
    let mut units = Vec::new();
    let mut i = 0;
    while i < all.len() {
        let end = (i + BLOCK).min(all.len());
        let block = &all[i..end];
        if has_text(block) {
            units.push(unit(i + 1, end, String::new(), block));
        }
        i += BLOCK;
    }
    units
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markdown_sections_keep_their_heading_path_and_lines() {
        let text = "intro line\n# Title\npara\n## Part A\nalpha\n```\n# not a heading\n```\n## Part B\nbeta\n";
        let units = markdown(text);
        let locs: Vec<(&str, &str)> = units.iter().map(|u| (u.loc.as_str(), u.heading.as_str())).collect();
        assert_eq!(locs, [("lines 1-1", ""), ("lines 2-3", "Title"), ("lines 4-8", "Title > Part A"), ("lines 9-10", "Title > Part B")]);
        assert!(units[2].text.contains("# not a heading"));
    }

    #[test]
    fn rst_sections_follow_their_underlines() {
        let text = "PEP: 8\n\nIntroduction\n============\n\nSome text.\n\nCode lay-out\n------------\nIndent.\n";
        let units = rst(text);
        let got: Vec<(&str, &str)> = units.iter().map(|u| (u.loc.as_str(), u.heading.as_str())).collect();
        assert_eq!(got, [("lines 1-2", ""), ("lines 3-7", "Introduction"), ("lines 8-10", "Code lay-out")]);
    }

    #[test]
    fn org_tex_and_plain() {
        let units = org("* Top\ntext\n** Sub\nmore\n");
        assert_eq!(units[1].heading, "Top > Sub");
        let units = tex("\\section{Intro}\nHello\n\\subsection*{Details}\nWorld\n");
        assert_eq!(units[1].heading, "Intro > Details");
        let text: String = (1..=85).map(|i| format!("line {i}\n")).collect();
        let units = plain(&text);
        assert_eq!(units.iter().map(|u| u.loc.as_str()).collect::<Vec<_>>(), ["lines 1-40", "lines 41-80", "lines 81-85"]);
    }
}
