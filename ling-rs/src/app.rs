//! `ling app`: opens Mightling's desktop window, `ling-app`.
//!
//! Upstream `codex app` opens the upstream vendor's closed-source desktop app, and is compiled only
//! on macOS and Windows. Mightling's own window is the Electron app in `desktop/electron`
//! (`ling-app`, specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md), so the launcher answers `app`
//! itself before Codex parses the command line. It does what `ling-admin desktop run` does before
//! opening the window, minus building it: empty the webview's HTTP cache.
//!
//! The app has one window with two views on the app's own `ling app-server`. Ask (the former
//! Chat, which showed the Onyx web UI until 2026-10-08 and was a window of its own on `ling web`
//! until 2026-10-09, specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6) is questions with no project;
//! `ling app` alone opens it. Work is the coding agent (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md):
//! `ling app --work`, `ling app <folder>` and `ling app --thread <id>` open it. Neither needs
//! Onyx, and both wait for the model server on the window's start-up screen rather than refusing.
//!
//! The app's output goes to `$CODEX_HOME/log/ling-app.log`, and `ling app` watches it for its first
//! seconds: when Chromium cannot start its sandbox (Ubuntu refuses a user namespace to a program
//! without an AppArmor profile) its zygote aborts before the app's own code runs, and with the
//! output thrown away `ling app` returned as if the window had opened (1.7 Linux tests).

use std::path::Path;
use std::path::PathBuf;
use std::process::Child;
use std::process::Command;
use std::process::ExitStatus;
use std::process::Stdio;
use std::time::Duration;
use std::time::Instant;

/// The executable and the desktop entry `ling-admin desktop install` registers for it.
const EXECUTABLE: &str = "ling-app";
const DESKTOP_ENTRY: &str = "ling-app.desktop";

/// The app's data directory, named after its identifier (`desktop/electron/app.json`). Its HTTP
/// cache is emptied when Ask is opened, so a window never shows a page from before an upgrade.
/// Chromium's `Cookies` file beside it is left alone.
const WEBVIEW_DATA_DIR: &str = "dev.dreamference.mightling";
const WEBVIEW_CACHE_DIR: &str = "Cache";

/// The app's stdout and stderr, under `$CODEX_HOME/log`, replaced at each `ling app`.
const LOG_FILE: &str = "ling-app.log";

/// How long `ling app` watches the app it started. A sandbox failure ends it within a second; a
/// second `ling app` hands its arguments to the open window and exits 0 just as fast, which is
/// success.
const STARTUP_WATCH: Duration = Duration::from_secs(3);

/// How many of the log's last lines a failure shows.
const TAIL_LINES: usize = 12;

/// What Chromium prints when it cannot start its sandbox.
const SANDBOX_FAILURES: [&str; 3] = ["No usable sandbox", "zygote_host", "setuid sandbox"];

/// Which view `ling app` opens the window on: Ask, or Work with the arguments `ling-app` takes
/// for it (`--work`, `--cwd <folder>`, `--thread <id>`).
#[derive(Debug, PartialEq)]
pub enum Window {
    Ask,
    Work(Vec<String>),
}

/// Reads `ling app`'s arguments. A folder must exist; it is passed on as an absolute path, since
/// `ling-app` starts in its own working directory.
pub fn window(args: &[String]) -> Result<Window, String> {
    let mut work = false;
    let mut ask = false;
    let mut forwarded = Vec::new();
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        match arg.as_str() {
            "--work" => work = true,
            // `--chat` is the name Ask had while it showed the Onyx web UI.
            "--ask" | "--chat" => ask = true,
            "--thread" => {
                let id = rest.next().ok_or("--thread needs a thread id")?;
                forwarded.extend(["--thread".to_string(), id.clone()]);
            }
            flag if flag.starts_with('-') => return Err(format!("unknown option {flag}")),
            folder => {
                let path = std::fs::canonicalize(folder).map_err(|error| format!("{folder}: {error}"))?;
                if !path.is_dir() {
                    return Err(format!("{folder} is not a folder"));
                }
                forwarded.extend(["--cwd".to_string(), path.display().to_string()]);
            }
        }
    }
    if ask && (work || !forwarded.is_empty()) {
        return Err("--ask opens Ask, which takes no folder or thread".to_string());
    }
    if work || !forwarded.is_empty() {
        forwarded.insert(0, "--work".to_string());
        return Ok(Window::Work(forwarded));
    }
    Ok(Window::Ask)
}

/// Handles `ling app [args...]` and returns the process exit code.
pub async fn open(args: &[String]) -> i32 {
    if args.iter().any(|arg| arg == "-h" || arg == "--help") {
        println!("Open Mightling's desktop window (ling-app).\n");
        println!("Usage: ling app [--ask]                Ask: questions with no project");
        println!("       ling app --work [<folder>]      the coding agent (Work), on a project folder");
        println!("       ling app --thread <id>          a thread in Work");
        return 0;
    }
    let window = match window(args) {
        Ok(window) => window,
        Err(error) => {
            eprintln!("❌ ling app: {error}");
            return 2;
        }
    };

    let Some(executable) = find_executable() else {
        eprintln!("❌ ling-app is not installed.");
        eprintln!("💡 Build and register it with: ling-admin desktop build");
        return 1;
    };
    // Nothing to check first: the app starts its own app-server, and says in its window
    // what it is waiting for.
    if window == Window::Ask {
        if let Some(home) = home_dir() {
            let _ = std::fs::remove_dir_all(
                home.join(".local/share").join(WEBVIEW_DATA_DIR).join(WEBVIEW_CACHE_DIR),
            );
        }
    }

    let forwarded = match &window {
        Window::Work(forwarded) => forwarded.clone(),
        Window::Ask => Vec::new(),
    };
    let log = log_path();
    let mut child = match launch(&executable, &forwarded, log.as_deref()) {
        Ok(child) => child,
        Err(error) => {
            eprintln!("❌ Could not start {}: {error}", executable.display());
            return 1;
        }
    };
    match watch(&mut child, STARTUP_WATCH).await {
        Some(status) if !status.success() => {
            let text = log.as_deref().and_then(|path| std::fs::read_to_string(path).ok()).unwrap_or_default();
            for line in failure_lines(status.code(), &text, log.as_deref()) {
                eprintln!("{line}");
            }
            1
        }
        _ => 0,
    }
}

/// `$CODEX_HOME/log/ling-app.log`, its folder created; None when there is no `CODEX_HOME`.
fn log_path() -> Option<PathBuf> {
    let dir = ling_node_locator::codex_home()?.join("log");
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join(LOG_FILE))
}

/// Starts the app detached from this terminal, its output in `log` (replaced), or nowhere.
///
/// A file, not a pipe: the app outlives `ling app`, and a pipe whose reader is gone would fail its
/// writes.
pub fn launch(executable: &Path, args: &[String], log: Option<&Path>) -> std::io::Result<Child> {
    let mut command = Command::new(executable);
    command.args(args).stdin(Stdio::null());
    match log.and_then(|path| std::fs::File::create(path).ok()) {
        Some(file) => {
            let stderr = file.try_clone()?;
            command.stdout(Stdio::from(file)).stderr(Stdio::from(stderr));
        }
        None => {
            command.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }
    // Its own process group, so a Ctrl-C in this terminal does not close the window.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(crate::DETACHED_PROCESS_FLAGS);
    }
    command.spawn()
}

/// How the app ended if it ended within `limit`; None while it still runs, which is a window open.
pub async fn watch(child: &mut Child, limit: Duration) -> Option<ExitStatus> {
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status),
            Ok(None) if started.elapsed() < limit => tokio::time::sleep(Duration::from_millis(100)).await,
            _ => return None,
        }
    }
}

/// What `ling app` prints when the app ended as it started: how, the log's last lines, and for a
/// sandbox failure what fixes it.
pub fn failure_lines(code: Option<i32>, log_text: &str, log: Option<&Path>) -> Vec<String> {
    let how = match code {
        Some(code) => format!("with code {code}"),
        None => "on a signal".to_string(),
    };
    let mut lines = vec![format!("❌ ling-app stopped as it started ({how}); no window opened.")];
    let tail: Vec<&str> = log_text.lines().filter(|line| !line.trim().is_empty()).collect();
    for line in &tail[tail.len().saturating_sub(TAIL_LINES)..] {
        lines.push(format!("   {line}"));
    }
    if SANDBOX_FAILURES.iter().any(|marker| log_text.contains(marker)) {
        lines.push("💡 Chromium could not start its sandbox: the app needs an AppArmor profile that allows it a user namespace.".to_string());
        lines.push("   From a checkout: ling-admin desktop install. An unpacked build: run install-sandbox-profile.sh beside the binary.".to_string());
    }
    if let Some(log) = log {
        lines.push(format!("   The app's output: {}", log.display()));
    }
    lines
}

/// `ling-app` on PATH, else the binary the registered desktop entry launches.
///
/// The desktop entry is where `ling-admin desktop` records the built binary's absolute path, so
/// it finds a checkout's build without this crate knowing where the checkout is.
pub fn find_executable() -> Option<PathBuf> {
    if let Some(on_path) = std::env::var_os("PATH").and_then(|path| {
        std::env::split_paths(&path)
            .map(|dir| dir.join(EXECUTABLE))
            .find(|candidate| is_executable(candidate))
    }) {
        return Some(on_path);
    }
    let data_home = std::env::var_os("XDG_DATA_HOME")
        .filter(|dir| !dir.is_empty())
        .map(PathBuf::from)
        .or_else(|| home_dir().map(|home| home.join(".local/share")))?;
    let entry = std::fs::read_to_string(data_home.join("applications").join(DESKTOP_ENTRY)).ok()?;
    exec_path(&entry).filter(|path| is_executable(path))
}

/// The program named on a desktop entry's `Exec=` line, without its arguments or field codes.
pub fn exec_path(entry: &str) -> Option<PathBuf> {
    let exec = entry
        .lines()
        .find_map(|line| line.trim().strip_prefix("Exec="))?;
    let program = exec.split_whitespace().next()?.trim_matches('"');
    (!program.is_empty()).then(|| PathBuf::from(program))
}

fn is_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .is_ok_and(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

fn home_dir() -> Option<PathBuf> {
    ling_node_locator::home_dir()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_exec_line_names_the_program() {
        let entry = "[Desktop Entry]\nName=Mightling\nExec=/opt/ling/ling-app %U\nIcon=ling-app\n";
        assert_eq!(exec_path(entry), Some(PathBuf::from("/opt/ling/ling-app")));
        assert_eq!(exec_path("Exec=\"/opt/ling-app\"\n"), Some(PathBuf::from("/opt/ling-app")));
        assert_eq!(exec_path("[Desktop Entry]\nName=x\n"), None);
    }

    fn args(text: &str) -> Vec<String> {
        text.split_whitespace().map(String::from).collect()
    }

    #[test]
    fn mightling_app_alone_opens_ask() {
        assert_eq!(window(&[]), Ok(Window::Ask));
        assert_eq!(window(&args("--ask")), Ok(Window::Ask));
        assert_eq!(window(&args("--chat")), Ok(Window::Ask));
    }

    #[test]
    fn a_folder_or_a_thread_opens_work() {
        assert_eq!(window(&args("--work")), Ok(Window::Work(args("--work"))));
        assert_eq!(window(&args("--thread t1")), Ok(Window::Work(args("--work --thread t1"))));
        let here = std::fs::canonicalize(".").map(|p| p.display().to_string()).unwrap_or_default();
        assert_eq!(window(&args(". --work")), Ok(Window::Work(vec!["--work".into(), "--cwd".into(), here])));
    }

    #[test]
    fn a_sandbox_failure_says_what_fixes_it() {
        let log = "[1:1:FATAL:zygote_host_impl_linux.cc(127)] No usable sandbox! If you are running on Ubuntu 23.10+ ...\n";
        let lines = failure_lines(None, log, Some(Path::new("/h/.mightling/log/ling-app.log")));
        assert_eq!(lines[0], "❌ ling-app stopped as it started (on a signal); no window opened.");
        assert!(lines[1].contains("No usable sandbox!"));
        assert!(lines.iter().any(|line| line.contains("AppArmor profile")));
        assert!(lines.iter().any(|line| line.contains("ling-admin desktop install")));
        assert_eq!(lines.last().map(String::as_str), Some("   The app's output: /h/.mightling/log/ling-app.log"));
    }

    #[test]
    fn another_failure_shows_the_logs_last_lines_and_no_sandbox_hint() {
        let log: String = (1..=20).map(|n| format!("line {n}\n")).collect();
        let lines = failure_lines(Some(3), &log, None);
        assert_eq!(lines[0], "❌ ling-app stopped as it started (with code 3); no window opened.");
        assert_eq!(lines[1], "   line 9");
        assert_eq!(lines.last().map(String::as_str), Some("   line 20"));
        assert!(!lines.iter().any(|line| line.contains("AppArmor")));
    }

    /// A stand-in app: a script `/bin/sh` runs. Not made executable and run itself, since another
    /// test thread's fork can still hold the file open for writing (ETXTBSY, "Text file busy").
    #[cfg(unix)]
    fn stand_in(name: &str, body: &str) -> (PathBuf, PathBuf) {
        let dir = std::env::temp_dir().join(format!("ling-app-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let script = dir.join("ling-app.sh");
        std::fs::write(&script, format!("{body}\n")).unwrap();
        (script, dir.join(LOG_FILE))
    }

    #[cfg(unix)]
    fn sh(script: &Path, rest: &[&str]) -> (PathBuf, Vec<String>) {
        let mut args = vec![script.display().to_string()];
        args.extend(rest.iter().map(|arg| arg.to_string()));
        (PathBuf::from("/bin/sh"), args)
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn an_app_that_dies_at_start_is_caught_with_its_output() {
        let (script, log) = stand_in("dies", "echo 'No usable sandbox!' >&2; exit 1");
        let (program, args) = sh(&script, &[]);
        let mut child = launch(&program, &args, Some(&log)).unwrap();
        let status = watch(&mut child, Duration::from_secs(5)).await.expect("it ended");
        assert!(!status.success());
        assert!(std::fs::read_to_string(&log).unwrap().contains("No usable sandbox!"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn an_app_still_running_after_the_watch_is_a_window() {
        let (script, log) = stand_in("runs", "echo started; sleep 2");
        let (program, args) = sh(&script, &["--work"]);
        let mut child = launch(&program, &args, Some(&log)).unwrap();
        assert!(watch(&mut child, Duration::from_millis(300)).await.is_none());
        let _ = child.kill();
        let _ = child.wait();
        assert_eq!(std::fs::read_to_string(&log).unwrap(), "started\n");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_second_instance_handing_off_and_exiting_zero_is_success() {
        let (script, log) = stand_in("hands-off", "exit 0");
        let (program, args) = sh(&script, &[]);
        let mut child = launch(&program, &args, Some(&log)).unwrap();
        assert!(watch(&mut child, Duration::from_secs(5)).await.is_some_and(|status| status.success()));
    }

    #[test]
    fn what_mightling_app_cannot_do_is_refused() {
        assert!(window(&args("--thread")).is_err());
        assert!(window(&args("--yolo")).is_err());
        assert!(window(&args("/no/such/folder/anywhere")).is_err());
        assert!(window(&args("--chat --work")).is_err());
        assert!(window(&args("--ask --thread t1")).is_err());
        assert!(window(&args("Cargo.toml")).is_err() || !std::path::Path::new("Cargo.toml").exists());
    }
}
