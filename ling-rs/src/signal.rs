//! `ling signal …`: Mightling over Signal (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §6). The commands
//! are the `ling-signal` crate's (`signal/src/cli.rs`); this module only says where the bridge
//! binary is. The daemon itself is not `ling`: the system account `mightling-signal` runs the small
//! `ling-signal` binary that setup copies to /usr/local/lib/mightling, so `ling signal setup` needs
//! the copy installed beside `ling` (by `ling update` or `ling-admin codex build`).

use std::path::Path;
use std::path::PathBuf;

/// The bridge binary's name, installed beside `ling` by `ling update` and `ling-admin codex build`.
pub const BRIDGE_COMMAND: &str = crate::update::SIGNAL_COMMAND;

/// `ling-signal` beside the running `ling`, links resolved (`~/.local/bin/ling` is a link into the
/// install folder); None when there is none.
pub fn bridge_beside(exe: &Path) -> Option<PathBuf> {
    let exe = exe.canonicalize().ok()?;
    let candidate = exe.parent()?.join(BRIDGE_COMMAND);
    candidate.is_file().then_some(candidate)
}

/// `ling signal …` from a shell. Returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let bridge = std::env::current_exe().ok().and_then(|exe| bridge_beside(&exe));
    let args = args.to_vec();
    // The commands are synchronous, and the bridge's own build their own runtime; a blocking
    // thread keeps both away from the launcher's.
    let invocation = ling_signal::cli::Invocation { bridge, bridge_commands: false };
    tokio::task::spawn_blocking(move || ling_signal::cli::run(&args, &invocation)).await.unwrap_or(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_bridge_is_found_beside_the_resolved_executable() {
        let root = std::env::temp_dir().join(format!("ling-signal-beside-{}", std::process::id()));
        let install = root.join("install/bin");
        let links = root.join("links");
        std::fs::create_dir_all(&install).unwrap();
        std::fs::create_dir_all(&links).unwrap();
        std::fs::write(install.join("ling"), b"").unwrap();
        std::os::unix::fs::symlink(install.join("ling"), links.join("ling")).unwrap();
        assert_eq!(bridge_beside(&links.join("ling")), None, "not installed yet");
        std::fs::write(install.join(BRIDGE_COMMAND), b"").unwrap();
        assert_eq!(bridge_beside(&links.join("ling")), Some(install.canonicalize().unwrap().join(BRIDGE_COMMAND)));
        let _ = std::fs::remove_dir_all(&root);
    }
}
