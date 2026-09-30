//! The router against recorded stores (spec §12): answers on an unchanged tree, and the changed
//! set of §7.3 after edits, deletions, a lost snapshot commit and an oversized change.

mod common;

use common::{fixture, git};
use puffin_code::manifest::Manifest;

fn rows<'a>(out: &'a str, tag: &str) -> Vec<&'a str> {
    out.lines().filter(|l| l.starts_with(&format!("{tag} ")) && !l.starts_with(&format!("{tag} ="))).collect()
}

#[test]
fn unchanged_tree_is_all_exact_and_says_so() {
    let f = fixture();
    let (code, out) = f.run(&["refs", "make_circle"]);
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("changed since snapshot: 0 files\n"), "{out}");
    assert_eq!(rows(&out, "exact").len(), 6, "{out}");
    assert!(rows(&out, "heuristic").is_empty() && !out.contains("heuristic (text) "), "{out}");
    assert!(!out.contains("re-index requested"), "{out}");
}

#[test]
fn method_calls_through_an_instance_are_exact() {
    // The graph has no edge for `circle.area()` in report.py or `make_circle(3.0).area()` in
    // cli.py (the 52% case, §2); scip-python resolves both.
    let f = fixture();
    let (_, out) = f.run(&["refs", "geometry.Circle.area"]);
    assert!(out.contains("exact shapes/report.py:9"), "{out}");
    assert!(out.contains("exact shapes/cli.py:9"), "{out}");
}

#[test]
fn references_added_since_the_snapshot_are_never_silent() {
    let f = fixture();
    f.append("shapes/report.py", "\n\ndef extra():\n    return make_circle(2.0)\n");
    f.write("shapes/extra.py", "from shapes.geometry import make_circle\n\nCIRCLE = make_circle(5.0)\n");
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(out.contains("changed since snapshot: 2 files, all searched"), "{out}");
    assert!(out.contains("heuristic (text) shapes/report.py:17"), "{out}");
    assert!(out.contains("heuristic (text) shapes/extra.py:1"), "{out}");
    assert!(out.contains("heuristic (text) shapes/extra.py:3"), "{out}");
    // Files that did not change keep their exact rows.
    assert!(out.contains("exact shapes/cli.py:9"), "{out}");
    assert!(out.contains("re-index requested"), "{out}");
    // The request is one fixed word.
    let requests = std::fs::read_to_string(f.repo.state_dir().join("code_index.requests")).unwrap();
    assert!(requests.lines().all(|l| l == "index"), "{requests}");
}

#[test]
fn committed_edits_count_too() {
    let f = fixture();
    f.write("shapes/extra.py", "from shapes.geometry import make_circle\n\nCIRCLE = make_circle(5.0)\n");
    f.commit("add extra");
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(out.contains("changed since snapshot: 1 files, all searched"), "{out}");
    assert!(out.contains("heuristic (text) shapes/extra.py:3"), "{out}");
}

#[test]
fn a_file_stale_for_scip_but_fresh_for_the_graph_is_still_searched() {
    // The graph has been re-indexed since report.py changed, and still has no edge for
    // `circle.area()` there (the 58% case); the SCIP snapshot is older than the file. Only the
    // text search can report the call.
    let f = fixture();
    f.append("shapes/report.py", "\n# edited after the SCIP snapshot\n");
    f.commit("edit report");
    let head = git(&f.repo.root, &["rev-parse", "HEAD"]);
    let stamp = puffin_code::manifest::FileStamp::of(&f.repo.root.join("shapes/report.py")).unwrap();
    rusqlite::Connection::open(&f.graph_db)
        .unwrap()
        .execute(
            "UPDATE file_hashes SET sha256 = ?1, mtime_ns = ?2, size = ?3 WHERE rel_path = 'shapes/report.py'",
            rusqlite::params![stamp.sha256, stamp.mtime_ns, stamp.size as i64],
        )
        .unwrap();
    let dir = f.repo.scip_dir();
    let mut graph = puffin_code::manifest::GraphSnapshot::load(&dir).unwrap();
    graph.commit = Some(head);
    graph.save(&dir).unwrap();
    let (_, out) = f.run(&["refs", "geometry.Circle.area"]);
    assert!(out.contains("changed since snapshot: 1 files, all searched"), "{out}");
    assert!(out.contains("heuristic (text) shapes/report.py:9"), "{out}");
    assert!(!out.contains("exact shapes/report.py"), "stale SCIP rows must not be served: {out}");
    assert!(out.contains("exact shapes/cli.py:9"), "{out}");
}

#[test]
fn rows_in_deleted_files_are_dropped_and_counted() {
    let f = fixture();
    std::fs::remove_file(f.repo.root.join("shapes/cli.py")).unwrap();
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(!out.contains("shapes/cli.py"), "{out}");
    assert!(out.contains("dropped 2 rows in files deleted since the snapshot"), "{out}");
    assert!(out.contains("changed since snapshot: 1 files"), "{out}");
}

#[test]
fn too_many_changed_files_print_not_checked() {
    let f = fixture();
    f.append("shapes/report.py", "\nX = make_circle(1)\n");
    f.write("shapes/extra.py", "Y = make_circle(1)\n");
    let config = f.dir.path().join("dreamference.toml");
    std::fs::write(&config, "code_index_scan_max_files = 1\n").unwrap();
    let (_, out) = f.run_env(&["refs", "make_circle"], &[("DREAMFERENCE_CONFIG_PATH", config.to_str().unwrap())]);
    assert!(out.contains("changed since snapshot: 2 files, not searched"), "{out}");
    assert!(out.contains("not checked 2 changed files were not searched"), "{out}");
    assert!(!out.contains("heuristic (text)"), "{out}");
}

#[test]
fn a_lost_snapshot_commit_falls_back_to_stat() {
    let f = fixture();
    f.write("shapes/extra.py", "Y = make_circle(1)\n");
    let dir = f.repo.scip_dir();
    let mut manifest = Manifest::load(&dir);
    for entry in manifest.runs.values_mut() {
        entry.commit = Some("0".repeat(40));
    }
    manifest.save(&dir).unwrap();
    let mut graph = puffin_code::manifest::GraphSnapshot::load(&dir).unwrap();
    graph.commit = Some("1".repeat(40));
    graph.save(&dir).unwrap();
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(out.contains("heuristic (text) shapes/extra.py:1"), "{out}");
    assert!(out.contains("changed since snapshot: 1 files, all searched"), "{out}");
    assert!(out.contains("freshness was checked file by file"), "{out}");
    assert_eq!(rows(&out, "exact").len(), 6, "{out}");
}

#[test]
fn ambiguous_names_are_listed_not_guessed() {
    let f = fixture();
    let (_, out) = f.run(&["refs", "area"]);
    assert!(out.contains("(ambiguous)"), "{out}");
    assert_eq!(out.lines().filter(|l| l.starts_with("  ") && l.contains(".area")).count(), 5, "{out}");
    assert!(rows(&out, "exact").is_empty(), "{out}");
    // A location names one definition.
    let (_, out) = f.run(&["refs", "shapes/geometry.py:12"]);
    assert!(out.contains("refs shapes.geometry.Circle.area"), "{out}");
    assert!(out.contains("exact shapes/report.py:9"), "{out}");
}

#[test]
fn rust_trait_calls_and_implementations() {
    let f = fixture();
    let (_, out) = f.run(&["refs", "Shape::area"]);
    assert!(out.contains("exact geom/src/lib.rs:9"), "{out}");
    let (_, out) = f.run(&["impl", "Shape"]);
    assert!(out.contains("exact geom/src/shapes.rs:7") && out.contains("exact geom/src/shapes.rs:23"), "{out}");
    let (_, out) = f.run(&["impl", "Shape::area"]);
    assert!(out.contains("exact geom/src/shapes.rs:18") && out.contains("exact geom/src/shapes.rs:28"), "{out}");
}

#[test]
fn callers_and_callees() {
    let f = fixture();
    let (_, out) = f.run(&["callers", "geometry.Circle.area"]);
    assert!(out.contains("exact shapes/cli.py:9  in shapes.cli.main"), "{out}");
    assert!(out.contains("exact shapes/report.py:9  in shapes.report.summary"), "{out}");
    let (_, out) = f.run(&["callees", "summary"]);
    for callee in ["make_circle", "geometry.Square", "Circle.area", "Square.area"] {
        assert!(out.contains(callee), "{callee}: {out}");
    }
}

#[test]
fn a_definition_written_since_the_snapshot_is_found() {
    let f = fixture();
    f.write("shapes/hexagon.py", "class Hexagon:\n    def area(self):\n        return 6\n");
    let (_, out) = f.run(&["def", "Hexagon"]);
    assert!(out.contains("heuristic (text) shapes/hexagon.py:1"), "{out}");
    let (_, out) = f.run(&["def", "make_circle"]);
    assert!(out.contains("exact shapes/geometry.py:26"), "{out}");
    assert!(!out.contains("heuristic (text)"), "a use is not a definition: {out}");
}

#[test]
fn output_is_bounded_and_pages_are_stable() {
    let f = fixture();
    let calls: String = (0..500).map(|i| format!("C{i} = make_circle({i})\n")).collect();
    f.write("shapes/many.py", &format!("from shapes.geometry import make_circle\n{calls}"));
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(out.contains("showing 40 from 0; next: --offset 40 --cursor "), "{out}");
    let summary = out.lines().skip_while(|l| !l.starts_with("by file")).skip(1).take_while(|l| l.starts_with("  ")).count();
    assert!(summary <= 15, "{out}");
    let row_lines: Vec<&str> = out.lines().filter(|l| (l.starts_with("exact ") && !l.starts_with("exact =")) || l.starts_with("heuristic ") && !l.starts_with("heuristic =")).collect();
    assert_eq!(row_lines.len(), 40, "{out}");
    // Exact rows come first even though text rows outnumber them.
    assert!(row_lines[0].starts_with("exact "), "{out}");
    let cursor = out.split("--cursor ").nth(1).unwrap().split(')').next().unwrap().to_string();
    let (_, page2) = f.run(&["refs", "make_circle", "--offset", "40", "--cursor", &cursor]);
    let second: Vec<&str> = page2.lines().filter(|l| l.starts_with("heuristic (text) ")).collect();
    assert_eq!(second.len(), 40, "{page2}");
    assert!(second.iter().all(|l| !row_lines.contains(l)), "pages overlap");
    // The tree changes: the old cursor is refused rather than silently shifted.
    f.write("shapes/more.py", "Z = make_circle(1)\n");
    let (code, stale) = f.run(&["refs", "make_circle", "--offset", "40", "--cursor", &cursor]);
    assert_ne!(code, 0);
    assert!(stale.contains("the index changed since the first page"), "{stale}");
    // Tokens: a full page stays under about 1,000 tokens (four characters a token).
    assert!(out.len() / 4 < 1000, "{} chars", out.len());
}

#[test]
fn json_output() {
    let f = fixture();
    let (_, out) = f.run(&["refs", "make_circle", "--json"]);
    let value: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(value["rows"].as_array().unwrap().len(), 6);
    assert_eq!(value["rows"][0]["tag"], "exact");
    assert_eq!(value["changed_files"], 0);
}

#[test]
fn an_unknown_store_schema_is_not_read() {
    let f = fixture();
    let db = f.repo.scip_dir().join("scip-python-shapes.db");
    rusqlite::Connection::open(&db).unwrap().execute_batch("CREATE TABLE extra (x);").unwrap();
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(out.contains("exact layer unavailable: scip-python:shapes"), "{out}");
    // The graph still answers, and says it is approximate.
    assert!(out.contains("heuristic shapes/cli.py:9"), "{out}");
    assert!(!out.contains("exact shapes/"), "{out}");
}

#[test]
fn an_unknown_graph_schema_is_not_read() {
    let f = fixture();
    rusqlite::Connection::open(&f.graph_db).unwrap().execute_batch("CREATE TABLE extra (x);").unwrap();
    let (_, out) = f.run(&["refs", "make_circle"]);
    assert!(out.contains("universal layer unavailable"), "{out}");
    assert_eq!(rows(&out, "exact").len(), 6, "{out}");
}

#[test]
fn worktrees_read_the_main_index_and_tag_their_own_edits() {
    let f = fixture();
    let wt = f.dir.path().join("wt");
    git(&f.repo.root, &["worktree", "add", "-q", wt.to_str().unwrap()]);
    std::fs::write(wt.join("shapes/extra.py"), "Y = make_circle(1)\n").unwrap();
    let (_, out) = f.run_in(&wt, &["refs", "make_circle"], &[]);
    assert!(out.contains("exact shapes/cli.py:9"), "{out}");
    assert!(out.contains("heuristic (text) shapes/extra.py:1"), "{out}");
    assert!(!wt.join(".dreamference").exists(), "a worktree gets no index of its own");
}

#[test]
fn search_outline_show() {
    let f = fixture();
    let (_, out) = f.run(&["search", "circle"]);
    assert!(out.contains("shapes/geometry.py:26  function shapes.geometry.make_circle"), "{out}");
    let (_, out) = f.run(&["outline", "shapes/report.py"]);
    assert!(out.contains("shapes/report.py:6  function shapes.report.summary (to 9)"), "{out}");
    let (_, out) = f.run(&["show", "summary"]);
    assert!(out.contains("    9      return circle.area() + square.area()"), "{out}");
}

#[test]
fn no_index_says_so() {
    let dir = tempfile::tempdir().unwrap();
    git(dir.path(), &["init", "-q"]);
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_puffin-code"))
        .args(["refs", "x"])
        .current_dir(dir.path())
        .env("HOME", dir.path())
        .env("CBM_CACHE_DIR", dir.path().join("cache"))
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(3));
    assert!(String::from_utf8_lossy(&out.stdout).contains("run `puffin-code index`"));
}


#[test]
fn the_same_answers_over_mcp() {
    use std::io::Write;
    let f = fixture();
    let mut child = f
        .command(&["mcp"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    writeln!(stdin, r#"{{"jsonrpc":"2.0","id":1,"method":"initialize","params":{{"protocolVersion":"2025-06-18"}}}}"#).unwrap();
    writeln!(stdin, r#"{{"jsonrpc":"2.0","method":"notifications/initialized"}}"#).unwrap();
    writeln!(stdin, r#"{{"jsonrpc":"2.0","id":2,"method":"tools/list"}}"#).unwrap();
    writeln!(stdin, r#"{{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{{"name":"code_refs","arguments":{{"name":"make_circle"}}}}}}"#).unwrap();
    drop(stdin);
    let out = child.wait_with_output().unwrap();
    let lines: Vec<serde_json::Value> = String::from_utf8_lossy(&out.stdout).lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(lines.len(), 3);
    assert_eq!(lines[0]["result"]["serverInfo"]["name"], "puffin-code");
    assert!(lines[1]["result"]["tools"].as_array().unwrap().iter().any(|t| t["name"] == "code_refs"));
    let text = lines[2]["result"]["content"][0]["text"].as_str().unwrap();
    assert!(text.contains("exact shapes/cli.py:9"), "{text}");
}
