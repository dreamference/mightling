//! Collections: the folders the user names (spec §5.1), stored in `docs.toml` in the agent's home.
//!
//! The file is user-level only. Nothing is ever read from a repository or from a folder being
//! indexed, so a cloned repository or a downloaded archive cannot add a collection, widen one or
//! change its exclusions (the rule `ling-code.toml` follows). `~/Documents` and `~/Downloads` are
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
            Ok(text) => {
                toml::from_str(&text).with_context(|| format!("{} does not parse", path.display()))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(DocsToml::default()),
            Err(e) => Err(e).with_context(|| format!("reading {}", path.display())),
        }
    }

    pub fn load() -> Result<DocsToml> {
        DocsToml::load_from(&config::docs_toml())
    }

    /// Writes the file whole (a temporary file renamed over it), mode 600.
    pub fn save_to(&self, path: &Path) -> Result<()> {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        // A staging file of this save's own: two writers (`ling docs add` beside the session) never
        // share one, and one left behind with another mode is not reused.
        static SAVES: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = SAVES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let staging = path.with_extension(format!("toml.{}-{n}.new", std::process::id()));
        let written = (|| -> Result<()> {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&staging)?;
            file.write_all(HEADER.as_bytes())?;
            file.write_all(toml::to_string_pretty(self)?.as_bytes())?;
            file.sync_all()?;
            std::fs::rename(&staging, path)?;
            Ok(())
        })();
        if written.is_err() {
            let _ = std::fs::remove_file(&staging);
        }
        written
    }

    pub fn save(&self) -> Result<()> {
        self.save_to(&config::docs_toml())
    }

    /// Changes `docs.toml` under its lock (`docs.toml.lock`): read afresh, changed by `change`,
    /// written back when it changed. Two writers at once (`ling docs add` beside the session adding
    /// the defaults) never lose one another's change. Returns the file as left and what `change`
    /// returned.
    pub fn update_at<T>(
        path: &Path,
        change: impl FnOnce(&mut DocsToml) -> Result<T>,
    ) -> Result<(DocsToml, T)> {
        use std::os::unix::fs::OpenOptionsExt;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let lock = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .open(path.with_extension("toml.lock"))?;
        crate::host::flock(&lock, true)?;
        let mut docs = DocsToml::load_from(path)?;
        let before = docs.clone();
        let out = change(&mut docs)?;
        if docs != before {
            docs.save_to(path)?;
        }
        Ok((docs, out))
    }

    /// [`DocsToml::update_at`] on the agent home's `docs.toml`.
    pub fn update<T>(change: impl FnOnce(&mut DocsToml) -> Result<T>) -> Result<(DocsToml, T)> {
        DocsToml::update_at(&config::docs_toml(), change)
    }

    pub fn get(&self, name: &str) -> Option<&Collection> {
        self.collections.iter().find(|c| c.name == name)
    }

    pub fn enabled(&self) -> impl Iterator<Item = &Collection> {
        self.collections.iter().filter(|c| c.enabled)
    }

    /// Adds `documents` and `downloads` when their folders exist, the user has not removed them,
    /// and no collection has that name yet or a root inside, at or around theirs. Returns the names
    /// added.
    pub fn ensure_defaults(&mut self, user_home: &Path) -> Vec<String> {
        let mut added = Vec::new();
        for (name, folder) in default_folders(user_home) {
            if self.removed_defaults.iter().any(|r| r == name) || self.get(name).is_some() {
                continue;
            }
            let Ok(root) = folder.canonicalize() else {
                continue;
            };
            // A folder inside another collection, or holding one, would be indexed twice.
            if !root.is_dir()
                || self
                    .collections
                    .iter()
                    .any(|c| root.starts_with(&c.root) || c.root.starts_with(&root))
            {
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
    pub fn add(
        &mut self,
        folder: &Path,
        name: Option<&str>,
        user_home: &Path,
    ) -> Result<Collection> {
        let root = folder
            .canonicalize()
            .with_context(|| format!("{} does not exist", folder.display()))?;
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
            bail!(
                "{} is the collection `{}` already",
                root.display(),
                other.name
            );
        }
        if let Some(other) = self
            .collections
            .iter()
            .find(|c| root.starts_with(&c.root) || c.root.starts_with(&root))
        {
            bail!(
                "{} overlaps the collection `{}` ({}); remove that one first",
                root.display(),
                other.name,
                other.root.display()
            );
        }
        let mut collection = Collection::new(&name, root);
        collection.default = DEFAULT_NAMES.contains(&name.as_str())
            && default_folders(user_home).iter().any(|(n, f)| {
                *n == name && f.canonicalize().ok().as_deref() == Some(&collection.root)
            });
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
        if (removed.default || DEFAULT_NAMES.contains(&name))
            && !self.removed_defaults.iter().any(|r| r == name)
        {
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
        let path = if let Some(rest) = value.strip_prefix("$HOME") {
            user_home.join(rest.trim_start_matches('/'))
        } else {
            PathBuf::from(value)
        };
        // `XDG_DOCUMENTS_DIR="$HOME/"` means "none"; never the home folder itself.
        (path != user_home && path.is_absolute()).then_some(path)
    };
    vec![
        (
            "documents",
            lookup("XDG_DOCUMENTS_DIR").unwrap_or_else(|| user_home.join("Documents")),
        ),
        (
            "downloads",
            lookup("XDG_DOWNLOAD_DIR").unwrap_or_else(|| user_home.join("Downloads")),
        ),
    ]
}

/// A collection name: lower-case letters, digits, `-` and `_`, up to 40, starting with a letter.
pub fn validate_name(name: &str) -> Result<()> {
    let ok = !name.is_empty()
        && name.len() <= 40
        && name.chars().next().is_some_and(|c| c.is_ascii_lowercase())
        && name
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '_');
    if !ok {
        bail!("`{name}` is not a collection name: use lower-case letters, digits, - and _ (up to 40, starting with a letter)");
    }
    if crate::config::is_pdf_cache_name(name) {
        bail!("`{name}` is the form of pdf_search's cache entries (pdf- and 12 hex digits): choose another name");
    }
    Ok(())
}

fn name_from(root: &Path) -> String {
    let base = root
        .file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let mut name: String = base
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect();
    name = name.trim_matches('-').to_string();
    if !name.chars().next().is_some_and(|c| c.is_ascii_lowercase()) {
        name = format!("docs-{name}");
    }
    name.truncate(40);
    name.trim_end_matches('-').to_string()
}

/// Folders under the home that are never a collection nor inside one (§5.2), relative to it.
pub const SECRET_DIRS: &[&str] = &[
    ".mightling",
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

/// A desktop project's knowledge folder, `$CODEX_HOME/projects/<id>/knowledge` (DESKTOP_PARITY §4.5
/// item 13, DESKTOP_PARITY_CLAUDE §4.7): the copies a user gave a project so that its threads read
/// them. The one folder inside a secret folder that may be a collection, since everything in it was
/// put there to be read; nothing else under `$CODEX_HOME` is.
pub fn is_project_knowledge(root: &Path, codex_home: &Path) -> bool {
    let projects = codex_home
        .canonicalize()
        .unwrap_or_else(|_| codex_home.to_path_buf())
        .join("projects");
    let Some(project) = root.parent() else {
        return false;
    };
    let id_ok = project
        .file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|id| {
            !id.is_empty()
                && id.len() <= 128
                && id
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        });
    root.file_name().is_some_and(|n| n == "knowledge")
        && project.parent() == Some(projects.as_path())
        && id_ok
}

/// Every secret folder under `home`, as named and, when it is a link (`~/.ssh` kept in a dotfiles
/// folder), as resolved: the folder a link points to is as secret as the link.
pub fn secret_paths(home: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for dir in SECRET_DIRS {
        let named = home.join(dir);
        if let Ok(resolved) = named.canonicalize() {
            if resolved != named && !out.contains(&resolved) {
                out.push(resolved);
            }
        }
        if !out.contains(&named) {
            out.push(named);
        }
    }
    out
}

/// The secret folders a walk of `root` skips: all of them, except the one holding a project's knowledge folder when `root` is one.
pub fn secret_dirs(root: &Path, home: &Path) -> Vec<PathBuf> {
    secret_dirs_in(root, home, &config::home())
}

/// [`secret_dirs`] with the agent's home named.
pub fn secret_dirs_in(root: &Path, home: &Path, codex_home: &Path) -> Vec<PathBuf> {
    let knowledge = is_project_knowledge(root, codex_home);
    secret_paths(home)
        .into_iter()
        .filter(|s| !(knowledge && root.starts_with(s)))
        .collect()
}

/// Refuses `/`, the home folder, any folder containing it, and anything inside a secret folder.
pub fn validate_root(root: &Path, user_home: &Path) -> Result<()> {
    validate_root_in(root, user_home, &config::home())
}

/// [`validate_root`] with the agent's home named, a project's knowledge folder in it allowed.
pub fn validate_root_in(root: &Path, user_home: &Path, codex_home: &Path) -> Result<()> {
    if root == Path::new("/") {
        bail!("`/` cannot be a collection: name the folders that hold documents instead");
    }
    let home = user_home
        .canonicalize()
        .unwrap_or_else(|_| user_home.to_path_buf());
    if root == home || home.starts_with(root) {
        bail!("{} holds your whole home folder, with its keys and settings: name the folders that hold documents instead (for example ~/Documents)", root.display());
    }
    if is_project_knowledge(root, codex_home) {
        return Ok(());
    }
    for secret in secret_paths(&home) {
        if root.starts_with(&secret) {
            bail!(
                "{} is inside {}, which is never indexed",
                root.display(),
                secret.display()
            );
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
        assert_eq!(
            docs.ensure_defaults(&home),
            vec!["documents".to_string()],
            "no Downloads folder, no downloads collection"
        );
        std::fs::create_dir(home.join("Downloads")).unwrap();
        assert_eq!(docs.ensure_defaults(&home), vec!["downloads".to_string()]);
        assert!(docs.ensure_defaults(&home).is_empty());
        docs.remove("downloads").unwrap();
        let path = home.join(".mightling/docs.toml");
        docs.save_to(&path).unwrap();
        let mut again = DocsToml::load_from(&path).unwrap();
        assert!(
            again.ensure_defaults(&home).is_empty(),
            "a removed default is never added back"
        );
        assert_eq!(again.removed_defaults, vec!["downloads".to_string()]);
        assert!(again.get("documents").unwrap().default);
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        // The user may add it back explicitly.
        again
            .add(&home.join("Downloads"), Some("downloads"), &home)
            .unwrap();
        assert!(again.removed_defaults.is_empty());
    }

    #[test]
    fn a_desktop_projects_knowledge_folder_is_the_one_collection_allowed_inside_the_agents_home() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        let codex = home.join(".mightling");
        let knowledge = codex.join("projects/p-1a2b/knowledge");
        for dir in [
            knowledge.as_path(),
            &codex.join("projects/p-1a2b/other"),
            &codex.join("sessions"),
            &codex.join("projects/bad id/knowledge"),
            &codex.join("sessions/p-1a2b/knowledge"),
        ] {
            std::fs::create_dir_all(dir).unwrap();
        }
        assert!(is_project_knowledge(&knowledge, &codex));
        assert!(validate_root_in(&knowledge, &home, &codex).is_ok());
        for refused in [
            codex.join("projects/p-1a2b/other"),
            codex.join("sessions"),
            codex.join("projects/bad id/knowledge"),
            codex.join("sessions/p-1a2b/knowledge"),
            codex.join("projects"),
        ] {
            assert!(
                !is_project_knowledge(&refused, &codex),
                "{}",
                refused.display()
            );
            assert!(
                validate_root_in(&refused, &home, &codex).is_err(),
                "{}",
                refused.display()
            );
        }
        // A project id is at most 128 characters.
        let longest = codex.join(format!("projects/{}/knowledge", "a".repeat(128)));
        let too_long = codex.join(format!("projects/{}/knowledge", "a".repeat(129)));
        assert!(is_project_knowledge(&longest, &codex));
        assert!(!is_project_knowledge(&too_long, &codex));
        // An agent home named through a link holds the same knowledge folders.
        let link = home.join("agent-link");
        std::os::unix::fs::symlink(&codex, &link).unwrap();
        assert!(is_project_knowledge(&knowledge, &link));
        // A walk of the knowledge folder skips every secret folder but the one it is in.
        let skipped = secret_dirs_in(&knowledge, &home, &codex);
        assert!(!skipped.contains(&home.join(".mightling")));
        assert!(skipped.contains(&home.join(".ssh")));
        assert!(secret_dirs_in(&home.join("Documents"), &home, &codex)
            .contains(&home.join(".mightling")));
    }

    #[test]
    fn the_home_folder_root_and_secret_folders_are_refused() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        for dir in [".ssh/keys", ".config/app", "notes"] {
            std::fs::create_dir_all(home.join(dir)).unwrap();
        }
        let mut docs = DocsToml::default();
        assert!(docs
            .add(&home, None, &home)
            .unwrap_err()
            .to_string()
            .contains("whole home folder"));
        assert!(docs.add(Path::new("/"), None, &home).is_err());
        assert!(
            docs.add(home.parent().unwrap(), None, &home).is_err(),
            "a folder containing the home is refused"
        );
        assert!(docs
            .add(&home.join(".ssh/keys"), None, &home)
            .unwrap_err()
            .to_string()
            .contains("never indexed"));
        assert!(docs.add(&home.join(".config/app"), None, &home).is_err());
        let added = docs.add(&home.join("notes"), None, &home).unwrap();
        assert_eq!(added.name, "notes");
        assert!(
            docs.add(&home.join("notes"), Some("other"), &home).is_err(),
            "the same folder twice"
        );
        assert!(docs
            .add(&home.join("notes"), Some("Bad Name"), &home)
            .is_err());
    }

    #[test]
    fn a_home_named_through_a_link_is_still_refused_with_its_secret_folders() {
        let dir = tempfile::tempdir().unwrap();
        let real = dir.path().canonicalize().unwrap().join("real");
        std::fs::create_dir_all(real.join(".ssh/keys")).unwrap();
        let link = dir.path().join("home");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        let codex = link.join(".mightling");
        assert!(validate_root_in(&real, &link, &codex)
            .unwrap_err()
            .to_string()
            .contains("whole home folder"));
        assert!(validate_root_in(real.parent().unwrap(), &link, &codex).is_err());
        assert!(validate_root_in(&real.join(".ssh/keys"), &link, &codex)
            .unwrap_err()
            .to_string()
            .contains("never indexed"));
    }

    #[test]
    fn every_secret_folder_and_what_is_inside_it_is_refused() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        let codex = home.join(".mightling");
        for dir in [
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
        ] {
            let inside = home.join(dir).join("inner");
            std::fs::create_dir_all(&inside).unwrap();
            for root in [home.join(dir), inside] {
                assert!(
                    validate_root_in(&root, &home, &codex).is_err(),
                    "{}",
                    root.display()
                );
            }
        }
    }

    #[test]
    fn a_folder_holding_a_collection_and_a_file_are_refused() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::create_dir_all(home.join("notes/sub")).unwrap();
        std::fs::write(home.join("notes.txt"), "x").unwrap();
        let mut docs = DocsToml::default();
        docs.add(&home.join("notes/sub"), Some("sub"), &home)
            .unwrap();
        assert!(docs.add(&home.join("notes"), None, &home).is_err());
        assert!(docs
            .add(&home.join("notes.txt"), Some("file"), &home)
            .unwrap_err()
            .to_string()
            .contains("not a folder"));
        assert_eq!(docs.collections.len(), 1);
    }

    #[test]
    fn a_default_folder_around_a_collection_or_in_a_secret_folder_is_not_added() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::create_dir_all(home.join("Documents/work")).unwrap();
        std::fs::create_dir_all(home.join(".ssh/downloads")).unwrap();
        std::fs::create_dir_all(home.join(".config")).unwrap();
        std::fs::write(
            home.join(".config/user-dirs.dirs"),
            "XDG_DOWNLOAD_DIR=\"$HOME/.ssh/downloads\"\n",
        )
        .unwrap();
        let mut docs = DocsToml::default();
        docs.add(&home.join("Documents/work"), Some("work"), &home)
            .unwrap();
        assert!(docs.ensure_defaults(&home).is_empty(), "{docs:?}");
    }

    #[test]
    fn a_default_folder_that_is_a_file_is_not_added() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::write(home.join("Documents"), b"not a folder").unwrap();
        let mut docs = DocsToml::default();
        assert!(docs.ensure_defaults(&home).is_empty(), "{docs:?}");
    }

    #[test]
    fn a_second_folder_under_a_name_in_use_is_refused() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::create_dir_all(home.join("a/notes")).unwrap();
        std::fs::create_dir_all(home.join("b/notes")).unwrap();
        let mut docs = DocsToml::default();
        docs.add(&home.join("a/notes"), None, &home).unwrap();
        let err = docs.add(&home.join("b/notes"), None, &home).unwrap_err();
        assert!(err.to_string().contains("exists already"), "{err}");
        assert_eq!(docs.collections.len(), 1);
    }

    #[test]
    fn two_writers_at_once_keep_both_changes() {
        // One writer holds the lock while it saves; the other, started first, waits and then
        // reads the file afresh, so neither change is lost.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("docs.toml");
        let lock = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .open(path.with_extension("toml.lock"))
            .unwrap();
        crate::host::flock(&lock, true).unwrap();
        let other = {
            let path = path.clone();
            let root = dir.path().join("b");
            std::thread::spawn(move || {
                DocsToml::update_at(&path, |docs| {
                    docs.collections.push(Collection::new("b", root));
                    Ok(())
                })
                .unwrap()
            })
        };
        std::thread::sleep(std::time::Duration::from_millis(300));
        assert!(!path.exists(), "the other writer waits for the lock");
        let mut first = DocsToml::default();
        first
            .collections
            .push(Collection::new("a", dir.path().join("a")));
        first.save_to(&path).unwrap();
        drop(lock);
        let (left, ()) = other.join().unwrap();
        let names: Vec<_> = left.collections.iter().map(|c| c.name.clone()).collect();
        assert_eq!(names, ["a", "b"]);
        assert_eq!(DocsToml::load_from(&path).unwrap(), left);
    }

    #[test]
    fn a_save_writes_its_own_staging_file_mode_600() {
        // Another writer's staging file, left behind or half written, mode 644: not reused.
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("docs.toml");
        let stale = dir.path().join("docs.toml.new");
        std::fs::write(&stale, "half written").unwrap();
        std::fs::set_permissions(&stale, std::fs::Permissions::from_mode(0o644)).unwrap();
        let mut docs = DocsToml::default();
        docs.collections
            .push(Collection::new("notes", dir.path().join("notes")));
        docs.save_to(&path).unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o600);
        assert_eq!(DocsToml::load_from(&path).unwrap().collections.len(), 1);
        assert_eq!(std::fs::read_to_string(&stale).unwrap(), "half written");
        let left: Vec<_> = std::fs::read_dir(dir.path()).unwrap().flatten().collect();
        assert_eq!(left.len(), 2, "no staging file of its own is left");
    }

    #[test]
    fn a_secret_folder_kept_elsewhere_through_a_link_is_still_secret() {
        // `~/.ssh` and `~/.config` as links into a dotfiles folder: the folders they point to are
        // as secret as the links, and are neither a collection nor walked.
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        for dir in ["dotfiles/ssh", "dotfiles/config/app", "Documents"] {
            std::fs::create_dir_all(home.join(dir)).unwrap();
        }
        std::os::unix::fs::symlink(home.join("dotfiles/ssh"), home.join(".ssh")).unwrap();
        std::os::unix::fs::symlink(home.join("dotfiles/config"), home.join(".config")).unwrap();
        let codex = home.join(".mightling");
        let mut docs = DocsToml::default();
        assert!(validate_root_in(&home.join("dotfiles/ssh"), &home, &codex).is_err());
        assert!(docs
            .add(&home.join("dotfiles/config/app"), None, &home)
            .is_err());
        assert!(docs.add(&home.join(".ssh"), None, &home).is_err());
        let skipped = secret_dirs_in(&home.join("dotfiles"), &home, &codex);
        assert!(skipped.contains(&home.join("dotfiles/ssh")));
        assert!(skipped.contains(&home.join("dotfiles/config")));
        assert!(docs.add(&home.join("Documents"), None, &home).is_ok());
    }

    #[test]
    fn xdg_user_dirs_name_the_defaults() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::create_dir_all(home.join(".config")).unwrap();
        std::fs::create_dir_all(home.join("Dokumente")).unwrap();
        std::fs::write(
            home.join(".config/user-dirs.dirs"),
            "XDG_DOCUMENTS_DIR=\"$HOME/Dokumente\"\nXDG_DOWNLOAD_DIR=\"$HOME/\"\n",
        )
        .unwrap();
        let mut docs = DocsToml::default();
        assert_eq!(docs.ensure_defaults(&home), vec!["documents".to_string()]);
        assert_eq!(docs.get("documents").unwrap().root, home.join("Dokumente"));
    }

    #[test]
    fn a_default_folder_inside_another_collection_is_not_added_twice() {
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::create_dir_all(home.join(".config")).unwrap();
        std::fs::create_dir_all(home.join("Documents/Downloads")).unwrap();
        std::fs::write(
            home.join(".config/user-dirs.dirs"),
            "XDG_DOWNLOAD_DIR=\"$HOME/Documents/Downloads\"\n",
        )
        .unwrap();
        let mut docs = DocsToml::default();
        assert_eq!(docs.ensure_defaults(&home), vec!["documents".to_string()]);
        assert!(docs.get("downloads").is_none());
    }

    #[test]
    fn a_broken_file_is_an_error_not_an_empty_list() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("docs.toml");
        std::fs::write(&path, "[[collection]]\nname = ").unwrap();
        assert!(DocsToml::load_from(&path).is_err());
        assert_eq!(
            DocsToml::load_from(&dir.path().join("missing.toml")).unwrap(),
            DocsToml::default()
        );
    }

    #[test]
    fn collection_names_are_validated_and_enable_disable_round_trips() {
        for good in ["documents", "a", "work_2026-q4"] {
            assert!(validate_name(good).is_ok(), "{good}");
        }
        for bad in [
            "",
            "-bad",
            "_bad",
            "Bad",
            "a/b",
            "a..b",
            "pdf-0123456789ab",
            &"x".repeat(65),
        ] {
            assert!(validate_name(bad).is_err(), "{bad}");
        }
        let home = tempfile::tempdir().unwrap();
        let home = home.path().canonicalize().unwrap();
        std::fs::create_dir_all(home.join("papers/sub")).unwrap();
        let mut docs = DocsToml::default();
        docs.add(&home.join("papers"), Some("papers"), &home)
            .unwrap();
        assert!(
            docs.add(&home.join("papers/sub"), Some("sub"), &home)
                .is_err(),
            "overlapping collection root is refused"
        );
        docs.collections[0].enabled = false;
        assert_eq!(docs.enabled().count(), 0);
        docs.collections[0].enabled = true;
        assert_eq!(docs.enabled().count(), 1);
        assert!(docs.remove("unknown").is_err());
    }
}
