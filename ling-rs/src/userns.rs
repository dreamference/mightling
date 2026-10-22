//! Whether `ling`'s command sandbox can start on this machine, said at start instead of at the
//! first command (specs/DREAMFERENCE_MIGHTLING_NO_SUDO.md).
//!
//! On Linux every sandboxed command goes through bubblewrap, which needs a user namespace. Ubuntu
//! 24.04 refuses one to a program without an AppArmor profile
//! (`kernel.apparmor_restrict_unprivileged_userns=1`) and ships none for `/usr/bin/bwrap`, so on a
//! machine nobody with root has prepared, each command fails with "bwrap: loopback: Failed
//! RTM_NEWADDR" and then asks to run outside the sandbox. Ubuntu 25.04 and later ship
//! `bwrap-userns-restrict`, and the sandbox works there with no step (measured on 26.04,
//! 2026-10-22). There is no route without root: the pinned Codex's Landlock fallback refuses every
//! policy that restricts the filesystem ("filesystem-restricted execution requires bubblewrap").
//!
//! So `ling` tries bubblewrap once, as Codex's own probe does, and when it is refused says what
//! that means and the exact command an administrator runs once. `/airgapped on` is enforced by the
//! sandbox's empty network namespace, so with no sandbox it is refused, never claimed: at start
//! for a configured `on`, and when typed.

use std::path::Path;
use std::path::PathBuf;

use crate::airgapped::ENV_VAR;
use crate::airgapped::Level;

/// Ubuntu's switch: 1 refuses a user namespace to programs without an AppArmor profile.
pub const USERNS_SYSCTL: &str = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns";

/// Where AppArmor profiles live. World-readable, so no root is needed to look.
pub const APPARMOR_DIR: &str = "/etc/apparmor.d";

/// The profile `ling-admin host setup` writes (`sandbox_prerequisite.py`), kept under its old name
/// so the command printed here and that one write the same file and never a second profile.
pub const PROFILE_NAME: &str = "puffin-bwrap";

/// What bubblewrap's attempt to sandbox `/bin/true` showed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Sandbox {
    /// It ran, or this is not Linux.
    Works,
    /// It could not be tried (it hung or could not be started); nothing is said, as Codex does.
    Unknown,
    /// No `bwrap` on PATH. Mightling ships no copy of its own.
    Missing,
    /// bubblewrap was refused its namespaces; `detail` is its first line of complaint.
    Refused { bwrap: PathBuf, detail: String },
}

/// What the messages need to know about the machine besides the probe.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Host {
    /// The sysctl's value: Some(true) is Ubuntu's AppArmor rule, None where it does not exist.
    pub restricted: Option<bool>,
    /// The profile files that attach to the bwrap in use.
    pub profiles: Vec<PathBuf>,
    /// Whether `ling-admin` is on PATH (a node; a client has none).
    pub admin: bool,
}

/// The probe's answer for this process, tried once.
pub fn sandbox_now() -> Sandbox {
    #[cfg(test)]
    {
        tests::FAKE.with(|fake| fake.borrow().clone()).unwrap_or(Sandbox::Works)
    }
    #[cfg(not(test))]
    {
        static ONCE: std::sync::OnceLock<Sandbox> = std::sync::OnceLock::new();
        ONCE.get_or_init(probe_now).clone()
    }
}

#[cfg(not(test))]
fn probe_now() -> Sandbox {
    if !cfg!(target_os = "linux") {
        return Sandbox::Works;
    }
    match find_bwrap() {
        Some(bwrap) => probe(&bwrap),
        None => Sandbox::Missing,
    }
}

/// The `bwrap` Codex's sandbox runs: the first on PATH, as its own lookup takes it.
pub fn find_bwrap() -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join("bwrap"))
        .find(|candidate| candidate.is_file())
        .map(|found| std::fs::canonicalize(&found).unwrap_or(found))
}

/// Runs `bwrap` as Codex's startup probe does (a user and a network namespace around `/bin/true`)
/// and reads the outcome. Any complaint from bwrap about so small a sandbox is a refusal.
#[cfg(not(test))]
fn probe(bwrap: &Path) -> Sandbox {
    use std::process::Command;
    use std::process::Stdio;
    use std::time::Duration;
    use std::time::Instant;

    let child = Command::new(bwrap)
        .args(["--unshare-user", "--unshare-net", "--ro-bind", "/", "/", "/bin/true"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn();
    let Ok(mut child) = child else {
        return Sandbox::Unknown;
    };
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut stderr = String::new();
                if let Some(mut pipe) = child.stderr.take() {
                    let _ = std::io::Read::read_to_string(&mut pipe, &mut stderr);
                }
                return outcome(bwrap, status.success(), &stderr);
            }
            Ok(None) if started.elapsed() < Duration::from_secs(2) => {
                std::thread::sleep(Duration::from_millis(10));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Sandbox::Unknown;
            }
        }
    }
}

/// The probe's outcome from its exit and its stderr.
pub fn outcome(bwrap: &Path, success: bool, stderr: &str) -> Sandbox {
    if success {
        return Sandbox::Works;
    }
    match stderr.lines().find(|line| line.starts_with("bwrap:")) {
        Some(line) => Sandbox::Refused {
            bwrap: bwrap.to_path_buf(),
            detail: line.trim().to_string(),
        },
        None => Sandbox::Unknown,
    }
}

/// The machine as it is now: the sysctl, the profiles naming `bwrap`, `ling-admin` on PATH.
pub fn host_now(bwrap: &Path) -> Host {
    let restricted = std::fs::read_to_string(USERNS_SYSCTL)
        .ok()
        .map(|value| value.trim() == "1");
    let admin = std::env::var_os("PATH").is_some_and(|path| {
        std::env::split_paths(&path).any(|dir| dir.join("ling-admin").is_file())
    });
    Host {
        restricted,
        profiles: profiles_attaching(Path::new(APPARMOR_DIR), bwrap),
        admin,
    }
}

/// The profile files in `dir` whose `profile` line attaches to `binary`. Two of them make
/// AppArmor attach neither, which breaks bubblewrap for every caller (openai/codex#38715): Ubuntu
/// 25.04 and later ship `bwrap-userns-restrict` for `/usr/bin/bwrap`, so a machine upgraded with
/// `puffin-bwrap` in place has two.
pub fn profiles_attaching(dir: &Path, binary: &Path) -> Vec<PathBuf> {
    let binary = binary.display().to_string();
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut found: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_file())
        .filter(|path| {
            std::fs::read_to_string(path).is_ok_and(|text| {
                text.lines().any(|line| {
                    let line = line.trim_start();
                    line.starts_with("profile ")
                        && line
                            .split_whitespace()
                            .nth(2)
                            .is_some_and(|attachment| attachment.trim_matches('"') == binary)
                })
            })
        })
        .collect();
    found.sort();
    found
}

/// The profile text the one-line fix writes: `ling-admin host setup`'s, without its comments.
pub fn profile_text(bwrap: &Path) -> String {
    format!(
        "abi <abi/4.0>,\ninclude <tunables/global>\nprofile {PROFILE_NAME} {} flags=(unconfined) {{\n  userns,\n}}\n",
        bwrap.display()
    )
}

/// The one command, for a terminal, that writes and loads the profile.
pub fn fix_command(bwrap: &Path) -> String {
    let file = format!("{APPARMOR_DIR}/{PROFILE_NAME}");
    let printf = profile_text(bwrap).replace('\n', "\\n");
    format!("printf '{printf}' | sudo tee {file} >/dev/null && sudo apparmor_parser -r {file}")
}

/// What an administrator does once, for this machine's state. Never `--no-sandbox`, never the
/// sysctl, never Full Access: the narrowest grant, to bubblewrap alone.
pub fn fix_lines(sandbox: &Sandbox, host: &Host) -> Vec<String> {
    let bwrap = match sandbox {
        Sandbox::Missing => {
            return vec![
                "   bubblewrap is not installed, and Mightling ships no copy. An administrator installs it once: sudo apt install bubblewrap".to_string(),
            ];
        }
        Sandbox::Refused { bwrap, .. } => bwrap,
        Sandbox::Works | Sandbox::Unknown => return Vec::new(),
    };
    if host.restricted != Some(true) {
        return vec![
            "   This system turns user namespaces off for ordinary programs altogether, not by Ubuntu's AppArmor rule, so no profile helps. An administrator checks user.max_user_namespaces and, on Debian kernels, kernel.unprivileged_userns_clone.".to_string(),
        ];
    }
    match host.profiles.as_slice() {
        [] => {
            let mut lines = vec![
                format!("   The fix, once per machine, by an administrator: an AppArmor profile that lets {} alone create its namespaces:", bwrap.display()),
                format!("   {}", fix_command(bwrap)),
            ];
            if host.admin {
                lines.push("   (On a node, ling-admin host setup writes the same profile.)".to_string());
            }
            lines
        }
        [one] => vec![
            format!("   {} attaches to {} but is not in force. An administrator loads it once:", one.display(), bwrap.display()),
            format!("   sudo apparmor_parser -r {}", one.display()),
        ],
        several => {
            // Keep Ubuntu's own when it is one of them: ours is the extra.
            let ours = several.iter().find(|path| path.file_name().is_some_and(|name| name == PROFILE_NAME));
            let remove: Vec<&PathBuf> = match ours {
                Some(ours) => vec![ours],
                None => several.iter().skip(1).collect(),
            };
            let names: Vec<String> = several.iter().map(|path| path.display().to_string()).collect();
            let commands: Vec<String> = remove
                .iter()
                .map(|path| format!("sudo apparmor_parser -R {0} && sudo rm {0}", path.display()))
                .collect();
            vec![
                format!("   {} AppArmor profiles attach to {} ({}), and AppArmor then attaches none.", several.len(), bwrap.display(), names.join(", ")),
                format!("   An administrator keeps one, once: {}", commands.join(" && ")),
            ]
        }
    }
}

/// The first line: what is wrong, in a sentence.
fn problem(sandbox: &Sandbox) -> Option<String> {
    match sandbox {
        Sandbox::Refused { bwrap, detail } => Some(format!(
            "ling's sandbox cannot start on this machine: {} is refused a user namespace ({detail})",
            bwrap.display()
        )),
        Sandbox::Missing => Some("ling's sandbox cannot start on this machine: bubblewrap is not installed".to_string()),
        Sandbox::Works | Sandbox::Unknown => None,
    }
}

/// What `ling` says before a session whose commands would run in the sandbox, when the sandbox
/// cannot start. `full_access` is a session with no sandbox to start, which needs no warning.
pub fn startup_lines(sandbox: &Sandbox, host: &Host, full_access: bool) -> Vec<String> {
    if full_access {
        return Vec::new();
    }
    let Some(problem) = problem(sandbox) else {
        return Vec::new();
    };
    let mut lines = vec![
        format!("⚠️ {problem}."),
        "   Each command the agent runs fails in the sandbox and then asks to run outside it; approving runs it with all your access. /airgapped on is refused.".to_string(),
    ];
    lines.extend(fix_lines(sandbox, host));
    lines
}

/// [`startup_lines`] for this machine. Nothing inside a command `ling` runs (it is sandboxed
/// already, and nested bubblewrap would always be refused).
pub fn startup_lines_now(full_access: bool) -> Vec<String> {
    if inside_sandbox() {
        return Vec::new();
    }
    let sandbox = sandbox_now();
    match &sandbox {
        Sandbox::Refused { bwrap, .. } => startup_lines(&sandbox, &host_now(bwrap), full_access),
        _ => startup_lines(&sandbox, &Host::default(), full_access),
    }
}

/// Why a launch at a configured `on` must not start: the sandbox that enforces it cannot.
pub fn airgapped_conflict(sandbox: &Sandbox, host: &Host, level: Level) -> Option<String> {
    if level != Level::On {
        return None;
    }
    let problem = problem(sandbox)?;
    let fix: Vec<String> = fix_lines(sandbox, host).iter().map(|line| line.trim().to_string()).collect();
    Some(format!(
        "airgapped is on, and {}, so `on` cannot be enforced: commands could only run outside the sandbox, with the network. {} Or: ling airgapped default off ({ENV_VAR}=off for one run).",
        problem.trim_start_matches("ling's sandbox cannot start on this machine: "),
        fix.join(" ")
    ))
}

/// [`airgapped_conflict`] for this machine.
pub fn airgapped_conflict_now(level: Level) -> Option<String> {
    if level != Level::On || inside_sandbox() {
        return None;
    }
    let sandbox = sandbox_now();
    let host = match &sandbox {
        Sandbox::Refused { bwrap, .. } => host_now(bwrap),
        _ => Host::default(),
    };
    airgapped_conflict(&sandbox, &host, level)
}

/// `/airgapped on` refused where the sandbox cannot start.
pub fn on_refusal(sandbox: &Sandbox, host: &Host) -> Option<Vec<String>> {
    let problem = problem(sandbox)?;
    let mut lines = vec![format!(
        "Airgapped not changed: {}, so `on` could not be enforced.",
        problem.trim_start_matches("ling's sandbox cannot start on this machine: ")
    )];
    lines.extend(fix_lines(sandbox, host).into_iter().map(|line| line.trim().to_string()));
    lines.push("Then restart ling.".to_string());
    Some(lines)
}

/// [`on_refusal`] for this machine.
pub fn on_refusal_now() -> Option<Vec<String>> {
    if !cfg!(target_os = "linux") || inside_sandbox() {
        return None;
    }
    let sandbox = sandbox_now();
    let host = match &sandbox {
        Sandbox::Refused { bwrap, .. } => host_now(bwrap),
        _ => Host::default(),
    };
    on_refusal(&sandbox, &host)
}

/// A command `ling` runs: Codex exports `CODEX_THREAD_ID` to each (and `CODEX_SANDBOX` on macOS),
/// the same test `ling-admin`'s check makes (`sandbox_prerequisite.py`).
fn inside_sandbox() -> bool {
    ["CODEX_THREAD_ID", "CODEX_SANDBOX"].iter().any(|name| std::env::var_os(name).is_some_and(|value| !value.is_empty()))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::cell::RefCell;

    thread_local! {
        /// What [`sandbox_now`] answers in a test; unset, the sandbox works, so no test depends on
        /// the machine it runs on.
        pub(crate) static FAKE: RefCell<Option<Sandbox>> = const { RefCell::new(None) };
    }

    /// Makes [`sandbox_now`] answer `sandbox` on this thread.
    pub(crate) fn fake(sandbox: Option<Sandbox>) {
        FAKE.with(|fake| *fake.borrow_mut() = sandbox);
    }

    fn refused() -> Sandbox {
        Sandbox::Refused {
            bwrap: PathBuf::from("/usr/bin/bwrap"),
            detail: "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted".to_string(),
        }
    }

    fn ubuntu_2404(profiles: Vec<PathBuf>) -> Host {
        Host { restricted: Some(true), profiles, admin: false }
    }

    #[test]
    fn bwraps_complaint_is_a_refusal_and_silence_is_not() {
        let bwrap = Path::new("/usr/bin/bwrap");
        assert_eq!(outcome(bwrap, true, ""), Sandbox::Works);
        assert_eq!(
            outcome(bwrap, false, "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n"),
            refused()
        );
        // Measured under a stacked `unprivileged_userns` label: another wording, the same refusal.
        assert!(matches!(
            outcome(bwrap, false, "bwrap: No permissions to create new namespace, likely because ..."),
            Sandbox::Refused { .. }
        ));
        assert_eq!(outcome(bwrap, false, "something else entirely\n"), Sandbox::Unknown);
    }

    #[test]
    fn a_refused_sandbox_says_what_it_means_and_the_one_command_that_fixes_it() {
        let lines = startup_lines(&refused(), &ubuntu_2404(vec![]), false);
        assert!(lines[0].starts_with("⚠️ ling's sandbox cannot start"), "{lines:?}");
        assert!(lines[0].contains("Failed RTM_NEWADDR"));
        assert!(lines[1].contains("asks to run outside it") && lines[1].contains("/airgapped on is refused"));
        let fix = lines.iter().find(|line| line.contains("sudo tee")).expect("the fix");
        assert!(fix.contains("/etc/apparmor.d/puffin-bwrap") && fix.contains("sudo apparmor_parser -r"));
        assert!(!lines.iter().any(|line| line.contains("--no-sandbox") || line.contains("sysctl -w")));
        assert!(!lines.iter().any(|line| line.contains("ling-admin")), "a client has no ling-admin");
        let node = startup_lines(&refused(), &Host { admin: true, ..ubuntu_2404(vec![]) }, false);
        assert!(node.iter().any(|line| line.contains("ling-admin host setup")));
    }

    #[test]
    fn nothing_is_said_where_the_sandbox_works_or_the_session_has_none() {
        assert!(startup_lines(&Sandbox::Works, &Host::default(), false).is_empty());
        assert!(startup_lines(&Sandbox::Unknown, &Host::default(), false).is_empty());
        assert!(startup_lines(&refused(), &ubuntu_2404(vec![]), true).is_empty());
    }

    #[test]
    fn the_printed_command_writes_the_profile_host_setup_writes() {
        let command = fix_command(Path::new("/usr/bin/bwrap"));
        let printf = command.split('\'').nth(1).expect("quoted");
        let written = printf.replace("\\n", "\n");
        assert_eq!(written, profile_text(Path::new("/usr/bin/bwrap")));
        assert!(written.contains("profile puffin-bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}"));
        assert!(!printf.contains('\''));
    }

    #[test]
    fn the_profile_compiles() {
        // -Q compiles without loading anything into the kernel; -K skips the binary cache. Skipped
        // where AppArmor's parser is not installed.
        let Some(parser) = ["/usr/sbin/apparmor_parser", "/sbin/apparmor_parser"].into_iter().find(|p| Path::new(p).is_file()) else {
            return;
        };
        let dir = std::env::temp_dir().join(format!("ling-userns-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join(PROFILE_NAME);
        std::fs::write(&file, profile_text(Path::new("/usr/bin/bwrap"))).unwrap();
        let out = std::process::Command::new(parser)
            .args(["-Q", "-K", "-I", APPARMOR_DIR])
            .arg(&file)
            .output()
            .unwrap();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    }

    #[test]
    fn a_profile_already_there_is_loaded_and_two_are_brought_back_to_one() {
        let one = fix_lines(&refused(), &ubuntu_2404(vec![PathBuf::from("/etc/apparmor.d/bwrap-userns-restrict")]));
        assert!(one.iter().any(|line| line.contains("sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict")));
        assert!(!one.iter().any(|line| line.contains("sudo tee")), "a second profile would break bwrap");
        let two = fix_lines(
            &refused(),
            &ubuntu_2404(vec![
                PathBuf::from("/etc/apparmor.d/bwrap-userns-restrict"),
                PathBuf::from("/etc/apparmor.d/puffin-bwrap"),
            ]),
        );
        assert!(two[0].contains("2 AppArmor profiles") && two[0].contains("attaches none"));
        assert!(two[1].contains("sudo apparmor_parser -R /etc/apparmor.d/puffin-bwrap && sudo rm /etc/apparmor.d/puffin-bwrap"));
        assert!(!two[1].contains("rm /etc/apparmor.d/bwrap-userns-restrict"), "Ubuntu's own is kept");
    }

    #[test]
    fn profiles_are_found_by_their_attachment() {
        let dir = std::env::temp_dir().join(format!("ling-userns-dir-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("puffin-bwrap"), "abi <abi/4.0>,\nprofile puffin-bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}\n").unwrap();
        std::fs::write(dir.join("bwrap-userns-restrict"), "profile bwrap /usr/bin/bwrap flags=(attach_disconnected) {\n}\nprofile unpriv_bwrap flags=(x) {\n}\n").unwrap();
        std::fs::write(dir.join("chrome"), "profile chrome /opt/google/chrome/chrome flags=(unconfined) {\n  userns,\n}\n").unwrap();
        std::fs::write(dir.join("mentions"), "# /usr/bin/bwrap in a comment only\n").unwrap();
        let found = profiles_attaching(&dir, Path::new("/usr/bin/bwrap"));
        let _ = std::fs::remove_dir_all(&dir);
        let names: Vec<String> = found.iter().map(|p| p.file_name().unwrap().to_string_lossy().into_owned()).collect();
        assert_eq!(names, ["bwrap-userns-restrict", "puffin-bwrap"]);
    }

    #[test]
    fn missing_bubblewrap_and_other_kernels_get_their_own_answer() {
        let missing = startup_lines(&Sandbox::Missing, &Host::default(), false);
        assert!(missing.iter().any(|line| line.contains("sudo apt install bubblewrap")));
        let debian = fix_lines(&refused(), &Host { restricted: None, ..Host::default() });
        assert!(debian[0].contains("no profile helps") && debian[0].contains("max_user_namespaces"));
    }

    #[test]
    fn a_configured_on_does_not_start_without_the_sandbox() {
        let host = ubuntu_2404(vec![]);
        assert_eq!(airgapped_conflict(&Sandbox::Works, &host, Level::On), None);
        assert_eq!(airgapped_conflict(&refused(), &host, Level::Off), None);
        let reason = airgapped_conflict(&refused(), &host, Level::On).expect("refused");
        assert!(reason.starts_with("airgapped is on, and /usr/bin/bwrap is refused a user namespace"), "{reason}");
        assert!(reason.contains("cannot be enforced") && reason.contains("sudo tee") && reason.contains("ling airgapped default off"));
    }

    #[test]
    fn airgapped_on_is_refused_not_claimed_without_the_sandbox() {
        assert_eq!(on_refusal(&Sandbox::Works, &Host::default()), None);
        let lines = on_refusal(&refused(), &ubuntu_2404(vec![])).expect("refused");
        assert!(lines[0].starts_with("Airgapped not changed: /usr/bin/bwrap is refused a user namespace"), "{lines:?}");
        assert!(lines.iter().any(|line| line.starts_with("printf ")));
        assert_eq!(lines.last().map(String::as_str), Some("Then restart ling."));
    }
}
