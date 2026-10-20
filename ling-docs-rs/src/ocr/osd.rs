//! Script detection per page (spec §15.7, §20): Tesseract's orientation and script detection
//! (`--psm 0`, the 10 MB `osd.traineddata`), which named the script of all 26 readable fixture
//! pages correctly in Phase 0. Tesseract is a C++ library loaded at run time from the lib
//! directory (`libtesseract.so.5` and Leptonica's `liblept.so.5`, the distribution's builds that
//! `ling-admin docs setup` installs), through its C API; it reads only the image it is given and
//! the model file, and opens no socket.
//!
//! Leptonica is loaded first, globally, so `libtesseract`'s dependency on `liblept.so.5` is met
//! by the copy already loaded (the dynamic loader matches it by name) rather than searched for on
//! the system. Their own dependencies (libarchive, libcurl's library, libgomp, the image codecs)
//! come from the system; a missing one is reported by name.

use std::ffi::{c_char, c_float, c_int, c_void, CStr, CString};
use std::path::Path;

use anyhow::{bail, Result};

/// What OCR route a page takes (§15.7): PP-OCRv6 for Latin and Chinese/Japanese, the PP-OCRv5
/// model of its script for Cyrillic, Korean and Arabic.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Script {
    Latin,
    Han,
    Cyrillic,
    Hangul,
    Arabic,
}

impl Script {
    /// Tesseract's script name to a route; anything else (Greek, Thai, Devanagari, an unknown
    /// name, no answer) takes the Latin route, as Phase 0 replayed it.
    pub fn of_tesseract(name: &str) -> Script {
        let n = name.trim();
        if n.starts_with("Hangul") || n.starts_with("Korean") {
            Script::Hangul
        } else if n.starts_with("Han") || n.starts_with("Japanese") || n == "Katakana" || n == "Hiragana" {
            Script::Han
        } else if n.starts_with("Cyrillic") {
            Script::Cyrillic
        } else if n.starts_with("Arabic") {
            Script::Arabic
        } else {
            Script::Latin
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Script::Latin => "latin",
            Script::Han => "han",
            Script::Cyrillic => "cyrillic",
            Script::Hangul => "hangul",
            Script::Arabic => "arabic",
        }
    }
}

type Create = unsafe extern "C" fn() -> *mut c_void;
type Init3 = unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char) -> c_int;
type SetPsm = unsafe extern "C" fn(*mut c_void, c_int);
type SetImage = unsafe extern "C" fn(*mut c_void, *const u8, c_int, c_int, c_int, c_int);
type Detect = unsafe extern "C" fn(*mut c_void, *mut c_int, *mut c_float, *mut *const c_char, *mut c_float) -> c_int;
type Delete = unsafe extern "C" fn(*mut c_void);
type SetVariable = unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char) -> c_int;

/// A loaded Tesseract, initialised for script detection.
pub struct Osd {
    api: *mut c_void,
    set_psm: SetPsm,
    set_image: SetImage,
    detect: Detect,
    delete: Delete,
}

fn dlerror() -> String {
    let e = unsafe { libc::dlerror() };
    if e.is_null() { "unknown error".into() } else { unsafe { CStr::from_ptr(e) }.to_string_lossy().into_owned() }
}

fn open(path: &Path) -> Result<*mut c_void> {
    let c = CString::new(path.as_os_str().as_encoded_bytes())?;
    let handle = unsafe { libc::dlopen(c.as_ptr(), libc::RTLD_NOW | libc::RTLD_GLOBAL) };
    if handle.is_null() {
        bail!("cannot load {}: {}", path.display(), dlerror());
    }
    Ok(handle)
}

fn symbol<T: Copy>(handle: *mut c_void, name: &str) -> Result<T> {
    let c = CString::new(name)?;
    let p = unsafe { libc::dlsym(handle, c.as_ptr()) };
    if p.is_null() {
        bail!("Tesseract has no {name}");
    }
    Ok(unsafe { std::mem::transmute_copy(&p) })
}

impl Osd {
    /// Loads the libraries from `lib_dir` and the model from `tessdata` (the folder holding
    /// `osd.traineddata`).
    pub fn load(lib_dir: &Path, tessdata: &Path) -> Result<Osd> {
        open(&lib_dir.join("liblept.so.5"))?;
        let tess = open(&lib_dir.join("libtesseract.so.5"))?;
        let create: Create = symbol(tess, "TessBaseAPICreate")?;
        let init: Init3 = symbol(tess, "TessBaseAPIInit3")?;
        let set_variable: SetVariable = symbol(tess, "TessBaseAPISetVariable")?;
        let (set_psm, set_image, detect, delete) = (symbol(tess, "TessBaseAPISetPageSegMode")?, symbol(tess, "TessBaseAPISetImage")?, symbol(tess, "TessBaseAPIDetectOrientationScript")?, symbol(tess, "TessBaseAPIDelete")?);
        let api = unsafe { create() };
        if api.is_null() {
            bail!("Tesseract could not start");
        }
        // Owned from here: dropped (and the API deleted) on any error below.
        let osd = Osd { api, set_psm, set_image, detect, delete };
        let data = CString::new(tessdata.as_os_str().as_encoded_bytes())?;
        let lang = CString::new("osd")?;
        // Tesseract prints its own warnings to stderr (the worker's log).
        if unsafe { init(api, data.as_ptr(), lang.as_ptr()) } != 0 {
            bail!("Tesseract could not read {}", tessdata.join("osd.traineddata").display());
        }
        let (key, value) = (CString::new("debug_file")?, CString::new("/dev/null")?);
        unsafe { set_variable(api, key.as_ptr(), value.as_ptr()) };
        unsafe { (osd.set_psm)(api, 0) }; // PSM_OSD_ONLY
        Ok(osd)
    }

    /// The script Tesseract sees on a grey page, with its confidence; `None` when it cannot tell
    /// (a blank or unreadable page).
    pub fn script(&mut self, gray: &[u8], width: usize, height: usize) -> Option<(String, f32)> {
        if width == 0 || height == 0 || gray.len() < width * height {
            return None;
        }
        unsafe { (self.set_image)(self.api, gray.as_ptr(), width as c_int, height as c_int, 1, width as c_int) };
        let (mut deg, mut oconf, mut name, mut sconf) = (0 as c_int, 0 as c_float, std::ptr::null::<c_char>(), 0 as c_float);
        let ok = unsafe { (self.detect)(self.api, &mut deg, &mut oconf, &mut name, &mut sconf) };
        if ok == 0 || name.is_null() {
            return None;
        }
        Some((unsafe { CStr::from_ptr(name) }.to_string_lossy().into_owned(), sconf))
    }
}

impl Drop for Osd {
    fn drop(&mut self) {
        if !self.api.is_null() {
            unsafe { (self.delete)(self.api) };
        }
    }
}

/// Why the OCR libraries cannot be loaded here, if they cannot: a missing file, or the name of a
/// system library they need.
pub fn probe(lib_dir: &Path) -> Option<String> {
    for name in ["liblept.so.5", "libtesseract.so.5"] {
        if !lib_dir.join(name).is_file() {
            return Some(format!("{name} is not installed (run `ling-admin docs setup`)"));
        }
    }
    match open(&lib_dir.join("liblept.so.5")).and_then(|_| open(&lib_dir.join("libtesseract.so.5"))) {
        Ok(_) => None,
        Err(e) => Some(format!("{e:#}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tesseract_scripts_map_to_routes() {
        for (name, script) in [("Latin", Script::Latin), ("HanS", Script::Han), ("HanT", Script::Han), ("Japanese", Script::Han), ("Hangul", Script::Hangul), ("Korean", Script::Hangul), ("Cyrillic", Script::Cyrillic), ("Arabic", Script::Arabic), ("Greek", Script::Latin), ("Devanagari", Script::Latin), ("", Script::Latin)] {
            assert_eq!(Script::of_tesseract(name), script, "{name}");
        }
    }

    #[test]
    fn the_libraries_detect_the_script_where_they_are_installed() {
        // Only with `MIGHTLING_DOCS_LIB_DIR` and `MIGHTLING_DOCS_OCR_DIR` naming installed copies.
        let (Some(lib), Some(ocr)) = (std::env::var_os("MIGHTLING_DOCS_LIB_DIR"), std::env::var_os("MIGHTLING_DOCS_OCR_DIR")) else { return };
        let (lib, ocr) = (std::path::PathBuf::from(lib), std::path::PathBuf::from(ocr));
        if probe(&lib).is_some() || !ocr.join("tessdata/osd.traineddata").is_file() {
            return;
        }
        let mut osd = Osd::load(&lib, &ocr.join("tessdata")).unwrap();
        // A blank page: no answer, and no crash.
        assert!(osd.script(&vec![255u8; 400 * 300], 400, 300).is_none_or(|(n, _)| !n.is_empty()));
    }
}
