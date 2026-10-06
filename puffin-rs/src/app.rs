//! `puffin app`: opens Puffin's desktop window, `puffin-app`.
//!
//! Upstream `codex app` opens OpenAI's closed-source desktop app, and is compiled only on macOS and
//! Windows. Puffin's own window is the Tauri shell in `desktop/` (`puffin-app`), which renders the
//! local Onyx web UI, so the launcher answers `app` itself before Codex parses the command line.
//! It does what `puffin-admin desktop run` does before opening the window, minus building it:
//! check Onyx is answering and empty the webview's HTTP cache.
//!
//! The window has a second half, Work: the coding agent on `puffin app-server`
//! (specs/DREAMFERENCE_PUFFIN_DESKTOP.md). `puffin app --work`, `puffin app <folder>` and
//! `puffin app --thread <id>` open it; `puffin app` alone still opens the chat, until Work passes
//! its Phase 1 acceptance. Work does not need Onyx, and waits for the model server on its own
//! start-up screen rather than refusing.

use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;

/// Where the window's Onyx deployment answers; `puffin-app` is pointed at the same address.
const ONYX_WEB_URL: &str = "http://localhost:3000";

/// The executable and the desktop entry `puffin-admin desktop install` registers for it.
const EXECUTABLE: &str = "puffin-app";
const DESKTOP_ENTRY: &str = "puffin-app.desktop";

/// The webview's data directory is named after the Tauri identifier in `tauri.conf.json`. Its
/// HTTP cache is emptied on every launch: Onyx serves stylesheets as `immutable` under names that
/// never change, so a cached copy would hide the last `puffin-admin puffin configure`. The sibling
/// `cookies` file is left alone, which is what keeps the user signed in.
const WEBVIEW_DATA_DIR: &str = "dev.dreamference.puffin";
const WEBVIEW_CACHE_DIR: &str = "WebKitCache";

/// Which window `puffin app` opens: Chat (the Onyx web UI), or Work with the arguments
/// `puffin-app` takes for it (`--work`, `--cwd <folder>`, `--thread <id>`).
#[derive(Debug, PartialEq)]
pub enum Window {
    Chat,
    Work(Vec<String>),
}

/// Reads `puffin app`'s arguments. A folder must exist; it is passed on as an absolute path, since
/// `puffin-app` starts in its own working directory.
pub fn window(args: &[String]) -> Result<Window, String> {
    let mut work = false;
    let mut chat = false;
    let mut forwarded = Vec::new();
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        match arg.as_str() {
            "--work" => work = true,
            "--chat" => chat = true,
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
    if chat && (work || !forwarded.is_empty()) {
        return Err("--chat opens the chat window, which takes no folder or thread".to_string());
    }
    if work || !forwarded.is_empty() {
        forwarded.insert(0, "--work".to_string());
        return Ok(Window::Work(forwarded));
    }
    Ok(Window::Chat)
}

/// Handles `puffin app [args...]` and returns the process exit code.
pub async fn open(args: &[String]) -> i32 {
    if args.iter().any(|arg| arg == "-h" || arg == "--help") {
        println!("Open Puffin's desktop window (puffin-app).\n");
        println!("Usage: puffin app [--chat]               the chat, on the local Onyx web UI");
        println!("       puffin app --work [<folder>]      the coding agent (Work), on a project folder");
        println!("       puffin app --thread <id>          a thread in Work");
        return 0;
    }
    let window = match window(args) {
        Ok(window) => window,
        Err(error) => {
            eprintln!("❌ puffin app: {error}");
            return 2;
        }
    };

    let Some(executable) = find_executable() else {
        eprintln!("❌ puffin-app is not installed.");
        eprintln!("💡 Build and register it with: puffin-admin desktop build");
        return 1;
    };
    // On a node, checked first: a window opened against a stopped server shows a bare connection
    // error with no hint of what to start. On a client the web UI is the node's: `puffin-app`
    // finds it and forwards this address to it, and says in its own window when it cannot
    // (specs/DREAMFERENCE_PUFFIN_NODE.md §7), so there is nothing on this machine to check.
    // Work needs neither Onyx nor its stylesheets.
    if window == Window::Chat {
        if puffin_node_locator::is_node() && !onyx_is_up(ONYX_WEB_URL).await {
            eprintln!("❌ Puffin is not answering at {ONYX_WEB_URL}.");
            eprintln!("💡 Start it first: puffin-admin puffin start");
            return 1;
        }
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
    match command.spawn() {
        Ok(_) => 0,
        Err(error) => {
            eprintln!("❌ Could not start {}: {error}", executable.display());
            1
        }
    }
}

/// `puffin-app` on PATH, else the binary the registered desktop entry launches.
///
/// The desktop entry is where `puffin-admin desktop` records the built binary's absolute path, so
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

async fn onyx_is_up(web_url: &str) -> bool {
    let Ok(client) = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
    else {
        return false;
    };
    client
        .get(format!("{}/api/health", web_url.trim_end_matches('/')))
        .send()
        .await
        .is_ok_and(|response| response.status().is_success())
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
    std::env::var_os("HOME").map(PathBuf::from)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_exec_line_names_the_program() {
        let entry = "[Desktop Entry]\nName=Puffin\nExec=/opt/puffin/puffin-app %U\nIcon=puffin-app\n";
        assert_eq!(exec_path(entry), Some(PathBuf::from("/opt/puffin/puffin-app")));
        assert_eq!(exec_path("Exec=\"/opt/puffin-app\"\n"), Some(PathBuf::from("/opt/puffin-app")));
        assert_eq!(exec_path("[Desktop Entry]\nName=x\n"), None);
    }

    fn args(text: &str) -> Vec<String> {
        text.split_whitespace().map(String::from).collect()
    }

    #[test]
    fn puffin_app_alone_still_opens_the_chat() {
        assert_eq!(window(&[]), Ok(Window::Chat));
        assert_eq!(window(&args("--chat")), Ok(Window::Chat));
    }

    #[test]
    fn a_folder_or_a_thread_opens_work() {
        assert_eq!(window(&args("--work")), Ok(Window::Work(args("--work"))));
        assert_eq!(window(&args("--thread t1")), Ok(Window::Work(args("--work --thread t1"))));
        let here = std::fs::canonicalize(".").map(|p| p.display().to_string()).unwrap_or_default();
        assert_eq!(window(&args(". --work")), Ok(Window::Work(vec!["--work".into(), "--cwd".into(), here])));
    }

    #[test]
    fn what_puffin_app_cannot_do_is_refused() {
        assert!(window(&args("--thread")).is_err());
        assert!(window(&args("--yolo")).is_err());
        assert!(window(&args("/no/such/folder/anywhere")).is_err());
        assert!(window(&args("--chat --work")).is_err());
        assert!(window(&args("Cargo.toml")).is_err() || !std::path::Path::new("Cargo.toml").exists());
    }
}
