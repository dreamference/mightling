//! `ling-docs ocr-worker <jobs.jsonl> <out.jsonl>` (spec §20): the scanned pages of PDFs and the
//! images of a collection read by OCR, inside the OCR scope's sandbox (no network, the home
//! hidden, the collection read-only, a 2 GiB cap) with a per-page time limit enforced from
//! outside (`index::ocr_all`).
//!
//! Per document: `start`, then `progress` after each page, then `ocr` with the pages' units and
//! confidences, or `ocrfailed` with the reason. A document's pages are read one at a time, from
//! the first that has no text layer, at most `max_pages` of them. Under `MIGHTLING_DOCS_TEST_OCR=fake`
//! no model is loaded: each page reads as a fixed sentence naming it, for the tests of the
//! plumbing.

use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

use anyhow::Result;
use serde::{Deserialize, Serialize};

use crate::discover::Kind;
use crate::extract::{Event, Unit};

/// What the units of a page read by OCR are headed with (the model sees it as the section).
pub const OCR_HEADING: &str = "scanned page (OCR)";
/// The locator of an image's text.
pub const IMAGE_LOC: &str = "image";

/// One document whose pages need OCR.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OcrJob {
    pub id: i64,
    pub path: PathBuf,
    pub kind: Kind,
    /// The pages to read (1-based), already capped.
    pub pages: Vec<u32>,
}

/// One page read.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OcrPage {
    pub page: u32,
    pub confidence: f32,
    pub route: String,
    pub lines: usize,
}

/// A document's OCR.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OcrResult {
    /// One unit per page that had text (`p.7`, or `image`).
    pub units: Vec<Unit>,
    pub pages: Vec<OcrPage>,
}

fn unit(kind: Kind, page: u32, text: String) -> Unit {
    let loc = if kind == Kind::Image { IMAGE_LOC.to_string() } else { format!("p.{page}") };
    Unit { loc, page: Some(page), line_first: None, line_last: None, heading: OCR_HEADING.to_string(), group: None, text }
}

/// The OCR worker's main loop.
pub fn worker(jobs: &Path, out: &Path, lib_dir: &Path, ocr_dir: &Path, threads: usize) -> Result<()> {
    let jobs: Vec<OcrJob> = std::io::BufReader::new(std::fs::File::open(jobs)?).lines().map_while(|l| l.ok()).filter_map(|l| serde_json::from_str(&l).ok()).collect();
    let sink = std::cell::RefCell::new(std::fs::OpenOptions::new().create(true).append(true).open(out)?);
    let emit = |event: &Event| -> Result<()> {
        let mut sink = sink.borrow_mut();
        writeln!(sink, "{}", serde_json::to_string(event)?)?;
        sink.flush()?;
        Ok(())
    };
    let fake = std::env::var("MIGHTLING_DOCS_TEST_OCR").as_deref() == Ok("fake");
    // Loaded on the first job, so a worker started for nothing loads nothing.
    let mut engine: Option<std::result::Result<super::Ocr, String>> = None;
    let mut pdfium: Option<std::result::Result<crate::extract::pdf::Pdf, String>> = None;
    for job in jobs {
        emit(&Event::Start { id: job.id })?;
        let mut result = OcrResult::default();
        let outcome = (|| -> Result<()> {
            let mut read = |page: u32, img: &super::image::Image, result: &mut OcrResult| -> Result<()> {
                let text = if fake {
                    let name = job.path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                    super::PageText { lines: vec![super::Line { text: format!("Recognised text of {name} page {page}"), score: 0.9 }], confidence: 0.9, route: "fake".into(), tesseract_script: None }
                } else {
                    let engine = engine.get_or_insert_with(|| super::Ocr::load(lib_dir, ocr_dir, threads).map_err(|e| format!("{e:#}")));
                    let engine = engine.as_mut().map_err(|why| anyhow::anyhow!("{why}"))?;
                    engine.read_page(img)?
                };
                emit(&Event::Progress { id: job.id })?;
                let joined = text.text();
                result.pages.push(OcrPage { page, confidence: text.confidence, route: text.route.clone(), lines: text.lines.len() });
                if !joined.trim().is_empty() {
                    result.units.push(unit(job.kind, page, joined));
                }
                Ok(())
            };
            match job.kind {
                Kind::Image => {
                    let img = if fake { super::image::Image::new(1, 1) } else { super::decode_image(&std::fs::read(&job.path)?)? };
                    read(1, &img, &mut result)?;
                }
                Kind::Pdf => {
                    if fake {
                        for &page in &job.pages {
                            read(page, &super::image::Image::new(1, 1), &mut result)?;
                        }
                    } else {
                        let pdf = pdfium.get_or_insert_with(|| crate::extract::pdf::Pdf::load(lib_dir).map_err(|e| format!("{e:#}")));
                        let pdf = pdf.as_ref().map_err(|why| anyhow::anyhow!("{why}"))?;
                        let mut each = |page: u32, w: usize, h: usize, gray: &[u8]| -> Result<()> {
                            read(page, &super::image::Image::from_gray(w, h, gray), &mut result)?;
                            Ok(())
                        };
                        pdf.render_gray(crate::extract::pdf::Source::Path(&job.path), &job.pages, &mut each)?;
                    }
                }
                other => anyhow::bail!("{} files are not read by OCR", other.as_str()),
            }
            Ok(())
        })();
        match outcome {
            Ok(()) => emit(&Event::Ocr { id: job.id, result })?,
            Err(error) => emit(&Event::OcrFailed { id: job.id, error: format!("{error:#}") })?,
        }
    }
    Ok(())
}
