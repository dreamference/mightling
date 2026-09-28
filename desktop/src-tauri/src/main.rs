// Puffin desktop shell.
//
// The window points straight at the Onyx deployment on this machine, so there is no bundled
// frontend to keep in step with the browser UI -- the desktop app and the browser render the same
// server, and every patch `puffin-admin onyx configure` applies shows up in both.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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
const WEBVIEW_ENV: [(&str, &str); 2] = [
    ("WEBKIT_DISABLE_DMABUF_RENDERER", "1"),
    ("GTK_THEME", "Adwaita:light"),
];

fn main() {
    // Only fill in what the caller has not set, so either can still be overridden from the shell
    // -- useful when debugging the GPU path or checking how the UI looks on a dark theme.
    for (key, value) in WEBVIEW_ENV {
        if std::env::var_os(key).is_none() {
            std::env::set_var(key, value);
        }
    }

    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("failed to start the Puffin window");
}
