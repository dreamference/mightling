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

use std::ffi::OsString;
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

/// The app's data directory's name, its identifier (`desktop/electron/app.json`); where it is, is
/// [`data_dir_for`]. Its HTTP cache is emptied when Ask is opened, so a window never shows a page
/// from before an upgrade. Chromium's `Cookies` file beside it is left alone.
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

/// Setuid sandbox helpers that Chromium-based programs install as root, by their default paths.
/// Chromium uses one in place of its user-namespace sandbox when `CHROME_DEVEL_SANDBOX` names it
/// and the app's binary belongs to the user (an unpacked build); the protocol between them has not
/// changed in years. This is Chromium's own documented route for builds without a profile
/// (docs/security/apparmor-userns-restrictions.md, "Option 3"), measured on 2026-10-22: an
/// unprofiled copy of the app started with Google Chrome's helper, its zygote in new PID and
/// network namespaces and its renderer under seccomp (specs/DREAMFERENCE_MIGHTLING_NO_SUDO.md).
const SETUID_HELPERS: [(&str, &str); 9] = [
    ("Google Chrome", "/opt/google/chrome/chrome-sandbox"),
    ("Microsoft Edge", "/opt/microsoft/msedge/msedge-sandbox"),
    ("Brave", "/opt/brave.com/brave/chrome-sandbox"),
    ("Vivaldi", "/opt/vivaldi/vivaldi-sandbox"),
    ("Chromium", "/usr/lib/chromium/chrome-sandbox"),
    ("Visual Studio Code", "/usr/share/code/chrome-sandbox"),
    ("Slack", "/usr/lib/slack/chrome-sandbox"),
    ("Discord", "/usr/share/discord/chrome-sandbox"),
    ("Signal", "/opt/Signal/chrome-sandbox"),
];

/// The variable that hands Chromium a setuid sandbox helper.
const HELPER_VARIABLE: &str = "CHROME_DEVEL_SANDBOX";

/// The script `forge.config.js` copies beside an unpacked build's binary.
const PROFILE_SCRIPT: &str = "install-sandbox-profile.sh";

/// How the app's Chromium sandbox can start on this machine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChromiumSandbox {
    /// User namespaces are not restricted here, or a profile lets the binary create one.
    Ready,
    /// No profile, but a root-owned setuid helper is installed and the binary is the user's.
    Helper { owner: String, path: PathBuf },
    /// Neither: the app aborts at start with "No usable sandbox!".
    Missing,
}

/// Decides from the facts: the sysctl, a covering profile, who owns the binary, a helper found.
pub fn chromium_sandbox(restricted: bool, covered: bool, user_owned: bool, helper: Option<(&str, &Path)>) -> ChromiumSandbox {
    if !restricted || covered {
        return ChromiumSandbox::Ready;
    }
    match helper {
        // Chromium reads the variable only for a binary the user owns (setuid_sandbox_host.cc).
        Some((owner, path)) if user_owned => ChromiumSandbox::Helper {
            owner: owner.to_string(),
            path: path.to_path_buf(),
        },
        _ => ChromiumSandbox::Missing,
    }
}

/// What `ling app` says before starting the app, for each case, and what fixes it for good.
pub fn sandbox_lines(sandbox: &ChromiumSandbox, binary: &Path) -> Vec<String> {
    let fix = match binary.parent().map(|dir| dir.join(PROFILE_SCRIPT)).filter(|script| script.is_file()) {
        Some(script) => format!("an administrator runs {} once (--print shows what it does)", script.display()),
        None => "from a checkout, ling-admin desktop install (sudo, once)".to_string(),
    };
    match sandbox {
        ChromiumSandbox::Ready => Vec::new(),
        ChromiumSandbox::Helper { owner, path } => vec![
            format!("💡 No AppArmor profile lets {} create Chromium's sandbox; using {owner}'s setuid sandbox helper instead ({path}).", binary.display(), path = path.display()),
            format!("   For the app's own sandbox, {fix}."),
        ],
        ChromiumSandbox::Missing => vec![
            format!("⚠️ No AppArmor profile lets {} create Chromium's sandbox (kernel.apparmor_restrict_unprivileged_userns=1), and no setuid sandbox helper from a Chromium browser can stand in for it (none is installed, or the binary is not yours, which Chromium requires): the app will stop at start.", binary.display()),
            format!("   The fix: {fix}."),
        ],
    }
}

/// The facts for `executable` on this machine, read without root.
#[cfg(target_os = "linux")]
pub fn chromium_sandbox_now(executable: &Path) -> (PathBuf, ChromiumSandbox) {
    use std::os::unix::fs::MetadataExt;
    let binary = std::fs::canonicalize(executable).unwrap_or_else(|_| executable.to_path_buf());
    let restricted = std::fs::read_to_string(crate::userns::USERNS_SYSCTL).is_ok_and(|value| value.trim() == "1");
    if !restricted {
        return (binary, ChromiumSandbox::Ready);
    }
    let covered = covering_profile(Path::new(crate::userns::APPARMOR_DIR), &binary).is_some();
    // SAFETY: getuid cannot fail.
    let uid = unsafe { libc::getuid() };
    let user_owned = binary.metadata().is_ok_and(|meta| meta.uid() == uid);
    let helper = SETUID_HELPERS.iter().find(|(_, path)| {
        Path::new(path)
            .metadata()
            .is_ok_and(|meta| meta.is_file() && meta.uid() == 0 && meta.mode() & 0o4000 != 0)
    });
    let sandbox = chromium_sandbox(restricted, covered, user_owned, helper.map(|(owner, path)| (*owner, Path::new(*path))));
    (binary, sandbox)
}

/// A profile file in `dir` that lets `binary` create a user namespace: one naming its quoted path
/// (the `.deb`'s, `ling-admin desktop install`'s, the unpacked build's script's) or attaching to it
/// unquoted, with `userns,` in it. The test `DesktopInstaller.covering_profile()` makes.
pub fn covering_profile(dir: &Path, binary: &Path) -> Option<PathBuf> {
    let quoted = format!("\"{}\"", binary.display());
    let attaching = crate::userns::profiles_attaching(dir, binary);
    let mut names: Vec<PathBuf> = std::fs::read_dir(dir).ok()?.filter_map(Result::ok).map(|entry| entry.path()).collect();
    names.sort();
    names.into_iter().find(|path| {
        std::fs::read_to_string(path).is_ok_and(|text| {
            text.contains("userns,") && (text.contains(&quoted) || attaching.contains(path))
        })
    })
}

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
        if let Some(data) = data_dir_for(cfg!(windows), &|name: &str| std::env::var_os(name)) {
            let _ = std::fs::remove_dir_all(data.join(WEBVIEW_CACHE_DIR));
        }
    }

    let forwarded = match &window {
        Window::Work(forwarded) => forwarded.clone(),
        Window::Ask => Vec::new(),
    };
    // Chromium's sandbox, checked before it aborts the app where nothing can catch it.
    #[cfg(target_os = "linux")]
    let helper = {
        let (binary, sandbox) = chromium_sandbox_now(&executable);
        for line in sandbox_lines(&sandbox, &binary) {
            eprintln!("{line}");
        }
        match sandbox {
            ChromiumSandbox::Helper { path, .. } => Some(path),
            _ => None,
        }
    };
    #[cfg(not(target_os = "linux"))]
    let helper: Option<PathBuf> = None;
    let log = log_path();
    let mut child = match launch_with(&executable, &forwarded, log.as_deref(), helper.as_deref()) {
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
    launch_with(executable, args, log, None)
}

/// [`launch`], with a setuid sandbox helper for Chromium when one is to be used.
pub fn launch_with(executable: &Path, args: &[String], log: Option<&Path>, helper: Option<&Path>) -> std::io::Result<Child> {
    let mut command = Command::new(executable);
    command.args(args).stdin(Stdio::null());
    if let Some(helper) = helper {
        command.env(HELPER_VARIABLE, helper);
    }
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
        lines.push("   From a checkout: ling-admin desktop install. An unpacked build: an administrator runs install-sandbox-profile.sh beside the binary.".to_string());
        lines.push("   Without an administrator: ling app uses the setuid sandbox helper of a Chromium browser that ships one (Google Chrome, Edge, Brave), for an unpacked build in your home.".to_string());
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

/// The app's data folder (Electron's `userData`), by the rule the app itself follows
/// (`desktop/electron/src/data-folder.ts`; both are tested against
/// `ling-rs/vectors/app-data-folder.json`): `%LOCALAPPDATA%\<identifier>` on Windows, else
/// `%USERPROFILE%\AppData\Local\<identifier>`; elsewhere `~/.local/share/<identifier>` (`HOME`,
/// else `USERPROFILE`), the Tauri app's folder. `var` reads a variable, so the rule is tested for
/// every platform on any machine: the path is built with the platform's separator, never with
/// `Path::join`, whose separator is the host's.
/// specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md §23.4 W4.
pub fn data_dir_for(windows: bool, var: &dyn Fn(&str) -> Option<OsString>) -> Option<PathBuf> {
    let set = |name: &str| var(name).filter(|value| !value.is_empty());
    if windows {
        if let Some(local) = set("LOCALAPPDATA") {
            return Some(joined(local, true, &[WEBVIEW_DATA_DIR]));
        }
        return set("USERPROFILE").map(|profile| joined(profile, true, &["AppData", "Local", WEBVIEW_DATA_DIR]));
    }
    set("HOME")
        .or_else(|| set("USERPROFILE"))
        .map(|home| joined(home, false, &[".local", "share", WEBVIEW_DATA_DIR]))
}

/// `base` without separators at its end, then `parts`, joined with `\` on Windows and `/` elsewhere.
fn joined(base: OsString, windows: bool, parts: &[&str]) -> PathBuf {
    let separators: &[char] = if windows { &['\\', '/'] } else { &['/'] };
    let mut path = match base.into_string() {
        Ok(text) => OsString::from(text.trim_end_matches(separators)),
        Err(raw) => raw,
    };
    for part in parts {
        path.push(if windows { "\\" } else { "/" });
        path.push(part);
    }
    PathBuf::from(path)
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

    #[test]
    fn the_data_folder_is_the_one_the_app_uses() {
        // The cases the app's own rule is tested against too (desktop/electron/src/data-folder.test.ts),
        // each run here for its platform whatever this machine is.
        let vectors: serde_json::Value =
            serde_json::from_str(include_str!("../vectors/app-data-folder.json")).expect("the cases parse");
        assert_eq!(vectors["identifier"], WEBVIEW_DATA_DIR);
        let cases = vectors["cases"].as_array().expect("cases");
        assert!(cases.len() >= 10);
        for case in cases {
            let windows = case["platform"] == "win32";
            let env = case["env"].as_object().expect("env");
            let var = |name: &str| env.get(name).and_then(|value| value.as_str()).map(OsString::from);
            let expected = case["expected"].as_str().map(PathBuf::from);
            assert_eq!(data_dir_for(windows, &var), expected, "{}", case["name"]);
        }
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
    fn the_chromium_sandbox_is_ready_from_a_profile_and_otherwise_borrows_a_helper() {
        let chrome = Path::new("/opt/google/chrome/chrome-sandbox");
        assert_eq!(chromium_sandbox(false, false, true, None), ChromiumSandbox::Ready);
        assert_eq!(chromium_sandbox(true, true, true, Some(("Google Chrome", chrome))), ChromiumSandbox::Ready);
        assert_eq!(
            chromium_sandbox(true, false, true, Some(("Google Chrome", chrome))),
            ChromiumSandbox::Helper { owner: "Google Chrome".into(), path: chrome.into() }
        );
        // Chromium ignores the variable for a binary root owns (an installed package's).
        assert_eq!(chromium_sandbox(true, false, false, Some(("Google Chrome", chrome))), ChromiumSandbox::Missing);
        assert_eq!(chromium_sandbox(true, false, true, None), ChromiumSandbox::Missing);
    }

    #[test]
    fn each_case_says_what_it_does_and_never_turns_the_sandbox_off() {
        let binary = Path::new("/home/u/.local/opt/Mightling/Mightling");
        assert!(sandbox_lines(&ChromiumSandbox::Ready, binary).is_empty());
        let helper = ChromiumSandbox::Helper { owner: "Google Chrome".into(), path: "/opt/google/chrome/chrome-sandbox".into() };
        let lines = sandbox_lines(&helper, binary);
        assert!(lines[0].contains("Google Chrome's setuid sandbox helper") && lines[0].contains("/opt/google/chrome/chrome-sandbox"));
        let missing = sandbox_lines(&ChromiumSandbox::Missing, binary);
        assert!(missing[0].contains("will stop at start") && missing[1].contains("ling-admin desktop install"));
        assert!(!lines.iter().chain(&missing).any(|line| line.contains("--no-sandbox")));
    }

    #[test]
    fn a_profile_covers_the_binary_it_names() {
        let dir = std::env::temp_dir().join(format!("ling-app-profiles-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("mightling-unpacked"), "profile mightling-unpacked \"/home/u/m/Mightling\" flags=(unconfined) {\n  userns,\n}\n").unwrap();
        std::fs::write(dir.join("obsidian"), "profile obsidian /opt/Obsidian/obsidian flags=(unconfined) {\n  userns,\n}\n").unwrap();
        std::fs::write(dir.join("named-only"), "# \"/home/u/x/Mightling\" without the rule\n").unwrap();
        assert_eq!(covering_profile(&dir, Path::new("/home/u/m/Mightling")), Some(dir.join("mightling-unpacked")));
        assert_eq!(covering_profile(&dir, Path::new("/opt/Obsidian/obsidian")), Some(dir.join("obsidian")));
        assert_eq!(covering_profile(&dir, Path::new("/home/u/x/Mightling")), None);
        assert_eq!(covering_profile(&dir, Path::new("/home/u/m/Mightling2")), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn the_helper_reaches_the_app() {
        let (script, log) = stand_in("helper", "echo \"helper=$CHROME_DEVEL_SANDBOX\"");
        let (program, args) = sh(&script, &[]);
        let mut child = launch_with(&program, &args, Some(&log), Some(Path::new("/opt/x/chrome-sandbox"))).unwrap();
        assert!(watch(&mut child, Duration::from_secs(5)).await.is_some());
        assert_eq!(std::fs::read_to_string(&log).unwrap(), "helper=/opt/x/chrome-sandbox\n");
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
