//! Class L1, the `lookup` tool: exact answers from `ling-code`'s index with no model
//! (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §4.2, §6.2).
//!
//! The operation is one of a closed list (`def`, `refs`, `callers`, `impl`, `outline`) and runs
//! `ling-code <op> --json` in the repository, which only reads its own stores and opens no socket.
//! Every hit's quote is read back from the file by this server, so a row the index got wrong (a
//! stale line, a path outside the scope) is dropped and counted instead of shown. Output is bounded
//! by a row limit, a quote size and a total size.

use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The closed list of operations.
pub const LOOKUP_OPS: &[&str] = &["def", "refs", "callers", "impl", "outline"];
/// Default number of rows asked of the index.
pub const DEFAULT_LIMIT: usize = 20;
/// Hard maximum number of rows.
pub const MAX_LIMIT: usize = 50;
/// Lines of a definition quoted at most.
pub const MAX_QUOTE_LINES: usize = 3;
/// Characters of one quoted line at most.
pub const MAX_QUOTE_LINE_CHARS: usize = 200;
/// Characters of the whole result's hits at most (about 1,500 tokens).
pub const MAX_OUTPUT_CHARS: usize = 6_000;
/// Seconds `ling-code` may run.
pub const INDEX_TIMEOUT_S: u64 = 20;
const MAX_NAME_CHARS: usize = 200;
const MAX_INDEX_BYTES: usize = 4 * 1024 * 1024;

/// A `lookup` call.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LookupRequest {
    /// One of [`LOOKUP_OPS`].
    pub op: String,
    /// The name to look up (`outline`: the file, relative to the repository).
    pub name: String,
    /// Keep only rows under this path prefix or glob (relative to the repository).
    #[serde(default)]
    pub scope: Option<String>,
    /// The repository folder (default: the current directory).
    #[serde(default)]
    pub repo: Option<String>,
    /// Rows to return (default 20, at most 50).
    #[serde(default)]
    pub limit: Option<usize>,
}

/// One verified hit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Hit {
    pub path: String,
    pub start: usize,
    pub end: usize,
    pub quote: String,
    /// What the index says this row is (kind and qualified name), a hint.
    pub detail: String,
}

/// The answer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LookupResult {
    pub op: String,
    pub query: String,
    pub scope: String,
    pub hits: Vec<Hit>,
    /// Rows the index returned that failed the read-back or fell outside the scope.
    pub dropped: usize,
    /// True when the row limit or the size bound cut the list short.
    pub truncated: bool,
    /// Index facts (exactness, freshness) and, for no hits, what the absence means.
    pub notes: Vec<String>,
}

/// What `lookup` needs from the machine: the index and the files, injectable for tests.
pub trait IndexBackend {
    /// Runs `ling-code <args>` in `repo` and returns its stdout.
    fn run_index(&self, repo: &Path, args: &[String]) -> Result<String, String>;
    /// Reads the lines of a file (bounded).
    fn read_lines(&self, path: &Path) -> Result<Vec<String>, String>;
}

/// The real backend: the `ling-code` on `PATH` and the file system.
pub struct SystemIndex;

impl IndexBackend for SystemIndex {
    fn run_index(&self, repo: &Path, args: &[String]) -> Result<String, String> {
        let mut child = Command::new("ling-code")
            .args(args)
            .current_dir(repo)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("cannot run ling-code: {e}"))?;
        let mut stdout = child.stdout.take().ok_or("ling-code has no stdout")?;
        let reader = std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = (&mut stdout)
                .take(MAX_INDEX_BYTES as u64)
                .read_to_end(&mut buf);
            buf
        });
        let deadline = Instant::now() + Duration::from_secs(INDEX_TIMEOUT_S);
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    let out = reader.join().unwrap_or_default();
                    if !status.success() {
                        return Err(format!("ling-code exited with {status}"));
                    }
                    return Ok(String::from_utf8_lossy(&out).into_owned());
                }
                Ok(None) if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("ling-code did not answer within {INDEX_TIMEOUT_S}s"));
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(20)),
                Err(e) => return Err(format!("ling-code: {e}")),
            }
        }
    }

    fn read_lines(&self, path: &Path) -> Result<Vec<String>, String> {
        let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
        let mut buf = Vec::new();
        file.take(16 * 1024 * 1024)
            .read_to_end(&mut buf)
            .map_err(|e| e.to_string())?;
        Ok(String::from_utf8_lossy(&buf).lines().map(str::to_string).collect())
    }
}

/// Checks that `rel` is a plain relative path or glob that cannot leave the repository.
fn check_relative(rel: &str, what: &str) -> Result<(), String> {
    if rel.is_empty() || rel.len() > 400 || rel.contains('\0') || rel.contains('\n') {
        return Err(format!("invalid {what}"));
    }
    if rel.starts_with('-') {
        return Err(format!("{what} may not start with `-`"));
    }
    let p = Path::new(rel);
    if p.is_absolute() || p.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(format!("{what} must be relative and inside the repository"));
    }
    Ok(())
}

/// Validates a request and returns the canonical repository folder.
pub fn validate(req: &LookupRequest) -> Result<PathBuf, String> {
    if !LOOKUP_OPS.contains(&req.op.as_str()) {
        return Err(format!(
            "unknown lookup op {:?}; the closed list is {}",
            req.op,
            LOOKUP_OPS.join(", ")
        ));
    }
    let name = req.name.trim();
    if name.is_empty() || name.len() > MAX_NAME_CHARS || name.contains('\0') || name.contains('\n')
    {
        return Err("`name` must be one line of 1 to 200 characters".to_string());
    }
    if name.starts_with('-') {
        return Err("`name` may not start with `-`".to_string());
    }
    if req.op == "outline" {
        check_relative(name, "outline file")?;
    }
    if let Some(scope) = &req.scope {
        check_relative(scope, "scope")?;
    }
    let repo = match &req.repo {
        Some(r) => PathBuf::from(r),
        None => std::env::current_dir().map_err(|e| e.to_string())?,
    };
    let repo = repo
        .canonicalize()
        .map_err(|e| format!("repository {} is not readable: {e}", repo.display()))?;
    if !repo.is_dir() {
        return Err(format!("{} is not a folder", repo.display()));
    }
    Ok(repo)
}

/// Parses the `(to N)` end line of an index row's detail.
fn end_line(detail: &str, start: usize) -> usize {
    detail
        .rsplit_once("(to ")
        .and_then(|(_, rest)| rest.trim_end_matches(')').trim().parse::<usize>().ok())
        .filter(|end| *end >= start)
        .unwrap_or(start)
}

fn quote_of(lines: &[String], start: usize, end: usize) -> Option<String> {
    if start == 0 || start > lines.len() {
        return None;
    }
    let last = end.min(start + MAX_QUOTE_LINES - 1).min(lines.len());
    let quote: Vec<String> = lines[start - 1..last]
        .iter()
        .map(|l| l.chars().take(MAX_QUOTE_LINE_CHARS).collect::<String>())
        .collect();
    Some(quote.join("\n"))
}

/// Runs one lookup: asks the index, drops every row whose file or line cannot be read back inside
/// the repository, and quotes what is left from the files themselves.
pub fn execute(req: &LookupRequest, backend: &dyn IndexBackend) -> Result<LookupResult, String> {
    let repo = validate(req)?;
    let limit = req.limit.unwrap_or(DEFAULT_LIMIT).clamp(1, MAX_LIMIT);
    let name = req.name.trim().to_string();
    let mut args: Vec<String> = vec![req.op.clone(), name.clone(), "--json".into()];
    args.push("--limit".into());
    args.push(limit.to_string());
    if let Some(scope) = &req.scope {
        args.push("--path".into());
        args.push(scope.clone());
    }
    let raw = backend.run_index(&repo, &args)?;
    let doc: Value =
        serde_json::from_str(&raw).map_err(|e| format!("ling-code printed no JSON: {e}"))?;
    let rows = doc.get("rows").and_then(Value::as_array).cloned().unwrap_or_default();

    let mut notes: Vec<String> = doc
        .get("notes")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
        .unwrap_or_default();
    notes.retain(|n| n != "re-index requested");
    notes.truncate(4);

    let mut hits = Vec::new();
    let mut dropped = 0;
    let mut truncated = rows.len() >= limit;
    let mut used = 0usize;
    for row in &rows {
        let (Some(path), Some(start)) = (
            row.get("path").and_then(Value::as_str),
            row.get("line").and_then(Value::as_u64),
        ) else {
            dropped += 1;
            continue;
        };
        let start = start as usize;
        let detail = row.get("detail").and_then(Value::as_str).unwrap_or("");
        let Ok(full) = repo.join(path).canonicalize() else {
            dropped += 1;
            continue;
        };
        if !full.starts_with(&repo) {
            dropped += 1;
            continue;
        }
        let Some(quote) = backend
            .read_lines(&full)
            .ok()
            .and_then(|lines| quote_of(&lines, start, end_line(detail, start)))
        else {
            dropped += 1;
            continue;
        };
        let hit = Hit {
            path: path.to_string(),
            start,
            end: end_line(detail, start),
            quote,
            detail: detail.chars().take(MAX_QUOTE_LINE_CHARS).collect(),
        };
        let size = hit.path.len() + hit.quote.len() + hit.detail.len() + 48;
        if used + size > MAX_OUTPUT_CHARS {
            truncated = true;
            break;
        }
        used += size;
        hits.push(hit);
    }
    if hits.is_empty() {
        notes.push(format!(
            "no row for `{name}` in the index (a fact about the index, not about the concept; try `rg` for a literal)"
        ));
    }
    Ok(LookupResult {
        op: req.op.clone(),
        query: name,
        scope: req.scope.clone().unwrap_or_else(|| ".".to_string()),
        hits,
        dropped,
        truncated,
        notes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    struct Fake {
        out: String,
        seen: RefCell<Vec<Vec<String>>>,
    }

    impl IndexBackend for Fake {
        fn run_index(&self, _repo: &Path, args: &[String]) -> Result<String, String> {
            self.seen.borrow_mut().push(args.to_vec());
            Ok(self.out.clone())
        }
        fn read_lines(&self, path: &Path) -> Result<Vec<String>, String> {
            SystemIndex.read_lines(path)
        }
    }

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-lookup-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("src")).unwrap();
        std::fs::write(
            dir.join("src/a.rs"),
            "// header\nfn alpha() {\n    1\n}\nfn beta() {}\n",
        )
        .unwrap();
        dir
    }

    fn req(repo: &Path, op: &str, name: &str) -> LookupRequest {
        LookupRequest {
            op: op.into(),
            name: name.into(),
            repo: Some(repo.display().to_string()),
            ..Default::default()
        }
    }

    #[test]
    fn quotes_are_read_back_from_the_file() {
        let dir = scratch("quote");
        let fake = Fake {
            out: r#"{"rows":[{"path":"src/a.rs","line":2,"detail":"function a.alpha (to 4)"},{"path":"src/a.rs","line":5,"detail":"function a.beta"}],"notes":["re-index requested","exact = x"]}"#.into(),
            seen: RefCell::new(vec![]),
        };
        let res = execute(&req(&dir, "def", "alpha"), &fake).unwrap();
        assert_eq!(res.hits.len(), 2);
        assert_eq!(res.hits[0].quote, "fn alpha() {\n    1\n}");
        assert_eq!((res.hits[0].start, res.hits[0].end), (2, 4));
        assert_eq!(res.hits[1].quote, "fn beta() {}");
        assert_eq!(res.notes, vec!["exact = x".to_string()]);
        assert_eq!(
            fake.seen.borrow()[0],
            vec!["def", "alpha", "--json", "--limit", "20"]
        );
    }

    #[test]
    fn rows_that_fail_the_read_back_or_leave_the_repository_are_dropped() {
        let dir = scratch("drop");
        let fake = Fake {
            out: r#"{"rows":[{"path":"src/a.rs","line":99,"detail":""},{"path":"src/missing.rs","line":1,"detail":""},{"path":"../../../etc/passwd","line":1,"detail":""},{"path":"src/a.rs","line":2,"detail":""}],"notes":[]}"#.into(),
            seen: RefCell::new(vec![]),
        };
        let res = execute(&req(&dir, "refs", "alpha"), &fake).unwrap();
        assert_eq!(res.hits.len(), 1);
        assert_eq!(res.dropped, 3);
    }

    #[test]
    fn zero_hits_state_a_fact_about_the_index_only() {
        let dir = scratch("zero");
        let fake = Fake {
            out: r#"{"rows":[],"notes":[]}"#.into(),
            seen: RefCell::new(vec![]),
        };
        let res = execute(&req(&dir, "callers", "nothing"), &fake).unwrap();
        assert!(res.hits.is_empty());
        assert!(res.notes[0].contains("a fact about the index"));
    }

    #[test]
    fn the_op_list_is_closed_and_arguments_cannot_become_flags_or_escape() {
        let dir = scratch("closed");
        let fake = Fake { out: "{}".into(), seen: RefCell::new(vec![]) };
        assert!(execute(&req(&dir, "index", "x"), &fake).is_err());
        assert!(execute(&req(&dir, "def", "--limit"), &fake).is_err());
        assert!(execute(&req(&dir, "def", ""), &fake).is_err());
        assert!(execute(&req(&dir, "outline", "/etc/passwd"), &fake).is_err());
        assert!(execute(&req(&dir, "outline", "../x.rs"), &fake).is_err());
        let mut r = req(&dir, "def", "x");
        r.scope = Some("../..".into());
        assert!(execute(&r, &fake).is_err());
        assert!(fake.seen.borrow().is_empty(), "nothing reached the index");
    }

    #[test]
    fn limits_and_size_are_bounded() {
        let dir = scratch("bound");
        let rows: Vec<String> = (0..50)
            .map(|_| r#"{"path":"src/a.rs","line":2,"detail":"function a.alpha"}"#.to_string())
            .collect();
        let fake = Fake {
            out: format!(r#"{{"rows":[{}],"notes":[]}}"#, rows.join(",")),
            seen: RefCell::new(vec![]),
        };
        let mut r = req(&dir, "def", "alpha");
        r.limit = Some(1000);
        let res = execute(&r, &fake).unwrap();
        assert_eq!(fake.seen.borrow()[0][4], MAX_LIMIT.to_string());
        let total: usize = res.hits.iter().map(|h| h.quote.len() + h.path.len()).sum();
        assert!(total <= MAX_OUTPUT_CHARS);
        assert!(res.truncated);
    }

    #[test]
    fn end_line_parses_the_detail() {
        assert_eq!(end_line("function x (to 146)", 130), 146);
        assert_eq!(end_line("function x", 130), 130);
        assert_eq!(end_line("function x (to 3)", 130), 130);
    }
}
