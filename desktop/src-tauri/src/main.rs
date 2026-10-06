// Puffin desktop shell.
//
// The window points straight at the Onyx deployment on this machine, so there is no bundled
// frontend to keep in step with the browser UI -- the desktop app and the browser render the same
// server, and every patch `puffin-admin onyx configure` applies shows up in both.
//
// On a machine that is not the Puffin node the deployment is the node's. The window still loads
// `http://localhost:3000/app`: `forwarder.rs` binds that port and passes it through to the node
// `discover.rs` found, which keeps the page a secure context (the microphone) and leaves every
// cookie, redirect and patch seeing the address it sees on the node.
//
// Beside it, the Work window (specs/DREAMFERENCE_PUFFIN_DESKTOP.md): the coding agent, a bundled UI
// that drives `puffin app-server` through `bridge.rs`. It opens only when asked for
// (`puffin app --work`, a folder or `--thread <id>`); without that the app is exactly the Chat
// window it always was.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod bridge;
mod discover;
mod forwarder;
#[allow(dead_code)] // A byte-identical copy of puffin-rs/node-locator; not all of it is used here.
mod node_locator;

/// Environment the WebKitGTK webview needs, applied before Tauri starts it.
///
/// Both of these were originally set by the Python launcher, which was wrong: a `.desktop` entry,
/// an AppImage's AppRun or someone running this binary directly all bypass it, and the bug comes
/// straight back. Setting them here means they travel with the executable however it is started.
///
/// * `WEBKIT_DISABLE_DMABUF_RENDERER` -- WebKitGTK composites through DMABUF/GBM by default, which
///   asks the DRM device for a buffer. Under the proprietary NVIDIA driver an ordinary X11 client
///   is not an authenticated DRM client, so the request is refused:
///
///       KMS: DRM_IOCTL_MODE_CREATE_DUMB failed: Permission denied
///       Failed to create GBM buffer of size 2560x1720: Permission denied
///
///   The process then starts, logs those lines and shows no window at all. Disabling the DMABUF
///   renderer falls back to a path that never touches DRM.
///
/// * `GTK_THEME` -- WebKit reports `prefers-color-scheme` from the GTK theme, and Onyx follows it.
///   On a dark desktop the app therefore came up in dark mode, where none of Dreamference's styling
///   applies: every rule in `onyx_ui_overrides.py` is scoped `html:not(.dark)` on purpose, so dark
///   mode is plain Onyx. Pinning the webview to a light GTK theme is what makes the window show
///   Puffin rather than the stock UI. It does not touch the rest of the desktop session.
/// The web UI's default account, as `puffin-admin puffin configure` creates it
/// (`DEFAULT_ONYX_EMAIL` and `DEFAULT_ONYX_PASSWORD` in `dreamference/chat/onyx_runner.py`; a test
/// holds the two in step). Used once per window to sign in when there is no session.
const DEFAULT_EMAIL: &str = "admin@dreamference.dev";
const DEFAULT_PASSWORD: &str = "dreamference";

/// The sign-in script, with the account filled in.
fn auto_sign_in_script() -> String {
    include_str!("auto_sign_in.js")
        .replace("__PUFFIN_EMAIL__", DEFAULT_EMAIL)
        .replace("__PUFFIN_PASSWORD__", DEFAULT_PASSWORD)
}

const WEBVIEW_ENV: [(&str, &str); 2] = [
    ("WEBKIT_DISABLE_DMABUF_RENDERER", "1"),
    ("GTK_THEME", "Adwaita:light"),
];

/// The Chat window's label in `tauri.conf.json`: the Onyx web UI, unchanged since before Work.
const CHAT_LABEL: &str = "puffin";

/// Chat's window configuration when this process started with Work only, so the Work window's
/// Chat button can open it later exactly as configured.
struct ChatConfig(std::sync::Mutex<Option<tauri::utils::config::WindowConfig>>);

/// Raises the Chat window, creating it from its configuration when it was not opened at start.
pub fn show_chat(app: &tauri::AppHandle) -> tauri::Result<()> {
    use tauri::Manager;
    if let Some(window) = app.get_webview_window(CHAT_LABEL) {
        window.show()?;
        return window.set_focus();
    }
    let config = app.state::<ChatConfig>().0.lock().map(|config| config.clone()).unwrap_or_default();
    if let Some(config) = config {
        tauri::WebviewWindowBuilder::from_config(app, &config)?.build()?;
    }
    Ok(())
}

/// How `puffin app` asked for the window: `--work`, `--cwd <folder>` and `--thread <id>` open Work
/// (the launcher passes them); nothing opens Chat, as before.
fn work_target(args: &[String]) -> Option<bridge::WorkTarget> {
    let value_of = |flag: &str| args.iter().position(|arg| arg == flag).and_then(|i| args.get(i + 1)).cloned();
    let target = bridge::WorkTarget { cwd: value_of("--cwd"), thread: value_of("--thread") };
    (args.iter().any(|arg| arg == "--work") || target.cwd.is_some() || target.thread.is_some()).then_some(target)
}

fn main() {
    // Only fill in what the caller has not set, so either can still be overridden from the shell
    // -- useful when debugging the GPU path or checking how the UI looks on a dark theme.
    for (key, value) in WEBVIEW_ENV {
        if std::env::var_os(key).is_none() {
            std::env::set_var(key, value);
        }
    }

    #[allow(unused_mut)]
    let mut context = tauri::generate_context!();
    // Not a node: bring the node's web UI to loopback before the window asks for it.
    if let Some(upstream) = discover::upstream() {
        match forwarder::start(upstream) {
            Ok(forwarder::PREFERRED_PORT) => {}
            // Port 3000 is taken on this machine: the window has to be told the other port. The
            // saved session is per origin, so this one keeps its own sign-in.
            Ok(port) => {
                for window in context.config_mut().app.windows.iter_mut().filter(|w| w.label == CHAT_LABEL) {
                    if let Ok(url) = format!("http://localhost:{port}/app").parse() {
                        window.url = tauri::WebviewUrl::External(url);
                    }
                    window.title = format!("Puffin (port {port}: 3000 is in use on this machine)");
                }
            }
            Err(error) => eprintln!("puffin-app: could not bind a loopback port for the node's web UI: {error}"),
        }
    }

    // Work only: Chat's window is taken out of the configuration, so it is not created, and kept to
    // be opened later from Work's Chat button.
    let args: Vec<String> = std::env::args().skip(1).collect();
    let work = work_target(&args);
    let mut chat_config = None;
    if work.is_some() {
        let windows = &mut context.config_mut().app.windows;
        if let Some(index) = windows.iter().position(|w| w.label == CHAT_LABEL) {
            chat_config = Some(windows.remove(index));
        }
    }

    let sign_in = auto_sign_in_script();
    let bridge = bridge::Bridge::default();
    let app = tauri::Builder::default()
        .manage(bridge.clone())
        .manage(work.clone().unwrap_or_default())
        .manage(ChatConfig(std::sync::Mutex::new(chat_config)))
        .invoke_handler(tauri::generate_handler![
            bridge::work_start,
            bridge::work_send,
            bridge::work_stop,
            bridge::work_open_chat,
            bridge::work_target,
            bridge::work_airgapped,
        ])
        // After each page load: sign in with the default account if the window has no session.
        // Chat only: Work is not Onyx.
        .on_page_load(move |webview, payload| {
            if webview.label() == CHAT_LABEL && payload.event() == tauri::webview::PageLoadEvent::Finished {
                let _ = webview.eval(sign_in.as_str());
            }
        })
        .setup(move |app| {
            if work.is_some() {
                bridge::show_work(app.handle())?;
            }
            Ok(())
        })
        .build(context)
        .expect("failed to start the Puffin window");
    app.run(move |_, event| {
        if let tauri::RunEvent::Exit = event {
            bridge.shutdown();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(text: &str) -> Vec<String> {
        text.split_whitespace().map(String::from).collect()
    }

    #[test]
    fn no_arguments_open_chat_as_before() {
        assert!(work_target(&[]).is_none());
        assert!(work_target(&args("--chat")).is_none());
    }

    #[test]
    fn work_opens_on_a_folder_or_a_thread() {
        assert!(work_target(&args("--work")).is_some_and(|t| t.cwd.is_none() && t.thread.is_none()));
        let target = work_target(&args("--cwd /home/u/project")).unwrap_or_default();
        assert_eq!(target.cwd.as_deref(), Some("/home/u/project"));
        let target = work_target(&args("--work --thread 019a-thread")).unwrap_or_default();
        assert_eq!(target.thread.as_deref(), Some("019a-thread"));
    }
}
