//! Process plumbing shared by the exact tools (`run_test`, `git_log`, `diff_stat`) and `task`
//! (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §4.2, §6.2).
//!
//! Everything that starts a process goes through the [`Runner`] trait, so the unit tests replace
//! `git`, the test runners, `ruff`, `systemctl` and `ling exec` with a script and need no network,
//! no model and no toolchain. [`SystemRunner`] is the real one: a process group per run, a hard
//! timeout that kills the group, an output tail bounded in memory, and, for the heavy runs, the
//! `mightling-batch.slice` scope at `nice 10` so the owner's browser and the model server stay
//! responsive.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

pub use crate::lookup::check_relative;

/// Bytes of a run's output kept (the tail).
pub const TAIL_BYTES: usize = 128 * 1024;
/// The systemd slice for background work on the model host.
pub const BATCH_SLICE: &str = "mightling-batch.slice";

/// One process to run.
#[derive(Debug, Clone)]
pub struct RunSpec {
    pub argv: Vec<String>,
    pub cwd: PathBuf,
    pub env: Vec<(String, String)>,
    pub timeout_s: u64,
    /// Run in `mightling-batch.slice` at `nice 10`.
    pub batch: bool,
}

impl RunSpec {
    pub fn new(argv: &[&str], cwd: &Path, timeout_s: u64) -> Self {
        Self {
            argv: argv.iter().map(|s| s.to_string()).collect(),
            cwd: cwd.to_path_buf(),
            env: Vec::new(),
            timeout_s,
            batch: false,
        }
    }
}

/// What a run left behind.
#[derive(Debug, Clone, Default)]
pub struct RunOutput {
    /// The exit code; `None` when the run was killed.
    pub exit: Option<i32>,
    /// The last [`TAIL_BYTES`] of stdout and stderr, interleaved by arrival.
    pub output: String,
    pub timed_out: bool,
    pub elapsed_s: u64,
}

/// What the tools need from the machine, injectable for tests.
pub trait Runner {
    /// Runs one process to its end or its timeout.
    fn run(&self, spec: &RunSpec) -> Result<RunOutput, String>;
}

/// The real runner.
pub struct SystemRunner;

fn on_path(program: &str) -> bool {
    std::env::var_os("PATH")
        .map(|paths| std::env::split_paths(&paths).any(|d| d.join(program).is_file()))
        .unwrap_or(false)
}

/// True when `systemd-run --user --scope` works here (probed once).
fn scope_available() -> bool {
    static AVAILABLE: OnceLock<bool> = OnceLock::new();
    *AVAILABLE.get_or_init(|| {
        on_path("systemd-run")
            && Command::new("systemd-run")
                .args(["--user", "--scope", "--quiet", &format!("--slice={BATCH_SLICE}"), "true"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
    })
}

fn batch_wrap(argv: Vec<String>) -> Vec<String> {
    let mut wrapped: Vec<String> = Vec::new();
    if scope_available() {
        wrapped.extend(
            ["systemd-run", "--user", "--scope", "--quiet", &format!("--slice={BATCH_SLICE}")]
                .map(String::from),
        );
    }
    if on_path("nice") {
        wrapped.extend(["nice", "-n", "10"].map(String::from));
    }
    wrapped.extend(argv);
    wrapped
}

fn drain(mut source: impl Read + Send + 'static, tail: Arc<Mutex<Vec<u8>>>) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut chunk = [0u8; 8192];
        while let Ok(n) = source.read(&mut chunk) {
            if n == 0 {
                break;
            }
            if let Ok(mut buf) = tail.lock() {
                buf.extend_from_slice(&chunk[..n]);
                if buf.len() > TAIL_BYTES {
                    let excess = buf.len() - TAIL_BYTES;
                    buf.drain(..excess);
                }
            }
        }
    })
}

impl Runner for SystemRunner {
    fn run(&self, spec: &RunSpec) -> Result<RunOutput, String> {
        let argv = if spec.batch { batch_wrap(spec.argv.clone()) } else { spec.argv.clone() };
        let (program, args) = argv.split_first().ok_or("empty command")?;
        let mut cmd = Command::new(program);
        cmd.args(args)
            .current_dir(&spec.cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for (k, v) in &spec.env {
            cmd.env(k, v);
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        let started = Instant::now();
        let mut child = cmd.spawn().map_err(|e| format!("cannot run {program}: {e}"))?;
        let tail = Arc::new(Mutex::new(Vec::new()));
        let readers: Vec<_> = [
            child.stdout.take().map(|s| drain(s, tail.clone())),
            child.stderr.take().map(|s| drain(s, tail.clone())),
        ]
        .into_iter()
        .flatten()
        .collect();
        let deadline = started + Duration::from_secs(spec.timeout_s);
        let (exit, timed_out) = loop {
            match child.try_wait() {
                Ok(Some(status)) => break (status.code(), false),
                Ok(None) if Instant::now() >= deadline => {
                    #[cfg(unix)]
                    {
                        let _ = Command::new("kill")
                            .args(["-KILL", "--", &format!("-{}", child.id())])
                            .status();
                    }
                    let _ = child.kill();
                    let _ = child.wait();
                    break (None, true);
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(20)),
                Err(e) => return Err(format!("{program}: {e}")),
            }
        };
        for reader in readers {
            let _ = reader.join();
        }
        let bytes = tail.lock().map(|b| b.clone()).unwrap_or_default();
        Ok(RunOutput {
            exit,
            output: String::from_utf8_lossy(&bytes).into_owned(),
            timed_out,
            elapsed_s: started.elapsed().as_secs(),
        })
    }
}

/// The last `lines` lines of `text`, at most `max_chars` characters; the flag says it was cut.
pub fn tail_of(text: &str, lines: usize, max_chars: usize) -> (String, bool) {
    let all: Vec<&str> = text.lines().collect();
    let start = all.len().saturating_sub(lines);
    let mut out = all[start..].join("\n");
    let mut cut = start > 0;
    if out.chars().count() > max_chars {
        let skip = out.chars().count() - max_chars;
        out = out.chars().skip(skip).collect();
        cut = true;
    }
    (out, cut)
}

/// Resolves a linked worktree: a folder whose `.git` is a file (a linked worktree), never the
/// main checkout. Returns the canonical path.
pub fn linked_worktree(path: &str) -> Result<PathBuf, String> {
    let root = repo_root(path)?;
    if root.join(".git").is_file() {
        Ok(root)
    } else {
        Err(format!(
            "{} is not a linked git worktree (the main checkout is never used; create one with `git worktree add`)",
            root.display()
        ))
    }
}

/// Resolves a repository or worktree root: a folder with `.git` directly inside it.
pub fn repo_root(path: &str) -> Result<PathBuf, String> {
    if path.trim().is_empty() || path.contains('\0') || path.contains('\n') {
        return Err("`worktree` or `repo` must be a folder path".to_string());
    }
    let root = PathBuf::from(path)
        .canonicalize()
        .map_err(|e| format!("{path} is not readable: {e}"))?;
    if !root.is_dir() || !root.join(".git").exists() {
        return Err(format!("{} is not the root of a git repository or worktree", root.display()));
    }
    Ok(root)
}

/// The main checkout a linked worktree belongs to (its `.git` file names
/// `<main>/.git/worktrees/<name>`).
pub fn main_checkout(worktree: &Path) -> Option<PathBuf> {
    let text = std::fs::read_to_string(worktree.join(".git")).ok()?;
    let mut p = PathBuf::from(text.trim().strip_prefix("gitdir:")?.trim());
    for _ in 0..3 {
        p.pop();
    }
    Some(p)
}

/// The tool `name` for a worktree: the main checkout's `.venv/bin`, else the absolute path on
/// `PATH`. Never the worktree's own `.venv`: the model writes the worktree, so an interpreter
/// there is the model's program.
pub fn venv_tool(worktree: &Path, name: &str) -> String {
    if let Some(main) = main_checkout(worktree) {
        let path = main.join(".venv/bin").join(name);
        if path.is_file() {
            return path.display().to_string();
        }
    }
    // The venv folder names its interpreter `python`; on PATH it is `python3`.
    absolute_program(if name == "python" { "python3" } else { name })
}

/// `program` as an absolute path found on `PATH` (the sandbox's `PATH` is not the caller's), or
/// unchanged when it already has a `/` or is not found.
pub fn absolute_program(program: &str) -> String {
    if program.contains('/') {
        return program.to_string();
    }
    std::env::var_os("PATH")
        .and_then(|paths| std::env::split_paths(&paths).map(|d| d.join(program)).find(|p| p.is_file()))
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| program.to_string())
}

/// The `ling` that provides `ling sandbox` (`DREAMFERENCE_DELEGATE_LING`, default `ling`).
pub fn delegate_ling_bin() -> String {
    std::env::var("DREAMFERENCE_DELEGATE_LING")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| "ling".to_string())
}

/// The sandbox every command that runs worktree code goes through: Mightling's `:workspace`
/// profile with the network off, so the worktree and the temp folder are writable, the rest of
/// the disk is read-only and no connection leaves. The worktree is the model's to write (a
/// `conftest.py`, a `build.rs`, a test), so its tests and gates never run on the host.
fn sandbox_prefix(ling_bin: &str, worktree: &Path) -> Vec<String> {
    vec![
        ling_bin.to_string(),
        "sandbox".into(),
        "-P".into(),
        ":workspace".into(),
        "--sandbox-state-disable-network".into(),
        "-C".into(),
        worktree.display().to_string(),
        "--".into(),
    ]
}

/// `argv` wrapped to run in [`sandbox_prefix`]'s sandbox, starting in `dir` (the worktree or a
/// folder inside it). There is no host fallback: when the sandbox cannot start, the run fails.
pub fn sandboxed(ling_bin: &str, worktree: &Path, dir: &Path, argv: &[String]) -> Vec<String> {
    let mut out = sandbox_prefix(ling_bin, worktree);
    out.extend(["sh", "-c", "cd \"$1\" && shift && exec \"$@\"", "sh"].map(String::from));
    out.push(dir.display().to_string());
    if let Some((program, rest)) = argv.split_first() {
        out.push(absolute_program(program));
        out.extend(rest.iter().cloned());
    }
    out
}

/// The environment of a sandboxed run: offline cargo (its home is read-only and the network is
/// off) and npm's cache in the temp folder.
pub fn sandbox_env() -> Vec<(String, String)> {
    vec![
        ("CI".into(), "1".into()),
        ("CARGO_NET_OFFLINE".into(), "true".into()),
        ("npm_config_cache".into(), std::env::temp_dir().join("ling-delegate-npm").display().to_string()),
        ("npm_config_update_notifier".into(), "false".into()),
    ]
}

/// Checks that the sandbox starts here (`true` inside it). `Err` means nothing may run: a test
/// that cannot be sandboxed is refused, never run on the host. `bwrap` needs the AppArmor
/// profile `ling-admin host setup` installs.
pub fn sandbox_check(runner: &dyn Runner, ling_bin: &str, worktree: &Path) -> Result<(), String> {
    let mut argv = sandbox_prefix(ling_bin, worktree);
    argv.push("true".into());
    let mut spec = RunSpec::new(&[], worktree, 60);
    spec.argv = argv;
    let refused = |why: String| {
        Err(format!(
            "the sandbox does not start here ({why}); tests and gates never run outside it (`ling-admin host setup` installs the AppArmor profile bwrap needs)"
        ))
    };
    match runner.run(&spec) {
        Ok(out) if out.exit == Some(0) && !out.timed_out => Ok(()),
        Ok(out) => refused(tail_of(&out.output, 3, 300).0),
        Err(e) => refused(e),
    }
}

/// Build output a test run leaves in the worktree; a new ignored path elsewhere is the model's.
pub fn is_build_artifact(path: &str) -> bool {
    let path = path.trim_end_matches('/');
    path.ends_with(".pyc")
        || path.split('/').any(|c| {
            matches!(
                c,
                "__pycache__" | ".pytest_cache" | ".ruff_cache" | ".mypy_cache" | ".hypothesis" | "target" | "node_modules"
            ) || c.ends_with(".egg-info")
        })
}

/// The worktree's ignored paths (`git status --ignored=matching`; an ignored folder is one entry).
pub fn ignored_paths(runner: &dyn Runner, repo: &Path) -> Result<Vec<String>, String> {
    let raw = git(runner, repo, &["status", "--porcelain=v1", "-z", "--ignored=matching", "--untracked-files=all"])?;
    Ok(parse_status(&raw).into_iter().filter(|e| e.code == '!').map(|e| e.path).collect())
}

/// The submodule `rel` lies inside, if any (`.gitmodules` of the worktree). A task never edits
/// one and cargo never runs in one: even `cargo tree` rewrites a submodule's lockfile.
pub fn under_submodule(worktree: &Path, rel: &str) -> Option<String> {
    let text = std::fs::read_to_string(worktree.join(".gitmodules")).ok()?;
    let rel = rel.trim_start_matches("./").trim_end_matches('/');
    text.lines()
        .filter_map(|l| l.trim().strip_prefix("path"))
        .filter_map(|l| l.trim_start().strip_prefix('='))
        .map(|p| p.trim().trim_end_matches('/').to_string())
        .find(|p| !p.is_empty() && (rel == p || rel.starts_with(&format!("{p}/"))))
}

/// One entry of `git status`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StatusEntry {
    pub path: String,
    /// `?` untracked, `M` modified, `A` added, `D` deleted, `R` renamed, `T` type change.
    pub code: char,
}

/// Runs `git` in `repo` and returns stdout, or the failure.
pub fn git(runner: &dyn Runner, repo: &Path, args: &[&str]) -> Result<String, String> {
    // The checkout's config is the agent's to write: no program it names may run (ling_safegit).
    let hardened: Vec<String> = ling_safegit::hardened_args(Some(repo), args).iter().map(|a| a.to_string_lossy().into_owned()).collect();
    let mut argv = vec!["git"];
    argv.extend(hardened.iter().map(String::as_str));
    let mut spec = RunSpec::new(&argv, repo, 30);
    spec.env.extend(ling_safegit::FIXED_ENV.iter().map(|(k, v)| ((*k).to_string(), (*v).to_string())));
    let out = runner.run(&spec)?;
    if out.timed_out || out.exit != Some(0) {
        let (tail, _) = tail_of(&out.output, 3, 300);
        return Err(format!("git {} failed: {tail}", args.first().copied().unwrap_or("")));
    }
    Ok(out.output)
}

/// Parses `git status --porcelain=v1 -z --untracked-files=all`.
pub fn parse_status(raw: &str) -> Vec<StatusEntry> {
    let mut entries = Vec::new();
    let mut parts = raw.split('\0').filter(|s| !s.is_empty());
    while let Some(rec) = parts.next() {
        if rec.len() < 4 {
            continue;
        }
        let (xy, path) = rec.split_at(2);
        let path = path.trim_start().to_string();
        let mut chars = xy.chars();
        let (x, y) = (chars.next().unwrap_or(' '), chars.next().unwrap_or(' '));
        if x == 'R' || x == 'C' || y == 'R' || y == 'C' {
            // The next record is the rename's source; both paths changed.
            if let Some(from) = parts.next() {
                entries.push(StatusEntry { path: from.to_string(), code: 'D' });
            }
            entries.push(StatusEntry { path, code: 'R' });
            continue;
        }
        let code = if x == '?' { '?' } else if y != ' ' { y } else { x };
        entries.push(StatusEntry { path, code });
    }
    entries
}

/// The worktree's changed paths (tracked and untracked).
pub fn status_entries(runner: &dyn Runner, repo: &Path) -> Result<Vec<StatusEntry>, String> {
    let raw = git(runner, repo, &["status", "--porcelain=v1", "-z", "--untracked-files=all"])?;
    Ok(parse_status(&raw))
}

/// One `git diff --numstat` row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NumStat {
    pub path: String,
    pub added: usize,
    pub deleted: usize,
    pub binary: bool,
}

/// Parses `git diff --numstat --no-renames`.
pub fn parse_numstat(raw: &str) -> Vec<NumStat> {
    raw.lines()
        .filter_map(|line| {
            let mut it = line.splitn(3, '\t');
            let (a, d, path) = (it.next()?, it.next()?, it.next()?);
            let binary = a == "-" || d == "-";
            Some(NumStat {
                path: path.to_string(),
                added: a.parse().unwrap_or(0),
                deleted: d.parse().unwrap_or(0),
                binary,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_parsing_handles_untracked_modified_and_renames() {
        let raw = " M src/a.rs\0?? new.txt\0R  b.rs\0a.rs\0D  gone.rs\0";
        let got = parse_status(raw);
        assert_eq!(
            got,
            vec![
                StatusEntry { path: "src/a.rs".into(), code: 'M' },
                StatusEntry { path: "new.txt".into(), code: '?' },
                StatusEntry { path: "a.rs".into(), code: 'D' },
                StatusEntry { path: "b.rs".into(), code: 'R' },
                StatusEntry { path: "gone.rs".into(), code: 'D' },
            ]
        );
    }

    #[test]
    fn numstat_parsing_marks_binary_files() {
        let got = parse_numstat("3\t1\tsrc/a.rs\n-\t-\timg.png\n");
        assert_eq!(got[0], NumStat { path: "src/a.rs".into(), added: 3, deleted: 1, binary: false });
        assert!(got[1].binary);
    }

    #[test]
    fn tail_is_bounded_by_lines_and_characters() {
        let text = (1..=100).map(|i| format!("line {i}")).collect::<Vec<_>>().join("\n");
        let (t, cut) = tail_of(&text, 3, 1000);
        assert_eq!(t, "line 98\nline 99\nline 100");
        assert!(cut);
        let (t, cut) = tail_of("abcdefghij", 5, 4);
        assert_eq!(t, "ghij");
        assert!(cut);
    }

    #[test]
    fn the_system_runner_times_out_and_keeps_a_bounded_tail() {
        let dir = std::env::temp_dir();
        let ok = SystemRunner.run(&RunSpec::new(&["sh", "-c", "echo out; echo err >&2; exit 3"], &dir, 10)).unwrap();
        assert_eq!(ok.exit, Some(3));
        assert!(ok.output.contains("out") && ok.output.contains("err"));
        let slow = SystemRunner.run(&RunSpec::new(&["sh", "-c", "sleep 30"], &dir, 1)).unwrap();
        assert!(slow.timed_out);
        assert_eq!(slow.exit, None);
        assert!(SystemRunner.run(&RunSpec::new(&["/no/such/program"], &dir, 1)).is_err());
    }

    #[test]
    fn sandboxed_commands_start_in_their_folder_inside_the_workspace_sandbox() {
        let argv = sandboxed("ling", Path::new("/w"), Path::new("/w/crate"), &["/bin/cargo".into(), "test".into(), "x".into()]);
        assert_eq!(
            argv,
            [
                "ling", "sandbox", "-P", ":workspace", "--sandbox-state-disable-network", "-C", "/w", "--",
                "sh", "-c", "cd \"$1\" && shift && exec \"$@\"", "sh", "/w/crate", "/bin/cargo", "test", "x",
            ]
        );
        assert!(sandbox_env().iter().any(|(k, v)| k == "CARGO_NET_OFFLINE" && v == "true"));
    }

    #[test]
    fn build_output_is_told_apart_from_the_models_files() {
        for p in ["src/__pycache__/", "a/b.pyc", "target/", "ui/node_modules/", ".pytest_cache/", "x.egg-info/"] {
            assert!(is_build_artifact(p), "{p}");
        }
        for p in ["tests/conftest.py", ".venv/", "src/pytest.py", "tests/.gitignore"] {
            assert!(!is_build_artifact(p), "{p}");
        }
    }

    #[test]
    fn worktree_checks_refuse_a_main_checkout_and_a_plain_folder() {
        let base = std::env::temp_dir().join(format!("ling-exec-wt-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(base.join("main/.git")).unwrap();
        std::fs::create_dir_all(base.join("linked")).unwrap();
        std::fs::write(base.join("linked/.git"), "gitdir: /x/.git/worktrees/linked\n").unwrap();
        std::fs::create_dir_all(base.join("plain")).unwrap();
        assert!(linked_worktree(base.join("main").to_str().unwrap()).is_err());
        assert!(linked_worktree(base.join("plain").to_str().unwrap()).is_err());
        assert!(linked_worktree(base.join("linked").to_str().unwrap()).is_ok());
        assert!(repo_root(base.join("main").to_str().unwrap()).is_ok());
    }
}
