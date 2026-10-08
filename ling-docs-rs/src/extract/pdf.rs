//! PDF text through PDFium, with the reading-order pass and the bidi step of spec §15.2.
//!
//! A port of Phase 0's `ordered_page` and `rtl_line` (`eval/worker.py`), which matched the
//! PyMuPDF reference everywhere and beat it on reading order: characters are read in
//! content-stream order and cut into runs where the stream jumps; lines keep stream order (which
//! follows columns in nearly every producer); a run drawn out of place (RFC PDFs draw MUST,
//! SHOULD … after the rest of their line) is moved back into the line it sits in; rotated margin
//! text is kept after the page's text; and a line holding right-to-left script is rebuilt from
//! its glyphs in visual order and turned into logical order by the Unicode bidi algorithm.
//!
//! The geometry works on plain numbers ([`Glyph`]), so it is tested without PDFium; [`Pdf`] reads
//! the glyphs through pdfium-render's raw bindings, the same calls Phase 0 made through
//! pypdfium2, in double precision.

use std::path::Path;

use anyhow::{anyhow, bail, Result};
use pdfium_render::prelude::*;

/// One character as PDFium reports it, in content-stream order.
#[derive(Debug, Clone, Copy)]
pub struct Glyph {
    pub ch: char,
    /// Radians; PDFium's -1 for "unknown" is kept as it is (Phase 0 did the same).
    pub angle: f64,
    pub left: f64,
    pub bottom: f64,
    pub right: f64,
    pub top: f64,
}

/// Hebrew, Arabic, Syriac, Thaana, N'Ko and the Arabic presentation forms.
pub fn is_rtl(ch: char) -> bool {
    let o = ch as u32;
    (0x0590..=0x08FF).contains(&o) || (0xFB1D..=0xFDFF).contains(&o) || (0xFE70..=0xFEFF).contains(&o)
}

/// A run: characters in stream order on one line, with its box and its glyphs' extents.
#[derive(Debug, Clone)]
struct Run {
    left: f64,
    bottom: f64,
    right: f64,
    top: f64,
    text: String,
    glyphs: Vec<(f64, f64, char)>,
}

struct Line {
    bottom: f64,
    top: f64,
    left: f64,
    right: f64,
    runs: Vec<Run>,
}

const PI: f64 = 3.14159265;

/// The page's text from its glyphs.
pub fn ordered_text(glyphs: &[Glyph]) -> String {
    let mut segs: Vec<Run> = Vec::new();
    let mut cur: Option<Run> = None;
    let mut rotated = String::new();
    for g in glyphs {
        let ch = g.ch;
        if !matches!(ch, ' ' | '\r' | '\n') {
            let a = g.angle.rem_euclid(PI);
            if 0.1 < a && a < 3.04 {
                rotated.push(ch);
                continue;
            }
        }
        if ch == '\r' || ch == '\n' {
            if let Some(run) = cur.take() {
                segs.push(run);
            }
            continue;
        }
        let (left, bottom, right, top) = (g.left, g.bottom, g.right, g.top);
        if ch == ' ' || top - bottom <= 0.0 {
            if ch == ' ' {
                if let Some(run) = cur.as_mut() {
                    run.text.push(' ');
                }
            }
            continue;
        }
        let h = top - bottom;
        let rtl = is_rtl(ch);
        if let Some(run) = cur.as_mut() {
            let centre = (bottom + top) / 2.0;
            let same_line = run.bottom - 0.2 * h <= centre && centre <= run.top + 0.2 * h;
            if same_line
                && ((run.right - 0.5 * h <= left && left <= run.right + 3.0 * h) || (rtl && run.left - 3.0 * h <= right && right <= run.left + 0.5 * h))
            {
                run.left = run.left.min(left);
                run.right = run.right.max(right);
                run.bottom = run.bottom.min(bottom);
                run.top = run.top.max(top);
                run.text.push(ch);
                run.glyphs.push((left, right, ch));
                continue;
            }
            segs.push(cur.take().unwrap());
        }
        cur = Some(Run { left, bottom, right, top, text: ch.to_string(), glyphs: vec![(left, right, ch)] });
    }
    if let Some(run) = cur {
        segs.push(run);
    }
    let segs: Vec<Run> = segs.into_iter().filter(|s| !s.text.trim().is_empty()).collect();
    let tail = if rotated.trim().is_empty() { String::new() } else { format!("\n{rotated}") };
    if segs.is_empty() {
        return tail.trim().to_string();
    }
    let mut lines: Vec<Line> = Vec::new();
    for s in segs {
        let h = s.top - s.bottom;
        let centre = (s.bottom + s.top) / 2.0;
        let mut home: Option<usize> = None;
        if let Some(last) = lines.last() {
            if last.bottom - 0.2 * h <= centre && centre <= last.top + 0.2 * h && (s.left >= last.left - h || s.text.chars().any(is_rtl)) {
                home = Some(lines.len() - 1);
            }
        }
        if home.is_none() {
            let from = lines.len().saturating_sub(80);
            for index in (from..lines.len()).rev() {
                let ln = &lines[index];
                if ln.bottom <= centre
                    && centre <= ln.top
                    && ln.left - 8.0 * h < s.left
                    && s.right < ln.right + 8.0 * h
                    && !ln.runs.iter().any(|r| r.left < s.right && s.left < r.right)
                {
                    home = Some(index);
                    break;
                }
            }
        }
        match home {
            None => lines.push(Line { bottom: s.bottom, top: s.top, left: s.left, right: s.right, runs: vec![s] }),
            Some(index) => {
                let ln = &mut lines[index];
                ln.bottom = ln.bottom.min(s.bottom);
                ln.top = ln.top.max(s.top);
                ln.left = ln.left.min(s.left);
                ln.right = ln.right.max(s.right);
                ln.runs.push(s);
            }
        }
    }
    let mut text: Vec<String> = Vec::new();
    for mut ln in lines {
        ln.runs.sort_by(|a, b| a.left.partial_cmp(&b.left).unwrap_or(std::cmp::Ordering::Equal));
        if ln.runs.iter().any(|r| r.text.chars().any(is_rtl)) {
            text.push(rtl_line(&ln.runs));
            continue;
        }
        let mut parts = String::new();
        let mut prev: Option<f64> = None;
        for r in &ln.runs {
            if let Some(p) = prev {
                if r.left - p > 0.15 * (r.top - r.bottom) && !parts.ends_with(' ') && !r.text.starts_with(' ') {
                    parts.push(' ');
                }
            }
            parts.push_str(&r.text);
            prev = Some(r.right);
        }
        text.push(parts);
    }
    text.join("\n") + &tail
}

/// A line holding right-to-left script: every glyph in visual order (left to right, a space where
/// the gap is a word gap), then the bidi algorithm turns the visual string into logical order
/// (reordering is its own inverse for right-to-left runs, and left-to-right runs such as numbers
/// keep their order). Stream order cannot be trusted here: Chromium's PDFs draw Arabic glyphs in
/// visual order, and PDFium's own text reverses some words and not others.
fn rtl_line(runs: &[Run]) -> String {
    let mut glyphs: Vec<(f64, f64, char)> = runs.iter().flat_map(|r| r.glyphs.iter().copied()).collect();
    glyphs.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    let h = runs.iter().map(|r| r.top - r.bottom).fold(f64::MIN, f64::max);
    let mut visual = String::new();
    let mut prev: Option<f64> = None;
    for (left, right, ch) in glyphs {
        if let Some(p) = prev {
            if left - p > 0.2 * h {
                visual.push(' ');
            }
        }
        visual.push(ch);
        prev = Some(match prev {
            None => right,
            Some(p) => p.max(right),
        });
    }
    let n_rtl = visual.chars().filter(|c| is_rtl(*c)).count();
    let n_ltr = visual.chars().filter(|c| c.is_alphabetic() && !is_rtl(*c)).count();
    display(&visual, n_rtl >= n_ltr)
}

/// The Unicode bidi algorithm's display order of one line (python-bidi's `get_display`, which is
/// itself this crate).
pub fn display(text: &str, rtl_base: bool) -> String {
    use unicode_bidi::{BidiInfo, Level};
    let level = if rtl_base { Level::rtl() } else { Level::ltr() };
    let info = BidiInfo::new(text, Some(level));
    let mut out = String::new();
    for para in &info.paragraphs {
        out.push_str(&info.reorder_line(para, para.range.clone()));
    }
    out
}

/// What became of one PDF.
#[derive(Debug, Default)]
pub struct PdfText {
    /// One string per page, in page order.
    pub pages: Vec<String>,
}

/// PDFium, bound once per process from the lib directory.
pub struct Pdf {
    bindings: Box<dyn PdfiumLibraryBindings>,
}

impl Pdf {
    pub fn load(lib_dir: &Path) -> Result<Pdf> {
        let path = Pdfium::pdfium_platform_library_name_at_path(lib_dir);
        let bindings = Pdfium::bind_to_library(&path).map_err(|e| anyhow!("cannot load PDFium from {}: {e:?}", path.display()))?;
        unsafe { bindings.FPDF_InitLibrary() };
        Ok(Pdf { bindings })
    }

    /// Every page's text. Encrypted, damaged or empty files are errors with a short reason.
    pub fn extract(&self, path: &Path) -> Result<PdfText> {
        let b = self.bindings.as_ref();
        let file = path.to_str().ok_or_else(|| anyhow!("the path is not UTF-8"))?;
        let doc = unsafe { b.FPDF_LoadDocument(file, None) };
        if doc.is_null() {
            let code = unsafe { b.FPDF_GetLastError() };
            bail!(match code {
                2 => "cannot open the file",
                3 => "not a PDF, or damaged",
                4 => "password-protected",
                5 => "unsupported security handler",
                _ => "PDFium could not read it",
            });
        }
        let count = unsafe { b.FPDF_GetPageCount(doc) };
        let mut out = PdfText::default();
        for index in 0..count.max(0) {
            let page = unsafe { b.FPDF_LoadPage(doc, index) };
            if page.is_null() {
                out.pages.push(String::new());
                continue;
            }
            let text_page = unsafe { b.FPDFText_LoadPage(page) };
            let mut glyphs = Vec::new();
            if !text_page.is_null() {
                let n = unsafe { b.FPDFText_CountChars(text_page) };
                let mut pending_high: Option<u32> = None;
                for c in 0..n.max(0) {
                    let mut cp = unsafe { b.FPDFText_GetUnicode(text_page, c) };
                    // An astral character (math italics) comes as two surrogate entries.
                    if (0xD800..0xDC00).contains(&cp) {
                        pending_high = Some(cp);
                        continue;
                    }
                    if (0xDC00..0xE000).contains(&cp) {
                        let Some(high) = pending_high else { continue };
                        cp = 0x10000 + ((high - 0xD800) << 10) + (cp - 0xDC00);
                    }
                    pending_high = None;
                    let Some(ch) = char::from_u32(cp).filter(|_| cp > 0) else { continue };
                    let angle = unsafe { b.FPDFText_GetCharAngle(text_page, c) } as f64;
                    let (mut left, mut right, mut bottom, mut top) = (0f64, 0f64, 0f64, 0f64);
                    unsafe { b.FPDFText_GetCharBox(text_page, c, &mut left, &mut right, &mut bottom, &mut top) };
                    glyphs.push(Glyph { ch, angle, left, bottom, right, top });
                }
                unsafe { b.FPDFText_ClosePage(text_page) };
            }
            unsafe { b.FPDF_ClosePage(page) };
            out.pages.push(ordered_text(&glyphs));
        }
        unsafe { b.FPDF_CloseDocument(doc) };
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Glyphs for `text` laid left to right from `x` on the line at `y`, 5 points a character.
    fn word(text: &str, x: f64, y: f64) -> Vec<Glyph> {
        text.chars()
            .enumerate()
            .map(|(i, ch)| Glyph { ch, angle: 0.0, left: x + 5.0 * i as f64, right: x + 5.0 * i as f64 + 4.5, bottom: y, top: y + 8.0 })
            .collect()
    }

    fn newline() -> Glyph {
        Glyph { ch: '\n', angle: 0.0, left: 0.0, right: 0.0, bottom: 0.0, top: 0.0 }
    }

    #[test]
    fn a_keyword_drawn_after_its_line_is_put_back_in_place() {
        // An RFC line: "Clients" … gap … "send it", with "MUST" drawn last into the gap.
        let mut glyphs = word("Clients", 10.0, 700.0);
        glyphs.extend(word("send", 90.0, 700.0));
        glyphs.push(newline());
        glyphs.extend(word("Next line here", 10.0, 688.0));
        glyphs.push(newline());
        glyphs.extend(word("MUST", 55.0, 700.0));
        let text = ordered_text(&glyphs);
        assert_eq!(text, "Clients MUST send\nNext line here");
    }

    #[test]
    fn rotated_margin_text_goes_after_the_page() {
        let mut glyphs = word("Body", 10.0, 700.0);
        glyphs.extend("Margin".chars().map(|ch| Glyph { ch, angle: 1.5708, left: 0.0, right: 1.0, bottom: 0.0, top: 1.0 }));
        assert_eq!(ordered_text(&glyphs), "Body\nMargin");
    }

    #[test]
    fn a_right_to_left_line_is_returned_in_logical_order() {
        // "سلام" drawn in visual order: the last letter leftmost.
        let logical: Vec<char> = "سلام".chars().collect();
        let glyphs: Vec<Glyph> = logical
            .iter()
            .rev()
            .enumerate()
            .map(|(i, ch)| Glyph { ch: *ch, angle: 0.0, left: 10.0 + 5.0 * i as f64, right: 14.5 + 5.0 * i as f64, bottom: 700.0, top: 708.0 })
            .collect();
        assert_eq!(ordered_text(&glyphs), "سلام");
    }

    #[test]
    fn an_empty_page_is_empty() {
        assert_eq!(ordered_text(&[]), "");
    }
}
