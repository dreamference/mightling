//! Extraction (spec §7.1): a file becomes located units of text.
//!
//! This runs only in the extractor sandbox ([`worker`]): bwrap with no network, the collection
//! root read-only, the home folder hidden, inside a scope capped at 1 GiB, with a per-file time
//! limit enforced from outside (`index::extract_all`). PDF parsers are attack surface; a file that
//! crashes or hangs the worker is recorded `failed` and the worker is started again on the rest.
//!
//! Every kind but PDF numbers its text by lines, so `read` can serve any range: the lines of a
//! text file, the paragraphs of a DOCX, ODT or HTML page (`para 12-30`), the rows of a CSV
//! (`rows 2-21`), and the lines of a mail file's messages and attachments (`msg 3`). PDFs are
//! read by page.

pub mod csv;
pub mod html;
pub mod mail;
pub mod office;
pub mod pdf;
pub mod text;

use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

use anyhow::Result;
use serde::{Deserialize, Serialize};

use crate::discover::Kind;

/// A located piece of a document: a page, a section, a block of lines.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Unit {
    /// What a citation says: `p.7`, `lines 120-180`.
    pub loc: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub line_first: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub line_last: Option<u32>,
    #[serde(default)]
    pub heading: String,
    /// Units of one group (a message and its attachments) are chunked together and never with
    /// another group's (§7.2: messages are chunks of their own).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group: Option<u32>,
    pub text: String,
}

/// A paragraph of a structured document, and its heading level when it is a heading.
#[derive(Debug, Clone, PartialEq)]
pub struct Para {
    pub text: String,
    pub level: Option<usize>,
}

/// Paragraphs (one line each, numbered from 1) cut into a unit per section: a heading starts
/// one and gives its heading path, as Markdown's sections do. `word` names the lines in the
/// locator (`para`).
pub fn sections(paras: Vec<Para>, word: &str) -> Vec<Unit> {
    let mut units = Vec::new();
    let mut path: Vec<(usize, String)> = Vec::new();
    let mut buf: Vec<String> = Vec::new();
    let mut start = 1;
    let heading = |path: &[(usize, String)]| path.iter().map(|(_, t)| t.as_str()).collect::<Vec<_>>().join(" > ");
    let unit = |first: usize, last: usize, heading: String, buf: &[String]| Unit {
        loc: format!("{word} {first}-{last}"),
        page: None,
        line_first: Some(first as u32),
        line_last: Some(last as u32),
        heading,
        group: None,
        text: buf.join("\n"),
    };
    let count = paras.len();
    for (i, para) in paras.into_iter().enumerate() {
        let n = i + 1;
        if let Some(level) = para.level {
            if !buf.is_empty() {
                units.push(unit(start, n - 1, heading(&path), &buf));
            }
            while path.last().is_some_and(|(l, _)| *l >= level) {
                path.pop();
            }
            path.push((level, para.text.clone()));
            start = n;
            buf = vec![para.text];
            continue;
        }
        buf.push(para.text);
    }
    if !buf.is_empty() {
        units.push(unit(start, count, heading(&path), &buf));
    }
    units
}

/// What the extractors need besides the file: PDFium, bound on the first PDF (a mailbox's
/// attachment included), so a run without PDFs never loads it.
pub struct Readers {
    lib_dir: Option<PathBuf>,
    pdfium: std::cell::OnceCell<std::result::Result<pdf::Pdf, String>>,
}

impl Readers {
    pub fn new(lib_dir: &Path) -> Readers {
        Readers { lib_dir: Some(lib_dir.to_path_buf()), pdfium: std::cell::OnceCell::new() }
    }

    /// No PDFium: PDFs fail, everything else reads.
    pub fn none() -> Readers {
        Readers { lib_dir: None, pdfium: std::cell::OnceCell::new() }
    }

    pub fn pdf(&self) -> Result<&pdf::Pdf> {
        let loaded = self.pdfium.get_or_init(|| match &self.lib_dir {
            Some(dir) => pdf::Pdf::load(dir).map_err(|e| format!("{e:#}")),
            None => Err("PDFium is not available".to_string()),
        });
        loaded.as_ref().map_err(|why| anyhow::anyhow!("{why}"))
    }
}

/// One file's extraction.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Extracted {
    pub units: Vec<Unit>,
    /// PDF pages, when the file is a PDF.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pages: Option<u32>,
    /// PDF pages with no text layer: unsearchable until OCR (Phase 2, §7.1).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub needs_ocr: Vec<u32>,
    /// PDF pages read, when a page cap stopped the reading before the end (PDF_SEARCH §3.5).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pages_read: Option<u32>,
    /// The PDF's own title (document information), when it has one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
}

/// Decodes a text file: UTF-8 (a BOM dropped), else Latin-1 rather than nothing.
pub fn decode(bytes: &[u8]) -> String {
    let bytes = bytes.strip_prefix(b"\xEF\xBB\xBF").unwrap_or(bytes);
    match std::str::from_utf8(bytes) {
        Ok(text) => text.to_string(),
        // Not UTF-8: most likely a Latin-1 or Windows-1252 file, whose letters this keeps.
        Err(_) => bytes.iter().map(|b| *b as char).collect(),
    }
}

/// Extracts one file; of a PDF, at most `max_pages` pages from the first.
pub fn extract_file(path: &Path, kind: Kind, readers: &Readers, max_pages: Option<u32>) -> Result<Extracted> {
    if kind == Kind::Pdf {
        return Ok(pdf_extracted(readers.pdf()?.extract(path, max_pages)?));
    }
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    extract_bytes(&name, kind, &std::fs::read(path)?, readers, max_pages)
}

/// Extracts a file's bytes (an attachment's, or a file read whole); `name` decides between CSV
/// and TSV.
pub fn extract_bytes(name: &str, kind: Kind, bytes: &[u8], readers: &Readers, max_pages: Option<u32>) -> Result<Extracted> {
    let units = match kind {
        Kind::Pdf => return Ok(pdf_extracted(readers.pdf()?.extract_bytes(bytes, max_pages)?)),
        Kind::Docx => office::docx(bytes)?,
        Kind::Odt => office::odt(bytes)?,
        Kind::Html => html::html(bytes),
        Kind::Eml => mail::eml(bytes, readers),
        Kind::Mbox => mail::mbox(bytes, readers),
        Kind::Csv => csv::csv(&decode(bytes), name.to_ascii_lowercase().ends_with(".tsv")),
        Kind::Markdown => text::markdown(&decode(bytes)),
        Kind::Rst => text::rst(&decode(bytes)),
        Kind::Org => text::org(&decode(bytes)),
        Kind::Tex => text::tex(&decode(bytes)),
        Kind::Text => text::plain(&decode(bytes)),
    };
    Ok(Extracted { units, ..Extracted::default() })
}

/// A PDF's pages as units; pages without text are recorded as needing OCR.
fn pdf_extracted(text: pdf::PdfText) -> Extracted {
    {
        let read = text.pages.len() as u32;
        let mut out = Extracted { pages: Some(text.page_count), pages_read: (read < text.page_count).then_some(read), title: text.title, ..Extracted::default() };
        for (i, page) in text.pages.into_iter().enumerate() {
            let number = i as u32 + 1;
            if page.trim().is_empty() {
                out.needs_ocr.push(number);
                continue;
            }
            out.units.push(Unit { loc: format!("p.{number}"), page: Some(number), line_first: None, line_last: None, heading: String::new(), group: None, text: page });
        }
        out
    }
}

/// One file the worker is asked to read.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Job {
    pub id: i64,
    pub path: PathBuf,
    pub kind: Kind,
    /// A PDF's page cap (`pdf_search`); collections read every page.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_pages: Option<u32>,
}

/// A line of the worker's output: `start` before a file, then its result.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "lowercase")]
pub enum Event {
    Start { id: i64 },
    Done { id: i64, result: Extracted },
    Failed { id: i64, error: String },
}

/// `ling-docs extract-worker <jobs.jsonl> <out.jsonl>`: reads each job's file and appends its
/// events, flushing after each, so the supervisor outside always knows which file is being read.
pub fn worker(jobs: &Path, out: &Path, lib_dir: &Path) -> Result<()> {
    let jobs: Vec<Job> = std::io::BufReader::new(std::fs::File::open(jobs)?)
        .lines()
        .map_while(|l| l.ok())
        .filter_map(|l| serde_json::from_str(&l).ok())
        .collect();
    let mut sink = std::fs::OpenOptions::new().create(true).append(true).open(out)?;
    let readers = Readers::new(lib_dir);
    let mut emit = |event: &Event| -> Result<()> {
        writeln!(sink, "{}", serde_json::to_string(event)?)?;
        sink.flush()?;
        Ok(())
    };
    for job in jobs {
        emit(&Event::Start { id: job.id })?;
        let result = extract_file(&job.path, job.kind, &readers, job.max_pages);
        match result {
            Ok(result) => emit(&Event::Done { id: job.id, result })?,
            Err(error) => emit(&Event::Failed { id: job.id, error: format!("{error:#}") })?,
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_files_become_units_and_a_bom_is_dropped() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a.md");
        std::fs::write(&path, b"\xEF\xBB\xBF# Hello\nworld\n").unwrap();
        let out = extract_file(&path, Kind::Markdown, &Readers::none(), None).unwrap();
        assert_eq!(out.units.len(), 1);
        assert_eq!(out.units[0].heading, "Hello");
        assert!(out.units[0].text.starts_with("# Hello"));
    }

    #[test]
    fn latin1_text_is_read_rather_than_dropped() {
        assert_eq!(decode(b"caf\xe9 cr\xe8me br\xfbl\xe9e d\xe9j\xe0 vu, \xe0 la carte, na\xefve fa\xe7ade"), "café crème brûlée déjà vu, à la carte, naïve façade");
        assert_eq!(decode("ok ✓".as_bytes()), "ok ✓");
    }

    #[test]
    fn the_worker_reports_each_file_and_goes_on_after_a_failure() {
        let dir = tempfile::tempdir().unwrap();
        let good = dir.path().join("good.txt");
        std::fs::write(&good, "hello\n").unwrap();
        let jobs = dir.path().join("jobs.jsonl");
        let lines = [
            serde_json::to_string(&Job { id: 1, path: dir.path().join("missing.txt"), kind: Kind::Text, max_pages: None }).unwrap(),
            serde_json::to_string(&Job { id: 2, path: good, kind: Kind::Text, max_pages: None }).unwrap(),
        ];
        std::fs::write(&jobs, lines.join("\n")).unwrap();
        let out = dir.path().join("out.jsonl");
        worker(&jobs, &out, dir.path()).unwrap();
        let events: Vec<Event> = std::fs::read_to_string(&out).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
        assert!(matches!(events[0], Event::Start { id: 1 }));
        assert!(matches!(events[1], Event::Failed { id: 1, .. }));
        assert!(matches!(&events[3], Event::Done { id: 2, result } if result.units.len() == 1));
    }
}
