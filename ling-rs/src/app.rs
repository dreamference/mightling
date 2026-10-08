//! `ling app`: opens Mightling's desktop window, `ling-app`.
//!
//! Upstream `codex app` opens the upstream vendor's closed-source desktop app, and is compiled only
//! on macOS and Windows. Mightling's own window is the Electron app in `desktop/electron`
//! (`ling-app`, specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md), so the launcher answers `app`
//! itself before Codex parses the command line. It does what `ling-admin desktop run` does before
//! opening the window, minus building it: empty the webview's HTTP cache.
//!
//! The app has two windows. Ask (the former Chat, which showed the Onyx web UI until 2026-10-08)
//! is the Mightling UI on `ling web`, which the app starts itself when nothing answers
//! (specs/DREAMFERENCE_MIGHTLING_ASK.md §10); `ling app` alone opens it. Work is the coding agent
//! on `ling app-server` (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md): `ling app --work`,
//! `ling app <folder>` and `ling app --thread <id>` open it. Neither needs Onyx, and both wait for
//! the model server on their own start-up screen rather than refusing.

use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;

/// The executable and the desktop entry `ling-admin desktop install` registers for it.
const EXECUTABLE: &str = "ling-app";
const DESKTOP_ENTRY: &str = "ling-app.desktop";

/// The app's data directory, named after its identifier (`desktop/electron/app.json`). Its HTTP
/// cache is emptied when Ask is opened, so a window never shows a page from before an upgrade.
/// Chromium's `Cookies` file beside it is left alone.
const WEBVIEW_DATA_DIR: &str = "dev.dreamference.mightling";
const WEBVIEW_CACHE_DIR: &str = "Cache";

/// Which window `ling app` opens: Ask (the Mightling UI on `ling web`), or Work with the
/// arguments `ling-app` takes for it (`--work`, `--cwd <folder>`, `--thread <id>`).
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
        return Err("--ask opens the Ask window, which takes no folder or thread".to_string());
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
        println!("Usage: ling app [--ask]                Ask: questions with no project, on ling web");
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
    // Nothing to check first: the app starts `ling web` for Ask itself, and says in its window
    // what it is waiting for.
    if window == Window::Ask {
        if let Some(home) = home_dir() {
            let _ = std::fs::remove_dir_all(
                home.join(".local/share").join(WEBVIEW_DATA_DIR).join(WEBVIEW_CACHE_DIR),
            );
        }
    }

    let mut command = Command::new(&executable);
    if let Window::Work(forwarded) = &window {
        command.args(forwarded);
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
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
    match command.spawn() {
        Ok(_) => 0,
        Err(error) => {
            eprintln!("❌ Could not start {}: {error}", executable.display());
            1
        }
    }
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
    fn what_mightling_app_cannot_do_is_refused() {
        assert!(window(&args("--thread")).is_err());
        assert!(window(&args("--yolo")).is_err());
        assert!(window(&args("/no/such/folder/anywhere")).is_err());
        assert!(window(&args("--chat --work")).is_err());
        assert!(window(&args("--ask --thread t1")).is_err());
        assert!(window(&args("Cargo.toml")).is_err() || !std::path::Path::new("Cargo.toml").exists());
    }
}
