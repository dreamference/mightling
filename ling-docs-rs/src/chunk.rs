//! Chunking (spec §7.2): by structure first, then packed to 512 tokens with ~15% overlap.
//!
//! A port of Phase 0's `evallib.chunk`, whose chunks §15.4 measured (512 tokens best or tied for
//! every model; no answer ever split by a boundary). Consecutive small units of a document are
//! packed together up to the target; a unit longer than the target is windowed over its
//! sentence-ish pieces, each window carrying the last ~15% of the one before. Token counts are the
//! embedding model's own (XLM-R's SentencePiece vocabulary).

use crate::extract::Unit;

/// Counts tokens the way the embedding model will.
pub trait TokenCount {
    fn count(&self, text: &str) -> usize;
}

/// Whitespace words: for tests, and a stand-in where no tokenizer is loaded.
pub struct Words;

impl TokenCount for Words {
    fn count(&self, text: &str) -> usize {
        text.split_whitespace().count()
    }
}

/// A chunk, with its locator.
#[derive(Debug, Clone, PartialEq)]
pub struct Chunk {
    /// `p.7`, `p.7 .. p.8`, `lines 10-80`, `lines 10-40 .. lines 41-80`.
    pub loc: String,
    pub page_first: Option<u32>,
    pub page_last: Option<u32>,
    pub line_first: Option<u32>,
    pub line_last: Option<u32>,
    pub heading: String,
    pub text: String,
}

/// The text that is embedded for a chunk: the document's title and the chunk's heading path
/// prepended, which the stored text does not carry (§7.2).
pub fn embed_text(title: &str, chunk: &Chunk) -> String {
    embed_input(title, &chunk.heading, &chunk.text)
}

/// [`embed_text`] from the parts a stored chunk has.
pub fn embed_input(title: &str, heading: &str, text: &str) -> String {
    let head = if heading.is_empty() {
        title.to_string()
    } else {
        format!("{title} | {heading}")
    };
    format!("{head}\n{text}")
}

/// The SHA-256 of exactly what is embedded for a chunk: a chunk written again with the same key
/// keeps its vector (§7.5), so a document read again embeds only the chunks whose text changed.
pub fn text_hash(title: &str, heading: &str, text: &str) -> [u8; 32] {
    use sha2::Digest;
    sha2::Sha256::digest(embed_input(title, heading, text).as_bytes()).into()
}

/// A piece of a long unit: a sentence or a line, with the line it came from; an empty piece marks
/// a paragraph's end.
struct Piece {
    text: String,
    tokens: usize,
    line: Option<u32>,
}

/// `re.split(r"(?<=[.!?])\s+", line)`, empty parts dropped.
fn sentences(line: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = line.char_indices().peekable();
    let mut prev: Option<char> = None;
    while let Some((i, c)) = chars.next() {
        if c.is_whitespace() && matches!(prev, Some('.') | Some('!') | Some('?')) {
            let mut end = i + c.len_utf8();
            while let Some(&(j, d)) = chars.peek() {
                if !d.is_whitespace() {
                    break;
                }
                end = j + d.len_utf8();
                chars.next();
            }
            out.push(&line[start..i]);
            start = end;
            prev = None;
            continue;
        }
        prev = Some(c);
    }
    out.push(&line[start..]);
    out.into_iter().filter(|p| !p.trim().is_empty()).collect()
}

fn pieces(unit: &Unit, counter: &dyn TokenCount) -> Vec<Piece> {
    let mut out = Vec::new();
    let mut in_para = false;
    for (i, line) in unit.text.split('\n').enumerate() {
        let number = unit.line_first.map(|first| first + i as u32);
        if line.trim().is_empty() {
            if in_para {
                out.push(Piece {
                    text: String::new(),
                    tokens: 0,
                    line: number,
                });
                in_para = false;
            }
            continue;
        }
        in_para = true;
        for sentence in sentences(line) {
            out.push(Piece {
                text: sentence.to_string(),
                tokens: counter.count(sentence),
                line: number,
            });
        }
    }
    out.push(Piece {
        text: String::new(),
        tokens: 0,
        line: None,
    });
    out
}

/// What is being packed: texts, their locators, pages and lines, and the units' group.
#[derive(Default)]
struct Packing {
    texts: Vec<String>,
    tokens: usize,
    locs: Vec<String>,
    pages: Vec<u32>,
    lines: Vec<u32>,
    group: Option<u32>,
}

/// `lines 10-40`, `para 3-9`, `rows 2-21`: the word and the range.
pub fn range(loc: &str) -> Option<(&str, u32, u32)> {
    let (word, span) = loc.split_once(' ')?;
    if !matches!(word, "lines" | "para" | "rows") {
        return None;
    }
    let (a, b) = span.split_once('-')?;
    Some((word, a.parse().ok()?, b.parse().ok()?))
}

/// `sheet Budget, rows 2-21`, `msg 3, lines 40-60`: a part, then a range in it.
pub fn prefixed(loc: &str) -> Option<(&str, &str, u32, u32)> {
    let (prefix, rest) = loc.rsplit_once(", ")?;
    let (word, a, b) = range(rest)?;
    Some((prefix, word, a, b))
}

/// The part of a file a locator lies in: a message (`msg 3`), a slide (`slide 4`), a sheet
/// (`sheet Budget`). Chunks of different parts are never joined.
pub fn part_of(loc: &str) -> Option<&str> {
    let loc = loc.split(" .. ").next().unwrap_or(loc);
    for keyword in ["msg ", "slide "] {
        if let Some(rest) = loc.strip_prefix(keyword) {
            let digits = rest
                .find(|c: char| !c.is_ascii_digit())
                .unwrap_or(rest.len());
            return (digits > 0).then(|| &loc[..keyword.len() + digits]);
        }
    }
    if loc.starts_with("sheet ") {
        return Some(prefixed(loc).map(|(prefix, ..)| prefix).unwrap_or(loc));
    }
    None
}

/// Whether two locators may be joined into one result: not when they lie in different parts
/// (messages of a mailbox, slides, sheets).
pub fn same_message(a: &str, b: &str) -> bool {
    part_of(a) == part_of(b)
}

/// The locator of a run of consecutive units or chunks, from its first and last: one range when
/// both are ranges of the same lines (`lines 1-80`, `sheet Budget, rows 2-41`), the part when
/// both are in one (`msg 3`), else `first .. last`.
pub fn join_locs(first: &str, last: &str) -> String {
    if first == last {
        return first.to_string();
    }
    if let (Some((w1, a, _)), Some((w2, _, b))) = (range(first), range(last)) {
        if w1 == w2 && a <= b {
            return format!("{w1} {a}-{b}");
        }
    }
    if let (Some((p1, w1, a, _)), Some((p2, w2, _, b))) = (prefixed(first), prefixed(last)) {
        if p1 == p2 && w1 == w2 && a <= b {
            return format!("{p1}, {w1} {a}-{b}");
        }
    }
    if let (Some(m1), Some(m2)) = (part_of(first), part_of(last)) {
        if m1 == m2 {
            return m1.to_string();
        }
    }
    format!("{first} .. {last}")
}

impl Packing {
    fn emit(&mut self, heading: &str, out: &mut Vec<Chunk>) {
        let text = self.texts.join("\n");
        if !self.texts.is_empty() && !text.trim().is_empty() {
            let loc = if self.locs.len() == 1 {
                self.locs[0].clone()
            } else {
                join_locs(&self.locs[0], &self.locs[self.locs.len() - 1])
            };
            out.push(Chunk {
                loc,
                page_first: self.pages.iter().min().copied(),
                page_last: self.pages.iter().max().copied(),
                line_first: self.lines.iter().min().copied(),
                line_last: self.lines.iter().max().copied(),
                heading: heading.to_string(),
                text,
            });
        }
        *self = Packing::default();
    }
}

/// A unit's locator for a window of its lines: the range in the unit's own word (`lines a-b`,
/// `para a-b`, `rows a-b`), a message's lines after the message (`msg 3, lines a-b`), or for
/// a page, the page.
fn window_loc(unit: &Unit, lines: &[u32]) -> String {
    match (unit.line_first, lines.iter().min(), lines.iter().max()) {
        (Some(first), Some(a), Some(b)) => match (range(&unit.loc), prefixed(&unit.loc)) {
            (Some((word, ..)), _) => format!("{word} {a}-{b}"),
            // A part's own numbering (a sheet's rows) is the lines shifted.
            (None, Some((prefix, word, start, _))) => {
                let shift = |n: u32| (n + start).saturating_sub(first);
                format!("{prefix}, {word} {}-{}", shift(*a), shift(*b))
            }
            (None, None) => format!("{}, lines {a}-{b}", unit.loc),
        },
        _ => unit.loc.clone(),
    }
}

/// Chunks a document's units to `target` tokens with `overlap` carried over.
pub fn chunk(units: &[Unit], counter: &dyn TokenCount, target: usize, overlap: f64) -> Vec<Chunk> {
    let mut out = Vec::new();
    let mut cur = Packing::default();
    let mut heading = String::new();
    for unit in units {
        let tokens = counter.count(&unit.text);
        if !cur.texts.is_empty() && (cur.tokens + tokens > target || unit.group != cur.group) {
            cur.emit(&heading, &mut out);
        }
        cur.group = unit.group;
        if !unit.heading.is_empty() {
            heading = unit.heading.clone();
        }
        if tokens <= target {
            cur.texts.push(unit.text.clone());
            cur.tokens += tokens;
            cur.locs.push(unit.loc.clone());
            cur.pages.extend(unit.page);
            cur.lines.extend(unit.line_first);
            cur.lines.extend(unit.line_last);
            continue;
        }
        // A long unit: windows over its pieces.
        let mut window: Vec<Piece> = Vec::new();
        let mut window_tokens = 0;
        let flush = |window: &[Piece], out: &mut Vec<Chunk>, heading: &str| {
            let lines: Vec<u32> = window.iter().filter_map(|p| p.line).collect();
            let mut packing = Packing {
                texts: window.iter().map(|p| p.text.clone()).collect(),
                tokens: 0,
                locs: vec![window_loc(unit, &lines)],
                pages: unit.page.into_iter().collect(),
                lines,
                group: unit.group,
            };
            packing.emit(heading, out);
        };
        for piece in pieces(unit, counter) {
            if window_tokens + piece.tokens > target && !window.is_empty() {
                flush(&window, &mut out, &heading);
                let mut keep: Vec<Piece> = Vec::new();
                let mut kept = 0;
                for q in window.drain(..).rev() {
                    if (kept + q.tokens) as f64 > target as f64 * overlap {
                        break;
                    }
                    kept += q.tokens;
                    keep.insert(0, q);
                }
                window = keep;
                window_tokens = kept;
            }
            window_tokens += piece.tokens;
            window.push(piece);
        }
        flush(&window, &mut out, &heading);
    }
    cur.emit(&heading, &mut out);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit(loc: &str, first: u32, text: &str) -> Unit {
        let n = text.split('\n').count() as u32;
        Unit {
            loc: loc.into(),
            page: None,
            line_first: Some(first),
            line_last: Some(first + n - 1),
            heading: String::new(),
            group: None,
            text: text.into(),
        }
    }

    #[test]
    fn small_units_are_packed_to_the_target() {
        let units = vec![
            unit("lines 1-1", 1, "a b c"),
            unit("lines 2-2", 2, "d e f"),
            unit("lines 3-3", 3, "g h i j"),
        ];
        let chunks = chunk(&units, &Words, 6, 0.15);
        assert_eq!(chunks.len(), 2);
        assert_eq!(chunks[0].loc, "lines 1-2");
        assert_eq!(
            (chunks[0].line_first, chunks[0].line_last),
            (Some(1), Some(2))
        );
        assert_eq!(chunks[1].text, "g h i j");
    }

    #[test]
    fn a_long_unit_is_windowed_with_overlap_and_line_locators() {
        let text: String = (1..=30)
            .map(|i| format!("Sentence number {i} is here."))
            .collect::<Vec<_>>()
            .join("\n");
        let units = vec![unit("lines 10-39", 10, &text)];
        let chunks = chunk(&units, &Words, 50, 0.15);
        assert!(chunks.len() >= 3, "{chunks:?}");
        // Each window starts with the tail of the one before.
        let last_of_first = chunks[0].text.lines().last().unwrap();
        assert!(
            chunks[1].text.starts_with(last_of_first),
            "{:?}",
            chunks[1].text
        );
        assert_eq!(chunks[0].line_first, Some(10));
        assert!(chunks[0].loc.starts_with("lines 10-"));
        assert_eq!(chunks.last().unwrap().line_last, Some(39));
    }

    #[test]
    fn a_message_is_never_packed_with_another_and_its_windows_name_it() {
        let mut units = vec![
            unit("msg 1", 1, "a b"),
            unit("msg 1 · notes.txt", 2, "c d"),
            unit("msg 2", 3, "e f"),
        ];
        units[0].group = Some(1);
        units[1].group = Some(1);
        units[2].group = Some(2);
        let chunks = chunk(&units, &Words, 100, 0.15);
        let locs: Vec<&str> = chunks.iter().map(|c| c.loc.as_str()).collect();
        assert_eq!(locs, ["msg 1", "msg 2"]);
        let text: String = (1..=30)
            .map(|i| format!("Sentence number {i} is here."))
            .collect::<Vec<_>>()
            .join("\n");
        let mut long = unit("msg 4", 10, &text);
        long.group = Some(4);
        let chunks = chunk(&[long], &Words, 50, 0.15);
        assert!(
            chunks[0].loc.starts_with("msg 4, lines 10-"),
            "{}",
            chunks[0].loc
        );
        assert_eq!(join_locs("para 1-4", "para 5-9"), "para 1-9");
        assert_eq!(join_locs("p.1", "p.2"), "p.1 .. p.2");
        assert_eq!(
            join_locs("msg 12, lines 3-9", "msg 12 · a.pdf p.2"),
            "msg 12"
        );
        assert_eq!(join_locs("msg 1", "msg 12"), "msg 1 .. msg 12");
        assert_eq!(
            join_locs("sheet Q1, Q2, rows 1-21", "sheet Q1, Q2, rows 22-41"),
            "sheet Q1, Q2, rows 1-41"
        );
        assert_eq!(join_locs("slide 3", "slide 3, lines 9-12"), "slide 3");
        assert!(
            !same_message("sheet A, rows 1-21", "sheet B, rows 1-21")
                && same_message("slide 2", "slide 2 .. slide 2")
        );
        // A long row group of a sheet is windowed in the sheet's own rows.
        let text: String = (1..=30)
            .map(|i| format!("Row {i} has a long value here."))
            .collect::<Vec<_>>()
            .join("\n");
        let mut sheet = unit("sheet Budget, rows 2-31", 101, &text);
        sheet.line_last = Some(130);
        let chunks = chunk(&[sheet], &Words, 50, 0.15);
        assert!(
            chunks[0].loc.starts_with("sheet Budget, rows 2-"),
            "{}",
            chunks[0].loc
        );
        assert!(
            chunks.last().unwrap().loc.ends_with("-31"),
            "{}",
            chunks.last().unwrap().loc
        );
    }

    #[test]
    fn sentences_split_after_terminal_punctuation_only() {
        assert_eq!(
            sentences("One. Two!  Three? four e.g.five"),
            ["One.", "Two!", "Three?", "four e.g.five"]
        );
        assert_eq!(sentences("   "), Vec::<&str>::new());
    }

    #[test]
    fn the_heading_path_is_embedded_but_not_stored() {
        let c = Chunk {
            loc: "p.1".into(),
            page_first: Some(1),
            page_last: Some(1),
            line_first: None,
            line_last: None,
            heading: "A > B".into(),
            text: "body".into(),
        };
        assert_eq!(embed_text("doc", &c), "doc | A > B\nbody");
    }
}
