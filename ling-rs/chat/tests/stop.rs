//! `ling chat stop` with a `systemctl` that is a shell script logging what it was asked, the only
//! program on PATH, so the user's real unit is never touched. One test, alone in its binary,
//! because it sets PATH for the whole process.

use std::path::PathBuf;

use ling_chat::Environment;
use ling_chat::run_cli;

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[tokio::test]
async fn stop_disables_the_unit_so_it_stays_off_until_start() {
    use std::os::unix::fs::PermissionsExt;
    let scratch = Scratch(std::env::temp_dir().join(format!("ling-chat-stop-{}", std::process::id())));
    let bin = scratch.0.join("bin");
    std::fs::create_dir_all(&bin).unwrap();
    let log = scratch.0.join("systemctl.log");
    let systemctl = bin.join("systemctl");
    std::fs::write(&systemctl, format!("#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{}'\n", log.display())).unwrap();
    std::fs::set_permissions(&systemctl, std::fs::Permissions::from_mode(0o755)).unwrap();
    // SAFETY: this binary has this one test and sets PATH before anything else runs.
    unsafe { std::env::set_var("PATH", &bin) };
    let environment = Environment { codex_home: scratch.0.join("codex-home"), home: scratch.0.join("home"), ling: scratch.0.join("bin/ling") };
    let asked = || std::fs::read_to_string(&log).unwrap_or_default();

    assert_eq!(run_cli(&["stop".to_string()], environment.clone()).await, 0);
    assert_eq!(asked(), "--user disable --now mightling-chat.service\n");
    // `stop now` is refused, not run as `stop`.
    assert_eq!(run_cli(&["stop".to_string(), "now".to_string()], environment).await, 2);
    assert_eq!(asked().lines().count(), 1);
}
