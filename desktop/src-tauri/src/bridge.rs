// The Work window's connection to `ling app-server` (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §4.3).
//
// One server per app, started through the launcher (`ling app-server`, never a bare `codex`) when
// the Work window first asks for it. Its stdout carries the protocol, one JSON object per line,
// passed to the Work window as `work://message` events; its stderr carries the launcher's own
// messages (the wait for a model server that is still loading), passed as `work://stderr` for the
// start-up screen. What the window sends goes through `ling_desktop_bridge::vet_outgoing` first.
// Only the window labelled `work` may use any of this: Chat is Onyx's page and has no business
// with the agent, and every command here refuses it whatever the capabilities say.

use std::collections::HashSet;
use std::io::BufRead;
use std::io::BufReader;
use std::io::Write;
use std::path::PathBuf;
use std::process::Child;
use std::process::ChildStdin;
use std::process::Command;
use std::process::Stdio;
use std::sync::Arc;
use std::sync::Mutex;

use ling_desktop_bridge::BusyTracker;
use ling_desktop_bridge::Incoming;
use serde_json::Value;
use tauri::AppHandle;
use tauri::Emitter;
use tauri::Manager;
use tauri::WebviewWindow;

/// The Work window's label; the only window the bridge serves.
pub const WORK_LABEL: &str = "work";

#[derive(Default)]
struct Running {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    pending: HashSet<String>,
    busy: BusyTracker,
    marker: Option<PathBuf>,
}

/// The bridge's state, managed by Tauri.
#[derive(Default, Clone)]
pub struct Bridge(Arc<Mutex<Running>>);

/// What the Work window learns when the server starts.
#[derive(serde::Serialize)]
pub struct Started {
    /// The model the launcher's catalog names, passed in each `thread/start` (§5).
    served_model: Option<String>,
    /// Whether this call started the server, or found it already running.
    started: bool,
}

fn only_work(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == WORK_LABEL {
        Ok(())
    } else {
        Err("only the Work window talks to the agent".to_string())
    }
}

impl Bridge {
    fn lock(&self) -> std::sync::MutexGuard<'_, Running> {
        self.0.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Kills the server and removes its busy marker; called when the app exits.
    pub fn shutdown(&self) {
        let mut running = self.lock();
        running.stdin = None;
        if let Some(mut child) = running.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if let Some(marker) = running.marker.take() {
            let _ = std::fs::remove_file(marker);
        }
    }

    fn start(&self, app: &AppHandle) -> Result<bool, String> {
        let mut running = self.lock();
        if running.child.is_some() {
            return Ok(false);
        }
        let ling = ling_desktop_bridge::find_mightling()
            .ok_or("ling is not installed: build it with `ling-admin codex build`, or install Mightling")?;
        let mut child = Command::new(&ling)
            .arg("app-server")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| format!("could not start {} app-server: {error}", ling.display()))?;
        let stdout = child.stdout.take().ok_or("the server has no stdout")?;
        let stderr = child.stderr.take().ok_or("the server has no stderr")?;
        running.stdin = child.stdin.take();
        running.marker = ling_desktop_bridge::codex_home().map(|home| ling_desktop_bridge::busy_marker(&home, child.id()));
        running.pending.clear();
        running.busy = BusyTracker::default();
        running.child = Some(child);
        drop(running);

        let bridge = self.clone();
        let handle = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                bridge.incoming(&handle, &line);
            }
            bridge.exited(&handle);
        });
        let handle = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines() {
                let Ok(line) = line else { break };
                let _ = handle.emit_to(WORK_LABEL, "work://stderr", line);
            }
        });
        Ok(true)
    }

    fn incoming(&self, app: &AppHandle, line: &str) {
        if line.trim().is_empty() {
            return;
        }
        let (kind, value) = ling_desktop_bridge::classify(line);
        match &kind {
            Incoming::NotProtocol => {
                // Fails loudly: something wrote on the protocol channel.
                let _ = app.emit_to(WORK_LABEL, "work://protocol-error", line.to_string());
                return;
            }
            Incoming::ServerRequest { key, .. } => {
                self.lock().pending.insert(key.clone());
            }
            Incoming::Notification { method } => {
                let params = value.as_ref().and_then(|v| v.get("params"));
                let mut running = self.lock();
                if running.busy.observe(method, params) {
                    if let Some(marker) = running.marker.clone() {
                        let _ = running.busy.sync_marker(&marker);
                    }
                }
            }
            Incoming::Response => {}
        }
        if let Some(value) = value {
            let _ = app.emit_to(WORK_LABEL, "work://message", value);
        }
    }

    fn exited(&self, app: &AppHandle) {
        let code = {
            let mut running = self.lock();
            running.stdin = None;
            running.pending.clear();
            running.busy = BusyTracker::default();
            if let Some(marker) = running.marker.take() {
                let _ = std::fs::remove_file(marker);
            }
            running.child.take().and_then(|mut child| child.wait().ok()).and_then(|status| status.code())
        };
        let _ = app.emit_to(WORK_LABEL, "work://exit", code);
    }

    fn send(&self, mut message: Value) -> Result<(), String> {
        let mut running = self.lock();
        let Running { stdin, pending, .. } = &mut *running;
        ling_desktop_bridge::vet_outgoing(&mut message, pending)?;
        let stdin = stdin.as_mut().ok_or("the agent's server is not running")?;
        let mut line = message.to_string();
        line.push('\n');
        stdin
            .write_all(line.as_bytes())
            .and_then(|()| stdin.flush())
            .map_err(|error| format!("the agent's server stopped listening: {error}"))
    }
}

/// Starts `ling app-server` if it is not running.
#[tauri::command]
pub fn work_start(app: AppHandle, window: WebviewWindow, bridge: tauri::State<'_, Bridge>) -> Result<Started, String> {
    only_work(&window)?;
    let started = bridge.start(&app)?;
    let served_model = ling_desktop_bridge::codex_home().and_then(|home| ling_desktop_bridge::served_model(&home));
    Ok(Started { served_model, started })
}

/// Sends one protocol message: a request, the `initialized` notification, or an answer to a
/// server request.
#[tauri::command]
pub fn work_send(window: WebviewWindow, bridge: tauri::State<'_, Bridge>, message: Value) -> Result<(), String> {
    only_work(&window)?;
    bridge.send(message)
}

/// Stops the server; the next `work_start` starts a fresh one.
#[tauri::command]
pub fn work_stop(window: WebviewWindow, bridge: tauri::State<'_, Bridge>) -> Result<(), String> {
    only_work(&window)?;
    bridge.shutdown();
    Ok(())
}

/// Raises the Chat window, creating it from its configuration if this process started with Work
/// only (`ling app --work`).
#[tauri::command]
pub fn work_open_chat(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    only_work(&window)?;
    crate::show_chat(&app).map_err(|error| error.to_string())
}

/// The air-gap level in force for a thread, or the configured one: the title bar shows it, and the
/// permission picker disables Full Access at `on` (a courtesy; the refusal that binds is the
/// server's, specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §8.2).
#[derive(serde::Serialize)]
pub struct Airgapped {
    level: &'static str,
    source: String,
}

#[tauri::command]
pub fn work_airgapped(window: WebviewWindow, thread: Option<String>) -> Result<Airgapped, String> {
    only_work(&window)?;
    let ids: Vec<&str> = thread.as_deref().into_iter().collect();
    let resolved = ling_airgapped::resolve(&ids);
    Ok(Airgapped { level: resolved.level.name(), source: resolved.source.label() })
}

/// Where the Work window was asked to open (`ling app <folder>`, `ling app --thread <id>`).
#[derive(Default, Clone, serde::Serialize)]
pub struct WorkTarget {
    pub cwd: Option<String>,
    pub thread: Option<String>,
}

/// The project folder or thread `ling app` named, if any.
#[tauri::command]
pub fn work_target(window: WebviewWindow, target: tauri::State<'_, WorkTarget>) -> Result<WorkTarget, String> {
    only_work(&window)?;
    Ok(target.inner().clone())
}

/// Shows the Work window, creating it the first time.
pub fn show_work(app: &AppHandle) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window(WORK_LABEL) {
        window.show()?;
        return window.set_focus();
    }
    tauri::WebviewWindowBuilder::new(app, WORK_LABEL, tauri::WebviewUrl::App("index.html".into()))
        .title("Mightling — Work")
        .inner_size(1280.0, 860.0)
        .min_inner_size(720.0, 520.0)
        .center()
        .background_color(tauri::webview::Color(255, 255, 255, 255))
        .build()?;
    Ok(())
}
