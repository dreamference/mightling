//! `git` that cannot be talked into running the agent's programs.
//!
//! A checkout's `.git/config` (and `.gitattributes`, and `.git/info/attributes`) is writable by the
//! agent, while the commands built here run outside the sandbox. Several config keys make git
//! execute a program during a plain read: `core.fsmonitor` (status, diff), `core.pager`,
//! `diff.external` and `diff.<driver>.textconv` (diff, show, log), `filter.<driver>.clean` and
//! `.smudge` and `.process` (status, diff, add, checkout), `merge.<driver>.driver`, `core.sshCommand`
//! and `core.gitProxy` (fetch, push), `remote.<name>.uploadpack` and `.receivepack`,
//! `credential.helper`, `gpg.program` (signatures), `core.hooksPath` and the hooks themselves,
//! `protocol.ext.allow` (the `ext::` transport), and `submodule.<name>.update`.
//!
//! The command-line `-c` scope outranks every file, so this crate pins the fixed keys there and, for
//! the three driver families whose names are the agent's to choose, reads the names from the
//! effective config and blanks each program. `--no-ext-diff` and `--no-textconv` go after
//! `diff`, `show` and `log`. The environment drops `GIT_EXTERNAL_DIFF` and sets `GIT_PAGER=cat`.
//! One window stays: a name added between the read and the run. Callers that have a sandbox use it
//! too; this is the layer for the ones that do not.

use std::ffi::OsString;
use std::io::Read;
use std::path::Path;
use std::process::{Command, Output, Stdio};
use std::time::{Duration, Instant};

/// Keys pinned on the command line. Every value is either inert or what git does by default.
pub const FIXED_CONFIG: &[&str] = &[
    "core.fsmonitor=false",
    "core.hooksPath=/dev/null",
    "core.pager=cat",
    "core.sshCommand=ssh",
    "core.gitProxy=",
    "core.askPass=",
    "core.editor=true",
    "sequence.editor=true",
    "diff.external=",
    "diff.submodule=short",
    "credential.helper=",
    "gpg.program=gpg",
    "gpg.ssh.program=ssh-keygen",
    "gpg.x509.program=gpgsm",
    "gpg.openpgp.program=gpg",
    "log.showSignature=false",
    "protocol.ext.allow=never",
    "uploadpack.packObjectsHook=",
    "submodule.recurse=false",
    "status.submoduleSummary=false",
    "gc.auto=0",
    "maintenance.auto=false",
    "core.quotepath=off",
];

/// The longest a host-side git command may run unless the caller says otherwise.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

/// Environment variables the command must not inherit (they name a program for diff).
pub const REMOVED_ENV: &[&str] = &["GIT_EXTERNAL_DIFF", "GIT_CONFIG_PARAMETERS", "GIT_PROXY_COMMAND"];

/// Environment variables the command sets.
pub const FIXED_ENV: &[(&str, &str)] =
    &[("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_PAGER", "cat"), ("GIT_TERMINAL_PROMPT", "0"), ("GIT_OPTIONAL_LOCKS", "0")];

/// The config entries the checkout itself supplies (its own `.git/config`, a linked worktree's, and
/// anything the command line adds), as (key, value): section and variable lowercased by git, the
/// driver name as written. The user's global and the system config are trusted and left alone, so a
/// user's own git-lfs filter keeps working. Empty when git cannot say.
fn checkout_config(dir: &Path) -> Vec<(String, String)> {
    let mut cmd = Command::new("git");
    // `safe.directory=*` only so that git reads the config of a checkout another user owns (it
    // runs nothing); the command the caller then runs decides for itself.
    cmd.args(["-c", "safe.directory=*", "-C"]).arg(dir).args(["config", "--list", "--null", "--show-scope"]);
    apply_env(&mut cmd);
    let Some(out) = run(cmd, Duration::from_secs(10)).ok().filter(|o| o.status.success()) else {
        return Vec::new();
    };
    let text = String::from_utf8_lossy(&out.stdout).into_owned();
    let mut fields = text.split('\0');
    let mut entries = Vec::new();
    while let (Some(scope), Some(entry)) = (fields.next(), fields.next()) {
        if matches!(scope, "local" | "worktree" | "command") {
            let (key, value) = entry.split_once('\n').unwrap_or((entry, ""));
            entries.push((key.to_string(), value.to_string()));
        }
    }
    entries
}

/// The `-c key=` pairs that blank every driver program the agent's config names in `dir`.
pub fn driver_overrides(dir: &Path) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    for (key, _) in checkout_config(dir) {
        let Some((family, rest)) = key.split_once('.') else { continue };
        let Some((name, variable)) = rest.rsplit_once('.') else { continue };
        let blank = match (family, variable.to_ascii_lowercase().as_str()) {
            ("filter", "clean" | "smudge" | "process") => "",
            ("filter", "required") => "false",
            ("diff", "textconv" | "command") => "",
            ("merge", "driver") => "false",
            ("submodule", "update") => "none",
            _ => continue,
        };
        // A name with an equals sign cannot be written as a -c pair; git refuses it too.
        if name.contains('=') || name.contains('\n') {
            continue;
        }
        found.push(format!("{key}={blank}"));
    }
    found.sort();
    found.dedup();
    found
}

/// Everything that goes after `git` and before the caller's own arguments: `--no-pager` and the
/// `-c` pairs, the fixed ones and (when `dir` is a repository) the driver overrides.
pub fn config_args(dir: Option<&Path>) -> Vec<String> {
    let mut out = vec!["--no-pager".to_string()];
    for pair in FIXED_CONFIG {
        out.push("-c".to_string());
        out.push((*pair).to_string());
    }
    if let Some(dir) = dir {
        for pair in driver_overrides(dir) {
            out.push("-c".to_string());
            out.push(pair);
        }
    }
    out
}

/// The index of the subcommand in `args`, skipping git's global options.
fn subcommand_index(args: &[&str]) -> Option<usize> {
    let mut i = 0;
    while i < args.len() {
        match args[i] {
            "-c" | "-C" | "--git-dir" | "--work-tree" | "--namespace" | "--super-prefix" | "--config-env" => i += 2,
            arg if arg.starts_with('-') => i += 1,
            _ => return Some(i),
        }
    }
    None
}

/// `args` with the flags that config cannot override placed after the subcommand: `--no-ext-diff
/// --no-textconv` for `diff`, `show` and `log`, and the stock `--upload-pack` for `fetch`, `pull`
/// and `ls-remote` and `--receive-pack` for `push`. The last pair is a flag because
/// `remote.<name>.uploadpack` and `.receivepack` are first-value-wins in git: a `-c` pair cannot
/// replace what the repository's config says, but a flag outranks it.
pub fn safe_flags(args: &[&str]) -> Vec<String> {
    let mut out: Vec<String> = args.iter().map(|a| (*a).to_string()).collect();
    if let Some(i) = subcommand_index(args) {
        let extra: &[&str] = match args[i] {
            "diff" | "show" | "log" => &["--no-ext-diff", "--no-textconv"],
            "fetch" | "pull" | "ls-remote" => &["--upload-pack=git-upload-pack"],
            "push" => &["--receive-pack=git-receive-pack"],
            _ => &[],
        };
        out.splice(i + 1..i + 1, extra.iter().map(|a| (*a).to_string()));
    }
    out
}

/// `argv` (a `git` command line built by `hardened_args`) without what this crate added, so a test
/// double can match on the command the caller meant. Anything not starting with `git` is returned as is.
pub fn plain_argv(argv: &[String]) -> Vec<String> {
    if argv.first().map(String::as_str) != Some("git") {
        return argv.to_vec();
    }
    let injected = ["--no-ext-diff", "--no-textconv", "--upload-pack=git-upload-pack", "--receive-pack=git-receive-pack"];
    let mut out = vec!["git".to_string()];
    let mut i = 1;
    while i < argv.len() && (argv[i] == "--no-pager" || argv[i] == "-c") {
        i += if argv[i] == "-c" { 2 } else { 1 };
    }
    if let Some(sub) = argv.get(i) {
        out.push(sub.clone());
        i += 1;
    }
    while i < argv.len() && injected.contains(&argv[i].as_str()) {
        i += 1;
    }
    out.extend(argv[i..].iter().cloned());
    out
}

/// The complete argument list after `git`: safe config, then `args` with the diff flags.
pub fn hardened_args(dir: Option<&Path>, args: &[&str]) -> Vec<OsString> {
    config_args(dir).into_iter().chain(safe_flags(args)).map(OsString::from).collect()
}

/// Sets the fixed variables and removes the program-naming ones.
pub fn apply_env(cmd: &mut Command) {
    for name in REMOVED_ENV {
        cmd.env_remove(name);
    }
    for (name, value) in FIXED_ENV {
        cmd.env(name, value);
    }
}

/// A `git -C <dir> …` command with everything above applied. The caller sets stdio and may add
/// environment of its own; it must not add `-c` pairs that name programs.
pub fn command(dir: &Path, args: &[&str]) -> Command {
    let mut cmd = Command::new("git");
    cmd.arg("-C").arg(dir).args(hardened_args(Some(dir), args));
    apply_env(&mut cmd);
    cmd
}

/// Runs `cmd` to completion, killing it when `timeout` passes. stdin is closed unless the caller
/// piped it (then the caller should use `spawn`); stdout and stderr are collected.
pub fn run(mut cmd: Command, timeout: Duration) -> std::io::Result<Output> {
    let mut child = cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()?;
    let drain = |pipe: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(mut pipe) = pipe {
                let _ = pipe.read_to_end(&mut buf);
            }
            buf
        })
    };
    let out = drain(child.stdout.take().map(|p| Box::new(p) as Box<dyn Read + Send>));
    let err = drain(child.stderr.take().map(|p| Box::new(p) as Box<dyn Read + Send>));
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let status = child.wait()?;
            let _ = (out.join(), err.join());
            return Err(std::io::Error::new(std::io::ErrorKind::TimedOut, format!("git timed out after {}s ({status})", timeout.as_secs())));
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    Ok(Output { status, stdout: out.join().unwrap_or_default(), stderr: err.join().unwrap_or_default() })
}

/// `git -C dir args…`, hardened, with the default timeout.
pub fn output(dir: &Path, args: &[&str]) -> std::io::Result<Output> {
    run(command(dir, args), DEFAULT_TIMEOUT)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    fn scratch(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-safegit-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn plain(dir: &Path, args: &[&str]) {
        let ok = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"])
            .args(args)
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .output()
            .unwrap()
            .status
            .success();
        assert!(ok, "git {args:?}");
    }

    fn script(dir: &Path, name: &str, marker: &Path) -> String {
        let path = dir.join(name);
        fs::write(&path, format!("#!/bin/sh\necho ran-{name} >> '{}'\ncat\n", marker.display())).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        path.display().to_string()
    }

    /// A repository whose config names a program for every hook the module knows about.
    fn hostile(name: &str) -> (std::path::PathBuf, std::path::PathBuf) {
        let root = scratch(name);
        let repo = root.join("repo");
        let tools = root.join("tools");
        fs::create_dir_all(&repo).unwrap();
        fs::create_dir_all(&tools).unwrap();
        let marker = root.join("marker");
        plain(&repo, &["init", "-q"]);
        fs::write(repo.join("a.txt"), "one\n").unwrap();
        fs::write(repo.join(".gitattributes"), "*.txt filter=evil diff=evil\n").unwrap();
        plain(&repo, &["add", "-A"]);
        plain(&repo, &["commit", "-q", "-m", "init"]);
        let fsmon = script(&tools, "fsmonitor", &marker);
        let pager = script(&tools, "pager", &marker);
        let ext = script(&tools, "external", &marker);
        let textconv = script(&tools, "textconv", &marker);
        let clean = script(&tools, "clean", &marker);
        let smudge = script(&tools, "smudge", &marker);
        let ssh = script(&tools, "ssh", &marker);
        let cred = script(&tools, "cred", &marker);
        let up = script(&tools, "uploadpack", &marker);
        let cfg = format!(
            "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tfsmonitor = {fsmon}\n\tpager = {pager}\n\tsshCommand = {ssh}\n\thooksPath = {hooks}\n\
             [diff]\n\texternal = {ext}\n[diff \"evil\"]\n\ttextconv = {textconv}\n\tcommand = {ext}\n\
             [filter \"evil\"]\n\tclean = {clean}\n\tsmudge = {smudge}\n\
             [credential]\n\thelper = {cred}\n[remote \"origin\"]\n\turl = {repo}\n\tuploadpack = {up}\n\treceivepack = {up}\n",
            hooks = tools.display(),
            repo = repo.display(),
        );
        fs::write(repo.join(".git/config"), cfg).unwrap();
        for hook in ["pre-commit", "post-checkout", "post-index-change"] {
            fs::copy(tools.join("pager"), tools.join(hook)).unwrap();
        }
        // Make the working file differ in size and time so status must re-read (and clean-filter) it.
        fs::write(repo.join("a.txt"), "one\ntwo changed\n").unwrap();
        (repo, marker)
    }

    #[test]
    fn the_hostile_config_does_run_programs_without_the_hardening() {
        // Guards the test below: if plain git ran nothing here, the proof would prove nothing.
        let (repo, marker) = hostile("control");
        let _ = Command::new("git").arg("-C").arg(&repo).args(["status", "--porcelain"]).env("GIT_CONFIG_NOSYSTEM", "1").output();
        let _ = Command::new("git").arg("-C").arg(&repo).args(["diff"]).env("GIT_CONFIG_NOSYSTEM", "1").env("GIT_PAGER", "").output();
        assert!(marker.exists(), "the control run should have executed a configured program");
    }

    #[test]
    fn no_configured_program_runs_for_the_read_only_commands() {
        let (repo, marker) = hostile("proof");
        for args in [
            vec!["status", "--porcelain", "--untracked-files=all"],
            vec!["diff"],
            vec!["diff", "--cached"],
            vec!["show", "HEAD"],
            vec!["log", "-p", "-1"],
            vec!["rev-parse", "--verify", "HEAD"],
            vec!["ls-files", "-m"],
            vec!["add", "-A"],
            vec!["write-tree"],
        ] {
            let out = output(&repo, &args).unwrap();
            assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
        }
        let ran = fs::read_to_string(&marker).unwrap_or_default();
        assert!(ran.is_empty(), "a configured program ran: {ran}");
    }

    #[test]
    fn a_fetch_does_not_run_the_remote_or_ssh_programs() {
        let (repo, marker) = hostile("fetch");
        for args in [vec!["fetch", "origin"], vec!["ls-remote", "origin"]] {
            let out = output(&repo, &args).unwrap();
            assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        }
        let out = output(&repo, &["push", "origin", "HEAD:refs/heads/probe"]).unwrap();
        let _ = out;
        let ran = fs::read_to_string(&marker).unwrap_or_default();
        assert!(ran.is_empty(), "a configured program ran: {ran}");
    }

    #[test]
    fn a_hung_command_is_killed() {
        let mut cmd = Command::new("sleep");
        cmd.arg("30");
        let started = Instant::now();
        let err = run(cmd, Duration::from_millis(200)).unwrap_err();
        assert_eq!(err.kind(), std::io::ErrorKind::TimedOut);
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn flags_follow_the_subcommand_only() {
        assert_eq!(safe_flags(&["push", "origin"]), ["push", "--receive-pack=git-receive-pack", "origin"]);
        assert_eq!(safe_flags(&["-C", "x", "fetch"]), ["-C", "x", "fetch", "--upload-pack=git-upload-pack"]);
        assert_eq!(safe_flags(&["--no-optional-locks", "diff", "HEAD"]), ["--no-optional-locks", "diff", "--no-ext-diff", "--no-textconv", "HEAD"]);
        assert_eq!(safe_flags(&["-c", "a=b", "log", "-1"]), ["-c", "a=b", "log", "--no-ext-diff", "--no-textconv", "-1"]);
        assert_eq!(safe_flags(&["status", "--porcelain"]), ["status", "--porcelain"]);
        assert_eq!(safe_flags(&["rev-parse", "diff"]), ["rev-parse", "diff"]);
    }

    #[test]
    fn driver_names_come_from_the_config() {
        let (repo, _) = hostile("names");
        let pairs = driver_overrides(&repo);
        for want in ["filter.evil.clean=", "filter.evil.smudge=", "diff.evil.textconv=", "diff.evil.command="] {
            assert!(pairs.iter().any(|p| p == want), "{want} missing from {pairs:?}");
        }
    }
}
