//! The summary the coordinator reads at a check-in (§7.5): `summary.md` and `summary.json`,
//! rewritten whole at every check through a temporary file and a rename, about 1,500 tokens at most.

use std::path::Path;

use serde_json::json;

use crate::alerts::Alerts;
use crate::checks;
use crate::checks::Finding;
use crate::checks::Section;
use crate::agents::Table;

/// About 1,500 tokens.
pub const MAX_CHARS: usize = 6000;

fn heading(section: Section) -> &'static str {
    match section {
        Section::Awaiting => "Waiting for an answer (C6)",
        Section::Stalled => "Stalled or looping (C2, C4)",
        Section::Context => "Over the context thresholds (C1)",
        Section::Limits => "Usage limits (C3)",
        Section::Rules => "Rules and releases (C5)",
        Section::Monitor => "The monitor itself",
    }
}

/// The last hour's figures (C7) and practice 2's count of agents active in the last 10 minutes.
pub struct Hour {
    pub active_10_min: usize,
    pub responses: u64,
    pub weighted: f64,
    pub idle_wake_weighted: f64,
    pub top: Vec<(String, f64, u64)>,
}

pub fn last_hour(table: &Table, now: i64) -> Hour {
    let labels = checks::labels(table);
    let mut hour = Hour { active_10_min: 0, responses: 0, weighted: 0.0, idle_wake_weighted: 0.0, top: Vec::new() };
    for agent in table.agents.values() {
        if now - agent.last_ts <= 600 {
            hour.active_10_min += 1;
        }
        let tokens = agent.tokens_since(now - 3600);
        if tokens.responses == 0 {
            continue;
        }
        hour.responses += tokens.responses;
        hour.weighted += tokens.weighted;
        hour.idle_wake_weighted += tokens.idle_wake_weighted;
        hour.top.push((labels.get(&agent.key).cloned().unwrap_or_default(), tokens.weighted, tokens.idle_wakes));
    }
    hour.top.sort_by(|a, b| b.1.total_cmp(&a.1));
    hour.top.truncate(5);
    hour
}

fn kilo(value: f64) -> String {
    if value >= 1e6 { format!("{:.1}M", value / 1e6) } else { format!("{:.0}K", value / 1e3) }
}

/// Renders the summary. Task descriptions appear only here, beside the agents they belong to, on
/// this machine; never in an alert.
pub fn render(table: &Table, findings: &[Finding], alerts: &Alerts, now: i64) -> (String, serde_json::Value) {
    let labels = checks::labels(table);
    let mut md = format!("# Session monitor, {}\n\n", checks::utc_time(now, now));
    let open: Vec<_> = alerts.episodes.values().filter(|e| e.alert && (e.sent.is_some() || e.held)).collect();
    md.push_str("## Open alerts\n");
    if open.is_empty() {
        md.push_str("- none\n");
    }
    let mut open_sorted = open.clone();
    open_sorted.sort_by_key(|e| std::cmp::Reverse(e.opened));
    for episode in &open_sorted {
        let how = if episode.held { "held, not sent" } else { "sent" };
        md.push_str(&format!("- {} ({how}, since {})\n", episode.text, checks::utc_time(episode.opened, now)));
    }
    let mut sections: Vec<Section> = findings.iter().map(|f| f.section).collect();
    sections.sort();
    sections.dedup();
    for section in sections {
        md.push_str(&format!("\n## {}\n", heading(section)));
        for finding in findings.iter().filter(|f| f.section == section) {
            let task = table
                .agents
                .get(&finding.subject)
                .filter(|a| !a.description.is_empty())
                .map(|a| format!(" — task: {}", a.description.chars().take(70).collect::<String>()))
                .unwrap_or_default();
            md.push_str(&format!("- {}{task}\n", finding.text));
        }
    }
    let hour = last_hour(table, now);
    md.push_str(&format!(
        "\n## Tokens since {} (C7)\n- agents active in the last 10 min: {}\n- responses: {}; price-weighted tokens: {}; idle-wake rewrites: {}%\n",
        checks::utc_time(now - 3600 - (now - 3600).rem_euclid(3600), now),
        hour.active_10_min,
        hour.responses,
        kilo(hour.weighted),
        if hour.weighted > 0.0 { (100.0 * hour.idle_wake_weighted / hour.weighted).round() as i64 } else { 0 }
    ));
    for (label, weighted, wakes) in &hour.top {
        md.push_str(&format!("- {label}: {} ({wakes} idle wakes)\n", kilo(*weighted)));
    }
    if md.len() > MAX_CHARS {
        let mut cut = MAX_CHARS - 80;
        while !md.is_char_boundary(cut) {
            cut -= 1;
        }
        md.truncate(cut);
        md.push_str("\n… (cut at about 1,500 tokens; `ling monitor status --json` has everything)\n");
    }
    let json = json!({
        "generated": now,
        "open_alerts": open_sorted.iter().map(|e| json!({"kind": e.kind, "text": e.text, "since": e.opened, "sent": e.sent, "held": e.held})).collect::<Vec<_>>(),
        "findings": findings.iter().map(|f| json!({"check": f.check, "kind": f.kind, "section": f.section, "text": f.text, "alert": f.alert, "urgent": f.urgent})).collect::<Vec<_>>(),
        "agents": table.agents.values().filter(|a| now - a.last_ts <= 3600).map(|a| json!({
            "label": labels.get(&a.key),
            "type": a.agent_type,
            "task": a.description,
            "last": a.last_ts,
            "context": a.context,
            "pending": a.pending.len(),
            "state": format!("{:?}", a.last),
        })).collect::<Vec<_>>(),
        "last_hour": {"active_10_min": hour.active_10_min, "responses": hour.responses, "weighted": hour.weighted, "idle_wake_weighted": hour.idle_wake_weighted},
    });
    (md, json)
}

/// Writes a file through a temporary file and a rename, so a reader never sees half of it.
pub fn write_atomic(path: &Path, text: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, text)?;
    std::fs::rename(&tmp, path)
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::Agent;
    use crate::agents::Tokens;

    fn ts(text: &str) -> i64 {
        chrono::DateTime::parse_from_rfc3339(text)
            .unwrap()
            .timestamp()
    }

    fn finding(check: &'static str, kind: &'static str, section: Section, text: &str) -> Finding {
        Finding {
            check,
            kind,
            subject: kind.to_string(),
            section,
            text: text.to_string(),
            alert: false,
            urgent: false,
            consecutive: 1,
        }
    }

    #[test]
    fn the_summary_lists_its_sections_in_the_order_of_the_spec() {
        let now = ts("2026-10-26T12:00:00Z");
        let table = Table::default();
        let mut alerts = Alerts::default();
        let mut open = finding("C5", "stall", Section::Stalled, "an open alert");
        open.alert = true;
        alerts.step(&[open], now, false, 6, false);
        // Handed in out of order on purpose.
        let findings = vec![
            finding("C3", "usage-limit", Section::Limits, "limits text"),
            finding("C1", "handoff", Section::Context, "context text"),
            finding("C2", "stall", Section::Stalled, "stalled text"),
            finding("C6", "awaiting", Section::Awaiting, "awaiting text"),
        ];
        let (md, json) = render(&table, &findings, &alerts, now);
        let at = |needle: &str| {
            md.find(needle)
                .unwrap_or_else(|| panic!("{needle} missing in {md}"))
        };
        let order = [
            "## Open alerts",
            "## Waiting for an answer",
            "## Stalled or looping",
            "## Over the context",
            "## Usage limits",
            "(C7)",
        ];
        let positions: Vec<usize> = order.iter().map(|n| at(n)).collect();
        assert!(positions.windows(2).all(|w| w[0] < w[1]), "{positions:?}");
        assert!(md.contains("an open alert") && json["open_alerts"].as_array().unwrap().len() == 1);
        assert_eq!(json["findings"].as_array().unwrap().len(), 4);
    }

    #[test]
    fn open_alerts_come_newest_first() {
        let table = Table::default();
        let mut alerts = Alerts::default();
        let mut first = finding("C2", "stall", Section::Stalled, "older alert");
        first.alert = true;
        let mut second = finding("C4", "loop", Section::Stalled, "newer alert");
        second.alert = true;
        alerts.step(&[first.clone()], 1000, false, 6, false);
        alerts.step(&[first, second], 1300, false, 6, false);
        let (md, _) = render(&table, &[], &alerts, 2000);
        assert!(
            md.find("newer alert").unwrap() < md.find("older alert").unwrap(),
            "{md}"
        );
    }

    #[test]
    fn the_last_hour_names_the_top_five_by_weighted_tokens() {
        let now = ts("2026-10-26T12:30:00Z");
        let hour = now - now.rem_euclid(3600);
        let mut table = Table::default();
        for i in 0..7u32 {
            let key = format!("s{i}");
            let mut a = Agent {
                key: key.clone(),
                session: key.clone(),
                last_ts: now - 60,
                ..Agent::default()
            };
            a.hours.insert(
                hour,
                Tokens {
                    responses: 1,
                    weighted: 1000.0 * (i + 1) as f64,
                    idle_wakes: u64::from(i == 6),
                    idle_wake_weighted: if i == 6 { 7000.0 } else { 0.0 },
                    ..Tokens::default()
                },
            );
            table.agents.insert(key, a);
        }
        let last = last_hour(&table, now);
        assert_eq!((last.active_10_min, last.responses), (7, 7));
        assert_eq!(last.top.len(), 5);
        assert_eq!(last.top[0].1, 7000.0);
        assert!(last.top.iter().all(|t| t.1 >= 3000.0));
        // The idle-wake rewrite share: 7000 of 28000 weighted.
        let (md, _) = render(&table, &[], &Alerts::default(), now);
        assert!(md.contains("idle-wake rewrites: 25%"), "{md}");
    }

    #[test]
    fn the_summary_is_capped_and_written_whole_through_a_rename() {
        let now = ts("2026-10-26T12:00:00Z");
        let table = Table::default();
        let findings: Vec<Finding> = (0..400)
            .map(|i| {
                finding(
                    "C2",
                    "stall",
                    Section::Stalled,
                    &format!("agent {i} stalled for a long time in a CI wait"),
                )
            })
            .collect();
        let (md, _) = render(&table, &findings, &Alerts::default(), now);
        assert!(md.len() <= MAX_CHARS, "{}", md.len());
        assert!(md.contains("cut at about 1,500 tokens"));
        let dir = std::env::temp_dir().join(format!("ling-monitor-summary-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let file = dir.join("nested/summary.md");
        write_atomic(&file, "first").unwrap();
        write_atomic(&file, "second").unwrap();
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "second");
        let names: Vec<_> = std::fs::read_dir(dir.join("nested"))
            .unwrap()
            .flatten()
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(
            names,
            vec!["summary.md".to_string()],
            "no temporary file is left"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
