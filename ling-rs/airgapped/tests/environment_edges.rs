//! More of the level as this process resolves it from its environment: the temporary folders that
//! count as writable, a relative runtime folder, seal ids, which session file wins, the variables
//! read for a command and the refusals of a permission profile. One test, alone in its binary,
//! because it sets the process's environment and working directory.

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
fn the_process_environment_is_read_as_the_command_would_see_it() {
    let base = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "airgapped-environment-edges-{}",
        std::process::id()
    ));
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
    let codex = base.join("codex");
    let tmp = base.join("tmp");
    for dir in [&home, &work, &run, &codex, &tmp] {
        std::fs::create_dir_all(dir).unwrap();
    }
    for var in [
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
    set("CODEX_HOME", Some(&codex));
    set("TMPDIR", Some(&tmp));
    set("XDG_RUNTIME_DIR", Some(&run));
    std::env::set_current_dir(&work).unwrap();
    assert_eq!(seal_dir(), Some(run.join(SEAL_DIR)));
    assert!(!level_files_exposed());

    // An empty TMPDIR names no folder; one naming the runtime folder leaves no place for seals.
    set("TMPDIR", Some(Path::new("")));
    assert_eq!(seal_dir(), Some(run.join(SEAL_DIR)));
    assert!(!level_files_exposed());
    set("TMPDIR", Some(&run));
    assert_eq!(seal_dir(), None);
    set("TMPDIR", Some(&tmp));

    // A seal id is a file name in the seals' folder, never a path out of it.
    std::fs::create_dir_all(run.join(SEAL_DIR)).unwrap();
    write(&run.join("stray"), "");
    assert!(!sealed(&["../stray"]));

    // The first id with a session file decides, as the thread's own comes before its root's.
    let (thread, root) = (
        "0b6e5c1a-7f43-4d1e-9c2a-5d8e3f0a1b2c",
        "1c2d3e4f-0000-4000-8000-000000000000",
    );
    write(&codex.join(SESSION_DIR).join(thread), "off\n");
    write(&codex.join(SESSION_DIR).join(root), "on\n");
    assert_eq!(resolve_in(&[thread, root], None, &work).level, Level::Off);
    assert_eq!(resolve_in(&[root, thread], None, &work).level, Level::On);
    set("CODEX_THREAD_ID", Some(Path::new(thread)));
    set("CODEX_SESSION_ID", Some(Path::new(root)));
    assert_eq!(resolve_for_command().level, Level::Off);
    set("CODEX_THREAD_ID", None);
    set("CODEX_SESSION_ID", None);

    // This process's level variable is a tier of its own resolution.
    set(ENV_VAR, Some(Path::new("on")));
    let resolved = resolve(&[]);
    assert_eq!(
        (resolved.level, resolved.source),
        (Level::On, Source::Environment)
    );
    // Full Access is refused at a configured `on`, and only then.
    assert_eq!(
        permission_refusal(true, &[]).map(|(field, _, _)| field),
        Some("sandbox_mode")
    );
    set(ENV_VAR, None);
    assert_eq!(permission_refusal(true, &[]), None);

    // A root holding the seals is refused once a session is sealed, not for an empty folder.
    assert_eq!(permission_refusal(false, std::slice::from_ref(&run)), None);
    write(&run.join(SEAL_DIR).join(root), "");
    assert_eq!(
        permission_refusal(false, std::slice::from_ref(&run)).map(|(field, _, _)| field),
        Some("writable_roots")
    );
    std::fs::remove_file(run.join(SEAL_DIR).join(root)).unwrap();

    // The user can keep the network from the read-only sandbox.
    assert!(read_only_network(|_| None));
    set(READ_ONLY_NETWORK_ENV_VAR, Some(Path::new("off")));
    assert!(!read_only_network(|_| None));
    set(READ_ONLY_NETWORK_ENV_VAR, None);

    // The runtime folder's own sockets are found, and denied at `on` only.
    write(&run.join("bus"), "");
    assert!(host_sockets().contains(&run.join("bus")));
    assert!(host_sockets_at(true).contains(&run.join("bus")));
    assert_eq!(host_sockets_at(false), Vec::<PathBuf>::new());

    // A named configuration file that is the user's own is read once.
    let user_config = home.join(".config/dreamference/config.toml");
    set("DREAMFERENCE_CONFIG_PATH", Some(&user_config));
    assert_eq!(config_files(&work), vec![user_config]);
    set("DREAMFERENCE_CONFIG_PATH", None);

    // A relative runtime folder is not taken: its seals would be wherever the command works.
    // (The real `/run/user/<uid>` may be taken instead; a made-up id has no seal there.)
    let id = "2d3e4f5a-0000-4000-8000-000000000000";
    write(&work.join("rel").join(SEAL_DIR).join(id), "");
    set("XDG_RUNTIME_DIR", Some(Path::new("rel")));
    assert!(!sealed(&[id]));

    let _ = std::fs::remove_dir_all(&base);
}
