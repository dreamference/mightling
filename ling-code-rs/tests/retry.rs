//! The concurrency rule of spec §7.5: codebase-memory's database uses a rollback journal, so an
//! answer read while it committed a re-index may mix both sides. The context notices a new
//! `mutation_gen` and the caller answers again, once. Its own binary: the graph is located through
//! the environment, which this process sets.

mod common;

use common::fixture;
use ling_code::config::Settings;
use ling_code::router::Context;

#[test]
fn a_new_mutation_generation_asks_for_one_more_answer() {
    let f = fixture();
    let isolated = [
        ("MIGHTLING_CODE_GRAPH_DB", f.graph_db.clone()),
        ("HOME", f.home.clone()),
        ("XDG_RUNTIME_DIR", f.dir.path().join("run")),
        ("MIGHTLING_CODE_TOOLS_DIR", f.dir.path().join("no-tools")),
        (
            "MIGHTLING_CODE_INDEXERS_DIR",
            f.dir.path().join("no-indexers"),
        ),
        ("MIGHTLING_CODE_SCRATCH_DIR", f.dir.path().join("scratch")),
        ("CBM_CACHE_DIR", f.dir.path().join("cbm-cache")),
    ];
    for (key, value) in &isolated {
        std::env::set_var(key, value);
    }
    std::env::set_var("MIGHTLING_CODE_PROJECT", &f.project);
    std::env::set_var("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent");
    for key in [
        "DREAMFERENCE_CONFIG_PATH",
        "CODEX_HOME",
        "MIGHTLING_CODE_STATE_DIR",
        "MIGHTLING_CODE_ROOT",
    ] {
        std::env::remove_var(key);
    }

    let context = Context::load(f.repo.clone(), Settings::default()).unwrap();
    assert!(context.has_index());
    assert!(
        !context.graph_changed_since_load(),
        "an untouched graph must not cost a second answer"
    );

    // codebase-memory commits a re-index while the answer is being read.
    rusqlite::Connection::open(&f.graph_db)
        .unwrap()
        .execute(
            "UPDATE store_meta SET v = 'after-reindex' WHERE k = 'mutation_gen'",
            [],
        )
        .unwrap();
    assert!(context.graph_changed_since_load());

    // The answer read again starts from the new generation, so it is not retried a second time.
    let reloaded = Context::load(f.repo.clone(), Settings::default()).unwrap();
    assert!(!reloaded.graph_changed_since_load());
}
