//! What `puffin skill list` and `puffin skill show` print.

use std::path::Path;

use crate::budget::grouped;
use crate::catalog::AGENTS;
use crate::catalog::Entry;
use crate::catalog::Machine;
use crate::catalog::Origin;
use crate::catalog::Plan;
use crate::catalog::Status;
use crate::install;
use crate::install::Provenance;
use crate::settings::Settings;

/// `/home/me/.claude/skills/x` as `~/.claude/skills/x`.
pub fn short_path(path: &Path, home: &Path) -> String {
    match path.strip_prefix(home) {
        Ok(rest) if !home.as_os_str().is_empty() => format!("~/{}", rest.display()),
        _ => path.display().to_string(),
    }
}

fn first_line(text: &str, width: usize) -> String {
    let line = text.lines().next().unwrap_or("").trim();
    if line.chars().count() > width {
        format!("{}…", line.chars().take(width.saturating_sub(1)).collect::<String>())
    } else {
        line.to_string()
    }
}

/// Where an installed skill came from, in a few words.
fn provenance_words(provenance: Option<&Provenance>) -> String {
    match provenance {
        Some(Provenance::Recorded(origin)) => origin_words(origin),
        Some(Provenance::Edited(origin)) => format!("{}, edited since", origin_words(origin)),
        Some(Provenance::Unrecorded) => "not installed by puffin skill add".to_string(),
        None => String::new(),
    }
}

fn origin_words(origin: &install::Origin) -> String {
    match &origin.commit {
        Some(commit) => format!("{}@{}", origin.source, &commit[..commit.len().min(7)]),
        None => origin.source.clone(),
    }
}

/// `puffin skill list`: what the model is offered, by source, then what it is not and why.
/// `all` adds the skills a higher-precedence one shadows and those of a switched-off source.
pub fn list(plan: &Plan, machine: &Machine, settings: &Settings, all: bool) -> Vec<String> {
    let mut lines = Vec::new();
    let offered = plan.offered().count();
    lines.push(match plan.limit {
        Some(limit) => format!(
            "Skills the model is offered: {offered} ({} of {} catalogue tokens)",
            grouped(plan.used()),
            grouped(limit)
        ),
        None => format!("Skills the model is offered: {offered} ({} catalogue tokens)", grouped(plan.used())),
    });

    let width = plan.entries.iter().map(|entry| entry.skill.name.chars().count()).max().unwrap_or(0).min(32);
    let mut groups: Vec<(Origin, String)> = vec![
        (Origin::Repository, "This repository (.agents/skills, .codex/skills)".to_string()),
        (Origin::Installed, format!("puffin ({})", short_path(&machine.skills_root(), &machine.home))),
        (Origin::SharedAgents, "~/.agents/skills (shared with Gemini CLI and OpenClaw)".to_string()),
    ];
    for agent in AGENTS {
        groups.push((Origin::Linked(agent), format!("{} (~/{}, linked as from-{})", agent.product, agent.folder, agent.id)));
    }
    groups.push((Origin::Bundled, "Bundled with puffin".to_string()));
    for (origin, title) in groups {
        let entries: Vec<&Entry> = plan.offered().filter(|entry| entry.skill.origin == origin).collect();
        if entries.is_empty() {
            continue;
        }
        lines.push(String::new());
        lines.push(title);
        for entry in entries {
            let mut extras: Vec<String> = Vec::new();
            let provenance = provenance_words(entry.skill.provenance().as_ref());
            if !provenance.is_empty() {
                extras.push(provenance);
            }
            extras.extend(entry.notes.iter().cloned());
            let extras = if extras.is_empty() { String::new() } else { format!("  [{}]", extras.join("; ")) };
            lines.push(format!(
                "  {:<width$}  {}{extras}",
                entry.skill.name,
                first_line(entry.skill.description(), 60),
            ));
        }
    }

    let withheld: Vec<&Entry> = plan
        .entries
        .iter()
        .filter(|entry| match entry.status {
            Status::Offered => false,
            Status::Shadowed(_) | Status::SourceOff => all,
            _ => true,
        })
        .collect();
    if !withheld.is_empty() {
        lines.push(String::new());
        lines.push("Not offered".to_string());
        for entry in &withheld {
            let hint = match entry.status {
                Status::Unavailable(_) | Status::ManualOnly | Status::Shadowed(_) => {
                    format!(" (puffin skill enable {})", entry.skill.name)
                }
                _ => String::new(),
            };
            lines.push(format!(
                "  {:<width$}  {}: {}{hint}",
                entry.skill.name,
                entry.skill.origin.label(),
                entry.status.reason(),
            ));
        }
    }
    let hidden = plan
        .entries
        .iter()
        .filter(|entry| matches!(entry.status, Status::Shadowed(_) | Status::SourceOff))
        .count();
    if !all && hidden > 0 {
        lines.push(String::new());
        lines.push(format!("{hidden} more shadowed or from a switched-off source: puffin skill list --all"));
    }

    lines.push(String::new());
    let sources: Vec<String> = AGENTS
        .iter()
        .map(|agent| {
            let state = if settings.sources_off.contains(agent.id) {
                "off"
            } else if machine.home.join(agent.folder).is_dir() {
                "on"
            } else {
                "on, no folder"
            };
            format!("{} {state}", agent.id)
        })
        .collect();
    lines.push(format!("Linked sources: {} (puffin skill source <agent> on|off)", sources.join("; ")));
    lines
}

/// `puffin skill show <name>`: what the model will see of one skill, and what it ships.
pub fn show(entry: &Entry, machine: &Machine) -> Vec<String> {
    let skill = &entry.skill;
    let mut lines = vec![format!("{}: {}", skill.name, entry.status.reason())];
    let place = match &skill.origin {
        Origin::Linked(agent) => format!(
            "{} ({}), linked as {}",
            agent.product,
            short_path(&skill.dir, &machine.home),
            short_path(&machine.skills_root().join(format!("from-{}", agent.id)).join(&skill.name), &machine.home)
        ),
        origin => format!("{} ({})", origin.label(), short_path(&skill.dir, &machine.home)),
    };
    lines.push(format!("From:        {place}"));
    lines.push(format!("File:        {}", skill.skill_md.display()));
    match &skill.frontmatter {
        Err(error) => lines.push(format!("Frontmatter: {error}")),
        Ok(frontmatter) => {
            lines.push(format!("Description: {}", frontmatter.description));
            if let Some(licence) = install::licence_line(&skill.dir, frontmatter) {
                lines.push(format!("Licence:     {licence}"));
            }
            if let Some(compatibility) = &frontmatter.compatibility {
                lines.push(format!("Says:        {compatibility}"));
            }
            let mut declares = Vec::new();
            if !frontmatter.platforms.is_empty() {
                declares.push(format!("for {}", frontmatter.platforms.join(", ")));
            }
            if !frontmatter.required_bins.is_empty() {
                declares.push(format!("needs programs {}", frontmatter.required_bins.join(", ")));
            }
            if !frontmatter.any_bins.is_empty() {
                declares.push(format!("needs one of {}", frontmatter.any_bins.join(", ")));
            }
            if !frontmatter.required_env.is_empty() {
                declares.push(format!("asks for {} (never stored by puffin)", frontmatter.required_env.join(", ")));
            }
            if frontmatter.manual_only {
                declares.push("manual-only in its own agent".to_string());
            }
            if !declares.is_empty() {
                lines.push(format!("Declares:    {}", declares.join("; ")));
            }
            if !frontmatter.neutralised.is_empty() {
                lines.push(format!(
                    "Never acted on: {} (puffin runs nothing and pre-approves nothing a skill asks for)",
                    frontmatter.neutralised.join(", ")
                ));
            }
        }
    }
    if let Some(provenance) = &skill.provenance() {
        let record = match provenance {
            Provenance::Recorded(origin) | Provenance::Edited(origin) => {
                let mut words = provenance_words(Some(provenance));
                if let Some(repository) = &origin.repository {
                    words.push_str(&format!(", from {repository}"));
                }
                if !origin.installed.is_empty() {
                    words.push_str(&format!(", on {}", origin.installed));
                }
                words
            }
            Provenance::Unrecorded => {
                format!("no record: written by hand, by the model or by the built-in installer (puffin skill adopt {})", skill.name)
            }
        };
        lines.push(format!("Origin:      {record}"));
    }
    lines.push(format!("Catalogue:   {} tokens each session", grouped(entry.cost)));
    let files = install::files_of(&skill.dir);
    let total: u64 = files.iter().map(|(_, size)| size).sum();
    lines.push(format!("Files:       {} ({} bytes)", files.len(), grouped(total as usize)));
    for (path, size) in files.iter().take(40) {
        lines.push(format!("  {path}  {}", grouped(*size as usize)));
    }
    if files.len() > 40 {
        lines.push(format!("  … and {} more", files.len() - 40));
    }
    let scripts: Vec<&(String, u64)> = files.iter().filter(|(path, _)| path.starts_with("scripts/")).collect();
    if !scripts.is_empty() {
        lines.push(format!(
            "Scripts:     {} file(s) under scripts/; they run only as commands the model issues, under the session's sandbox",
            scripts.len()
        ));
    }
    lines
}

/// What `puffin skill add` shows before anything is installed (spec §6.2 step 4): the name and
/// description the model will be shown, where the skill comes from, its licence, every script it
/// ships, whether it can work here, and the catalogue budget after it.
pub fn staged(staged: &install::Staged, origin: &install::Origin, plan: &Plan, machine: &Machine) -> Vec<String> {
    let frontmatter = &staged.frontmatter;
    let from = match (&origin.repository, &origin.commit) {
        (Some(repository), Some(commit)) => format!("{}, {repository}@{}", origin.source, &commit[..commit.len().min(7)]),
        _ => origin.source.clone(),
    };
    let mut lines = vec![format!("{}  ({from})", staged.name), format!("  Description: {}", frontmatter.description)];
    lines.push(format!(
        "  Licence:     {}",
        install::licence_line(&staged.dir, frontmatter).unwrap_or_else(|| "none stated".to_string())
    ));
    lines.push(format!("  Files:       {} ({} bytes)", staged.files.len(), grouped(staged.total_bytes() as usize)));
    let scripts = staged.scripts();
    if scripts.is_empty() {
        lines.push("  Scripts:     none".to_string());
    }
    for (index, (path, size)) in scripts.iter().enumerate() {
        let label = if index == 0 { "  Scripts:    " } else { "              " };
        lines.push(format!("{label} {path} ({} bytes)", grouped(*size as usize)));
    }
    let (verdict, notes) = crate::preflight::check(frontmatter, &machine.host);
    lines.push(format!(
        "  Here:        {}",
        match verdict {
            crate::preflight::Verdict::Fits => "nothing it declares rules it out on this machine".to_string(),
            crate::preflight::Verdict::Unavailable(why) => {
                format!("unavailable: {why}. It will be installed but not offered (puffin skill enable {})", staged.name)
            }
            crate::preflight::Verdict::ManualOnly => format!(
                "manual-only in its own agent. It will be installed but not offered (puffin skill enable {})",
                staged.name
            ),
        }
    ));
    for note in notes {
        lines.push(format!("               {note} (shown only; puffin never stores a credential)"));
    }
    if !frontmatter.neutralised.is_empty() {
        lines.push(format!("  Never acted on: {}", frontmatter.neutralised.join(", ")));
    }
    if let Some(entry) = plan.offered().find(|entry| entry.skill.name == staged.name) {
        lines.push(format!(
            "  Note:        a skill named {} is already offered from {}; {}",
            staged.name,
            entry.skill.origin.label(),
            if entry.skill.origin == Origin::Repository { "that one keeps precedence here" } else { "this one will take its place" }
        ));
    }
    let skill_md = machine.skills_root().join(&staged.name).join("SKILL.md");
    let cost = crate::budget::line_cost(&staged.name, &frontmatter.description, &skill_md.to_string_lossy());
    lines.push(match plan.limit {
        Some(limit) => format!(
            "  Catalogue:   +{} tokens each session; {} of {} after this install",
            grouped(cost),
            grouped(plan.used() + cost),
            grouped(limit)
        ),
        None => format!("  Catalogue:   +{} tokens each session", grouped(cost)),
    });
    lines.push("Nothing runs by being installed. Its scripts run only as commands the model issues, under the session's sandbox.".to_string());
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::plan;
    use crate::testing::Fixture;

    #[test]
    fn the_list_groups_offered_skills_and_says_why_the_others_are_not() {
        let fixture = Fixture::new("report-list");
        fixture.skill(".puffin/skills/pdf", "pdf", "");
        fixture.skill(".claude/skills/pdf", "pdf", "");
        fixture.skill(".claude/skills/comms", "comms", "required_environment_variables: [SLACK_TOKEN]\n");
        fixture.skill(".hermes/skills/apple/imessage", "imessage", "platforms: [macos]\n");
        let mut machine = fixture.machine();
        machine.context_window = Some(262_144);
        let settings = Settings { sources_off: ["gemini".to_string()].into(), ..Settings::default() };
        let plan = plan(&machine, &settings);

        let text = list(&plan, &machine, &settings, false).join("\n");
        assert!(text.starts_with("Skills the model is offered: 2 ("), "{text}");
        assert!(text.contains("of 5,242 catalogue tokens)"), "{text}");
        assert!(text.contains("puffin (~/.puffin/skills)\n  pdf "), "{text}");
        assert!(text.contains("[not installed by puffin skill add]"), "{text}");
        assert!(text.contains("Claude Code (~/.claude/skills, linked as from-claude)\n  comms "), "{text}");
        assert!(text.contains("[needs SLACK_TOKEN]"), "{text}");
        assert!(text.contains("Not offered\n  imessage  Hermes Agent: unavailable: for macos only (puffin skill enable imessage)"), "{text}");
        assert!(text.contains("1 more shadowed or from a switched-off source: puffin skill list --all"), "{text}");
        assert!(text.ends_with("Linked sources: claude on; gemini off; openclaw on, no folder; hermes on (puffin skill source <agent> on|off)"), "{text}");

        let all = list(&plan, &machine, &settings, true).join("\n");
        assert!(all.contains("pdf       Claude Code: shadowed by puffin (~/.puffin/skills/pdf) (puffin skill enable pdf)"), "{all}");
        assert!(!all.contains("more shadowed"));
    }

    #[test]
    fn a_staged_skill_is_shown_before_it_is_installed() {
        let fixture = Fixture::new("report-staged");
        fixture.skill(".claude/skills/comms", "comms", "");
        let dir = fixture.skill(
            ".puffin/skills/.staging/t1/comms",
            "comms",
            "allowed-tools: Bash\nrequired_environment_variables: [SLACK_TOKEN]\nmetadata:\n  openclaw:\n    requires:\n      bins: [slackcat]\n",
        );
        std::fs::create_dir_all(dir.join("scripts")).unwrap_or_default();
        std::fs::write(dir.join("scripts/post.py"), "print(1)\n").unwrap_or_default();
        std::fs::write(dir.join("LICENSE.txt"), "Apache License\n").unwrap_or_default();
        let staged_skill = install::validate(&dir).unwrap_or_else(|error| panic!("{error}"));
        let origin = install::Origin {
            source: "anthropic/comms".into(),
            repository: Some("anthropics/skills".into()),
            commit: Some("0123456789abcdef".into()),
            ..install::Origin::default()
        };
        let mut machine = fixture.machine();
        machine.context_window = Some(262_144);
        let plan = plan(&machine, &Settings::default());
        let text = staged(&staged_skill, &origin, &plan, &machine).join("\n");
        assert!(text.starts_with("comms  (anthropic/comms, anthropics/skills@0123456)\n  Description: The comms skill, for tests.\n  Licence:     Apache License\n  Files:       3 ("), "{text}");
        assert!(text.contains("  Scripts:     scripts/post.py (9 bytes)"), "{text}");
        assert!(text.contains("  Here:        unavailable: needs slackcat. It will be installed but not offered (puffin skill enable comms)"), "{text}");
        assert!(text.contains("needs SLACK_TOKEN (shown only; puffin never stores a credential)"), "{text}");
        assert!(text.contains("  Never acted on: allowed-tools"), "{text}");
        assert!(text.contains("a skill named comms is already offered from Claude Code; this one will take its place"), "{text}");
        assert!(text.contains(" of 5,242 after this install"), "{text}");
        assert!(text.ends_with("under the session's sandbox."), "{text}");
    }

    #[test]
    fn show_prints_what_the_model_sees_and_what_the_skill_ships() {
        let fixture = Fixture::new("report-show");
        let dir = fixture.skill(
            ".claude/skills/deploy",
            "deploy",
            "license: MIT\ndisable-model-invocation: true\nhooks:\n  PreToolUse: []\nmetadata:\n  openclaw:\n    requires:\n      bins: [gh]\n      env: [GH_TOKEN]\n",
        );
        std::fs::create_dir_all(dir.join("scripts")).unwrap_or_default();
        std::fs::write(dir.join("scripts/run.sh"), "#!/bin/sh\n").unwrap_or_default();
        let machine = fixture.machine();
        let plan = plan(&machine, &Settings::default());
        let entry = plan.find("deploy").cloned();
        let text = entry.map(|entry| show(&entry, &machine).join("\n")).unwrap_or_default();
        assert!(text.starts_with("deploy: unavailable: needs gh\n"), "{text}");
        assert!(text.contains("From:        Claude Code (~/.claude/skills/deploy), linked as ~/.puffin/skills/from-claude/deploy"), "{text}");
        assert!(text.contains("Licence:     MIT"), "{text}");
        assert!(text.contains("Declares:    needs programs gh; asks for GH_TOKEN (never stored by puffin); manual-only in its own agent"), "{text}");
        assert!(text.contains("Never acted on: hooks"), "{text}");
        assert!(text.contains("Files:       2 ("), "{text}");
        assert!(text.contains("  scripts/run.sh  10"), "{text}");
        assert!(text.contains("Scripts:     1 file(s) under scripts/"), "{text}");
    }
}
