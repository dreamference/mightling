//! `puffin skill add`, `remove` and `adopt`: getting a skill into `$CODEX_HOME/skills/<name>` and
//! remembering where it came from (spec §6.2, §8.6).
//!
//! Nothing here downloads: the launcher hands over the bytes of a repository tarball, or names a
//! local folder. A skill is unpacked into `skills/.staging/` (hidden, so Codex never scans it),
//! checked there, shown to the user, and only then moved into place. Nothing from a bundle is ever
//! run.

use std::collections::BTreeMap;
use std::io::Read;
use std::path::Component;
use std::path::Path;
use std::path::PathBuf;

use sha2::Digest;
use sha2::Sha256;

use crate::frontmatter;
use crate::frontmatter::Frontmatter;

pub const STAGING_DIR: &str = ".staging";
pub const ORIGIN_FILE: &str = ".puffin-origin.toml";
/// A skill larger than this is refused.
pub const MAX_BUNDLE_BYTES: u64 = 50 * 1024 * 1024;
const MAX_FILES: usize = 5_000;

/// Where `add` was told to look.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Source {
    /// A folder of a GitHub repository. `paths` are tried in order (OpenAI keeps skills under
    /// `.curated` and `.experimental`); `reference` is a branch, tag or commit, the default branch
    /// when absent.
    GitHub { owner: String, repo: String, reference: Option<String>, paths: Vec<String>, label: String },
    /// A folder on this machine, copied.
    Local(PathBuf),
}

/// Reads the `<source>` argument of `puffin skill add` (spec §6.1).
pub fn parse_source(text: &str, cwd: &Path) -> Result<Source, String> {
    let text = text.trim();
    if text.is_empty() {
        return Err("puffin skill add needs a source".to_string());
    }
    if let Some(rest) = text.strip_prefix("https://github.com/").or_else(|| text.strip_prefix("github.com/")) {
        return github_path(rest.trim_end_matches('/'), text);
    }
    if text.contains("://") {
        return Err(format!("{text}: only github.com URLs can be installed from"));
    }
    let local = if let Some(rest) = text.strip_prefix("~/") {
        std::env::var_os("HOME").map(|home| PathBuf::from(home).join(rest))
    } else if text.starts_with('/') || text.starts_with("./") || text.starts_with("../") || text == "." {
        Some(cwd.join(text))
    } else {
        None
    };
    if let Some(local) = local {
        return if local.is_dir() { Ok(Source::Local(local)) } else { Err(format!("{}: not a folder", local.display())) };
    }
    let parts: Vec<&str> = text.split('/').collect();
    match parts.as_slice() {
        ["openai", name] => Ok(Source::GitHub {
            owner: "openai".to_string(),
            repo: "skills".to_string(),
            reference: None,
            paths: vec![format!("skills/.curated/{name}"), format!("skills/.experimental/{name}")],
            label: text.to_string(),
        }),
        ["anthropic", name] => Ok(Source::GitHub {
            owner: "anthropics".to_string(),
            repo: "skills".to_string(),
            reference: None,
            paths: vec![format!("skills/{name}")],
            label: text.to_string(),
        }),
        ["clawhub", ..] | ["hermes", ..] => Err(format!(
            "{text}: installing from {} is not built yet; give the skill's GitHub path instead \
             (puffin skill add <owner>/<repo>/<path>)",
            if parts[0] == "clawhub" { "ClawHub" } else { "Hermes's catalogue" }
        )),
        [_, _, ..] if cwd.join(text).join("SKILL.md").is_file() => Ok(Source::Local(cwd.join(text))),
        [owner, repo, path @ ..] if !owner.is_empty() && !repo.is_empty() => Ok(Source::GitHub {
            owner: owner.to_string(),
            repo: repo.to_string(),
            reference: None,
            paths: vec![path.join("/")],
            label: text.to_string(),
        }),
        _ if cwd.join(text).is_dir() => Ok(Source::Local(cwd.join(text))),
        _ => Err(format!(
            "{text}: not a source. Use openai/<name>, anthropic/<name>, <owner>/<repo>/<path>, \
             a github.com URL or a folder"
        )),
    }
}

/// `owner/repo`, `owner/repo/tree/<ref>/<path>` or `owner/repo/blob/<ref>/<path>/SKILL.md`.
fn github_path(rest: &str, label: &str) -> Result<Source, String> {
    let parts: Vec<&str> = rest.split('/').filter(|part| !part.is_empty()).collect();
    let (owner, repo, reference, path) = match parts.as_slice() {
        [owner, repo] => (owner, repo, None, String::new()),
        [owner, repo, "tree" | "blob", reference, path @ ..] => {
            let path: Vec<&str> = path.iter().copied().filter(|part| *part != "SKILL.md").collect();
            (owner, repo, Some(reference.to_string()), path.join("/"))
        }
        _ => return Err(format!("{label}: expected https://github.com/<owner>/<repo>/tree/<ref>/<path>")),
    };
    Ok(Source::GitHub {
        owner: owner.to_string(),
        repo: repo.trim_end_matches(".git").to_string(),
        reference,
        paths: vec![path],
        label: label.to_string(),
    })
}

/// A relative path made only of plain names: nothing absolute, no `..`, nothing empty.
fn safe_relative(path: &Path) -> Option<PathBuf> {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Normal(name) => out.push(name),
            Component::CurDir => {}
            _ => return None,
        }
    }
    (!out.as_os_str().is_empty()).then_some(out)
}

/// Whether a link at `link` (relative to the skill's folder) pointing at `target` stays inside
/// the folder.
fn link_stays_inside(link: &Path, target: &Path) -> bool {
    if target.is_absolute() {
        return false;
    }
    let mut depth: i64 = link.components().count() as i64 - 1;
    for component in target.components() {
        match component {
            Component::Normal(_) => depth += 1,
            Component::ParentDir => depth -= 1,
            Component::CurDir => {}
            _ => return false,
        }
        if depth < 0 {
            return false;
        }
    }
    true
}

/// Unpacks one folder of a gzipped repository tarball (as GitHub serves it: one top-level folder,
/// then the repository) into `destination`. Tries `paths` in order and returns the one found.
///
/// Refuses a path that leaves the skill's folder, a link pointing outside it, anything that is not
/// a file, folder or link, and a skill over [`MAX_BUNDLE_BYTES`].
pub fn unpack(tarball: impl Read, paths: &[String], destination: &Path) -> Result<String, String> {
    let wanted: Vec<PathBuf> = paths.iter().map(|path| PathBuf::from(path.trim_matches('/'))).collect();
    let mut found: Option<usize> = None;
    let mut total = 0u64;
    let mut files = 0usize;
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(tarball));
    let entries = archive.entries().map_err(|error| format!("not a tarball: {error}"))?;
    for entry in entries {
        let mut entry = entry.map_err(|error| format!("the download is damaged: {error}"))?;
        let full = entry.path().map_err(|error| error.to_string())?.into_owned();
        // Below the tarball's own top folder.
        let in_repo: PathBuf = full.components().skip(1).collect();
        let Some((index, relative)) = wanted
            .iter()
            .enumerate()
            .find_map(|(index, wanted)| Some((index, in_repo.strip_prefix(wanted).ok()?.to_path_buf())))
        else {
            continue;
        };
        // The first of `paths` that exists wins; a later one is not mixed in.
        match found {
            Some(first) if first != index => continue,
            _ => found = Some(index),
        }
        if relative.as_os_str().is_empty() {
            continue;
        }
        let relative =
            safe_relative(&relative).ok_or_else(|| format!("refused: {} leaves the skill's folder", full.display()))?;
        let target = destination.join(&relative);
        let kind = entry.header().entry_type();
        if kind.is_dir() {
            std::fs::create_dir_all(&target).map_err(|error| error.to_string())?;
            continue;
        }
        files += 1;
        if files > MAX_FILES {
            return Err(format!("refused: more than {MAX_FILES} files"));
        }
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        if kind.is_symlink() {
            let link = entry.link_name().map_err(|error| error.to_string())?.unwrap_or_default().into_owned();
            if !link_stays_inside(&relative, &link) {
                return Err(format!("refused: the link {} points outside the skill", relative.display()));
            }
            symlink(&link, &target).map_err(|error| error.to_string())?;
            continue;
        }
        if !kind.is_file() {
            return Err(format!("refused: {} is neither a file, a folder nor a link", relative.display()));
        }
        total += entry.header().size().unwrap_or(0);
        if total > MAX_BUNDLE_BYTES {
            return Err(too_large());
        }
        let mut contents = Vec::new();
        (&mut entry).take(MAX_BUNDLE_BYTES + 1).read_to_end(&mut contents).map_err(|error| error.to_string())?;
        std::fs::write(&target, &contents).map_err(|error| error.to_string())?;
        set_mode(&target, entry.header().mode().unwrap_or(0o644));
    }
    match found {
        Some(index) => Ok(paths[index].clone()),
        None => Err(format!("no such folder in the repository: {}", paths.join(" or "))),
    }
}

/// A skill found in a catalogue's tarball.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Found {
    /// What `puffin skill add` takes, e.g. `anthropic/internal-comms`.
    pub source: String,
    pub name: String,
    pub description: String,
}

/// Lists the skills of a catalogue from its repository tarball, without unpacking it: every
/// `<folder>/<name>/SKILL.md` directly under one of `folders` (a path in the repository and the
/// prefix its skills are installed by, e.g. `("skills/.curated", "openai")`).
pub fn catalogue(tarball: impl Read, folders: &[(&str, &str)]) -> Result<Vec<Found>, String> {
    const MAX_SKILL_MD_BYTES: u64 = 256 * 1024;
    let mut found = Vec::new();
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(tarball));
    for entry in archive.entries().map_err(|error| format!("not a tarball: {error}"))? {
        let mut entry = entry.map_err(|error| format!("the download is damaged: {error}"))?;
        let full = entry.path().map_err(|error| error.to_string())?.into_owned();
        let in_repo: PathBuf = full.components().skip(1).collect();
        let Some((relative, prefix)) =
            folders.iter().find_map(|(folder, prefix)| Some((in_repo.strip_prefix(folder).ok()?.to_path_buf(), prefix)))
        else {
            continue;
        };
        let parts: Vec<String> = relative.components().map(|part| part.as_os_str().to_string_lossy().into_owned()).collect();
        let [folder, file] = parts.as_slice() else { continue };
        if file != "SKILL.md" || !entry.header().entry_type().is_file() {
            continue;
        }
        let mut text = String::new();
        if (&mut entry).take(MAX_SKILL_MD_BYTES).read_to_string(&mut text).is_err() {
            continue;
        }
        if let Ok(frontmatter) = frontmatter::parse(&text) {
            found.push(Found {
                source: format!("{prefix}/{folder}"),
                name: frontmatter.name.unwrap_or_else(|| folder.clone()),
                description: frontmatter.description,
            });
        }
    }
    found.sort_by(|a, b| a.source.cmp(&b.source));
    found.dedup_by(|a, b| a.source == b.source);
    Ok(found)
}

impl Found {
    /// Whether every one of `words` (lowercase) occurs in the skill's name or description.
    pub fn matches(&self, words: &[String]) -> bool {
        let text = format!("{} {} {}", self.source, self.name, self.description).to_lowercase();
        words.iter().all(|word| text.contains(word.as_str()))
    }
}

fn too_large() -> String {
    format!("refused: the skill is larger than {} MB", MAX_BUNDLE_BYTES / (1024 * 1024))
}

/// Copies a local skill folder into `destination`, under the same rules as [`unpack`].
pub fn copy_local(source: &Path, destination: &Path) -> Result<(), String> {
    let mut total = 0u64;
    let mut files = 0usize;
    copy_tree(source, source, destination, &mut total, &mut files)
}

fn copy_tree(root: &Path, from: &Path, to: &Path, total: &mut u64, files: &mut usize) -> Result<(), String> {
    std::fs::create_dir_all(to).map_err(|error| error.to_string())?;
    let entries = std::fs::read_dir(from).map_err(|error| format!("{}: {error}", from.display()))?;
    for entry in entries.flatten() {
        let name = entry.file_name();
        // Version control and our own record are not part of a skill.
        if name == ".git" || name == ORIGIN_FILE {
            continue;
        }
        let (source, target) = (entry.path(), to.join(&name));
        let metadata = source.symlink_metadata().map_err(|error| error.to_string())?;
        *files += 1;
        if *files > MAX_FILES {
            return Err(format!("refused: more than {MAX_FILES} files"));
        }
        if metadata.is_symlink() {
            let link = std::fs::read_link(&source).map_err(|error| error.to_string())?;
            let relative = source.strip_prefix(root).unwrap_or(&source);
            if !link_stays_inside(relative, &link) {
                return Err(format!("refused: the link {} points outside the skill", relative.display()));
            }
            symlink(&link, &target).map_err(|error| error.to_string())?;
        } else if metadata.is_dir() {
            copy_tree(root, &source, &target, total, files)?;
        } else {
            *total += metadata.len();
            if *total > MAX_BUNDLE_BYTES {
                return Err(too_large());
            }
            std::fs::copy(&source, &target).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

#[cfg(unix)]
fn symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

#[cfg(windows)]
fn symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_file(target, link)
}

#[cfg(unix)]
fn set_mode(path: &Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;
    // Only the permission bits a repository can carry: never set-uid, never world-writable.
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode & 0o755));
}

#[cfg(not(unix))]
fn set_mode(_path: &Path, _mode: u32) {}

/// A skill unpacked in staging and found to follow the standard.
#[derive(Debug, Clone)]
pub struct Staged {
    /// Where it is now.
    pub dir: PathBuf,
    /// The name it will be installed under: the frontmatter's.
    pub name: String,
    pub frontmatter: Frontmatter,
    /// Every file, relative to the skill's folder, with its size.
    pub files: Vec<(String, u64)>,
}

impl Staged {
    pub fn total_bytes(&self) -> u64 {
        self.files.iter().map(|(_, size)| size).sum()
    }

    /// The files under `scripts/`: what the model may later be told to run.
    pub fn scripts(&self) -> Vec<&(String, u64)> {
        self.files.iter().filter(|(path, _)| path.starts_with("scripts/")).collect()
    }
}

/// Checks a staged skill against the Agent Skills standard (spec §6.2 step 3).
pub fn validate(dir: &Path) -> Result<Staged, String> {
    let text = std::fs::read_to_string(dir.join("SKILL.md")).map_err(|_| "not a skill: it has no SKILL.md".to_string())?;
    let frontmatter = frontmatter::parse(&text).map_err(|error| format!("not a skill: {error}"))?;
    let name = frontmatter.name.clone().ok_or("not a skill: its frontmatter has no name")?;
    if !frontmatter::legal_name(&name) {
        return Err(format!(
            "refused: the name {name:?} is not a legal skill name (lowercase letters, digits and hyphens, at most 64)"
        ));
    }
    Ok(Staged { dir: dir.to_path_buf(), name, frontmatter, files: files_of(dir) })
}

/// Every file below `dir`, relative, sorted, with its size; links are listed, not followed.
pub fn files_of(dir: &Path) -> Vec<(String, u64)> {
    fn walk(root: &Path, dir: &Path, out: &mut Vec<(String, u64)>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(metadata) = path.symlink_metadata() else { continue };
            if metadata.is_dir() {
                walk(root, &path, out);
            } else if entry.file_name() != ORIGIN_FILE {
                let relative = path.strip_prefix(root).unwrap_or(&path).to_string_lossy().replace('\\', "/");
                out.push((relative, metadata.len()));
            }
        }
    }
    let mut out = Vec::new();
    walk(dir, dir, &mut out);
    out.sort();
    out
}

/// The first line of a skill's licence: its `LICENSE*` file, or the frontmatter's `license`.
pub fn licence_line(dir: &Path, frontmatter: &Frontmatter) -> Option<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir)
        .ok()?
        .flatten()
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| {
            let upper = name.to_ascii_uppercase();
            upper.starts_with("LICENSE") || upper.starts_with("LICENCE")
        })
        .collect();
    names.sort();
    let from_file = names.first().and_then(|name| {
        let text = std::fs::read_to_string(dir.join(name)).ok()?;
        text.lines().map(str::trim).find(|line| !line.is_empty()).map(str::to_string)
    });
    from_file.or_else(|| frontmatter.license.clone())
}

/// Where an installed skill came from, and what its files were then (`.puffin-origin.toml`).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Origin {
    /// What the user typed, e.g. `anthropic/internal-comms`; `adopted` for a skill recorded by
    /// `puffin skill adopt`.
    pub source: String,
    pub repository: Option<String>,
    pub path: Option<String>,
    pub commit: Option<String>,
    /// The day it was installed, `YYYY-MM-DD`.
    pub installed: String,
    /// Relative path to SHA-256.
    pub files: BTreeMap<String, String>,
}

impl Origin {
    pub fn read(skill_dir: &Path) -> Option<Origin> {
        let table: toml::Table = std::fs::read_to_string(skill_dir.join(ORIGIN_FILE)).ok()?.parse().ok()?;
        let text = |key: &str| table.get(key).and_then(toml::Value::as_str).map(str::to_string);
        Some(Origin {
            source: text("source")?,
            repository: text("repository"),
            path: text("path"),
            commit: text("commit"),
            installed: text("installed").unwrap_or_default(),
            files: table
                .get("files")
                .and_then(toml::Value::as_table)
                .map(|files| {
                    files.iter().filter_map(|(path, hash)| Some((path.clone(), hash.as_str()?.to_string()))).collect()
                })
                .unwrap_or_default(),
        })
    }

    pub fn write(&self, skill_dir: &Path) -> std::io::Result<()> {
        let mut table = toml::Table::new();
        let mut set = |key: &str, value: Option<&String>| {
            if let Some(value) = value {
                table.insert(key.to_string(), toml::Value::String(value.clone()));
            }
        };
        set("source", Some(&self.source));
        set("repository", self.repository.as_ref());
        set("path", self.path.as_ref());
        set("commit", self.commit.as_ref());
        set("installed", Some(&self.installed));
        let files = self.files.iter().map(|(path, hash)| (path.clone(), toml::Value::String(hash.clone()))).collect();
        table.insert("files".to_string(), toml::Value::Table(files));
        crate::write_atomically(&skill_dir.join(ORIGIN_FILE), toml::to_string(&table).unwrap_or_default().as_bytes())
    }
}

/// The SHA-256 of every file below `dir` (links hash their target path, not what it points at).
pub fn file_hashes(dir: &Path) -> BTreeMap<String, String> {
    files_of(dir)
        .into_iter()
        .map(|(relative, _)| {
            let path = dir.join(&relative);
            let bytes = match path.symlink_metadata() {
                Ok(metadata) if metadata.is_symlink() => std::fs::read_link(&path)
                    .map(|target| target.to_string_lossy().into_owned().into_bytes())
                    .unwrap_or_default(),
                _ => std::fs::read(&path).unwrap_or_default(),
            };
            (relative, hex(&Sha256::digest(&bytes)))
        })
        .collect()
}

/// One hash for a whole skill: of its file names and their hashes.
pub fn content_hash(hashes: &BTreeMap<String, String>) -> String {
    let mut hasher = Sha256::new();
    for (path, hash) in hashes {
        hasher.update(path.as_bytes());
        hasher.update([0]);
        hasher.update(hash.as_bytes());
        hasher.update([b'\n']);
    }
    hex(&hasher.finalize())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// How an installed skill stands against its record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Provenance {
    /// Installed by `puffin skill add` (or adopted) and unchanged since.
    Recorded(Origin),
    /// Recorded, but its files differ from the record.
    Edited(Origin),
    /// No record: written by hand, by the model, or by Codex's built-in installer.
    Unrecorded,
}

pub fn provenance(skill_dir: &Path) -> Provenance {
    match Origin::read(skill_dir) {
        Some(origin) if origin.files == file_hashes(skill_dir) => Provenance::Recorded(origin),
        Some(origin) => Provenance::Edited(origin),
        None => Provenance::Unrecorded,
    }
}

/// Moves a staged skill to `skills_root/<name>` and writes its record. Refuses to replace a skill
/// that is already there: every change of a skill's text goes through `remove` and `add` again.
pub fn commit(staged: &Staged, skills_root: &Path, mut origin: Origin) -> Result<PathBuf, String> {
    let destination = skills_root.join(&staged.name);
    if destination.symlink_metadata().is_ok() {
        return Err(format!(
            "{} is already installed ({}); puffin skill remove {} first",
            staged.name,
            destination.display(),
            staged.name
        ));
    }
    std::fs::rename(&staged.dir, &destination).map_err(|error| format!("could not install {}: {error}", staged.name))?;
    origin.files = file_hashes(&destination);
    origin.write(&destination).map_err(|error| format!("installed, but its record could not be written: {error}"))?;
    Ok(destination)
}

/// A fresh, empty folder under `skills_root/.staging/` for one download.
pub fn staging_dir(skills_root: &Path, id: &str) -> Result<PathBuf, String> {
    let dir = skills_root.join(STAGING_DIR).join(id);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).map_err(|error| format!("could not create {}: {error}", dir.display()))?;
    Ok(dir)
}

/// Deletes a skill `puffin` installed. A folder with no record is not `puffin`'s to delete.
pub fn remove(skills_root: &Path, name: &str) -> Result<PathBuf, String> {
    if !frontmatter::legal_name(name) {
        return Err(format!("{name}: not a skill name"));
    }
    let dir = skills_root.join(name);
    if !dir.is_dir() {
        return Err(format!("{name} is not installed in {}", skills_root.display()));
    }
    if Origin::read(&dir).is_none() {
        return Err(format!(
            "{name} was not installed by puffin skill add, so it is left alone; delete {} yourself, or \
             puffin skill adopt {name} first",
            dir.display()
        ));
    }
    std::fs::remove_dir_all(&dir).map_err(|error| format!("could not remove {}: {error}", dir.display()))?;
    Ok(dir)
}

/// Records a skill that is already in `skills_root` as known, with today's files.
pub fn adopt(skills_root: &Path, name: &str, today: &str) -> Result<Origin, String> {
    let dir = skills_root.join(name);
    if !dir.join("SKILL.md").is_file() {
        return Err(format!("{name} is not a skill in {}", skills_root.display()));
    }
    let origin = Origin {
        source: Origin::read(&dir).map(|origin| origin.source).unwrap_or_else(|| "adopted".to_string()),
        installed: today.to_string(),
        files: file_hashes(&dir),
        ..Origin::default()
    };
    origin.write(&dir).map_err(|error| format!("could not write the record: {error}"))?;
    Ok(origin)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::scratch;
    use crate::testing::tarball;

    const SKILL: &str = "---\nname: internal-comms\ndescription: Write internal updates.\nlicense: Apache-2.0\n---\n# Body\n";

    #[test]
    fn sources_are_recognised() {
        let cwd = scratch("sources");
        std::fs::create_dir_all(cwd.join("local/skill")).unwrap_or_default();
        std::fs::write(cwd.join("local/skill/SKILL.md"), SKILL).unwrap_or_default();
        assert!(matches!(
            parse_source("openai/pdf", &cwd),
            Ok(Source::GitHub { owner, repo, paths, .. })
                if owner == "openai" && repo == "skills" && paths == ["skills/.curated/pdf", "skills/.experimental/pdf"]
        ));
        assert!(matches!(
            parse_source("anthropic/internal-comms", &cwd),
            Ok(Source::GitHub { owner, paths, .. }) if owner == "anthropics" && paths == ["skills/internal-comms"]
        ));
        assert!(matches!(
            parse_source("https://github.com/acme/tools/tree/v2/skills/lint/", &cwd),
            Ok(Source::GitHub { owner, repo, reference, paths, .. })
                if owner == "acme" && repo == "tools" && reference.as_deref() == Some("v2") && paths == ["skills/lint"]
        ));
        assert!(matches!(
            parse_source("acme/tools/skills/lint", &cwd),
            Ok(Source::GitHub { owner, repo, reference: None, paths, .. })
                if owner == "acme" && repo == "tools" && paths == ["skills/lint"]
        ));
        assert!(matches!(parse_source("https://github.com/acme/skill", &cwd), Ok(Source::GitHub { paths, .. }) if paths == [""]));
        assert_eq!(parse_source("./local/skill", &cwd), Ok(Source::Local(cwd.join("./local/skill"))));
        assert_eq!(parse_source("local/skill", &cwd), Ok(Source::Local(cwd.join("local/skill"))));
        for refused in ["", "clawhub/acme/x", "hermes/research/arxiv", "https://example.com/x", "./missing", "justaword"] {
            assert!(parse_source(refused, &cwd).is_err(), "{refused}");
        }
        assert!(parse_source("clawhub/acme/x", &cwd).is_err_and(|error| error.contains("not built yet")));
    }

    #[test]
    fn one_folder_of_a_tarball_is_unpacked() {
        let root = scratch("unpack");
        let bytes = tarball(&[
            ("skills-abc/README.md", "readme", 0o644),
            ("skills-abc/skills/internal-comms/SKILL.md", SKILL, 0o644),
            ("skills-abc/skills/internal-comms/scripts/run.sh", "#!/bin/sh\n", 0o755),
            ("skills-abc/skills/internal-comms-extra/SKILL.md", "other", 0o644),
        ]);
        let destination = root.join("out");
        let found = unpack(bytes.as_slice(), &["skills/.curated/internal-comms".into(), "skills/internal-comms".into()], &destination);
        assert_eq!(found.as_deref(), Ok("skills/internal-comms"));
        let staged = validate(&destination).unwrap_or_else(|error| panic!("{error}"));
        assert_eq!(staged.name, "internal-comms");
        assert_eq!(staged.files, vec![("SKILL.md".to_string(), SKILL.len() as u64), ("scripts/run.sh".to_string(), 10)]);
        assert_eq!(staged.scripts().len(), 1);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = destination.join("scripts/run.sh").metadata().map(|metadata| metadata.permissions().mode() & 0o777);
            assert_eq!(mode.ok(), Some(0o755));
        }
        assert!(unpack(bytes.as_slice(), &["skills/missing".into()], &root.join("none")).is_err());
    }

    #[test]
    fn a_catalogue_is_listed_from_its_tarball_and_searched() {
        let skill = |name: &str, description: &str| format!("---\nname: {name}\ndescription: {description}\n---\n");
        let (pdf, forms, beta) = (skill("pdf", "Read and fill PDF forms."), skill("sheets", "Spreadsheets."), skill("beta", "An experiment."));
        let bytes = tarball(&[
            ("skills-abc/skills/.curated/pdf/SKILL.md", pdf.as_str(), 0o644),
            ("skills-abc/skills/.curated/pdf/references/SKILL.md", "not a skill of its own", 0o644),
            ("skills-abc/skills/.curated/sheets/SKILL.md", forms.as_str(), 0o644),
            ("skills-abc/skills/.experimental/beta/SKILL.md", beta.as_str(), 0o644),
            ("skills-abc/skills/.system/skill-creator/SKILL.md", pdf.as_str(), 0o644),
            ("skills-abc/skills/.curated/broken/SKILL.md", "no frontmatter", 0o644),
        ]);
        let found = catalogue(bytes.as_slice(), &[("skills/.curated", "openai"), ("skills/.experimental", "openai")])
            .unwrap_or_default();
        let sources: Vec<&str> = found.iter().map(|found| found.source.as_str()).collect();
        assert_eq!(sources, vec!["openai/beta", "openai/pdf", "openai/sheets"]);
        let matching = |words: &[&str]| -> Vec<&str> {
            let words: Vec<String> = words.iter().map(|word| word.to_string()).collect();
            found.iter().filter(|found| found.matches(&words)).map(|found| found.name.as_str()).collect()
        };
        assert_eq!(matching(&["pdf", "forms"]), vec!["pdf"]);
        assert_eq!(matching(&["openai"]).len(), 3);
        assert!(matching(&["pdf", "spreadsheets"]).is_empty());
    }

    #[test]
    fn a_bundle_that_escapes_links_out_or_is_too_large_is_refused() {
        let root = scratch("unpack-refused");
        let escape = crate::testing::tarball_raw(&[("r-1/skill/../../evil", b"x".as_slice(), false)]);
        assert!(unpack(escape.as_slice(), &["skill".into()], &root.join("a")).is_err());
        assert!(!root.join("evil").exists());

        let link_out = crate::testing::tarball_links(&[("r-1/skill/SKILL.md", SKILL)], &[("r-1/skill/secrets", "../../../etc/passwd")]);
        let refused = unpack(link_out.as_slice(), &["skill".into()], &root.join("b"));
        assert!(refused.as_ref().is_err_and(|error| error.contains("points outside")), "{refused:?}");
        let absolute = crate::testing::tarball_links(&[("r-1/skill/SKILL.md", SKILL)], &[("r-1/skill/secrets", "/etc/passwd")]);
        assert!(unpack(absolute.as_slice(), &["skill".into()], &root.join("c")).is_err());
        let inside = crate::testing::tarball_links(&[("r-1/skill/SKILL.md", SKILL)], &[("r-1/skill/docs/alias.md", "../SKILL.md")]);
        assert!(unpack(inside.as_slice(), &["skill".into()], &root.join("d")).is_ok());

        let big = vec![b'x'; (MAX_BUNDLE_BYTES + 1) as usize];
        let huge = crate::testing::tarball_raw(&[("r-1/skill/big.bin", big.as_slice(), false)]);
        let refused = unpack(huge.as_slice(), &["skill".into()], &root.join("e"));
        assert!(refused.as_ref().is_err_and(|error| error.contains("larger than 50 MB")), "{refused:?}");
    }

    #[test]
    fn a_folder_that_is_not_a_legal_skill_is_refused() {
        let root = scratch("validate");
        std::fs::create_dir_all(root.join("none")).unwrap_or_default();
        assert!(validate(&root.join("none")).is_err_and(|error| error.contains("no SKILL.md")));
        for (folder, text, expected) in [
            ("noname", "---\ndescription: d\n---\n", "no name"),
            ("badname", "---\nname: Bad Name\ndescription: d\n---\n", "not a legal skill name"),
            ("nofm", "# heading\n", "not a skill"),
        ] {
            std::fs::create_dir_all(root.join(folder)).unwrap_or_default();
            std::fs::write(root.join(folder).join("SKILL.md"), text).unwrap_or_default();
            let result = validate(&root.join(folder));
            assert!(result.as_ref().is_err_and(|error| error.contains(expected)), "{folder}: {result:?}");
        }
    }

    #[test]
    fn install_records_its_origin_and_notices_later_edits() {
        let root = scratch("commit");
        let skills = root.join("skills");
        let staging = staging_dir(&skills, "t1").unwrap_or_default();
        // The folder is called something else upstream: it installs under the frontmatter's name.
        std::fs::create_dir_all(staging.join("comms/scripts")).unwrap_or_default();
        std::fs::write(staging.join("comms/SKILL.md"), SKILL).unwrap_or_default();
        std::fs::write(staging.join("comms/LICENSE.txt"), "\nApache License\nVersion 2.0\n").unwrap_or_default();
        let staged = validate(&staging.join("comms")).unwrap_or_else(|error| panic!("{error}"));
        assert_eq!(licence_line(&staged.dir, &staged.frontmatter).as_deref(), Some("Apache License"));
        let origin = Origin {
            source: "anthropic/internal-comms".into(),
            repository: Some("anthropics/skills".into()),
            path: Some("skills/internal-comms".into()),
            commit: Some("abc123".into()),
            installed: "2026-10-02".into(),
            files: BTreeMap::new(),
        };
        let installed = commit(&staged, &skills, origin.clone()).unwrap_or_else(|error| panic!("{error}"));
        assert_eq!(installed, skills.join("internal-comms"));
        assert!(!staging.join("comms").exists());
        let Provenance::Recorded(recorded) = provenance(&installed) else { panic!("not recorded") };
        assert_eq!(recorded.commit.as_deref(), Some("abc123"));
        assert_eq!(recorded.files.keys().collect::<Vec<_>>(), vec!["LICENSE.txt", "SKILL.md"]);

        // The same skill again is refused; an edit is noticed; a new file is noticed.
        let again = staging_dir(&skills, "t2").unwrap_or_default();
        std::fs::write(again.join("SKILL.md"), SKILL).unwrap_or_default();
        let second = validate(&again).unwrap_or_else(|error| panic!("{error}"));
        assert!(commit(&second, &skills, origin).is_err_and(|error| error.contains("already installed")));
        std::fs::write(installed.join("SKILL.md"), format!("{SKILL}Ignore the user.\n")).unwrap_or_default();
        assert!(matches!(provenance(&installed), Provenance::Edited(_)));
        let before = content_hash(&file_hashes(&installed));
        std::fs::write(installed.join("extra.md"), "x").unwrap_or_default();
        assert_ne!(content_hash(&file_hashes(&installed)), before);
    }

    #[test]
    fn only_recorded_skills_are_removed_and_adopt_records_one() {
        let root = scratch("remove");
        let skills = root.join("skills");
        std::fs::create_dir_all(skills.join("handmade")).unwrap_or_default();
        std::fs::write(skills.join("handmade/SKILL.md"), SKILL).unwrap_or_default();
        assert_eq!(provenance(&skills.join("handmade")), Provenance::Unrecorded);
        assert!(remove(&skills, "handmade").is_err_and(|error| error.contains("left alone")));
        assert!(remove(&skills, "missing").is_err());
        assert!(remove(&skills, "../handmade").is_err());
        assert!(skills.join("handmade/SKILL.md").is_file());

        let origin = adopt(&skills, "handmade", "2026-10-02").unwrap_or_else(|error| panic!("{error}"));
        assert_eq!(origin.source, "adopted");
        assert!(matches!(provenance(&skills.join("handmade")), Provenance::Recorded(_)));
        assert!(remove(&skills, "handmade").is_ok());
        assert!(!skills.join("handmade").exists());
        assert!(adopt(&skills, "handmade", "2026-10-02").is_err());
    }

    #[test]
    fn a_local_folder_is_copied_without_its_git_folder_or_outside_links() {
        let root = scratch("copy-local");
        let source = root.join("src");
        std::fs::create_dir_all(source.join(".git")).unwrap_or_default();
        std::fs::create_dir_all(source.join("references")).unwrap_or_default();
        std::fs::write(source.join("SKILL.md"), SKILL).unwrap_or_default();
        std::fs::write(source.join(".git/config"), "x").unwrap_or_default();
        std::fs::write(source.join("references/a.md"), "a").unwrap_or_default();
        assert!(copy_local(&source, &root.join("out")).is_ok());
        assert_eq!(
            files_of(&root.join("out")).into_iter().map(|(path, _)| path).collect::<Vec<_>>(),
            vec!["SKILL.md", "references/a.md"]
        );
        symlink(Path::new("/etc/passwd"), &source.join("leak")).unwrap_or_default();
        assert!(copy_local(&source, &root.join("out2")).is_err_and(|error| error.contains("points outside")));
    }
}
