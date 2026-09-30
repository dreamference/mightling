//! The launcher's two calls to `puffin-code`, the code index router
//! (specs/DREAMFERENCE_PUFFIN_CODE_INDEX.md §4.2). The router is a program of its own, so this is
//! all the launcher knows about it:
//!
//! 1. it starts `puffin-code session --parent-pid <this process>` detached, outside Codex's
//!    sandbox (the launcher runs before Codex sandboxes anything), and continues;
//! 2. it appends what `puffin-code prompt-block` prints to the model's prompt, so the commands and
//!    the prompt that describes them live in one place.
//!
//! With `puffin-code` not installed, both are skipped and nothing changes.

use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;

/// Where `puffin-admin codex build` installs `puffin-code`: beside `puffin`, never looked up on
/// PATH, for the same reason `puffin` itself is not.
pub fn binary() -> Option<PathBuf> {
    let path = match std::env::var_os("PUFFIN_CODE_BIN") {
        Some(path) => PathBuf::from(path),
        None => PathBuf::from(std::env::var_os("HOME")?).join(".local/share/dreamference/puffin/bin/puffin-code"),
    };
    path.is_file().then_some(path)
}

/// Starts the session process and returns the prompt block (empty when there is none).
pub fn start_and_prompt_block() -> String {
    let Some(binary) = binary() else { return String::new() };
    // Its own process group, so Ctrl-C in the TUI does not reach it; it exits with this process.
    let _ = {
        use std::os::unix::process::CommandExt;
        Command::new(&binary)
            .args(["session", "--parent-pid", &std::process::id().to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
    };
    let Ok(output) = Command::new(&binary).arg("prompt-block").stdin(Stdio::null()).stderr(Stdio::null()).output() else {
        return String::new();
    };
    let block = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() || block.is_empty() {
        return String::new();
    }
    format!("\n\n{block}\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn without_puffin_code_nothing_starts_and_no_block_is_added() {
        // SAFETY: tests in this module are the only readers of PUFFIN_CODE_BIN.
        unsafe { std::env::set_var("PUFFIN_CODE_BIN", "/nonexistent/puffin-code") };
        assert_eq!(binary(), None);
        assert_eq!(start_and_prompt_block(), "");
        unsafe { std::env::remove_var("PUFFIN_CODE_BIN") };
    }
}
