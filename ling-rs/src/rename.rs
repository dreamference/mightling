//! Puffin became Mightling (specs/DREAMFERENCE_RENAME_MIGHTLING.md §4.2).
//!
//! A 1.4.x installation updates itself with `puffin update`, which installs this binary under the
//! old name in the old folder. The first time it runs, it moves that installation to the new names
//! once: the install folder and the binaries in it, the agent's home (an allow-list, never the
//! login), the configuration keys, the command links, the pairing key and the desktop app's data.
//! Every step checks before it acts, so a second run finds nothing to do and says nothing. The old
//! names are not kept as aliases: the old links are removed, not redirected.
//!
//! Std only, and called first thing in `main()` (from `home::use_mightling_home`), before any thread
//! exists.

use std::io;
use std::path::Path;
use std::path::PathBuf;

/// The agent's home before the rename, relative to `$HOME`.
pub const LEGACY_HOME_DIR: &str = ".puffin";
/// Where release binaries lived before the rename, and where they live now, relative to `$HOME`.
pub const LEGACY_INSTALL_DIR: &str = ".local/share/dreamference/puffin";
pub const INSTALL_DIR: &str = ".local/share/dreamference/mightling";
/// Written into the new home after the carry-over, listing what came across.
pub const MARKER: &str = ".migrated-from-puffin";

/// Binaries in the install folder, by their old and new names.
const BINARIES: &[(&str, &str)] = &[
    ("puffin", "ling"),
    ("puffin-search", "ling-search"),
    ("puffin-fetch", "ling-fetch"),
    ("puffin-code", "ling-code"),
];
/// Command links the old installation put in `~/.local/bin`, removed when they are links.
const LEGACY_LINKS: &[&str] = &[
    "puffin",
    "puffin-search",
    "puffin-fetch",
    "puffin-code",
    "puffin-app",
    "puffin-admin",
];
/// Entries of the old home carried into the new one: Codex's own state (the same allow-list as
/// the first-run copy from `~/.codex`, `home::should_carry_over`) plus the launcher's.
const CARRY_OVER: &[&str] = &[
    "session_index.jsonl",
    "airgapped",
    "cave_mode",
    "audit",
    "night",
    "system-prompts",
    "node.json",
];
/// Files of the old home whose names carried the old product name.
const RENAMED_FILES: &[(&str, &str)] = &[
    ("puffin-code.toml", "ling-code.toml"),
    ("puffin-skills.toml", "ling-skills.toml"),
];
/// Each installed skill records where it came from in a file named after the product
/// (`ling-skills`' `install::ORIGIN_FILE` today), so `ling skill` still knows its source.
const ORIGIN_FILES: (&str, &str) = (".puffin-origin.toml", ".mightling-origin.toml");
/// Configuration keys took the product's name as a prefix.
const LEGACY_KEY_PREFIX: &str = "puffin_";
const KEY_PREFIX: &str = "mightling_";

/// What a migration did, one line per step; empty when there was nothing to do.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub lines: Vec<String>,
    /// An old `puffin-admin` link was removed and its virtualenv has no `ling-admin` yet.
    pub admin_missing: bool,
}

impl Report {
    pub fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    /// The notice printed once, after a migration that did something.
    pub fn notice(&self) -> String {
        let mut text = String::from("🐦 Puffin is now Mightling: run `ling`.\n");
        for line in &self.lines {
            text.push_str("   ");
            text.push_str(line);
            text.push('\n');
        }
        if self.admin_missing {
            text.push_str(
                "   `puffin-admin` is now `ling-admin`: run the installer again to update it \
                 (curl -fsSL https://github.com/dreamference/mightling/releases/latest/download/install.sh | bash).\n",
            );
        }
        text
    }
}

/// Moves what the old installation left under `home` (and the configuration in `cwd`).
///
/// The agent's home is carried over by [`carry_over_home`], which `use_mightling_home` calls when
/// it creates the new home; this covers everything else.
pub fn migrate(home: &Path, cwd: &Path, config_path: Option<&Path>) -> Report {
    let mut report = Report::default();
    move_install_dir(home, &mut report);
    relink(home, &mut report);
    let mut configs = vec![
        home.join(".config/dreamference/config.toml"),
        cwd.join("dreamference.toml"),
    ];
    if let Some(path) = config_path {
        configs.push(path.to_path_buf());
    }
    configs.sort();
    configs.dedup();
    for path in configs {
        if let Ok(true) = rewrite_keys(&path) {
            report.lines.push(format!("renamed the puffin_* settings in {}", path.display()));
        }
    }
    rename_if_free(
        &home.join(".ssh/puffin-node_ed25519"),
        &home.join(".ssh/mightling-node_ed25519"),
        &mut report,
    );
    rename_if_free(
        &home.join(".ssh/puffin-node_ed25519.pub"),
        &home.join(".ssh/mightling-node_ed25519.pub"),
        &mut report,
    );
    rename_if_free(
        &home.join(".local/share/dev.dreamference.puffin"),
        &home.join(".local/share/dev.dreamference.mightling"),
        &mut report,
    );
    report
}

fn rename_if_free(from: &Path, to: &Path, report: &mut Report) {
    if std::fs::symlink_metadata(from).is_ok() && std::fs::symlink_metadata(to).is_err() {
        if let Some(parent) = to.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if std::fs::rename(from, to).is_ok() {
            report
                .lines
                .push(format!("moved {} to {}", from.display(), to.display()));
        }
    }
}

fn move_install_dir(home: &Path, report: &mut Report) {
    let legacy = home.join(LEGACY_INSTALL_DIR);
    let current = home.join(INSTALL_DIR);
    if legacy.is_dir() && std::fs::symlink_metadata(&current).is_err() {
        if let Some(parent) = current.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if std::fs::rename(&legacy, &current).is_ok() {
            report.lines.push(format!(
                "moved {} to {}",
                legacy.display(),
                current.display()
            ));
        }
    }
    let bin = current.join("bin");
    for (old, new) in BINARIES {
        let from = bin.join(old);
        let to = bin.join(new);
        if from.is_file() && std::fs::symlink_metadata(&to).is_err() && std::fs::rename(&from, &to).is_ok() {
            report.lines.push(format!("renamed {old} to {new}"));
        }
    }
}

fn relink(home: &Path, report: &mut Report) {
    let links = home.join(".local/bin");
    let bin = home.join(INSTALL_DIR).join("bin");
    let mut removed = Vec::new();
    for name in LEGACY_LINKS {
        let path = links.join(name);
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        // A real file of that name belongs to something else.
        if !metadata.file_type().is_symlink() {
            continue;
        }
        let target = std::fs::read_link(&path).ok();
        if std::fs::remove_file(&path).is_ok() {
            removed.push(*name);
            if *name == "puffin-admin" {
                let admin = target
                    .as_deref()
                    .and_then(Path::parent)
                    .map(|venv_bin| venv_bin.join("ling-admin"));
                match admin {
                    Some(admin) if admin.is_file() => {
                        let _ = link(&admin, &links.join("ling-admin"));
                    }
                    _ => report.admin_missing = true,
                }
            }
        }
    }
    if removed.is_empty() {
        return;
    }
    for (old, new) in BINARIES {
        if removed.contains(old) && bin.join(new).is_file() {
            let _ = link(&bin.join(new), &links.join(new));
        }
    }
    report.lines.push(format!(
        "replaced the links {} in {}",
        removed.join(", "),
        links.display()
    ));
}

/// Links `link_path` to `target`, unless a real file already has that name.
fn link(target: &Path, link_path: &Path) -> io::Result<()> {
    match std::fs::symlink_metadata(link_path) {
        Ok(metadata) if !metadata.file_type().is_symlink() => return Ok(()),
        Ok(_) => std::fs::remove_file(link_path)?,
        Err(_) => {}
    }
    if let Some(parent) = link_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::os::unix::fs::symlink(target, link_path)
}

/// Rewrites `puffin_<key> =` lines to `mightling_<key> =`, whole-line key matches only.
///
/// Returns whether the file changed. A missing file is not an error.
pub fn rewrite_keys(path: &Path) -> io::Result<bool> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    let mut changed = false;
    let rewritten: Vec<String> = text
        .split_inclusive('\n')
        .map(|line| {
            let indent = line.len() - line.trim_start().len();
            let rest = &line[indent..];
            let is_key = rest.starts_with(LEGACY_KEY_PREFIX)
                && rest[LEGACY_KEY_PREFIX.len()..]
                    .split(|c: char| c == '=' || c.is_whitespace())
                    .next()
                    .is_some_and(|key| !key.is_empty() && key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
                && rest.contains('=');
            if is_key {
                changed = true;
                format!("{}{KEY_PREFIX}{}", &line[..indent], &rest[LEGACY_KEY_PREFIX.len()..])
            } else {
                line.to_string()
            }
        })
        .collect();
    if changed {
        std::fs::write(path, rewritten.concat())?;
    }
    Ok(changed)
}

/// Fills a new, empty home from the old one: Codex's state (`home::should_carry_over`) and the
/// launcher's (`CARRY_OVER`), with the renamed files under their new names. `auth.json`,
/// `installation_id`, the logs and anything not named stay behind, and the old home is left as it
/// was, so the user can go back to it by hand. Paths into the old home in the copied `config.toml`
/// are rewritten.
pub fn carry_over_home(legacy: &Path, home: &Path) -> io::Result<usize> {
    let mut carried = Vec::new();
    for entry in std::fs::read_dir(legacy)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let destination = RENAMED_FILES
            .iter()
            .find(|(old, _)| *old == name)
            .map(|(_, new)| (*new).to_string())
            .or_else(|| {
                (crate::home::should_carry_over(&name) || CARRY_OVER.contains(&name.as_str()))
                    .then(|| name.clone())
            });
        let Some(destination) = destination else {
            continue;
        };
        if copy_tree(&entry.path(), &home.join(&destination)).is_ok() {
            carried.push(destination);
        }
    }
    rename_origin_files(&home.join("skills"));
    let config = home.join("config.toml");
    if let Ok(text) = std::fs::read_to_string(&config) {
        let old = format!("/{LEGACY_HOME_DIR}/");
        let new = format!("/{}/", crate::home::MIGHTLING_HOME_DIR);
        if text.contains(&old) {
            std::fs::write(&config, text.replace(&old, &new))?;
        }
    }
    carried.sort();
    std::fs::write(
        home.join(MARKER),
        format!("from {}\n{}\n", legacy.display(), carried.join("\n")),
    )?;
    Ok(carried.len())
}

/// Gives every installed skill's origin record its new name. Links (the `from-*` skills of other
/// agents) are not followed: those folders are not the user's installations.
fn rename_origin_files(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if metadata.is_dir() {
            rename_origin_files(&path);
        } else if entry.file_name() == ORIGIN_FILES.0 {
            let _ = std::fs::rename(&path, path.with_file_name(ORIGIN_FILES.1));
        }
    }
}

fn copy_tree(from: &Path, to: &Path) -> io::Result<()> {
    let metadata = std::fs::symlink_metadata(from)?;
    if metadata.file_type().is_symlink() {
        std::os::unix::fs::symlink(std::fs::read_link(from)?, to)
    } else if metadata.is_dir() {
        std::fs::create_dir_all(to)?;
        for entry in std::fs::read_dir(from)? {
            let entry = entry?;
            copy_tree(&entry.path(), &to.join(entry.file_name()))?;
        }
        Ok(())
    } else {
        std::fs::copy(from, to).map(|_| ())
    }
}

/// The old home under `home`, when it is there.
pub fn legacy_home(home: &Path) -> Option<PathBuf> {
    let path = home.join(LEGACY_HOME_DIR);
    path.is_dir().then_some(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-rename-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    /// The layout `install.sh` and `puffin update` left on a 1.4.x client and node.
    fn legacy_layout(home: &Path) {
        let bin = home.join(LEGACY_INSTALL_DIR).join("bin");
        for name in ["puffin", "puffin-search", "puffin-fetch", "puffin-code", "codex-code-mode-host"] {
            write(&bin.join(name), name);
        }
        write(&home.join(LEGACY_INSTALL_DIR).join("indexers/scip-python"), "tool");
        let links = home.join(".local/bin");
        std::fs::create_dir_all(&links).unwrap();
        for name in ["puffin", "puffin-search", "puffin-fetch", "puffin-code"] {
            std::os::unix::fs::symlink(bin.join(name), links.join(name)).unwrap();
        }
        let venv_bin = home.join(".local/share/dreamference/venv/bin");
        write(&venv_bin.join("puffin-admin"), "admin");
        std::os::unix::fs::symlink(venv_bin.join("puffin-admin"), links.join("puffin-admin")).unwrap();
        write(&links.join("puffin-app"), "a real file someone else put here");
        write(
            &home.join(".config/dreamference/config.toml"),
            "vllm_host = \"http://gb10:8000\"\npuffin_airgapped = \"on\"\n  puffin_gmail = false\n# puffin_prompt = \"x\" stays a comment\n[night]\nindex = true\n",
        );
        write(&home.join(".ssh/puffin-node_ed25519"), "key");
        write(&home.join(".ssh/puffin-node_ed25519.pub"), "ssh-ed25519 AAAA puffin-node");
        let old_home = home.join(LEGACY_HOME_DIR);
        write(&old_home.join("auth.json"), "{\"token\": \"secret\"}");
        write(&old_home.join("installation_id"), "id");
        write(&old_home.join("logs_2.sqlite"), "log");
        write(&old_home.join("history.jsonl"), "{}\n");
        write(&old_home.join("state_5.sqlite"), "db");
        write(&old_home.join("night/tasks/a.json"), "{}");
        write(&old_home.join("system-prompts/mine.md"), "be brief");
        write(&old_home.join("puffin-code.toml"), "[submodules]\n");
        write(&old_home.join("skills/pdf/.puffin-origin.toml"), "source = \"anthropic/pdf\"\n");
        write(
            &old_home.join("config.toml"),
            "[[skills.config]]\npath = \"/home/u/.puffin/skills/from-claude/x/SKILL.md\"\n",
        );
    }

    #[test]
    fn a_14x_layout_migrates_and_a_second_run_does_nothing() {
        let home = scratch("full");
        legacy_layout(&home);
        let cwd = home.join("project");
        write(&cwd.join("dreamference.toml"), "puffin_cave_mode = \"off\"\n");

        let report = migrate(&home, &cwd, None);
        assert!(!report.is_empty());
        let bin = home.join(INSTALL_DIR).join("bin");
        for name in ["ling", "ling-search", "ling-fetch", "ling-code", "codex-code-mode-host"] {
            assert!(bin.join(name).is_file(), "{name} in the new install folder");
        }
        assert!(!home.join(LEGACY_INSTALL_DIR).exists());
        assert!(home.join(INSTALL_DIR).join("indexers/scip-python").is_file(), "the code index's tools moved too");
        let links = home.join(".local/bin");
        for name in ["puffin", "puffin-search", "puffin-fetch", "puffin-code", "puffin-admin"] {
            assert!(std::fs::symlink_metadata(links.join(name)).is_err(), "{name} is not kept as an alias");
        }
        assert!(links.join("puffin-app").is_file(), "a real file is left alone");
        for name in ["ling", "ling-search", "ling-fetch", "ling-code"] {
            assert_eq!(std::fs::read_link(links.join(name)).unwrap(), bin.join(name));
        }
        assert!(report.admin_missing, "the venv has no ling-admin yet");
        assert!(report.notice().contains("Puffin is now Mightling: run `ling`"));
        let config = std::fs::read_to_string(home.join(".config/dreamference/config.toml")).unwrap();
        assert!(config.contains("mightling_airgapped = \"on\"") && config.contains("  mightling_gmail = false"));
        assert!(config.contains("# puffin_prompt"), "comments are not keys");
        assert!(config.contains("vllm_host") && !config.contains("\npuffin_"));
        assert_eq!(
            std::fs::read_to_string(cwd.join("dreamference.toml")).unwrap(),
            "mightling_cave_mode = \"off\"\n"
        );
        assert!(home.join(".ssh/mightling-node_ed25519").is_file());
        assert!(home.join(".ssh/mightling-node_ed25519.pub").is_file());

        assert_eq!(migrate(&home, &cwd, None), Report::default(), "a second run finds nothing to do");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn the_admin_link_follows_when_the_venv_already_has_ling_admin() {
        let home = scratch("admin");
        legacy_layout(&home);
        let venv_bin = home.join(".local/share/dreamference/venv/bin");
        write(&venv_bin.join("ling-admin"), "admin");
        let report = migrate(&home, &home, None);
        assert!(!report.admin_missing);
        assert_eq!(
            std::fs::read_link(home.join(".local/bin/ling-admin")).unwrap(),
            venv_bin.join("ling-admin")
        );
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn the_home_is_carried_over_without_the_login_and_with_its_paths_rewritten() {
        let home = scratch("home");
        legacy_layout(&home);
        let new_home = home.join(crate::home::MIGHTLING_HOME_DIR);
        std::fs::create_dir_all(&new_home).unwrap();
        let carried = carry_over_home(&home.join(LEGACY_HOME_DIR), &new_home).unwrap();
        assert!(carried >= 5);
        for name in ["auth.json", "installation_id", "logs_2.sqlite", "puffin-code.toml"] {
            assert!(!new_home.join(name).exists(), "{name} is not carried over");
        }
        for name in [
            "history.jsonl",
            "state_5.sqlite",
            "night/tasks/a.json",
            "system-prompts/mine.md",
            "ling-code.toml",
            "skills/pdf/.mightling-origin.toml",
        ] {
            assert!(new_home.join(name).exists(), "{name} is carried over");
        }
        let config = std::fs::read_to_string(new_home.join("config.toml")).unwrap();
        assert!(config.contains("/home/u/.mightling/skills/") && !config.contains("/.puffin/"));
        assert!(new_home.join(MARKER).is_file());
        assert!(home.join(LEGACY_HOME_DIR).join("auth.json").is_file(), "the old home is left as it was");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn nothing_old_means_nothing_to_do() {
        let home = scratch("clean");
        assert!(migrate(&home, &home, None).is_empty());
        let _ = std::fs::remove_dir_all(&home);
    }
}
