//! Night Shift's queue and its `/night` command (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md).
//!
//! Tasks are queued here during the day; `mling-admin night run` (Python, beside host safety and
//! the model server's health checks) works through them overnight. The two share only the files
//! under `$CODEX_HOME/night/`:
//!
//! ```text
//! tasks/<id>.json   one task, replaced whole (write + rename) under tasks/<id>.lock
//! reports/<date>.md the morning report, one `## <repository>` section per repository
//! seen.json         when each repository's results were last announced at startup
//! ```
//!
//! Nothing here calls the model or starts a run, so `/night` answers at once, mid-turn included.
//! There is no network code: the model id recorded with a task comes from the catalog the launcher
//! already wrote.

use std::collections::BTreeMap;
use std::fs::File;
use std::fs::OpenOptions;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;

use serde_json::Value;
use serde_json::json;

const USAGE: &str = "Usage: /night [list] | /night add [--test \"<cmd>\"] [--on <node>] <task> | /night show <id> | /night drop <id> | /night report";

/// Statuses after which a task is finished for the night: the startup line counts them.
const FINISHED: &[&str] = &["done", "no-change", "stalled", "failed", "interrupted"];

/// The default window, as `mling-admin night enable` installs it.
const DEFAULT_WINDOW: &str = "01:00-07:00";

/// What a `/night` line or `mling night` command line asks for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Request {
    List,
    /// `on`: a paired node whose own runner works the task (specs/DREAMFERENCE_MIGHTLING_NODE.md §13.3).
    Add { test: Option<String>, on: Option<String>, task: String },
    Show(String),
    Drop(String),
    Report,
    Usage(String),
}

impl Request {
    /// Parses what follows `/night` in the TUI. The task is everything after the options,
    /// verbatim, so its punctuation and line breaks survive.
    pub fn from_line(line: &str) -> Request {
        let line = line.trim();
        let (verb, rest) = match line.split_once(char::is_whitespace) {
            Some((verb, rest)) => (verb, rest.trim_start()),
            None => (line, ""),
        };
        match verb {
            "" | "list" if rest.is_empty() => Request::List,
            "report" if rest.is_empty() => Request::Report,
            "show" | "drop" => match rest.split_whitespace().collect::<Vec<_>>().as_slice() {
                [id] if verb == "show" => Request::Show(id.to_string()),
                [id] => Request::Drop(id.to_string()),
                _ => Request::Usage(USAGE.to_string()),
            },
            "add" => match take_add_options(rest) {
                Ok((test, on, task)) if !task.trim().is_empty() => Request::Add {
                    test,
                    on,
                    task: task.trim().to_string(),
                },
                Ok(_) => Request::Usage("Nothing to queue: /night add <task>".to_string()),
                Err(error) => Request::Usage(error),
            },
            _ => Request::Usage(USAGE.to_string()),
        }
    }

    /// Parses `mling night …`, whose words the shell has already split.
    pub fn from_args(args: &[String]) -> Request {
        match args.first().map(String::as_str) {
            Some("add") => {
                let mut rest = &args[1..];
                let mut test = None;
                let mut on = None;
                while let Some(first) = rest.first() {
                    let (option, inline) = match first.split_once('=') {
                        Some((option, value)) => (option, Some(value.to_string())),
                        None => (first.as_str(), None),
                    };
                    if option != "--test" && option != "--on" {
                        break;
                    }
                    let (value, used) = match inline {
                        Some(value) => (value, 1),
                        None => match rest.get(1) {
                            Some(value) => (value.clone(), 2),
                            None => return Request::Usage(format!("{option} needs a value")),
                        },
                    };
                    if option == "--test" {
                        test = Some(value);
                    } else {
                        match node_name(&value) {
                            Ok(name) => on = Some(name),
                            Err(error) => return Request::Usage(error),
                        }
                    }
                    rest = &rest[used..];
                }
                let task = rest.join(" ");
                if task.trim().is_empty() {
                    Request::Usage("Nothing to queue: mling night add <task>".to_string())
                } else {
                    Request::Add { test, on, task: task.trim().to_string() }
                }
            }
            _ => Request::from_line(&args.join(" ")),
        }
    }
}

/// Splits the leading `--test` and `--on` options, in either order, off `rest`.
fn take_add_options(mut rest: &str) -> Result<(Option<String>, Option<String>, &str), String> {
    let mut test = None;
    let mut on = None;
    loop {
        rest = rest.trim_start();
        let (found, after) = take_test_option(rest)?;
        if found.is_some() {
            test = found;
            rest = after;
            continue;
        }
        let (found, after) = take_on_option(rest)?;
        if found.is_some() {
            on = found;
            rest = after;
            continue;
        }
        return Ok((test, on, rest));
    }
}

/// Splits a leading `--on <node>` (or `--on=<node>`) off `rest`.
fn take_on_option(rest: &str) -> Result<(Option<String>, &str), String> {
    let Some(after) = rest.strip_prefix("--on") else {
        return Ok((None, rest));
    };
    // A task may begin with a word such as `--only`: only the option proper is taken.
    if !after.is_empty() && !after.starts_with(|next: char| next == '=' || next.is_whitespace()) {
        return Ok((None, rest));
    }
    let after = after.strip_prefix('=').unwrap_or(after).trim_start();
    let end = after.find(char::is_whitespace).unwrap_or(after.len());
    Ok((Some(node_name(&after[..end])?), &after[end..]))
}

/// A node's name as `mling-admin node list` shows it: a host name, not a path or an option.
fn node_name(name: &str) -> Result<String, String> {
    let valid = !name.is_empty()
        && name.len() <= 63
        && name.chars().next().is_some_and(|first| first.is_ascii_alphanumeric())
        && name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'));
    if valid {
        Ok(name.to_string())
    } else if name.is_empty() {
        Err("--on needs a node's name (see mling-admin node list)".to_string())
    } else {
        Err(format!("--on: {name:?} is not a node's name"))
    }
}

/// Splits a leading `--test "<cmd>"` (or `--test=<cmd>`, or an unquoted single word) off `rest`.
fn take_test_option(rest: &str) -> Result<(Option<String>, &str), String> {
    let Some(after) = rest.strip_prefix("--test") else {
        return Ok((None, rest));
    };
    // A task may itself begin with a word such as `--tests`: only the option proper is taken.
    if !after.is_empty() && !after.starts_with(|next: char| next == '=' || next.is_whitespace()) {
        return Ok((None, rest));
    }
    let after = after.strip_prefix('=').unwrap_or(after).trim_start();
    let mut chars = after.char_indices();
    match chars.next() {
        Some((_, quote @ ('"' | '\''))) => match after[1..].find(quote) {
            Some(end) => Ok((Some(after[1..1 + end].to_string()), &after[end + 2..])),
            None => Err(format!("--test: no closing {quote}")),
        },
        Some(_) => {
            let end = after.find(char::is_whitespace).unwrap_or(after.len());
            Ok((Some(after[..end].to_string()), &after[end..]))
        }
        None => Err("--test needs a command".to_string()),
    }
}

/// `$CODEX_HOME/night`, i.e. `~/.mightling/night` unless `CODEX_HOME` says otherwise.
pub fn night_dir() -> Option<PathBuf> {
    let home = codex_utils_home_dir::find_codex_home().ok()?;
    Some(home.as_path().join("night"))
}

/// Runs `/night [args]` from the TUI and returns the lines to print.
pub fn command(args: &str, cwd: &Path) -> Vec<String> {
    if let Some(refusal) = client_refusal(&Request::from_line(args), runner_present()) {
        return vec![refusal];
    }
    match night_dir() {
        Some(dir) => run(&dir, Request::from_line(args), cwd),
        None => vec!["Night Shift: could not resolve CODEX_HOME".to_string()],
    }
}

/// Runs `mling night …` from a shell, for scripts and cron. Returns the exit code.
pub fn run_cli(args: &[String]) -> i32 {
    let Some(dir) = night_dir() else {
        eprintln!("Night Shift: could not resolve CODEX_HOME");
        return 1;
    };
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let request = Request::from_args(args);
    if let Some(refusal) = client_refusal(&request, runner_present()) {
        println!("{refusal}");
        return 1;
    }
    let failed = matches!(request, Request::Usage(_));
    for line in run(&dir, request, &cwd) {
        println!("{line}");
    }
    i32::from(failed) * 2
}

/// Why a task cannot be queued on this machine, if it cannot: the runner is `mling-admin night
/// run`, which exists only on a node, so on a client a queued task would wait for a night that
/// never comes (specs/DREAMFERENCE_MIGHTLING_NODE.md §10). Reading the queue is not refused.
pub fn client_refusal(request: &Request, is_node: bool) -> Option<String> {
    (!is_node && matches!(request, Request::Add { .. })).then(|| {
        "Night Shift runs on a Mightling node, and this machine is a client: nothing here would run the task. \
         Queue it on the node, in a checkout of the repository there."
            .to_string()
    })
}

/// Whether this machine can run a night: it is a node, or the Python half that holds the runner
/// is installed (a node that has not loaded a model since the split has no node id yet).
fn runner_present() -> bool {
    mling_node_locator::is_node()
        || mling_node_locator::home_dir().is_some_and(|home| home.join(".local/bin/mling-admin").exists())
}

/// Does what `request` asks, in queue directory `dir`, for the repository containing `cwd`.
pub fn run(dir: &Path, request: Request, cwd: &Path) -> Vec<String> {
    let repo = match repo_root(cwd) {
        Some(repo) => repo,
        None if matches!(request, Request::Usage(_)) => PathBuf::new(),
        None => return vec!["Night Shift needs a git repository, and this directory is not in one.".to_string()],
    };
    match request {
        Request::Usage(text) => vec![text],
        Request::List => list(dir, &repo),
        Request::Add { test, on, task } => add(dir, &repo, cwd, test, on, &task),
        Request::Show(id) => show(dir, &repo, &id),
        Request::Drop(id) => drop_task(dir, &repo, &id),
        Request::Report => report(dir, &repo),
    }
}

fn add(dir: &Path, repo: &Path, cwd: &Path, test: Option<String>, on: Option<String>, task: &str) -> Vec<String> {
    let Some(base) = git(cwd, &["rev-parse", "HEAD"]) else {
        return vec!["Night Shift: this repository has no commit yet, so there is nothing to start from.".to_string()];
    };
    let now = chrono::Local::now();
    let record_for = |id: &str| {
        json!({
            "id": id,
            "repo": repo.to_string_lossy(),
            "base": base,
            "branch": format!("night/{id}"),
            "task": task,
            "test": test,
            "on": on,
            "model_at_add": served_model_id(),
            "status": "queued",
            "history": [{"at": now.to_rfc3339_opts(chrono::SecondsFormat::Secs, false), "status": "queued"}],
            "attempts": 0,
            "nudges": 0,
            "session": null,
            "result": null,
        })
    };
    let id = match create_task(dir, &now.format("%Y%m%d-%H%M").to_string(), record_for) {
        Ok(id) => id,
        Err(error) => return vec![format!("Night Shift: could not queue the task: {error}")],
    };
    let mut lines = vec![
        format!("Queued {id} on branch night/{id}, from {}.", &base[..base.len().min(10)]),
        match &test {
            Some(command) => format!("Test command: {command}"),
            None => "Test command: detected in the worktree when the task runs.".to_string(),
        },
    ];
    if let Some(node) = &on {
        lines.push(format!(
            "Runs on {node}: tonight's run here hands it over, {node}'s own runner works it with its own model, \
             and the branch comes back here."
        ));
    }
    if git(cwd, &["status", "--porcelain"]).is_some_and(|status| !status.is_empty()) {
        lines.push("Note: the working tree has uncommitted changes; the night run starts from HEAD and will not see them.".to_string());
    }
    lines.push(next_window_line());
    lines
}

/// Writes a new task file under a fresh id, `<stamp>-<3 hex>`. The file is staged and then hard
/// linked into place, which fails rather than overwriting if the id is taken.
fn create_task(dir: &Path, stamp: &str, record_for: impl Fn(&str) -> Value) -> std::io::Result<String> {
    let tasks = dir.join("tasks");
    std::fs::create_dir_all(&tasks)?;
    for _ in 0..64 {
        let id = format!("{stamp}-{:03x}", rand::random::<u16>() & 0xfff);
        let path = tasks.join(format!("{id}.json"));
        if path.exists() {
            continue;
        }
        let staging = tasks.join(format!(".{id}.{}.tmp", std::process::id()));
        std::fs::write(&staging, pretty(&record_for(&id)))?;
        let linked = std::fs::hard_link(&staging, &path);
        let _ = std::fs::remove_file(&staging);
        match linked {
            Ok(()) => return Ok(id),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(std::io::Error::other("no free task id"))
}

fn list(dir: &Path, repo: &Path) -> Vec<String> {
    let tasks = tasks_for(dir, repo);
    let mut lines = Vec::new();
    if tasks.is_empty() {
        lines.push("No Night Shift tasks for this repository. Queue one with /night add <task>.".to_string());
    }
    let now = chrono::Local::now();
    for task in &tasks {
        let age = last_change(task)
            .map(|at| short_age(now.signed_duration_since(at)))
            .unwrap_or_default();
        let node = task["on"].as_str().map(|node| format!("[{node}] ")).unwrap_or_default();
        lines.push(format!(
            "{}  {:<16} {:>4}  {node}{}",
            text(task, "id"),
            text(task, "status"),
            age,
            first_line(&text(task, "task"), 60),
        ));
    }
    lines.push(next_window_line());
    lines
}

fn show(dir: &Path, repo: &Path, id: &str) -> Vec<String> {
    let Some(task) = find(dir, repo, id) else {
        return vec![format!("No task {id} for this repository (see /night list).")];
    };
    let mut lines = vec![
        format!("{}  {}", text(&task, "id"), text(&task, "status")),
        format!("Branch: {}   base: {}", text(&task, "branch"), text(&task, "base")),
        format!("Test: {}", task["test"].as_str().unwrap_or("detected at run time")),
        format!("Model when queued: {}", task["model_at_add"].as_str().unwrap_or("unknown")),
    ];
    if let Some(node) = task["on"].as_str() {
        lines.push(format!("Node: {node} (its own runner works the task)"));
    }
    lines.push("Task:".to_string());
    lines.extend(text(&task, "task").lines().map(|line| format!("  {line}")));
    lines.push("History:".to_string());
    for entry in task["history"].as_array().into_iter().flatten() {
        let note = entry["note"].as_str().map(|note| format!(" — {note}")).unwrap_or_default();
        lines.push(format!("  {}  {}{note}", text(entry, "at"), text(entry, "status")));
    }
    if let Some(result) = task["result"].as_object() {
        lines.push("Result:".to_string());
        for (key, value) in result {
            let value = value.as_str().map(str::to_string).unwrap_or_else(|| value.to_string());
            let mut value_lines = value.lines();
            lines.push(format!("  {key}: {}", value_lines.next().unwrap_or("")));
            lines.extend(value_lines.map(|line| format!("    {line}")));
        }
    }
    lines
}

fn drop_task(dir: &Path, repo: &Path, id: &str) -> Vec<String> {
    let Some(found) = find(dir, repo, id) else {
        return vec![format!("No task {id} for this repository (see /night list).")];
    };
    let id = text(&found, "id");
    let id = id.as_str();
    let outcome = update_task(dir, id, |task| {
        let status = text(task, "status");
        let next = match status.as_str() {
            "queued" | "interrupted" => "cancelled",
            // A task another node holds is dropped there by the next night run here.
            "running" | "sent" => "cancel-requested",
            _ => return Err(format!("{id} is {status}; there is nothing to cancel.")),
        };
        set_status(task, next, None);
        Ok(next)
    });
    match outcome {
        Ok("cancelled") => vec![format!("Cancelled {id}.")],
        Ok(_) if text(&found, "status") == "sent" => vec![format!(
            "{id} is on {}; the next night run here tells that node to stop it.",
            found["on"].as_str().unwrap_or("another node")
        )],
        Ok(_) => vec![format!("{id} is running; it stops at its next step.")],
        Err(message) => vec![message],
    }
}

fn report(dir: &Path, repo: &Path) -> Vec<String> {
    let Some(path) = latest_report(dir) else {
        return vec!["No Night Shift report yet.".to_string()];
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return vec![format!("Could not read {}", path.display())];
    };
    match report_section(&text, repo) {
        Some(section) => {
            let mut lines = vec![format!("{}:", path.display())];
            lines.extend(section.lines().map(str::to_string));
            lines
        }
        None => vec![format!("The latest report ({}) has nothing for this repository.", path.display())],
    }
}

/// The `## <repo>` section of a report, up to the next `## ` heading: another repository's
/// section or the closing notes (`## Review`, `## Notes`), which are left out.
pub fn report_section(text: &str, repo: &Path) -> Option<String> {
    let heading = format!("## {}", repo.display());
    let start = text.lines().position(|line| line.trim_end() == heading)?;
    let lines: Vec<&str> = text.lines().collect();
    let end = lines[start + 1..]
        .iter()
        .position(|line| line.starts_with("## "))
        .map_or(lines.len(), |offset| start + 1 + offset);
    Some(lines[start..end].join("\n").trim_end().to_string())
}

/// The line `mling` prints before the TUI opens when a night run finished tasks for this
/// repository since the last such line, e.g. `Night Shift: 3 done, 1 stalled — /night report`.
pub fn startup_line(dir: &Path, cwd: &Path) -> Option<String> {
    let repo = repo_root(cwd)?;
    let seen_path = dir.join("seen.json");
    let mut seen: BTreeMap<String, String> = std::fs::read_to_string(&seen_path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default();
    let key = repo.to_string_lossy().into_owned();
    let since = seen
        .get(&key)
        .and_then(|at| chrono::DateTime::parse_from_rfc3339(at).ok());
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut newest = since;
    for task in tasks_for(dir, &repo) {
        let status = text(&task, "status");
        let Some(at) = last_change(&task) else { continue };
        if FINISHED.contains(&status.as_str()) && since.is_none_or(|since| at > since) {
            *counts.entry(status).or_default() += 1;
            newest = Some(newest.map_or(at, |newest| newest.max(at)));
        }
    }
    if counts.is_empty() {
        return None;
    }
    if let Some(newest) = newest {
        seen.insert(key, newest.to_rfc3339());
        let _ = write_atomically(&seen_path, pretty(&json!(seen)).as_bytes());
    }
    let summary: Vec<String> = FINISHED
        .iter()
        .filter_map(|status| counts.get(*status).map(|count| format!("{count} {status}")))
        .collect();
    Some(format!("Night Shift: {} — /night report", summary.join(", ")))
}

/// Read-modify-write of one task under `tasks/<id>.lock`, the lock `mling-admin night run`
/// takes for the same files (flock on Linux on both sides), so a drop and a status change can
/// never overwrite each other.
pub fn update_task<T>(
    dir: &Path,
    id: &str,
    change: impl FnOnce(&mut Value) -> Result<T, String>,
) -> Result<T, String> {
    let tasks = dir.join("tasks");
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(tasks.join(format!("{id}.lock")))
        .map_err(|error| format!("could not lock {id}: {error}"))?;
    lock.lock().map_err(|error| format!("could not lock {id}: {error}"))?;
    let path = tasks.join(format!("{id}.json"));
    let mut task = read_task(&path).ok_or_else(|| format!("could not read {id}"))?;
    let outcome = change(&mut task)?;
    write_atomically(&path, pretty(&task).as_bytes()).map_err(|error| format!("could not write {id}: {error}"))?;
    drop::<File>(lock);
    Ok(outcome)
}

fn set_status(task: &mut Value, status: &str, note: Option<&str>) {
    task["status"] = json!(status);
    let mut entry = json!({
        "at": chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, false),
        "status": status,
    });
    if let Some(note) = note {
        entry["note"] = json!(note);
    }
    if let Some(history) = task["history"].as_array_mut() {
        history.push(entry);
    }
}

/// Tasks queued for `repo`, oldest first (ids sort by the time they were added).
pub fn tasks_for(dir: &Path, repo: &Path) -> Vec<Value> {
    let Ok(entries) = std::fs::read_dir(dir.join("tasks")) else {
        return Vec::new();
    };
    let repo = repo.to_string_lossy();
    let mut tasks: Vec<Value> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "json"))
        .filter_map(|path| read_task(&path))
        .filter(|task| task["repo"].as_str() == Some(&*repo))
        .collect();
    tasks.sort_by_key(|task| text(task, "id"));
    tasks
}

/// Finds a task of `repo` by its id, or by a unique id suffix (the 3-hex tail is enough).
fn find(dir: &Path, repo: &Path, id: &str) -> Option<Value> {
    let tasks = tasks_for(dir, repo);
    if let Some(task) = tasks.iter().find(|task| text(task, "id") == id) {
        return Some(task.clone());
    }
    let matches: Vec<&Value> = tasks.iter().filter(|task| text(task, "id").ends_with(id)).collect();
    (matches.len() == 1).then(|| matches[0].clone())
}

fn read_task(path: &Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// The newest report by modification time: a second run on one date is `<date>-2.md`, which
/// sorts before `<date>.md` by name.
fn latest_report(dir: &Path) -> Option<PathBuf> {
    std::fs::read_dir(dir.join("reports"))
        .ok()?
        .flatten()
        .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "md"))
        .filter_map(|entry| Some((entry.metadata().ok()?.modified().ok()?, entry.path())))
        .max()
        .map(|(_, path)| path)
}

/// The repository a directory belongs to. A linked worktree belongs to its main checkout, so a
/// task queued from one is listed with the others.
pub fn repo_root(cwd: &Path) -> Option<PathBuf> {
    let top = PathBuf::from(git(cwd, &["rev-parse", "--show-toplevel"])?);
    let common = git(cwd, &["rev-parse", "--path-format=absolute", "--git-common-dir"]).map(PathBuf::from);
    match common {
        Some(common) if common.file_name().is_some_and(|name| name == ".git") => {
            Some(common.parent().map(Path::to_path_buf).unwrap_or(top))
        }
        _ => Some(top),
    }
}

fn git(cwd: &Path, args: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(cwd)
        .arg("--no-optional-locks")
        .args(args)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim_end().to_string())
}

/// The id of the model the launcher last configured, from the catalog it wrote. For the report
/// only; it does not pin the model a night run uses.
fn served_model_id() -> Option<String> {
    let home = codex_utils_home_dir::find_codex_home().ok()?;
    let catalog: Value = serde_json::from_str(&std::fs::read_to_string(home.as_path().join("model_catalog.json")).ok()?).ok()?;
    catalog["models"][0]["slug"].as_str().map(str::to_string)
}

/// `Next window: 01:00-07:00.` The installed timer's window wins (`mling-admin night enable`
/// writes it on a marker line); without a timer, `[night] window` from the config file says what
/// enabling would install.
fn next_window_line() -> String {
    let timer = std::env::var_os("HOME")
        .map(|home| PathBuf::from(home).join(".config/systemd/user/mightling-night.timer"))
        .and_then(|path| std::fs::read_to_string(path).ok());
    if let Some(window) = timer.as_deref().and_then(window_from_timer) {
        return format!("Next window: {window}.");
    }
    let window = crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| window_from_toml(&text))
        .unwrap_or_else(|| DEFAULT_WINDOW.to_string());
    format!("Night Shift is not enabled; `mling-admin night enable` runs the queue every night, {window}.")
}

fn window_from_timer(text: &str) -> Option<String> {
    text.lines()
        .find_map(|line| line.strip_prefix("# Night Shift window: "))
        .map(|window| window.trim().to_string())
}

fn window_from_toml(text: &str) -> Option<String> {
    let doc: toml::Table = text.parse().ok()?;
    doc.get("night")?.get("window")?.as_str().map(str::to_string)
}

fn last_change(task: &Value) -> Option<chrono::DateTime<chrono::FixedOffset>> {
    let at = task["history"].as_array()?.last()?["at"].as_str()?;
    chrono::DateTime::parse_from_rfc3339(at).ok()
}

fn short_age(age: chrono::TimeDelta) -> String {
    let minutes = age.num_minutes().max(0);
    match minutes {
        0..60 => format!("{minutes}m"),
        60..2880 => format!("{}h", minutes / 60),
        _ => format!("{}d", minutes / 1440),
    }
}

fn first_line(text: &str, width: usize) -> String {
    let line = text.lines().next().unwrap_or("");
    if line.chars().count() > width {
        format!("{}…", line.chars().take(width - 1).collect::<String>())
    } else {
        line.to_string()
    }
}

fn text(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or_default().to_string()
}

fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_default() + "\n"
}

fn write_atomically(path: &Path, contents: &[u8]) -> std::io::Result<()> {
    let name = path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
    let staging = path.with_file_name(format!(".{name}.{}.tmp", std::process::id()));
    std::fs::write(&staging, contents)?;
    std::fs::rename(&staging, path).inspect_err(|_| {
        let _ = std::fs::remove_file(&staging);
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mightling-night-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap_or_default();
        dir
    }

    /// A repository with one commit, and a queue directory beside it.
    fn repo(name: &str) -> (PathBuf, PathBuf) {
        let root = scratch(name);
        let repo = root.join("repo");
        std::fs::create_dir_all(&repo).unwrap_or_default();
        for args in [
            vec!["init", "-q"],
            vec!["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"],
        ] {
            let _ = Command::new("git").arg("-C").arg(&repo).args(&args).output();
        }
        let repo = repo.canonicalize().unwrap_or(repo);
        (repo, root.join("night"))
    }

    #[test]
    fn parses_every_form_of_the_slash_command() {
        assert_eq!(Request::from_line(""), Request::List);
        assert_eq!(Request::from_line("list"), Request::List);
        assert_eq!(Request::from_line("report"), Request::Report);
        assert_eq!(Request::from_line("show 20260101-0100-abc"), Request::Show("20260101-0100-abc".into()));
        assert_eq!(Request::from_line("drop abc"), Request::Drop("abc".into()));
        assert_eq!(
            Request::from_line("add Fix the flaky test.\nThen run it twice."),
            Request::Add { test: None, on: None, task: "Fix the flaky test.\nThen run it twice.".into() }
        );
        assert_eq!(
            Request::from_line("add --test \"pytest -q tests/x.py\" Add type hints"),
            Request::Add { test: Some("pytest -q tests/x.py".into()), on: None, task: "Add type hints".into() }
        );
        assert_eq!(
            Request::from_line("add --test=make Build it"),
            Request::Add { test: Some("make".into()), on: None, task: "Build it".into() }
        );
        assert_eq!(
            Request::from_line("add --tests are slow, speed them up"),
            Request::Add { test: None, on: None, task: "--tests are slow, speed them up".into() }
        );
        assert_eq!(
            Request::from_line("add --on spark-2 --test \"pytest -q\" Fix the flaky test"),
            Request::Add { test: Some("pytest -q".into()), on: Some("spark-2".into()), task: "Fix the flaky test".into() }
        );
        assert_eq!(
            Request::from_line("add --test=make --on=spark-2.local Build it"),
            Request::Add { test: Some("make".into()), on: Some("spark-2.local".into()), task: "Build it".into() }
        );
        assert_eq!(
            Request::from_line("add --only the docs need it"),
            Request::Add { test: None, on: None, task: "--only the docs need it".into() }
        );
        assert!(matches!(Request::from_line("add --on"), Request::Usage(_)));
        assert!(matches!(Request::from_line("add --on /etc Fix"), Request::Usage(_)));
        assert!(matches!(Request::from_line("add"), Request::Usage(_)));
        assert!(matches!(Request::from_line("add --test \"unclosed task"), Request::Usage(_)));
        assert!(matches!(Request::from_line("show"), Request::Usage(_)));
        assert!(matches!(Request::from_line("launch"), Request::Usage(_)));
    }

    #[test]
    fn on_a_client_a_task_is_refused_and_the_queue_can_still_be_read() {
        let add = Request::from_line("add Fix the flaky test");
        assert!(client_refusal(&add, false).is_some_and(|text| text.contains("runs on a Mightling node")));
        assert_eq!(client_refusal(&add, true), None);
        assert_eq!(client_refusal(&Request::List, false), None);
        assert_eq!(client_refusal(&Request::Report, false), None);
    }

    #[test]
    fn parses_the_shell_form() {
        let args = |words: &[&str]| words.iter().map(|word| word.to_string()).collect::<Vec<_>>();
        assert_eq!(Request::from_args(&[]), Request::List);
        assert_eq!(
            Request::from_args(&args(&["add", "--test", "cargo test -p x", "Fix", "it"])),
            Request::Add { test: Some("cargo test -p x".into()), on: None, task: "Fix it".into() }
        );
        assert_eq!(
            Request::from_args(&args(&["add", "--on", "spark-2", "--test=make", "Fix", "it"])),
            Request::Add { test: Some("make".into()), on: Some("spark-2".into()), task: "Fix it".into() }
        );
        assert!(matches!(Request::from_args(&args(&["add", "--on", "../x", "Fix"])), Request::Usage(_)));
        assert!(matches!(Request::from_args(&args(&["add", "--on"])), Request::Usage(_)));
        assert_eq!(Request::from_args(&args(&["drop", "abc"])), Request::Drop("abc".into()));
        assert!(matches!(Request::from_args(&args(&["add", "--test"])), Request::Usage(_)));
    }

    #[test]
    fn add_list_show_drop_round_trip() {
        let (repo, dir) = repo("roundtrip");
        let added = run(&dir, Request::from_line("add --test \"true\" Rename a function"), &repo);
        assert!(added[0].starts_with("Queued "), "{added:?}");
        let tasks = tasks_for(&dir, &repo);
        assert_eq!(tasks.len(), 1);
        let task = &tasks[0];
        let id = text(task, "id");
        assert_eq!(text(task, "branch"), format!("night/{id}"));
        assert_eq!(text(task, "status"), "queued");
        assert_eq!(task["test"], json!("true"));
        assert_eq!(task["base"].as_str().map(str::len), Some(40));
        assert!(list(&dir, &repo).iter().any(|line| line.contains(&id) && line.contains("Rename a function")));
        assert!(show(&dir, &repo, &id).iter().any(|line| line == "  Rename a function"));
        // A unique suffix is enough to name a task.
        let suffix = &id[id.len() - 3..];
        assert_eq!(drop_task(&dir, &repo, suffix), vec![format!("Cancelled {id}.")]);
        assert_eq!(text(&tasks_for(&dir, &repo)[0], "status"), "cancelled");
        assert!(drop_task(&dir, &repo, &id)[0].contains("nothing to cancel"));
        // No staging or lock files are mistaken for tasks.
        assert_eq!(tasks_for(&dir, &repo).len(), 1);
    }

    #[test]
    fn a_task_for_another_node_records_it_and_its_drop_is_forwarded() {
        let (repo, dir) = repo("on");
        let added = run(&dir, Request::from_line("add --on spark-2 Fix the flaky test"), &repo);
        assert!(added.iter().any(|line| line.starts_with("Runs on spark-2")), "{added:?}");
        let id = text(&tasks_for(&dir, &repo)[0], "id");
        assert_eq!(tasks_for(&dir, &repo)[0]["on"], json!("spark-2"));
        assert!(list(&dir, &repo).iter().any(|line| line.contains("[spark-2] Fix the flaky test")));
        assert!(show(&dir, &repo, &id).iter().any(|line| line.starts_with("Node: spark-2")));
        update_task(&dir, &id, |task| {
            set_status(task, "sent", None);
            Ok(())
        })
        .unwrap_or_default();
        assert!(drop_task(&dir, &repo, &id)[0].contains("tells that node to stop it"));
        assert_eq!(text(&tasks_for(&dir, &repo)[0], "status"), "cancel-requested");
        // Without --on the field is there and empty: the runner reads it as "run it here".
        run(&dir, Request::from_line("add Here"), &repo);
        assert!(tasks_for(&dir, &repo).iter().any(|task| task["on"].is_null()));
    }

    #[test]
    fn dropping_a_running_task_asks_the_runner_to_stop_it() {
        let (repo, dir) = repo("running");
        run(&dir, Request::from_line("add Long task"), &repo);
        let id = text(&tasks_for(&dir, &repo)[0], "id");
        update_task(&dir, &id, |task| {
            set_status(task, "running", None);
            Ok(())
        })
        .unwrap_or_default();
        assert!(drop_task(&dir, &repo, &id)[0].contains("stops at its next step"));
        assert_eq!(text(&tasks_for(&dir, &repo)[0], "status"), "cancel-requested");
    }

    #[test]
    fn uncommitted_changes_are_named_and_the_task_is_queued_anyway() {
        let (repo, dir) = repo("dirty");
        std::fs::write(repo.join("new.txt"), "x").unwrap_or_default();
        let lines = run(&dir, Request::from_line("add Something"), &repo);
        assert!(lines.iter().any(|line| line.contains("uncommitted changes")), "{lines:?}");
        assert_eq!(tasks_for(&dir, &repo).len(), 1);
    }

    #[test]
    fn outside_a_repository_it_refuses() {
        let dir = scratch("norepo");
        let lines = run(&dir.join("night"), Request::from_line("add Something"), &dir);
        assert!(lines[0].contains("needs a git repository"));
        assert!(!dir.join("night/tasks").exists());
    }

    #[test]
    fn a_linked_worktree_belongs_to_its_main_checkout() {
        let (repo, _) = repo("worktree");
        let linked = repo.parent().map(|parent| parent.join("linked")).unwrap_or_default();
        let _ = Command::new("git")
            .arg("-C")
            .arg(&repo)
            .args(["worktree", "add", "-q", "--detach"])
            .arg(&linked)
            .output();
        assert_eq!(repo_root(&linked), Some(repo.clone()));
        assert_eq!(repo_root(&repo), Some(repo));
    }

    #[test]
    fn ids_never_collide() {
        let dir = scratch("ids");
        let ids: std::collections::BTreeSet<String> = (0..200)
            .map(|_| create_task(&dir, "20260101-0100", |id| json!({"id": id})).unwrap_or_default())
            .collect();
        assert_eq!(ids.len(), 200);
        assert!(ids.iter().all(|id| id.len() == "20260101-0100-abc".len()));
    }

    #[test]
    fn the_report_section_is_this_repositorys_only() {
        let report = "# Night Shift — 2026-10-01\n\n## /a\n\n| row a |\n\n## /b\n\n| row b |\n";
        assert_eq!(report_section(report, Path::new("/a")).as_deref(), Some("## /a\n\n| row a |"));
        assert_eq!(report_section(report, Path::new("/b")).as_deref(), Some("## /b\n\n| row b |"));
        assert_eq!(report_section(report, Path::new("/c")), None);
    }

    #[test]
    fn the_startup_line_announces_finished_tasks_once() {
        let (repo, dir) = repo("startup");
        assert_eq!(startup_line(&dir, &repo), None);
        for _ in 0..3 {
            run(&dir, Request::from_line("add A task"), &repo);
        }
        let ids: Vec<String> = tasks_for(&dir, &repo).iter().map(|task| text(task, "id")).collect();
        for (id, status) in ids.iter().zip(["done", "done", "stalled"]) {
            update_task(&dir, id, |task| {
                set_status(task, status, None);
                Ok(())
            })
            .unwrap_or_default();
        }
        assert_eq!(
            startup_line(&dir, &repo).as_deref(),
            Some("Night Shift: 2 done, 1 stalled — /night report")
        );
        assert_eq!(startup_line(&dir, &repo), None);
    }

    #[test]
    fn the_window_comes_from_the_timer_then_the_night_table() {
        assert_eq!(
            window_from_timer("# Night Shift window: 01:30-06:00\n[Unit]\n").as_deref(),
            Some("01:30-06:00")
        );
        assert_eq!(window_from_toml("[night]\nwindow = \"02:00-05:30\"\n").as_deref(), Some("02:00-05:30"));
        assert_eq!(window_from_toml("vllm_host = \"x\"\n"), None);
    }

    #[test]
    fn report_shows_this_repositorys_section_of_the_newest_report() {
        let (repo, dir) = repo("report");
        assert_eq!(report(&dir, &repo), vec!["No Night Shift report yet.".to_string()]);
        let reports = dir.join("reports");
        std::fs::create_dir_all(&reports).unwrap_or_default();
        std::fs::write(reports.join("2026-10-01.md"), format!("# old\n\n## {}\n\nold row\n", repo.display()))
            .unwrap_or_default();
        std::thread::sleep(std::time::Duration::from_millis(20));
        std::fs::write(reports.join("2026-10-01-2.md"), format!("# new\n\n## {}\n\nnew row\n\n## Review\n", repo.display()))
            .unwrap_or_default();
        let lines = report(&dir, &repo);
        assert!(lines.iter().any(|line| line == "new row"), "{lines:?}");
        assert!(!lines.iter().any(|line| line == "## Review"));
    }
}
