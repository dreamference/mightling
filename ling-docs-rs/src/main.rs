//! `ling-docs`: the local file index's command line (spec §8.1). `ling docs <command>` runs it.

use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};

use ling_docs::collections::DocsToml;
use ling_docs::config::{self, Settings};
use ling_docs::host::SystemdHost;
use ling_docs::requests::{self, Request};
use ling_docs::search::{Filters, Searcher};
use ling_docs::{index, mcp, prompt, read, search, store};

#[derive(Parser)]
#[command(name = "ling-docs", about = "Search the user's own documents, indexed on this machine", version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Index a folder as a collection.
    Add {
        folder: PathBuf,
        #[arg(long)]
        name: Option<String>,
    },
    /// Remove a collection and delete its index (not the files).
    Remove { name: String },
    /// List the collections.
    List {
        #[arg(long)]
        json: bool,
    },
    /// Collections, documents, chunks, failures, pages that need OCR, last run, disk used.
    Status {
        name: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Search every collection (or one) by meaning and keywords.
    Search {
        #[arg(required = true, num_args = 1..)]
        query: Vec<String>,
        #[arg(long)]
        collection: Option<String>,
        #[arg(long, default_value_t = search::DEFAULT_K)]
        k: usize,
        /// Only documents whose path in the collection matches this glob.
        #[arg(long)]
        path: Option<String>,
        /// Only documents of this type (pdf, markdown, text, rst, org, tex).
        #[arg(long = "type")]
        kind: Option<String>,
        /// Only documents modified on or after this date (YYYY-MM-DD).
        #[arg(long)]
        after: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Read a document a search found, by page or by lines.
    Read {
        doc_id: String,
        #[arg(long, conflicts_with = "lines")]
        page: Option<u32>,
        /// A range such as 120-180.
        #[arg(long)]
        lines: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Rebuild a collection's index from its files.
    Reindex { name: String },
    /// Index now, in the foreground (outside the agent's sandbox).
    Index {
        #[arg(long)]
        collection: Option<String>,
        #[arg(long)]
        rebuild: bool,
    },
    /// Keep the collections indexed while a session lives (the launcher starts this).
    #[command(hide = true)]
    Session {
        #[arg(long)]
        parent_pid: Option<i32>,
    },
    /// Serve docs_search and docs_read over MCP on stdio.
    Mcp,
    /// Print the prompt block for the model (empty without collections).
    #[command(hide = true)]
    PromptBlock {
        #[arg(long)]
        tools: bool,
    },
    #[command(hide = true)]
    ExtractWorker {
        jobs: PathBuf,
        out: PathBuf,
        #[arg(long)]
        lib_dir: PathBuf,
    },
    #[command(hide = true)]
    IndexWorker {
        #[arg(long)]
        collection: String,
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        events: PathBuf,
        #[arg(long, default_value_t = 4)]
        threads: usize,
        #[arg(long)]
        lib_dir: PathBuf,
        #[arg(long)]
        model_dir: PathBuf,
        #[arg(long)]
        test_embedder: bool,
    },
    /// Prints the embeddings of lines of text read from stdin, one JSON array per line (checks).
    #[command(hide = true)]
    Embed {
        /// Embed as queries (`query: ` prefix).
        #[arg(long)]
        query: bool,
    },
    /// Runs many searches in one process and records the ranked chunks and the latency of each
    /// (the evaluation of spec §12).
    #[command(hide = true)]
    BenchSearch {
        questions: PathBuf,
        out: PathBuf,
        #[arg(long, default_value_t = 10)]
        k: usize,
        #[arg(long)]
        no_merge: bool,
    },
}

fn main() -> ExitCode {
    match run(Cli::parse()) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("ling-docs: {error:#}");
            ExitCode::from(1)
        }
    }
}

/// Whether a session process keeps the index (it holds `docs/session.lock`).
fn session_running() -> bool {
    index::lock_held(&config::docs_dir().join("session.lock"))
}

/// Asks for indexing: through the session when one runs, else by starting a run in the
/// background.
fn request_index(request: Request) -> Result<String> {
    if session_running() {
        requests::append(&config::docs_dir(), &request)?;
        return Ok("indexing starts in the background".to_string());
    }
    let mut args = vec!["index".to_string()];
    match &request {
        Request::All => {}
        Request::One(name) => args.extend(["--collection".to_string(), name.clone()]),
        Request::Rebuild(name) => args.extend(["--collection".to_string(), name.clone(), "--rebuild".to_string()]),
    }
    store::create_private_dir(&config::docs_dir())?;
    let log = std::fs::File::create(config::docs_dir().join("index.log"))?;
    ling_docs::session::spawn_detached(&std::env::current_exe()?, &args, log)?;
    Ok("indexing runs in the background".to_string())
}

/// `docs.toml` with the default collections added when their folders exist (best effort: inside
/// the agent's sandbox the file cannot be written, and nothing is added there).
fn load_docs() -> Result<DocsToml> {
    let mut docs = DocsToml::load()?;
    let mut probe = docs.clone();
    if !probe.ensure_defaults(&config::user_home()).is_empty() && probe.save().is_ok() {
        docs = probe;
    }
    Ok(docs)
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * (m + if m > 2 { -3 } else { 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn parse_date(text: &str) -> Result<i64> {
    let parts: Vec<i64> = text.split('-').map(|p| p.parse::<i64>()).collect::<std::result::Result<_, _>>().with_context(|| format!("`{text}` is not a date (YYYY-MM-DD)"))?;
    let [y, m, d] = parts[..] else { bail!("`{text}` is not a date (YYYY-MM-DD)") };
    Ok(days_from_civil(y, m, d) * 86400)
}

fn human_bytes(n: u64) -> String {
    match n {
        n if n >= 1 << 30 => format!("{:.1} GB", n as f64 / (1u64 << 30) as f64),
        n if n >= 1 << 20 => format!("{:.1} MB", n as f64 / (1u64 << 20) as f64),
        n => format!("{} KB", n >> 10),
    }
}

fn run(cli: Cli) -> Result<ExitCode> {
    let mut out = std::io::stdout();
    match cli.command {
        Command::Add { folder, name } => {
            let mut docs = load_docs()?;
            let collection = docs.add(&folder, name.as_deref(), &config::user_home())?;
            docs.save()?;
            writeln!(out, "Added `{}`: {}", collection.name, collection.root.display())?;
            if let Some(missing) = config::missing_runtime() {
                writeln!(out, "Indexing waits: {missing}.")?;
            } else {
                writeln!(out, "{}; `ling docs status` shows progress.", request_index(Request::One(collection.name.clone()))?)?;
            }
        }
        Command::Remove { name } => {
            let mut docs = DocsToml::load()?;
            let removed = docs.remove(&name)?;
            docs.save()?;
            index::remove_database(&name)?;
            writeln!(out, "Removed `{name}` ({}); its index, which held the documents' text, is deleted. The files are untouched.", removed.root.display())?;
            if removed.default {
                writeln!(out, "It is a default collection: it will not be added back.")?;
            }
        }
        Command::List { json } => {
            let docs = load_docs()?;
            if json {
                writeln!(out, "{}", serde_json::to_string_pretty(&docs.collections)?)?;
            } else if docs.collections.is_empty() {
                writeln!(out, "No collections. Add one with `ling docs add <folder>`.")?;
            } else {
                for c in &docs.collections {
                    writeln!(out, "{:<16} {}{}", c.name, c.root.display(), if c.enabled { "" } else { " (disabled)" })?;
                }
            }
        }
        Command::Status { name, json } => {
            let docs = load_docs()?;
            let mut rows = Vec::new();
            for c in docs.collections.iter().filter(|c| name.as_ref().is_none_or(|n| *n == c.name)) {
                let path = config::db_path(&c.name);
                let conn = store::open_ro(&path)?;
                let counts = conn.as_ref().map(store::counts).transpose()?.unwrap_or_default();
                let meta = |key: &str| conn.as_ref().and_then(|c| store::get_meta(c, key).ok().flatten());
                let size = ["", "-journal"].iter().filter_map(|s| std::fs::metadata(format!("{}{s}", path.display())).ok()).map(|m| m.len()).sum::<u64>();
                rows.push(serde_json::json!({
                    "name": c.name, "root": c.root, "enabled": c.enabled, "default": c.default,
                    "counts": counts, "last_status": meta("last_status"), "last_run": meta("last_run").and_then(|v| v.parse::<i64>().ok()),
                    "skipped_unopened": meta("skipped_unopened").and_then(|v| serde_json::from_str::<serde_json::Value>(&v).ok()),
                    "extract_peak_mb": meta("extract_peak_mb"), "embed_peak_mb": meta("embed_peak_mb"),
                    "disk_bytes": size,
                }));
            }
            let missing = config::missing_runtime();
            if json {
                writeln!(out, "{}", serde_json::to_string_pretty(&serde_json::json!({ "collections": rows, "runtime_missing": missing, "network": "none: local only, unaffected by /airgapped" }))?)?;
                return Ok(ExitCode::SUCCESS);
            }
            if rows.is_empty() {
                writeln!(out, "No collections. Add one with `ling docs add <folder>`.")?;
            }
            for row in &rows {
                let n = |key: &str| row["counts"][key].as_i64().unwrap_or(0);
                writeln!(out, "{} — {}", row["name"].as_str().unwrap_or(""), row["root"].as_str().unwrap_or(""))?;
                writeln!(out, "  documents {} indexed, {} pending, {} failed, {} duplicates, {} skipped", n("indexed"), n("pending"), n("failed"), n("duplicates"), n("skipped"))?;
                writeln!(out, "  chunks {} ({} embedded); index {}", n("chunks"), n("embedded"), human_bytes(row["disk_bytes"].as_u64().unwrap_or(0)))?;
                if n("needs_ocr_pages") > 0 {
                    writeln!(out, "  needs OCR: {} pages in {} documents (unsearchable until OCR, Phase 2)", n("needs_ocr_pages"), n("needs_ocr_documents"))?;
                }
                if let Some(status) = row["last_status"].as_str() {
                    let ago = row["last_run"].as_i64().map(|t| format!(", {} min ago", (index::now() - t) / 60)).unwrap_or_default();
                    writeln!(out, "  last run: {status}{ago}")?;
                }
                if let Some(skipped) = row["skipped_unopened"].as_object().filter(|m| !m.is_empty()) {
                    let parts: Vec<String> = skipped.iter().map(|(k, v)| format!("{v} {k}")).collect();
                    writeln!(out, "  left out without opening: {}", parts.join(", "))?;
                }
            }
            if let Some(missing) = missing {
                writeln!(out, "Indexing waits: {missing}.")?;
            }
            writeln!(out, "Network: none. The index is local only, unaffected by /airgapped.")?;
        }
        Command::Search { query, collection, k, path, kind, after, json } => {
            let query = query.join(" ");
            let collections = search::collections()?;
            let filters = Filters { collection, path_glob: path, kind, modified_after: after.as_deref().map(parse_date).transpose()? };
            let mut searcher = Searcher::default();
            searcher.refresh(&collections)?;
            for c in &collections {
                if !config::db_path(&c.name).is_file() {
                    let _ = requests::append(&config::docs_dir(), &Request::One(c.name.clone()));
                }
            }
            let embedder = mcp::query_embedder().ok();
            let answer = searcher.search(&query, k.clamp(1, 50), &filters, embedder.as_deref(), Settings::load().bm25_weight)?;
            if json {
                writeln!(out, "{}", serde_json::to_string_pretty(&answer)?)?;
            } else {
                for note in &answer.notes {
                    writeln!(out, "Note: {note}.")?;
                }
                if answer.hits.is_empty() {
                    writeln!(out, "No passage matched.")?;
                }
                for (i, hit) in answer.hits.iter().enumerate() {
                    writeln!(out, "{}. {}  {}  {}  [{}]", i + 1, hit.doc_id, hit.path, hit.loc, format_args!("{:.4}", hit.score))?;
                    writeln!(out, "   {}", hit.snippet)?;
                }
            }
        }
        Command::Read { doc_id, page, lines, json } => {
            let collections = search::collections()?;
            let locator = page.map(|p| format!("p.{p}")).or(lines.map(|l| format!("lines {l}")));
            let result = read::read(&collections, &doc_id, locator.as_deref(), read::BUDGET_TOKENS)?;
            if json {
                writeln!(out, "{}", serde_json::to_string_pretty(&result)?)?;
            } else {
                writeln!(out, "{} {} ({})", result.doc_id, result.path, result.loc)?;
                writeln!(out, "{}", result.text.trim_end())?;
                if let Some(next) = result.next {
                    writeln!(out, "-- more: ling docs read {} {}", result.doc_id, if next.starts_with("p.") { format!("--page {}", &next[2..]) } else { format!("--lines {}", next.trim_start_matches("lines ")) })?;
                }
            }
        }
        Command::Reindex { name } => {
            let docs = DocsToml::load()?;
            if docs.get(&name).is_none() {
                bail!("no collection named `{name}`");
            }
            writeln!(out, "Rebuilding `{name}`: {}.", request_index(Request::Rebuild(name.clone()))?)?;
        }
        Command::Index { collection, rebuild } => {
            let docs = load_docs()?;
            let host = SystemdHost;
            let env = index::Env::real(&host)?;
            let mut failed = false;
            for (name, outcome) in index::run(&env, &docs, collection.as_deref(), rebuild)? {
                writeln!(out, "{name}: {}", outcome.status())?;
                failed |= matches!(outcome, index::Outcome::Failed(_));
            }
            if failed {
                return Ok(ExitCode::from(1));
            }
        }
        Command::Session { parent_pid } => ling_docs::session::run(parent_pid)?,
        Command::Mcp => mcp::serve()?,
        Command::PromptBlock { tools } => {
            let settings = Settings::load();
            if settings.enabled {
                let collections = search::collections().unwrap_or_default();
                let block = prompt::block(&collections, tools);
                if !block.is_empty() {
                    writeln!(out, "{block}")?;
                }
            }
        }
        Command::ExtractWorker { jobs, out: events, lib_dir } => ling_docs::extract::worker(&jobs, &events, &lib_dir)?,
        Command::IndexWorker { collection, db, events, threads, lib_dir, model_dir, test_embedder } => {
            let mut log = std::io::stderr();
            if test_embedder {
                let embedder = ling_docs::embed::HashEmbedder { dim: 64 };
                index::index_worker(&collection, &db, &events, &embedder, &ling_docs::chunk::Words, &mut log)?;
            } else {
                let embedder = ling_docs::embed::OnnxEmbedder::load(&model_dir, &lib_dir, threads)?;
                index::index_worker(&collection, &db, &events, &embedder, &embedder.tokenizer, &mut log)?;
            }
        }
        Command::BenchSearch { questions, out: path, k, no_merge } => bench_search(&questions, &path, k, no_merge)?,
        Command::Embed { query } => {
            use std::io::BufRead;
            let embedder = ling_docs::embed::OnnxEmbedder::load(&config::model_dir(), &config::lib_dir(), 4)?;
            for line in std::io::stdin().lock().lines() {
                // Each line is a JSON string, so texts may hold newlines.
                let text: String = serde_json::from_str(&line?)?;
                let text = if query { format!("{}{text}", ling_docs::embed::QUERY_PREFIX) } else { text };
                use ling_docs::embed::Embed;
                writeln!(out, "{}", serde_json::to_string(&embedder.embed(&text)?)?)?;
            }
        }
    }
    Ok(ExitCode::SUCCESS)
}

/// One JSON line per question: its id, the ranked chunks (document path and text) and the
/// search's wall time, with the model and the databases loaded once, as a query process would.
fn bench_search(questions: &PathBuf, path: &PathBuf, k: usize, no_merge: bool) -> Result<()> {
    use std::io::BufRead;
    let collections = search::collections()?;
    let mut searcher = Searcher::default();
    searcher.merge_adjacent = !no_merge;
    searcher.refresh(&collections)?;
    let embedder = mcp::query_embedder()?;
    let weight = Settings::load().bm25_weight;
    let mut sink = std::fs::File::create(path)?;
    // One warm-up query loads the vectors, as the first query of a session does.
    let _ = searcher.search("warm up", 1, &Filters::default(), Some(embedder.as_ref()), weight)?;
    for line in std::io::BufReader::new(std::fs::File::open(questions)?).lines() {
        let q: serde_json::Value = serde_json::from_str(&line?)?;
        let text = q["question"].as_str().unwrap_or("");
        let started = std::time::Instant::now();
        let answer = searcher.search(text, k, &Filters::default(), Some(embedder.as_ref()), weight)?;
        let ms = started.elapsed().as_secs_f64() * 1000.0;
        let hits: Vec<serde_json::Value> = answer.hits.iter().map(|h| serde_json::json!({ "rel": h.rel, "collection": h.collection, "loc": h.loc, "text": h.text })).collect();
        writeln!(sink, "{}", serde_json::json!({ "id": q["id"], "ms": ms, "hits": hits }))?;
    }
    Ok(())
}
