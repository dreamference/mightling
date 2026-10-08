// Embeds the Mightling UI build in the binary (specs/DREAMFERENCE_MIGHTLING_ASK.md §4.1: no files
// to go stale, nothing loaded from elsewhere). `LING_WEB_UI_DIST` names the UI's `dist` folder
// (desktop/ui after `npm run build`; `CodexBrandedBuilder` builds it and sets this for every `ling`
// build); without it the table is empty and `/` serves a placeholder.

use std::path::Path;
use std::path::PathBuf;

fn walk(root: &Path, dir: &Path, files: &mut Vec<(String, PathBuf)>) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let mut entries: Vec<PathBuf> = entries.flatten().map(|entry| entry.path()).collect();
    entries.sort();
    for path in entries {
        if path.is_dir() {
            walk(root, &path, files);
        } else if let Ok(relative) = path.strip_prefix(root) {
            let name = relative.components().map(|part| part.as_os_str().to_string_lossy()).collect::<Vec<_>>().join("/");
            files.push((name, path.clone()));
        }
    }
}

fn main() {
    println!("cargo:rerun-if-env-changed=LING_WEB_UI_DIST");
    let out = PathBuf::from(std::env::var("OUT_DIR").expect("cargo sets OUT_DIR")).join("ui_assets.rs");
    let mut files = Vec::new();
    if let Some(dist) = std::env::var_os("LING_WEB_UI_DIST").filter(|dist| !dist.is_empty()) {
        // Absolute, so `include_bytes!` finds the files from OUT_DIR; not canonical, because on
        // Windows that is a verbatim (`\\?\`) path.
        let dist = std::path::absolute(PathBuf::from(dist)).expect("LING_WEB_UI_DIST is a usable path");
        assert!(dist.join("index.html").is_file(), "LING_WEB_UI_DIST={} has no index.html", dist.display());
        println!("cargo:rerun-if-changed={}", dist.display());
        walk(&dist, &dist, &mut files);
    }
    let mut code = String::from("pub static UI_ASSETS: &[(&str, &[u8])] = &[\n");
    for (name, path) in &files {
        println!("cargo:rerun-if-changed={}", path.display());
        code.push_str(&format!("    ({name:?}, include_bytes!({:?})),\n", path.display().to_string()));
    }
    code.push_str("];\n");
    std::fs::write(out, code).expect("write ui_assets.rs");
}
