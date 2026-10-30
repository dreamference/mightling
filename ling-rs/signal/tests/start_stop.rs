//! `ling signal start` and `stop` on a scratch node: `sudo` is a shell script that logs what it was
//! asked and is the only program on PATH, so no real unit is ever touched. One test, alone in its
//! binary, because it sets PATH for the whole process.

use std::path::PathBuf;

use ling_signal::cli::Host;
use ling_signal::cli::Invocation;
use ling_signal::cli::run;

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn words(line: &[&str]) -> Vec<String> {
    line.iter().map(|word| word.to_string()).collect()
}

#[test]
fn stop_disables_the_unit_so_it_stays_off_and_start_enables_it_again() {
    use std::os::unix::fs::PermissionsExt;
    let scratch = Scratch(std::env::temp_dir().join(format!("ling-signal-start-stop-{}", std::process::id())));
    let bin = scratch.0.join("bin");
    let root = scratch.0.join("root");
    std::fs::create_dir_all(&bin).unwrap();
    std::fs::create_dir_all(root.join("var/lib/mightling-signal")).unwrap();
    let log = scratch.0.join("sudo.log");
    let sudo = bin.join("sudo");
    std::fs::write(&sudo, format!("#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{}'\n", log.display())).unwrap();
    std::fs::set_permissions(&sudo, std::fs::Permissions::from_mode(0o755)).unwrap();
    // SAFETY: this binary has this one test, so no other thread reads the environment.
    unsafe { std::env::set_var("PATH", &bin) };
    let invocation = Invocation { bridge: None, bridge_commands: false, host: Host { root } };
    let asked = || std::fs::read_to_string(&log).unwrap_or_default();

    assert_eq!(run(&words(&["stop"]), &invocation), 0);
    assert_eq!(asked(), "systemctl disable --now mightling-signal.service\n");
    assert_eq!(run(&words(&["start"]), &invocation), 0);
    assert_eq!(asked(), "systemctl disable --now mightling-signal.service\nsystemctl enable --now mightling-signal.service\n");
    // A bridge this node does not have is refused before any sudo.
    assert_eq!(run(&words(&["stop", "--name", "work"]), &invocation), 2);
    assert_eq!(asked().lines().count(), 2);
    // A refused sudo is a failed stop, not a stopped bridge.
    std::fs::write(&sudo, "#!/bin/sh\nexit 1\n").unwrap();
    assert_eq!(run(&words(&["stop"]), &invocation), 1);
}
