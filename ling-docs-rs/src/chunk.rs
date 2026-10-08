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
    let head = if chunk.heading.is_empty() { title.to_string() } else { format!("{title} | {}", chunk.heading) };
    format!("{head}\n{}", chunk.text)
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
                out.push(Piece { text: String::new(), tokens: 0, line: number });
                in_para = false;
            }
            continue;
        }
        in_para = true;
        for sentence in sentences(line) {
            out.push(Piece { text: sentence.to_string(), tokens: counter.count(sentence), line: number });
        }
    }
    out.push(Piece { text: String::new(), tokens: 0, line: None });
    out
}

/// What is being packed: texts, their locators, pages and lines.
#[derive(Default)]
struct Packing {
    texts: Vec<String>,
    tokens: usize,
    locs: Vec<String>,
    pages: Vec<u32>,
    lines: Vec<u32>,
}

impl Packing {
    fn emit(&mut self, heading: &str, out: &mut Vec<Chunk>) {
        let text = self.texts.join("\n");
        if !self.texts.is_empty() && !text.trim().is_empty() {
            let loc = if self.locs.len() == 1 { self.locs[0].clone() } else { format!("{} .. {}", self.locs[0], self.locs[self.locs.len() - 1]) };
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

/// A unit's locator for a window of its lines: `lines a-b` for line-located units, else its own.
fn window_loc(unit: &Unit, lines: &[u32]) -> String {
    match (unit.line_first, lines.iter().min(), lines.iter().max()) {
        (Some(_), Some(a), Some(b)) => format!("lines {a}-{b}"),
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
        if cur.tokens + tokens > target && !cur.texts.is_empty() {
            cur.emit(&heading, &mut out);
        }
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
        Unit { loc: loc.into(), page: None, line_first: Some(first), line_last: Some(first + n - 1), heading: String::new(), text: text.into() }
    }

    #[test]
    fn small_units_are_packed_to_the_target() {
        let units = vec![unit("lines 1-1", 1, "a b c"), unit("lines 2-2", 2, "d e f"), unit("lines 3-3", 3, "g h i j")];
        let chunks = chunk(&units, &Words, 6, 0.15);
        assert_eq!(chunks.len(), 2);
        assert_eq!(chunks[0].loc, "lines 1-1 .. lines 2-2");
        assert_eq!((chunks[0].line_first, chunks[0].line_last), (Some(1), Some(2)));
        assert_eq!(chunks[1].text, "g h i j");
    }

    #[test]
    fn a_long_unit_is_windowed_with_overlap_and_line_locators() {
        let text: String = (1..=30).map(|i| format!("Sentence number {i} is here.")).collect::<Vec<_>>().join("\n");
        let units = vec![unit("lines 10-39", 10, &text)];
        let chunks = chunk(&units, &Words, 50, 0.15);
        assert!(chunks.len() >= 3, "{chunks:?}");
        // Each window starts with the tail of the one before.
        let last_of_first = chunks[0].text.lines().last().unwrap();
        assert!(chunks[1].text.starts_with(last_of_first), "{:?}", chunks[1].text);
        assert_eq!(chunks[0].line_first, Some(10));
        assert!(chunks[0].loc.starts_with("lines 10-"));
        assert_eq!(chunks.last().unwrap().line_last, Some(39));
    }

    #[test]
    fn sentences_split_after_terminal_punctuation_only() {
        assert_eq!(sentences("One. Two!  Three? four e.g.five"), ["One.", "Two!", "Three?", "four e.g.five"]);
        assert_eq!(sentences("   "), Vec::<&str>::new());
    }

    #[test]
    fn the_heading_path_is_embedded_but_not_stored() {
        let c = Chunk { loc: "p.1".into(), page_first: Some(1), page_last: Some(1), line_first: None, line_last: None, heading: "A > B".into(), text: "body".into() };
        assert_eq!(embed_text("doc", &c), "doc | A > B\nbody");
    }
}
