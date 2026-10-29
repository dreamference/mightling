//! Night Shift from a phone (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16.8; MOONSHOT §3.5): which
//! repositories a task can go to, queueing one, the queue's state, and the morning reports, for
//! the phone bridges, which reach the user's files only through `ling web` (the Signal bridge's
//! system account cannot read the home folder at all).
//!
//! Night Shift's files are the launcher's (`$CODEX_HOME/night/`: `tasks/<id>.json`,
//! `reports/<date>.md`; ling-rs `night.rs`). Reading them needs nothing more; a task is queued by
//! running `ling night add <task>` in the repository, so the record is the launcher's own.

use std::path::Path;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;
use std::time::SystemTime;

use serde_json::Value;
use serde_json::json;

use crate::app_server::Launch;

/// Statuses after which a task is finished (night.rs's `FINISHED`, and `cancelled`).
const FINISHED: &[&str] = &["done", "no-change", "stalled", "failed", "interrupted", "cancelled"];
/// A finished task is still shown this long after its last change.
const RECENT_HOURS: u64 = 36;
/// Shown at most.
const STATUS_LIMIT: usize = 15;
/// A report's text, at most.
pub const REPORT_CHARS: usize = 3_500;
/// `ling night add` reads git and writes one file; it never takes this long.
const ADD_TIMEOUT: Duration = Duration::from_secs(60);

/// Every task, with when its file last changed (each change replaces the file whole).
fn tasks_with_time(night: &Path) -> Vec<(SystemTime, Value)> {
    let Ok(entries) = std::fs::read_dir(night.join("tasks")) else { return Vec::new() };
    entries
        .flatten()
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .filter_map(|e| {
            let changed = e.metadata().ok()?.modified().ok()?;
            let task = serde_json::from_str::<Value>(&std::fs::read_to_string(e.path()).ok()?).ok()?;
            Some((changed, task))
        })
        .collect()
}

fn tasks(night: &Path) -> Vec<Value> {
    tasks_with_time(night).into_iter().map(|(_, task)| task).collect()
}

fn expand(path: &str, home: Option<&Path>) -> PathBuf {
    match (path.strip_prefix("~/"), home) {
        (Some(rest), Some(home)) => home.join(rest),
        _ => PathBuf::from(path),
    }
}

/// The repositories a phone task can go to: `[chat] night_repos` in the user's configuration,
/// then every repository a task was ever queued in; each an existing git checkout, once.
pub fn repos(night: &Path, configured: &[String], home: Option<&Path>) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = Vec::new();
    let from_tasks = tasks(night).into_iter().filter_map(|t| t.get("repo").and_then(Value::as_str).map(PathBuf::from));
    for path in configured.iter().map(|p| expand(p, home)).chain(from_tasks) {
        let path = path.canonicalize().unwrap_or(path);
        if path.join(".git").exists() && !found.contains(&path) {
            found.push(path);
        }
    }
    found
}

/// A repository's short name, for the phone.
pub fn name_of(repo: &Path) -> String {
    repo.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| repo.display().to_string())
}

fn first_line(text: &str, limit: usize) -> String {
    let line = text.lines().find(|l| !l.trim().is_empty()).unwrap_or_default().trim();
    let mut cut: String = line.chars().take(limit).collect();
    if line.chars().count() > limit {
        cut.push('…');
    }
    cut
}

/// The queue as the phone shows it: tasks not finished, and finished ones changed in the last
/// [`RECENT_HOURS`], newest first.
pub fn status(night: &Path, now: SystemTime) -> Vec<Value> {
    let mut shown: Vec<(SystemTime, Value)> = tasks_with_time(night)
        .into_iter()
        .filter_map(|(changed, task)| {
            let status = task.get("status").and_then(Value::as_str).unwrap_or("queued").to_string();
            let recent = now.duration_since(changed).map_or(true, |age| age.as_secs() < RECENT_HOURS * 3600);
            if FINISHED.contains(&status.as_str()) && !recent {
                return None;
            }
            let repo = task.get("repo").and_then(Value::as_str).map(|r| name_of(Path::new(r))).unwrap_or_default();
            let entry = json!({
                "id": task.get("id").and_then(Value::as_str).unwrap_or_default(),
                "status": status,
                "repo": repo,
                "task": first_line(task.get("task").and_then(Value::as_str).unwrap_or_default(), 80),
                "branch": task.get("branch").and_then(Value::as_str).unwrap_or_default(),
            });
            Some((changed, entry))
        })
        .collect();
    shown.sort_by(|a, b| b.0.cmp(&a.0));
    shown.into_iter().take(STATUS_LIMIT).map(|(_, entry)| entry).collect()
}

/// Where a report's name falls among the others: `YYYY-MM-DD.md` is a night's first run, and a later
/// run the same day is `YYYY-MM-DD-2.md`, `-3.md` … (night_shift_report.py), which plain string order
/// would put before the first and `-10` before `-2`.
fn report_order(name: &str) -> (&str, u64, &str) {
    let stem = name.strip_suffix(".md").unwrap_or(name);
    let (date, rest) = stem.split_at(stem.char_indices().nth(10).map_or(stem.len(), |(at, _)| at));
    match rest.strip_prefix('-').and_then(|run| run.parse::<u64>().ok()) {
        Some(run) => (date, run, ""),
        None if rest.is_empty() => (date, 1, ""),
        None => (date, 0, rest),
    }
}

/// The morning reports named after `after` (by date, then by run, as [`report_order`] puts them),
/// oldest first, each cut at [`REPORT_CHARS`]; and the newest name there is, for a bridge starting
/// fresh.
pub fn reports_after(night: &Path, after: &str) -> (Vec<Value>, Option<String>) {
    let Ok(entries) = std::fs::read_dir(night.join("reports")) else { return (Vec::new(), None) };
    let mut names: Vec<String> = entries.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| n.ends_with(".md") && !n.starts_with('.')).collect();
    names.sort_by(|a, b| report_order(a).cmp(&report_order(b)).then_with(|| a.cmp(b)));
    let latest = names.last().cloned();
    let after = report_order(after);
    let reports = names
        .into_iter()
        .filter(|name| report_order(name) > after)
        .filter_map(|name| {
            let text = std::fs::read_to_string(night.join("reports").join(&name)).ok()?;
            let mut cut: String = text.chars().take(REPORT_CHARS).collect();
            if text.chars().count() > REPORT_CHARS {
                cut.push_str("\n\n… (the rest is in the report on the node: /night report there)");
            }
            Some(json!({ "name": name, "text": cut.trim() }))
        })
        .collect();
    (reports, latest)
}

/// A task as `ling night add` takes it: never read as one of its options.
pub fn task_argument(task: &str) -> String {
    let task = task.trim();
    if task.starts_with('-') { format!("Task: {task}") } else { task.to_string() }
}

/// Queues `task` in `repo` with `ling night add`; its lines, and whether it queued.
pub async fn add(launch: &Launch, repo: &Path, task: &str) -> (bool, Vec<String>) {
    let mut command = tokio::process::Command::new(&launch.ling);
    command
        .args(["night", "add"])
        .arg(task_argument(task))
        .current_dir(repo)
        .envs(launch.env.iter().map(|(key, value)| (key.as_str(), value.as_str())))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    match tokio::time::timeout(ADD_TIMEOUT, command.output()).await {
        Ok(Ok(output)) => {
            let lines: Vec<String> = String::from_utf8_lossy(&output.stdout).lines().map(str::to_string).filter(|l| !l.trim().is_empty()).collect();
            let queued = output.status.success() && lines.first().is_some_and(|l| l.starts_with("Queued "));
            (queued, lines)
        }
        Ok(Err(err)) => (false, vec![format!("Could not run ling night add: {err}")]),
        Err(_) => (false, vec!["ling night add took too long".to_string()]),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-web-night-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("night/tasks")).unwrap();
        std::fs::create_dir_all(dir.join("night/reports")).unwrap();
        dir
    }

    #[test]
    fn repositories_come_from_the_setting_and_the_queue_and_must_be_checkouts() {
        let dir = scratch("repos");
        std::fs::create_dir_all(dir.join("home/code/app/.git")).unwrap();
        std::fs::create_dir_all(dir.join("lib/.git")).unwrap();
        std::fs::create_dir_all(dir.join("not-a-repo")).unwrap();
        std::fs::write(dir.join("night/tasks/1.json"), json!({ "id": "1", "repo": dir.join("lib") }).to_string()).unwrap();
        let configured = vec!["~/code/app".to_string(), dir.join("not-a-repo").to_string_lossy().into_owned(), "~/code/app".to_string()];
        let found = repos(&dir.join("night"), &configured, Some(&dir.join("home")));
        let names: Vec<String> = found.iter().map(|p| name_of(p)).collect();
        assert_eq!(names, ["app", "lib"]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn status_shows_the_open_and_the_recent_and_reports_come_after_a_name() {
        let dir = scratch("status");
        let night = dir.join("night");
        let task = |id: &str, status: &str| json!({ "id": id, "repo": "/w/app", "status": status, "task": "Fix the parser\nmore", "branch": format!("night/{id}") });
        std::fs::write(night.join("tasks/b.json"), task("b", "done").to_string()).unwrap();
        std::thread::sleep(Duration::from_millis(20));
        std::fs::write(night.join("tasks/a.json"), task("a", "queued").to_string()).unwrap();
        std::fs::write(night.join("tasks/c.json"), task("c", "done").to_string()).unwrap();
        // Two days on, the finished ones are past; the queued one still shows.
        let later = SystemTime::now() + Duration::from_secs(48 * 3600);
        let ids = |shown: Vec<Value>| shown.iter().map(|t| t["id"].as_str().unwrap().to_string()).collect::<Vec<_>>();
        assert_eq!(ids(status(&night, later)), ["a"]);
        let shown = status(&night, SystemTime::now());
        assert_eq!(shown.len(), 3);
        assert_eq!(ids(shown.clone())[2], "b", "newest first");
        assert_eq!(shown[0]["task"], "Fix the parser");
        assert_eq!(shown[0]["repo"], "app");
        std::fs::write(night.join("reports/2026-10-20.md"), "# Night of the 20th").unwrap();
        std::fs::write(night.join("reports/2026-10-21.md"), "# Night of the 21st").unwrap();
        let (reports, latest) = reports_after(&night, "2026-10-20.md");
        assert_eq!(latest.as_deref(), Some("2026-10-21.md"));
        assert_eq!(reports, vec![json!({ "name": "2026-10-21.md", "text": "# Night of the 21st" })]);
        assert_eq!(reports_after(&night, "").0.len(), 2);
        assert_eq!(task_argument("--test rm -rf"), "Task: --test rm -rf");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_second_run_on_the_same_night_comes_after_the_first() {
        let dir = scratch("same-night");
        let night = dir.join("night");
        // night_shift_report.py: `<date>.md`, then `<date>-2.md`, `<date>-3.md` … the same day.
        for name in ["2026-10-21.md", "2026-10-21-2.md", "2026-10-21-10.md", "2026-10-22.md"] {
            std::fs::write(night.join("reports").join(name), name).unwrap();
        }
        let names = |after: &str| -> Vec<String> {
            reports_after(&night, after).0.iter().map(|r| r["name"].as_str().unwrap().to_string()).collect()
        };
        assert_eq!(names(""), ["2026-10-21.md", "2026-10-21-2.md", "2026-10-21-10.md", "2026-10-22.md"]);
        assert_eq!(names("2026-10-21.md"), ["2026-10-21-2.md", "2026-10-21-10.md", "2026-10-22.md"]);
        assert_eq!(names("2026-10-21-2.md"), ["2026-10-21-10.md", "2026-10-22.md"]);
        assert_eq!(names("2026-10-22.md"), Vec::<String>::new());
        std::fs::remove_file(night.join("reports/2026-10-22.md")).unwrap();
        assert_eq!(reports_after(&night, "").1.as_deref(), Some("2026-10-21-10.md"), "the newest is the last run");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
