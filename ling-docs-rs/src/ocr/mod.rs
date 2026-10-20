//! OCR of scanned pages and images (spec §15.7, §20): RapidOCR's pipeline over its PP-OCR ONNX
//! models, written in Rust over ONNX Runtime, with the script of each page picked by Tesseract's
//! orientation and script detection, the route Phase 0 measured at 0.93 word recall on the scanned
//! fixtures:
//!
//! 1. Tesseract OSD names the page's script ([`osd`]);
//! 2. Latin, Chinese and Japanese pages go through PP-OCRv6 small (detection and recognition, one
//!    model for every script it knows); Cyrillic, Korean and Arabic pages through PP-OCRv5 mobile
//!    detection and the PP-OCRv5 recognition model of their script;
//! 3. detection ([`det`]) finds the text lines, each line is straightened ([`image::Image::warp`]),
//!    turned the right way up when the orientation classifier says it is upside down, and
//!    recognised ([`ctc`]); a line under 0.5 confidence is dropped; Arabic lines are put back into
//!    logical order with the bidi algorithm (the recogniser reads left to right).
//!
//! Every step follows RapidOCR 3.9.2's defaults (the sizes, thresholds, batches of six and the
//! image operations' sampling), so the text matches what Phase 0 measured; §20 compares the two
//! page by page. CPU only, four ONNX Runtime threads, in the OCR scope's sandbox and memory cap.

pub mod ctc;
pub mod det;
pub mod image;
pub mod osd;
pub mod worker;

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use ort::session::Session;
use ort::value::Tensor;

use image::{round_half_even, Image};
use osd::{Osd, Script};

/// RapidOCR's release whose models and defaults these are.
pub const RAPIDOCR_VERSION: &str = "3.9.2";

/// The model files in the OCR directory, by role.
pub const DET_V6: &str = "PP-OCRv6_det_small.onnx";
pub const REC_V6: &str = "PP-OCRv6_rec_small.onnx";
pub const DET_V5: &str = "ch_PP-OCRv5_det_mobile.onnx";
pub const CLS: &str = "ch_ppocr_mobile_v2.0_cls_mobile.onnx";
pub const REC_CYRILLIC: &str = "eslav_PP-OCRv5_rec_mobile.onnx";
pub const REC_HANGUL: &str = "korean_PP-OCRv5_rec_mobile.onnx";
pub const REC_ARABIC: &str = "arabic_PP-OCRv5_rec_mobile.onnx";
/// Tesseract's script detection model, in `tessdata/` under the OCR directory.
pub const OSD_MODEL: &str = "tessdata/osd.traineddata";

/// Every file the OCR needs in its directory.
pub const MODEL_FILES: [&str; 8] = [DET_V6, REC_V6, DET_V5, CLS, REC_CYRILLIC, REC_HANGUL, REC_ARABIC, OSD_MODEL];

/// A page below this mean confidence is one the vision queue would take (`docs_ocr_send_below`).
pub const SEND_BELOW: f32 = 0.92;

// RapidOCR's `Global` and `Det` defaults.
const MAX_SIDE: usize = 2000;
const MIN_SIDE: usize = 30;
const MIN_HEIGHT: usize = 30;
const WIDTH_HEIGHT_RATIO: f64 = 8.0;
const DET_LIMIT: usize = 736;
const TEXT_SCORE: f32 = 0.5;
const BATCH: usize = 6;
const CLS_SHAPE: (usize, usize) = (48, 192);
const CLS_THRESH: f32 = 0.9;
const REC_SHAPE: (usize, usize) = (48, 320);

/// What is missing for OCR, if anything.
pub fn missing(lib_dir: &Path, ocr_dir: &Path) -> Option<String> {
    let absent: Vec<&str> = MODEL_FILES.iter().copied().filter(|f| !ocr_dir.join(f).is_file()).collect();
    if !absent.is_empty() {
        return Some(format!("the OCR models are not installed (run `ling-admin docs setup`; missing {})", absent.join(", ")));
    }
    if !lib_dir.join("libonnxruntime.so").is_file() {
        return Some("ONNX Runtime is not installed (run `ling-admin docs setup`)".into());
    }
    osd::probe(lib_dir).map(|why| format!("Tesseract cannot be loaded: {why}"))
}

/// One recognised line.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Line {
    pub text: String,
    pub score: f32,
}

/// One page read.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct PageText {
    pub lines: Vec<Line>,
    /// The mean line confidence, weighted by words (characters for Chinese, Japanese and Korean).
    pub confidence: f32,
    /// The route taken, and what Tesseract named (absent when it could not tell).
    pub route: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tesseract_script: Option<String>,
}

impl PageText {
    pub fn text(&self) -> String {
        self.lines.iter().map(|l| l.text.as_str()).collect::<Vec<_>>().join("\n")
    }
}

/// A model and, for a recogniser, its classes.
struct Model {
    session: Session,
    classes: Vec<String>,
}

/// The loaded OCR: sessions opened as routes need them, Tesseract once.
pub struct Ocr {
    dir: PathBuf,
    threads: usize,
    models: HashMap<&'static str, Model>,
    osd: Osd,
}

impl Ocr {
    pub fn load(lib_dir: &Path, ocr_dir: &Path, threads: usize) -> Result<Ocr> {
        if let Some(why) = missing(lib_dir, ocr_dir) {
            anyhow::bail!(why);
        }
        crate::embed::init_runtime(lib_dir)?;
        let osd = Osd::load(lib_dir, &ocr_dir.join("tessdata"))?;
        Ok(Ocr { dir: ocr_dir.to_path_buf(), threads, models: HashMap::new(), osd })
    }

    fn model(&mut self, file: &'static str) -> Result<&mut Model> {
        if !self.models.contains_key(file) {
            let path = self.dir.join(file);
            // RapidOCR's `enable_cpu_mem_arena: false`: memory goes back after each run, which
            // keeps a worker that switches scripts under its cap.
            let session = Session::builder()
                .map_err(|e| anyhow!("{e}"))?
                .with_execution_providers([ort::ep::CPU::default().with_arena_allocator(false).build()])
                .map_err(|e| anyhow!("{e}"))?
                .with_intra_threads(self.threads)
                .map_err(|e| anyhow!("{e}"))?
                .with_inter_threads(1)
                .map_err(|e| anyhow!("{e}"))?
                .commit_from_file(&path)
                .with_context(|| format!("loading {}", path.display()))?;
            let classes = session.metadata().ok().and_then(|m| m.custom("character")).map(|c| ctc::classes(&c)).unwrap_or_default();
            self.models.insert(file, Model { session, classes });
        }
        Ok(self.models.get_mut(file).unwrap())
    }

    /// Runs a model on a batch of `n` images of `h` × `w`; the output's shape and values.
    fn run(&mut self, file: &'static str, data: Vec<f32>, n: usize, h: usize, w: usize) -> Result<(Vec<usize>, Vec<f32>)> {
        let model = self.model(file)?;
        let input = Tensor::from_array(([n, 3, h, w], data)).map_err(|e| anyhow!("{e}"))?;
        let outputs = model.session.run(ort::inputs!["x" => input]).map_err(|e| anyhow!("{e}"))?;
        let (shape, values) = outputs[0].try_extract_tensor::<f32>().map_err(|e| anyhow!("{e}"))?;
        Ok((shape.iter().map(|d| *d as usize).collect(), values.to_vec()))
    }

    /// Reads one page: script detection, then its route.
    pub fn read_page(&mut self, img: &Image) -> Result<PageText> {
        let detected = self.osd.script(&img.gray(), img.width, img.height);
        let script = detected.as_ref().map(|(n, _)| Script::of_tesseract(n)).unwrap_or(Script::Latin);
        let mut page = self.read_as(img, script)?;
        page.tesseract_script = detected.map(|(n, _)| n);
        Ok(page)
    }

    /// Reads one page by a given route (the measurements use it with each route).
    pub fn read_as(&mut self, img: &Image, script: Script) -> Result<PageText> {
        let (det, rec) = match script {
            Script::Latin | Script::Han => (DET_V6, REC_V6),
            Script::Cyrillic => (DET_V5, REC_CYRILLIC),
            Script::Hangul => (DET_V5, REC_HANGUL),
            Script::Arabic => (DET_V5, REC_ARABIC),
        };
        // One route's models at a time (and the classifier): a collection in several scripts
        // reloads a model now and then (a fraction of a second) rather than holding them all.
        self.models.retain(|file, _| *file == det || *file == rec || *file == CLS);
        let img = preprocess(img);
        let img = pad_for_detection(&img);
        let quads = self.detect(det, &img)?;
        let mut crops: Vec<Image> = quads.iter().map(|q| crop(&img, q)).collect();
        self.classify(&mut crops)?;
        let mut lines = self.recognise(rec, &crops)?;
        lines.retain(|l| !l.text.trim().is_empty() && l.score >= TEXT_SCORE);
        if script == Script::Arabic {
            for line in &mut lines {
                line.text = logical_order(&line.text);
            }
        }
        let weight = |t: &str| t.split_whitespace().map(|w| if w.chars().any(crate::store::is_cjk) { w.chars().count() } else { 1 }).sum::<usize>().max(1) as f32;
        let total: f32 = lines.iter().map(|l| weight(&l.text)).sum();
        let confidence = if total > 0.0 { lines.iter().map(|l| l.score * weight(&l.text)).sum::<f32>() / total } else { 0.0 };
        Ok(PageText { lines, confidence, route: format!("{}/{}", if det == DET_V6 { "v6-small" } else { "v5-mobile" }, script.as_str()), tesseract_script: None })
    }

    fn detect(&mut self, file: &'static str, img: &Image) -> Result<Vec<det::Quad>> {
        // `limit_type: min`: the shorter side brought up to 736, both sides to multiples of 32.
        let (h, w) = (img.height as f64, img.width as f64);
        let ratio = if h.min(w) < DET_LIMIT as f64 { DET_LIMIT as f64 / h.min(w) } else { 1.0 };
        let rh = (round_half_even((h * ratio).trunc() / 32.0) * 32.0) as usize;
        let rw = (round_half_even((w * ratio).trunc() / 32.0) * 32.0) as usize;
        if rh == 0 || rw == 0 {
            return Ok(Vec::new());
        }
        let resized = img.resize(rw, rh);
        let (shape, pred) = self.run(file, resized.normalized_chw(rw), 1, rh, rw)?;
        let (ph, pw) = (shape[2], shape[3]);
        Ok(det::boxes(&pred[..ph * pw], pw, ph, img.width, img.height))
    }

    fn classify(&mut self, crops: &mut [Image]) -> Result<()> {
        let (ch, cw) = CLS_SHAPE;
        for batch in by_ratio(crops).chunks(BATCH) {
            let mut data = Vec::with_capacity(batch.len() * 3 * ch * cw);
            for &i in batch {
                let ratio = crops[i].width as f64 / crops[i].height as f64;
                let rw = ((ch as f64 * ratio).ceil() as usize).clamp(1, cw);
                data.extend(crops[i].resize(rw, ch).normalized_chw(cw));
            }
            let (_, probs) = self.run(CLS, data, batch.len(), ch, cw)?;
            for (k, &i) in batch.iter().enumerate() {
                let (p0, p180) = (probs[k * 2], probs[k * 2 + 1]);
                if p180 > p0 && p180 > CLS_THRESH {
                    crops[i] = crops[i].rot180();
                }
            }
        }
        Ok(())
    }

    fn recognise(&mut self, file: &'static str, crops: &[Image]) -> Result<Vec<Line>> {
        let (rh, rw) = REC_SHAPE;
        let mut out = vec![Line { text: String::new(), score: 0.0 }; crops.len()];
        for batch in by_ratio(crops).chunks(BATCH) {
            let mut max_ratio = rw as f64 / rh as f64;
            for &i in batch {
                max_ratio = max_ratio.max(crops[i].width as f64 / crops[i].height as f64);
            }
            let width = (rh as f64 * max_ratio) as usize;
            let mut data = Vec::with_capacity(batch.len() * 3 * rh * width);
            for &i in batch {
                let ratio = crops[i].width as f64 / crops[i].height as f64;
                let w = ((rh as f64 * ratio).ceil() as usize).clamp(1, width);
                data.extend(crops[i].resize(w, rh).normalized_chw(width));
            }
            let (shape, probs) = self.run(file, data, batch.len(), rh, width)?;
            let (steps, n) = (shape[1], shape[2]);
            let classes = &self.models[file].classes;
            if classes.len() != n {
                anyhow::bail!("{file}: {n} classes in the output, {} in its character list", classes.len());
            }
            for (k, &i) in batch.iter().enumerate() {
                let (text, score) = ctc::decode(&probs[k * steps * n..(k + 1) * steps * n], steps, classes);
                out[i] = Line { text, score };
            }
        }
        Ok(out)
    }
}

/// Indices sorted by width-to-height ratio, as RapidOCR batches its crops.
fn by_ratio(crops: &[Image]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..crops.len()).collect();
    order.sort_by(|&a, &b| {
        let r = |i: usize| crops[i].width as f64 / crops[i].height.max(1) as f64;
        r(a).partial_cmp(&r(b)).unwrap_or(std::cmp::Ordering::Equal)
    });
    order
}

/// RapidOCR's `resize_image_within_bounds`: the longer side down to 2,000, the shorter up to 30,
/// both to multiples of 32.
fn preprocess(img: &Image) -> Image {
    let mut img = img.clone();
    let scaled = |img: &Image, ratio: f64| {
        let h = (round_half_even((img.height as f64 * ratio).trunc() / 32.0) * 32.0).max(32.0) as usize;
        let w = (round_half_even((img.width as f64 * ratio).trunc() / 32.0) * 32.0).max(32.0) as usize;
        img.resize(w, h)
    };
    if img.width.max(img.height) > MAX_SIDE {
        img = scaled(&img, MAX_SIDE as f64 / img.width.max(img.height) as f64);
    }
    if img.width.min(img.height) < MIN_SIDE {
        img = scaled(&img, MIN_SIDE as f64 / img.width.min(img.height) as f64);
    }
    img
}

/// RapidOCR's `apply_vertical_padding`: a very flat or very low image gets black bands.
fn pad_for_detection(img: &Image) -> Image {
    let (h, w) = (img.height, img.width);
    if h <= MIN_HEIGHT || w as f64 / h as f64 > WIDTH_HEIGHT_RATIO {
        let new_h = (((w as f64 / WIDTH_HEIGHT_RATIO) as usize).max(MIN_HEIGHT) * 2) as f64;
        let pad = ((new_h - h as f64).abs() / 2.0) as usize;
        return img.pad_vertical(pad, pad);
    }
    img.clone()
}

/// `get_rotate_crop_image`: a line's box straightened; a tall result turned a quarter.
fn crop(img: &Image, q: &det::Quad) -> Image {
    let d = |a: [f32; 2], b: [f32; 2]| ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt();
    let w = (d(q[0], q[1]).max(d(q[2], q[3])) as usize).max(1);
    let h = (d(q[0], q[3]).max(d(q[1], q[2])) as usize).max(1);
    let out = img.warp(q, w, h);
    if h as f64 / w as f64 >= 1.5 { out.rot90() } else { out }
}

/// A line read left to right put into logical order (python-bidi's `get_display`, base
/// direction from the first strong character).
pub fn logical_order(text: &str) -> String {
    let info = unicode_bidi::BidiInfo::new(text, None);
    let mut out = String::new();
    for para in &info.paragraphs {
        out.push_str(&info.reorder_line(para, para.range.clone()));
    }
    out
}

/// An image file decoded for OCR, its EXIF orientation applied, within memory limits: at most
/// [`MAX_IMAGE_PIXELS`] and 512 MiB for the decoder.
pub fn decode_image(bytes: &[u8]) -> Result<Image> {
    use ::image::{DynamicImage, ImageDecoder, ImageReader};
    let mut reader = ImageReader::new(std::io::Cursor::new(bytes)).with_guessed_format().map_err(|e| anyhow!("{e}"))?;
    let mut limits = ::image::Limits::default();
    limits.max_alloc = Some(512 << 20);
    limits.max_image_width = Some(30_000);
    limits.max_image_height = Some(30_000);
    reader.limits(limits);
    let mut decoder = reader.into_decoder().map_err(|e| anyhow!("not a readable image: {e}"))?;
    let orientation = decoder.orientation().ok();
    let (w, h) = decoder.dimensions();
    if w as u64 * h as u64 > MAX_IMAGE_PIXELS {
        anyhow::bail!("the image is {w} × {h} pixels, more than OCR reads");
    }
    let mut img = DynamicImage::from_decoder(decoder).map_err(|e| anyhow!("not a readable image: {e}"))?;
    if let Some(o) = orientation {
        img.apply_orientation(o);
    }
    let rgb = img.into_rgb8();
    Ok(Image { width: rgb.width() as usize, height: rgb.height() as usize, data: rgb.into_raw() })
}

/// The largest image read (a 50-megapixel photo).
pub const MAX_IMAGE_PIXELS: u64 = 50_000_000;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sizes_and_padding_follow_rapidocr() {
        let page = Image::new(1700, 2200);
        let out = preprocess(&page);
        assert_eq!((out.width, out.height), (1536, 1984), "2000/2200 × each side, to multiples of 32 (62.5 rounds to 62)");
        let flat = Image::new(900, 100);
        let padded = pad_for_detection(&flat);
        assert_eq!(padded.height, 100 + 2 * 62, "the height brought to 2 × max(900 / 8, 30)");
        assert_eq!(pad_for_detection(&Image::new(500, 400)).height, 400);
    }

    #[test]
    fn arabic_read_left_to_right_is_put_back_in_logical_order() {
        // "سلام" as the recogniser sees it, left to right: the letters reversed.
        let visual: String = "سلام".chars().rev().collect();
        assert_eq!(logical_order(&visual), "سلام");
        assert_eq!(logical_order("Latin stays"), "Latin stays");
    }

    #[test]
    fn an_image_decodes_with_limits() {
        let mut png = Vec::new();
        ::image::RgbImage::from_pixel(4, 3, ::image::Rgb([10, 20, 30])).write_to(&mut std::io::Cursor::new(&mut png), ::image::ImageFormat::Png).unwrap();
        let img = decode_image(&png).unwrap();
        assert_eq!((img.width, img.height, img.at(1, 1, 2)), (4, 3, 30));
        assert!(decode_image(b"not an image").is_err());
    }

    #[test]
    fn an_image_that_claims_to_be_huge_is_refused_before_it_is_decoded() {
        // A PNG header for 40,000 × 40,000 pixels (4.8 GB as RGB) and no image data.
        fn crc(bytes: &[u8]) -> u32 {
            let mut c = 0xFFFF_FFFFu32;
            for &b in bytes {
                c ^= b as u32;
                for _ in 0..8 {
                    c = if c & 1 != 0 { 0xEDB8_8320 ^ (c >> 1) } else { c >> 1 };
                }
            }
            !c
        }
        let mut ihdr = b"IHDR".to_vec();
        ihdr.extend(40_000u32.to_be_bytes());
        ihdr.extend(40_000u32.to_be_bytes());
        ihdr.extend([8, 2, 0, 0, 0]);
        let mut png = b"\x89PNG\r\n\x1a\n".to_vec();
        png.extend(13u32.to_be_bytes());
        png.extend(&ihdr);
        png.extend(crc(&ihdr).to_be_bytes());
        let started = std::time::Instant::now();
        let error = format!("{:#}", decode_image(&png).unwrap_err());
        assert!(started.elapsed().as_secs() < 2, "refused at once: {error}");
        assert!(error.contains("not a readable image") || error.contains("more than OCR reads"), "{error}");
    }
}
