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
