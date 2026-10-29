//! Discovery (spec §5.2, §7.1, §15.8): which files of a collection are indexed.
//!
//! **The walk opens nothing it will not index.** It reads directory entries and metadata only and
//! keeps files by extension; a disk image, an installer or a video in `~/Downloads` is never read
//! (on the synthetic Downloads of §15.8 that saved 10.4 GiB of reads). A kept file is then sniffed
//! (its first 8 KiB: a PDF must say `%PDF-`, a DOCX or ODT must be a ZIP archive, a mailbox must
//! start with a `From ` line, text must have no NUL byte) and hashed, so a
//! browser's `x (1).pdf` copy is indexed once; both happen only for files that are new or changed.
//!
//! Excluded whatever the globs say: hidden files and folders, the secret folders of
//! [`crate::collections::SECRET_DIRS`], package and build trees, and files named like secrets. A
//! `.lingignore` (gitignore syntax) can only exclude more. Symlinks are skipped unless the
//! collection follows them, and never lead out of its root.

use std::collections::{BTreeMap, HashSet};
use std::io::Read;
use std::path::{Path, PathBuf};

use anyhow::Result;
use globset::{Glob, GlobSet, GlobSetBuilder};
use ignore::gitignore::{Gitignore, GitignoreBuilder};
use sha2::{Digest, Sha256};

use crate::collections::{secret_dirs_in, Collection};

/// What a file is read as.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Text,
    Markdown,
    Rst,
    Org,
    Tex,
    Pdf,
    /// Word's `.docx` (Phase 2).
    Docx,
    /// OpenDocument text, `.odt` (Phase 2).
    Odt,
    /// `.html`, `.htm`, `.xhtml` (Phase 2).
    Html,
    /// One message, `.eml` (Phase 2).
    Eml,
    /// A mailbox of messages, `.mbox` (Phase 2).
    Mbox,
    /// `.csv` and `.tsv` (Phase 2).
    Csv,
    /// Excel workbooks: `.xlsx`, `.xlsm`, `.xlsb` (Phase 3).
    Xlsx,
    /// The legacy binary workbook, `.xls` (Phase 3).
    Xls,
    /// OpenDocument spreadsheet, `.ods` (Phase 3).
    Ods,
    /// PowerPoint, `.pptx` (Phase 3).
    Pptx,
    /// OpenDocument presentation, `.odp` (Phase 3).
    Odp,
    /// A picture read by OCR: `.png .jpg .jpeg .tif .tiff .webp .bmp .gif` (§20).
    Image,
}

impl Kind {
    /// Every kind, in the order `--type` lists them.
    pub const ALL: [Kind; 18] = [
        Kind::Text,
        Kind::Markdown,
        Kind::Rst,
        Kind::Org,
        Kind::Tex,
        Kind::Pdf,
        Kind::Docx,
        Kind::Odt,
        Kind::Html,
        Kind::Eml,
        Kind::Mbox,
        Kind::Csv,
        Kind::Xlsx,
        Kind::Xls,
        Kind::Ods,
        Kind::Pptx,
        Kind::Odp,
        Kind::Image,
    ];

    /// The kind of a file name, by extension only (§7.1: the formats of Phases 1, 2 and 3).
    pub fn of(name: &str) -> Option<Kind> {
        let ext = name.rsplit_once('.')?.1.to_ascii_lowercase();
        Some(match ext.as_str() {
            "txt" | "text" => Kind::Text,
            "md" | "markdown" => Kind::Markdown,
            "rst" => Kind::Rst,
            "org" => Kind::Org,
            "tex" => Kind::Tex,
            "pdf" => Kind::Pdf,
            "docx" => Kind::Docx,
            "odt" => Kind::Odt,
            "html" | "htm" | "xhtml" => Kind::Html,
            "eml" => Kind::Eml,
            "mbox" => Kind::Mbox,
            "csv" | "tsv" => Kind::Csv,
            "xlsx" | "xlsm" | "xlsb" => Kind::Xlsx,
            "xls" => Kind::Xls,
            "ods" => Kind::Ods,
            "pptx" => Kind::Pptx,
            "odp" => Kind::Odp,
            "png" | "jpg" | "jpeg" | "tif" | "tiff" | "webp" | "bmp" | "gif" => Kind::Image,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Text => "text",
            Kind::Markdown => "markdown",
            Kind::Rst => "rst",
            Kind::Org => "org",
            Kind::Tex => "tex",
            Kind::Pdf => "pdf",
            Kind::Docx => "docx",
            Kind::Odt => "odt",
            Kind::Html => "html",
            Kind::Eml => "eml",
            Kind::Mbox => "mbox",
            Kind::Csv => "csv",
            Kind::Xlsx => "xlsx",
            Kind::Xls => "xls",
            Kind::Ods => "ods",
            Kind::Pptx => "pptx",
            Kind::Odp => "odp",
            Kind::Image => "image",
        }
    }

    pub fn parse(text: &str) -> Option<Kind> {
        Kind::ALL.into_iter().find(|k| k.as_str() == text)
    }

    /// A ZIP archive (Office and OpenDocument files).
    pub fn is_zip(self) -> bool {
        matches!(
            self,
            Kind::Docx | Kind::Odt | Kind::Xlsx | Kind::Ods | Kind::Pptx | Kind::Odp
        )
    }

    /// What a locator over this kind's numbered lines calls them (§7.1's locators): paragraphs
    /// of a document, rows of a table, lines of everything else. PDFs are read by page.
    pub fn line_word(self) -> &'static str {
        match self {
            Kind::Docx | Kind::Odt | Kind::Html => "para",
            Kind::Csv => "rows",
            _ => "lines",
        }
    }
}

/// A file the index reads.
#[derive(Debug, Clone, PartialEq)]
pub struct Candidate {
    /// Relative to the collection root.
    pub rel: String,
    pub abs: PathBuf,
    pub size: u64,
    pub mtime_ns: i64,
    pub kind: Kind,
}

/// What a walk found.
#[derive(Debug, Default)]
pub struct Discovery {
    pub candidates: Vec<Candidate>,
    /// Files left out, by reason; nothing in this count was opened.
    pub skipped: BTreeMap<&'static str, u64>,
}

/// Folder names never entered: package and build trees.
const SKIP_DIRS: &[&str] = &[
    "node_modules",
    "__pycache__",
    "site-packages",
    "bower_components",
];

/// File names that look like secrets (§5.2).
fn secret_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    lower.starts_with(".env")
        || lower.starts_with("id_")
        || lower.starts_with("credentials")
        || [".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore", ".jks"]
            .iter()
            .any(|ext| lower.ends_with(ext) || lower.contains(&format!("{ext}.")))
}

/// Whether a folder is a build or cache tree by its own marker: `CACHEDIR.TAG` (Cargo's
/// `target/`, many caches) or a virtualenv's `pyvenv.cfg`.
fn build_tree(dir: &Path) -> bool {
    dir.join("CACHEDIR.TAG").is_file() || dir.join("pyvenv.cfg").is_file()
}

/// Whether the walk enters a folder of this name at this path: not hidden, not a package or
/// build tree (the change watch follows the same rule).
pub fn entered(name: &str, path: &Path) -> bool {
    !name.starts_with('.') && !SKIP_DIRS.contains(&name) && !build_tree(path)
}

/// Whether the walk itself reaches `target`, a resolved path inside `root`: no hidden, package or
/// build folder on the way, and not a hidden file or one named like a secret. A followed link
/// leads nowhere else (§5.2: excluded whatever the globs say).
fn walk_reaches(root: &Path, target: &Path) -> bool {
    let Ok(rel) = target.strip_prefix(root) else {
        return false;
    };
    let mut path = root.to_path_buf();
    for component in rel.components() {
        let name = component.as_os_str().to_string_lossy();
        path.push(&*name);
        if name.starts_with('.') {
            return false;
        }
        if path.is_file() {
            return !secret_name(&name);
        }
        if !entered(&name, &path) {
            return false;
        }
    }
    true
}

fn globs(patterns: &[String]) -> Option<GlobSet> {
    if patterns.is_empty() {
        return None;
    }
    let mut builder = GlobSetBuilder::new();
    for pattern in patterns {
        if let Ok(glob) = Glob::new(pattern) {
            builder.add(glob);
        }
    }
    builder.build().ok()
}

/// Walks a collection. Images are read only by OCR, so they are kept only when `images` is true
/// (`docs_ocr`, §20); otherwise they count as a type not read.
pub fn walk(collection: &Collection, user_home: &Path, images: bool) -> Discovery {
    walk_in(collection, user_home, &crate::config::home(), images)
}

/// [`walk`] with the agent's home named (where a project's knowledge folder may be a collection).
pub fn walk_in(
    collection: &Collection,
    user_home: &Path,
    codex_home: &Path,
    images: bool,
) -> Discovery {
    let mut out = Discovery::default();
    let root = match collection.root.canonicalize() {
        Ok(root) => root,
        Err(_) => return out,
    };
    let home = user_home
        .canonicalize()
        .unwrap_or_else(|_| user_home.to_path_buf());
    let secret: Vec<PathBuf> = secret_dirs_in(&root, &home, codex_home);
    let include = globs(&collection.include);
    let exclude = globs(&collection.exclude);
    let max_size = collection.max_file_mb.saturating_mul(1 << 20);
    let mut visited: HashSet<PathBuf> = HashSet::new();
    let mut stack: Vec<(PathBuf, Vec<Gitignore>)> = vec![(root.clone(), Vec::new())];
    while let Some((dir, mut ignores)) = stack.pop() {
        if !visited.insert(dir.clone()) {
            continue; // a symlink loop
        }
        let lingignore = dir.join(".lingignore");
        if lingignore.is_file() {
            let mut builder = GitignoreBuilder::new(&dir);
            builder.add(&lingignore);
            if let Ok(gi) = builder.build() {
                ignores.push(gi);
            }
        }
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        let mut entries: Vec<_> = entries.flatten().collect();
        entries.sort_by_key(|e| e.file_name());
        for entry in entries {
            let name = entry.file_name().to_string_lossy().into_owned();
            let walked = entry.path();
            let Ok(meta) = std::fs::symlink_metadata(&walked) else {
                continue;
            };
            if name.starts_with('.') {
                *out.skipped.entry("hidden").or_default() += 1;
                continue;
            }
            // Symlinks: skipped unless followed, and never out of the root.
            let (path, meta) = if meta.file_type().is_symlink() {
                if !collection.follow_symlinks {
                    *out.skipped.entry("symlink").or_default() += 1;
                    continue;
                }
                let Ok(target) = walked.canonicalize() else {
                    continue;
                };
                if !target.starts_with(&root) {
                    *out.skipped
                        .entry("symlink out of the collection")
                        .or_default() += 1;
                    continue;
                }
                if !walk_reaches(&root, &target) {
                    *out.skipped
                        .entry("symlink to what the walk skips")
                        .or_default() += 1;
                    continue;
                }
                let Ok(meta) = std::fs::metadata(&target) else {
                    continue;
                };
                (target, meta)
            } else {
                (walked.clone(), meta)
            };
            if secret.iter().any(|s| path.starts_with(s)) {
                *out.skipped.entry("excluded folder").or_default() += 1;
                continue;
            }
            let ignored = |is_dir: bool| -> bool {
                for gi in ignores.iter().rev() {
                    let m = gi.matched(&path, is_dir);
                    if m.is_ignore() {
                        return true;
                    }
                    if m.is_whitelist() {
                        return false;
                    }
                }
                false
            };
            if meta.is_dir() {
                if SKIP_DIRS.contains(&name.as_str()) || build_tree(&path) {
                    *out.skipped.entry("build or package folder").or_default() += 1;
                    continue;
                }
                if ignored(true) {
                    *out.skipped.entry(".lingignore").or_default() += 1;
                    continue;
                }
                stack.push((path, ignores.clone()));
                continue;
            }
            if !meta.is_file() {
                continue;
            }
            let Some(kind) = Kind::of(&name).filter(|k| images || *k != Kind::Image) else {
                *out.skipped.entry("type not read").or_default() += 1;
                continue;
            };
            if secret_name(&name) {
                *out.skipped.entry("named like a secret").or_default() += 1;
                continue;
            }
            // Named by where it was found (a link by its own name); read from where it points.
            let rel = walked
                .strip_prefix(&root)
                .map(|p| p.to_string_lossy().into_owned())
                .unwrap_or_else(|_| name.clone());
            if include.as_ref().is_some_and(|g| !g.is_match(&rel))
                || exclude.as_ref().is_some_and(|g| g.is_match(&rel))
            {
                *out.skipped.entry("collection globs").or_default() += 1;
                continue;
            }
            if ignored(false) {
                *out.skipped.entry(".lingignore").or_default() += 1;
                continue;
            }
            if meta.len() > max_size {
                *out.skipped.entry("too large").or_default() += 1;
                continue;
            }
            if meta.len() == 0 {
                *out.skipped.entry("empty").or_default() += 1;
                continue;
            }
            use std::os::unix::fs::MetadataExt;
            let mtime_ns = meta.mtime() * 1_000_000_000 + meta.mtime_nsec();
            out.candidates.push(Candidate {
                rel,
                abs: path,
                size: meta.len(),
                mtime_ns,
                kind,
            });
        }
    }
    out.candidates.sort_by(|a, b| a.rel.cmp(&b.rel));
    out
}

/// Bytes read to sniff a file (§7.1).
pub const SNIFF_BYTES: usize = 8192;

/// Checks that a file is what its extension says, from its first 8 KiB: a PDF says `%PDF-` near
/// the start, text has no NUL byte. Returns why not.
pub fn sniff(path: &Path, kind: Kind) -> std::result::Result<(), &'static str> {
    let mut head = vec![0u8; SNIFF_BYTES];
    let mut file = std::fs::File::open(path).map_err(|_| "unreadable")?;
    let mut read = 0;
    while read < head.len() {
        match file.read(&mut head[read..]) {
            Ok(0) => break,
            Ok(n) => read += n,
            Err(_) => return Err("unreadable"),
        }
    }
    head.truncate(read);
    sniff_bytes(&head, kind)
}

/// [`sniff`] on the first bytes of a file.
pub fn sniff_bytes(head: &[u8], kind: Kind) -> std::result::Result<(), &'static str> {
    match kind {
        Kind::Pdf => {
            let window = &head[..head.len().min(1024)];
            if window.windows(5).any(|w| w == b"%PDF-") {
                Ok(())
            } else {
                Err("not a PDF (no %PDF- header)")
            }
        }
        Kind::Image => {
            let magic: &[&[u8]] = &[
                b"\x89PNG\r\n\x1a\n",
                b"\xFF\xD8\xFF",
                b"II*\x00",
                b"MM\x00*",
                b"GIF87a",
                b"GIF89a",
                b"BM",
            ];
            let webp = head.len() >= 12 && &head[..4] == b"RIFF" && &head[8..12] == b"WEBP";
            if webp || magic.iter().any(|m| head.starts_with(m)) {
                Ok(())
            } else {
                Err("not an image (no PNG, JPEG, TIFF, WebP, GIF or BMP header)")
            }
        }
        Kind::Xls => {
            if head.starts_with(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1") {
                Ok(())
            } else {
                Err("not an Excel workbook (no OLE header)")
            }
        }
        k if k.is_zip() => {
            if head.starts_with(b"PK\x03\x04") {
                Ok(())
            } else {
                Err("not an Office or OpenDocument file (not a ZIP archive)")
            }
        }
        Kind::Mbox => {
            let text = head.strip_prefix(b"\xEF\xBB\xBF").unwrap_or(head);
            let start = text
                .iter()
                .position(|b| !b.is_ascii_whitespace())
                .unwrap_or(text.len());
            if head.contains(&0) {
                Err("binary content (NUL byte)")
            } else if text[start..].starts_with(b"From ") {
                Ok(())
            } else {
                Err("not a mailbox (no `From ` line first)")
            }
        }
        _ => {
            if head.contains(&0) {
                Err("binary content (NUL byte)")
            } else {
                Ok(())
            }
        }
    }
}

/// SHA-256 of a file, hex.
pub fn hash(path: &Path) -> Result<String> {
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(path: &Path, bytes: &[u8]) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, bytes).unwrap();
    }

    #[test]
    fn a_projects_knowledge_folder_is_walked_but_nothing_it_links_to_outside_it() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        let codex = home.join(".mightling");
        let knowledge = codex.join("projects/p-1/knowledge");
        write(&knowledge.join("checklist.md"), b"# Checklist");
        write(&home.join(".ssh/id_ed25519.md"), b"secret");
        write(&codex.join("sessions/rollout.md"), b"a session");
        write(&home.join("elsewhere/notes.md"), b"not the project's");
        std::os::unix::fs::symlink(home.join(".ssh/id_ed25519.md"), knowledge.join("key.md"))
            .unwrap();
        std::os::unix::fs::symlink(codex.join("sessions"), knowledge.join("sessions")).unwrap();
        std::os::unix::fs::symlink(home.join("elsewhere/notes.md"), knowledge.join("notes.md"))
            .unwrap();
        let mut collection = Collection::new("project-p-1", knowledge.clone());
        let found = walk_in(&collection, &home, &codex, true);
        let rels: Vec<_> = found.candidates.iter().map(|c| c.rel.clone()).collect();
        assert_eq!(rels, vec!["checklist.md".to_string()]);
        assert_eq!(found.skipped["symlink"], 3);
        // Followed, a link still never leads out of the folder.
        collection.follow_symlinks = true;
        let found = walk_in(&collection, &home, &codex, true);
        assert_eq!(
            found
                .candidates
                .iter()
                .map(|c| c.rel.clone())
                .collect::<Vec<_>>(),
            vec!["checklist.md".to_string()]
        );
        assert_eq!(found.skipped["symlink out of the collection"], 3);
        // A sibling of the knowledge folder, and a knowledge folder that is a link to a secret one, stay refused.
        assert!(
            crate::collections::validate_root_in(&codex.join("projects/p-1"), &home, &codex)
                .is_err()
        );
        std::fs::create_dir_all(codex.join("projects/p-2")).unwrap();
        std::os::unix::fs::symlink(home.join(".ssh"), codex.join("projects/p-2/knowledge"))
            .unwrap();
        let linked = codex.join("projects/p-2/knowledge").canonicalize().unwrap();
        assert!(crate::collections::validate_root_in(&linked, &home, &codex).is_err());
    }

    #[test]
    fn only_readable_types_are_kept_and_the_rest_is_never_opened() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        let root = home.join("Downloads");
        write(&root.join("a.pdf"), b"%PDF-1.7 ...");
        write(&root.join("notes.md"), b"# Notes\n");
        write(&root.join("big.iso"), b"not read");
        write(&root.join("setup.deb"), b"not read");
        write(&root.join(".hidden.md"), b"x");
        write(&root.join("node_modules/pkg/README.md"), b"x");
        write(&root.join("proj/target/CACHEDIR.TAG"), b"x");
        write(&root.join("proj/target/doc.md"), b"x");
        write(&root.join("keys/server.key.txt"), b"x");
        write(&root.join("credentials.txt"), b"x");
        write(&root.join("sub/.lingignore"), b"private/\n*.tex\n");
        write(&root.join("sub/private/plan.md"), b"x");
        write(&root.join("sub/paper.tex"), b"x");
        write(&root.join("sub/ok.txt"), b"x");
        write(&root.join("empty.txt"), b"");
        // An unreadable file of a skipped type: the walk would fail if it opened it.
        write(&root.join("locked.zip"), b"x");
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(
            root.join("locked.zip"),
            std::fs::Permissions::from_mode(0o000),
        )
        .unwrap();
        let collection = Collection::new("downloads", root.clone());
        let found = walk(&collection, &home, true);
        let rels: Vec<&str> = found.candidates.iter().map(|c| c.rel.as_str()).collect();
        assert_eq!(rels, ["a.pdf", "notes.md", "sub/ok.txt"]);
        assert_eq!(found.skipped["type not read"], 3);
        assert_eq!(found.skipped[".lingignore"], 2);
        assert_eq!(found.skipped["named like a secret"], 2);
        assert!(found.skipped["build or package folder"] >= 2);
        assert_eq!(found.skipped["empty"], 1);
    }

    #[test]
    fn symlinks_are_skipped_or_kept_inside_the_root() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        let root = home.join("docs");
        write(&root.join("in.md"), b"x");
        write(&home.join("outside/secret.md"), b"x");
        std::os::unix::fs::symlink(home.join("outside/secret.md"), root.join("escape.md")).unwrap();
        std::os::unix::fs::symlink(root.join("in.md"), root.join("alias.md")).unwrap();
        std::os::unix::fs::symlink(&root, root.join("loop")).unwrap();
        let mut collection = Collection::new("docs", root.clone());
        let rels = |c: &Collection| {
            walk(c, &home, true)
                .candidates
                .iter()
                .map(|c| c.rel.clone())
                .collect::<Vec<_>>()
        };
        assert_eq!(rels(&collection), ["in.md"]);
        collection.follow_symlinks = true;
        // The alias is kept under its own name (the hash makes it a duplicate later); the escape
        // and the loop add nothing.
        assert_eq!(rels(&collection), ["alias.md", "in.md"]);
    }

    #[test]
    fn a_followed_link_reaches_nothing_the_walk_itself_skips() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        let root = home.join("docs");
        write(&root.join("in.md"), b"x");
        write(&root.join(".private/diary.md"), b"x");
        write(&root.join("node_modules/pkg/README.md"), b"x");
        write(&root.join("proj/target/CACHEDIR.TAG"), b"x");
        write(&root.join("proj/target/doc.md"), b"x");
        write(&root.join("keys/credentials.txt"), b"x");
        let link = |target: &str, name: &str| {
            std::os::unix::fs::symlink(root.join(target), root.join(name)).unwrap()
        };
        link(".private/diary.md", "diary.md");
        link(".private", "private");
        link("node_modules/pkg/README.md", "readme.md");
        link("proj/target/doc.md", "built.md");
        link("proj/target", "built");
        link("keys/credentials.txt", "keys.txt");
        let mut collection = Collection::new("docs", root.clone());
        collection.follow_symlinks = true;
        let found = walk(&collection, &home, true);
        let rels: Vec<_> = found.candidates.iter().map(|c| c.rel.as_str()).collect();
        assert_eq!(rels, ["in.md"]);
        assert_eq!(found.skipped["symlink to what the walk skips"], 6);
    }

    #[test]
    fn a_lying_extension_is_caught_by_the_sniff() {
        let dir = tempfile::tempdir().unwrap();
        let fake = dir.path().join("movie.pdf");
        std::fs::write(&fake, b"\x00\x00\x00\x18ftypmp42").unwrap();
        assert!(sniff(&fake, Kind::Pdf).is_err());
        let text = dir.path().join("x.txt");
        std::fs::write(&text, b"abc\x00def").unwrap();
        assert!(sniff(&text, Kind::Text).is_err());
        let pdf = dir.path().join("y.pdf");
        std::fs::write(&pdf, b"\n%PDF-1.4\n").unwrap();
        assert!(sniff(&pdf, Kind::Pdf).is_ok());
        // Pictures by their own headers (§20).
        for head in [
            &b"\x89PNG\r\n\x1a\n...."[..],
            b"\xFF\xD8\xFF\xE0..JFIF",
            b"RIFF\x00\x00\x00\x00WEBPVP8 ",
            b"II*\x00....",
            b"GIF89a..",
            b"BM......",
        ] {
            assert!(sniff_bytes(head, Kind::Image).is_ok(), "{head:?}");
        }
        assert!(sniff_bytes(b"%PDF-1.4", Kind::Image).is_err());
        assert_eq!(Kind::of("Scan 3.JPEG"), Some(Kind::Image));
        assert_eq!(Kind::of("photo.heic"), None, "no pure-Rust decoder");
        assert_eq!(hash(&pdf).unwrap().len(), 64);
    }

    #[test]
    fn collection_globs_max_size_and_image_flag_filter_candidates() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        let root = home.join("docs");
        write(&root.join("keep/a.md"), b"hello");
        write(&root.join("keep/big.md"), &[b'x'; 2 * 1024 * 1024]);
        write(&root.join("keep/scan.png"), b"\x89PNG\r\n\x1a\n");
        write(&root.join("drop/b.md"), b"hello");
        let mut c = Collection::new("docs", root);
        c.include = vec!["keep/**".into()];
        c.exclude = vec!["**/scan*".into()];
        c.max_file_mb = 1;
        let found = walk(&c, &home, true);
        assert_eq!(
            found
                .candidates
                .iter()
                .map(|x| x.rel.as_str())
                .collect::<Vec<_>>(),
            ["keep/a.md"]
        );
        assert_eq!(found.skipped["collection globs"], 2);
        assert_eq!(found.skipped["too large"], 1);

        // With images=false, image files are counted as "type not read".
        c.include.clear();
        c.exclude.clear();
        let no_images = walk(&c, &home, false);
        assert!(!no_images.candidates.iter().any(|x| x.kind == Kind::Image));
        assert_eq!(no_images.skipped["type not read"], 1);
    }
}
