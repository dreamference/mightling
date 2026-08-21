// Puffin desktop shell.
//
// The window points straight at the Onyx deployment on this machine, so there is no bundled
// frontend to keep in step with the browser UI -- the desktop app and the browser render the same
// server, and every patch `dream onyx configure` applies shows up in both.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("failed to start the Puffin window");
}
