//! End to end, without systemd or bwrap: discovery, extraction by the real worker, chunking,
//! the test embedder, search, read, the prompt block and the MCP server, over a scratch home.
//! The host runs each scope's command directly; nothing here reaches the user's systemd, home or
//! network. PDF cases run when `MIGHTLING_DOCS_LIB_DIR` names a directory with `libpdfium.so`.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use ling_docs::collections::DocsToml;
use ling_docs::config::Settings;
use ling_docs::embed::HashEmbedder;
use ling_docs::host::{Host, LiveScope};
use ling_docs::index::{self, Env, Outcome};
use ling_docs::search::{Filters, Searcher};

/// Runs a scope's argv as a plain child.
struct LocalHost {
    dir: PathBuf,
    started: Mutex<Vec<(String, u64)>>,
}

impl Host for LocalHost {
    fn mem_available(&self) -> u64 { 100 << 30 }
    fn mem_total(&self) -> u64 { 121 << 30 }
    fn earlyoom_percent(&self) -> Option<f64> { Some(5.0) }
    fn live_scopes(&self) -> Vec<LiveScope> { Vec::new() }
    fn set_slice_limits(&self, _: u64) -> anyhow::Result<()> { Ok(()) }
    fn can_create_scopes(&self) -> bool { true }
    fn start_scope(&self, unit: &str, cap: u64, argv: &[String], log: &Path) -> anyhow::Result<Child> {
        self.started.lock().unwrap().push((unit.to_string(), cap));
        let log = std::fs::File::create(log)?;
        Ok(Command::new(&argv[0]).args(&argv[1..]).stdin(Stdio::null()).stdout(log.try_clone()?).stderr(log).spawn()?)
    }
    fn freeze(&self, _: &str) -> anyhow::Result<()> { Ok(()) }
    fn thaw(&self, _: &str) -> anyhow::Result<()> { Ok(()) }
    fn stop(&self, _: &str) -> anyhow::Result<()> { Ok(()) }
    fn lock_dir(&self) -> PathBuf { self.dir.clone() }
}

fn exe() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_ling-docs"))
}

fn lib_dir() -> Option<PathBuf> {
    std::env::var_os("MIGHTLING_DOCS_LIB_DIR").map(PathBuf::from).filter(|d| d.join("libpdfium.so").is_file())
}

/// A one-page PDF whose text layer is `lines`, in Helvetica.
fn pdf(lines: &[&str]) -> Vec<u8> {
    let mut content = String::from("BT /F1 12 Tf 72 720 Td 14 TL\n");
    for line in lines {
        content.push_str(&format!("({}) Tj T*\n", line.replace('(', "\\(").replace(')', "\\)")));
    }
    content.push_str("ET");
    let objects = [
        "<< /Type /Catalog /Pages 2 0 R >>".to_string(),
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_string(),
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".to_string(),
        format!("<< /Length {} >>\nstream\n{content}\nendstream", content.len()),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_string(),
    ];
    let mut out = b"%PDF-1.4\n".to_vec();
    let mut offsets = Vec::new();
    for (i, body) in objects.iter().enumerate() {
        offsets.push(out.len());
        out.extend(format!("{} 0 obj\n{body}\nendobj\n", i + 1).as_bytes());
    }
    let xref = out.len();
    out.extend(format!("xref\n0 {}\n0000000000 65535 f \n", objects.len() + 1).as_bytes());
    for offset in offsets {
        out.extend(format!("{offset:010} 00000 n \n").as_bytes());
    }
    out.extend(format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n", objects.len() + 1).as_bytes());
    out
}

fn env<'a>(host: &'a LocalHost, home: &Path, exe: PathBuf) -> Env<'a> {
    Env {
        host,
        settings: Settings { extract_timeout_s: 2, ..Settings::default() },
        user_home: home.to_path_buf(),
        exe,
        lib_dir: lib_dir().unwrap_or_else(|| home.join("no-lib")),
        model_dir: home.join("no-model"),
        sandbox: false,
        worker_args: vec!["--test-embedder".into()],
        poll: Duration::from_millis(20),
    }
}

/// Every test shares the process environment (`CODEX_HOME`), so they take turns.
static TURN: Mutex<()> = Mutex::new(());

fn scratch_home() -> (tempfile::TempDir, PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let home = dir.path().canonicalize().unwrap();
    // SAFETY: the tests of this file take turns (TURN), and nothing else reads it concurrently.
    unsafe { std::env::set_var("CODEX_HOME", home.join(".mightling")) };
    (dir, home)
}

#[test]
fn a_collection_is_indexed_searched_read_and_kept_current() {
    let _turn = TURN.lock().unwrap_or_else(|p| p.into_inner());
    let (_dir, home) = scratch_home();
    let docs_root = home.join("Documents");
    std::fs::create_dir_all(docs_root.join("contracts")).unwrap();
    std::fs::write(docs_root.join("contracts/acme.md"), "# Acme MSA\n\n## Termination\n\nEither party may terminate with a notice period of ninety days.\n\n## Fees\n\nInvoices are due within thirty days.\n").unwrap();
    std::fs::write(docs_root.join("contracts/acme (1).md"), std::fs::read(docs_root.join("contracts/acme.md")).unwrap()).unwrap();
    std::fs::write(docs_root.join("coffee.md"), "# 咖啡\n\n咖啡是一种用烘焙过的咖啡豆制作的饮料。\n").unwrap();
    std::fs::write(docs_root.join("plain.txt"), (1..=60).map(|i| format!("line {i} about volcanoes\n")).collect::<String>()).unwrap();
    std::fs::write(docs_root.join("movie.pdf"), b"\x00\x00\x00\x18ftypmp42 not a pdf").unwrap();
    std::fs::write(docs_root.join("setup.iso"), b"never opened").unwrap();
    std::fs::write(docs_root.join("id_rsa.txt"), b"-----BEGIN KEY-----").unwrap();
    let with_pdf = lib_dir().is_some();
    if with_pdf {
        std::fs::write(docs_root.join("report.pdf"), pdf(&["Quarterly report", "The reactor output rose by eleven percent."])).unwrap();
        std::fs::write(docs_root.join("broken.pdf"), b"%PDF-1.4\n garbage with no objects").unwrap();
    }

    let mut docs = DocsToml::default();
    assert_eq!(docs.ensure_defaults(&home), vec!["documents".to_string()]);
    docs.save().unwrap();
    let host = LocalHost { dir: home.join("locks"), started: Mutex::new(Vec::new()) };
    let env = env(&host, &home, exe());
    let outcome = index::run(&env, &docs, None, false).unwrap();
    assert_eq!(outcome, vec![("documents".to_string(), Outcome::Ok)]);
    // Two scopes, each named for `server start` to stop, the extractor capped at the PDF cap.
    let started = host.started.lock().unwrap().clone();
    assert_eq!(started.len(), 2, "{started:?}");
    assert!(started.iter().all(|(unit, _)| unit.starts_with("mightling-index-docs-documents-")));
    assert_eq!(started[0].1, 1 << 30);

    let conn = ling_docs::store::open_ro(&ling_docs::config::db_path("documents")).unwrap().unwrap();
    let counts = ling_docs::store::counts(&conn).unwrap();
    assert_eq!(counts.duplicates, 1, "{counts:?}");
    assert_eq!(counts.skipped, 1, "the lying movie.pdf");
    assert_eq!(counts.indexed, if with_pdf { 4 } else { 3 }, "{counts:?}");
    assert_eq!(counts.failed, if with_pdf { 1 } else { 0 }, "{counts:?}");
    assert_eq!(counts.chunks, counts.embedded);
    let skipped: String = ling_docs::store::get_meta(&conn, "skipped_unopened").unwrap().unwrap();
    assert!(skipped.contains("\"type not read\":1") && skipped.contains("named like a secret"), "{skipped}");
    drop(conn);

    let collections: Vec<_> = docs.enabled().cloned().collect();
    let mut searcher = Searcher::default();
    searcher.refresh(&collections).unwrap();
    let embedder = HashEmbedder { dim: 64 };
    let answer = searcher.search("notice period termination", 3, &Filters::default(), Some(&embedder), 0.25).unwrap();
    let top = &answer.hits[0];
    assert_eq!(top.rel, "contracts/acme.md");
    assert!(top.text.contains("ninety days"));
    assert!(top.loc.starts_with("lines 1-"), "{}", top.loc);
    // Chinese, which unicode61 cannot segment, through the trigram table.
    let answer = searcher.search("烘焙过的咖啡豆", 3, &Filters::default(), Some(&embedder), 0.25).unwrap();
    assert_eq!(answer.hits[0].rel, "coffee.md");
    // Filters.
    let only_text = Filters { kind: Some("txt".into()), ..Filters::default() };
    let answer = searcher.search("volcanoes", 5, &only_text, Some(&embedder), 0.25).unwrap();
    assert!(answer.hits.iter().all(|h| h.rel == "plain.txt") && !answer.hits.is_empty());
    if with_pdf {
        let answer = searcher.search("reactor output eleven percent", 3, &Filters::default(), Some(&embedder), 0.25).unwrap();
        assert_eq!(answer.hits[0].rel, "report.pdf");
        assert_eq!(answer.hits[0].loc, "p.1");
        let read = ling_docs::read::read(&collections, &answer.hits[0].doc_id, Some("p.1"), 6000).unwrap();
        assert!(read.text.contains("eleven percent"), "{}", read.text);
    }
    let plain = searcher.search("volcanoes", 1, &only_text, Some(&embedder), 0.25).unwrap().hits[0].doc_id.clone();
    let read = ling_docs::read::read(&collections, &plain, Some("lines 41-45"), 6000).unwrap();
    assert_eq!(read.text.lines().next(), Some("line 41 about volcanoes"));
    assert_eq!(read.loc, "lines 41-45");
    assert_eq!(read.next.as_deref(), Some("lines 46-50"));

    // The prompt block names the collection with its count.
    let block = ling_docs::prompt::block(&collections, true);
    assert!(block.contains(&format!("documents ({} files)", if with_pdf { 4 } else { 3 })), "{block}");

    // A change, a deletion: the next scan follows.
    std::thread::sleep(Duration::from_millis(20));
    std::fs::write(docs_root.join("plain.txt"), "now about glaciers\n").unwrap();
    std::fs::remove_file(docs_root.join("coffee.md")).unwrap();
    index::run(&env, &docs, None, false).unwrap();
    let mut searcher = Searcher::default();
    searcher.refresh(&collections).unwrap();
    let answer = searcher.search("glaciers", 3, &Filters::default(), Some(&embedder), 0.25).unwrap();
    assert_eq!(answer.hits[0].rel, "plain.txt");
    assert!(searcher.search("烘焙过的咖啡豆", 3, &Filters::default(), Some(&embedder), 0.25).unwrap().hits.iter().all(|h| h.rel != "coffee.md"));

    // The MCP server answers both tools, wrapped as untrusted text.
    let mut server = Command::new(exe())
        .arg("mcp")
        .env("MIGHTLING_DOCS_TEST_EMBEDDER", "hash")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = server.stdin.take().unwrap();
    let mut stdout = BufReader::new(server.stdout.take().unwrap());
    let mut ask = |message: serde_json::Value| -> serde_json::Value {
        writeln!(stdin, "{message}").unwrap();
        let mut line = String::new();
        stdout.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap()
    };
    ask(serde_json::json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}));
    let list = ask(serde_json::json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}));
    assert_eq!(list["result"]["tools"].as_array().unwrap().len(), 2);
    let found = ask(serde_json::json!({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "docs_search", "arguments": {"query": "notice period", "k": 2}}}));
    let text = found["result"]["content"][0]["text"].as_str().unwrap().to_string();
    assert!(text.starts_with("<untrusted source=\"docs\" id=\"documents:"), "{text}");
    assert!(text.contains("ninety days"));
    let id = text.split("id=\"").nth(1).unwrap().split('"').next().unwrap().to_string();
    let read = ask(serde_json::json!({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "docs_read", "arguments": {"doc_id": id}}}));
    assert!(read["result"]["content"][0]["text"].as_str().unwrap().contains("Invoices are due"));
    drop(stdin);
    let _ = server.wait();
}

/// A stand-in extractor: hangs on files named `hang*`, dies as the kernel's OOM kill would on
/// `crash*`, and reads the rest with the real worker; every other command is the real binary.
fn fake_worker(dir: &Path) -> PathBuf {
    let script = dir.join("fake-ling-docs");
    std::fs::write(
        &script,
        format!(
            r#"#!/usr/bin/env python3
import json, os, sys, time
real = {real:?}
if sys.argv[1] != "extract-worker":
    os.execv(real, [real] + sys.argv[1:])
jobs, out = sys.argv[2], sys.argv[3]
for line in open(jobs):
    job = json.loads(line)
    with open(out, "a") as f:
        f.write(json.dumps({{"event": "start", "id": job["id"]}}) + "\n")
    name = os.path.basename(job["path"])
    if name.startswith("hang"):
        time.sleep(100)
    if name.startswith("crash"):
        os._exit(137)
    with open(out, "a") as f:
        f.write(json.dumps({{"event": "done", "id": job["id"], "result": {{"units": [{{"loc": "lines 1-1", "line_first": 1, "line_last": 1, "heading": "", "text": open(job["path"]).read()}}]}}}}) + "\n")
"#,
            real = exe()
        ),
    )
    .unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    script
}

#[test]
fn a_file_that_hangs_or_kills_the_extractor_fails_alone() {
    let _turn = TURN.lock().unwrap_or_else(|p| p.into_inner());
    let (_dir, home) = scratch_home();
    let root = home.join("notes");
    std::fs::create_dir_all(&root).unwrap();
    for (name, text) in [("a.txt", "alpha"), ("crash.txt", "boom"), ("hang.txt", "stuck"), ("z.txt", "omega")] {
        std::fs::write(root.join(name), text).unwrap();
    }
    let mut docs = DocsToml::default();
    docs.add(&root, None, &home).unwrap();
    docs.save().unwrap();
    let host = LocalHost { dir: home.join("locks"), started: Mutex::new(Vec::new()) };
    let env = env(&host, &home, fake_worker(&home));
    let started = std::time::Instant::now();
    assert_eq!(index::run(&env, &docs, Some("notes"), false).unwrap()[0].1, Outcome::Ok);
    assert!(started.elapsed() < Duration::from_secs(30));
    let conn = ling_docs::store::open_ro(&ling_docs::config::db_path("notes")).unwrap().unwrap();
    let status = |path: &str| conn.query_row("SELECT status FROM documents WHERE path = ?", [path], |r| r.get::<_, String>(0)).unwrap();
    assert_eq!(status("a.txt"), "ok");
    assert_eq!(status("z.txt"), "ok", "the run went on after both");
    assert!(status("crash.txt").starts_with("failed: killed"), "{}", status("crash.txt"));
    assert_eq!(status("hang.txt"), "failed: timed out after 2 s");
    // Jobs run shortest name first (a, z, hang, crash): one extractor until the hang, a new one
    // for the crash, then the embedder.
    assert_eq!(host.started.lock().unwrap().len(), 3);
    // A failed file is not retried until it changes.
    drop(conn);
    index::run(&env, &docs, Some("notes"), false).unwrap();
    assert_eq!(host.started.lock().unwrap().len(), 3);
}
