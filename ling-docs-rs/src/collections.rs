//! Collections: the folders the user names (spec §5.1), stored in `docs.toml` in the agent's home.
//!
//! The file is user-level only. Nothing is ever read from a repository or from a folder being
//! indexed, so a cloned repository or a downloaded archive cannot add a collection, widen one or
//! change its exclusions (the rule `puffin-code.toml` follows). `~/Documents` and `~/Downloads` are
//! collections by default when they exist (§14.1); removing one is remembered in
//! `removed_defaults`, so it is never added back.

use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};

use crate::config;

/// One collection (§5.1).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Collection {
    pub name: String,
    /// Absolute and resolved.
    pub root: PathBuf,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub include: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub exclude: Vec<String>,
    #[serde(default = "default_max_file_mb")]
    pub max_file_mb: u64,
    #[serde(default)]
    pub follow_symlinks: bool,
    #[serde(default = "yes")]
    pub enabled: bool,
    /// One of the default collections (`documents`, `downloads`).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub default: bool,
}

fn default_max_file_mb() -> u64 {
    50
}

fn yes() -> bool {
    true
}

impl Collection {
    pub fn new(name: &str, root: PathBuf) -> Collection {
        Collection {
            name: name.to_string(),
            root,
            include: Vec::new(),
            exclude: Vec::new(),
            max_file_mb: default_max_file_mb(),
            follow_symlinks: false,
            enabled: true,
            default: false,
        }
    }
}

/// `docs.toml`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct DocsToml {
    /// Default collections the user removed: never added again.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub removed_defaults: Vec<String>,
    #[serde(default, rename = "collection")]
    pub collections: Vec<Collection>,
}

const HEADER: &str = "# ling-docs collections (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §5.1).\n# Change with `ling docs add|remove`; this file is read from your home folder only.\n\n";

impl DocsToml {
    /// Reads `docs.toml`; a missing file is an empty one. A file that does not parse is an error,
    /// so a typo never silently drops every collection.
    pub fn load_from(path: &Path) -> Result<DocsToml> {
        match std::fs::read_to_string(path) {
            Ok(text) => toml::from_str(&text).with_context(|| format!("{} does not parse", path.display())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(DocsToml::default()),
            Err(e) => Err(e).with_context(|| format!("reading {}", path.display())),
        }
    }

    pub fn load() -> Result<DocsToml> {
        DocsToml::load_from(&config::docs_toml())
    }

    /// Writes the file whole (a temporary file renamed over it), mode 600.
    pub fn save_to(&self, path: &Path) -> Result<()> {
        use std::os::unix::fs::OpenOptionsExt;
        use std::io::Write;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let staging = path.with_extension("toml.new");
        let mut file = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&staging)?;
        file.write_all(HEADER.as_bytes())?;
        file.write_all(toml::to_string_pretty(self)?.as_bytes())?;
        file.sync_all()?;
        std::fs::rename(&staging, path)?;
        Ok(())
    }

    pub fn save(&self) -> Result<()> {
        self.save_to(&config::docs_toml())
    }

    pub fn get(&self, name: &str) -> Option<&Collection> {
        self.collections.iter().find(|c| c.name == name)
    }

    pub fn enabled(&self) -> impl Iterator<Item = &Collection> {
        self.collections.iter().filter(|c| c.enabled)
    }

    /// Adds `documents` and `downloads` when their folders exist, the user has not removed them,
    /// and no collection has that name or root yet. Returns the names added.
    pub fn ensure_defaults(&mut self, user_home: &Path) -> Vec<String> {
        let mut added = Vec::new();
        for (name, folder) in default_folders(user_home) {
            if self.removed_defaults.iter().any(|r| r == name) || self.get(name).is_some() {
                continue;
            }
            let Ok(root) = folder.canonicalize() else { continue };
            if !root.is_dir() || self.collections.iter().any(|c| c.root == root) {
                continue;
            }
            if validate_root(&root, user_home).is_err() {
                continue;
            }
            let mut collection = Collection::new(name, root);
            collection.default = true;
            self.collections.push(collection);
            added.push(name.to_string());
        }
        added
    }

    /// Adds a collection after the checks of §5.2. Returns it.
    pub fn add(&mut self, folder: &Path, name: Option<&str>, user_home: &Path) -> Result<Collection> {
        let root = folder.canonicalize().with_context(|| format!("{} does not exist", folder.display()))?;
        if !root.is_dir() {
            bail!("{} is not a folder", root.display());
        }
        validate_root(&root, user_home)?;
        let name = match name {
            Some(name) => name.to_string(),
            None => name_from(&root),
        };
        validate_name(&name)?;
        if self.get(&name).is_some() {
            bail!("a collection named `{name}` exists already (choose another with --name)");
        }
        if let Some(other) = self.collections.iter().find(|c| c.root == root) {
            bail!("{} is the collection `{}` already", root.display(), other.name);
        }
        if let Some(other) = self.collections.iter().find(|c| root.starts_with(&c.root) || c.root.starts_with(&root)) {
            bail!("{} overlaps the collection `{}` ({}); remove that one first", root.display(), other.name, other.root.display());
        }
        let mut collection = Collection::new(&name, root);
        collection.default = DEFAULT_NAMES.contains(&name.as_str()) && default_folders(user_home).iter().any(|(n, f)| *n == name && f.canonicalize().ok().as_deref() == Some(&collection.root));
        // Adding back a default the user removed earlier is their choice now.
        self.removed_defaults.retain(|r| *r != name);
        self.collections.push(collection.clone());
        Ok(collection)
    }

    /// Removes a collection; a default one is remembered so it is never added again. The caller
    /// deletes its database.
    pub fn remove(&mut self, name: &str) -> Result<Collection> {
        let Some(index) = self.collections.iter().position(|c| c.name == name) else {
            bail!("no collection named `{name}`");
        };
        let removed = self.collections.remove(index);
        if (removed.default || DEFAULT_NAMES.contains(&name)) && !self.removed_defaults.iter().any(|r| r == name) {
            self.removed_defaults.push(name.to_string());
        }
        Ok(removed)
    }
}

/// The default collections' names.
pub const DEFAULT_NAMES: &[&str] = &["documents", "downloads"];

/// The default folders: the XDG user directories when `~/.config/user-dirs.dirs` names them
/// (a German desktop's `~/Dokumente`), else `~/Documents` and `~/Downloads`.
pub fn default_folders(user_home: &Path) -> Vec<(&'static str, PathBuf)> {
    let xdg = std::fs::read_to_string(user_home.join(".config/user-dirs.dirs")).unwrap_or_default();
    let lookup = |key: &str| -> Option<PathBuf> {
        let line = xdg.lines().find(|l| l.trim_start().starts_with(key))?;
        let value = line.split_once('=')?.1.trim().trim_matches('"');
        let path = if let Some(rest) = value.strip_prefix("$HOME") { user_home.join(rest.trim_start_matches('/')) } else { PathBuf::from(value) };
        // `XDG_DOCUMENTS_DIR="$HOME/"` means "none"; never the home folder itself.
        (path != user_home && path.is_absolute()).then_some(path)
    };
    vec![
        ("documents", lookup("XDG_DOCUMENTS_DIR").unwrap_or_else(|| user_home.join("Documents"))),
        ("downloads", lookup("XDG_DOWNLOAD_DIR").unwrap_or_else(|| user_home.join("Downloads"))),
    ]
}

/// A collection name: lower-case letters, digits, `-` and `_`, up to 40, starting with a letter.
pub fn validate_name(name: &str) -> Result<()> {
    let ok = !name.is_empty()
        && name.len() <= 40
        && name.chars().next().is_some_and(|c| c.is_ascii_lowercase())
        && name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '_');
    if !ok {
        bail!("`{name}` is not a collection name: use lower-case letters, digits, - and _ (up to 40, starting with a letter)");
    }
    Ok(())
}

fn name_from(root: &Path) -> String {
    let base = root.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    let mut name: String = base.chars().map(|c| if c.is_ascii_alphanumeric() { c } else { '-' }).collect();
    name = name.trim_matches('-').to_string();
    if !name.chars().next().is_some_and(|c| c.is_ascii_lowercase()) {
        name = format!("docs-{name}");
    }
    name.truncate(40);
    name.trim_end_matches('-').to_string()
}

/// Folders under the home that are never a collection nor inside one (§5.2), relative to it.
pub const SECRET_DIRS: &[&str] = &[
    ".puffin",
    ".mightling",
    ".codex",
    ".ssh",
    ".gnupg",
    ".config",
    ".password-store",
    ".local/share/keyrings",
    ".mozilla",
    ".thunderbird",
    "snap/firefox",
    "snap/chromium",
    ".var/app",
    ".aws",
    ".kube",
    ".docker",
];

/// Refuses `/`, the home folder, any folder containing it, and anything inside a secret folder.
pub fn validate_root(root: &Path, user_home: &Path) -> Result<()> {
    if root == Path::new("/") {
        bail!("`/` cannot be a collection: name the folders that hold documents instead");
    }
    let home = user_home.canonicalize().unwrap_or_else(|_| user_home.to_path_buf());
    if root == home || home.starts_with(root) {
        bail!("{} holds your whole home folder, with its keys and settings: name the folders that hold documents instead (for example ~/Documents)", root.display());
    }
    for secret in SECRET_DIRS {
        let secret = home.join(secret);
        if root.starts_with(&secret) {
            bail!("{} is inside {}, which is never indexed", root.display(), secret.display());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_appear_when_the_folders_exist_and_a_removal_is_remembered() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::create_dir(home.join("Documents")).unwrap();
        let mut docs = DocsToml::default();
        assert_eq!(docs.ensure_defaults(&home), vec!["documents".to_string()], "no Downloads folder, no downloads collection");
        std::fs::create_dir(home.join("Downloads")).unwrap();
        assert_eq!(docs.ensure_defaults(&home), vec!["downloads".to_string()]);
        assert!(docs.ensure_defaults(&home).is_empty());
        docs.remove("downloads").unwrap();
        let path = home.join(".puffin/docs.toml");
        docs.save_to(&path).unwrap();
        let mut again = DocsToml::load_from(&path).unwrap();
        assert!(again.ensure_defaults(&home).is_empty(), "a removed default is never added back");
        assert_eq!(again.removed_defaults, vec!["downloads".to_string()]);
        assert!(again.get("documents").unwrap().default);
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        // The user may add it back explicitly.
        again.add(&home.join("Downloads"), Some("downloads"), &home).unwrap();
        assert!(again.removed_defaults.is_empty());
    }

    #[test]
    fn the_home_folder_root_and_secret_folders_are_refused() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        for dir in [".ssh/keys", ".config/app", "notes"] {
            std::fs::create_dir_all(home.join(dir)).unwrap();
        }
        let mut docs = DocsToml::default();
        assert!(docs.add(&home, None, &home).unwrap_err().to_string().contains("whole home folder"));
        assert!(docs.add(Path::new("/"), None, &home).is_err());
        assert!(docs.add(home.parent().unwrap(), None, &home).is_err(), "a folder containing the home is refused");
        assert!(docs.add(&home.join(".ssh/keys"), None, &home).unwrap_err().to_string().contains("never indexed"));
        assert!(docs.add(&home.join(".config/app"), None, &home).is_err());
        let added = docs.add(&home.join("notes"), None, &home).unwrap();
        assert_eq!(added.name, "notes");
        assert!(docs.add(&home.join("notes"), Some("other"), &home).is_err(), "the same folder twice");
        assert!(docs.add(&home.join("notes"), Some("Bad Name"), &home).is_err());
    }

    #[test]
    fn xdg_user_dirs_name_the_defaults() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::create_dir_all(home.join(".config")).unwrap();
        std::fs::create_dir_all(home.join("Dokumente")).unwrap();
        std::fs::write(home.join(".config/user-dirs.dirs"), "XDG_DOCUMENTS_DIR=\"$HOME/Dokumente\"\nXDG_DOWNLOAD_DIR=\"$HOME/\"\n").unwrap();
        let mut docs = DocsToml::default();
        assert_eq!(docs.ensure_defaults(&home), vec!["documents".to_string()]);
        assert_eq!(docs.get("documents").unwrap().root, home.join("Dokumente"));
    }

    #[test]
    fn a_broken_file_is_an_error_not_an_empty_list() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("docs.toml");
        std::fs::write(&path, "[[collection]]\nname = ").unwrap();
        assert!(DocsToml::load_from(&path).is_err());
        assert_eq!(DocsToml::load_from(&dir.path().join("missing.toml")).unwrap(), DocsToml::default());
    }
}
