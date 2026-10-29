//! Where things are: the repository, its main worktree, the state directory, and git.
//!
//! Every git call passes `--no-optional-locks`: queries run inside Codex's sandbox, where `.git` is
//! read-only, and a plain `git status` would try to refresh the index there (spec §2).

use std::path::{Path, PathBuf};

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
        if let Ok(root) = std::env::var("MIGHTLING_CODE_ROOT") {
            let root = PathBuf::from(root);
            let is_git = git(&root, &["rev-parse", "--git-dir"]).is_ok();
            return Ok(Repo {
                main_root: root.clone(),
                root,
                is_git,
            });
        }
        match git(cwd, &["rev-parse", "--show-toplevel"]) {
            Ok(top) => {
                let root = PathBuf::from(top.trim());
                let main_root = main_worktree(&root).unwrap_or_else(|| root.clone());
                Ok(Repo {
                    root,
                    main_root,
                    is_git: true,
                })
            }
            Err(_) => Ok(Repo {
                root: cwd.to_path_buf(),
                main_root: cwd.to_path_buf(),
                is_git: false,
            }),
        }
    }

    /// The directory ling-code keeps its state in: the SCIP stores, manifests and requests.
    pub fn state_dir(&self) -> PathBuf {
        match std::env::var("MIGHTLING_CODE_STATE_DIR") {
            Ok(dir) => PathBuf::from(dir),
            Err(_) => self.main_root.join(".dreamference"),
        }
    }

    /// Opens (creating, never truncating) a file the session keeps in the state directory.
    ///
    /// The directory is writable from inside the sandbox and a clone can commit it, while the
    /// session runs outside: a symlink planted for the file, or for `.dreamference` itself, would
    /// have it write where the symlink points. Both are refused.
    pub fn state_file(&self, name: &str) -> std::io::Result<std::fs::File> {
        let named = std::env::var_os("MIGHTLING_CODE_STATE_DIR").is_some();
        open_state_file(&self.state_dir(), name, !named)
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
        let Ok(out) = git(&self.root, &["submodule", "status"]) else {
            return Vec::new();
        };
        out.lines()
            .filter_map(|line| line.get(1..)?.split_whitespace().nth(1).map(str::to_string))
            .collect()
    }

    /// The commit checked out, if any.
    pub fn head(&self) -> Option<String> {
        if !self.is_git {
            return None;
        }
        git(&self.root, &["rev-parse", "HEAD"])
            .ok()
            .map(|s| s.trim().to_string())
    }
}

/// The main worktree of a linked worktree: the parent of the shared `.git` directory.
fn main_worktree(root: &Path) -> Option<PathBuf> {
    let common = git(
        root,
        &["rev-parse", "--path-format=absolute", "--git-common-dir"],
    )
    .ok()?;
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
    // The checkout's config is the agent's to write and indexing runs outside the sandbox:
    // `ling_safegit` pins every key that makes git run a program.
    let mut all = vec!["--no-optional-locks"];
    all.extend_from_slice(args);
    let output = ling_safegit::run(
        ling_safegit::command(dir, &all),
        std::time::Duration::from_secs(600),
    )
    .with_context(|| format!("running git {}", args.join(" ")))?;
    if !output.status.success() {
        bail!(
            "git {}: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Like [`git`], splitting NUL-terminated output (for `-z`).
pub fn git_z(dir: &Path, args: &[&str]) -> Result<Vec<String>> {
    Ok(git(dir, args)?
        .split('\0')
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect())
}

/// Whether process `pid` is running. Only a Unix can be asked here; indexing, the one thing that
/// asks, runs only there for now (specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md §12).
#[cfg(unix)]
pub fn process_alive(pid: i32) -> bool {
    // SAFETY: signal 0 only checks that the process exists and may be signalled.
    // pid 0 and negative pids address a process group or every process, not one process.
    pid > 0 && unsafe { libc::kill(pid, 0) == 0 }
}

#[cfg(not(unix))]
pub fn process_alive(_pid: i32) -> bool {
    false
}

/// Indexing needs bubblewrap, systemd scopes and cgroups: a Linux host (§9). Elsewhere `index`
/// and `session` say so, and queries read an index built on Linux.
pub fn indexing_supported() -> anyhow::Result<()> {
    if indexing_here() {
        Ok(())
    } else {
        anyhow::bail!("indexing runs on Linux only for now; on this system ling-code answers queries from an existing index")
    }
}

/// Whether this system builds indexes: the condition [`indexing_supported`] checks, which the
/// messages below are given so they never name a command that cannot run here.
pub fn indexing_here() -> bool {
    cfg!(target_os = "linux")
}

/// What a message says in place of `ling-code index` or `ling-admin code setup` on a system that
/// does not index (a Windows or macOS client, MIGHTLING_WINDOWS_ARM §23.4 W8): neither exists there.
pub const BUILT_ON_LINUX: &str = "the index is built on Linux nodes only for now";

/// `` `command` `` where indexing runs, for a hint in brackets; else [`BUILT_ON_LINUX`].
pub fn index_command(indexing: bool, command: &str) -> String {
    if indexing {
        format!("`{command}`")
    } else {
        BUILT_ON_LINUX.to_string()
    }
}

/// `` run `command` `` where indexing runs, for a hint after a semicolon; else [`BUILT_ON_LINUX`].
pub fn run_index_command(indexing: bool, command: &str) -> String {
    if indexing {
        format!("run `{command}`")
    } else {
        BUILT_ON_LINUX.to_string()
    }
}

/// What a query answers when the repository has no index yet; `search` adds where to look meanwhile.
pub fn no_index_yet(indexing: bool, search: bool) -> String {
    let meanwhile = match (search, indexing) {
        (false, _) => "",
        (true, true) => ", and use rg meanwhile",
        (true, false) => "; search the files meanwhile",
    };
    format!(
        "no code index for this repository yet; {}{meanwhile}",
        run_index_command(indexing, "ling-code index")
    )
}

/// [`Repo::state_file`], for a directory that is refused as a symlink when `refuse_linked_dir`.
fn open_state_file(
    dir: &Path,
    name: &str,
    refuse_linked_dir: bool,
) -> std::io::Result<std::fs::File> {
    let refuse = |what: &Path| {
        std::io::Error::other(format!(
            "{} is not a regular file or directory; not writing through it",
            what.display()
        ))
    };
    if refuse_linked_dir && std::fs::symlink_metadata(dir).is_ok_and(|m| m.file_type().is_symlink())
    {
        return Err(refuse(dir));
    }
    std::fs::create_dir_all(dir)?;
    let path = dir.join(name);
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true);
    // No following a symlink, and no blocking on a FIFO with no reader.
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::custom_flags(
        &mut options,
        libc::O_NOFOLLOW | libc::O_NONBLOCK,
    );
    let file = options.open(&path).map_err(|e| {
        if std::fs::symlink_metadata(&path).is_ok_and(|m| m.file_type().is_symlink()) {
            refuse(&path)
        } else {
            e
        }
    })?;
    if !file.metadata()?.is_file() {
        return Err(refuse(&path));
    }
    Ok(file)
}

#[cfg(all(test, unix))]
mod state_file_tests {
    use super::*;

    #[test]
    fn a_planted_symlink_is_refused_and_its_target_left_alone() {
        let dir = tempfile::tempdir().unwrap();
        let outside = dir.path().join("outside");
        std::fs::write(&outside, "keep").unwrap();
        let state = dir.path().join("state");
        std::fs::create_dir(&state).unwrap();
        std::os::unix::fs::symlink(&outside, state.join("session.lock")).unwrap();
        assert!(open_state_file(&state, "session.lock", true).is_err());
        assert_eq!(std::fs::read_to_string(&outside).unwrap(), "keep");
        // A state directory that is itself a symlink, as a clone can commit `.dreamference`.
        let linked = dir.path().join("linked");
        std::os::unix::fs::symlink(&state, &linked).unwrap();
        assert!(open_state_file(&linked, "code_index.building", true).is_err());
        assert!(!state.join("code_index.building").exists());
    }

    #[test]
    fn an_existing_file_is_opened_without_truncating_it() {
        let dir = tempfile::tempdir().unwrap();
        let state = dir.path().join("state");
        assert!(open_state_file(&state, "session.lock", true).is_ok());
        std::fs::write(state.join("session.lock"), "kept").unwrap();
        assert!(open_state_file(&state, "session.lock", true).is_ok());
        assert_eq!(
            std::fs::read_to_string(state.join("session.lock")).unwrap(),
            "kept"
        );
    }
}

#[cfg(test)]
mod hint_tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn process_alive_is_false_for_pids_that_are_not_one_process() {
        assert!(process_alive(std::process::id() as i32));
        // kill(0) signals the caller's process group and kill(-1) every process: neither names a process.
        assert!(!process_alive(0));
        assert!(!process_alive(-1));
    }

    #[test]
    fn where_indexing_runs_the_hints_name_the_command() {
        assert_eq!(
            index_command(true, "ling-code index --exact"),
            "`ling-code index --exact`"
        );
        assert_eq!(
            run_index_command(true, "ling-code index"),
            "run `ling-code index`"
        );
        assert_eq!(
            no_index_yet(true, true),
            "no code index for this repository yet; run `ling-code index`, and use rg meanwhile"
        );
        assert_eq!(
            no_index_yet(true, false),
            "no code index for this repository yet; run `ling-code index`"
        );
    }

    #[test]
    fn elsewhere_they_say_where_the_index_is_built_and_name_no_command() {
        // A Windows client has neither `ling-code index` nor `ling-admin code setup`, nor rg.
        for hint in [
            index_command(false, "ling-code index --exact"),
            run_index_command(false, "ling-code index"),
            no_index_yet(false, true),
            no_index_yet(false, false),
        ] {
            assert!(hint.contains("built on Linux nodes only for now"), "{hint}");
            assert!(!hint.contains('`') && !hint.contains(" rg "), "{hint}");
        }
        assert_eq!(
            no_index_yet(false, true),
            "no code index for this repository yet; the index is built on Linux nodes only for now; search the files meanwhile"
        );
    }

    #[test]
    fn the_hints_follow_whether_this_system_indexes() {
        assert_eq!(indexing_here(), indexing_supported().is_ok());
        assert_eq!(indexing_here(), cfg!(target_os = "linux"));
    }
}

/// The user's home directory: `HOME`, or `USERPROFILE` on Windows, where `HOME` is usually unset.
pub fn home() -> PathBuf {
    home_from(std::env::var_os("HOME"), std::env::var_os("USERPROFILE"))
}

/// [`home`] on given values: the first that is set and not empty, else `/`.
pub fn home_from(
    home: Option<std::ffi::OsString>,
    userprofile: Option<std::ffi::OsString>,
) -> PathBuf {
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
        assert_eq!(
            home_from(None, Some(OsString::from(r"C:\Users\Jane Doe"))),
            PathBuf::from(r"C:\Users\Jane Doe")
        );
        assert_eq!(
            home_from(
                Some(OsString::from("/home/u")),
                Some(OsString::from(r"C:\x"))
            ),
            PathBuf::from("/home/u")
        );
        assert_eq!(home_from(Some(OsString::new()), None), PathBuf::from("/"));
    }
}

/// Where Mightling installs its binaries and the pinned tools (spec §5).
pub fn install_bin_dir() -> PathBuf {
    match std::env::var("MIGHTLING_CODE_TOOLS_DIR") {
        Ok(dir) => PathBuf::from(dir),
        Err(_) => home().join(".local/share/dreamference/mightling/bin"),
    }
}

/// Where the npm-installed indexers live (spec §5).
pub fn indexers_dir() -> PathBuf {
    match std::env::var("MIGHTLING_CODE_INDEXERS_DIR") {
        Ok(dir) => PathBuf::from(dir),
        Err(_) => home().join(".local/share/dreamference/mightling/indexers"),
    }
}

/// `$CODEX_HOME`, resolved as `ling-rs/src/home.rs` does: `~/.mightling` unless set.
pub fn codex_home() -> PathBuf {
    match std::env::var_os("CODEX_HOME") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => home().join(".mightling"),
    }
}

/// The per-user runtime directory, for the host-wide admission lock (spec §6.4).
pub fn runtime_dir() -> PathBuf {
    match std::env::var_os("XDG_RUNTIME_DIR") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => std::env::temp_dir(),
    }
}
