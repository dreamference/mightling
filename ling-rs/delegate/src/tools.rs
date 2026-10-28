//! The exact tools beside `lookup`, all with no model: `run_test`, `git_log` and `diff_stat`
//! (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §4.2).
//!
//! Same discipline as `lookup`: a closed list (the runners; the three fixed git invocations), every
//! argument validated before a process starts, a timeout, bounded output, and a read-back where
//! one is possible (the file's line count for a `git_log` range, the repository's own `git status`
//! for `diff_stat`). Nothing here writes to the repository.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::exec::{
    NumStat, RunSpec, Runner, check_relative, git, linked_worktree, parse_numstat, repo_root,
    status_entries, tail_of, under_submodule, venv_tool,
};

/// The closed list of test runners.
pub const TEST_RUNNERS: &[&str] = &["pytest", "cargo", "npm"];
/// Default and maximum seconds a test may run.
pub const DEFAULT_TEST_TIMEOUT_S: u64 = 600;
pub const MAX_TEST_TIMEOUT_S: u64 = 1_800;
/// Default and maximum output lines returned.
pub const DEFAULT_TAIL_LINES: usize = 40;
pub const MAX_TAIL_LINES: usize = 120;
/// Characters of output returned at most (about 1,500 tokens).
pub const MAX_TAIL_CHARS: usize = 6_000;
/// `git_log` commits, default and maximum.
pub const DEFAULT_LOG_LIMIT: usize = 10;
pub const MAX_LOG_LIMIT: usize = 30;
/// `diff_stat` files listed at most.
pub const MAX_STAT_FILES: usize = 100;
const MAX_TARGET_CHARS: usize = 300;

// ---------------------------------------------------------------------------------------------
// run_test
// ---------------------------------------------------------------------------------------------

/// A `run_test` call: one named test in a linked worktree.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunTestRequest {
    /// One of [`TEST_RUNNERS`].
    pub runner: String,
    /// pytest: a node id (`tests/test_x.py::test_y`); cargo: a test name filter; npm: a test pattern.
    pub target: String,
    /// The linked worktree to run in.
    pub worktree: String,
    /// A folder inside the worktree to run from (a crate, a package), relative.
    #[serde(default)]
    pub cwd: Option<String>,
    /// cargo only: `-p <package>`.
    #[serde(default)]
    pub package: Option<String>,
    #[serde(default)]
    pub timeout_s: Option<u64>,
    /// Output lines returned (default 40, at most 120).
    #[serde(default)]
    pub lines: Option<usize>,
}

/// The result of a test run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RunTestResult {
    pub runner: String,
    pub target: String,
    pub passed: bool,
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub seconds: u64,
    /// The runner's own summary line, when one was found in the output.
    pub summary: String,
    pub tail: String,
    pub truncated: bool,
}

/// A test target: one token of path, name and node-id characters, never a flag.
pub fn check_target(target: &str, what: &str) -> Result<(), String> {
    let ok = !target.is_empty()
        && target.len() <= MAX_TARGET_CHARS
        && !target.starts_with('-')
        && target.chars().all(|c| {
            c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | ':' | '[' | ']' | '-' | ',' | '=' | '+' | '@')
        });
    if ok {
        Ok(())
    } else {
        Err(format!(
            "invalid {what}: one token of letters, digits and _ . / : [ ] - , = + @, not starting with `-`"
        ))
    }
}

/// Validates a request and builds the command and its folder.
pub fn test_command(req: &RunTestRequest) -> Result<(Vec<String>, PathBuf, PathBuf), String> {
    if !TEST_RUNNERS.contains(&req.runner.as_str()) {
        return Err(format!(
            "unknown runner {:?}; the closed list is {}",
            req.runner,
            TEST_RUNNERS.join(", ")
        ));
    }
    check_target(&req.target, "target")?;
    let worktree = linked_worktree(&req.worktree)?;
    let mut dir = worktree.clone();
    if let Some(cwd) = &req.cwd {
        check_relative(cwd, "cwd")?;
        dir = worktree
            .join(cwd)
            .canonicalize()
            .map_err(|e| format!("cwd {cwd} is not readable: {e}"))?;
        if !dir.starts_with(&worktree) || !dir.is_dir() {
            return Err("cwd must be a folder inside the worktree".to_string());
        }
        if let Some(sub) = under_submodule(&worktree, cwd) {
            return Err(format!("cwd is inside the submodule {sub}; tests never run in a submodule"));
        }
    }
    if req.package.is_some() && req.runner != "cargo" {
        return Err("`package` applies to cargo only".to_string());
    }
    let mut argv: Vec<String> = match req.runner.as_str() {
        "pytest" => vec![
            venv_tool(&worktree, "python"),
            "-m".into(),
            "pytest".into(),
            req.target.clone(),
            "-q".into(),
            "-x".into(),
            "--no-header".into(),
            "-p".into(),
            "no:cacheprovider".into(),
        ],
        "cargo" => {
            let mut v = vec!["cargo".to_string(), "test".to_string()];
            if let Some(p) = &req.package {
                check_target(p, "package")?;
                v.push("-p".into());
                v.push(p.clone());
            }
            v.push(req.target.clone());
            v
        }
        _ => vec!["npm".into(), "test".into(), "--".into(), req.target.clone()],
    };
    argv.shrink_to_fit();
    Ok((argv, dir, worktree))
}

/// The runner's own summary line in an output, if any.
fn summary_line(output: &str) -> String {
    output
        .lines()
        .rev()
        .find(|l| {
            let low = l.to_ascii_lowercase();
            (low.contains("passed") || low.contains("failed") || low.contains("test result") || low.contains("error"))
                && l.chars().count() < 200
        })
        .map(|l| l.trim().to_string())
        .unwrap_or_default()
}

/// Runs one test with a timeout and returns a bounded tail.
pub fn run_test(req: &RunTestRequest, runner: &dyn Runner) -> Result<RunTestResult, String> {
    let (argv, dir, _worktree) = test_command(req)?;
    let timeout = req.timeout_s.unwrap_or(DEFAULT_TEST_TIMEOUT_S).clamp(1, MAX_TEST_TIMEOUT_S);
    let lines = req.lines.unwrap_or(DEFAULT_TAIL_LINES).clamp(1, MAX_TAIL_LINES);
    let spec = RunSpec {
        argv,
        cwd: dir,
        env: vec![("CI".into(), "1".into())],
        timeout_s: timeout,
        batch: true,
    };
    let out = runner.run(&spec)?;
    let (tail, truncated) = tail_of(&out.output, lines, MAX_TAIL_CHARS);
    Ok(RunTestResult {
        runner: req.runner.clone(),
        target: req.target.clone(),
        passed: out.exit == Some(0) && !out.timed_out,
        exit_code: out.exit,
        timed_out: out.timed_out,
        seconds: out.elapsed_s,
        summary: summary_line(&out.output),
        tail,
        truncated,
    })
}

// ---------------------------------------------------------------------------------------------
// git_log
// ---------------------------------------------------------------------------------------------

/// A `git_log` call: the history of a file, or of a line range in it.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GitLogRequest {
    /// The file, relative to the repository.
    pub path: String,
    /// First line of the range (with `end`): the history of those lines only.
    #[serde(default)]
    pub start: Option<usize>,
    #[serde(default)]
    pub end: Option<usize>,
    /// The repository or worktree root (default: the current directory).
    #[serde(default)]
    pub repo: Option<String>,
    /// Commits to return (default 10, at most 30).
    #[serde(default)]
    pub limit: Option<usize>,
}

/// One commit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Commit {
    pub hash: String,
    pub author: String,
    pub date: String,
    pub subject: String,
}

/// The history.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GitLogResult {
    pub path: String,
    pub range: Option<(usize, usize)>,
    pub commits: Vec<Commit>,
    /// Rows that were not a commit (a malformed hash).
    pub dropped: usize,
    pub truncated: bool,
    pub notes: Vec<String>,
}

fn is_hash(s: &str) -> bool {
    s.len() == 40 && s.chars().all(|c| c.is_ascii_hexdigit())
}

/// Parses `--format=%H%x1f%an%x1f%aI%x1f%s` rows (`-L` output carries only these with `-s`).
pub fn parse_log(raw: &str) -> (Vec<Commit>, usize) {
    let mut commits = Vec::new();
    let mut dropped = 0;
    for line in raw.lines().filter(|l| !l.trim().is_empty()) {
        let parts: Vec<&str> = line.splitn(4, '\u{1f}').collect();
        if parts.len() == 4 && is_hash(parts[0]) {
            commits.push(Commit {
                hash: parts[0].to_string(),
                author: parts[1].chars().take(80).collect(),
                date: parts[2].to_string(),
                subject: parts[3].chars().take(200).collect(),
            });
        } else {
            dropped += 1;
        }
    }
    (commits, dropped)
}

/// Reads the history of a file or a line range.
pub fn git_log(req: &GitLogRequest, runner: &dyn Runner) -> Result<GitLogResult, String> {
    check_relative(&req.path, "path")?;
    if req.path.contains(['*', '?', '[', ':']) {
        return Err("`path` is one file, with no glob and no `:`".to_string());
    }
    let repo = repo_root(&req.repo.clone().unwrap_or_else(|| ".".to_string()))?;
    let range = match (req.start, req.end) {
        (None, None) => None,
        (Some(s), Some(e)) if s >= 1 && e >= s => Some((s, e)),
        _ => return Err("`start` and `end` go together, 1 <= start <= end".to_string()),
    };
    let mut notes = Vec::new();
    if let Some((s, e)) = range {
        // Read-back: the range must exist in the file as it is now.
        let full = repo.join(&req.path).canonicalize().map_err(|e| format!("{}: {e}", req.path))?;
        if !full.starts_with(&repo) {
            return Err("`path` leaves the repository".to_string());
        }
        let lines = std::fs::read_to_string(&full).map_err(|er| format!("{}: {er}", req.path))?.lines().count();
        if e > lines {
            return Err(format!("{} has {lines} lines; the range {s}-{e} is past the end", req.path));
        }
    }
    let limit = req.limit.unwrap_or(DEFAULT_LOG_LIMIT).clamp(1, MAX_LOG_LIMIT);
    let n = (limit + 1).to_string();
    let format = "--format=%H%x1f%an%x1f%aI%x1f%s";
    let raw = match range {
        None => git(runner, &repo, &["log", "--no-color", "-n", &n, format, "--", &req.path])?,
        Some((s, e)) => {
            let spec = format!("-L{s},{e}:{}", req.path);
            git(runner, &repo, &["log", "--no-color", "-s", "-n", &n, format, &spec])?
        }
    };
    let (mut commits, dropped) = parse_log(&raw);
    let truncated = commits.len() > limit;
    commits.truncate(limit);
    if commits.is_empty() {
        notes.push("no commit touches this path (or range) in the history of HEAD".to_string());
    }
    Ok(GitLogResult { path: req.path.clone(), range, commits, dropped, truncated, notes })
}

// ---------------------------------------------------------------------------------------------
// diff_stat
// ---------------------------------------------------------------------------------------------

/// A `diff_stat` call.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiffStatRequest {
    /// The repository or worktree root (default: the current directory).
    #[serde(default)]
    pub repo: Option<String>,
}

/// One changed file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StatFile {
    pub path: String,
    /// `M` modified, `A` added, `D` deleted, `R` renamed, `?` untracked.
    pub status: String,
    pub added: usize,
    pub deleted: usize,
    pub binary: bool,
}

/// The change summary.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DiffStatResult {
    pub branch: String,
    pub head: String,
    pub files: Vec<StatFile>,
    pub file_count: usize,
    pub added: usize,
    pub deleted: usize,
    pub untracked: usize,
    pub truncated: bool,
}

/// Summarises the working tree against HEAD: tracked changes with their line counts, untracked
/// files by name. Untracked files are not counted in `added`/`deleted`.
pub fn diff_stat(req: &DiffStatRequest, runner: &dyn Runner) -> Result<DiffStatResult, String> {
    let repo = repo_root(&req.repo.clone().unwrap_or_else(|| ".".to_string()))?;
    stat_of(&repo, runner)
}

pub(crate) fn stat_of(repo: &Path, runner: &dyn Runner) -> Result<DiffStatResult, String> {
    let entries = status_entries(runner, repo)?;
    let numstat: Vec<NumStat> = match git(runner, repo, &["diff", "--numstat", "--no-renames", "HEAD"]) {
        Ok(raw) => parse_numstat(&raw),
        // A repository with no commit yet has no HEAD to compare to.
        Err(_) => Vec::new(),
    };
    let mut files: Vec<StatFile> = Vec::new();
    for e in &entries {
        let stat = numstat.iter().find(|n| n.path == e.path);
        files.push(StatFile {
            path: e.path.clone(),
            status: e.code.to_string(),
            added: stat.map(|s| s.added).unwrap_or(0),
            deleted: stat.map(|s| s.deleted).unwrap_or(0),
            binary: stat.map(|s| s.binary).unwrap_or(false),
        });
    }
    files.sort_by(|a, b| a.path.cmp(&b.path));
    let (added, deleted) = files.iter().fold((0, 0), |(a, d), f| (a + f.added, d + f.deleted));
    let untracked = files.iter().filter(|f| f.status == "?").count();
    let file_count = files.len();
    let truncated = file_count > MAX_STAT_FILES;
    files.truncate(MAX_STAT_FILES);
    let branch = git(runner, repo, &["rev-parse", "--abbrev-ref", "HEAD"]).unwrap_or_default().trim().to_string();
    let head = git(runner, repo, &["rev-parse", "--short", "HEAD"]).unwrap_or_default().trim().to_string();
    Ok(DiffStatResult { branch, head, files, file_count, added, deleted, untracked, truncated })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::exec::RunOutput;
    use std::cell::RefCell;

    /// Answers each command from a table keyed on a fragment of its joined argv.
    pub(crate) struct Script {
        pub rules: Vec<(&'static str, i32, String)>,
        pub seen: RefCell<Vec<(Vec<String>, PathBuf, bool)>>,
    }

    impl Script {
        pub fn new(rules: Vec<(&'static str, i32, String)>) -> Self {
            Self { rules, seen: RefCell::new(vec![]) }
        }
    }

    impl Runner for Script {
        fn run(&self, spec: &RunSpec) -> Result<RunOutput, String> {
            let argv = ling_safegit::plain_argv(&spec.argv);
            self.seen.borrow_mut().push((argv.clone(), spec.cwd.clone(), spec.batch));
            let joined = argv.join(" ");
            for (frag, code, out) in &self.rules {
                if joined.contains(frag) {
                    return Ok(RunOutput { exit: Some(*code), output: out.clone(), timed_out: false, elapsed_s: 1 });
                }
            }
            Err(format!("unexpected command: {joined}"))
        }
    }

    pub(crate) fn scratch_worktree(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-tools-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("crate")).unwrap();
        std::fs::write(dir.join(".git"), "gitdir: /nowhere/.git/worktrees/x\n").unwrap();
        std::fs::write(dir.join("f.txt"), "1\n2\n3\n").unwrap();
        dir
    }

    fn req(dir: &Path, runner: &str, target: &str) -> RunTestRequest {
        RunTestRequest {
            runner: runner.into(),
            target: target.into(),
            worktree: dir.display().to_string(),
            ..Default::default()
        }
    }

    #[test]
    fn run_test_builds_one_fixed_command_per_runner_in_the_batch_slice() {
        let dir = scratch_worktree("rt");
        let s = Script::new(vec![("pytest", 0, "x\n1 passed in 0.1s\n".into()), ("cargo", 101, "test result: FAILED. 0 passed; 1 failed\n".into())]);
        let ok = run_test(&req(&dir, "pytest", "tests/test_a.py::test_b[x-1]"), &s).unwrap();
        assert!(ok.passed);
        assert_eq!(ok.summary, "1 passed in 0.1s");
        let (argv, _, batch) = s.seen.borrow()[0].clone();
        assert!(batch);
        assert_eq!(&argv[1..5], ["-m", "pytest", "tests/test_a.py::test_b[x-1]", "-q"]);

        let mut r = req(&dir, "cargo", "lookup::tests");
        r.cwd = Some("crate".into());
        r.package = Some("ling-delegate".into());
        let bad = run_test(&r, &s).unwrap();
        assert!(!bad.passed);
        assert_eq!(bad.exit_code, Some(101));
        let (argv, cwd, _) = s.seen.borrow()[1].clone();
        assert_eq!(argv, ["cargo", "test", "-p", "ling-delegate", "lookup::tests"]);
        assert!(cwd.ends_with("crate"));
    }

    #[test]
    fn run_test_refuses_anything_outside_the_closed_list() {
        let dir = scratch_worktree("rt-closed");
        let s = Script::new(vec![]);
        for (runner, target) in [("make", "x"), ("pytest", "--co"), ("pytest", "a b"), ("pytest", "x;rm"), ("pytest", "$(id)"), ("pytest", "")] {
            assert!(run_test(&req(&dir, runner, target), &s).is_err(), "{runner} {target}");
        }
        let mut r = req(&dir, "pytest", "t.py");
        r.cwd = Some("../..".into());
        assert!(run_test(&r, &s).is_err());
        std::fs::write(dir.join(".gitmodules"), "[submodule \"codex\"]\n\tpath = codex\n\turl = x\n").unwrap();
        std::fs::create_dir_all(dir.join("codex/codex-rs")).unwrap();
        r.runner = "cargo".into();
        r.cwd = Some("codex/codex-rs".into());
        assert!(run_test(&r, &s).unwrap_err().contains("submodule"));
        r.runner = "pytest".into();
        r.cwd = None;
        r.cwd = None;
        r.package = Some("p".into());
        assert!(run_test(&r, &s).is_err(), "package is cargo-only");
        // The main checkout is not a worktree.
        let main = std::env::temp_dir().join(format!("ling-tools-main-{}", std::process::id()));
        std::fs::create_dir_all(main.join(".git")).unwrap();
        assert!(run_test(&req(&main, "pytest", "t.py"), &s).is_err());
        assert!(s.seen.borrow().is_empty(), "nothing started");
    }

    #[test]
    fn run_test_bounds_its_output_and_clamps_the_timeout() {
        let dir = scratch_worktree("rt-bound");
        let long = (0..500).map(|i| format!("line {i}")).collect::<Vec<_>>().join("\n");
        let s = Script::new(vec![("npm", 1, long)]);
        let mut r = req(&dir, "npm", "ui.test");
        r.lines = Some(10_000);
        r.timeout_s = Some(999_999);
        let res = run_test(&r, &s).unwrap();
        assert!(res.truncated);
        assert!(res.tail.lines().count() <= MAX_TAIL_LINES);
        assert!(res.tail.chars().count() <= MAX_TAIL_CHARS);
        assert!(!res.passed);
    }

    #[test]
    fn git_log_reads_the_history_and_checks_the_range_against_the_file() {
        let dir = scratch_worktree("gl");
        let h1 = "a".repeat(40);
        let h2 = "b".repeat(40);
        let out = format!("{h1}\u{1f}Ann\u{1f}2026-10-01T00:00:00+00:00\u{1f}fix it\nnot a commit\n{h2}\u{1f}Bob\u{1f}2026-09-01T00:00:00+00:00\u{1f}first\n");
        let s = Script::new(vec![("log", 0, out)]);
        let r = GitLogRequest { path: "f.txt".into(), start: Some(2), end: Some(3), repo: Some(dir.display().to_string()), limit: Some(1) };
        let res = git_log(&r, &s).unwrap();
        assert_eq!(res.commits.len(), 1);
        assert!(res.truncated);
        assert_eq!(res.dropped, 1);
        assert_eq!(res.commits[0].subject, "fix it");
        let argv = s.seen.borrow()[0].0.clone();
        assert!(argv.contains(&"-L2,3:f.txt".to_string()) && argv.contains(&"-s".to_string()));

        let mut past = r.clone();
        past.end = Some(99);
        assert!(git_log(&past, &s).is_err(), "range past the end of the file");
        let mut half = r.clone();
        half.end = None;
        assert!(git_log(&half, &s).is_err());
        for bad in ["../x", "/etc/passwd", "-p", "a*b", "a:b"] {
            let mut b = r.clone();
            b.path = bad.into();
            b.start = None;
            b.end = None;
            assert!(git_log(&b, &s).is_err(), "{bad}");
        }
    }

    #[test]
    fn diff_stat_joins_status_and_numstat() {
        let dir = scratch_worktree("ds");
        let s = Script::new(vec![
            ("status", 0, " M src/a.rs\0?? new.txt\0D  old.rs\0".into()),
            ("numstat", 0, "5\t2\tsrc/a.rs\n0\t7\told.rs\n".into()),
            ("--abbrev-ref", 0, "feature\n".into()),
            ("--short", 0, "abc1234\n".into()),
        ]);
        let res = diff_stat(&DiffStatRequest { repo: Some(dir.display().to_string()) }, &s).unwrap();
        assert_eq!((res.file_count, res.added, res.deleted, res.untracked), (3, 5, 9, 1));
        assert_eq!(res.branch, "feature");
        assert_eq!(res.files[0].path, "new.txt");
        assert_eq!(res.files[0].status, "?");
    }
}
