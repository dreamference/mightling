//! `ling docs setup|status|remove`: what `ling-docs` loads at run time, fetched by the launcher
//! (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §7.1, §7.3), so a client needs no `ling-admin` for
//! it. `ling-docs` opens no socket at any step, so the three things it needs that are not in its
//! binary are fetched here, once, each pinned by URL and SHA-256, as `ling-admin docs setup`
//! (`dreamference/runner/docs_index_setup.py`) fetches them on a node; the pins are the same:
//!
//! - PDFium, the pdfium-binaries build `chromium/8076` (BSD-3), the one Phase 0 measured;
//! - ONNX Runtime 1.30.0 (MIT), CPU only;
//! - the embedding model, `Snowflake/snowflake-arctic-embed-m-v2.0`'s int8 ONNX export and its
//!   tokenizer (Apache-2.0), at a pinned revision.
//!
//! They go where the binary looks for them, beside it: `<install>/lib/ling-docs/` and
//! `<install>/models/snowflake-arctic-embed-m-v2.0-int8/` (`config::lib_dir`, `config::model_dir`
//! in `ling-docs-rs/src/config.rs`). A file already there at its pin is not fetched again; a
//! library's pin is the archive's hash, kept in `.<name>.pin` beside it. Linux only, where the
//! index runs.

use std::io::Read;
use std::path::Path;
use std::path::PathBuf;

use sha2::Digest;
use sha2::Sha256;

pub const MODEL_NAME: &str = "snowflake-arctic-embed-m-v2.0-int8";
const PDFIUM_RELEASE: &str = "chromium/8076";
const ONNXRUNTIME_VERSION: &str = "1.30.0";
const MODEL_REPO: &str = "Snowflake/snowflake-arctic-embed-m-v2.0";
const MODEL_REVISION: &str = "95c2741480856aa9666782eb4afe11959938017f";
const USAGE: &str = "Usage: ling docs setup | ling docs status | ling docs remove";

/// A pinned archive and the one member taken from it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Library {
    pub url: &'static str,
    pub sha256: &'static str,
    pub member: &'static str,
    pub name: &'static str,
}

/// The libraries for a machine (`std::env::consts::ARCH`), or None where `ling-docs` has no pins.
pub fn libraries(arch: &str) -> Option<[Library; 2]> {
    match arch {
        "aarch64" => Some([
            Library {
                url: "https://github.com/bblanchon/pdfium-binaries/releases/download/chromium%2F8076/pdfium-linux-arm64.tgz",
                sha256: "d7247b33ae5545615a5e877235dd97afc879e3a8805689684f528cae3339d352",
                member: "lib/libpdfium.so",
                name: "libpdfium.so",
            },
            Library {
                url: "https://github.com/microsoft/onnxruntime/releases/download/v1.30.0/onnxruntime-linux-aarch64-1.30.0.tgz",
                sha256: "e16a27a8ed330bbc698df7330b0cf56e722f354e3bcc92118682c74ef3c3e3da",
                member: "onnxruntime-linux-aarch64-1.30.0/lib/libonnxruntime.so.1.30.0",
                name: "libonnxruntime.so",
            },
        ]),
        "x86_64" => Some([
            Library {
                url: "https://github.com/bblanchon/pdfium-binaries/releases/download/chromium%2F8076/pdfium-linux-x64.tgz",
                sha256: "d9d67bc40af03aef4fe28a60b19b1086f28ace019c8c9caf19cb7fe3d14ceca3",
                member: "lib/libpdfium.so",
                name: "libpdfium.so",
            },
            Library {
                url: "https://github.com/microsoft/onnxruntime/releases/download/v1.30.0/onnxruntime-linux-x64-1.30.0.tgz",
                sha256: "a5ed5a3cac51fbb2e90da632ae43d19212faaa20e76484e62bcb7c23ddb3b3fd",
                member: "onnxruntime-linux-x64-1.30.0/lib/libonnxruntime.so.1.30.0",
                name: "libonnxruntime.so",
            },
        ]),
        _ => None,
    }
}

/// (path in the model repository, SHA-256, installed name).
pub const MODEL_FILES: [(&str, &str, &str); 2] = [
    ("onnx/model_int8.onnx", "03d923bb1850ebdccb068e2f3abd8aa43fe81c50d07d037ef103fe3d0fb78e3b", "model.onnx"),
    ("tokenizer.json", "f1cc44ad7faaeec47241864835473fd5403f2da94673f3f764a77ebcb0a803ec", "tokenizer.json"),
];

/// The install directory, from this executable: `<install>/bin/ling` → `<install>`.
pub fn install_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    exe.parent()?.parent().map(Path::to_path_buf)
}

pub fn lib_dir(install: &Path) -> PathBuf {
    install.join("lib").join("ling-docs")
}

pub fn model_dir(install: &Path) -> PathBuf {
    install.join("models").join(MODEL_NAME)
}

fn hex_sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes).iter().map(|byte| format!("{byte:02x}")).collect()
}

fn sha256_of(path: &Path) -> Option<String> {
    let mut file = std::fs::File::open(path).ok()?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let read = file.read(&mut buffer).ok()?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Some(digest.finalize().iter().map(|byte| format!("{byte:02x}")).collect())
}

/// Where the pin a library was installed from is recorded (the archive's hash, not the file's).
pub fn stamp_path(target: &Path) -> PathBuf {
    let name = target.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    target.with_file_name(format!(".{name}.pin"))
}

/// What is not installed at its pin under `install`, by installed name; empty when all is current.
pub fn missing(install: &Path, arch: &str) -> Vec<String> {
    let mut out = Vec::new();
    for library in libraries(arch).into_iter().flatten() {
        let target = lib_dir(install).join(library.name);
        let pinned = std::fs::read_to_string(stamp_path(&target)).map(|text| text.trim() == library.sha256).unwrap_or(false);
        if !target.is_file() || !pinned {
            out.push(library.name.to_string());
        }
    }
    for (_, sha256, name) in MODEL_FILES {
        let target = model_dir(install).join(name);
        if !target.is_file() || sha256_of(&target).as_deref() != Some(sha256) {
            out.push(name.to_string());
        }
    }
    out
}

/// The one member of a gzipped tar archive, or an error naming what was wrong.
pub fn extract_member(archive: &[u8], member: &str) -> Result<Vec<u8>, String> {
    let decoder = flate2::read::GzDecoder::new(archive);
    let mut entries = tar::Archive::new(decoder);
    for entry in entries.entries().map_err(|error| format!("not a tar archive: {error}"))? {
        let mut entry = entry.map_err(|error| format!("a damaged tar entry: {error}"))?;
        let path = entry.path().map_err(|error| format!("a tar entry with no path: {error}"))?;
        if path.to_string_lossy().trim_start_matches("./") == member {
            let mut bytes = Vec::new();
            entry.read_to_end(&mut bytes).map_err(|error| format!("cannot read {member}: {error}"))?;
            return Ok(bytes);
        }
    }
    Err(format!("{member} is not in the archive"))
}

/// Writes `target` through a temporary file renamed over it, so a running query keeps its copy.
fn install_bytes(target: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let dir = target.parent().ok_or_else(|| std::io::Error::other("a target with no directory"))?;
    std::fs::create_dir_all(dir)?;
    let staging = dir.join(format!(".ling-docs-{}.part", std::process::id()));
    std::fs::write(&staging, bytes)?;
    std::fs::rename(&staging, target)
}

async fn fetch(client: &reqwest::Client, url: &str, expected: &str) -> Result<Vec<u8>, String> {
    let response = client.get(url).send().await.map_err(|error| format!("{url}: {error}"))?;
    let response = response.error_for_status().map_err(|error| format!("{url}: {error}"))?;
    let bytes = response.bytes().await.map_err(|error| format!("{url}: {error}"))?.to_vec();
    let got = hex_sha256(&bytes);
    if got != expected {
        return Err(format!("{url}: SHA-256 {got}, expected {expected}"));
    }
    Ok(bytes)
}

/// Installs whatever is missing under `install` for `arch`. Prints what it does.
pub async fn setup(install: &Path, arch: &str) -> Result<(), String> {
    let Some(libraries) = libraries(arch) else {
        return Err(format!("ling-docs has no pinned PDFium and ONNX Runtime for {arch}."));
    };
    let todo = missing(install, arch);
    if todo.is_empty() {
        println!("✅ ling-docs: PDFium, ONNX Runtime and the embedding model are installed.");
        return Ok(());
    }
    let client = reqwest::Client::builder()
        .user_agent(format!("ling/{}", crate::update::MIGHTLING_VERSION.unwrap_or("source")))
        .connect_timeout(std::time::Duration::from_secs(15))
        .build()
        .map_err(|error| error.to_string())?;
    for library in libraries {
        if !todo.iter().any(|name| name == library.name) {
            continue;
        }
        println!("⬇️  {} ({})...", library.name, library.url.rsplit('/').next().unwrap_or(library.url));
        let archive = fetch(&client, library.url, library.sha256).await?;
        let bytes = extract_member(&archive, library.member).map_err(|error| format!("{}: {error}", library.url))?;
        let target = lib_dir(install).join(library.name);
        install_bytes(&target, &bytes).map_err(|error| format!("cannot write {}: {error}", target.display()))?;
        std::fs::write(stamp_path(&target), format!("{}\n", library.sha256)).map_err(|error| error.to_string())?;
    }
    for (path, sha256, name) in MODEL_FILES {
        if !todo.iter().any(|missing| missing == name) {
            continue;
        }
        let url = format!("https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{path}");
        println!("⬇️  {name} ({MODEL_REPO}, {path})...");
        let bytes = fetch(&client, &url, sha256).await?;
        let target = model_dir(install).join(name);
        install_bytes(&target, &bytes).map_err(|error| format!("cannot write {}: {error}", target.display()))?;
    }
    println!("✅ ling-docs: installed in {} and {}", lib_dir(install).display(), model_dir(install).display());
    Ok(())
}

/// Removes the libraries and the model (`ling-docs` then waits for `ling docs setup`).
pub fn remove(install: &Path) {
    for dir in [lib_dir(install), model_dir(install)] {
        let _ = std::fs::remove_dir_all(dir);
    }
}

/// Whether `ling docs <word>` is one of these commands rather than `ling-docs`'s own.
pub fn handles(word: Option<&str>) -> bool {
    matches!(word, Some("setup" | "status" | "remove"))
}

/// Runs `ling docs setup|status|remove` and returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let Some(install) = install_dir() else {
        println!("❌ cannot find the install directory from this executable.");
        return 1;
    };
    let arch = std::env::consts::ARCH;
    match args.first().map(String::as_str) {
        Some("setup") if cfg!(target_os = "linux") => match setup(&install, arch).await {
            Ok(()) => 0,
            Err(error) => {
                println!("❌ ling-docs setup failed: {error}");
                1
            }
        },
        Some("setup") => {
            println!("ℹ️  The local file index runs on Linux only; there is nothing to set up here.");
            1
        }
        Some("status") => {
            let todo = missing(&install, arch);
            if todo.is_empty() {
                println!("✅ ling-docs: PDFium, ONNX Runtime and the embedding model are installed in {}.", install.display());
                0
            } else {
                println!("⚠️  ling-docs: missing or not at their pin: {}. Run `ling docs setup`.", todo.join(", "));
                1
            }
        }
        Some("remove") => {
            remove(&install);
            println!("✅ ling-docs: libraries and model removed from {}.", install.display());
            0
        }
        _ => {
            println!("{USAGE}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mightling-docs-setup-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn tgz(entries: &[(&str, &[u8])]) -> Vec<u8> {
        let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast()));
        for (path, bytes) in entries {
            let mut header = tar::Header::new_gnu();
            header.set_size(bytes.len() as u64);
            header.set_mode(0o644);
            header.set_cksum();
            builder.append_data(&mut header, path, *bytes).unwrap();
        }
        builder.into_inner().unwrap().finish().unwrap()
    }

    #[test]
    fn the_pins_are_the_nodes_and_cover_both_linux_machines() {
        for arch in ["aarch64", "x86_64"] {
            let [pdfium, onnx] = libraries(arch).unwrap();
            assert!(pdfium.url.contains("chromium%2F8076") && pdfium.member == "lib/libpdfium.so");
            assert!(onnx.url.contains(ONNXRUNTIME_VERSION) && onnx.member.ends_with("libonnxruntime.so.1.30.0"));
            for library in [pdfium, onnx] {
                assert_eq!(library.sha256.len(), 64, "{}", library.url);
            }
        }
        assert!(libraries("riscv64").is_none());
        assert_eq!(MODEL_FILES.len(), 2);
        assert!(PDFIUM_RELEASE.starts_with("chromium/") && MODEL_REVISION.len() == 40);
    }

    #[test]
    fn missing_names_what_is_absent_or_off_its_pin() {
        let install = scratch("missing");
        assert_eq!(missing(&install, "x86_64"), ["libpdfium.so", "libonnxruntime.so", "model.onnx", "tokenizer.json"]);
        // A library counts once its file and its pin are there; a model file by its own hash.
        let [pdfium, _] = libraries("x86_64").unwrap();
        let target = lib_dir(&install).join(pdfium.name);
        install_bytes(&target, b"not really pdfium").unwrap();
        assert!(missing(&install, "x86_64").contains(&"libpdfium.so".to_string()));
        std::fs::write(stamp_path(&target), format!("{}\n", pdfium.sha256)).unwrap();
        assert!(!missing(&install, "x86_64").contains(&"libpdfium.so".to_string()));
        let tokenizer = model_dir(&install).join("tokenizer.json");
        install_bytes(&tokenizer, b"{}").unwrap();
        assert!(missing(&install, "x86_64").contains(&"tokenizer.json".to_string()));
        remove(&install);
        assert!(!lib_dir(&install).exists() && !model_dir(&install).exists());
        let _ = std::fs::remove_dir_all(&install);
    }

    #[test]
    fn the_one_member_is_taken_from_the_archive() {
        let archive = tgz(&[("lib/README", b"x"), ("lib/libpdfium.so", b"ELF-not-really")]);
        assert_eq!(extract_member(&archive, "lib/libpdfium.so").unwrap(), b"ELF-not-really");
        assert!(extract_member(&archive, "lib/missing.so").unwrap_err().contains("not in the archive"));
        assert!(extract_member(b"not an archive", "x").is_err());
        assert_eq!(hex_sha256(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    }

    #[test]
    fn the_stamp_sits_beside_its_library_and_the_dirs_are_the_binarys_neighbours() {
        let install = Path::new("/opt/mightling");
        assert_eq!(stamp_path(&lib_dir(install).join("libpdfium.so")), lib_dir(install).join(".libpdfium.so.pin"));
        assert_eq!(lib_dir(install), Path::new("/opt/mightling/lib/ling-docs"));
        assert_eq!(model_dir(install), Path::new("/opt/mightling/models").join(MODEL_NAME));
        assert!(handles(Some("setup")) && handles(Some("status")) && handles(Some("remove")));
        assert!(!handles(Some("search")) && !handles(None));
    }
}
