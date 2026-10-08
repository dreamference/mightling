//! Extraction (spec §7.1): a file becomes located units of text.
//!
//! This runs only in the extractor sandbox ([`worker`]): bwrap with no network, the collection
//! root read-only, the home folder hidden, inside a scope capped at 1 GiB, with a per-file time
//! limit enforced from outside (`index::extract_all`). PDF parsers are attack surface; a file that
//! crashes or hangs the worker is recorded `failed` and the worker is started again on the rest.

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
    pub text: String,
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

/// Extracts one file.
pub fn extract_file(path: &Path, kind: Kind, pdfium: Option<&pdf::Pdf>) -> Result<Extracted> {
    if kind == Kind::Pdf {
        let Some(pdfium) = pdfium else { anyhow::bail!("PDFium is not available") };
        let text = pdfium.extract(path)?;
        let mut out = Extracted { pages: Some(text.pages.len() as u32), ..Extracted::default() };
        for (i, page) in text.pages.into_iter().enumerate() {
            let number = i as u32 + 1;
            if page.trim().is_empty() {
                out.needs_ocr.push(number);
                continue;
            }
            out.units.push(Unit { loc: format!("p.{number}"), page: Some(number), line_first: None, line_last: None, heading: String::new(), text: page });
        }
        return Ok(out);
    }
    let text = decode(&std::fs::read(path)?);
    let units = match kind {
        Kind::Markdown => text::markdown(&text),
        Kind::Rst => text::rst(&text),
        Kind::Org => text::org(&text),
        Kind::Tex => text::tex(&text),
        _ => text::plain(&text),
    };
    Ok(Extracted { units, ..Extracted::default() })
}

/// One file the worker is asked to read.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Job {
    pub id: i64,
    pub path: PathBuf,
    pub kind: Kind,
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
    let needs_pdf = jobs.iter().any(|j| j.kind == Kind::Pdf);
    let pdfium = if needs_pdf { pdf::Pdf::load(lib_dir).map_err(|e| e.to_string()) } else { Err(String::new()) };
    let mut emit = |event: &Event| -> Result<()> {
        writeln!(sink, "{}", serde_json::to_string(event)?)?;
        sink.flush()?;
        Ok(())
    };
    for job in jobs {
        emit(&Event::Start { id: job.id })?;
        let result = match (&pdfium, job.kind) {
            (Err(why), Kind::Pdf) => Err(anyhow::anyhow!("{why}")),
            (pdfium, kind) => extract_file(&job.path, kind, pdfium.as_ref().ok()),
        };
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
        let out = extract_file(&path, Kind::Markdown, None).unwrap();
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
            serde_json::to_string(&Job { id: 1, path: dir.path().join("missing.txt"), kind: Kind::Text }).unwrap(),
            serde_json::to_string(&Job { id: 2, path: good, kind: Kind::Text }).unwrap(),
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
