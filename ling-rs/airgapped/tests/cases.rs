//! The shared conformance cases (cases.json, AIRGAPPED §6.5): every `resolve` case, read as the
//! resolver reads its tiers, gives the level, the tier and the number of notes the case expects.

use std::path::PathBuf;

use ling_airgapped::{FileRead, Level, Source, read_file, resolve_guarded_read};
use serde_json::Value;

const CASES: &str = include_str!("../cases.json");

/// A file in a case: its text, or why it could not be read.
fn file(value: &Value) -> FileRead {
    match value.get("unreadable").and_then(Value::as_str) {
        Some(why) => Err(why.to_string()),
        None => Ok(value["text"]
            .as_str()
            .expect("text or unreadable")
            .to_string()),
    }
}

fn tier(source: &Source) -> (&'static str, Option<PathBuf>) {
    match source {
        Source::Session => ("session", None),
        Source::Sealed => ("sealed", None),
        Source::Environment => ("environment", None),
        Source::ConfigFile(path) => ("config", Some(path.clone())),
        Source::Default => ("default", None),
    }
}

#[test]
fn every_resolve_case_passes() {
    let cases: Value = serde_json::from_str(CASES).expect("cases.json is JSON");
    let cases = cases["resolve"].as_array().expect("resolve cases");
    assert!(cases.len() >= 20, "the resolve cases are there");
    for case in cases {
        let name = case["name"].as_str().unwrap_or("?");
        let session = case.get("session").map(file);
        let environment = case.get("environment").and_then(Value::as_str);
        let configs: Vec<(PathBuf, FileRead)> = case["configs"]
            .as_array()
            .expect("configs")
            .iter()
            .map(|config| {
                (
                    PathBuf::from(config["path"].as_str().expect("path")),
                    file(config),
                )
            })
            .collect();
        let flag = |key: &str| case.get(key).and_then(Value::as_bool).unwrap_or(false);
        let resolved = resolve_guarded_read(
            session.as_ref(),
            environment,
            &configs,
            flag("sealed"),
            flag("exposed"),
        );
        let expect = &case["expect"];
        let level = Level::parse(expect["level"].as_str().expect("level")).expect("a level");
        let path = expect
            .get("path")
            .and_then(Value::as_str)
            .map(PathBuf::from);
        assert_eq!(resolved.level, level, "{name}: {resolved:?}");
        assert_eq!(
            tier(&resolved.source),
            (expect["tier"].as_str().unwrap(), path),
            "{name}"
        );
        assert_eq!(
            resolved.invalid.len() as u64,
            expect["notes"].as_u64().unwrap(),
            "{name}: {:?}",
            resolved.invalid
        );
        assert_eq!(
            resolved.source.tier(),
            expect["tier"].as_str().unwrap(),
            "{name}"
        );
    }
}

/// A config slot that is a directory is not an absent file: it reads as unreadable, and so as `on`
/// (the "unreadable" cases above), never as passed over.
#[test]
fn a_directory_in_a_config_slot_is_unreadable_not_absent() {
    let dir = std::env::temp_dir().join(format!("ling-airgapped-slot-{}", std::process::id()));
    std::fs::create_dir_all(dir.join("dreamference.toml")).unwrap();
    let read = read_file(&dir.join("dreamference.toml"));
    std::fs::remove_dir_all(&dir).unwrap();
    assert!(matches!(read, Some(Err(_))), "{read:?}");
    assert!(read_file(&dir.join("dreamference.toml")).is_none());
}
