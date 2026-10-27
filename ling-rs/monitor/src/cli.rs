//! `ling monitor …` (and `ling-monitor …`): the commands, and the one check the timer runs.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;
use std::time::Instant;

use serde::Deserialize;
use serde::Serialize;

use crate::agents;
use crate::agents::Table;
use crate::alerts::Alerts;
use crate::checks;
use crate::checks::CommitProblem;
use crate::checks::Env;
use crate::config::Config;
use crate::limits::Zone;
use crate::summary;
use crate::units;

pub const USAGE: &str = "\
Usage: ling monitor <command>

  check        run one check now: read what the transcripts gained, rewrite the summary, send due alerts
  status       print the summary (--json for summary.json)
  doctor       the paths read, the Claude Code versions seen, and the lines that could not be read
  replay       run the checks over history, sending nothing: --since YYYY-MM-DD [--until YYYY-MM-DD]
  enable       install and start the user timer (one check every 5 minutes)
  disable      stop the timer and remove its units
  notify-test  send one alert through the configured notify command
  digest       the hourly model digest (phase M1; not built yet, does nothing)

Settings: [monitor], [monitor.waits] and [monitor.rules] in ~/.config/dreamference/config.toml.
";

/// What the monitor keeps between checks, in `<state_dir>/state.json`.
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct State {
    pub table: Table,
    pub alerts: Alerts,
    pub last_check: i64,
    /// C5(c): `~/.local/bin` entries (name to target or size) and the count of the home's own entries.
    pub bin_snapshot: BTreeMap<String, String>,
    pub home_entries: BTreeSet<String>,
    /// C5(a): commits already checked.
    pub seen_commits: BTreeSet<String>,
}

/// Runs one command; returns the exit code.
pub fn run(args: &[String], exec_prefix: &[String]) -> i32 {
    let Some(home) = std::env::var_os("HOME").map(PathBuf::from) else {
        eprintln!("ling monitor: HOME is not set");
        return 2;
    };
    let config = Config::load(&home, &home.join(".config/dreamference/config.toml"));
    let now = chrono::Utc::now().timestamp();
    let command = args.first().map(String::as_str).unwrap_or("status");
    let rest = &args[args.len().min(1)..];
    match command {
        "check" => match check(&config, &Zone::system(), now, &SystemProbe, rest.iter().any(|a| a == "--dry-run")) {
            Ok(outcome) => {
                if rest.iter().any(|a| a == "--verbose" || a == "--once") {
                    println!("{outcome}");
                }
                0
            }
            Err(error) => {
                eprintln!("ling monitor check: {error}");
                1
            }
        },
        "status" => {
            let file = if rest.iter().any(|a| a == "--json") { "summary.json" } else { "summary.md" };
            match std::fs::read_to_string(config.state_dir.join(file)) {
                Ok(text) => {
                    print!("{text}");
                    0
                }
                Err(_) => {
                    eprintln!("No summary yet: `ling monitor check` writes one, and `ling monitor enable` runs it every 5 minutes.");
                    1
                }
            }
        }
        "doctor" => doctor(&config, now),
        "replay" => replay_cli(&config, rest),
        "enable" => enable(&home, exec_prefix),
        "disable" => {
            let _ = systemctl(&["disable", "--now", units::CHECK_TIMER]);
            let _ = systemctl(&["disable", "--now", units::DIGEST_TIMER]);
            units::remove(&units::unit_dir(&home));
            let _ = systemctl(&["daemon-reload"]);
            println!("✅ The monitor's timer is stopped and its units removed; its state stays in {}.", config.state_dir.display());
            0
        }
        "notify-test" => match &config.notify_command {
            Some(command) => {
                if notify(command, "monitor: test alert; checks run every 5 min") {
                    println!("✅ sent");
                    0
                } else {
                    eprintln!("❌ the notify command failed");
                    1
                }
            }
            None => {
                eprintln!("No notify command: set [monitor] notify_command in ~/.config/dreamference/config.toml.");
                1
            }
        },
        "digest" => {
            // M1: the Qwen digest of §7.4. The unit exists so enabling it later is one command.
            0
        }
        "help" | "--help" | "-h" => {
            print!("{USAGE}");
            0
        }
        other => {
            eprintln!("ling monitor: unknown command `{other}`\n\n{USAGE}");
            2
        }
    }
}

/// What a check needs from the machine beyond files: replaced in tests.
pub trait Probe {
    fn claude_alive(&self) -> bool;
    fn send(&self, command: &str, text: &str) -> bool;
    fn git(&self, dir: &Path, args: &[&str]) -> Option<String>;
}

pub struct SystemProbe;

impl Probe for SystemProbe {
    fn claude_alive(&self) -> bool {
        let Ok(processes) = std::fs::read_dir("/proc") else { return true };
        processes.flatten().any(|p| {
            std::fs::read_to_string(p.path().join("comm")).is_ok_and(|comm| comm.trim() == "claude")
        })
    }

    fn send(&self, command: &str, text: &str) -> bool {
        notify(command, text)
    }

    fn git(&self, dir: &Path, args: &[&str]) -> Option<String> {
        let output = Command::new("git").arg("-C").arg(dir).args(args).output().ok()?;
        output.status.success().then(|| String::from_utf8_lossy(&output.stdout).into_owned())
    }
}

/// Runs the notify command with the message as its one argument; gives up after 30 seconds.
fn notify(command: &str, text: &str) -> bool {
    let Ok(mut child) = Command::new(command)
        .arg(text)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
    else {
        return false;
    };
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if started.elapsed() < Duration::from_secs(30) => std::thread::sleep(Duration::from_millis(100)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

fn systemctl(args: &[&str]) -> bool {
    Command::new("systemctl").arg("--user").args(args).status().is_ok_and(|s| s.success())
}

fn enable(home: &Path, exec_prefix: &[String]) -> i32 {
    let dir = units::unit_dir(home);
    if let Err(error) = units::write(&dir, exec_prefix) {
        eprintln!("❌ Could not write the units into {}: {error}", dir.display());
        return 1;
    }
    if !(systemctl(&["daemon-reload"]) && systemctl(&["enable", "--now", units::CHECK_TIMER])) {
        eprintln!("❌ systemctl --user could not start {}", units::CHECK_TIMER);
        return 1;
    }
    println!("✅ The monitor checks every 5 minutes ({}); `ling monitor status` shows its summary.", units::CHECK_TIMER);
    0
}

fn load_state(path: &Path) -> Option<State> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// `~/.local/bin`'s entries, each with its link target or its size and time.
fn bin_snapshot(home: &Path) -> BTreeMap<String, String> {
    let mut snapshot = BTreeMap::new();
    let Ok(entries) = std::fs::read_dir(home.join(".local/bin")) else { return snapshot };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let value = match std::fs::read_link(entry.path()) {
            Ok(target) => format!("-> {}", target.display()),
            Err(_) => entry
                .metadata()
                .map(|m| format!("{} {:?}", m.len(), m.modified().ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())))
                .unwrap_or_default(),
        };
        snapshot.insert(name, value);
    }
    snapshot
}

fn home_entries(home: &Path) -> BTreeSet<String> {
    std::fs::read_dir(home)
        .map(|entries| entries.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect())
        .unwrap_or_default()
}

/// What changed under `~/.local/bin` and directly under the home: names under `~/.local/bin`, only a
/// count for the home (a file's name there could be anything).
pub fn home_changes(
    before_bin: &BTreeMap<String, String>,
    after_bin: &BTreeMap<String, String>,
    before_home: &BTreeSet<String>,
    after_home: &BTreeSet<String>,
) -> Vec<String> {
    let mut changes = Vec::new();
    for (name, value) in after_bin {
        match before_bin.get(name) {
            None => changes.push(format!("~/.local/bin/{name} added")),
            Some(old) if old != value => changes.push(format!("~/.local/bin/{name} changed")),
            _ => {}
        }
    }
    for name in before_bin.keys().filter(|n| !after_bin.contains_key(*n)) {
        changes.push(format!("~/.local/bin/{name} removed"));
    }
    let new_home = after_home.difference(before_home).count();
    if new_home > 0 {
        changes.push(format!("{new_home} new entr{} directly under the home", if new_home == 1 { "y" } else { "ies" }));
    }
    changes
}

/// Commits in `git log --format=%H%x1f%an <%ae>%x1f%(trailers:only,unfold)%x1e` output that break
/// the owner's rules.
pub fn commit_problems(log: &str, repo: &str, author: Option<&str>, trailers: &[String], seen: &mut BTreeSet<String>) -> Vec<CommitProblem> {
    let mut problems = Vec::new();
    for record in log.split('\x1e') {
        let mut fields = record.trim_start_matches('\n').split('\x1f');
        let (Some(hash), Some(who), trailer_text) = (fields.next(), fields.next(), fields.next().unwrap_or_default()) else { continue };
        if hash.len() < 7 || !seen.insert(hash.to_string()) {
            continue;
        }
        let short = hash[..7].to_string();
        if let Some(author) = author
            && who != author
        {
            problems.push(CommitProblem { repo: repo.to_string(), short: short.clone(), problem: "has an author other than the configured one".into() });
        }
        let lower = trailer_text.to_ascii_lowercase();
        if trailers.iter().any(|t| lower.lines().any(|line| line.trim_start().starts_with(&t.to_ascii_lowercase()))) {
            problems.push(CommitProblem { repo: repo.to_string(), short, problem: "carries a forbidden trailer".into() });
        }
    }
    problems
}

fn scan_commits(config: &Config, table: &Table, since: i64, probe: &dyn Probe, seen: &mut BTreeSet<String>) -> Vec<CommitProblem> {
    if config.commit_author.is_none() && config.forbidden_trailers.is_empty() {
        return Vec::new();
    }
    let mut dirs: BTreeSet<PathBuf> = config.repos.iter().cloned().collect();
    if dirs.is_empty() {
        dirs = table.agents.values().filter(|a| a.last_ts >= since - 3600).filter_map(|a| a.cwd.as_ref().map(PathBuf::from)).collect();
    }
    let mut repos: BTreeMap<String, PathBuf> = BTreeMap::new();
    for dir in dirs.iter().filter(|d| d.is_dir()).take(40) {
        if let Some(common) = probe.git(dir, &["rev-parse", "--path-format=absolute", "--git-common-dir"]) {
            repos.entry(common.trim().to_string()).or_insert_with(|| dir.clone());
        }
    }
    let mut problems = Vec::new();
    for (common, dir) in repos {
        let name = Path::new(&common)
            .parent()
            .and_then(Path::file_name)
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "a repository".into());
        let since_arg = format!("--since=@{}", since - 120);
        let format = "--format=%H%x1f%an <%ae>%x1f%(trailers:only,unfold)%x1e";
        if let Some(log) = probe.git(&dir, &["log", "--all", &since_arg, format]) {
            problems.extend(commit_problems(&log, &name, config.commit_author.as_deref(), &config.forbidden_trailers, seen));
        }
    }
    if seen.len() > 5000 {
        let keep: BTreeSet<String> = seen.iter().rev().take(2500).cloned().collect();
        *seen = keep;
    }
    problems
}

/// Peak resident memory of this process in kB, from `/proc/self/status`.
fn peak_rss_kb() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| s.lines().find(|l| l.starts_with("VmHWM:")).and_then(|l| l.split_whitespace().nth(1)?.parse().ok()))
        .unwrap_or(0)
}

/// One check: read, check, alert, summarise, save. Returns a one-line account.
pub fn check(config: &Config, zone: &Zone, now: i64, probe: &dyn Probe, dry_run: bool) -> Result<String, String> {
    let started = Instant::now();
    let state_path = config.state_dir.join("state.json");
    let loaded = load_state(&state_path);
    let first = loaded.is_none();
    let mut state = loaded.unwrap_or_default();
    let sources = agents::sources(&config.projects_dir);
    state.table.handoff_context = config.handoff_context;
    let mut lines = state.table.read_new(&sources, first.then_some(now - 24 * 3600));
    lines.sort_by_key(|(_, line)| line.ts);
    for (source, line) in &lines {
        state.table.ingest(source, line, zone);
    }
    state.table.settle(now);
    let bin = bin_snapshot(&config.home);
    let home = home_entries(&config.home);
    let since = if first { now } else { state.last_check };
    let mut env = Env { claude_alive: probe.claude_alive(), ..Env::default() };
    if !first {
        env.home_changes = home_changes(&state.bin_snapshot, &bin, &state.home_entries, &home);
        let mut seen = std::mem::take(&mut state.seen_commits);
        env.commits = scan_commits(config, &state.table, since, probe, &mut seen);
        state.seen_commits = seen;
    }
    state.bin_snapshot = bin;
    state.home_entries = home;
    let findings = checks::run(&state.table, config, now, &env);
    let local_hour = chrono::Timelike::hour(&(zone.to_local)(now));
    let quiet = config.is_quiet(local_hour);
    let outbox = state.alerts.step(&findings, now, quiet, config.max_alerts_per_hour, first);
    let mut sent = 0;
    if !dry_run && let Some(command) = &config.notify_command {
        for text in &outbox {
            if probe.send(command, &format!("monitor: {text}")) {
                sent += 1;
            } else {
                state.alerts.unsend(text);
            }
        }
    }
    let (md, json) = summary::render(&state.table, &findings, &state.alerts, now);
    state.last_check = now;
    let account = format!(
        "{} lines read, {} agents, {} findings, {} alerts due, {} sent, {} ms, peak {} MB",
        lines.len(),
        state.table.agents.len(),
        findings.len(),
        outbox.len(),
        sent,
        started.elapsed().as_millis(),
        peak_rss_kb() / 1024
    );
    if !dry_run {
        summary::write_atomic(&config.state_dir.join("summary.md"), &md).map_err(|e| e.to_string())?;
        summary::write_atomic(&config.state_dir.join("summary.json"), &json.to_string()).map_err(|e| e.to_string())?;
        let saved = serde_json::to_string(&state).map_err(|e| e.to_string())?;
        summary::write_atomic(&state_path, &saved).map_err(|e| e.to_string())?;
        append_log(&config.state_dir.join("checks.log"), &format!("{} {account}\n", checks::utc_time(now, 0)));
    }
    Ok(account)
}

/// Appends one line, keeping the log under about 1 MB.
fn append_log(path: &Path, line: &str) {
    use std::io::Write;
    if std::fs::metadata(path).is_ok_and(|m| m.len() > 1_000_000) {
        let _ = std::fs::rename(path, path.with_extension("log.1"));
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = file.write_all(line.as_bytes());
    }
}

fn doctor(config: &Config, now: i64) -> i32 {
    let sources = agents::sources(&config.projects_dir);
    let mut table = Table::default();
    let lines = table.read_new(&sources, Some(now - 24 * 3600));
    let counts = &table.counts;
    println!("transcripts:     {} ({} files, {} lines of the last day read)", config.projects_dir.display(), sources.len(), lines.len());
    println!("state, summary:  {}", config.state_dir.display());
    println!("notify command:  {}", config.notify_command.as_deref().unwrap_or("(none: alerts reach the summary only)"));
    println!("Claude Code:     {}", counts.versions.iter().cloned().collect::<Vec<_>>().join(", "));
    println!(
        "lines:           {} used, {} skipped on purpose, {} of unknown types, {} unreadable",
        counts.used, counts.skipped, counts.unknown_type, counts.unclassed
    );
    if !counts.unknown_types.is_empty() {
        println!("unknown types:   {}", counts.unknown_types.iter().cloned().collect::<Vec<_>>().join(", "));
    }
    println!("rules (C5a):     {}", if config.commit_author.is_some() || !config.forbidden_trailers.is_empty() { "set" } else { "none set" });
    0
}

/// What a replay over history found: the gate's evidence for M0 (§10).
#[derive(Debug, Default, Serialize)]
pub struct Replay {
    pub lines: usize,
    pub agents: usize,
    pub limit_events: usize,
    pub limits: Vec<String>,
    pub push_refusals: usize,
    pub agents_over_handoff: usize,
    /// The agents that passed the hand-off threshold, by key (session and agent ids only).
    pub over_handoff: Vec<String>,
    /// Alerts the replay would have sent, by local day and kind.
    pub alerts_by_day: BTreeMap<String, BTreeMap<String, usize>>,
    pub limit_alerts: Vec<String>,
    /// Every alert the replay would have sent, with its time: fixed templates, no transcript text.
    pub alerts: Vec<String>,
}

/// Runs the checks every 5 minutes of history from `since` to `until`, sending nothing.
pub fn replay(config: &Config, zone: &Zone, since: i64, until: i64) -> Replay {
    let sources = agents::sources(&config.projects_dir);
    let mut table = Table { handoff_context: config.handoff_context, ..Table::default() };
    let mut lines: Vec<_> = table
        .read_new(&sources, Some(since))
        .into_iter()
        .filter(|(_, line)| line.ts.is_some_and(|ts| ts >= since && ts < until))
        .collect();
    lines.sort_by_key(|(_, line)| line.ts);
    let mut report = Replay { lines: lines.len(), ..Replay::default() };
    let mut alerts = Alerts::default();
    let mut over: BTreeSet<String> = BTreeSet::new();
    let mut seen_agents: BTreeSet<String> = BTreeSet::new();
    let mut limit_keys: BTreeSet<String> = BTreeSet::new();
    let mut next = 0;
    let env = Env { claude_alive: true, ..Env::default() };
    let mut tick = since - since.rem_euclid(300) + 300;
    while tick <= until && next < lines.len() + 1 {
        let before_events = table.events.len();
        while next < lines.len() && lines[next].1.ts.unwrap_or(0) <= tick {
            let (source, line) = &lines[next];
            seen_agents.insert(source.key.clone());
            table.ingest(source, line, zone);
            next += 1;
        }
        for event in &table.events[before_events..] {
            match &event.kind {
                agents::EventKind::Limit(limit) => {
                    report.limit_events += 1;
                    if limit_keys.insert(limit.key()) {
                        report.limits.push(format!(
                            "{} {} reset {}",
                            checks::utc_time(event.ts, 0),
                            limit.kind,
                            limit.reset.map(|r| checks::utc_time(r, 0)).unwrap_or_else(|| format!("as written: {}", limit.reset_text))
                        ));
                    }
                }
                agents::EventKind::PushRefused => report.push_refusals += 1,
                _ => {}
            }
        }
        table.settle(tick);
        for agent in table.agents.values() {
            if agent.peak_context >= config.handoff_context {
                over.insert(agent.key.clone());
            }
        }
        let findings = checks::run(&table, config, tick, &env);
        let local = (zone.to_local)(tick);
        let quiet = config.is_quiet(chrono::Timelike::hour(&local));
        let sent = alerts.step(&findings, tick, quiet, config.max_alerts_per_hour, false);
        let day = local.format("%Y-%m-%d").to_string();
        for text in sent {
            let kind = findings
                .iter()
                .find(|f| f.text == text)
                .map(|f| f.kind.to_string())
                .unwrap_or_else(|| "overflow".to_string());
            if kind == "usage-limit" || kind == "limit-lifted" {
                report.limit_alerts.push(format!("{} {text}", checks::utc_time(tick, 0)));
            }
            report.alerts.push(format!("{} {text}", checks::utc_time(tick, 0)));
            *report.alerts_by_day.entry(day.clone()).or_default().entry(kind).or_default() += 1;
        }
        if next >= lines.len() && tick >= until {
            break;
        }
        tick += 300;
    }
    report.agents = seen_agents.len();
    report.agents_over_handoff = over.len();
    report.over_handoff = over.into_iter().collect();
    report
}

fn replay_cli(config: &Config, args: &[String]) -> i32 {
    let date = |flag: &str| {
        args.iter()
            .position(|a| a == flag)
            .and_then(|i| args.get(i + 1))
            .and_then(|d| chrono::NaiveDate::parse_from_str(d, "%Y-%m-%d").ok())
            .map(|d| d.and_hms_opt(0, 0, 0).unwrap_or_default().and_utc().timestamp())
    };
    let Some(since) = date("--since") else {
        eprintln!("ling monitor replay: --since YYYY-MM-DD is required");
        return 2;
    };
    let until = date("--until").unwrap_or_else(|| chrono::Utc::now().timestamp());
    let report = replay(config, &Zone::system(), since, until);
    println!("{}", serde_json::to_string_pretty(&report).unwrap_or_default());
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    struct FakeProbe {
        sent: RefCell<Vec<String>>,
        log: String,
    }

    impl Probe for FakeProbe {
        fn claude_alive(&self) -> bool {
            true
        }
        fn send(&self, _command: &str, text: &str) -> bool {
            self.sent.borrow_mut().push(text.to_string());
            true
        }
        fn git(&self, _dir: &Path, args: &[&str]) -> Option<String> {
            Some(if args[0] == "rev-parse" { "/home/u/repo/.git\n".to_string() } else { self.log.clone() })
        }
    }

    fn ts(text: &str) -> i64 {
        chrono::DateTime::parse_from_rfc3339(text).unwrap().timestamp()
    }

    fn write_limit(file: &Path, at: &str) {
        let mut text = std::fs::read_to_string(file).unwrap_or_default();
        text.push_str(&format!(
            r#"{{"type":"assistant","timestamp":"{at}","cwd":"/home/u","error":"rate_limit","isApiErrorMessage":true,"message":{{"id":"e-{at}","model":"<synthetic>","content":[{{"type":"text","text":"You've hit your session limit · resets 3pm (Europe/Lisbon)"}}]}}}}"#
        ));
        text.push('\n');
        std::fs::write(file, text).unwrap();
    }

    #[test]
    fn a_check_reads_only_what_was_appended_and_alerts_a_new_limit_once() {
        let root = std::env::temp_dir().join(format!("ling-monitor-check-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let home = root.join("home");
        let project = home.join(".claude/projects/p");
        std::fs::create_dir_all(&project).unwrap();
        let transcript = project.join("sess.jsonl");
        let inputs_before = std::fs::read_dir(&project).unwrap().count();
        let mut config = Config::defaults(&home);
        config.notify_command = Some("/bin/true".into());
        let zone = Zone::fixed("Europe/Lisbon", 0);
        let probe = FakeProbe { sent: RefCell::new(Vec::new()), log: String::new() };
        write_limit(&transcript, "2026-10-27T10:00:00Z");
        // The first check is a baseline: it sends nothing.
        check(&config, &zone, ts("2026-10-27T10:01:00Z"), &probe, false).unwrap();
        assert!(probe.sent.borrow().is_empty());
        assert!(config.state_dir.join("summary.md").is_file());
        // Another agent stopped by the same limit and reset belongs to the same episode.
        write_limit(&transcript, "2026-10-27T10:03:00Z");
        let mtime = std::fs::metadata(&transcript).unwrap().modified().unwrap();
        let account = check(&config, &zone, ts("2026-10-27T10:05:00Z"), &probe, false).unwrap();
        assert!(account.starts_with("1 lines read"), "{account}");
        check(&config, &zone, ts("2026-10-27T10:10:00Z"), &probe, false).unwrap();
        assert!(probe.sent.borrow().is_empty());
        let md = std::fs::read_to_string(config.state_dir.join("summary.md")).unwrap();
        assert!(md.contains("usage limit (session) hit; 1 agent stopped; resets 15:00 UTC"), "{md}");
        // At the reset the lift is sent.
        check(&config, &zone, ts("2026-10-27T15:02:00Z"), &probe, false).unwrap();
        assert_eq!(*probe.sent.borrow(), vec!["monitor: usage limit (session) lifted at 15:00 UTC; 1 agent had stopped".to_string()]);
        // Nothing under the input folder was written or touched.
        assert_eq!(std::fs::read_dir(&project).unwrap().count(), inputs_before + 1);
        assert_eq!(std::fs::metadata(&transcript).unwrap().modified().unwrap(), mtime);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn commits_are_checked_once_against_the_configured_rules() {
        let mut seen = BTreeSet::new();
        let log = "aaaaaaa1111\x1fsomeone <someone@example.invalid>\x1f\x1e\nbbbbbbb2222\x1fother <o@example.invalid>\x1fMade-By: a tool\n\x1e";
        let rules = vec!["made-by:".to_string()];
        let found = commit_problems(log, "repo", Some("someone <someone@example.invalid>"), &rules, &mut seen);
        assert_eq!(found.len(), 2);
        assert_eq!((found[0].short.as_str(), found[1].problem.as_str()), ("bbbbbbb", "carries a forbidden trailer"));
        assert!(commit_problems(log, "repo", Some("x"), &rules, &mut seen).is_empty(), "already checked");
    }

    #[test]
    fn home_changes_name_bin_entries_and_only_count_home_entries() {
        let before: BTreeMap<String, String> = [("ling".to_string(), "-> /opt/ling".to_string())].into();
        let after: BTreeMap<String, String> = [("ling".to_string(), "-> /tmp/x/ling".to_string()), ("new".to_string(), "1".to_string())].into();
        let home_before: BTreeSet<String> = ["a".to_string()].into();
        let home_after: BTreeSet<String> = ["a".to_string(), "secret-name.txt".to_string()].into();
        let changes = home_changes(&before, &after, &home_before, &home_after);
        assert_eq!(changes, vec!["~/.local/bin/ling changed", "~/.local/bin/new added", "1 new entry directly under the home"]);
    }
}
