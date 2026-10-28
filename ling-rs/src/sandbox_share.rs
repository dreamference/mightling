//! The elevated Windows sandbox on a PC with several users (WINDOWS_ARM §23.4 W11).
//!
//! The elevated sandbox runs commands as two machine-wide accounts, `CodexSandboxOffline` and
//! `CodexSandboxOnline`. Each user's setup (`ling sandbox setup --elevated`, which `install.ps1`'s
//! elevated step runs) gives both accounts fresh random passwords and keeps them, protected with
//! machine-scope DPAPI, in that user's `CODEX_HOME\.sandbox-secrets\sandbox_users.json`. So the
//! second user's setup changed the passwords the first user's file still held: every other user's
//! elevated sandbox then failed to log on, and asked for an administrator again, whose setup broke
//! the next one. Seen on the test PC: one test user's setup broke the other's and the owner's.
//!
//! What the launcher does, without a Codex patch: it runs an elevated setup as a child, and when it
//! succeeds, writes the new passwords file into the `.sandbox-secrets` of every user on this PC who
//! already has one (their `~\.mightling`), in place, so that file keeps its own ACL. Nobody who
//! had not set up learns the passwords: only files that exist are rewritten, and only when they
//! name the same two accounts. A folder or file that is a link or junction is skipped, so a user
//! cannot point the administrator's write somewhere else. A `CODEX_HOME` outside `~\.mightling`
//! is not found; such a user runs setup again.

use std::path::Path;
use std::path::PathBuf;

/// Set for the child the launcher runs the setup as, so the child does not wrap itself again.
pub const CHILD_ENV: &str = "DREAMFERENCE_MIGHTLING_SANDBOX_SETUP_CHILD";

/// The passwords file, relative to a `CODEX_HOME`.
const SECRETS_FILE: [&str; 2] = [".sandbox-secrets", "sandbox_users.json"];

/// Whether `user_args`, with the subcommand at `index`, are an elevated sandbox setup.
pub fn is_elevated_setup(user_args: &[String], index: usize) -> bool {
    user_args.get(index).map(String::as_str) == Some("sandbox")
        && user_args.get(index + 1).map(String::as_str) == Some("setup")
        && user_args.iter().skip(index + 2).any(|arg| arg == "--elevated")
}

/// The `CODEX_HOME` the setup writes: its `--codex-home`, else the environment's, else the default.
pub fn codex_home_of(user_args: &[String], from_env: Option<PathBuf>, default: Option<PathBuf>) -> Option<PathBuf> {
    let mut args = user_args.iter();
    while let Some(arg) = args.next() {
        if arg == "--codex-home" {
            return args.next().map(PathBuf::from);
        }
        if let Some(value) = arg.strip_prefix("--codex-home=") {
            return Some(PathBuf::from(value));
        }
    }
    from_env.or(default)
}

/// The two account names a passwords file holds, if it is one.
fn accounts(text: &[u8]) -> Option<(String, String)> {
    let value: serde_json::Value = serde_json::from_slice(text).ok()?;
    let name = |key: &str| value.get(key)?.get("username")?.as_str().map(str::to_string);
    Some((name("offline")?, name("online")?))
}

/// Whether `path` or any of its parents below `stop` is a link or junction.
fn through_a_link(path: &Path, stop: &Path) -> bool {
    path.ancestors()
        .take_while(|here| *here != stop)
        .any(|here| std::fs::symlink_metadata(here).map(|meta| meta.file_type().is_symlink()).unwrap_or(true))
}

/// The other users' passwords files under `profiles` (each `<profile>\.mightling\…`) that name the
/// same accounts as `source`, skipping `source_home` and anything reached through a link.
pub fn targets(profiles: &Path, source_home: &Path, source: &[u8]) -> Vec<PathBuf> {
    let Some(wanted) = accounts(source) else { return Vec::new() };
    let source_file = SECRETS_FILE.iter().fold(source_home.to_path_buf(), |path, part| path.join(part));
    let Ok(entries) = std::fs::read_dir(profiles) else { return Vec::new() };
    let mut found: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .map(|profile| SECRETS_FILE.iter().fold(profile.join(".mightling"), |path, part| path.join(part)))
        .filter(|file| file.is_file() && !same_file(file, &source_file))
        .filter(|file| !through_a_link(file, profiles))
        .filter(|file| std::fs::read(file).ok().and_then(|text| accounts(&text)).as_ref() == Some(&wanted))
        .collect();
    found.sort();
    found
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => a == b,
    }
}

/// Writes `source` into each target in place (truncating, so the file keeps its ACL). Returns
/// how many were written and a line for each that could not be.
pub fn share_into(source: &[u8], files: &[PathBuf]) -> (usize, Vec<String>) {
    use std::io::Write;
    let mut written = 0;
    let mut failed = Vec::new();
    for file in files {
        let result = std::fs::OpenOptions::new().write(true).truncate(true).open(file).and_then(|mut handle| handle.write_all(source));
        match result {
            Ok(()) => written += 1,
            Err(err) => failed.push(format!("{}: {err}", file.display())),
        }
    }
    (written, failed)
}

/// Runs the elevated setup as a child, then shares its passwords with the users already set up.
/// Returns the setup's exit code.
pub fn run(user_args: &[String]) -> i32 {
    let Ok(exe) = std::env::current_exe() else {
        eprintln!("❌ could not find the running ling");
        return 1;
    };
    let status = std::process::Command::new(&exe).args(user_args).env(CHILD_ENV, "1").status();
    let code = match status {
        Ok(status) => status.code().unwrap_or(1),
        Err(err) => {
            eprintln!("❌ could not run the sandbox setup: {err}");
            return 1;
        }
    };
    if code != 0 {
        return code;
    }
    let home = ling_node_locator::home_dir();
    let Some(codex_home) = codex_home_of(user_args, std::env::var_os("CODEX_HOME").map(PathBuf::from), home.as_ref().map(|home| home.join(".mightling"))) else {
        return code;
    };
    let source_file = SECRETS_FILE.iter().fold(codex_home.clone(), |path, part| path.join(part));
    let (Ok(source), Some(profiles)) = (std::fs::read(&source_file), home.as_deref().and_then(Path::parent)) else {
        return code;
    };
    let files = targets(profiles, &codex_home, &source);
    let (written, failed) = share_into(&source, &files);
    if written > 0 {
        println!("The sandbox accounts' new passwords were given to {written} other user(s) of this PC set up before, so their sandbox keeps working.");
    }
    for line in failed {
        eprintln!("⚠️  Could not give the sandbox accounts' new passwords to {line}; that user runs ling sandbox setup again.");
    }
    code
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(text: &str) -> Vec<String> {
        text.split_whitespace().map(str::to_string).collect()
    }

    const FILE: &str = r#"{"version":5,"offline":{"username":"CodexSandboxOffline","password":"bmV3"},"online":{"username":"CodexSandboxOnline","password":"bmV3"}}"#;
    const OLD: &str = r#"{"version":5,"offline":{"username":"CodexSandboxOffline","password":"b2xk"},"online":{"username":"CodexSandboxOnline","password":"b2xk"}}"#;

    #[test]
    fn only_an_elevated_setup_is_wrapped() {
        assert!(is_elevated_setup(&args("sandbox setup --elevated --user pc\\a"), 0));
        assert!(is_elevated_setup(&args("-c x=1 sandbox setup --user pc\\a --elevated"), 2));
        assert!(!is_elevated_setup(&args("sandbox setup"), 0));
        assert!(!is_elevated_setup(&args("sandbox -- cmd /c echo --elevated"), 0));
        assert!(!is_elevated_setup(&args("exec --elevated"), 0));
    }

    #[test]
    fn the_codex_home_is_the_flag_then_the_environment_then_the_default() {
        let env = Some(PathBuf::from("env"));
        let default = Some(PathBuf::from("default"));
        assert_eq!(codex_home_of(&args("sandbox setup --elevated --codex-home C:\\h"), env.clone(), default.clone()), Some(PathBuf::from("C:\\h")));
        assert_eq!(codex_home_of(&args("sandbox setup --codex-home=C:\\h2"), env.clone(), default.clone()), Some(PathBuf::from("C:\\h2")));
        assert_eq!(codex_home_of(&args("sandbox setup"), env.clone(), default.clone()), env);
        assert_eq!(codex_home_of(&args("sandbox setup"), None, default.clone()), default);
    }

    fn secrets(profile: &Path, text: &str) -> PathBuf {
        let dir = profile.join(".mightling").join(".sandbox-secrets");
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("sandbox_users.json");
        std::fs::write(&file, text).unwrap();
        file
    }

    #[test]
    fn the_new_passwords_go_to_users_already_set_up_and_nowhere_else() {
        let root = std::env::temp_dir().join(format!("mightling-sandbox-share-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let admin = root.join("admin");
        let source = secrets(&admin, FILE);
        let earlier = secrets(&root.join("earlier"), OLD);
        let other_accounts = secrets(&root.join("other"), r#"{"offline":{"username":"X"},"online":{"username":"Y"}}"#);
        let not_json = secrets(&root.join("broken"), "not json");
        std::fs::create_dir_all(root.join("never").join(".mightling")).unwrap();
        let files = targets(&root, &admin.join(".mightling"), FILE.as_bytes());
        assert_eq!(files, vec![earlier.clone()]);
        assert_eq!(share_into(FILE.as_bytes(), &files), (1, vec![]));
        assert_eq!(std::fs::read_to_string(&earlier).unwrap(), FILE);
        assert!(std::fs::read_to_string(&other_accounts).unwrap().contains("\"X\""));
        assert_eq!(std::fs::read_to_string(&not_json).unwrap(), "not json");
        assert_eq!(std::fs::read_to_string(&source).unwrap(), FILE);
        assert!(!root.join("never").join(".mightling").join(".sandbox-secrets").exists());
        assert!(targets(&root, &admin.join(".mightling"), b"not json").is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn a_folder_reached_through_a_link_is_skipped() {
        let root = std::env::temp_dir().join(format!("mightling-sandbox-share-link-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let profiles = root.join("profiles");
        std::fs::create_dir_all(&profiles).unwrap();
        let elsewhere = root.join("elsewhere");
        secrets(&elsewhere, OLD);
        std::fs::create_dir_all(profiles.join("linked")).unwrap();
        std::os::unix::fs::symlink(elsewhere.join(".mightling"), profiles.join("linked").join(".mightling")).unwrap();
        assert!(targets(&profiles, &root.join("admin").join(".mightling"), FILE.as_bytes()).is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }
}
