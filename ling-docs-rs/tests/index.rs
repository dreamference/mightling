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
    pdf_pages(&[lines], None)
}

/// A PDF of one page per entry of `pages` (an empty entry is a page with no text layer, as a
/// scan reads), with an optional document-information title.
fn pdf_pages(pages: &[&[&str]], title: Option<&str>) -> Vec<u8> {
    let n = pages.len();
    // 1 catalog, 2 pages, 3 font, 4 info, then a page and its content per page.
    let mut objects = vec![
        "<< /Type /Catalog /Pages 2 0 R >>".to_string(),
        format!("<< /Type /Pages /Kids [{}] /Count {n} >>", (0..n).map(|i| format!("{} 0 R", 5 + 2 * i)).collect::<Vec<_>>().join(" ")),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_string(),
        format!("<< /Title ({}) >>", title.unwrap_or("")),
    ];
    for (i, lines) in pages.iter().enumerate() {
        let mut content = String::from("BT /F1 12 Tf 72 720 Td 14 TL\n");
        for line in lines.iter() {
            content.push_str(&format!("({}) Tj T*\n", line.replace('(', "\\(").replace(')', "\\)")));
        }
        content.push_str("ET");
        objects.push(format!("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {} 0 R /Resources << /Font << /F1 3 0 R >> >> >>", 6 + 2 * i));
        objects.push(format!("<< /Length {} >>\nstream\n{content}\nendstream", content.len()));
    }
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
    let info = if title.is_some() { " /Info 4 0 R" } else { "" };
    out.extend(format!("trailer\n<< /Size {} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n", objects.len() + 1).as_bytes());
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
    // docs_search and docs_read, and pdf_search where PDFium is installed.
    assert_eq!(list["result"]["tools"].as_array().unwrap().len(), if with_pdf { 3 } else { 2 });
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

/// Base64, wrapped at 76 characters as mail has it.
fn base64(bytes: &[u8]) -> String {
    const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let n = (chunk[0] as u32) << 16 | (*chunk.get(1).unwrap_or(&0) as u32) << 8 | *chunk.get(2).unwrap_or(&0) as u32;
        for i in 0..4 {
            out.push(if i <= chunk.len() { A[(n >> (18 - 6 * i) & 63) as usize] as char } else { '=' });
        }
    }
    out.as_bytes().chunks(76).map(|l| String::from_utf8_lossy(l).into_owned()).collect::<Vec<_>>().join("\n")
}

/// A ZIP archive of the given entries (a DOCX or ODT).
fn zip_of(entries: &[(&str, &str)]) -> Vec<u8> {
    let mut out = std::io::Cursor::new(Vec::new());
    {
        let mut writer = zip::ZipWriter::new(&mut out);
        let options = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        for (name, body) in entries {
            writer.start_file(*name, options).unwrap();
            writer.write_all(body.as_bytes()).unwrap();
        }
        writer.finish().unwrap();
    }
    out.into_inner()
}

#[test]
fn office_files_pages_mail_and_tables_are_indexed_searched_and_read() {
    let _turn = TURN.lock().unwrap_or_else(|p| p.into_inner());
    let (_dir, home) = scratch_home();
    let root = home.join("Documents");
    std::fs::create_dir_all(&root).unwrap();
    let w = r#"xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main""#;
    let document = format!(
        r#"<w:document {w}><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Retry policy</w:t></w:r></w:p>
        <w:p><w:r><w:t>Clients retry a failed upload three times with exponential backoff.</w:t></w:r></w:p></w:body></w:document>"#
    );
    let styles = format!(r#"<w:styles {w}><w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>"#);
    std::fs::write(root.join("design.docx"), zip_of(&[("word/document.xml", &document), ("word/styles.xml", &styles)])).unwrap();
    let content = r#"<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text><text:h text:outline-level="1">Minutes</text:h><text:p>The board approved the Gothenburg lease.</text:p></office:text></office:body></office:document-content>"#;
    std::fs::write(root.join("minutes.odt"), zip_of(&[("mimetype", "application/vnd.oasis.opendocument.text"), ("content.xml", content)])).unwrap();
    std::fs::write(root.join("page.html"), "<html><head><title>T</title></head><body><nav>Home</nav><h1>Volcanoes</h1><p>Basaltic lava flows quietly.</p><h2>Eruptions</h2><p>Plinian eruptions eject pumice high into the stratosphere.</p></body></html>").unwrap();
    let mbox = "From a@x Tue Mar  4 09:30:00 2025\nFrom: Maria <maria@kestrel.example>\nSubject: Shipment held\n\nThe gripper parts are stuck in Rotterdam customs.\n\nFrom b@x Wed Mar  5 09:30:00 2025\nFrom: Ops <ops@kestrel.example>\nSubject: Price list\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=b\n\n--b\nContent-Type: text/plain\n\nNew prices attached.\n--b\nContent-Type: text/csv\nContent-Disposition: attachment; filename=prices.csv\n\nModel,Price\nRX-40,1490 EUR\n--b--\n";
    let with_pdf = lib_dir().is_some();
    let mut mbox = mbox.to_string();
    if with_pdf {
        // A PDF attachment goes through PDFium like a PDF file.
        let pdf = base64(&pdf(&["Credit note", "Acme credits 4200 EUR for the bearing claim."]));
        mbox.push_str(&format!("\nFrom c@x Thu Mar  6 09:30:00 2025\nFrom: Acme <billing@acme.example>\nSubject: Credit note\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=c\n\n--c\nContent-Type: text/plain\n\nSee attached.\n--c\nContent-Type: application/pdf\nContent-Disposition: attachment; filename=credit.pdf\nContent-Transfer-Encoding: base64\n\n{pdf}\n--c--\n"));
    }
    std::fs::write(root.join("inbox.mbox"), mbox).unwrap();
    let mut csv = String::from("Sensor,Calibrated by,Date\n");
    for i in 1..=30 {
        csv.push_str(&format!("TS-{i:04},{},2025-01-{:02}\n", if i == 27 { "Ingrid Berg" } else { "Lab" }, i % 28 + 1));
    }
    std::fs::write(root.join("calibration.csv"), csv).unwrap();
    std::fs::write(root.join("fake.docx"), "not a zip").unwrap();

    let mut docs = DocsToml::default();
    docs.ensure_defaults(&home);
    docs.save().unwrap();
    let host = LocalHost { dir: home.join("locks"), started: Mutex::new(Vec::new()) };
    let env = env(&host, &home, exe());
    assert_eq!(index::run(&env, &docs, None, false).unwrap(), vec![("documents".to_string(), Outcome::Ok)]);
    let conn = ling_docs::store::open_ro(&ling_docs::config::db_path("documents")).unwrap().unwrap();
    let counts = ling_docs::store::counts(&conn).unwrap();
    assert_eq!((counts.indexed, counts.skipped, counts.failed), (5, 1, 0), "{counts:?}");
    drop(conn);

    let collections: Vec<_> = docs.enabled().cloned().collect();
    let mut searcher = Searcher::default();
    searcher.refresh(&collections).unwrap();
    let embedder = HashEmbedder { dim: 64 };
    let mut top = |query: &str| searcher.search(query, 3, &Filters::default(), Some(&embedder), 0.25).unwrap().hits.remove(0);
    let hit = top("exponential backoff upload");
    assert_eq!((hit.rel.as_str(), hit.loc.as_str(), hit.heading.as_str()), ("design.docx", "para 1-2", "Retry policy"));
    // Its citation link opens the file, and only inside the collection.
    let link = ling_docs::locate::link(&hit.doc_id, None).unwrap();
    let located = ling_docs::locate::locate(&collections, &link).unwrap();
    assert_eq!(located.path, root.join("design.docx"));
    std::fs::rename(root.join("design.docx"), home.join("moved.docx")).unwrap();
    std::os::unix::fs::symlink(home.join("moved.docx"), root.join("design.docx")).unwrap();
    assert!(ling_docs::locate::locate(&collections, &link).unwrap_err().to_string().contains("leads out"));
    let hit = top("Gothenburg lease");
    assert_eq!((hit.rel.as_str(), hit.loc.as_str()), ("minutes.odt", "para 1-2"));
    let hit = top("Plinian pumice stratosphere");
    assert_eq!((hit.rel.as_str(), hit.heading.as_str()), ("page.html", "Volcanoes > Eruptions"));
    assert!(!hit.text.contains("Home"));
    let hit = top("gripper parts customs");
    assert_eq!((hit.rel.as_str(), hit.loc.as_str(), hit.heading.as_str()), ("inbox.mbox", "msg 1", "Shipment held"));
    let hit = top("RX-40 price");
    assert_eq!((hit.rel.as_str(), hit.loc.as_str()), ("inbox.mbox", "msg 2"), "the attachment is chunked with its message");
    assert!(hit.text.contains("Model: RX-40, Price: 1490 EUR"), "{}", hit.text);
    let mail = hit.doc_id.clone();
    if with_pdf {
        let hit = top("bearing claim credit 4200");
        assert_eq!((hit.rel.as_str(), hit.loc.as_str()), ("inbox.mbox", "msg 3"));
        let read = ling_docs::read::read(&collections, &mail, Some("msg 3 · credit.pdf p.1"), 6000).unwrap();
        assert!(read.text.contains("Acme credits 4200 EUR") && !read.text.contains("See attached"), "{}", read.text);
    }
    let hit = top("Ingrid Berg calibrated");
    assert_eq!((hit.rel.as_str(), hit.loc.as_str(), hit.heading.as_str()), ("calibration.csv", "rows 1-31", "Sensor, Calibrated by, Date"), "small row groups are packed together");
    let table = hit.doc_id.clone();

    // Read back at the locators the search gave, in each kind's own words.
    let read = ling_docs::read::read(&collections, &mail, Some("msg 2"), 6000).unwrap();
    assert!(read.text.starts_with("From: Ops <ops@kestrel.example>") && read.text.contains("Model: RX-40"), "{}", read.text);
    assert!(!read.text.contains("gripper"));
    let read = ling_docs::read::read(&collections, &mail, Some("msg 2 · prices.csv"), 6000).unwrap();
    assert_eq!(read.text.trim_end(), "Model, Price\nModel: RX-40, Price: 1490 EUR");
    let read = ling_docs::read::read(&collections, &table, Some("rows 28-28"), 6000).unwrap();
    assert_eq!(read.text.trim_end(), "Sensor: TS-0027, Calibrated by: Ingrid Berg, Date: 2025-01-28");
    assert_eq!((read.loc.as_str(), read.next.as_deref()), ("rows 28-28", Some("rows 29-29")));
    assert!(ling_docs::read::read(&collections, &table, Some("p.2"), 6000).unwrap_err().to_string().contains("by rows"));
    // The type filter knows the new kinds.
    let only = Filters { kind: Some("eml".into()), ..Filters::default() };
    assert!(searcher.search("gripper", 3, &only, Some(&embedder), 0.25).unwrap().hits.is_empty());
    let only = Filters { kind: Some("mbox".into()), ..Filters::default() };
    assert_eq!(searcher.search("gripper", 3, &only, Some(&embedder), 0.25).unwrap().hits[0].rel, "inbox.mbox");
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

/// A stand-in `ling-fetch --download <file> --max-bytes N <url>`: copies `<dir>/<last segment of
/// the URL>` into the file, or fails as the real one does.
fn fake_fetch(dir: &Path) -> PathBuf {
    let script = dir.join("fake-ling-fetch");
    std::fs::write(
        &script,
        format!(
            r#"#!/usr/bin/env python3
import json, os, shutil, sys
args = sys.argv[1:]
dest, url = args[args.index("--download") + 1], args[-1]
name = url.rstrip("/").rsplit("/", 1)[-1]
src = os.path.join({dir:?}, name)
if not os.path.exists(src):
    print("❌ request failed: HTTP 404 Not Found for url: " + url)
    sys.exit(1)
shutil.copy(src, dest)
print(json.dumps({{"final_url": url, "status": 200, "content_type": "application/pdf", "bytes": os.path.getsize(dest)}}))
"#,
            dir = dir.join("web")
        ),
    )
    .unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    std::fs::create_dir_all(dir.join("web")).unwrap();
    script
}

/// `ling-docs mcp` in a scratch home, its runs as plain children, with `extra` in its environment.
fn pdf_server(home: &Path, cwd: &Path, extra: &[(&str, &str)]) -> (Child, impl FnMut(serde_json::Value) -> serde_json::Value) {
    let mut command = Command::new(exe());
    command
        .arg("mcp")
        .current_dir(cwd)
        .env("HOME", home)
        .env("CODEX_HOME", home.join(".mightling"))
        .env("DREAMFERENCE_CONFIG_PATH", home.join("no-config.toml"))
        .env("XDG_RUNTIME_DIR", home.join("run"))
        .env("MIGHTLING_DOCS_TEST_EMBEDDER", "hash")
        .env("MIGHTLING_DOCS_TEST_HOST", "direct")
        .env("MIGHTLING_FETCH_BIN", fake_fetch(home))
        .env("TMPDIR", home.join("tmp"))
        .env_remove("DREAMFERENCE_MIGHTLING_AIRGAPPED")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped());
    std::fs::create_dir_all(home.join("run")).unwrap();
    std::fs::create_dir_all(home.join("tmp")).unwrap();
    for (key, value) in extra {
        command.env(key, value);
    }
    let mut server = command.spawn().unwrap();
    let mut stdin = server.stdin.take().unwrap();
    let mut stdout = BufReader::new(server.stdout.take().unwrap());
    let mut next = 0;
    let ask = move |message: serde_json::Value| -> serde_json::Value {
        next += 1;
        let mut message = message;
        message["jsonrpc"] = "2.0".into();
        message["id"] = next.into();
        writeln!(stdin, "{message}").unwrap();
        let mut line = String::new();
        stdout.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap()
    };
    (server, ask)
}

fn call(name: &str, arguments: serde_json::Value) -> serde_json::Value {
    serde_json::json!({ "method": "tools/call", "params": { "name": name, "arguments": arguments } })
}

fn text(answer: &serde_json::Value) -> String {
    answer["result"]["content"][0]["text"].as_str().unwrap_or_default().to_string()
}

#[test]
fn pdf_search_finds_the_page_reads_it_back_and_keeps_to_the_session() {
    let Some(_) = lib_dir() else {
        eprintln!("skipped: MIGHTLING_DOCS_LIB_DIR does not name PDFium");
        return;
    };
    let _turn = TURN.lock().unwrap_or_else(|p| p.into_inner());
    let (_dir, home) = scratch_home();
    let work = home.join("work");
    std::fs::create_dir_all(work.join("papers")).unwrap();
    // Pages of about 400 words, as a paper's are, so each page is a chunk of its own.
    let filler: Vec<String> = (0..32).map(|i| format!("Line {i} of measured field observations from the eruption campaign data")).collect();
    let page = |lead: &[&str]| -> Vec<String> { lead.iter().map(|l| l.to_string()).chain(filler.iter().cloned()).collect() };
    let pages = [
        page(&["Scaling laws for volcanic ash dispersal", "We study plume height and wind shear."]),
        page(&["Training details", "All models used a learning rate of 0.0003 with cosine decay."]),
        Vec::new(),
        page(&["Results", "Ash travelled furthest under strong shear."]),
    ];
    let refs: Vec<Vec<&str>> = pages.iter().map(|p| p.iter().map(String::as_str).collect()).collect();
    let slices: Vec<&[&str]> = refs.iter().map(|p| p.as_slice()).collect();
    let paper = pdf_pages(&slices, Some("Volcanic Ash Transport"));
    std::fs::write(work.join("papers/ash.pdf"), &paper).unwrap();
    std::fs::write(work.join("notes.pdf"), b"<html>not a pdf</html>").unwrap();
    std::fs::create_dir_all(home.join("elsewhere")).unwrap();
    std::fs::write(home.join("elsewhere/ash.pdf"), &paper).unwrap();

    let (mut server, mut ask) = pdf_server(&home, &work, &[("DREAMFERENCE_MIGHTLING_DOCS_PDF_WAIT_S", "0")]);
    let init = ask(serde_json::json!({ "method": "initialize", "params": {} }));
    assert!(init["result"]["capabilities"]["experimental"]["codex/sandbox-state-meta"].is_object(), "{init}");
    let list = ask(serde_json::json!({ "method": "tools/list" }));
    let names: Vec<&str> = list["result"]["tools"].as_array().unwrap().iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["pdf_search", "docs_read"], "no collection: no docs_search");

    // The page that holds the answer comes first, cited by source and page.
    let found = ask(call("pdf_search", serde_json::json!({ "source": "papers/ash.pdf", "query": "learning rate" })));
    let answer = text(&found);
    assert!(found["result"]["isError"].is_null(), "{answer}");
    let path = work.join("papers/ash.pdf");
    assert!(answer.starts_with(&format!("PDF: {} — \"Volcanic Ash Transport\", 4 pages.", path.display())), "{answer}");
    assert!(answer.contains("No text layer (scanned?) on p.3"), "{answer}");
    let first = answer.split("<untrusted").nth(1).unwrap();
    assert!(first.contains("locator: p.2\n") && first.contains("0.0003"), "{answer}");
    let id = first.split("id=\"").nth(1).unwrap().split('"').next().unwrap().to_string();
    assert!(id.starts_with("pdf-") && id.ends_with(":1"), "{id}");
    assert!(answer.contains(&format!("docs_read(doc_id=\"{id}\", locator=\"p.2\")")), "{answer}");

    // docs_read serves the cached pages, a page at a time.
    let read = text(&ask(call("docs_read", serde_json::json!({ "doc_id": id, "locator": "p.4" }))));
    assert!(read.contains("Ash travelled furthest") && read.contains(&format!("path: {}", path.display())), "{read}");

    // The same bytes elsewhere in the workspace (another call's working directory from Codex's
    // `_meta`) are the same entry: no second extraction.
    let other = home.join("work2");
    std::fs::create_dir_all(&other).unwrap();
    std::fs::write(other.join("copy.pdf"), &paper).unwrap();
    let meta = serde_json::json!({ "codex/sandbox-state-meta": { "sandbox_cwd": format!("file://{}", other.display()) } });
    let again = ask(serde_json::json!({ "method": "tools/call", "params": { "name": "pdf_search", "arguments": { "source": "copy.pdf", "query": "wind shear" }, "_meta": meta } }));
    assert!(text(&again).contains(&id), "{}", text(&again));
    let entries = ling_docs::pdf_search::entries();
    assert_eq!(entries.len(), 1, "{entries:?}");

    // Refusals, each in one line.
    let refuse = |answer: serde_json::Value| -> String {
        assert_eq!(answer["result"]["isError"], true, "{answer}");
        text(&answer)
    };
    assert!(refuse(ask(call("pdf_search", serde_json::json!({ "source": "../elsewhere/ash.pdf", "query": "x" })))).contains("outside this session's workspace"));
    assert!(refuse(ask(call("pdf_search", serde_json::json!({ "source": "notes.pdf", "query": "x" })))).contains("not a PDF"));
    assert!(refuse(ask(call("pdf_search", serde_json::json!({ "source": "drive:1AbC", "query": "x" })))).contains("Drive PDFs cannot be searched yet"));
    assert!(refuse(ask(call("pdf_search", serde_json::json!({ "source": "papers/ash.pdf" })))).contains("needs a query"));

    // A URL goes through ling-fetch; at /airgapped on (a seal written by the session, this test)
    // a new one is refused before anything is started, and a local PDF is still searched.
    std::fs::write(home.join("web/remote.pdf"), pdf_pages(&[&["Remote appendix", "The contract ends after ninety days of notice."]], None)).unwrap();
    let remote = text(&ask(call("pdf_search", serde_json::json!({ "source": "https://example.org/files/remote.pdf", "query": "notice period" }))));
    assert!(remote.starts_with("PDF: https://example.org/files/remote.pdf — \"remote\", 1 pages.") && remote.contains("ninety days"), "{remote}");
    assert!(refuse(ask(call("pdf_search", serde_json::json!({ "source": "https://example.org/files/missing.pdf", "query": "x" })))).contains("HTTP 404"));
    std::fs::create_dir_all(home.join("run/ling-airgapped")).unwrap();
    std::fs::write(home.join("run/ling-airgapped/thread-1"), std::process::id().to_string()).unwrap();
    std::fs::write(home.join("web/other.pdf"), pdf_pages(&[&["Other"]], None)).unwrap();
    assert!(refuse(ask(call("pdf_search", serde_json::json!({ "source": "https://example.org/other.pdf", "query": "x" })))).contains("/airgapped on"));
    assert!(text(&ask(call("pdf_search", serde_json::json!({ "source": "papers/ash.pdf", "query": "plume height" })))).contains("p.1"));
    drop(ask);
    let _ = server.wait();

    // Every chunk ended up embedded, in the background where the call did not wait.
    let conn = ling_docs::store::open_ro(&ling_docs::config::db_path(id.split(':').next().unwrap())).unwrap().unwrap();
    let counts = ling_docs::store::counts(&conn).unwrap();
    assert_eq!((counts.indexed, counts.needs_ocr_pages), (1, 1), "{counts:?}");
    assert_eq!(counts.chunks, counts.embedded, "{counts:?}");
    // Nothing left behind but the indexes.
    let left: Vec<String> = std::fs::read_dir(ling_docs::config::pdf_cache_dir()).unwrap().flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| !n.ends_with(".db") && !n.ends_with(".lock")).collect();
    assert!(left.is_empty(), "{left:?}");
}

#[test]
fn a_pdf_over_the_page_cap_is_indexed_from_the_start_and_says_so() {
    let Some(_) = lib_dir() else { return };
    let _turn = TURN.lock().unwrap_or_else(|p| p.into_inner());
    let (_dir, home) = scratch_home();
    let pages: Vec<Vec<String>> = (1..=5).map(|i| vec![format!("Chapter {i}"), format!("marker{i} appears on this page only")]).collect();
    let refs: Vec<Vec<&str>> = pages.iter().map(|p| p.iter().map(String::as_str).collect()).collect();
    let slices: Vec<&[&str]> = refs.iter().map(|p| p.as_slice()).collect();
    std::fs::write(home.join("long.pdf"), pdf_pages(&slices, None)).unwrap();
    let (mut server, mut ask) = pdf_server(&home, &home, &[("DREAMFERENCE_MIGHTLING_DOCS_PDF_MAX_PAGES", "2")]);
    let answer = text(&ask(call("pdf_search", serde_json::json!({ "source": "long.pdf", "query": "marker2" }))));
    assert!(answer.contains("5 pages (pages 1-2 indexed"), "{answer}");
    assert!(answer.contains("marker2"), "{answer}");
    let late = text(&ask(call("pdf_search", serde_json::json!({ "source": "long.pdf", "query": "marker5" }))));
    assert!(!late.contains("marker5 appears"), "{late}");
    drop(ask);
    let _ = server.wait();
}

#[test]
fn the_cache_is_evicted_by_age_and_size_and_spares_what_is_in_use() {
    let _turn = TURN.lock().unwrap_or_else(|p| p.into_inner());
    let (_dir, _home) = scratch_home();
    let dir = ling_docs::config::pdf_cache_dir();
    std::fs::create_dir_all(&dir).unwrap();
    let now = std::time::SystemTime::now();
    let entry = |hex: &str, mb: usize, hours_ago: u64| {
        let path = dir.join(format!("pdf-{hex}.db"));
        std::fs::write(&path, vec![0u8; mb << 20]).unwrap();
        std::fs::File::options().append(true).open(&path).unwrap().set_modified(now - Duration::from_secs(hours_ago * 3600)).unwrap();
        format!("pdf-{hex}")
    };
    let old = entry("000000000001", 1, 30);
    let big_old = entry("000000000002", 3, 5);
    let in_use = entry("000000000003", 3, 6);
    let fresh = entry("000000000004", 2, 1);
    std::fs::create_dir_all(dir.join("run-pdf-000000000009-999999999")).unwrap();
    // Another session is embedding this one: its lock is held, so it stays, however old.
    let busy = entry("000000000005", 1, 48);
    let held = std::fs::File::create(dir.join(format!("{busy}.lock"))).unwrap();
    ling_docs::host::flock(&held, false).unwrap();
    // One day unused goes; then the oldest until at most 6 MiB, sparing the ones in use.
    let removed = ling_docs::pdf_search::evict(6, 1, &[in_use.clone()]);
    let left: Vec<String> = ling_docs::pdf_search::entries().into_iter().map(|(n, ..)| n).collect();
    assert_eq!(removed, 2, "{left:?}");
    assert!(!left.contains(&old) && !left.contains(&big_old), "{left:?}");
    assert!(left.contains(&in_use) && left.contains(&fresh) && left.contains(&busy), "{left:?}");
    drop(held);
    assert!(!dir.join("run-pdf-000000000009-999999999").exists(), "a dead run's folder is removed");
}

#[test]
fn extract_prints_pages_and_names_the_scans_without_a_scope() {
    let Some(_) = lib_dir() else { return };
    let _turn = TURN.lock().unwrap_or_else(|p| p.into_inner());
    let (_dir, home) = scratch_home();
    std::fs::write(home.join("doc.pdf"), pdf_pages(&[&["First page"], &["Second page text"], &[]], Some("Three Pages"))).unwrap();
    // No user bus here: the worker runs as a plain child under RLIMIT_AS, as inside the sandbox.
    let output = Command::new(exe())
        .args(["extract", "doc.pdf", "--pages", "2-3"])
        .current_dir(&home)
        .env("HOME", &home)
        .env("XDG_RUNTIME_DIR", home.join("no-run"))
        .env("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent")
        .env("DREAMFERENCE_CONFIG_PATH", home.join("no-config.toml"))
        .output()
        .unwrap();
    let out = String::from_utf8_lossy(&output.stdout);
    assert!(output.status.success(), "{out} {}", String::from_utf8_lossy(&output.stderr));
    assert!(out.starts_with("doc.pdf — \"Three Pages\", 3 pages; p.2 returned"), "{out}");
    assert!(out.contains("--- page 2 of 3 ---\nSecond page text"), "{out}");
    assert!(!out.contains("First page"), "{out}");
    assert!(out.contains("p.3: no text layer (scanned?)"), "{out}");
    let broken = home.join("broken.pdf");
    std::fs::write(&broken, b"%PDF-1.4\n garbage").unwrap();
    let output = Command::new(exe()).args(["extract", "broken.pdf"]).current_dir(&home).env("HOME", &home).env("XDG_RUNTIME_DIR", home.join("no-run")).env("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent").output().unwrap();
    assert!(!output.status.success());
    assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "failed: not a PDF, or damaged");
}
