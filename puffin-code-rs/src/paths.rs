//! Where things are: the repository, its main worktree, the state directory, and git.
//!
//! Every git call passes `--no-optional-locks`: queries run inside Codex's sandbox, where `.git` is
//! read-only, and a plain `git status` would try to refresh the index there (spec §2).

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{bail, Context, Result};

/// The repository a command runs in.
#[derive(Debug, Clone)]
pub struct Repo {
    /// The working tree the user is in: files are read and git is asked here.
    pub root: PathBuf,
    /// The main worktree. Linked worktrees share its index (spec §4.1), so the stores live here.
    pub main_root: PathBuf,
    /// False for a directory that is not a git repository; freshness then falls back to `stat`.
    pub is_git: bool,
}

impl Repo {
    /// Finds the repository containing `cwd`.
    ///
    /// Args: `cwd`, any directory inside the working tree.
    /// Returns: the repository, or `cwd` itself (not a git repository) when git knows none.
    pub fn discover(cwd: &Path) -> Result<Repo> {
        if let Ok(root) = std::env::var("PUFFIN_CODE_ROOT") {
            let root = PathBuf::from(root);
            let is_git = git(&root, &["rev-parse", "--git-dir"]).is_ok();
            return Ok(Repo { main_root: root.clone(), root, is_git });
        }
        match git(cwd, &["rev-parse", "--show-toplevel"]) {
            Ok(top) => {
                let root = PathBuf::from(top.trim());
                let main_root = main_worktree(&root).unwrap_or_else(|| root.clone());
                Ok(Repo { root, main_root, is_git: true })
            }
            Err(_) => Ok(Repo { root: cwd.to_path_buf(), main_root: cwd.to_path_buf(), is_git: false }),
        }
    }

    /// The directory puffin-code keeps its state in: the SCIP stores, manifests and requests.
    pub fn state_dir(&self) -> PathBuf {
        match std::env::var("PUFFIN_CODE_STATE_DIR") {
            Ok(dir) => PathBuf::from(dir),
            Err(_) => self.main_root.join(".dreamference"),
        }
    }

    /// Where the SCIP stores and their manifest live.
    pub fn scip_dir(&self) -> PathBuf {
        self.state_dir().join("scip")
    }

    /// Makes a repository-relative path absolute in the working tree.
    pub fn abs(&self, rel: &str) -> PathBuf {
        self.root.join(rel)
    }

    /// The submodule paths of the repository, relative to its root.
    pub fn submodules(&self) -> Vec<String> {
        if !self.is_git {
            return Vec::new();
        }
        let Ok(out) = git(&self.root, &["submodule", "status"]) else { return Vec::new() };
        out.lines()
            .filter_map(|line| line.get(1..)?.split_whitespace().nth(1).map(str::to_string))
            .collect()
    }

    /// The commit checked out, if any.
    pub fn head(&self) -> Option<String> {
        if !self.is_git {
            return None;
        }
        git(&self.root, &["rev-parse", "HEAD"]).ok().map(|s| s.trim().to_string())
    }
}

/// The main worktree of a linked worktree: the parent of the shared `.git` directory.
fn main_worktree(root: &Path) -> Option<PathBuf> {
    let common = git(root, &["rev-parse", "--path-format=absolute", "--git-common-dir"]).ok()?;
    let common = PathBuf::from(common.trim());
    // A submodule's common dir is `<super>/.git/modules/<name>`: it is its own main worktree.
    if common.file_name()? == ".git" {
        common.parent().map(Path::to_path_buf)
    } else {
        None
    }
}

/// Runs git in `dir` without optional locks and returns its standard output.
///
/// Args: `dir`, the directory to run in; `args`, git's arguments.
/// Returns: stdout as text, or an error carrying stderr when git fails.
pub fn git(dir: &Path, args: &[&str]) -> Result<String> {
    let output = Command::new("git")
        .arg("--no-optional-locks")
        .args(args)
        .current_dir(dir)
        .env("GIT_OPTIONAL_LOCKS", "0")
        .stdin(Stdio::null())
        .output()
        .with_context(|| format!("running git {}", args.join(" ")))?;
    if !output.status.success() {
        bail!("git {}: {}", args.join(" "), String::from_utf8_lossy(&output.stderr).trim());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Like [`git`], splitting NUL-terminated output (for `-z`).
pub fn git_z(dir: &Path, args: &[&str]) -> Result<Vec<String>> {
    Ok(git(dir, args)?.split('\0').filter(|s| !s.is_empty()).map(str::to_string).collect())
}

/// The user's home directory: `HOME`, or `USERPROFILE` on Windows, where `HOME` is usually unset.
pub fn home() -> PathBuf {
    home_from(std::env::var_os("HOME"), std::env::var_os("USERPROFILE"))
}

/// [`home`] on given values: the first that is set and not empty, else `/`.
pub fn home_from(home: Option<std::ffi::OsString>, userprofile: Option<std::ffi::OsString>) -> PathBuf {
    home.filter(|home| !home.is_empty())
        .or_else(|| userprofile.filter(|home| !home.is_empty()))
        .map_or_else(|| PathBuf::from("/"), PathBuf::from)
}

#[cfg(test)]
mod home_tests {
    use super::*;

    #[test]
    fn the_home_folder_falls_back_to_userprofile() {
        use std::ffi::OsString;
        assert_eq!(home_from(None, Some(OsString::from(r"C:\Users\Jane Doe"))), PathBuf::from(r"C:\Users\Jane Doe"));
        assert_eq!(home_from(Some(OsString::from("/home/u")), Some(OsString::from(r"C:\x"))), PathBuf::from("/home/u"));
        assert_eq!(home_from(Some(OsString::new()), None), PathBuf::from("/"));
    }
}

/// Where Puffin installs its binaries and the pinned tools (spec §5).
pub fn install_bin_dir() -> PathBuf {
    match std::env::var("PUFFIN_CODE_TOOLS_DIR") {
        Ok(dir) => PathBuf::from(dir),
        Err(_) => home().join(".local/share/dreamference/puffin/bin"),
    }
}

/// Where the npm-installed indexers live (spec §5).
pub fn indexers_dir() -> PathBuf {
    match std::env::var("PUFFIN_CODE_INDEXERS_DIR") {
        Ok(dir) => PathBuf::from(dir),
        Err(_) => home().join(".local/share/dreamference/puffin/indexers"),
    }
}

/// `$CODEX_HOME`, resolved as `puffin-rs/src/home.rs` does: `~/.puffin` unless set.
pub fn codex_home() -> PathBuf {
    match std::env::var_os("CODEX_HOME") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => home().join(".puffin"),
    }
}

/// The per-user runtime directory, for the host-wide admission lock (spec §6.4).
pub fn runtime_dir() -> PathBuf {
    match std::env::var_os("XDG_RUNTIME_DIR") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => std::env::temp_dir(),
    }
}
