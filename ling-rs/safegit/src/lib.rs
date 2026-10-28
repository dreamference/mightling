//! `git` that cannot be talked into running the agent's programs.
//!
//! A checkout's `.git/config` (and `.gitattributes`, and `.git/info/attributes`) is writable by the
//! agent, while the commands built here run outside the sandbox. Several config keys make git
//! execute a program during a plain read: `core.fsmonitor` (status, diff), `core.pager`,
//! `diff.external` and `diff.<driver>.textconv` (diff, show, log), `filter.<driver>.clean` and
//! `.smudge` and `.process` (status, diff, add, checkout), `merge.<driver>.driver`, `core.sshCommand`
//! and `core.gitProxy` (fetch, push), `remote.<name>.uploadpack` and `.receivepack`,
//! `credential.helper`, `gpg.program` (signatures),
//! `protocol.ext.allow` (the `ext::` transport), and `submodule.<name>.update`.
//!
//! The command-line `-c` scope outranks every file, so this crate pins the fixed keys there and, for
//! the three driver families whose names are the agent's to choose, reads the names from the
//! effective config and blanks each program. `--no-ext-diff` and `--no-textconv` go after
//! `diff`, `show` and `log`. The environment drops `GIT_EXTERNAL_DIFF` and the variables that add
//! config or move a config file, and sets `GIT_PAGER=cat`; the system config is read (see `FIXED_ENV`).
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
    "gpg.ssh.defaultKeyCommand=",
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

/// Environment variables the command must not inherit: `GIT_EXTERNAL_DIFF` names a program for
/// diff, and the others add config or move the system or global file to another path.
pub const REMOVED_ENV: &[&str] =
    &["GIT_EXTERNAL_DIFF", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_COUNT"];

/// Prefixes of the numbered config variables (`GIT_CONFIG_KEY_0`, `GIT_CONFIG_VALUE_0`…), also removed.
pub const REMOVED_ENV_PREFIXES: &[&str] = &["GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"];

/// Environment variables the command sets. `GIT_PROXY_COMMAND` is set empty rather than removed:
/// `core.gitProxy` is first-value-wins, so a `-c` pair cannot replace the checkout's own value,
/// but git reads no `core.gitProxy` at all once the variable is set, and an empty one means none.
///
/// `GIT_CONFIG_NOSYSTEM` is not set: the system config is trusted like the global one, since only
/// an administrator can write it (a per-user Git for Windows install keeps it in the user's own
/// install folder: the user's, not the checkout's), and `REMOVED_ENV` keeps a variable from moving
/// it elsewhere. It holds settings the host's git must keep: Git for Windows sets
/// `core.autocrlf=true` there (without it a file `git stash pop` wrote with CRLF diffs whole
/// against `HEAD`), and its git-lfs filter. A program it names loses to the `-c` pairs as well.
pub const FIXED_ENV: &[(&str, &str)] = &[
    ("GIT_PAGER", "cat"),
    ("GIT_TERMINAL_PROMPT", "0"),
    ("GIT_OPTIONAL_LOCKS", "0"),
    ("GIT_PROXY_COMMAND", ""),
];

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
    let numbered: Vec<OsString> = std::env::vars_os()
        .map(|(name, _)| name)
        .chain(cmd.get_envs().map(|(name, _)| name.to_os_string()))
        .filter(|name| name.to_str().is_some_and(|n| REMOVED_ENV_PREFIXES.iter().any(|p| n.starts_with(p))))
        .collect();
    for name in numbered {
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

    /// A repository whose config names a program for every key the module neutralizes (not hooks: see `hooks_run_by_the_owners_choice`).
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
            "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tfsmonitor = {fsmon}\n\tpager = {pager}\n\tsshCommand = {ssh}\n\
             [diff]\n\texternal = {ext}\n[diff \"evil\"]\n\ttextconv = {textconv}\n\tcommand = {ext}\n\
             [filter \"evil\"]\n\tclean = {clean}\n\tsmudge = {smudge}\n\
             [credential]\n\thelper = {cred}\n[remote \"origin\"]\n\turl = {repo}\n\tuploadpack = {up}\n\treceivepack = {up}\n",
            repo = repo.display(),
        );
        fs::write(repo.join(".git/config"), cfg).unwrap();
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
    fn the_system_config_is_read_so_line_endings_follow_it() {
        // Git for Windows keeps core.autocrlf=true in its system config; a hardened diff that
        // skipped it saw every CRLF file `git stash pop` wrote as changed in full.
        let root = scratch("system");
        let repo = root.join("repo");
        fs::create_dir_all(&repo).unwrap();
        plain(&repo, &["init", "-q"]);
        let system = root.join("gitconfig");
        fs::write(&system, "[core]\n\tautocrlf = true\n").unwrap();
        let mut cmd = command(&repo, &["config", "--get", "core.autocrlf"]);
        cmd.env("GIT_CONFIG_SYSTEM", &system).env_remove("GIT_CONFIG_NOSYSTEM");
        let out = run(cmd, DEFAULT_TIMEOUT).unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "true");
        assert!(FIXED_ENV.iter().all(|(name, _)| *name != "GIT_CONFIG_NOSYSTEM"));
    }

    /// A repository with a changed file, and a system-config file naming a program for fsmonitor,
    /// sshCommand, the credential helper, the pager and diff.external.
    fn hostile_system(name: &str) -> (std::path::PathBuf, std::path::PathBuf, std::path::PathBuf) {
        let root = scratch(name);
        let repo = root.join("repo");
        fs::create_dir_all(&repo).unwrap();
        let marker = root.join("marker");
        plain(&repo, &["init", "-q"]);
        fs::write(repo.join("a.txt"), "one\n").unwrap();
        plain(&repo, &["add", "-A"]);
        plain(&repo, &["commit", "-q", "-m", "init"]);
        fs::write(repo.join("a.txt"), "one\ntwo changed\n").unwrap();
        let fsmon = script(&root, "fsmonitor", &marker);
        let ssh = script(&root, "ssh", &marker);
        let cred = script(&root, "cred", &marker);
        let pager = script(&root, "pager", &marker);
        let ext = script(&root, "external", &marker);
        let system = root.join("system-gitconfig");
        fs::write(
            &system,
            format!(
                "[core]\n\tfsmonitor = {fsmon}\n\tsshCommand = {ssh}\n\tpager = {pager}\n[credential]\n\thelper = {cred}\n[diff]\n\texternal = {ext}\n"
            ),
        )
        .unwrap();
        (repo, system, marker)
    }

    const SYSTEM_PINS: &[(&str, &str)] = &[
        ("core.fsmonitor", "false"),
        ("core.sshCommand", "ssh"),
        ("core.pager", "cat"),
        ("credential.helper", ""),
        ("diff.external", ""),
    ];

    #[test]
    fn a_system_config_that_names_programs_loses_to_the_pins() {
        // Control: plain git runs the system file's fsmonitor.
        let (repo, system, marker) = hostile_system("system-control");
        let _ = Command::new("git").arg("-C").arg(&repo).args(["status", "--porcelain"]).env("GIT_CONFIG_SYSTEM", &system).env_remove("GIT_CONFIG_NOSYSTEM").output();
        assert!(marker.exists(), "the control run should have executed the system config's fsmonitor");

        // The file read as the system config (the caller sets the variable after `command`).
        let (repo, system, marker) = hostile_system("system-proof");
        for args in [vec!["status", "--porcelain"], vec!["diff"], vec!["log", "-p", "-1"]] {
            let mut cmd = command(&repo, &args);
            cmd.env("GIT_CONFIG_SYSTEM", &system).env_remove("GIT_CONFIG_NOSYSTEM");
            let out = run(cmd, DEFAULT_TIMEOUT).unwrap();
            assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
        }
        for (key, pin) in SYSTEM_PINS {
            let mut cmd = command(&repo, &["config", "--get", key]);
            cmd.env("GIT_CONFIG_SYSTEM", &system).env_remove("GIT_CONFIG_NOSYSTEM");
            let out = run(cmd, DEFAULT_TIMEOUT).unwrap();
            assert_eq!(String::from_utf8_lossy(&out.stdout).trim_end_matches('\n'), *pin, "{key}");
        }
        let ran = fs::read_to_string(&marker).unwrap_or_default();
        assert!(ran.is_empty(), "a program the system config names ran: {ran}");
    }

    #[test]
    fn an_inherited_variable_cannot_move_or_add_config() {
        let (repo, system, marker) = hostile_system("system-env");
        let fsmon = script(repo.parent().unwrap(), "numbered", &marker);
        // Set on the command before the hardening, as an inherited environment would be.
        let mut cmd = Command::new("git");
        cmd.env("GIT_CONFIG_SYSTEM", &system)
            .env("GIT_CONFIG_GLOBAL", &system)
            .env("GIT_CONFIG_COUNT", "1")
            .env("GIT_CONFIG_KEY_0", "core.fsmonitor")
            .env("GIT_CONFIG_VALUE_0", &fsmon)
            .env("GIT_CONFIG_PARAMETERS", format!("'core.fsmonitor'='{fsmon}'"))
            .env_remove("GIT_CONFIG_NOSYSTEM");
        cmd.arg("-C").arg(&repo).args(hardened_args(Some(&repo), &["status", "--porcelain"]));
        apply_env(&mut cmd);
        for (name, value) in cmd.get_envs() {
            let name = name.to_string_lossy();
            assert!(!name.starts_with("GIT_CONFIG_") || value.is_none(), "{name} reaches git");
        }
        let out = run(cmd, DEFAULT_TIMEOUT).unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let ran = fs::read_to_string(&marker).unwrap_or_default();
        assert!(ran.is_empty(), "a program an inherited variable named ran: {ran}");
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

    /// A repository whose own config names a `git://` proxy and an SSH signing-key command, both
    /// of which git reads only from the first value it meets (so a `-c` pair alone does not win).
    fn proxy_and_key_command(name: &str) -> (std::path::PathBuf, std::path::PathBuf) {
        let root = scratch(name);
        let repo = root.join("repo");
        fs::create_dir_all(&repo).unwrap();
        let marker = root.join("marker");
        plain(&repo, &["init", "-q"]);
        let proxy = script(&root, "proxy", &marker);
        let key = script(&root, "keycommand", &marker);
        for (k, v) in [
            ("core.gitProxy", proxy.as_str()),
            ("gpg.format", "ssh"),
            ("commit.gpgSign", "true"),
            ("gpg.ssh.defaultKeyCommand", key.as_str()),
            ("user.name", "t"),
            ("user.email", "t@t"),
        ] {
            plain(&repo, &["config", k, v]);
        }
        (repo, marker)
    }

    #[test]
    fn a_git_proxy_and_a_signing_key_command_in_the_checkout_never_run() {
        // Control: plain git runs both programs from this config.
        let (repo, marker) = proxy_and_key_command("proxy-control");
        let plain_git = |args: &[&str]| {
            let _ = Command::new("git").arg("-C").arg(&repo).args(args).env("GIT_CONFIG_NOSYSTEM", "1").env_remove("GIT_PROXY_COMMAND").output();
        };
        plain_git(&["-c", "core.gitProxy=", "ls-remote", "git://127.0.0.1:1/x"]);
        plain_git(&["commit", "-q", "--allow-empty", "-m", "m"]);
        let ran = fs::read_to_string(&marker).unwrap_or_default();
        assert!(ran.contains("ran-proxy") && ran.contains("ran-keycommand"), "control ran: {ran:?}");

        let (repo, marker) = proxy_and_key_command("proxy");
        let _ = output(&repo, &["ls-remote", "git://127.0.0.1:1/x"]);
        let _ = output(&repo, &["commit", "-q", "--allow-empty", "-m", "m"]);
        let ran = fs::read_to_string(&marker).unwrap_or_default();
        assert!(ran.is_empty(), "a configured program ran: {ran}");
    }

    #[test]
    fn hooks_run_by_the_owners_choice() {
        // Owner decision 2026-10-28 ("allow all for now"): repository hooks run from host-side git.
        // The known risk (an agent-written hook runs on the host) is in docs/dev/host-safety.md.
        let root = scratch("hooks");
        let repo = root.join("repo");
        fs::create_dir_all(&repo).unwrap();
        plain(&repo, &["init", "-q"]);
        let marker = root.join("marker");
        let hooks = repo.join(".git/hooks");
        fs::create_dir_all(&hooks).unwrap();
        script(&hooks, "pre-commit", &marker);
        fs::write(repo.join("a.txt"), "x\n").unwrap();
        output(&repo, &["add", "-A"]).unwrap();
        let out = output(&repo, &["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "m"]).unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        assert!(fs::read_to_string(&marker).unwrap_or_default().contains("ran-pre-commit"));
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
