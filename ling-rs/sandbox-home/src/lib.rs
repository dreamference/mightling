//! The home folder, hidden from the commands `ling` runs
//! (specs/DREAMFERENCE_MIGHTLING_SANDBOX_HOME.md; security review 2026-10, finding 13).
//!
//! Codex's read-only and workspace-write sandboxes on Linux mount the whole filesystem read-only
//! (`:root = read`) and the workspace read-write, so a sandboxed command could read everything the
//! user can: SSH keys, browser profiles, `~/.mightling`'s sessions and secrets, other projects.
//! [`hide_home`] rewrites such a profile, once per command, in Codex's sandbox helper (patch 0019):
//!
//! - `:root = read` becomes one read entry per directory beside the home folder and beside each
//!   of its ancestors (`/usr`, `/etc`, `/opt`, `/tmp`, other users' homes, …), so everything outside
//!   the home folder stays as readable as before and the home folder itself is not mounted;
//! - into it come, read-only, the paths a command needs ([`Plan`]): the toolchains, ling's own tools
//!   and their indexes, the repository the session works in, the folders on `PATH`, and what the
//!   user lists under `[sandbox] readable` in the user-level configuration file;
//! - known credential files inside those paths are hidden again (`deny`);
//! - the workspace and every other writable root are mounted by Codex as before.
//!
//! Codex's own deny-read rules cannot say this: its bubblewrap builder masks a denied folder and
//! reopens only *writable* paths under it, so "deny `~`, read `~/.cargo`" hides `~/.cargo`. A policy
//! that does not read `/` is built from `--tmpfs /` plus one `--ro-bind` per readable root instead,
//! which is what this produces. Full Access has no sandbox and is never touched; neither is a
//! profile that does not read `/` (it is already narrower) or one that writes it.

use std::collections::BTreeSet;
use std::ffi::OsString;
use std::fs;
use std::path::Component;
use std::path::Path;
use std::path::PathBuf;

use codex_protocol::models::ManagedFileSystemPermissions;
use codex_protocol::models::PermissionProfile;
use codex_protocol::permissions::FileSystemAccessMode;
use codex_protocol::permissions::FileSystemPath;
use codex_protocol::permissions::FileSystemSandboxEntry;
use codex_protocol::permissions::FileSystemSpecialPath;
use codex_utils_absolute_path::AbsolutePathBuf;
use codex_utils_path_uri::PathUri;

/// The user-level file's table this rule reads. A table, not flat keys: `ling-admin` rewrites the
/// file's flat keys from its own settings and keeps tables as they are (`[night]`, `[node]`).
pub const SECTION: &str = "sandbox";

/// The key in [`SECTION`] that switches the rule off: `home = "visible"`.
pub const MODE_KEY: &str = "home";

/// The key in [`SECTION`] that adds read-only paths: `readable = ["~/data", "/srv/x"]`.
pub const READABLE_KEY: &str = "readable";

/// Paths under the home folder that commands may read by default: toolchains, package caches and
/// git's own configuration. A path that does not exist costs nothing (the sandbox skips it).
pub const DEFAULT_READABLE: &[&str] = &[
    // Rust
    ".cargo",
    ".rustup",
    // Python
    ".local/bin",
    ".local/lib",
    ".local/share/uv",
    ".pyenv",
    ".virtualenvs",
    ".conda",
    "miniconda3",
    "miniforge3",
    "anaconda3",
    ".cache/pip",
    ".cache/uv",
    ".cache/pypoetry",
    // JavaScript
    ".nvm",
    ".npm",
    ".bun",
    ".deno",
    ".volta",
    ".local/share/pnpm",
    ".cache/yarn",
    // Go, JVM and the rest
    "go",
    ".cache/go-build",
    ".gradle",
    ".m2",
    ".sdkman",
    ".rbenv",
    ".ghcup",
    ".opam",
    ".elan",
    ".juliaup",
    ".local/share/mise",
    // git: the identity commits are made with
    ".gitconfig",
    ".config/git",
    // Mightling's own tools, their indexers and the code index's stores (`ling-code` queries run
    // inside the sandbox), and the user-level configuration `ling-search`, `ling-fetch` and
    // `ling-code` read. Not the rest of `~/.config/dreamference`: Gmail, pairing and signing keys.
    ".local/share/dreamference/mightling",
    ".config/dreamference/config.toml",
    ".cache/codebase-memory-mcp",
];

/// Paths under `$CODEX_HOME` that commands may read: the skills the model is told to open, the
/// shell snapshot every command sources, the folder Codex puts on `PATH` for `apply_patch`, the
/// air-gap level files `ling-search` and `ling-fetch` consult, and the code index's submodule choices.
/// Never the sessions, the history, the logs, the databases or `ling web`'s device keys.
pub const CODEX_HOME_READABLE: &[&str] = &["skills", "plugins", "shell_snapshots", "tmp", "airgapped", "ling-code.toml"];

/// Credential files inside the readable paths, hidden again.
pub const DEFAULT_HIDDEN: &[&str] = &[
    ".cargo/credentials",
    ".cargo/credentials.toml",
    ".config/git/credentials",
    ".gradle/gradle.properties",
    ".m2/settings.xml",
    ".m2/settings-security.xml",
    ".local/share/uv/credentials",
    ".cache/huggingface/token",
    ".cache/huggingface/stored_tokens",
];

/// Environment variables that name a toolchain's folder.
pub const TOOLCHAIN_VARS: &[&str] = &[
    "CARGO_HOME",
    "RUSTUP_HOME",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYENV_ROOT",
    "NVM_DIR",
    "PNPM_HOME",
    "BUN_INSTALL",
    "VOLTA_HOME",
    "DENO_DIR",
    "GOPATH",
    "GOMODCACHE",
    "SDKMAN_DIR",
];

/// Folders whose contents are too varied to open because one program on `PATH` lives in them: a
/// link into one of these opens the program's own file only.
const SHARED_FOLDERS: &[&str] = &["", ".local", ".local/bin", ".local/share", ".local/lib", ".config", ".cache", "bin"];

/// At most this many entries of one `PATH` folder are looked at for links and scripts.
const MAX_PATH_ENTRIES: usize = 2048;

/// What a command's sandbox is built from, gathered from the helper's process ([`Inputs::from_process`])
/// or given by a test.
#[derive(Debug, Clone, Default)]
pub struct Inputs {
    /// The user's home folder.
    pub home: PathBuf,
    /// `$CODEX_HOME`.
    pub codex_home: Option<PathBuf>,
    /// The sandbox's working directory (`--sandbox-policy-cwd`).
    pub cwd: Option<PathBuf>,
    /// The folders on the command's `PATH`.
    pub path_dirs: Vec<PathBuf>,
    /// The folders [`TOOLCHAIN_VARS`] name.
    pub toolchain_dirs: Vec<PathBuf>,
    /// The sandbox helper's own executable, which re-enters itself inside the sandbox.
    pub current_exe: Option<PathBuf>,
    /// The user-level configuration file's text (`~/.config/dreamference/config.toml`), never a
    /// repository's: a sandboxed command can write the repository's file.
    pub user_config: Option<String>,
    /// Where the walk to the home folder starts: `/`, or a scratch folder in tests.
    pub top: PathBuf,
}

/// The home folder's part of a command's sandbox.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Plan {
    /// The home folder, as a real path.
    pub home: PathBuf,
    /// Everything beside the home folder and beside its ancestors: readable as before.
    pub outside: Vec<PathBuf>,
    /// The paths inside the home folder a command may read.
    pub readable: Vec<PathBuf>,
    /// Credential files inside those paths, hidden again.
    pub hidden: Vec<PathBuf>,
}

/// The one-line hook in Codex's sandbox helper: hides the home folder from the command about to
/// run, unless the profile is Full Access, does not read `/`, writes it, or the user switched the
/// rule off. Running it on a profile it has already rewritten changes nothing, which matters
/// because the helper runs twice per command (outside bubblewrap, then inside it).
pub fn hide_home(profile: &mut PermissionProfile) {
    let PermissionProfile::Managed { file_system: ManagedFileSystemPermissions::Restricted { entries, .. }, .. } = profile
    else {
        return;
    };
    if !reads_whole_disk(entries) {
        return;
    }
    let Some(inputs) = Inputs::from_process() else { return };
    if let Some(plan) = plan(&inputs) {
        apply(entries, &plan);
    }
}

/// Whether `entries` read `/` and do not write it: the read-only and workspace-write sandboxes.
pub fn reads_whole_disk(entries: &[FileSystemSandboxEntry]) -> bool {
    let is_root = |entry: &FileSystemSandboxEntry| {
        matches!(&entry.path, FileSystemPath::Special { value: FileSystemSpecialPath::Root })
    };
    entries.iter().any(|entry| is_root(entry) && entry.access == FileSystemAccessMode::Read)
        && !entries.iter().any(|entry| is_root(entry) && entry.access == FileSystemAccessMode::Write)
}

/// Rewrites `entries` by `plan`: `:root = read` is replaced by the plan's outside and readable
/// paths, the hidden ones are denied, and the working directory is made readable when nothing
/// names it (the read-only sandbox). Every other entry, the writable roots among them, is kept.
pub fn apply(entries: &mut Vec<FileSystemSandboxEntry>, plan: &Plan) {
    entries.retain(|entry| {
        !(matches!(&entry.path, FileSystemPath::Special { value: FileSystemSpecialPath::Root })
            && entry.access == FileSystemAccessMode::Read)
    });
    let names_workspace = entries.iter().any(|entry| {
        matches!(&entry.path, FileSystemPath::Special { value: FileSystemSpecialPath::ProjectRoots { subpath: None } })
    });
    if !names_workspace {
        entries.push(FileSystemSandboxEntry::new(
            FileSystemPath::Special { value: FileSystemSpecialPath::project_roots(None) },
            FileSystemAccessMode::Read,
        ));
    }
    for path in plan.outside.iter().chain(&plan.readable) {
        if let Some(entry) = entry(path, FileSystemAccessMode::Read) {
            entries.push(entry);
        }
    }
    for path in &plan.hidden {
        if let Some(entry) = entry(path, FileSystemAccessMode::Deny) {
            entries.push(entry);
        }
    }
}

fn entry(path: &Path, access: FileSystemAccessMode) -> Option<FileSystemSandboxEntry> {
    let path = AbsolutePathBuf::from_absolute_path(path).ok()?;
    Some(FileSystemSandboxEntry::new(FileSystemPath::Path { path: PathUri::from_abs_path(&path) }, access))
}

impl Inputs {
    /// The inputs of the command about to run, from the helper's own environment and arguments.
    /// `None` without a home folder: there is then nothing to hide.
    pub fn from_process() -> Option<Inputs> {
        let home = ling_airgapped::home_dir().filter(|home| home.is_absolute())?;
        let args: Vec<OsString> = std::env::args_os().collect();
        let cwd = sandbox_policy_cwd(&args).or_else(|| std::env::current_dir().ok());
        let path_dirs = std::env::var_os("PATH")
            .map(|path| std::env::split_paths(&path).filter(|dir| dir.is_absolute()).collect())
            .unwrap_or_default();
        let toolchain_dirs = TOOLCHAIN_VARS
            .iter()
            .filter_map(|var| std::env::var_os(var))
            .filter(|value| !value.is_empty())
            .flat_map(|value| std::env::split_paths(&value).collect::<Vec<_>>())
            .filter(|dir| dir.is_absolute())
            .collect();
        let user_config = ling_airgapped::user_config_file().and_then(|file| fs::read_to_string(file).ok());
        Some(Inputs {
            home,
            codex_home: ling_airgapped::codex_home(),
            cwd,
            path_dirs,
            toolchain_dirs,
            current_exe: std::env::current_exe().ok(),
            user_config,
            top: PathBuf::from("/"),
        })
    }
}

/// The value of the helper's `--sandbox-policy-cwd`, in either of clap's spellings.
pub fn sandbox_policy_cwd(args: &[OsString]) -> Option<PathBuf> {
    const FLAG: &str = "--sandbox-policy-cwd";
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        let text = arg.to_string_lossy();
        if text == FLAG {
            return args.next().map(PathBuf::from);
        }
        if let Some(value) = text.strip_prefix("--sandbox-policy-cwd=") {
            return Some(PathBuf::from(value));
        }
        if text == "--" {
            break;
        }
    }
    None
}

/// What the user set in the user-level file: whether the rule is on, and the paths they added.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct UserSettings {
    pub visible: bool,
    pub readable: Vec<String>,
}

/// Reads [`MODE_KEY`] and [`READABLE_KEY`] from the user-level file's text. A file that does not
/// parse leaves the rule on with no additions: a typo must not open the home folder.
pub fn user_settings(text: Option<&str>) -> UserSettings {
    let Some(file) = text.and_then(|text| text.parse::<toml::Table>().ok()) else {
        return UserSettings::default();
    };
    let Some(table) = file.get(SECTION).and_then(toml::Value::as_table) else {
        return UserSettings::default();
    };
    let visible = table
        .get(MODE_KEY)
        .and_then(toml::Value::as_str)
        .is_some_and(|mode| mode.trim().eq_ignore_ascii_case("visible"));
    let readable = table
        .get(READABLE_KEY)
        .and_then(toml::Value::as_array)
        .map(|paths| paths.iter().filter_map(toml::Value::as_str).map(str::to_string).collect())
        .unwrap_or_default();
    UserSettings { visible, readable }
}

/// The home folder's part of the sandbox for `inputs`, or `None` when the user switched the rule
/// off or the home folder does not exist.
pub fn plan(inputs: &Inputs) -> Option<Plan> {
    let settings = user_settings(inputs.user_config.as_deref());
    if settings.visible {
        return None;
    }
    let home = fs::canonicalize(&inputs.home).ok()?;
    let top = fs::canonicalize(&inputs.top).ok()?;
    if home == top || !home.starts_with(&top) {
        return None;
    }
    let mut readable = Readable::new(&home);

    for relative in DEFAULT_READABLE {
        readable.add(&home.join(relative));
    }
    if let Some(codex_home) = &inputs.codex_home {
        for relative in CODEX_HOME_READABLE {
            readable.add(&codex_home.join(relative));
        }
        readable.add_skill_links(&codex_home.join("skills"));
    }
    if let Some(exe) = &inputs.current_exe
        && let Ok(exe) = fs::canonicalize(exe)
        && let Some(dir) = exe.parent()
    {
        readable.add(dir);
    }
    for dir in &inputs.toolchain_dirs {
        readable.add(dir);
    }
    for dir in &inputs.path_dirs {
        readable.add_path_dir(dir);
    }
    if let Some(cwd) = &inputs.cwd {
        readable.add_repository(cwd);
    }
    for path in &settings.readable {
        if let Some(path) = expand(path, &inputs.home) {
            readable.add_chosen(&path);
        }
    }

    let hidden = DEFAULT_HIDDEN
        .iter()
        .map(|relative| home.join(relative))
        .filter(|path| fs::symlink_metadata(path).is_ok())
        .collect();
    Some(Plan { outside: outside(&home, &top), readable: readable.paths.into_iter().collect(), hidden, home })
}

/// `~/x` against `home`, an absolute path as it is; anything else is ignored.
fn expand(path: &str, home: &Path) -> Option<PathBuf> {
    let path = path.trim();
    if path == "~" {
        return Some(home.to_path_buf());
    }
    if let Some(rest) = path.strip_prefix("~/") {
        return Some(home.join(rest));
    }
    let path = PathBuf::from(path);
    path.is_absolute().then_some(path)
}

/// Everything beside `home` and beside each of its ancestors below `top`: the folders a command
/// reads as before. Skipped: the next folder on the way down, anything that resolves to the home
/// folder, into it or above it (a link such as `/home -> /data/home`), and `/dev`, which the
/// sandbox mounts itself.
pub fn outside(home: &Path, top: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let Ok(below) = home.strip_prefix(top) else { return found };
    let mut dir = top.to_path_buf();
    for part in below.components() {
        let Component::Normal(part) = part else { continue };
        let next = dir.join(part);
        let Ok(children) = fs::read_dir(&dir) else { break };
        let mut children: Vec<PathBuf> = children.filter_map(|child| child.ok().map(|child| child.path())).collect();
        children.sort();
        for child in children {
            if child == next || child == Path::new("/dev") {
                continue;
            }
            if let Ok(real) = fs::canonicalize(&child)
                && (home.starts_with(&real) || real.starts_with(home))
            {
                continue;
            }
            found.push(child);
        }
        dir = next;
    }
    found
}

/// The readable paths under the home folder, as they are collected.
struct Readable<'a> {
    home: &'a Path,
    paths: BTreeSet<PathBuf>,
}

impl<'a> Readable<'a> {
    fn new(home: &'a Path) -> Self {
        Readable { home, paths: BTreeSet::new() }
    }

    /// The real path of `path` if it exists and lies strictly inside the home folder.
    fn inside(&self, path: &Path) -> Option<PathBuf> {
        let real = fs::canonicalize(path).ok()?;
        (real != self.home && real.starts_with(self.home)).then_some(real)
    }

    /// Adds `path` (and its real path, when a link leads elsewhere in the home folder) if it lies
    /// inside the home folder. Never the home folder itself or anything above it.
    fn add(&mut self, path: &Path) {
        let Some(real) = self.inside(path) else { return };
        if path.is_absolute() && path != real && path.starts_with(self.home) && path != self.home {
            self.paths.insert(path.to_path_buf());
        }
        self.paths.insert(real);
    }

    /// A path the user listed: added even when it is the home folder (that is their choice).
    fn add_chosen(&mut self, path: &Path) {
        match fs::canonicalize(path) {
            Ok(real) if real == self.home => {
                self.paths.insert(real);
            }
            _ => self.add(path),
        }
    }

    fn covered(&self, path: &Path) -> bool {
        self.paths.iter().any(|visible| path.starts_with(visible))
    }

    /// Skills from other agents are links (`skills/from-<agent>/<name>`) into those agents' folders.
    fn add_skill_links(&mut self, skills: &Path) {
        let Ok(groups) = fs::read_dir(skills) else { return };
        for group in groups.flatten() {
            let group = group.path();
            if group.is_symlink() {
                self.add(&group);
                continue;
            }
            let is_link_group = group.file_name().is_some_and(|name| name.to_string_lossy().starts_with("from-"));
            if !is_link_group {
                continue;
            }
            let Ok(links) = fs::read_dir(&group) else { continue };
            for link in links.flatten().take(MAX_PATH_ENTRIES) {
                let link = link.path();
                if link.is_symlink() {
                    self.add(&link);
                }
            }
        }
    }

    /// A folder on `PATH`: the folder itself, the virtualenv it belongs to, and for each program in
    /// it that is a link or a script, the folder the program really lives in.
    fn add_path_dir(&mut self, dir: &Path) {
        let Some(real) = self.inside(dir) else { return };
        if !real.is_dir() {
            return;
        }
        self.add(dir);
        if let Some(venv) = venv_root(&real, self.home) {
            self.add(&venv);
        }
        let Ok(programs) = fs::read_dir(&real) else { return };
        for program in programs.flatten().take(MAX_PATH_ENTRIES) {
            let program = program.path();
            let target = if program.is_symlink() { fs::canonicalize(&program).ok() } else { interpreter(&program) };
            if let Some(target) = target
                && self.inside(&target).is_some()
                && !self.covered(&target)
                && let Some(root) = install_root(&target, self.home)
            {
                self.add(&root);
            }
        }
    }

    /// The repository the session works in: the working directory's enclosing repository (a session
    /// started in a subfolder still sees its repository), and for a git worktree, the main
    /// repository's git folder, which holds its objects and refs, not the main checkout's files.
    fn add_repository(&mut self, cwd: &Path) {
        let Some(cwd) = self.inside(cwd) else { return };
        for dir in cwd.ancestors() {
            if dir == self.home || !dir.starts_with(self.home) {
                return;
            }
            let dot_git = dir.join(".git");
            if dot_git.is_dir() {
                self.add(dir);
                return;
            }
            if dot_git.is_file() {
                self.add(dir);
                if let Some(common) = git_common_dir(&dot_git) {
                    self.add(&common);
                }
                return;
            }
        }
    }
}

/// The git folder shared by a worktree whose `.git` file is `dot_git`: its `gitdir:` line, then that
/// folder's `commondir` file (relative to it), or the gitdir itself when there is none.
pub fn git_common_dir(dot_git: &Path) -> Option<PathBuf> {
    let text = fs::read_to_string(dot_git).ok()?;
    let gitdir = text.lines().find_map(|line| line.strip_prefix("gitdir:"))?.trim();
    let base = dot_git.parent()?;
    let gitdir = base.join(gitdir);
    let common = match fs::read_to_string(gitdir.join("commondir")) {
        Ok(common) => gitdir.join(common.trim()),
        Err(_) => gitdir,
    };
    fs::canonicalize(common).ok()
}

/// The virtualenv `path` lies in: the nearest folder holding `pyvenv.cfg`, looking no higher than
/// the home folder and no higher than a folder holding `.git` (that is a checkout, not a virtualenv).
fn venv_root(path: &Path, home: &Path) -> Option<PathBuf> {
    for dir in path.ancestors() {
        if dir == home || !dir.starts_with(home) {
            return None;
        }
        if dir.join("pyvenv.cfg").is_file() {
            return Some(dir.to_path_buf());
        }
        if dir.join(".git").exists() {
            return None;
        }
    }
    None
}

/// The folder to open for a program that really lives at `target` (a real path inside the home
/// folder): its virtualenv, its Node.js version folder under `.nvm`, else the folder holding it
/// unless that folder is a shared one (`~/.local/share`, …) or a checkout, then the file alone.
pub fn install_root(target: &Path, home: &Path) -> Option<PathBuf> {
    if let Some(venv) = venv_root(target, home) {
        return Some(venv);
    }
    let relative = target.strip_prefix(home).ok()?;
    let parts: Vec<_> = relative.components().collect();
    if parts.len() > 4 && parts[0].as_os_str() == ".nvm" && parts[1].as_os_str() == "versions" {
        return Some(home.join(parts[..4].iter().collect::<PathBuf>()));
    }
    let parent = target.parent()?;
    let shared = SHARED_FOLDERS.iter().any(|folder| parent == home.join(folder));
    if shared || parent == home || parent.join(".git").exists() {
        return Some(target.to_path_buf());
    }
    Some(parent.to_path_buf())
}

/// The interpreter a small script names in its `#!` line, if it is an absolute path.
fn interpreter(path: &Path) -> Option<PathBuf> {
    let metadata = fs::metadata(path).ok()?;
    if !metadata.is_file() || metadata.len() > 4096 {
        return None;
    }
    let text = fs::read(path).ok()?;
    let line = text.split(|byte| *byte == b'\n').next()?;
    let line = std::str::from_utf8(line).ok()?.strip_prefix("#!")?;
    let interpreter = PathBuf::from(line.split_whitespace().next()?);
    if !interpreter.is_absolute() {
        return None;
    }
    fs::canonicalize(interpreter).ok()
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use codex_protocol::permissions::FileSystemSandboxPolicy;
    use codex_protocol::permissions::NetworkSandboxPolicy;
    use std::os::unix::fs::symlink;
    use tempfile::TempDir;

    /// A scratch filesystem: `<root>/usr`, `<root>/etc`, `<root>/home/u` (the user) and
    /// `<root>/home/other`, with the user's secrets and toolchains in place.
    struct Machine {
        _dir: TempDir,
        top: PathBuf,
        home: PathBuf,
    }

    impl Machine {
        fn new() -> Machine {
            let dir = TempDir::new().unwrap();
            let top = fs::canonicalize(dir.path()).unwrap();
            let home = top.join("home/u");
            for folder in [
                "usr/bin",
                "etc",
                "opt/tool",
                "home/other",
                "home/u/.ssh",
                "home/u/.mozilla/firefox",
                "home/u/.config/dreamference/gmail",
                "home/u/.cargo/bin",
                "home/u/.rustup",
                "home/u/.local/bin",
                "home/u/.local/share/dreamference/mightling/bin",
                "home/u/.local/share/keyrings",
                "home/u/.mightling/sessions",
                "home/u/.mightling/skills/pdf",
                "home/u/.mightling/skills/from-claude",
                "home/u/.mightling/shell_snapshots",
                "home/u/.mightling/tmp/arg0",
                "home/u/.mightling/airgapped",
                "home/u/.claude/skills/review",
                "home/u/projects/app/.git",
                "home/u/projects/app/src",
                "home/u/projects/other/.git",
            ] {
                fs::create_dir_all(top.join(folder)).unwrap();
            }
            for (file, text) in [
                ("home/u/.ssh/id_ed25519", "key"),
                ("home/u/.cargo/credentials.toml", "token"),
                ("home/u/.config/dreamference/config.toml", "vllm_host = \"x\"\n"),
                ("home/u/.config/dreamference/gmail/service-secret", "s"),
                ("home/u/.mightling/history.jsonl", "{}"),
                ("home/u/.gitconfig", "[user]\n"),
            ] {
                fs::write(top.join(file), text).unwrap();
            }
            symlink(top.join("home/u/.claude/skills/review"), top.join("home/u/.mightling/skills/from-claude/review")).unwrap();
            Machine { _dir: dir, top, home }
        }

        fn inputs(&self) -> Inputs {
            Inputs {
                home: self.home.clone(),
                codex_home: Some(self.home.join(".mightling")),
                cwd: Some(self.home.join("projects/app")),
                path_dirs: vec![self.top.join("usr/bin"), self.home.join(".cargo/bin")],
                toolchain_dirs: Vec::new(),
                current_exe: None,
                user_config: None,
                top: self.top.clone(),
            }
        }

        fn at(&self, relative: &str) -> PathBuf {
            self.top.join(relative)
        }
    }

    fn visible(plan: &Plan, path: &Path) -> bool {
        plan.outside.iter().chain(&plan.readable).any(|root| path.starts_with(root))
    }

    #[test]
    fn everything_outside_the_home_folder_stays_readable_and_the_home_folder_does_not() {
        let machine = Machine::new();
        let plan = plan(&machine.inputs()).unwrap();
        for path in ["usr/bin", "etc", "opt/tool", "home/other"] {
            assert!(visible(&plan, &machine.at(path)), "{path}: {plan:?}");
        }
        assert!(!plan.outside.iter().any(|root| root == &machine.at("home") || machine.home.starts_with(root)));
    }

    #[test]
    fn secrets_browser_profiles_ling_s_own_state_and_other_projects_are_hidden() {
        let machine = Machine::new();
        let plan = plan(&machine.inputs()).unwrap();
        for path in [
            "home/u/.ssh/id_ed25519",
            "home/u/.mozilla/firefox",
            "home/u/.config/dreamference/gmail/service-secret",
            "home/u/.local/share/keyrings",
            "home/u/.mightling/sessions",
            "home/u/.mightling/history.jsonl",
            "home/u/projects/other",
        ] {
            assert!(!visible(&plan, &machine.at(path)), "{path} is visible: {plan:?}");
        }
    }

    #[test]
    fn toolchains_ling_s_tools_skills_and_the_workspace_stay_readable() {
        let machine = Machine::new();
        let plan = plan(&machine.inputs()).unwrap();
        for path in [
            "home/u/.cargo/bin",
            "home/u/.rustup",
            "home/u/.local/bin",
            "home/u/.local/share/dreamference/mightling/bin",
            "home/u/.config/dreamference/config.toml",
            "home/u/.gitconfig",
            "home/u/.mightling/skills/pdf",
            "home/u/.mightling/shell_snapshots",
            "home/u/.mightling/tmp/arg0",
            "home/u/.mightling/airgapped",
            "home/u/.claude/skills/review",
            "home/u/projects/app/src",
        ] {
            assert!(visible(&plan, &machine.at(path)), "{path} is hidden: {plan:?}");
        }
        assert!(!visible(&plan, &machine.at("home/u/.claude")), "only the linked skill, not the agent's folder");
    }

    #[test]
    fn credential_files_inside_a_readable_folder_are_hidden_again() {
        let machine = Machine::new();
        let plan = plan(&machine.inputs()).unwrap();
        assert_eq!(plan.hidden, vec![machine.at("home/u/.cargo/credentials.toml")]);
    }

    #[test]
    fn a_session_in_a_subfolder_still_sees_its_whole_repository() {
        let machine = Machine::new();
        let mut inputs = machine.inputs();
        inputs.cwd = Some(machine.at("home/u/projects/app/src"));
        let plan = plan(&inputs).unwrap();
        assert!(plan.readable.contains(&machine.at("home/u/projects/app")));
        assert!(!visible(&plan, &machine.at("home/u/projects/other")));
    }

    #[test]
    fn a_worktree_sees_the_main_repository_s_git_folder_but_not_its_checkout() {
        let machine = Machine::new();
        let gitdir = machine.at("home/u/projects/other/.git/worktrees/w1");
        fs::create_dir_all(&gitdir).unwrap();
        fs::write(gitdir.join("commondir"), "../..\n").unwrap();
        fs::create_dir_all(machine.at("home/u/.mightling/night/w1")).unwrap();
        fs::write(machine.at("home/u/.mightling/night/w1/.git"), format!("gitdir: {}\n", gitdir.display())).unwrap();
        fs::write(machine.at("home/u/projects/other/README"), "x").unwrap();
        let mut inputs = machine.inputs();
        inputs.cwd = Some(machine.at("home/u/.mightling/night/w1"));
        let plan = plan(&inputs).unwrap();
        assert!(visible(&plan, &machine.at("home/u/projects/other/.git/objects")));
        assert!(!visible(&plan, &machine.at("home/u/projects/other/README")));
    }

    #[test]
    fn a_program_on_path_brings_the_folder_it_really_lives_in() {
        let machine = Machine::new();
        let venv = machine.at("home/u/projects/other/.venv");
        fs::create_dir_all(venv.join("bin")).unwrap();
        fs::write(venv.join("pyvenv.cfg"), "home = /usr/bin\n").unwrap();
        fs::write(venv.join("bin/tool"), "#!/bin/sh\n").unwrap();
        symlink(venv.join("bin/tool"), machine.at("home/u/.local/bin/tool")).unwrap();
        let node = machine.at("home/u/.nvm/versions/node/v24/bin");
        fs::create_dir_all(&node).unwrap();
        fs::write(node.join("node"), "").unwrap();
        fs::write(machine.at("home/u/.local/bin/script"), format!("#!{} -x\n", node.join("node").display())).unwrap();
        let single = machine.at("home/u/.local/share/thing/v1");
        fs::create_dir_all(&single).unwrap();
        fs::write(single.join("thing"), "").unwrap();
        symlink(single.join("thing"), machine.at("home/u/.local/bin/thing")).unwrap();
        let mut inputs = machine.inputs();
        inputs.path_dirs.push(machine.at("home/u/.local/bin"));
        let plan = plan(&inputs).unwrap();
        assert!(plan.readable.contains(&venv), "{plan:?}");
        assert!(visible(&plan, &node.join("node")));
        assert!(plan.readable.contains(&single));
        assert!(!visible(&plan, &machine.at("home/u/projects/other/README")));
    }

    #[test]
    fn a_path_folder_never_opens_the_home_folder_or_a_shared_parent() {
        let machine = Machine::new();
        let mut inputs = machine.inputs();
        inputs.path_dirs = vec![machine.home.clone(), machine.at("home"), machine.at("home/u/.local/bin")];
        fs::write(machine.at("home/u/.local/share/loose"), "").unwrap();
        symlink(machine.at("home/u/.local/share/loose"), machine.at("home/u/.local/bin/loose")).unwrap();
        let plan = plan(&inputs).unwrap();
        assert!(!plan.readable.iter().any(|path| machine.home.starts_with(path)), "{plan:?}");
        assert!(plan.readable.contains(&machine.at("home/u/.local/share/loose")));
        assert!(!visible(&plan, &machine.at("home/u/.local/share/keyrings")));
    }

    #[test]
    fn the_user_adds_paths_in_the_user_level_file_or_switches_the_rule_off() {
        let machine = Machine::new();
        fs::create_dir_all(machine.at("home/u/datasets")).unwrap();
        let mut inputs = machine.inputs();
        inputs.user_config = Some(format!("[{SECTION}]\n{READABLE_KEY} = [\"~/datasets\", \"relative/ignored\"]\n"));
        let plan = plan(&inputs).unwrap();
        assert!(plan.readable.contains(&machine.at("home/u/datasets")));
        inputs.user_config = Some(format!("[{SECTION}]\n{MODE_KEY} = \"visible\"\n"));
        assert_eq!(super::plan(&inputs), None);
        inputs.user_config = Some(format!("{MODE_KEY} = \"visible\"\n"));
        assert!(super::plan(&inputs).is_some(), "only the [sandbox] table counts");
        inputs.user_config = Some(format!("[{SECTION}]\n{MODE_KEY} = \"visible\"\nnot toml at all ["));
        assert!(super::plan(&inputs).is_some(), "a file that does not parse keeps the rule on");
    }

    #[test]
    fn a_link_beside_the_home_folder_that_leads_into_it_is_not_mounted() {
        let machine = Machine::new();
        symlink(machine.at("home/u/.ssh"), machine.at("keys")).unwrap();
        symlink(machine.at("home"), machine.at("homes")).unwrap();
        symlink(machine.at("home/u"), machine.at("home/me")).unwrap();
        let plan = plan(&machine.inputs()).unwrap();
        assert!(!plan.outside.contains(&machine.at("keys")), "a link into the home folder");
        assert!(!plan.outside.contains(&machine.at("homes")), "a link above it");
        assert!(!plan.outside.contains(&machine.at("home/me")), "a link to it");
        assert!(plan.outside.contains(&machine.at("opt")));
        assert!(plan.outside.contains(&machine.at("home/other")));
    }

    #[test]
    fn the_sandbox_helper_s_own_folder_is_readable() {
        let machine = Machine::new();
        let exe = machine.at("home/u/.local/share/dreamference/mightling/bin/ling");
        fs::write(&exe, "").unwrap();
        let mut inputs = machine.inputs();
        inputs.current_exe = Some(exe.clone());
        inputs.path_dirs.clear();
        let plan = plan(&inputs).unwrap();
        assert!(visible(&plan, &exe));
    }

    fn profile(entries: Vec<FileSystemSandboxEntry>) -> PermissionProfile {
        PermissionProfile::Managed {
            file_system: ManagedFileSystemPermissions::Restricted { entries, glob_scan_max_depth: None },
            network: NetworkSandboxPolicy::Restricted,
        }
    }

    fn entries_of(profile: &PermissionProfile) -> &Vec<FileSystemSandboxEntry> {
        match profile {
            PermissionProfile::Managed { file_system: ManagedFileSystemPermissions::Restricted { entries, .. }, .. } => entries,
            _ => panic!("not restricted"),
        }
    }

    #[test]
    fn workspace_write_keeps_its_writable_workspace_and_loses_only_the_whole_disk_read() {
        let machine = Machine::new();
        let cwd = machine.at("home/u/projects/app");
        // The scratch machine lives under the temporary folder, which workspace-write would make
        // writable (and so readable) as a whole: leave /tmp and $TMPDIR out of this policy.
        let mut entries = FileSystemSandboxPolicy::workspace_write(&[], true, true).entries;
        assert!(reads_whole_disk(&entries));
        let plan = plan(&machine.inputs()).unwrap();
        apply(&mut entries, &plan);
        assert!(!reads_whole_disk(&entries));
        let policy = FileSystemSandboxPolicy::restricted(entries);
        assert!(!policy.has_full_disk_read_access());
        assert!(policy.can_write_local_path_with_cwd(&cwd.join("src/main.rs"), &cwd));
        assert!(policy.can_read_local_path_with_cwd(&machine.at("usr/bin/env"), &cwd));
        assert!(policy.can_read_local_path_with_cwd(&machine.at("home/u/.cargo/bin/cargo"), &cwd));
        assert!(!policy.can_read_local_path_with_cwd(&machine.at("home/u/.ssh/id_ed25519"), &cwd));
        assert!(!policy.can_read_local_path_with_cwd(&machine.at("home/u/.cargo/credentials.toml"), &cwd));
        assert!(policy.get_unreadable_roots_with_cwd(&cwd).iter().any(|root| root.as_path().ends_with(".cargo/credentials.toml")));
        assert!(!policy.get_writable_roots_with_cwd(&cwd).is_empty());
    }

    #[test]
    fn read_only_reads_the_workspace_and_nothing_else_of_the_home_folder() {
        let machine = Machine::new();
        let cwd = machine.at("home/u/projects/app");
        let mut entries = FileSystemSandboxPolicy::read_only().entries;
        apply(&mut entries, &plan(&machine.inputs()).unwrap());
        let policy = FileSystemSandboxPolicy::restricted(entries);
        assert!(policy.can_read_local_path_with_cwd(&cwd.join("src"), &cwd));
        assert!(!policy.can_write_local_path_with_cwd(&cwd.join("src"), &cwd));
        assert!(!policy.can_read_local_path_with_cwd(&machine.at("home/u/projects/other"), &cwd));
        assert!(policy.get_writable_roots_with_cwd(&cwd).is_empty());
    }

    #[test]
    fn full_access_a_narrower_profile_and_a_rewritten_one_are_left_alone() {
        let mut disabled = PermissionProfile::Disabled;
        hide_home(&mut disabled);
        assert_eq!(disabled, PermissionProfile::Disabled);

        let mut external = PermissionProfile::External { network: NetworkSandboxPolicy::Restricted };
        hide_home(&mut external);
        assert_eq!(external, PermissionProfile::External { network: NetworkSandboxPolicy::Restricted });

        let root_write = vec![FileSystemSandboxEntry::new(
            FileSystemPath::Special { value: FileSystemSpecialPath::Root },
            FileSystemAccessMode::Write,
        )];
        let mut writes_everything = profile(root_write.clone());
        hide_home(&mut writes_everything);
        assert_eq!(entries_of(&writes_everything), &root_write);

        let machine = Machine::new();
        let mut entries = FileSystemSandboxPolicy::read_only().entries;
        apply(&mut entries, &plan(&machine.inputs()).unwrap());
        let mut rewritten = profile(entries.clone());
        hide_home(&mut rewritten);
        assert_eq!(entries_of(&rewritten), &entries, "the helper's inner stage must not rewrite it again");
    }

    /// The workspace-write profile the live check in the spec (§8.2) hands the helper.
    const LIVE_CHECK_PROFILE: &str = r#"{"type":"managed","file_system":{"type":"restricted","entries":[{"path":{"type":"special","value":{"kind":"root"}},"access":"read"},{"path":{"type":"special","value":{"kind":"project_roots"}},"access":"write"}]},"network":"restricted"}"#;

    #[test]
    fn the_live_check_s_profile_is_one_the_rule_rewrites() {
        let profile: PermissionProfile = serde_json::from_str(LIVE_CHECK_PROFILE).unwrap();
        assert!(reads_whole_disk(entries_of(&profile)));
    }

    #[test]
    fn the_policy_cwd_is_read_from_the_helper_s_arguments() {
        let args = |list: &[&str]| list.iter().map(OsString::from).collect::<Vec<_>>();
        assert_eq!(
            sandbox_policy_cwd(&args(&["ling", "--sandbox-policy-cwd", "/w", "--", "ls"])),
            Some(PathBuf::from("/w"))
        );
        assert_eq!(sandbox_policy_cwd(&args(&["ling", "--sandbox-policy-cwd=/w"])), Some(PathBuf::from("/w")));
        assert_eq!(sandbox_policy_cwd(&args(&["ling", "--", "--sandbox-policy-cwd", "/x"])), None);
    }
}
