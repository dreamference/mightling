//! Repetition-loop detection on streamed model text (docs/dev/loop-guard.md).
//!
//! A model that falls into a loop repeats a block of text until something stops it: on
//! 2026-10-20 the served Qwen3.8-27B decoded about 72,000 tokens in 15 minutes at a draft
//! acceptance of 0.95 to 0.99, the signature of exact repetition. [`Detector`] watches text as it
//! streams and reports a loop with three checks, so that one check's blind spot is covered by
//! another:
//!
//! - **periodic suffix**: the last pieces are one block of `min_pattern_size..=max_pattern_size`
//!   pieces repeated at least `min_count` times back to back (exact loops, short or long);
//! - **compression**: the last `compression_window` bytes deflate to less than
//!   `min_compression_ratio` of their size (near loops, whose copies differ in a number or a word,
//!   which no exact match sees);
//! - **runs**: one piece repeated `max_run` times, or one character `max_char_run` times
//!   (single-character runs such as `!!!!` or a page of newlines, which substring checks miss
//!   because the run is one ever-growing piece).
//!
//! A *piece* stands in for a token: text is split as the Qwen tokenizer's pre-tokenizer splits it
//! (a word with its leading space or punctuation mark, one digit, a run of punctuation, a run of
//! whitespace), which counts close to the server's tokens without the vocabulary. The threshold
//! names are the server-side rule's (`repetition_detection` in ling-engine, after vLLM's), so one
//! configuration can drive both; here they count pieces, there tokens.
//!
//! The thresholds were set on the normal outputs of every SWE-bench night and every saved session
//! (`tests/corpus.rs`; the counts are in docs/dev/loop-guard.md).

use std::hash::Hasher;
use std::io::Write;

/// What counts as a loop. Field names follow the server rule's `repetition_detection`.
#[derive(Clone, Debug, PartialEq)]
pub struct Thresholds {
    /// The longest repeated block the periodic check looks for, in pieces.
    pub max_pattern_size: usize,
    /// The shortest; shorter loops are left to the run and compression checks.
    pub min_pattern_size: usize,
    /// How many back-to-back copies of the block make a loop.
    pub min_count: usize,
    /// One piece repeated this many times in a row is a loop.
    pub max_run: usize,
    /// One character repeated this many times in a row is a loop.
    pub max_char_run: usize,
    /// The bytes the compression check deflates, the most recent ones; 0 switches it off.
    pub compression_window: usize,
    /// A window that deflates below this fraction of its size is a loop.
    pub min_compression_ratio: f64,
}

impl Default for Thresholds {
    /// The defaults measured on the corpus (docs/dev/loop-guard.md).
    fn default() -> Self {
        Thresholds {
            // About 9 KB of text: a whole plan or answer repeated, not only a sentence.
            max_pattern_size: 2048,
            // Shorter blocks (a matrix row, a repeated short line) occur in normal code; a short
            // loop fills the compression window within seconds instead.
            min_pattern_size: 32,
            // Three identical rows of a matrix literal were seen in normal output; four were not.
            min_count: 4,
            // Normal output reached 40 (a run of zeros in a test value).
            max_run: 64,
            max_char_run: 400,
            // Normal output never deflated below 0.17 in any window, source files 0.19 to 0.24;
            // a loop whose block is under ~4 KB deflates below 0.08.
            compression_window: 16384,
            min_compression_ratio: 0.10,
        }
    }
}

/// Which check fired.
#[derive(Clone, Debug, PartialEq)]
pub enum Kind {
    /// `count` back-to-back copies of a block of `period` pieces.
    Periodic { period: usize, count: usize },
    /// The last window deflated to `ratio` of its size.
    Compression { ratio: f64 },
    /// One piece repeated `len` times.
    TokenRun { len: usize },
    /// One character repeated `len` times.
    CharRun { len: usize },
}

impl Kind {
    /// A short name for logs and reports.
    pub fn name(&self) -> &'static str {
        match self {
            Kind::Periodic { .. } => "periodic",
            Kind::Compression { .. } => "compression",
            Kind::TokenRun { .. } => "token-run",
            Kind::CharRun { .. } => "char-run",
        }
    }
}

/// A loop, and where the text before it ends.
#[derive(Clone, Debug, PartialEq)]
pub struct Detection {
    pub kind: Kind,
    /// Byte offset into the text pushed so far (always a character boundary): the text before it
    /// is worth keeping, the loop's first copy included, and everything after is repetition.
    pub cut: usize,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Class {
    Letter,
    Digit,
    Newline,
    Space,
    Punct,
}

fn class(c: char) -> Class {
    if c == '\n' || c == '\r' {
        Class::Newline
    } else if c.is_whitespace() {
        Class::Space
    } else if c.is_alphabetic() {
        Class::Letter
    } else if c.is_numeric() {
        Class::Digit
    } else {
        Class::Punct
    }
}

/// What the piece being built is so far.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Open {
    None,
    Letters,
    Punct,
    Spaces,
    /// Whitespace or punctuation that has reached a newline: only more newlines join it.
    Newlines,
}

/// Watches one stream of model text (one reasoning item or one answer) for a loop.
pub struct Detector {
    t: Thresholds,
    /// Everything pushed so far.
    text: String,
    /// The piece being built: its start offset and kind; its text is `text[open_start..]`.
    open: Open,
    open_start: usize,
    open_chars: usize,
    /// The last `max_pattern_size + 1` pieces: hash and start offset, a ring.
    ids: Vec<u64>,
    starts: Vec<usize>,
    pieces: usize,
    /// `matched[l]`: how many pieces in a row, ending with the latest, equal the piece `l` before.
    matched: Vec<usize>,
    run_len: usize,
    run_start: usize,
    last_char: Option<char>,
    char_run: usize,
    char_run_start: usize,
    next_compression_at: usize,
    detection: Option<Detection>,
}

impl Detector {
    pub fn new(t: Thresholds) -> Self {
        let ring = t.max_pattern_size.max(1) + 1;
        let next_compression_at = t.compression_window;
        Detector {
            ids: vec![0; ring],
            starts: vec![0; ring],
            matched: vec![0; ring],
            t,
            text: String::new(),
            open: Open::None,
            open_start: 0,
            open_chars: 0,
            pieces: 0,
            run_len: 0,
            run_start: 0,
            last_char: None,
            char_run: 0,
            char_run_start: 0,
            next_compression_at,
            detection: None,
        }
    }

    /// The pieces so far: an estimate of the tokens streamed.
    pub fn pieces(&self) -> usize {
        self.pieces + usize::from(self.open != Open::None)
    }

    /// The text pushed so far.
    pub fn text(&self) -> &str {
        &self.text
    }

    /// The first loop found, if any.
    pub fn detection(&self) -> Option<&Detection> {
        self.detection.as_ref()
    }

    /// Adds streamed text; returns the loop the moment it is first seen, and None before and after.
    pub fn push(&mut self, delta: &str) -> Option<Detection> {
        if self.detection.is_some() {
            return None;
        }
        for c in delta.chars() {
            let at = self.text.len();
            self.text.push(c);
            self.on_char(c, at);
            if self.detection.is_some() {
                return self.detection.clone();
            }
        }
        if self.t.compression_window > 0 && self.text.len() >= self.next_compression_at {
            self.next_compression_at = self.text.len() + (self.t.compression_window / 8).max(64);
            self.check_compression();
        }
        self.detection.clone()
    }

    fn on_char(&mut self, c: char, at: usize) {
        if self.last_char == Some(c) {
            self.char_run += 1;
        } else {
            self.last_char = Some(c);
            self.char_run = 1;
            self.char_run_start = at;
        }
        if self.t.max_char_run > 0 && self.char_run >= self.t.max_char_run {
            // Keep a few of the run: a separator line or an ellipsis is fine, a page of it is not.
            let keep = self.char_run_start + c.len_utf8() * 8;
            self.found(Kind::CharRun { len: self.char_run }, keep);
            return;
        }
        let k = class(c);
        match (self.open, k) {
            (Open::Letters, Class::Letter) | (Open::Punct, Class::Punct) | (Open::Spaces, Class::Space) => {
                self.open_chars += 1;
            }
            (Open::Newlines, Class::Newline) | (Open::Punct | Open::Spaces, Class::Newline) => {
                self.open = Open::Newlines;
                self.open_chars += 1;
            }
            // One space or punctuation mark joins the word after it (" word", "(word").
            (Open::Punct | Open::Spaces, Class::Letter) if self.open_chars == 1 => {
                self.open = Open::Letters;
                self.open_chars += 1;
            }
            // A longer run of spaces gives its last space to the word after it.
            (Open::Spaces, Class::Letter | Class::Punct) => {
                let last = self.text[..at].char_indices().next_back().map_or(at, |(i, _)| i);
                self.close_at(last);
                self.open = if k == Class::Letter { Open::Letters } else { Open::Punct };
                self.open_start = last;
                self.open_chars = 2;
            }
            (_, Class::Digit) => {
                self.close_at(at);
                // A digit is a piece of its own.
                self.emit(at);
                self.open = Open::None;
            }
            _ => {
                self.close_at(at);
                self.open = match k {
                    Class::Letter => Open::Letters,
                    Class::Punct => Open::Punct,
                    Class::Space => Open::Spaces,
                    Class::Newline => Open::Newlines,
                    Class::Digit => unreachable!(),
                };
                self.open_start = at;
                self.open_chars = 1;
            }
        }
    }

    /// Ends the open piece at `end` (exclusive), if there is one.
    fn close_at(&mut self, end: usize) {
        if self.open != Open::None && end > self.open_start {
            let start = self.open_start;
            self.emit_range(start, end);
        }
        self.open = Open::None;
    }

    /// Emits the one-character piece starting at `at`.
    fn emit(&mut self, at: usize) {
        let end = at + self.text[at..].chars().next().map_or(0, char::len_utf8);
        self.emit_range(at, end);
    }

    fn emit_range(&mut self, start: usize, end: usize) {
        let mut hasher = std::hash::DefaultHasher::new();
        hasher.write(self.text[start..end].as_bytes());
        let id = hasher.finish();
        let ring = self.ids.len();
        let n = self.pieces;
        let slot = n % ring;
        self.ids[slot] = id;
        self.starts[slot] = start;
        self.pieces += 1;

        if n > 0 && self.ids[(n - 1) % ring] == id {
            self.run_len += 1;
        } else {
            self.run_len = 1;
            self.run_start = start;
        }
        if self.t.max_run > 0 && self.run_len >= self.t.max_run {
            let keep = self.run_start;
            self.found(Kind::TokenRun { len: self.run_len }, keep);
            return;
        }

        let max = self.t.max_pattern_size.min(ring - 1);
        let min = self.t.min_pattern_size.max(1);
        let needed = self.t.min_count.max(2) - 1;
        let mut hit: Option<(usize, usize)> = None;
        for l in 1..=max {
            if n >= l && self.ids[(n - l) % ring] == id {
                self.matched[l] += 1;
                if hit.is_none() && l >= min && self.matched[l] >= l * needed {
                    hit = Some((l, self.matched[l]));
                }
            } else {
                self.matched[l] = 0;
            }
        }
        // A span that repeats with a period shorter than the shortest pattern repeats with every
        // multiple of it too (`0.0, ` makes a 15-piece "block" of three copies): that is a short
        // loop, left to the run and compression checks, not a long one.
        if let Some((period, _)) = hit
            && (1..min).any(|d| self.matched[d] >= period * needed)
        {
            hit = None;
        }
        if let Some((period, matched)) = hit {
            // The repetition began `matched` pieces ago with its second copy: keep the first.
            let second = n + 1 - matched;
            let cut = self.starts[second % ring];
            self.found(Kind::Periodic { period, count: matched / period + 1 }, cut);
        }
    }

    fn check_compression(&mut self) {
        let w = self.t.compression_window;
        let mut from = self.text.len() - w;
        while !self.text.is_char_boundary(from) {
            from += 1;
        }
        let window = &self.text.as_bytes()[from..];
        let mut encoder = flate2::write::DeflateEncoder::new(Vec::new(), flate2::Compression::fast());
        if encoder.write_all(window).is_err() {
            return;
        }
        let Ok(out) = encoder.finish() else { return };
        let ratio = out.len() as f64 / window.len() as f64;
        if ratio < self.t.min_compression_ratio {
            self.found(Kind::Compression { ratio }, from);
        }
    }

    fn found(&mut self, kind: Kind, cut: usize) {
        if self.detection.is_none() {
            let mut cut = cut.min(self.text.len());
            while !self.text.is_char_boundary(cut) {
                cut -= 1;
            }
            self.detection = Some(Detection { kind, cut });
        }
    }
}

/// Runs a whole text through a fresh detector, in small slices as a stream would bring it.
pub fn scan(text: &str, t: &Thresholds) -> Option<Detection> {
    let mut detector = Detector::new(t.clone());
    let mut rest = text;
    while !rest.is_empty() {
        let mut take = rest.len().min(7);
        while !rest.is_char_boundary(take) {
            take += 1;
        }
        let (now, later) = rest.split_at(take);
        if let Some(found) = detector.push(now) {
            return Some(found);
        }
        rest = later;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn defaults() -> Thresholds {
        Thresholds::default()
    }

    fn pieces(text: &str) -> Vec<String> {
        let mut d = Detector::new(Thresholds { max_run: 0, max_char_run: 0, compression_window: 0, ..defaults() });
        d.push(text);
        let ring = d.ids.len();
        let n = d.pieces;
        let mut out = Vec::new();
        for i in n.saturating_sub(ring)..n {
            let start = d.starts[i % ring];
            let end = if i + 1 < n { d.starts[(i + 1) % ring] } else { d.open_start.max(start) };
            out.push(d.text[start..end].to_string());
        }
        out
    }

    #[test]
    fn text_splits_into_pieces_as_the_tokenizer_would() {
        assert_eq!(pieces("Hello world, 42 times.\n    x"), vec!["Hello", " world", ",", " ", "4", "2", " times", ".\n", "   "]);
    }

    #[test]
    fn a_paragraph_repeated_back_to_back_is_a_periodic_loop() {
        let block = "Wait, let me reconsider the approach. Actually the function returns early when the list is \
            empty, so the second branch never runs, which means the test cannot reach it either; ";
        let text = format!("Let me think. {}", block.repeat(6));
        let found = scan(&text, &defaults()).expect("a loop");
        assert!(matches!(found.kind, Kind::Periodic { count: 4, .. }), "{found:?}");
        // The first copy is kept (its trailing space starts the next piece), the rest is cut.
        assert_eq!(&text[..found.cut], format!("Let me think. {}", block.trim_end()));
    }

    #[test]
    fn a_long_block_repeated_is_caught_even_far_above_the_shortest_pattern() {
        let block: String = (0..120).map(|i| format!("word{} ", i % 37)).collect();
        let found = scan(&block.repeat(5), &defaults()).expect("a loop");
        assert!(matches!(found.kind, Kind::Periodic { .. }), "{found:?}");
    }

    #[test]
    fn a_near_loop_whose_copies_differ_is_caught_by_compression() {
        let mut text = String::new();
        for i in 0..400 {
            text.push_str(&format!("Step {i}: check whether the value is cached, and if it is not, compute it again and store it.\n"));
        }
        let found = scan(&text, &defaults()).expect("a loop");
        assert_eq!(found.kind.name(), "compression", "{found:?}");
    }

    #[test]
    fn a_short_sentence_loop_is_caught_once_the_compression_window_fills() {
        let line = "I will check the file again. ";
        let text = line.repeat(1000);
        let found = scan(&text, &defaults()).expect("a loop");
        assert_eq!(found.kind.name(), "compression", "{found:?}");
        // Within the first window and a check interval of it.
        assert!(found.cut + 16384 <= 16384 + 2048 + line.len(), "{found:?}");
    }

    #[test]
    fn single_character_runs_are_caught_though_they_make_one_piece() {
        let text = format!("The answer is {}", "!".repeat(1000));
        let found = scan(&text, &defaults()).expect("a loop");
        assert_eq!(found.kind, Kind::CharRun { len: 400 });
        assert_eq!(&text[..found.cut], "The answer is !!!!!!!!");
        let newlines = format!("Done.{}", "\n".repeat(600));
        assert!(matches!(scan(&newlines, &defaults()).unwrap().kind, Kind::CharRun { .. }));
    }

    #[test]
    fn one_word_repeated_is_a_token_run() {
        let text = format!("and then{}", " again".repeat(100));
        let found = scan(&text, &defaults()).expect("a loop");
        assert_eq!(found.kind, Kind::TokenRun { len: 64 });
        assert_eq!(&text[..found.cut], "and then");
    }

    #[test]
    fn normal_prose_code_and_tables_pass() {
        let prose = "The function reads the configuration once, at start, and keeps it in a field. \
            A later change to the file is therefore not seen until the next start, which is what \
            the tests expect. If the file is missing the defaults apply, and a warning names it.";
        let code = "def area(shape):\n    if shape.kind == 'circle':\n        return math.pi * shape.r ** 2\n    \
            if shape.kind == 'square':\n        return shape.side ** 2\n    raise ValueError(shape.kind)\n";
        let table = "| name | size | kind |\n|------|------|------|\n| a.py | 120 | file |\n| b.py | 98 | file |\n| tests | 12 | dir |\n";
        let rule = format!("{}\nSection\n{}\n", "=".repeat(79), "-".repeat(79));
        let zeros = format!("weights = [{}]\n", vec!["0.0"; 48].join(", "));
        let zero_rows = |n: usize| format!("[{}]\n", vec![format!("[{}]", vec!["0"; n].join(", ")); n].join(", "));
        let same_lines = "    self.assertEqual(response.status_code, 200)\n".repeat(8);
        let digits = format!("assert parse('1e-{}') == 0\n", "0".repeat(40));
        for text in [prose.to_string(), code.repeat(3), table.to_string(), rule, zeros, zero_rows(4), zero_rows(8), same_lines, digits] {
            assert_eq!(scan(&text, &defaults()), None, "{text}");
        }
    }

    #[test]
    fn a_stream_split_anywhere_finds_the_same_loop() {
        let block = "The cache is stale, so we rebuild it from the index and try again; if the index is stale \
            too, we rebuild that first, which takes a while. ";
        let text = format!("Intro. {}", block.repeat(8));
        let whole = {
            let mut d = Detector::new(defaults());
            d.push(&text)
        };
        assert!(whole.is_some());
        assert_eq!(scan(&text, &defaults()), whole);
        let mut byte_at_a_time = Detector::new(defaults());
        let mut found = None;
        for c in text.chars() {
            found = found.or(byte_at_a_time.push(&c.to_string()));
        }
        assert_eq!(found, whole);
    }

    #[test]
    fn a_detection_is_reported_once() {
        let mut d = Detector::new(defaults());
        assert!(d.push(&"x".repeat(500)).is_some());
        assert_eq!(d.push("more"), None);
        assert!(d.detection().is_some());
    }

    #[test]
    fn checks_switch_off_at_zero() {
        let off = Thresholds { max_run: 0, max_char_run: 0, compression_window: 0, min_count: 1000, ..defaults() };
        assert_eq!(scan(&"!".repeat(5000), &off), None);
        assert_eq!(scan(&" again".repeat(500), &off), None);
    }

    #[test]
    fn multibyte_text_cuts_on_character_boundaries() {
        let text = format!("Ответ: {}", "дальше ".repeat(80));
        let found = scan(&text, &defaults()).expect("a loop");
        assert!(text.is_char_boundary(found.cut));
        let run = format!("Да{}", "я".repeat(500));
        let found = scan(&run, &defaults()).expect("a loop");
        assert!(run.is_char_boundary(found.cut));
        // Eight of the run's characters are kept, not eight bytes.
        assert_eq!(&run[..found.cut], format!("Да{}", "я".repeat(8)));
    }
}
