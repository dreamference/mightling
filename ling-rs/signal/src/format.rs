//! The agent's Markdown as Signal text (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §9).
//!
//! Signal shows plain text with style ranges (bold, italic, strikethrough, monospace, spoiler);
//! signal-cli takes each as `start:length:STYLE`, counted in UTF-16 code units. [`render`] turns
//! Markdown into text plus ranges, and [`split`] cuts the result into messages Signal will carry,
//! closing a range at a cut and reopening it in the next part.

/// A Signal text style.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StyleKind {
    Bold,
    Italic,
    Strikethrough,
    Monospace,
}

impl StyleKind {
    pub fn name(self) -> &'static str {
        match self {
            StyleKind::Bold => "BOLD",
            StyleKind::Italic => "ITALIC",
            StyleKind::Strikethrough => "STRIKETHROUGH",
            StyleKind::Monospace => "MONOSPACE",
        }
    }
}

/// One range, in UTF-16 code units of the message text.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Style {
    pub start: usize,
    pub length: usize,
    pub kind: StyleKind,
}

impl Style {
    /// signal-cli's form: `start:length:STYLE`.
    pub fn argument(&self) -> String {
        format!("{}:{}:{}", self.start, self.length, self.kind.name())
    }
}

/// Text with its style ranges.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Styled {
    pub text: String,
    pub styles: Vec<Style>,
}

impl Styled {
    pub fn plain(text: &str) -> Styled {
        Styled { text: text.to_string(), styles: Vec::new() }
    }

    /// The text's length in UTF-16 code units, the unit Signal counts in.
    pub fn units(&self) -> usize {
        utf16_len(&self.text)
    }

    /// The style arguments signal-cli's `send` takes.
    pub fn style_arguments(&self) -> Vec<String> {
        self.styles.iter().map(Style::argument).collect()
    }
}

/// A message's length in UTF-16 code units.
pub fn utf16_len(text: &str) -> usize {
    text.chars().map(char::len_utf16).sum()
}

/// Builds text and ranges together, counting UTF-16 units as it goes.
#[derive(Default)]
struct Builder {
    text: String,
    units: usize,
    styles: Vec<Style>,
}

impl Builder {
    fn push(&mut self, text: &str) {
        self.text.push_str(text);
        self.units += utf16_len(text);
    }

    fn style(&mut self, start: usize, kind: StyleKind) {
        if self.units > start {
            self.styles.push(Style { start, length: self.units - start, kind });
        }
    }
}

/// Markdown as Signal text. Fenced blocks and tables become monospace lines, headings bold lines,
/// `-`/`*` bullets `•`, links `text (url)`; inline code, bold, italic and strikethrough become
/// ranges. Anything it does not recognise stays as it was written.
pub fn render(markdown: &str) -> Styled {
    let mut out = Builder::default();
    let mut lines = markdown.lines().peekable();
    let mut first = true;
    while let Some(line) = lines.next() {
        if !first {
            out.push("\n");
        }
        first = false;
        let trimmed = line.trim_start();
        if let Some(fence) = fence_of(trimmed) {
            // A fenced block: its lines verbatim, one monospace range, the fences dropped.
            let start = out.units;
            let mut wrote = false;
            for inner in lines.by_ref() {
                if inner.trim_start().starts_with(fence) && inner.trim().trim_start_matches(fence).is_empty() {
                    break;
                }
                if wrote {
                    out.push("\n");
                }
                out.push(inner);
                wrote = true;
            }
            out.style(start, StyleKind::Monospace);
            continue;
        }
        if trimmed.starts_with('|') {
            let start = out.units;
            out.push(line);
            out.style(start, StyleKind::Monospace);
            continue;
        }
        if let Some(heading) = heading_text(trimmed) {
            let start = out.units;
            inline(heading, &mut out);
            out.style(start, StyleKind::Bold);
            continue;
        }
        let indent = &line[..line.len() - trimmed.len()];
        if let Some(item) = trimmed.strip_prefix("- ").or_else(|| trimmed.strip_prefix("* ")).or_else(|| trimmed.strip_prefix("+ ")) {
            out.push(indent);
            out.push("• ");
            inline(item, &mut out);
            continue;
        }
        inline(line, &mut out);
    }
    Styled { text: out.text, styles: out.styles }
}

fn fence_of(line: &str) -> Option<&'static str> {
    if line.starts_with("```") {
        Some("```")
    } else if line.starts_with("~~~") {
        Some("~~~")
    } else {
        None
    }
}

fn heading_text(line: &str) -> Option<&str> {
    let hashes = line.chars().take_while(|c| *c == '#').count();
    if (1..=6).contains(&hashes) {
        let rest = &line[hashes..];
        if rest.starts_with(' ') {
            return Some(rest.trim().trim_end_matches('#').trim_end());
        }
    }
    None
}

fn is_word(c: Option<char>) -> bool {
    c.is_some_and(|c| c.is_alphanumeric())
}

/// One line's inline Markdown.
fn inline(src: &str, out: &mut Builder) {
    let chars: Vec<(usize, char)> = src.char_indices().collect();
    let at = |i: usize| chars.get(i).map(|(_, c)| *c);
    let byte = |i: usize| chars.get(i).map(|(b, _)| *b).unwrap_or(src.len());
    let mut i = 0;
    let mut plain_from = 0;
    let flush = |out: &mut Builder, from: usize, to: usize| {
        if to > from {
            out.push(&src[from..to]);
        }
    };
    while i < chars.len() {
        let c = chars[i].1;
        // `code`: nothing inside is parsed.
        if c == '`' {
            let ticks = chars[i..].iter().take_while(|(_, c)| *c == '`').count();
            let open_end = i + ticks;
            let mut j = open_end;
            let mut close = None;
            while j < chars.len() {
                if at(j) == Some('`') {
                    let run = chars[j..].iter().take_while(|(_, c)| *c == '`').count();
                    if run == ticks {
                        close = Some(j);
                        break;
                    }
                    j += run;
                } else {
                    j += 1;
                }
            }
            if let Some(close) = close {
                flush(out, plain_from, byte(i));
                let inner = &src[byte(open_end)..byte(close)];
                let inner = if inner.len() >= 2 && inner.starts_with(' ') && inner.ends_with(' ') { &inner[1..inner.len() - 1] } else { inner };
                let start = out.units;
                out.push(inner);
                out.style(start, StyleKind::Monospace);
                i = close + ticks;
                plain_from = byte(i);
                continue;
            }
            i = open_end;
            continue;
        }
        // [text](url)
        if c == '['
            && let Some((text_end, url_start, url_end)) = link_at(&chars, i)
        {
            flush(out, plain_from, byte(i));
            let text = &src[byte(i + 1)..byte(text_end)];
            let url = &src[byte(url_start)..byte(url_end)];
            if text == url || text.is_empty() {
                out.push(url);
            } else {
                inline(text, out);
                out.push(" (");
                out.push(url);
                out.push(")");
            }
            i = url_end + 1;
            plain_from = byte(i);
            continue;
        }
        // **bold**, __bold__, ~~strike~~
        let pair = match (c, at(i + 1)) {
            ('*', Some('*')) => Some(("**", StyleKind::Bold)),
            ('_', Some('_')) => Some(("__", StyleKind::Bold)),
            ('~', Some('~')) => Some(("~~", StyleKind::Strikethrough)),
            _ => None,
        };
        if let Some((marker, kind)) = pair {
            let after = at(i + 2);
            if after.is_some_and(|c| !c.is_whitespace())
                && (marker != "__" || !is_word(i.checked_sub(1).and_then(at)))
                && let Some(close) = find_close(src, &chars, i + 2, marker)
            {
                flush(out, plain_from, byte(i));
                let start = out.units;
                inline(&src[byte(i + 2)..byte(close)], out);
                out.style(start, kind);
                i = close + 2;
                plain_from = byte(i);
                continue;
            }
        }
        // *italic*, _italic_ (an underscore only between word boundaries, so snake_case stays).
        if (c == '*' || c == '_') && at(i + 1).is_some_and(|n| !n.is_whitespace() && n != c) {
            let marker = if c == '*' { "*" } else { "_" };
            let word_before = is_word(i.checked_sub(1).and_then(at));
            if !(c == '_' && word_before)
                && let Some(close) = find_close(src, &chars, i + 1, marker)
                && (c == '*' || !is_word(at(close + 1)))
            {
                flush(out, plain_from, byte(i));
                let start = out.units;
                inline(&src[byte(i + 1)..byte(close)], out);
                out.style(start, StyleKind::Italic);
                i = close + 1;
                plain_from = byte(i);
                continue;
            }
        }
        i += 1;
    }
    flush(out, plain_from, src.len());
}

/// The index of the closing `marker` after `from` (a char index), preceded by a non-space.
fn find_close(src: &str, chars: &[(usize, char)], from: usize, marker: &str) -> Option<usize> {
    let marker_chars: Vec<char> = marker.chars().collect();
    let mut j = from;
    while j + marker_chars.len() <= chars.len() {
        let matches = marker_chars.iter().enumerate().all(|(k, m)| chars[j + k].1 == *m);
        if matches && j > from && !chars[j - 1].1.is_whitespace() {
            // `**` must not close a single `*`, and vice versa.
            let next = chars.get(j + marker_chars.len()).map(|(_, c)| *c);
            if marker.len() == 1 && next == Some(marker_chars[0]) {
                j += 2;
                continue;
            }
            return Some(j);
        }
        if chars[j].1 == '`' {
            // Skip a code span: a marker inside it is not a closer.
            let rest = &src[chars[j].0 + 1..];
            if let Some(end) = rest.find('`') {
                let end_byte = chars[j].0 + 1 + end;
                j = chars.iter().position(|(b, _)| *b == end_byte).map(|p| p + 1).unwrap_or(chars.len());
                continue;
            }
        }
        j += 1;
    }
    None
}

/// `[text](url)` starting at `open` (a `[`): (index of `]`, first url char, index of `)`).
fn link_at(chars: &[(usize, char)], open: usize) -> Option<(usize, usize, usize)> {
    let mut depth = 0;
    let mut j = open;
    let text_end = loop {
        let c = chars.get(j)?.1;
        match c {
            '[' => depth += 1,
            ']' => {
                depth -= 1;
                if depth == 0 {
                    break j;
                }
            }
            _ => {}
        }
        j += 1;
    };
    if chars.get(text_end + 1)?.1 != '(' {
        return None;
    }
    let url_start = text_end + 2;
    let mut k = url_start;
    while let Some((_, c)) = chars.get(k) {
        if *c == ')' {
            return (k > url_start).then_some((text_end, url_start, k));
        }
        if c.is_whitespace() {
            return None;
        }
        k += 1;
    }
    None
}

/// Cuts a message into parts of at most `limit` UTF-16 units: at the last paragraph break that
/// fits, else the last line break, else the last space, else at the limit itself. Ranges are cut
/// with the text, so a monospace block that spans a cut is closed in one part and reopened in the
/// next. Whitespace at a cut is dropped.
pub fn split(styled: &Styled, limit: usize) -> Vec<Styled> {
    assert!(limit >= 2, "a part must hold at least one character");
    // Each char's starting unit offset, and the total.
    let chars: Vec<char> = styled.text.chars().collect();
    let mut offsets = Vec::with_capacity(chars.len() + 1);
    let mut units = 0;
    for c in &chars {
        offsets.push(units);
        units += c.len_utf16();
    }
    offsets.push(units);

    let mut parts = Vec::new();
    let mut from = 0; // char index
    while from < chars.len() {
        // Skip whitespace left at the start of a part.
        while from < chars.len() && chars[from].is_whitespace() && !parts.is_empty() {
            from += 1;
        }
        if from >= chars.len() {
            break;
        }
        // The furthest char index whose end fits.
        let mut end = from;
        while end < chars.len() && offsets[end + 1] - offsets[from] <= limit {
            end += 1;
        }
        let cut = if end >= chars.len() {
            chars.len()
        } else {
            let window: String = chars[from..end].iter().collect();
            let in_window = |pattern: &str| {
                window.rfind(pattern).map(|byte| from + window[..byte].chars().count()).filter(|cut| *cut > from)
            };
            in_window("\n\n").or_else(|| in_window("\n")).or_else(|| in_window(" ")).unwrap_or(end.max(from + 1))
        };
        // Trailing whitespace stays out of the part.
        let mut last = cut;
        while last > from && chars[last - 1].is_whitespace() {
            last -= 1;
        }
        let (start_unit, end_unit) = (offsets[from], offsets[last]);
        let text: String = chars[from..last].iter().collect();
        let styles = styled
            .styles
            .iter()
            .filter_map(|style| {
                let a = style.start.max(start_unit);
                let b = (style.start + style.length).min(end_unit);
                (b > a).then(|| Style { start: a - start_unit, length: b - a, kind: style.kind })
            })
            .collect();
        if !text.is_empty() {
            parts.push(Styled { text, styles });
        }
        from = cut;
    }
    parts
}

/// What the bridge sends for one answer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reply {
    pub parts: Vec<Styled>,
    /// The whole answer as Markdown, sent as `answer.md`, when it would take too many messages.
    pub attachment: Option<String>,
}

/// The longest message the bridge sends, in UTF-16 units.
pub const PART_LIMIT: usize = 2000;
/// More parts than this and the answer goes as a file, with only its first part as a message.
pub const MAX_PARTS: usize = 8;

/// An answer as the messages that carry it.
pub fn reply(markdown: &str) -> Reply {
    let parts = split(&render(markdown), PART_LIMIT);
    if parts.len() > MAX_PARTS {
        let mut first = parts.into_iter().next().unwrap_or_default();
        first.text.push_str("\n\n(The full answer is attached: answer.md)");
        return Reply { parts: vec![first], attachment: Some(markdown.to_string()) };
    }
    Reply { parts, attachment: None }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn styled(text: &str) -> (String, Vec<String>) {
        let s = render(text);
        (s.text.clone(), s.style_arguments())
    }

    /// The text a range covers, counting UTF-16 units as Signal does.
    fn covered(s: &Styled, style: &Style) -> String {
        let units: Vec<u16> = s.text.encode_utf16().collect();
        String::from_utf16(&units[style.start..style.start + style.length]).unwrap()
    }

    #[test]
    fn inline_forms_become_ranges() {
        assert_eq!(styled("a **b** c"), ("a b c".to_string(), vec!["2:1:BOLD".to_string()]));
        assert_eq!(styled("x *y* z"), ("x y z".to_string(), vec!["2:1:ITALIC".to_string()]));
        assert_eq!(styled("run `ls -la` now"), ("run ls -la now".to_string(), vec!["4:6:MONOSPACE".to_string()]));
        assert_eq!(styled("~~old~~ new"), ("old new".to_string(), vec!["0:3:STRIKETHROUGH".to_string()]));
        assert_eq!(styled("__strong__"), ("strong".to_string(), vec!["0:6:BOLD".to_string()]));
        assert_eq!(styled("_it_"), ("it".to_string(), vec!["0:2:ITALIC".to_string()]));
    }

    #[test]
    fn snake_case_and_lone_markers_stay_as_written() {
        assert_eq!(styled("call my_func_name now").0, "call my_func_name now");
        assert!(render("call my_func_name now").styles.is_empty());
        assert_eq!(styled("2 * 3 * 4").0, "2 * 3 * 4");
        assert_eq!(styled("a ** b").0, "a ** b");
        assert_eq!(styled("unclosed `tick").0, "unclosed `tick");
    }

    #[test]
    fn nested_styles_keep_both_ranges() {
        let s = render("**bold and `code`**");
        assert_eq!(s.text, "bold and code");
        assert!(s.styles.contains(&Style { start: 9, length: 4, kind: StyleKind::Monospace }));
        assert!(s.styles.contains(&Style { start: 0, length: 13, kind: StyleKind::Bold }));
    }

    #[test]
    fn markers_inside_code_are_not_parsed() {
        let s = render("`a*b*c` and *d*");
        assert_eq!(s.text, "a*b*c and d");
        assert_eq!(s.style_arguments(), vec!["0:5:MONOSPACE", "10:1:ITALIC"]);
    }

    #[test]
    fn links_become_text_and_address() {
        assert_eq!(styled("see [the docs](https://x.dev/a) now").0, "see the docs (https://x.dev/a) now");
        assert_eq!(styled("[https://x.dev](https://x.dev)").0, "https://x.dev");
        assert_eq!(styled("[1] is a citation").0, "[1] is a citation");
    }

    #[test]
    fn blocks_headings_lists_and_tables() {
        let md = "# Title\n\nSome text.\n\n```python\nprint('hi')\nx = 1\n```\n- one\n* two\n| a | b |\n|---|---|";
        let s = render(md);
        assert_eq!(s.text, "Title\n\nSome text.\n\nprint('hi')\nx = 1\n• one\n• two\n| a | b |\n|---|---|");
        let block = s.styles.iter().find(|st| st.kind == StyleKind::Monospace).unwrap();
        assert_eq!(covered(&s, block), "print('hi')\nx = 1");
        let title = s.styles.iter().find(|st| st.kind == StyleKind::Bold).unwrap();
        assert_eq!(covered(&s, title), "Title");
        let tables: Vec<_> = s.styles.iter().filter(|st| st.kind == StyleKind::Monospace).skip(1).collect();
        assert_eq!(tables.len(), 2);
    }

    #[test]
    fn an_unclosed_fence_runs_to_the_end() {
        let s = render("```\ncode\nmore");
        assert_eq!(s.text, "code\nmore");
        assert_eq!(s.style_arguments(), vec!["0:9:MONOSPACE"]);
    }

    #[test]
    fn ranges_count_utf16_units() {
        // 😀 is two UTF-16 units; 漢 is one.
        let s = render("😀 **漢字** `x`");
        assert_eq!(s.text, "😀 漢字 x");
        assert_eq!(s.style_arguments(), vec!["3:2:BOLD", "6:1:MONOSPACE"]);
        for style in &s.styles {
            assert!(!covered(&s, style).is_empty());
        }
        assert_eq!(s.units(), 7);
    }

    #[test]
    fn splitting_prefers_paragraphs_then_lines_then_spaces() {
        let s = Styled::plain("aaaa bbbb\n\ncccc dddd\neeee");
        let parts = split(&s, 13);
        let texts: Vec<_> = parts.iter().map(|p| p.text.as_str()).collect();
        assert_eq!(texts, vec!["aaaa bbbb", "cccc dddd", "eeee"]);
        let words = split(&Styled::plain("one two three four"), 9);
        let texts: Vec<_> = words.iter().map(|p| p.text.as_str()).collect();
        assert_eq!(texts, vec!["one two", "three", "four"]);
        let hard = split(&Styled::plain("abcdefghij"), 4);
        let texts: Vec<_> = hard.iter().map(|p| p.text.as_str()).collect();
        assert_eq!(texts, vec!["abcd", "efgh", "ij"]);
        for part in parts.iter().chain(&words).chain(&hard) {
            assert!(part.units() <= 14);
        }
    }

    #[test]
    fn a_range_across_a_cut_is_closed_and_reopened() {
        let s = render("```\nline one\nline two\nline three\n```");
        let parts = split(&s, 18);
        assert!(parts.len() >= 2);
        for part in &parts {
            assert_eq!(part.styles.len(), 1, "{part:?}");
            let style = part.styles[0];
            assert_eq!(style.kind, StyleKind::Monospace);
            assert_eq!(style.start, 0);
            assert_eq!(style.length, part.units());
        }
    }

    #[test]
    fn splitting_never_cuts_a_surrogate_pair() {
        let s = Styled::plain(&"😀".repeat(5));
        for part in split(&s, 3) {
            assert_eq!(part.text, "😀");
        }
    }

    #[test]
    fn a_very_long_answer_goes_as_a_file() {
        let paragraph = "word ".repeat(380);
        let md = vec![paragraph.as_str(); 12].join("\n\n");
        let r = reply(&md);
        assert_eq!(r.parts.len(), 1);
        assert_eq!(r.attachment.as_deref(), Some(md.as_str()));
        assert!(r.parts[0].text.ends_with("answer.md)"));
        let short = reply("hello **there**");
        assert_eq!(short.parts.len(), 1);
        assert!(short.attachment.is_none());
    }
}
