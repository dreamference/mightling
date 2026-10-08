//! Discovery (spec §5.2, §7.1, §15.8): which files of a collection are indexed.
//!
//! **The walk opens nothing it will not index.** It reads directory entries and metadata only and
//! keeps files by extension; a disk image, an installer or a video in `~/Downloads` is never read
//! (on the synthetic Downloads of §15.8 that saved 10.4 GiB of reads). A kept file is then sniffed
//! (its first 8 KiB: a PDF must say `%PDF-`, text must have no NUL byte) and hashed, so a
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

use crate::collections::{Collection, SECRET_DIRS};

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
}

impl Kind {
    /// The kind of a file name, by extension only (§7.1, v1 formats of Phase 1).
    pub fn of(name: &str) -> Option<Kind> {
        let ext = name.rsplit_once('.')?.1.to_ascii_lowercase();
        Some(match ext.as_str() {
            "txt" | "text" => Kind::Text,
            "md" | "markdown" => Kind::Markdown,
            "rst" => Kind::Rst,
            "org" => Kind::Org,
            "tex" => Kind::Tex,
            "pdf" => Kind::Pdf,
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
        }
    }

    pub fn parse(text: &str) -> Option<Kind> {
        [Kind::Text, Kind::Markdown, Kind::Rst, Kind::Org, Kind::Tex, Kind::Pdf].into_iter().find(|k| k.as_str() == text)
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
const SKIP_DIRS: &[&str] = &["node_modules", "__pycache__", "site-packages", "bower_components"];

/// File names that look like secrets (§5.2).
fn secret_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    lower.starts_with(".env")
        || lower.starts_with("id_")
        || lower.starts_with("credentials")
        || [".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore", ".jks"].iter().any(|ext| lower.ends_with(ext) || lower.contains(&format!("{ext}.")))
}

/// Whether a folder is a build or cache tree by its own marker: `CACHEDIR.TAG` (Cargo's
/// `target/`, many caches) or a virtualenv's `pyvenv.cfg`.
fn build_tree(dir: &Path) -> bool {
    dir.join("CACHEDIR.TAG").is_file() || dir.join("pyvenv.cfg").is_file()
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

/// Walks a collection.
pub fn walk(collection: &Collection, user_home: &Path) -> Discovery {
    let mut out = Discovery::default();
    let root = match collection.root.canonicalize() {
        Ok(root) => root,
        Err(_) => return out,
    };
    let home = user_home.canonicalize().unwrap_or_else(|_| user_home.to_path_buf());
    let secret: Vec<PathBuf> = SECRET_DIRS.iter().map(|d| home.join(d)).collect();
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
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        let mut entries: Vec<_> = entries.flatten().collect();
        entries.sort_by_key(|e| e.file_name());
        for entry in entries {
            let name = entry.file_name().to_string_lossy().into_owned();
            let walked = entry.path();
            let Ok(meta) = std::fs::symlink_metadata(&walked) else { continue };
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
                let Ok(target) = walked.canonicalize() else { continue };
                if !target.starts_with(&root) {
                    *out.skipped.entry("symlink out of the collection").or_default() += 1;
                    continue;
                }
                let Ok(meta) = std::fs::metadata(&target) else { continue };
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
            let Some(kind) = Kind::of(&name) else {
                *out.skipped.entry("type not read").or_default() += 1;
                continue;
            };
            if secret_name(&name) {
                *out.skipped.entry("named like a secret").or_default() += 1;
                continue;
            }
            // Named by where it was found (a link by its own name); read from where it points.
            let rel = walked.strip_prefix(&root).map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| name.clone());
            if include.as_ref().is_some_and(|g| !g.is_match(&rel)) || exclude.as_ref().is_some_and(|g| g.is_match(&rel)) {
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
            out.candidates.push(Candidate { rel, abs: path, size: meta.len(), mtime_ns, kind });
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
    match kind {
        Kind::Pdf => {
            let window = &head[..head.len().min(1024)];
            if window.windows(5).any(|w| w == b"%PDF-") { Ok(()) } else { Err("not a PDF (no %PDF- header)") }
        }
        _ => {
            if head.contains(&0) { Err("binary content (NUL byte)") } else { Ok(()) }
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
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(path: &Path, bytes: &[u8]) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, bytes).unwrap();
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
        std::fs::set_permissions(root.join("locked.zip"), std::fs::Permissions::from_mode(0o000)).unwrap();
        let collection = Collection::new("downloads", root.clone());
        let found = walk(&collection, &home);
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
        let rels = |c: &Collection| walk(c, &home).candidates.iter().map(|c| c.rel.clone()).collect::<Vec<_>>();
        assert_eq!(rels(&collection), ["in.md"]);
        collection.follow_symlinks = true;
        // The alias is kept under its own name (the hash makes it a duplicate later); the escape
        // and the loop add nothing.
        assert_eq!(rels(&collection), ["alias.md", "in.md"]);
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
        assert_eq!(hash(&pdf).unwrap().len(), 64);
    }
}
