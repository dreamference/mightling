//! The `from-<agent>` folders under `$CODEX_HOME/skills` (spec §3).
//!
//! A skill another agent installed stays in that agent's folder; `puffin` sees it through one
//! symbolic link per skill, `skills/from-claude/<name> -> ~/.claude/skills/<name>`. On Windows the
//! link is a directory junction, which needs neither Developer Mode nor administrator rights. The folders are
//! the launcher's alone. They are rebuilt at every start, a folder that is already right is left
//! untouched, and whatever else turns up under a `from-` name is moved to `skills/.quarantine/`:
//! the agent can write `$CODEX_HOME/skills`, so a steered session could otherwise plant a folder of
//! its own there and have it kept.

use std::collections::BTreeMap;
use std::fs::File;
use std::io;
use std::path::Path;
use std::path::PathBuf;

/// The prefix every launcher-owned folder carries. Codex skips hidden directories below a root,
/// which is why these are not `.claude` and why `.quarantine` and `.staging` are never scanned.
pub const LINK_PREFIX: &str = "from-";
pub const QUARANTINE_DIR: &str = ".quarantine";
const LOCK_FILE: &str = ".puffin-links.lock";

/// The links one agent's folder should hold: skill name to the directory of its `SKILL.md`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LinkSet {
    pub agent: String,
    /// Where the launcher's links in this folder point. A link pointing anywhere else is not the
    /// launcher's.
    pub source: Source,
    pub links: BTreeMap<String, PathBuf>,
}

/// Where the links of one `from-` folder may point.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Source {
    /// The agent's own skills folder: `~/.claude/skills`.
    Folder(PathBuf),
    /// That folder in any repository (`.claude/skills`, relative): the `from-repo-*` folders point
    /// into whichever repository `puffin` last started in, so a link left from the previous one is
    /// still the launcher's, removed rather than quarantined.
    AnyRepository(&'static str),
}

impl Source {
    /// Whether a link to `target` is one the launcher could have written. A target with `..` in it
    /// starts with the source and ends anywhere, so it never is.
    pub fn holds(&self, target: &Path) -> bool {
        if target.components().any(|part| matches!(part, std::path::Component::ParentDir)) {
            return false;
        }
        match self {
            Source::Folder(folder) => target.starts_with(folder),
            Source::AnyRepository(folder) => {
                let wanted: Vec<_> = Path::new(folder).components().collect();
                let parts: Vec<_> = target.components().collect();
                target.is_absolute()
                    && !wanted.is_empty()
                    && parts.windows(wanted.len()).any(|window| window == wanted.as_slice())
            }
        }
    }
}

/// What a rebuild did.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Rebuilt {
    /// Folders written or removed (`from-claude`, …). Empty when everything was already right.
    pub changed: Vec<String>,
    /// Where each thing that was not the launcher's now is.
    pub quarantined: Vec<PathBuf>,
}

/// Brings every `from-*` folder under `skills_root` to `wanted`.
///
/// `stamp` names this run's quarantine folder. The whole pass holds one lock, because Night Shift
/// starts several `puffin exec` at once and one launcher must not rebuild a folder under another.
pub fn rebuild(skills_root: &Path, wanted: &[LinkSet], stamp: &str) -> io::Result<Rebuilt> {
    std::fs::create_dir_all(skills_root)?;
    let lock = File::options()
        .create(true)
        .truncate(false)
        .write(true)
        .open(skills_root.join(LOCK_FILE))?;
    lock.lock()?;
    let mut rebuilt = Rebuilt::default();
    let quarantine = skills_root.join(QUARANTINE_DIR).join(stamp);

    let mut names: Vec<String> = std::fs::read_dir(skills_root)?
        .flatten()
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| name.starts_with(LINK_PREFIX))
        .collect();
    for set in wanted.iter().filter(|set| !set.links.is_empty()) {
        names.push(format!("{LINK_PREFIX}{}", set.agent));
    }
    names.sort();
    names.dedup();

    for name in names {
        let folder = skills_root.join(&name);
        let set = wanted
            .iter()
            .find(|set| format!("{LINK_PREFIX}{}", set.agent) == name && !set.links.is_empty());
        if let Some(set) = set
            && holds_exactly(&folder, set)
        {
            continue;
        }
        // Take out whatever is not a link into the agent's own folder before anything is replaced.
        let source = wanted
            .iter()
            .find(|set| format!("{LINK_PREFIX}{}", set.agent) == name)
            .map(|set| &set.source);
        set_aside_foreign(&folder, source, &quarantine.join(&name), &mut rebuilt.quarantined)?;
        match set {
            Some(set) => replace_folder(skills_root, &name, set)?,
            None => remove_folder(&folder)?,
        }
        rebuilt.changed.push(name);
    }
    drop(lock);
    Ok(rebuilt)
}

/// Whether `folder` is a real directory holding exactly `set`'s links and nothing else.
fn holds_exactly(folder: &Path, set: &LinkSet) -> bool {
    if !folder.symlink_metadata().is_ok_and(|metadata| metadata.is_dir()) {
        return false;
    }
    let Ok(entries) = std::fs::read_dir(folder) else { return false };
    let mut found = BTreeMap::new();
    for entry in entries.flatten() {
        let Ok(target) = read_link(&entry.path()) else { return false };
        found.insert(entry.file_name().to_string_lossy().into_owned(), target);
    }
    found == set.links
}

/// Moves to `quarantine` everything at `folder` that the launcher would not have put there: the
/// folder itself when it is a file or a link, and inside it any plain file or folder and any link
/// that does not point into `source`.
fn set_aside_foreign(
    folder: &Path,
    source: Option<&Source>,
    quarantine: &Path,
    moved: &mut Vec<PathBuf>,
) -> io::Result<()> {
    let Ok(metadata) = folder.symlink_metadata() else { return Ok(()) };
    if !metadata.is_dir() {
        return set_aside(folder, quarantine, moved);
    }
    for entry in std::fs::read_dir(folder)?.flatten() {
        let ours = read_link(&entry.path()).is_ok_and(|target| source.is_some_and(|source| source.holds(&target)));
        if !ours {
            set_aside(&entry.path(), &quarantine.join(entry.file_name()), moved)?;
        }
    }
    Ok(())
}

fn set_aside(path: &Path, destination: &Path, moved: &mut Vec<PathBuf>) -> io::Result<()> {
    if let Some(parent) = destination.parent() {
        std::fs::create_dir_all(parent)?;
    }
    // A second thing of the same name in one run keeps both.
    let mut destination = destination.to_path_buf();
    let mut copy = 1;
    while destination.symlink_metadata().is_ok() {
        copy += 1;
        destination.set_extension(copy.to_string());
    }
    std::fs::rename(path, &destination)?;
    moved.push(destination);
    Ok(())
}

/// Removes what is left of a launcher folder: by now only links into the agent's own folder.
/// `remove_dir_all` does not follow links, so no target is touched.
fn remove_folder(folder: &Path) -> io::Result<()> {
    match folder.symlink_metadata() {
        Ok(metadata) if metadata.is_dir() => std::fs::remove_dir_all(folder),
        Ok(_) => std::fs::remove_file(folder),
        Err(_) => Ok(()),
    }
}

/// Writes `set`'s links in a hidden sibling and swaps it into place, so another session's Codex
/// scanning `skills/` sees the old folder or the new one, never an empty one.
fn replace_folder(skills_root: &Path, name: &str, set: &LinkSet) -> io::Result<()> {
    let staging = skills_root.join(format!(".{name}.{}.tmp", std::process::id()));
    remove_folder(&staging)?;
    std::fs::create_dir(&staging)?;
    for (link, target) in &set.links {
        symlink_dir(target, &staging.join(link))?;
    }
    let folder = skills_root.join(name);
    if folder.symlink_metadata().is_ok() {
        swap(&staging, &folder)?;
        remove_folder(&staging)
    } else {
        std::fs::rename(&staging, &folder)
    }
}

#[cfg(unix)]
fn symlink_dir(target: &Path, link: &Path) -> io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

/// A directory junction: unlike a symbolic link it needs no privilege, so it works on a standard
/// Windows 11 laptop with Developer Mode off (specs/DREAMFERENCE_PUFFIN_WINDOWS_ARM.md §15).
#[cfg(windows)]
fn symlink_dir(target: &Path, link: &Path) -> io::Result<()> {
    junction::create(target, link)
}

/// Where a link points, in the plain form the wanted links are written in.
fn read_link(path: &Path) -> io::Result<PathBuf> {
    std::fs::read_link(path).map(|target| plain_path(&target))
}

/// `target` without Windows' verbatim prefix: a junction reads back as `\\?\C:\…` (or the NT
/// form `\??\C:\…`), and the links it is compared with are written as `C:\…`. Anything else is
/// returned as it is.
pub fn plain_path(target: &Path) -> PathBuf {
    let text = target.to_string_lossy();
    for prefix in [r"\\?\UNC\", r"\??\UNC\"] {
        if let Some(rest) = text.strip_prefix(prefix) {
            return PathBuf::from(format!(r"\\{rest}"));
        }
    }
    for prefix in [r"\\?\", r"\??\"] {
        if let Some(rest) = text.strip_prefix(prefix) {
            return PathBuf::from(rest);
        }
    }
    target.to_path_buf()
}

/// Exchanges two directories in one step where the system can (`renameat2(RENAME_EXCHANGE)`).
#[cfg(target_os = "linux")]
fn swap(new: &Path, old: &Path) -> io::Result<()> {
    use std::os::unix::ffi::OsStrExt;
    let path = |path: &Path| std::ffi::CString::new(path.as_os_str().as_bytes()).map_err(io::Error::other);
    let (new_c, old_c) = (path(new)?, path(old)?);
    // SAFETY: both arguments are NUL-terminated paths that outlive the call.
    let result = unsafe {
        libc::renameat2(libc::AT_FDCWD, new_c.as_ptr(), libc::AT_FDCWD, old_c.as_ptr(), libc::RENAME_EXCHANGE)
    };
    if result == 0 {
        return Ok(());
    }
    // A filesystem without the exchange (some network mounts): two renames, with a moment between
    // them in which the folder is missing.
    two_step_swap(new, old)
}

#[cfg(not(target_os = "linux"))]
fn swap(new: &Path, old: &Path) -> io::Result<()> {
    two_step_swap(new, old)
}

/// After it, `old`'s name holds the new folder and `new`'s name holds the old one, as the exchange
/// leaves them.
fn two_step_swap(new: &Path, old: &Path) -> io::Result<()> {
    let parked = new.with_extension("old");
    std::fs::rename(old, &parked)?;
    std::fs::rename(new, old)?;
    std::fs::rename(&parked, new)
}

#[cfg(test)]
mod path_tests {
    use super::*;

    #[test]
    fn a_junction_target_reads_back_in_its_plain_form() {
        assert_eq!(plain_path(Path::new(r"\\?\C:\Users\Jane Doe\.claude\skills\a")), PathBuf::from(r"C:\Users\Jane Doe\.claude\skills\a"));
        assert_eq!(plain_path(Path::new(r"\??\C:\Users\j\.claude\skills\a")), PathBuf::from(r"C:\Users\j\.claude\skills\a"));
        assert_eq!(plain_path(Path::new(r"\\?\UNC\server\share\a")), PathBuf::from(r"\\server\share\a"));
        assert_eq!(plain_path(Path::new("/home/u/.claude/skills/a")), PathBuf::from("/home/u/.claude/skills/a"));
        assert_eq!(plain_path(Path::new(r"C:\plain")), PathBuf::from(r"C:\plain"));
    }
}

// The tests compare inodes and make links freely, which needs a Unix.
#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use crate::testing::scratch;

    fn set(agent: &str, source: &Path, names: &[&str]) -> LinkSet {
        LinkSet {
            agent: agent.to_string(),
            source: Source::Folder(source.to_path_buf()),
            links: names.iter().map(|name| (name.to_string(), source.join(name))).collect(),
        }
    }

    fn listing(folder: &Path) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(folder)
            .map(|entries| entries.flatten().map(|entry| entry.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default();
        names.sort();
        names
    }

    #[test]
    fn links_are_created_pruned_and_left_alone_when_right() {
        let root = scratch("links-basic");
        let (skills, claude) = (root.join("puffin/skills"), root.join("claude/skills"));
        for name in ["a", "b", "c"] {
            std::fs::create_dir_all(claude.join(name)).unwrap_or_default();
        }
        let first = rebuild(&skills, &[set("claude", &claude, &["a", "b"])], "t1").unwrap_or_default();
        assert_eq!(first.changed, vec!["from-claude".to_string()]);
        assert_eq!(listing(&skills.join("from-claude")), vec!["a", "b"]);
        assert_eq!(std::fs::read_link(skills.join("from-claude/a")).ok(), Some(claude.join("a")));

        // Nothing to do: the folder is not rewritten (its inode is the same).
        let inode = |path: &Path| {
            use std::os::unix::fs::MetadataExt;
            path.metadata().map(|metadata| metadata.ino()).unwrap_or_default()
        };
        let before = inode(&skills.join("from-claude"));
        let second = rebuild(&skills, &[set("claude", &claude, &["a", "b"])], "t2").unwrap_or_default();
        assert_eq!(second, Rebuilt::default());
        assert_eq!(inode(&skills.join("from-claude")), before);

        // One skill gone, one new: pruned and added, nothing quarantined, no staging left behind.
        let third = rebuild(&skills, &[set("claude", &claude, &["b", "c"])], "t3").unwrap_or_default();
        assert_eq!(third.quarantined, Vec::<PathBuf>::new());
        assert_eq!(listing(&skills.join("from-claude")), vec!["b", "c"]);
        assert_eq!(listing(&skills), vec![".puffin-links.lock", "from-claude"]);

        // The source switched off, or its last skill gone: the folder goes, the targets stay.
        let fourth = rebuild(&skills, &[set("claude", &claude, &[])], "t4").unwrap_or_default();
        assert_eq!(fourth.changed, vec!["from-claude".to_string()]);
        assert!(!skills.join("from-claude").exists());
        assert!(claude.join("b").is_dir());
    }

    #[test]
    fn whatever_is_planted_under_a_from_name_is_quarantined() {
        let root = scratch("links-planted");
        let (skills, claude) = (root.join("puffin/skills"), root.join("claude/skills"));
        std::fs::create_dir_all(claude.join("a")).unwrap_or_default();
        let elsewhere = root.join("elsewhere/evil");
        std::fs::create_dir_all(&elsewhere).unwrap_or_default();
        rebuild(&skills, &[set("claude", &claude, &["a"])], "t1").unwrap_or_default();

        // A plain folder, a link pointing outside the agent's own folder, and a from- name the
        // launcher does not know.
        std::fs::create_dir_all(skills.join("from-claude/planted")).unwrap_or_default();
        std::fs::write(skills.join("from-claude/planted/SKILL.md"), "x").unwrap_or_default();
        symlink_dir(&elsewhere, &skills.join("from-claude/evil")).unwrap_or_default();
        symlink_dir(&claude.join("../../elsewhere/evil"), &skills.join("from-claude/dotdot")).unwrap_or_default();
        std::fs::create_dir_all(skills.join("from-mallory/x")).unwrap_or_default();

        let rebuilt = rebuild(&skills, &[set("claude", &claude, &["a"])], "t2").unwrap_or_default();
        assert_eq!(listing(&skills.join("from-claude")), vec!["a"]);
        assert!(!skills.join("from-mallory").exists());
        let quarantine = skills.join(".quarantine/t2");
        assert_eq!(listing(&quarantine.join("from-claude")), vec!["dotdot", "evil", "planted"]);
        assert!(quarantine.join("from-claude/planted/SKILL.md").is_file());
        assert!(quarantine.join("from-mallory/x").is_dir());
        assert_eq!(rebuilt.quarantined.len(), 4);
        // The link was moved, not followed: its target is untouched.
        assert!(elsewhere.is_dir());
    }

    #[test]
    fn a_from_name_that_is_a_link_or_a_file_is_replaced_whole() {
        let root = scratch("links-replaced");
        let (skills, claude) = (root.join("puffin/skills"), root.join("claude/skills"));
        std::fs::create_dir_all(claude.join("a")).unwrap_or_default();
        let own = root.join("own");
        std::fs::create_dir_all(own.join("fake")).unwrap_or_default();
        std::fs::create_dir_all(&skills).unwrap_or_default();
        symlink_dir(&own, &skills.join("from-claude")).unwrap_or_default();
        std::fs::write(skills.join("from-gemini"), "not a folder").unwrap_or_default();

        let rebuilt = rebuild(&skills, &[set("claude", &claude, &["a"])], "t1").unwrap_or_default();
        assert!(skills.join("from-claude").symlink_metadata().is_ok_and(|metadata| metadata.is_dir()));
        assert_eq!(listing(&skills.join("from-claude")), vec!["a"]);
        assert!(!skills.join("from-gemini").exists());
        assert_eq!(rebuilt.quarantined.len(), 2);
        assert!(own.join("fake").is_dir());
    }

    #[test]
    fn a_repository_folder_keeps_links_into_any_repository_and_nothing_else() {
        let root = scratch("links-repository");
        let skills = root.join("puffin/skills");
        let (first, second) = (root.join("one/.claude/skills"), root.join("two/.claude/skills"));
        for dir in [first.join("lint"), second.join("fmt")] {
            std::fs::create_dir_all(dir).unwrap_or_default();
        }
        let repo = |links: &[(&str, &Path)]| LinkSet {
            agent: "repo-claude".to_string(),
            source: Source::AnyRepository(".claude/skills"),
            links: links.iter().map(|(name, target)| (name.to_string(), target.to_path_buf())).collect(),
        };
        rebuild(&skills, &[repo(&[("lint", &first.join("lint"))])], "t1").unwrap_or_default();
        assert_eq!(listing(&skills.join("from-repo-claude")), vec!["lint"]);

        // Started in another repository: the first one's link is the launcher's, so it is removed,
        // not quarantined; a link planted beside it pointing elsewhere is quarantined.
        symlink_dir(Path::new("/etc"), &skills.join("from-repo-claude/planted")).unwrap_or_default();
        let rebuilt = rebuild(&skills, &[repo(&[("fmt", &second.join("fmt"))])], "t2").unwrap_or_default();
        assert_eq!(listing(&skills.join("from-repo-claude")), vec!["fmt"]);
        assert_eq!(rebuilt.quarantined, vec![skills.join(".quarantine/t2/from-repo-claude/planted")]);

        // Outside any repository the folder goes, quietly.
        let gone = rebuild(&skills, &[repo(&[])], "t3").unwrap_or_default();
        assert!(gone.quarantined.is_empty());
        assert!(!skills.join("from-repo-claude").exists());
        assert!(second.join("fmt").is_dir());

        let holds = |target: &str| Source::AnyRepository(".claude/skills").holds(Path::new(target));
        assert!(holds("/w/r/.claude/skills/x"));
        assert!(!holds("/w/r/.claude/x"));
        assert!(!holds("/w/r/.claude/skills/../../etc"));
        assert!(!holds("relative/.claude/skills/x"));
    }
}
