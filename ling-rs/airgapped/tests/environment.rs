//! The level as this process resolves it, from its environment, working directory and runtime
//! folder: the seal and the guard on an exposed session file are applied by `resolve_in` itself,
//! not only by `resolve_guarded` on given facts. One test, alone in its binary, because it sets
//! the process's environment and working directory.

#![cfg(unix)]

use ling_airgapped::*;
use std::path::{Path, PathBuf};

fn set(key: &str, value: Option<&Path>) {
    // SAFETY: the only test in this binary, so no other thread reads the environment.
    unsafe {
        match value {
            Some(value) => std::env::set_var(key, value),
            None => std::env::remove_var(key),
        }
    }
}

fn write(path: &Path, text: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

#[test]
fn the_guards_bind_on_the_files_and_folders_this_process_finds() {
    let base = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join(format!("airgapped-environment-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    let base = std::fs::canonicalize(&base).unwrap();
    if base.starts_with("/tmp") {
        // Commands can write all of /tmp: every file here would be exposed.
        eprintln!("skipped: {} lies under /tmp", base.display());
        return;
    }
    let home = base.join("home");
    let work = base.join("work");
    let run = base.join("run");
    let home_run = home.join("run");
    for dir in [&home, &work, &run, &home_run, &base.join("tmp")] {
        std::fs::create_dir_all(dir).unwrap();
    }
    for var in [
        "CODEX_HOME",
        "DREAMFERENCE_CONFIG_PATH",
        ENV_VAR,
        READ_ONLY_NETWORK_ENV_VAR,
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
        "USERPROFILE",
    ] {
        set(var, None);
    }
    set("HOME", Some(&home));
    set("TMPDIR", Some(&base.join("tmp")));
    let id = "0b6e5c1a-7f43-4d1e-9c2a-5d8e3f0a1b2c";
    let user_config = home.join(".config/dreamference/config.toml");
    write(&user_config, "mightling_airgapped = \"on\"\n");
    write(&home.join(".mightling").join(SESSION_DIR).join(id), "off\n");

    // Started in the home folder, which also holds the runtime folder: nowhere to keep a seal,
    // and a command could have rewritten the session file, so it may not loosen the user's `on`.
    std::env::set_current_dir(&home).unwrap();
    set("XDG_RUNTIME_DIR", Some(&home_run));
    assert_eq!(seal_dir(), None);
    assert!(level_files_exposed());
    let resolved = resolve_in(&[id], None, &home);
    assert_eq!(resolved.level, Level::On);
    assert_eq!(resolved.source, Source::ConfigFile(user_config.clone()));

    // Each of the two level files alone makes them exposed.
    set("CODEX_HOME", Some(&base.join("codex")));
    assert!(level_files_exposed()); // The user's configuration file, in the home folder.
    std::env::set_current_dir(&work).unwrap();
    assert!(!level_files_exposed());
    set("CODEX_HOME", Some(&work.join("codex")));
    assert!(level_files_exposed()); // $CODEX_HOME, in the working directory.

    // Started elsewhere, with a runtime folder commands cannot write: the session file is not
    // exposed and loosens the level, until a seal holds the session at `on`.
    set("CODEX_HOME", Some(&base.join("codex")));
    set("XDG_RUNTIME_DIR", Some(&run));
    assert_eq!(seal_dir(), Some(run.join(SEAL_DIR)));
    write(&base.join("codex").join(SESSION_DIR).join(id), "off\n");
    let resolved = resolve_in(&[id], None, &work);
    assert_eq!(
        (resolved.level, resolved.source),
        (Level::Off, Source::Session)
    );
    write(&user_config, "mightling_airgapped = \"off\"\n");
    write(&run.join(SEAL_DIR).join(id), "");
    let resolved = resolve_in(&[id], None, &work);
    assert_eq!(
        (resolved.level, resolved.source),
        (Level::On, Source::Sealed)
    );
    // A subagent without a file of its own is held by its root session's seal.
    let other = "1c2d3e4f-0000-4000-8000-000000000000";
    assert_eq!(resolve_in(&[other, id], None, &work).level, Level::On);

    // The command's own ids, from its environment or from the map Codex hands over.
    assert!(!sealed_for_command());
    set("CODEX_THREAD_ID", Some(Path::new(id)));
    assert!(sealed_for_command());
    set("CODEX_THREAD_ID", None);
    let thread = |key: &str| (key == "CODEX_THREAD_ID").then(|| id.to_string());
    assert!(sealed_for_env(thread));
    assert!(!read_only_network(thread));

    // With no level in the command's map, the one this process holds is the command's too.
    std::fs::remove_file(run.join(SEAL_DIR).join(id)).unwrap();
    assert!(!sealed_for_env(|_| None));
    assert!(read_only_network(|_| None));
    set(ENV_VAR, Some(Path::new("on")));
    assert!(sealed_for_env(|_| None));
    assert!(!read_only_network(|_| None));
    assert!(!sealed_for_env(
        |key| (key == ENV_VAR).then(|| "off".to_string())
    ));
    set(ENV_VAR, None);

    // A named configuration file replaces the working directory's.
    let named = base.join("named.toml");
    set("DREAMFERENCE_CONFIG_PATH", Some(&named));
    assert_eq!(config_files(&work), vec![named, user_config]);
    set("DREAMFERENCE_CONFIG_PATH", None);

    // An empty $CODEX_HOME is an unset one.
    set("CODEX_HOME", Some(Path::new("")));
    assert_eq!(codex_home(), Some(home.join(".mightling")));

    let _ = std::fs::remove_dir_all(&base);
}
