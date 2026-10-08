//! `ling chat …`: Mightling from a phone messenger (specs/DREAMFERENCE_MIGHTLING_CHAT.md). The bridge is
//! the `ling-chat` crate (`chat/`); this module only tells it where things are: the home folder, the
//! user's home (the user unit, the user-level config) and this binary (the unit runs it, and the
//! bridge starts `ling web` with it when nothing answers).

use std::path::PathBuf;

/// `ling chat …` from a shell. Returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        eprintln!("Could not resolve CODEX_HOME.");
        return 1;
    };
    let Some(home) = std::env::var_os("HOME").map(PathBuf::from) else {
        eprintln!("HOME is not set.");
        return 1;
    };
    let ling = match std::env::current_exe() {
        Ok(path) => path,
        Err(err) => {
            eprintln!("Could not find this program's path: {err}");
            return 1;
        }
    };
    ling_chat::run_cli(args, ling_chat::Environment { codex_home, home, ling }).await
}
