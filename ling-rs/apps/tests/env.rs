//! What reads the environment and the home folder, in a process of its own: each step sets the
//! variables it needs, which tests running alongside would otherwise see.

use ling_apps::Service;
use std::ffi::OsStr;
use std::path::PathBuf;

fn set(key: &str, value: impl AsRef<OsStr>) {
    // SAFETY: this file's one test runs alone in its process, and nothing else reads the environment.
    unsafe { std::env::set_var(key, value) }
}

fn unset(key: &str) {
    // SAFETY: as in `set`.
    unsafe { std::env::remove_var(key) }
}

#[test]
fn the_node_the_switch_and_the_air_gap_decide_what_is_offered() {
    let root = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join(format!("apps-env-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let home = root.join("home");
    let run = root.join("run");
    std::fs::create_dir_all(&home).unwrap_or_default();
    std::fs::create_dir_all(&run).unwrap_or_default();
    set("HOME", &home);
    set("XDG_RUNTIME_DIR", &run);
    set("CODEX_HOME", root.join("codex"));
    set("DREAMFERENCE_CONFIG_PATH", root.join("no-config.toml"));
    unset("TMPDIR");
    unset(ling_apps::ENV_SWITCH);
    unset(ling_apps::mcp::SECRET_FILE_ENV);
    // At `on` while the offer is checked, so `list` never asks the service on loopback.
    set(ling_airgapped::ENV_VAR, "on");

    // Not a node: nothing is offered, and Codex's own path answers `/apps`.
    assert!(!ling_apps::is_node());
    assert!(!ling_apps::offered());
    assert_eq!(ling_apps::list(), None);
    let node_id = home.join(".config/dreamference/node-id");
    std::fs::create_dir_all(&node_id).unwrap_or_default();
    assert!(!ling_apps::is_node(), "a folder named node-id is no node id");
    let _ = std::fs::remove_dir(&node_id);
    std::fs::write(&node_id, "node-1\n").unwrap_or_default();
    assert!(ling_apps::is_node());
    assert!(ling_apps::offered());
    assert_eq!(ling_apps::list(), Some(ling_apps::rows(&Service::Up(Vec::new()), true)));
    for off in ["off", " FALSE "] {
        set(ling_apps::ENV_SWITCH, off);
        assert!(!ling_apps::offered(), "{off}");
        assert_eq!(ling_apps::list(), None, "{off}");
    }
    set(ling_apps::ENV_SWITCH, "1");
    assert!(ling_apps::offered());

    // The air gap: the configured level, or a seal the session's process wrote.
    let seals = run.join(ling_airgapped::SEAL_DIR);
    assert_eq!(ling_airgapped::seal_dir(), Some(seals.clone()), "the runtime folder lies outside the writable roots");
    assert!(!ling_apps::any_sealed(), "no folder, no seal");
    std::fs::create_dir_all(&seals).unwrap_or_default();
    assert!(!ling_apps::any_sealed(), "an empty folder holds no seal");
    assert!(ling_apps::air_gapped_for(4242), "the configured level alone");
    set(ling_airgapped::ENV_VAR, "off");
    assert!(!ling_apps::air_gapped_for(4242));
    std::fs::write(seals.join("0199a1b2-thread"), "4242\n").unwrap_or_default();
    assert!(ling_apps::any_sealed());
    assert!(ling_apps::air_gapped_for(4242), "a seal the session wrote");
    assert!(!ling_apps::air_gapped_for(77), "another session's seal");

    // The shared secret: the file `ling-admin gmail` writes, unless a test names another.
    assert_eq!(ling_apps::mcp::secret_file(), Some(home.join(".config/dreamference/gmail/service-secret")));
    set(ling_apps::mcp::SECRET_FILE_ENV, root.join("secret"));
    assert_eq!(ling_apps::mcp::secret_file(), Some(root.join("secret")));
    let _ = std::fs::remove_dir_all(&root);
}
