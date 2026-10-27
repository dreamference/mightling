//! The deterministic checks C1-C7 of §7.3, over the agent table at one moment. Each finding carries
//! a fixed template of facts (D13): the agent's ordinal, type and short id, counts and times; never
//! transcript text, a digest or a task description.

use std::collections::BTreeMap;
use std::collections::BTreeSet;

use chrono::TimeZone;
use serde::Serialize;

use crate::agents::Agent;
use crate::agents::Call;
use crate::agents::EventKind;
use crate::agents::Last;
use crate::agents::Pending;
use crate::agents::Table;
use crate::agents::WaitKind;
use crate::config::Config;

/// Where a finding goes in the summary (§7.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub enum Section {
    Awaiting,
    Stalled,
    Context,
    Limits,
    Rules,
    Monitor,
}

/// One thing that needs attention.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Finding {
    pub check: &'static str,
    /// The alert kind deduplication and rate limits go by.
    pub kind: &'static str,
    /// What the finding is about: an agent's key, a limit's key, a commit.
    pub subject: String,
    pub section: Section,
    /// The fixed template of facts, as the summary shows it and an alert sends it.
    pub text: String,
    /// Whether it is sent to the owner, or kept in the summary only.
    pub alert: bool,
    /// Sent during quiet hours too (usage limits, failed releases, failed nights).
    pub urgent: bool,
    /// Checks in a row the condition must hold before it is sent.
    pub consecutive: u32,
}

/// What a check learned outside the transcripts.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Env {
    /// Whether any Claude Code process is running; with none, nothing can be stalled.
    pub claude_alive: bool,
    /// C5(c): what changed under the real `~/.local/bin` or directly under the home since the last check.
    pub home_changes: Vec<String>,
    /// C5(a): commits since the last check that break the owner's rules.
    pub commits: Vec<CommitProblem>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommitProblem {
    pub repo: String,
    pub short: String,
    pub problem: String,
}

/// `14:05 UTC`, or with its date when it is not today's.
pub fn utc_time(ts: i64, now: i64) -> String {
    let at = chrono::Utc.timestamp_opt(ts, 0).single().unwrap_or_default();
    let today = chrono::Utc.timestamp_opt(now, 0).single().unwrap_or_default();
    if at.date_naive() == today.date_naive() {
        at.format("%H:%M UTC").to_string()
    } else {
        at.format("%b %-d %H:%M UTC").to_string()
    }
}

/// Labels for every agent: `coordinator 1a2b3c4d`, or `#7 (general-purpose, a1b2c3d)` for a
/// subagent, numbered by first appearance within its session.
pub fn labels(table: &Table) -> BTreeMap<String, String> {
    let mut by_session: BTreeMap<&str, Vec<&Agent>> = BTreeMap::new();
    let mut labels = BTreeMap::new();
    for agent in table.agents.values() {
        if agent.subagent {
            by_session.entry(agent.session.as_str()).or_default().push(agent);
        } else {
            labels.insert(agent.key.clone(), format!("coordinator {}", short(&agent.session, 8)));
        }
    }
    for agents in by_session.values_mut() {
        agents.sort_by(|a, b| (a.first_ts, &a.key).cmp(&(b.first_ts, &b.key)));
        for (index, agent) in agents.iter().enumerate() {
            let id = agent.key.rsplit('/').next().unwrap_or_default();
            labels.insert(agent.key.clone(), format!("#{} ({}, {})", index + 1, agent.agent_type, short(id, 7)));
        }
    }
    labels
}

fn short(text: &str, n: usize) -> &str {
    &text[..text.len().min(n)]
}

/// Tools that put a question to the owner and wait for the answer.
const ASKS_THE_OWNER: &[&str] = &["AskUserQuestion", "ExitPlanMode"];

/// When a pending call's wait is overdue: 1.5 times its expected duration after the last line, or
/// for a night window half an hour after the window's end.
fn overdue_at(pending: &Pending, last_ts: i64, config: &Config) -> i64 {
    let minutes = match pending.wait {
        WaitKind::Ci => config.waits.ci,
        WaitKind::Shell => config.waits.shell,
        WaitKind::Subagent => config.waits.subagent,
        WaitKind::Tool => config.waits.tool,
        WaitKind::Night => {
            let since = chrono::Utc.timestamp_opt(pending.since, 0).single().unwrap_or_default();
            let end = since.date_naive().and_hms_opt(config.waits.night_end_hour_utc, 0, 0).unwrap_or_default().and_utc();
            let end = if end <= since { end + chrono::Duration::days(1) } else { end };
            return end.timestamp().max(last_ts) + 1800;
        }
    };
    last_ts + minutes * 60 * 3 / 2
}

/// Every finding at `now`.
pub fn run(table: &Table, config: &Config, now: i64, env: &Env) -> Vec<Finding> {
    let labels = labels(table);
    let label = |key: &str| labels.get(key).cloned().unwrap_or_else(|| "an agent".to_string());
    let mut findings = Vec::new();
    for agent in table.agents.values() {
        let name = label(&agent.key);
        let idle_min = (now - agent.last_ts) / 60;
        let recent = now - agent.last_ts <= 6 * 3600;
        // C1: context size.
        if agent.context >= config.warn_context && now - agent.last_ts <= 2 * 3600 && !finished(agent) {
            let due = agent.over_handoff_since.filter(|_| agent.context >= config.handoff_context);
            let text = match due {
                Some(since) => format!("{name} at {}K context; hand-off due for {} min", agent.context / 1000, (now - since).max(0) / 60),
                None => format!("{name} at {}K context; hand-off due at {}K", agent.context / 1000, config.handoff_context / 1000),
            };
            findings.push(Finding {
                check: "C1",
                kind: "handoff",
                subject: agent.key.clone(),
                section: Section::Context,
                // Summary only: a subagent's hand-off is the coordinator's to act on, and the
                // coordinator compacts by itself, so no owner can act on an alert about it.
                alert: false,
                urgent: false,
                consecutive: 1,
                text,
            });
        }
        // C2: idle and stall against the stated wait.
        if recent && env.claude_alive && !matches!(agent.last, Last::Limit(_)) {
            if let Some(oldest) = agent.pending.values().min_by_key(|p| p.since) {
                if ASKS_THE_OWNER.contains(&oldest.tool.as_str()) {
                    // A question put to the owner through a tool is C6, not a stall.
                    if now - oldest.since >= 30 * 60 {
                        findings.push(Finding {
                            check: "C6",
                            kind: "awaiting",
                            subject: agent.key.clone(),
                            section: Section::Awaiting,
                            text: format!("{name} has waited {} min for an answer to its question", (now - oldest.since) / 60),
                            alert: !agent.subagent,
                            urgent: false,
                            consecutive: 1,
                        });
                    }
                } else if now > overdue_at(oldest, agent.last_ts, config) {
                    findings.push(Finding {
                        check: "C2",
                        kind: "stall",
                        subject: agent.key.clone(),
                        section: Section::Stalled,
                        text: match oldest.wait {
                            WaitKind::Tool => format!("{name} stalled {idle_min} min waiting on a {} call", oldest.tool),
                            wait => format!("{name} stalled {idle_min} min in a {} wait", wait.label()),
                        },
                        alert: !agent.subagent,
                        urgent: false,
                        consecutive: 1,
                    });
                }
            } else if matches!(agent.last, Last::Result | Last::User) && now - agent.last_ts >= 20 * 60 {
                findings.push(Finding {
                    check: "C2",
                    kind: "stall",
                    subject: agent.key.clone(),
                    section: Section::Stalled,
                    text: format!("{name} has not answered for {idle_min} min with no call pending"),
                    alert: !agent.subagent,
                    urgent: false,
                    consecutive: 1,
                });
            }
        }
        // C4: repeated failures and loops, on agents still at work.
        if now - agent.last_ts <= 600 {
            if agent.error_streak >= 3 {
                findings.push(loop_finding(agent, &name, format!("{name}: {} failed {} times in a row", agent.error_tool, agent.error_streak)));
            }
            let since_edit: Vec<&Call> = agent.recent.iter().rev().take_while(|c| **c != Call::Edit).collect();
            let mut commands: BTreeMap<(u64, bool), usize> = BTreeMap::new();
            let mut reads: BTreeMap<u64, usize> = BTreeMap::new();
            for call in &since_edit {
                match call {
                    Call::Command { hash, wait } => *commands.entry((*hash, *wait)).or_default() += 1,
                    Call::Read { hash } => *reads.entry(*hash).or_default() += 1,
                    _ => {}
                }
            }
            if let Some(((_, wait), count)) = commands.iter().max_by_key(|(_, n)| **n).filter(|(_, n)| **n >= 5) {
                let text = format!("{name}: the same command {count} times in its last {} calls", since_edit.len());
                if *wait {
                    findings.push(Finding {
                        check: "C4",
                        kind: "polling",
                        subject: agent.key.clone(),
                        section: Section::Stalled,
                        text: format!("{text} (polling a wait)"),
                        alert: false,
                        urgent: false,
                        consecutive: 1,
                    });
                } else {
                    findings.push(loop_finding(agent, &name, text));
                }
            }
            if let Some((_, count)) = reads.iter().max_by_key(|(_, n)| **n).filter(|(_, n)| **n >= 4) {
                findings.push(loop_finding(agent, &name, format!("{name}: the same file read {count} times in its last {} calls", since_edit.len())));
            }
        }
        // C6: reported and awaiting an answer.
        if let Last::Text { question } = agent.last {
            if !agent.subagent && question && now - agent.last_ts >= 30 * 60 {
                findings.push(Finding {
                    check: "C6",
                    kind: "awaiting",
                    subject: agent.key.clone(),
                    section: Section::Awaiting,
                    text: format!("{name} has waited {idle_min} min for an answer to its question"),
                    alert: true,
                    urgent: false,
                    consecutive: 1,
                });
            } else if agent.subagent && now - agent.last_ts >= 10 * 60 && recent {
                let coordinator = table.agents.get(&agent.session);
                if coordinator.is_some_and(|c| c.last_response_ts < agent.last_ts) {
                    findings.push(Finding {
                        check: "C6",
                        kind: "awaiting",
                        subject: agent.key.clone(),
                        section: Section::Awaiting,
                        text: format!("{name} reported {idle_min} min ago; the coordinator has not answered since"),
                        alert: false,
                        urgent: false,
                        consecutive: 1,
                    });
                }
            }
        }
    }
    findings.extend(limit_findings(table, now, &label));
    findings.extend(rule_findings(table, now, env, &label));
    findings.extend(release_findings(table, now, &label));
    let counts = &table.counts;
    let read = counts.used + counts.unclassed;
    if read >= 100 && counts.unclassed * 20 > read {
        let version = counts.versions.iter().last().cloned().unwrap_or_else(|| "unknown".to_string());
        findings.push(Finding {
            check: "C0",
            kind: "format-drift",
            subject: version.clone(),
            section: Section::Monitor,
            text: format!("format drift: {}% of transcript lines unreadable (Claude Code {version})", counts.unclassed * 100 / read),
            alert: true,
            urgent: false,
            consecutive: 1,
        });
    }
    for path in &counts.unreadable {
        let name = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        findings.push(Finding {
            check: "C0",
            kind: "unreadable",
            subject: path.clone(),
            section: Section::Monitor,
            text: format!("transcript {name} could not be read"),
            alert: false,
            urgent: false,
            consecutive: 1,
        });
    }
    findings
}

fn finished(agent: &Agent) -> bool {
    agent.subagent && matches!(agent.last, Last::Text { .. }) && agent.pending.is_empty()
}

fn loop_finding(agent: &Agent, _name: &str, text: String) -> Finding {
    Finding {
        check: "C4",
        kind: "loop",
        subject: agent.key.clone(),
        section: Section::Stalled,
        text,
        alert: true,
        urgent: false,
        consecutive: 2,
    }
}

/// C3: one finding per limit and reset, then a "lifted" note at the reset time.
fn limit_findings(table: &Table, now: i64, label: &dyn Fn(&str) -> String) -> Vec<Finding> {
    let mut by_key: BTreeMap<String, (crate::limits::Limit, i64, BTreeSet<String>)> = BTreeMap::new();
    for event in &table.events {
        if let EventKind::Limit(limit) = &event.kind {
            let entry = by_key.entry(limit.key()).or_insert_with(|| (limit.clone(), event.ts, BTreeSet::new()));
            entry.1 = entry.1.max(event.ts);
            entry.2.insert(event.agent.clone());
        }
    }
    let mut findings = Vec::new();
    for (key, (limit, last_seen, agents)) in by_key {
        let stopped = table.agents.values().filter(|a| a.last == Last::Limit(key.clone())).count();
        let n = if stopped > 0 { stopped } else { agents.len() };
        let reset = match (limit.reset, &limit.reset_of) {
            (Some(reset), Some(of)) => format!("the {of} limit resets {}", utc_time(reset, now)),
            (Some(reset), None) => format!("resets {}", utc_time(reset, now)),
            (None, _) if limit.reset_text.is_empty() => "no reset time given".to_string(),
            (None, _) => format!("resets {}", limit.reset_text),
        };
        let lifts_at = if limit.reset_of.is_none() { limit.reset } else { None };
        let open = match lifts_at {
            Some(reset) => now < reset,
            None => now - last_seen < 6 * 3600,
        };
        if open {
            findings.push(Finding {
                check: "C3",
                kind: "usage-limit",
                subject: key.clone(),
                section: Section::Limits,
                text: format!("usage limit ({}) hit; {n} agent{} stopped; {reset}", limit.kind, if n == 1 { "" } else { "s" }),
                alert: true,
                urgent: true,
                consecutive: 1,
            });
        } else if let Some(reset) = lifts_at.filter(|reset| now - reset < 3600) {
            findings.push(Finding {
                check: "C3",
                kind: "limit-lifted",
                subject: key.clone(),
                section: Section::Limits,
                text: format!("usage limit ({}) lifted at {}; {n} agent{} had stopped", limit.kind, utc_time(reset, now), if n == 1 { "" } else { "s" }),
                alert: true,
                urgent: true,
                consecutive: 1,
            });
        }
        let _ = label;
    }
    findings
}

/// C5: commit identity and trailers, pre-push refusals, tests reaching for the real home.
fn rule_findings(table: &Table, now: i64, env: &Env, label: &dyn Fn(&str) -> String) -> Vec<Finding> {
    let mut findings = Vec::new();
    for commit in &env.commits {
        findings.push(Finding {
            check: "C5",
            kind: "rule",
            subject: format!("commit {} {}", commit.repo, commit.short),
            section: Section::Rules,
            text: format!("commit {} in {} {}", commit.short, commit.repo, commit.problem),
            alert: true,
            urgent: false,
            consecutive: 1,
        });
    }
    let mut refusals: BTreeMap<&str, Vec<i64>> = BTreeMap::new();
    let mut guards: BTreeMap<&str, usize> = BTreeMap::new();
    let mut tests = false;
    let mut installs = false;
    for event in &table.events {
        match event.kind {
            EventKind::PushRefused if now - event.ts <= 24 * 3600 => refusals.entry(&event.agent).or_default().push(event.ts),
            EventKind::HomeGuard if now - event.ts <= 900 => *guards.entry(&event.agent).or_default() += 1,
            EventKind::TestRun if now - event.ts <= 600 => tests = true,
            EventKind::Install if now - event.ts <= 600 => installs = true,
            _ => {}
        }
    }
    for (agent, times) in refusals {
        let in_two_hours = times.iter().filter(|t| now - **t <= 2 * 3600).count();
        findings.push(Finding {
            check: "C5",
            kind: "push-refused",
            subject: agent.to_string(),
            section: Section::Rules,
            text: format!("{}: push refused by the pre-push hook {} time{} today", label(agent), times.len(), if times.len() == 1 { "" } else { "s" }),
            alert: in_two_hours >= 2,
            urgent: false,
            consecutive: 1,
        });
    }
    for (agent, count) in guards {
        findings.push(Finding {
            check: "C5",
            kind: "home-guard",
            subject: agent.to_string(),
            section: Section::Rules,
            text: format!("{}: the test suite's guard stopped {count} test{} reaching for the real machine", label(agent), if count == 1 { "" } else { "s" }),
            alert: true,
            urgent: false,
            consecutive: 1,
        });
    }
    if !env.home_changes.is_empty() && tests && !installs {
        findings.push(Finding {
            check: "C5",
            kind: "home-write",
            subject: env.home_changes.join(", "),
            section: Section::Rules,
            text: format!("the real home changed during a test run: {}", env.home_changes.join(", ")),
            alert: true,
            urgent: false,
            consecutive: 1,
        });
    }
    findings
}

/// A release workflow seen finishing in a `gh run` result (§0.1, question 3: from the transcripts).
fn release_findings(table: &Table, now: i64, label: &dyn Fn(&str) -> String) -> Vec<Finding> {
    let mut findings = Vec::new();
    for event in &table.events {
        if let EventKind::Release { succeeded } = event.kind
            && now - event.ts <= 900
        {
            findings.push(Finding {
                check: "C5",
                kind: if succeeded { "release-published" } else { "release-failed" },
                subject: format!("{}@{}", event.agent, event.ts),
                section: Section::Rules,
                text: format!(
                    "release workflow {} ({}, {})",
                    if succeeded { "succeeded" } else { "failed" },
                    label(&event.agent),
                    utc_time(event.ts, now)
                ),
                alert: true,
                urgent: !succeeded,
                consecutive: 1,
            });
        }
    }
    findings
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::Event;
    use crate::limits::Limit;
    use std::path::Path;

    fn ts(text: &str) -> i64 {
        chrono::DateTime::parse_from_rfc3339(text).unwrap().timestamp()
    }

    fn agent(key: &str, subagent: bool, last_ts: i64) -> Agent {
        Agent {
            key: key.to_string(),
            session: key.split('/').next().unwrap().to_string(),
            subagent,
            agent_type: if subagent { "general-purpose".into() } else { "coordinator".into() },
            description: "a task description that must never reach an alert".into(),
            first_ts: last_ts - 600,
            last_ts,
            last_response_ts: last_ts,
            ..Agent::default()
        }
    }

    fn env() -> Env {
        Env { claude_alive: true, ..Env::default() }
    }

    #[test]
    fn a_session_limit_alerts_once_per_reset_and_then_lifts() {
        let now = ts("2026-10-27T00:30:00Z");
        let mut table = Table::default();
        let limit = Limit { kind: "session".into(), reset: Some(ts("2026-10-27T01:00:00Z")), reset_text: "1am (Europe/Lisbon)".into(), reset_of: None, seen_day: String::new() };
        for key in ["s", "s/a1", "s/a2"] {
            let mut a = agent(key, key.contains('/'), now - 60);
            a.last = Last::Limit(limit.key());
            table.agents.insert(key.into(), a);
            table.events.push(Event { ts: now - 60, agent: key.into(), kind: EventKind::Limit(limit.clone()) });
        }
        let config = Config::defaults(Path::new("/home/u"));
        let found: Vec<_> = run(&table, &config, now, &env()).into_iter().filter(|f| f.check == "C3").collect();
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].text, "usage limit (session) hit; 3 agents stopped; resets 01:00 UTC");
        assert!(found[0].urgent && found[0].alert);
        let later = run(&table, &config, ts("2026-10-27T01:05:00Z"), &env());
        let lifted: Vec<_> = later.iter().filter(|f| f.check == "C3").collect();
        assert_eq!(lifted[0].kind, "limit-lifted");
        assert!(run(&table, &config, ts("2026-10-27T02:30:00Z"), &env()).iter().all(|f| f.check != "C3"));
    }

    #[test]
    fn a_coordinator_stalled_in_a_ci_wait_alerts_and_a_subagent_only_shows() {
        let now = ts("2026-10-26T12:00:00Z");
        let mut table = Table::default();
        let mut coordinator = agent("s", false, now - 50 * 60);
        coordinator.pending.insert("t1".into(), Pending { tool: "Bash".into(), wait: WaitKind::Ci, since: now - 50 * 60, test: false, gh_run: true, push: false });
        table.agents.insert("s".into(), coordinator);
        let mut sub = agent("s/a1", true, now - 25 * 60);
        sub.last = Last::Result;
        table.agents.insert("s/a1".into(), sub);
        let config = Config::defaults(Path::new("/home/u"));
        let found: Vec<_> = run(&table, &config, now, &env()).into_iter().filter(|f| f.check == "C2").collect();
        assert_eq!(found.len(), 2);
        let alerting: Vec<_> = found.iter().filter(|f| f.alert).collect();
        assert_eq!(alerting.len(), 1);
        assert_eq!(alerting[0].text, "coordinator s stalled 50 min in a CI wait");
        // Inside the expected wait: nothing.
        assert!(run(&table, &config, now - 10 * 60, &env()).iter().all(|f| f.check != "C2" || f.subject != "s"));
        // With no Claude Code process running nothing is stalled.
        assert!(run(&table, &config, now, &Env::default()).iter().all(|f| f.check != "C2"));
    }

    #[test]
    fn no_finding_carries_the_task_description() {
        let now = ts("2026-10-26T12:00:00Z");
        let mut table = Table::default();
        let mut a = agent("s/a1", true, now - 30);
        a.context = 320_000;
        a.over_handoff_since = Some(now - 7200);
        a.error_streak = 4;
        a.error_tool = "Bash".into();
        a.last = Last::Tool;
        a.pending.insert("t".into(), Pending { tool: "Bash".into(), wait: WaitKind::Tool, since: now - 30, test: false, gh_run: false, push: false });
        for _ in 0..6 {
            a.recent.push_back(Call::Command { hash: 7, wait: false });
        }
        table.agents.insert("s/a1".into(), a);
        table.agents.insert("s".into(), agent("s", false, now - 30));
        let config = Config::defaults(Path::new("/home/u"));
        let found = run(&table, &config, now, &env());
        let kinds: BTreeSet<_> = found.iter().map(|f| f.kind).collect();
        assert!(kinds.contains("handoff") && kinds.contains("loop"), "{kinds:?}");
        assert!(found.iter().any(|f| f.text == "#1 (general-purpose, a1) at 320K context; hand-off due for 120 min" && !f.alert));
        assert!(found.iter().all(|f| !f.text.contains("description")));
    }

    #[test]
    fn a_coordinator_question_waits_half_an_hour_before_it_alerts() {
        let now = ts("2026-10-26T12:00:00Z");
        let mut table = Table::default();
        let mut c = agent("s", false, now - 31 * 60);
        c.last = Last::Text { question: true };
        table.agents.insert("s".into(), c);
        let config = Config::defaults(Path::new("/home/u"));
        let found: Vec<_> = run(&table, &config, now, &env()).into_iter().filter(|f| f.check == "C6").collect();
        assert_eq!(found[0].text, "coordinator s has waited 31 min for an answer to its question");
    }

    #[test]
    fn rule_findings_need_a_test_run_for_a_home_change() {
        let now = ts("2026-10-26T12:00:00Z");
        let mut table = Table::default();
        let config = Config::defaults(Path::new("/home/u"));
        let changed = Env { claude_alive: true, home_changes: vec!["~/.local/bin/ling removed".into()], commits: vec![] };
        assert!(run(&table, &config, now, &changed).iter().all(|f| f.kind != "home-write"));
        table.events.push(Event { ts: now - 120, agent: "s".into(), kind: EventKind::TestRun });
        assert!(run(&table, &config, now, &changed).iter().any(|f| f.kind == "home-write" && f.alert));
        table.events.push(Event { ts: now - 100, agent: "s".into(), kind: EventKind::Install });
        assert!(run(&table, &config, now, &changed).iter().all(|f| f.kind != "home-write"), "an install explains it");
        table.events.push(Event { ts: now - 100, agent: "s".into(), kind: EventKind::PushRefused });
        let once = run(&table, &config, now, &env());
        assert!(once.iter().any(|f| f.kind == "push-refused" && !f.alert));
        table.events.push(Event { ts: now - 50, agent: "s".into(), kind: EventKind::PushRefused });
        assert!(run(&table, &config, now, &env()).iter().any(|f| f.kind == "push-refused" && f.alert));
    }
}
