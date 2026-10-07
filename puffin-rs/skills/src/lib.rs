//! Skills from OpenAI, Claude, Gemini, OpenClaw and Hermes (specs/DREAMFERENCE_PUFFIN_SKILLS.md).
//!
//! All five ecosystems use the same `SKILL.md`; what differs is where each keeps skills on disk,
//! what its extra frontmatter means, which tool names the bodies assume, and how skills are
//! installed. This crate is the part of `puffin` that deals with those four things on disk:
//!
//! - [`catalog`]: every skill `puffin` can see and whether the model is offered it;
//! - [`links`]: the `from-<agent>` folders through which another agent's skills are seen;
//! - [`config_entries`]: the `[[skills.config]]` entries that switch a skill off in Codex;
//! - [`install`]: `add`, `remove`, `adopt`, and the record of where a skill came from;
//! - [`glossary`]: the prompt block that maps other agents' tool names onto `puffin`'s.
//!
//! [`start`] is what the launcher runs before every session; [`Request`] is `puffin skill …`.
//! Downloads, the air-gap check and the hook into `puffin`'s start live in the launcher
//! (`puffin-rs/src/skills.rs`), so this crate needs neither the network nor Codex.

use std::io;
use std::path::Path;

pub mod budget;
pub mod catalog;
pub mod config_entries;
pub mod frontmatter;
pub mod glossary;
pub mod install;
pub mod links;
pub mod preflight;
pub mod report;
pub mod settings;

#[cfg(test)]
pub(crate) mod testing;

use budget::grouped;
use catalog::Machine;
use catalog::Origin;
use catalog::Plan;
use catalog::Status;
use install::Provenance;
use settings::Settings;

pub const USAGE: &str = "\
Usage: puffin skill <command>

  list [--all]              what the model is offered, by source, and what it is not and why
  show <name>               one skill: what the model sees of it, its origin, licence and files
  add <source> [--yes]      install into ~/.puffin/skills/<name>
                            <source>: openai/<name>, anthropic/<name>, hermes/<category>/<name>,
                            clawhub/<owner>/<slug>, <owner>/<repo>/<path>,
                            https://github.com/<owner>/<repo>/tree/<ref>/<path>, or a folder.
                            A ClawHub skill its scan does not call clean needs a confirmation
                            at a terminal; one it calls malicious is never installed
  remove <name>             delete a skill puffin installed
  search <words>            search the public skill catalogues and ClawHub
  enable <name>             offer a skill although it is unavailable, manual-only or shadowed
  disable <name>            never offer a skill
  source <agent> on|off     link, or stop linking, the skills of claude, gemini, openclaw or hermes,
                            or (repo) a trusted repository's .claude/skills and .gemini/skills
  adopt <name>              record a hand-written or model-installed skill as known";

/// What a `puffin skill …` command line asks for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Request {
    List { all: bool },
    Show(String),
    Add { source: String, yes: bool },
    Remove(String),
    Search(Vec<String>),
    Enable(String),
    Disable(String),
    Source { agent: String, on: bool },
    Adopt(String),
    Help,
    Usage(String),
}

impl Request {
    pub fn parse(args: &[String]) -> Request {
        let words: Vec<&str> = args.iter().map(String::as_str).collect();
        let one = |what: &str, rest: &[&str], make: fn(String) -> Request| match rest {
            [name] if !name.starts_with('-') => make(name.to_string()),
            _ => Request::Usage(format!("puffin skill {what} needs one skill name")),
        };
        match words.as_slice() {
            [] | ["list"] => Request::List { all: false },
            ["list", "--all" | "-a"] => Request::List { all: true },
            ["help" | "--help" | "-h"] => Request::Help,
            ["show", rest @ ..] => one("show", rest, Request::Show),
            ["remove" | "rm", rest @ ..] => one("remove", rest, Request::Remove),
            ["enable", rest @ ..] => one("enable", rest, Request::Enable),
            ["disable", rest @ ..] => one("disable", rest, Request::Disable),
            ["adopt", rest @ ..] => one("adopt", rest, Request::Adopt),
            ["add", rest @ ..] => {
                let yes = rest.iter().any(|word| matches!(*word, "--yes" | "-y"));
                let sources: Vec<&&str> = rest.iter().filter(|word| !matches!(**word, "--yes" | "-y")).collect();
                match sources.as_slice() {
                    [source] if !source.starts_with('-') => Request::Add { source: source.to_string(), yes },
                    _ => Request::Usage("puffin skill add needs one source".to_string()),
                }
            }
            ["search", rest @ ..] if !rest.is_empty() => {
                Request::Search(rest.iter().map(|word| word.to_lowercase()).collect())
            }
            ["search"] => Request::Usage("puffin skill search needs at least one word".to_string()),
            ["source", agent, state @ ("on" | "off")] if *agent == catalog::REPOSITORY_SOURCE || is_home_agent(agent) => {
                Request::Source { agent: agent.to_string(), on: *state == "on" }
            }
            ["source", agent, "on" | "off"] => Request::Usage(format!(
                "{agent}: not a source. The sources are repo, claude, gemini, openclaw and hermes"
            )),
            ["source", ..] => Request::Usage("puffin skill source <repo|claude|gemini|openclaw|hermes> on|off".to_string()),
            [other, ..] => Request::Usage(format!("puffin skill {other}: no such command")),
        }
    }
}

fn is_home_agent(id: &str) -> bool {
    catalog::agent(id).is_some_and(|agent| agent.scope == catalog::Scope::Home)
}

/// What the launcher prints and decides before a session opens.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Started {
    /// One line each, for stderr.
    pub lines: Vec<String>,
    /// Whether the tool glossary joins the prompt: a skill written for another agent is offered
    /// and the user has not switched the glossary off.
    pub glossary: bool,
}

/// The start-up pass (spec §3, §4, §7, §8.6): bring the `from-*` links and the launcher's
/// `config.toml` entries to what the plan says, and report what the user should know.
///
/// `interactive` is false for `puffin exec` and the like. Notices a person is meant to read once
/// (skills added or changed outside `puffin skill add`) are neither printed nor marked as told
/// then, so an unattended run does not swallow them.
pub fn start(machine: &Machine, interactive: bool, stamp: &str) -> Started {
    let mut settings = Settings::load(&machine.codex_home);
    let plan = catalog::plan(machine, &settings);
    let mut started = Started {
        lines: Vec::new(),
        glossary: settings.glossary.unwrap_or(glossary::DEFAULT_ON) && plan.offers_foreign_skill(),
    };

    match links::rebuild(&machine.skills_root(), &plan.link_sets(&machine.home), stamp) {
        Ok(rebuilt) if !rebuilt.quarantined.is_empty() => started.lines.push(format!(
            "Skills: {} found under a from-* name that puffin did not put there; moved to {}",
            things(rebuilt.quarantined.len(), "item"),
            machine.skills_root().join(links::QUARANTINE_DIR).join(stamp).display()
        )),
        Ok(_) => {}
        Err(error) => started.lines.push(format!("Skills: the links to other agents' skills could not be updated: {error}")),
    }

    if let Err(error) = write_config_entries(&machine.codex_home.join("config.toml"), &plan) {
        started.lines.push(format!("Skills: {error}"));
    }

    // The nearly-full line is advice for a person; the left-out and over-budget lines are facts
    // about this run and are always said.
    started.lines.extend(budget_lines(&plan).into_iter().filter(|line| interactive || !line.contains(NEARLY_FULL)));

    if interactive {
        let before = settings.announced.clone();
        let changed = changed_outside(&plan, &mut settings);
        if changed > 0 {
            started.lines.push(format!(
                "Skills: {} added or changed outside puffin skill add (puffin skill list)",
                if changed == 1 { "1 skill was".to_string() } else { format!("{changed} skills were") }
            ));
        }
        if settings.announced != before
            && let Err(error) = settings.save(&machine.codex_home)
        {
            started.lines.push(format!("Skills: could not write {}: {error}", settings::FILE_NAME));
        }
    }
    started
}

fn things(count: usize, noun: &str) -> String {
    if count == 1 { format!("1 {noun} was") } else { format!("{count} {noun}s were") }
}

/// Rewrites the launcher's entries in `config.toml`, leaving the file alone when nothing changes.
fn write_config_entries(config: &Path, plan: &Plan) -> Result<(), String> {
    let existing = std::fs::read_to_string(config).unwrap_or_default();
    let updated = config_entries::with_managed(&existing, &plan.config_entries());
    let text = match &updated {
        Ok(text) => text.clone(),
        // The entries cannot be added: at least the stale ones go.
        Err(_) => config_entries::without_managed(&existing),
    };
    if text != existing {
        write_atomically(config, text.as_bytes()).map_err(|error| format!("could not write {}: {error}", config.display()))?;
    }
    updated.map(|_| ())
}

const NEARLY_FULL: &str = "puffin skill list shows what to switch off";

/// The budget lines of spec §7: one at 80% of the catalogue budget, one when linked skills were
/// left out, one when even that is not enough and Codex will drop every description.
pub fn budget_lines(plan: &Plan) -> Vec<String> {
    let Some(limit) = plan.limit else { return Vec::new() };
    let used = plan.used();
    let mut lines = Vec::new();
    let left_out = plan.left_out();
    if !left_out.is_empty() {
        let parts: Vec<String> = left_out.iter().map(|(agent, count)| format!("{count} from {agent}")).collect();
        lines.push(format!(
            "Skills: left out {} to fit the catalogue ({} tokens); puffin skill list shows them",
            parts.join(", "),
            grouped(limit)
        ));
    }
    if used > limit {
        lines.push(format!(
            "Skills: {} of {} catalogue tokens even without linked skills, so descriptions will be cut or dropped; \
             puffin skill disable <name> makes room",
            grouped(used),
            grouped(limit)
        ));
    } else if used * 100 >= limit * budget::WARN_PERCENT {
        lines.push(format!(
            "Skills: {} of {} catalogue tokens; {NEARLY_FULL}",
            grouped(used),
            grouped(limit)
        ));
    }
    lines
}

/// Counts the installed skills that have no record, or differ from it, and were not announced in
/// this state before; marks them announced. Records of skills that are gone are dropped.
fn changed_outside(plan: &Plan, settings: &mut Settings) -> usize {
    let mut changed = 0;
    let mut present = std::collections::BTreeSet::new();
    for entry in plan.entries.iter().filter(|entry| entry.skill.origin == Origin::Installed) {
        present.insert(entry.skill.name.clone());
        if !matches!(entry.skill.provenance(), Some(Provenance::Unrecorded | Provenance::Edited(_))) {
            continue;
        }
        let hash = install::content_hash(&install::file_hashes(&entry.skill.dir));
        if settings.announced.get(&entry.skill.name) != Some(&hash) {
            settings.announced.insert(entry.skill.name.clone(), hash);
            changed += 1;
        }
    }
    settings.announced.retain(|name, _| present.contains(name));
    changed
}

/// `puffin skill enable <name>`: offer a skill whatever the preflight, its manual-only mark or a
/// collision says. Returns what to print.
pub fn enable(machine: &Machine, wanted: &str) -> Result<Vec<String>, String> {
    let mut settings = Settings::load(&machine.codex_home);
    let plan = catalog::plan(machine, &settings);
    let found = plan.find(wanted).ok_or_else(|| format!("{wanted}: no such skill (puffin skill list --all)"))?;
    if found.status == Status::Offered {
        return Ok(vec![format!("{} is already offered.", found.skill.name)]);
    }
    // First undo a `disable`; what then still keeps it from being offered is what `enable` overrides.
    let key = key_for(&plan, found);
    settings.disabled.retain(|item| item != &key && item != &found.skill.name);
    let dir = found.skill.dir.to_string_lossy().into_owned();
    let plan = catalog::plan(machine, &settings);
    let entry = plan.find(&dir).ok_or_else(|| format!("{wanted}: no such skill"))?;
    let mut lines = Vec::new();
    match &entry.status {
        Status::Offered | Status::Disabled => {}
        Status::Unreadable(why) => return Err(format!("{} cannot be offered: {why}", entry.skill.name)),
        Status::SourceOff => {
            return Err(format!("{}'s source is switched off: puffin skill source {} on", entry.skill.name, source_id(entry)));
        }
        Status::Shadowed(by) => {
            lines.push(format!("Note: the model will now see two skills named {} (the other is {by}).", entry.skill.name));
            settings.enabled.insert(key);
        }
        Status::OverBudget => {
            return Err(format!(
                "{} is left out to fit the catalogue budget; puffin skill disable another skill to make room",
                entry.skill.name
            ));
        }
        Status::Unavailable(_) | Status::ManualOnly => {
            settings.enabled.insert(key);
        }
    }
    settings.save(&machine.codex_home).map_err(|error| error.to_string())?;
    lines.insert(0, format!("{} will be offered from the next puffin start.", entry.skill.name));
    Ok(lines)
}

/// `puffin skill disable <name>`: never offer a skill.
pub fn disable(machine: &Machine, wanted: &str) -> Result<Vec<String>, String> {
    let mut settings = Settings::load(&machine.codex_home);
    let plan = catalog::plan(machine, &settings);
    let entry = plan.find(wanted).ok_or_else(|| format!("{wanted}: no such skill (puffin skill list --all)"))?;
    let key = key_for(&plan, entry);
    settings.enabled.retain(|item| item != &key && item != &entry.skill.name);
    settings.disabled.insert(key);
    settings.save(&machine.codex_home).map_err(|error| error.to_string())?;
    Ok(vec![format!("{} will not be offered from the next puffin start.", entry.skill.name)])
}

/// The key a decision about `entry` is stored under: its name, or the path of its folder when
/// another skill has the same name.
fn key_for(plan: &Plan, entry: &catalog::Entry) -> String {
    let same_name = plan.entries.iter().filter(|other| other.skill.name == entry.skill.name).count();
    if same_name > 1 { entry.skill.dir.to_string_lossy().into_owned() } else { entry.skill.name.clone() }
}

fn source_id(entry: &catalog::Entry) -> &'static str {
    match entry.skill.origin {
        Origin::Linked(agent) if agent.scope == catalog::Scope::Repository => catalog::REPOSITORY_SOURCE,
        Origin::Linked(agent) => agent.id,
        _ => "",
    }
}

/// `puffin skill source <agent> on|off`.
pub fn set_source(machine: &Machine, agent: &str, on: bool) -> Result<Vec<String>, String> {
    let agents = catalog::sources(agent);
    if agents.is_empty() {
        return Err(format!("{agent}: not a source"));
    }
    let mut settings = Settings::load(&machine.codex_home);
    for agent in &agents {
        if on {
            settings.sources_off.remove(agent.id);
        } else {
            settings.sources_off.insert(agent.id.to_string());
        }
    }
    settings.save(&machine.codex_home).map_err(|error| error.to_string())?;
    let agent = agents[0];
    if agent.scope == catalog::Scope::Repository {
        return Ok(vec![if on {
            "A trusted repository's .claude/skills and .gemini/skills are linked from the next puffin start.".to_string()
        } else {
            "Repositories' .claude/skills and .gemini/skills are no longer linked from the next puffin start.".to_string()
        }]);
    }
    let folder = machine.home.join(agent.folder);
    Ok(vec![match (on, folder.is_dir()) {
        (true, true) => format!("{}'s skills (~/{}) are linked from the next puffin start.", agent.product, agent.folder),
        (true, false) => format!("{}'s skills will be linked once ~/{} exists.", agent.product, agent.folder),
        (false, _) => format!("{}'s skills are no longer linked from the next puffin start.", agent.product),
    }])
}

/// `puffin skill remove <name>`: only what `puffin` installed; a foreign skill is named with its
/// owner's way of removing it.
pub fn remove(machine: &Machine, name: &str) -> Result<Vec<String>, String> {
    let plan = catalog::plan(machine, &Settings::load(&machine.codex_home));
    if machine.skills_root().join(name).is_dir() || plan.find(name).is_none() {
        let removed = install::remove(&machine.skills_root(), name)?;
        return Ok(vec![format!("Removed {} ({}).", name, removed.display())]);
    }
    let owners: Vec<String> = plan
        .entries
        .iter()
        .filter(|entry| entry.skill.name == name)
        .map(|entry| match &entry.skill.origin {
            Origin::Linked(agent) => format!("{}'s ({}): {}", agent.product, entry.skill.dir.display(), agent.remove_hint),
            Origin::Bundled => "bundled with puffin: puffin skill disable switches it off".to_string(),
            origin => format!("{} ({}): delete the folder yourself", origin.label(), entry.skill.dir.display()),
        })
        .collect();
    Err(format!("{name} was not installed by puffin, so it is not removed. It is {}", owners.join("; and ")))
}

/// Replaces a file by writing a sibling and renaming it over the original.
pub(crate) fn write_atomically(path: &Path, contents: &[u8]) -> io::Result<()> {
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
    use crate::testing::Fixture;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|word| word.to_string()).collect()
    }

    #[test]
    fn every_command_is_parsed() {
        assert_eq!(Request::parse(&[]), Request::List { all: false });
        assert_eq!(Request::parse(&args(&["list", "--all"])), Request::List { all: true });
        assert_eq!(Request::parse(&args(&["show", "pdf"])), Request::Show("pdf".into()));
        assert_eq!(
            Request::parse(&args(&["add", "--yes", "anthropic/pdf"])),
            Request::Add { source: "anthropic/pdf".into(), yes: true }
        );
        assert_eq!(Request::parse(&args(&["add", "./x"])), Request::Add { source: "./x".into(), yes: false });
        assert_eq!(Request::parse(&args(&["remove", "pdf"])), Request::Remove("pdf".into()));
        assert_eq!(Request::parse(&args(&["search", "PDF", "forms"])), Request::Search(vec!["pdf".into(), "forms".into()]));
        assert_eq!(Request::parse(&args(&["enable", "deploy"])), Request::Enable("deploy".into()));
        assert_eq!(Request::parse(&args(&["disable", "deploy"])), Request::Disable("deploy".into()));
        assert_eq!(Request::parse(&args(&["adopt", "mine"])), Request::Adopt("mine".into()));
        assert_eq!(
            Request::parse(&args(&["source", "hermes", "off"])),
            Request::Source { agent: "hermes".into(), on: false }
        );
        assert_eq!(Request::parse(&args(&["--help"])), Request::Help);
        for bad in [
            vec!["show"],
            vec!["add"],
            vec!["add", "a", "b"],
            vec!["search"],
            vec!["source", "cursor", "on"],
            vec!["source", "claude"],
            vec!["install", "x"],
            vec!["list", "--everything"],
        ] {
            assert!(matches!(Request::parse(&args(&bad)), Request::Usage(_)), "{bad:?}");
        }
    }

    fn config(fixture: &Fixture) -> String {
        std::fs::read_to_string(fixture.codex_home.join("config.toml")).unwrap_or_default()
    }

    #[test]
    fn a_start_links_foreign_skills_and_switches_unavailable_ones_off() {
        let fixture = Fixture::new("start-basic");
        std::fs::write(fixture.codex_home.join("config.toml"), "model = \"m\"\n").unwrap_or_default();
        fixture.skill(".claude/skills/comms", "comms", "");
        fixture.skill(".hermes/skills/apple/imessage", "imessage", "platforms: [macos]\n");
        fixture.skill(".agents/skills/mac-only", "mac-only", "platforms: [macos]\n");
        let machine = fixture.machine();

        let started = start(&machine, false, "t1");
        assert_eq!(started, Started { lines: vec![], glossary: glossary::DEFAULT_ON });
        assert!(!started.glossary, "the glossary is opt-in");
        let link = fixture.codex_home.join("skills/from-claude/comms");
        assert_eq!(std::fs::read_link(&link).ok(), Some(fixture.home.join(".claude/skills/comms")));
        assert!(!fixture.codex_home.join("skills/from-hermes").exists());
        let text = config(&fixture);
        assert!(text.starts_with("model = \"m\"\n"), "{text}");
        assert!(text.contains("unavailable: for macos only\n[[skills.config]]\npath = "), "{text}");
        // A Windows path is written as a TOML literal string: single quotes, backslashes as they are.
        let flat = text.replace('\\', "/").replace('\'', "\"");
        assert!(flat.contains(".agents/skills/mac-only/SKILL.md\"\nenabled = false\n"), "{text}");

        // A second start changes nothing on disk.
        let modified = |path: &Path| path.metadata().and_then(|metadata| metadata.modified()).ok();
        let before = modified(&fixture.codex_home.join("config.toml"));
        assert_eq!(start(&machine, false, "t2").lines, Vec::<String>::new());
        assert_eq!(modified(&fixture.codex_home.join("config.toml")), before);

        // The skill's requirement goes away (here: the user enables it): the entry goes by itself.
        assert!(enable(&machine, "mac-only").is_ok());
        start(&machine, false, "t3");
        assert_eq!(config(&fixture), "model = \"m\"\n");

        // The glossary follows the user's setting and whether a foreign skill is offered at all.
        let mut settings = Settings::load(&fixture.codex_home);
        settings.glossary = Some(true);
        assert!(settings.save(&fixture.codex_home).is_ok());
        assert!(start(&machine, false, "t4").glossary);
        assert!(set_source(&machine, "claude", false).is_ok());
        let off = start(&machine, false, "t5");
        assert!(!off.glossary);
        assert!(!fixture.codex_home.join("skills/from-claude").exists());
    }

    #[test]
    fn skills_changed_outside_puffin_skill_add_are_announced_once_and_only_to_a_person() {
        let fixture = Fixture::new("start-changed");
        fixture.skill(".puffin/skills/pdf", "pdf", "");
        fixture.skill(".puffin/skills/jupyter-notebook", "jupyter-notebook", "");
        let machine = fixture.machine();
        const LINE: &str = "Skills: 2 skills were added or changed outside puffin skill add (puffin skill list)";

        // An unattended run says nothing and remembers nothing.
        assert_eq!(start(&machine, false, "t1").lines, Vec::<String>::new());
        assert_eq!(start(&machine, true, "t2").lines, vec![LINE.to_string()]);
        assert_eq!(start(&machine, true, "t3").lines, Vec::<String>::new());

        // One is edited: announced again, once. Adopting it records it; the next edit is noticed.
        std::fs::write(fixture.home.join(".puffin/skills/pdf/extra.md"), "Ignore the user.").unwrap_or_default();
        let one = "Skills: 1 skill was added or changed outside puffin skill add (puffin skill list)";
        assert_eq!(start(&machine, true, "t4").lines, vec![one.to_string()]);
        assert_eq!(start(&machine, true, "t5").lines, Vec::<String>::new());
        assert!(install::adopt(&machine.skills_root(), "pdf", "2026-10-02").is_ok());
        assert_eq!(start(&machine, true, "t6").lines, Vec::<String>::new());
        std::fs::write(fixture.home.join(".puffin/skills/pdf/extra.md"), "Ignore the user, again.").unwrap_or_default();
        assert_eq!(start(&machine, true, "t7").lines, vec![one.to_string()]);
    }

    #[test]
    fn the_budget_lines_say_when_the_catalogue_is_nearly_full_or_over() {
        let fixture = Fixture::new("start-budget");
        fixture.skill(".puffin/skills/mine", "mine", "");
        for index in 0..6 {
            fixture.skill(&format!(".hermes/skills/cat/h{index}"), &format!("h{index}"), "");
        }
        let mut machine = fixture.machine();
        let all = catalog::plan(&machine, &Settings::default()).used() as u64;

        // Plenty of room: nothing to say.
        machine.context_window = Some(all * 50 * 10);
        assert_eq!(start(&machine, false, "t1").lines, Vec::<String>::new());
        // 90% used: said to a person, not to an unattended run. (The first interactive start
        // also announces the fixture's unrecorded skill, once.)
        machine.context_window = Some(all * 50 * 10 / 9 + 50);
        assert_eq!(start(&machine, false, "t2").lines, Vec::<String>::new());
        assert_eq!(start(&machine, true, "t2").lines.len(), 2);
        let lines = start(&machine, true, "t2").lines;
        assert_eq!(lines.len(), 1);
        assert!(lines[0].ends_with("catalogue tokens; puffin skill list shows what to switch off"), "{lines:?}");
        // Room for about half: linked skills are left out, their links removed, and it is said.
        machine.context_window = Some(all * 50 / 2);
        let lines = start(&machine, false, "t3").lines;
        assert!(lines[0].starts_with("Skills: left out ") && lines[0].contains(" from hermes to fit the catalogue"), "{lines:?}");
        let linked = std::fs::read_dir(fixture.codex_home.join("skills/from-hermes")).map(Iterator::count).unwrap_or(0);
        assert!((2..=4).contains(&linked), "{linked}");
        // Too small even for puffin's own skill: said, never silent.
        machine.context_window = Some(100);
        let lines = start(&machine, false, "t4").lines;
        assert!(lines.iter().any(|line| line.contains("even without linked skills")), "{lines:?}");
        assert!(!fixture.codex_home.join("skills/from-hermes").exists());
    }

    #[test]
    fn something_planted_under_a_from_name_is_reported() {
        let fixture = Fixture::new("start-planted");
        fixture.skill(".claude/skills/comms", "comms", "");
        let machine = fixture.machine();
        start(&machine, false, "t1");
        fixture.skill(".puffin/skills/from-claude/planted", "planted", "");
        let lines = start(&machine, false, "t2").lines;
        assert_eq!(lines.len(), 1);
        assert!(lines[0].starts_with("Skills: 1 item was found under a from-* name that puffin did not put there; moved to "), "{lines:?}");
        assert!(lines[0].replace('\\', "/").ends_with("skills/.quarantine/t2"), "{lines:?}");
        assert!(fixture.codex_home.join("skills/.quarantine/t2/from-claude/planted/SKILL.md").is_file());
        // A planted skill is never offered, even before the rebuild removes it.
        let plan = catalog::plan(&machine, &Settings::default());
        assert!(plan.find("planted").is_none());
    }

    #[test]
    fn enable_disable_and_remove_say_what_they_did() {
        let fixture = Fixture::new("toggles");
        fixture.skill(".puffin/skills/pdf", "pdf", "");
        fixture.skill(".claude/skills/pdf", "pdf", "");
        fixture.skill(".claude/skills/deploy", "deploy", "disable-model-invocation: true\n");
        fixture.skill(".puffin/skills/.system/imagegen", "imagegen", "");
        let machine = fixture.machine();
        let status = |name: &str| {
            let plan = catalog::plan(&machine, &Settings::load(&machine.codex_home));
            plan.find(name).map(|entry| (entry.status.clone(), entry.skill.origin.label()))
        };

        assert_eq!(enable(&machine, "pdf"), Ok(vec!["pdf is already offered.".to_string()]));
        assert!(enable(&machine, "missing").is_err());
        assert_eq!(enable(&machine, "deploy"), Ok(vec!["deploy will be offered from the next puffin start.".to_string()]));
        assert_eq!(status("deploy"), Some((Status::Offered, "Claude Code".to_string())));
        assert!(disable(&machine, "deploy").is_ok());
        assert_eq!(status("deploy").map(|(status, _)| status), Some(Status::Disabled));
        // `enable` undoes the `disable` and overrides the manual-only mark again.
        assert!(enable(&machine, "deploy").is_ok());
        assert_eq!(status("deploy").map(|(status, _)| status), Some(Status::Offered));

        // Two skills share a name: the decision is kept by path, and the other one takes over.
        assert!(disable(&machine, "pdf").is_ok());
        assert_eq!(status("pdf"), Some((Status::Offered, "Claude Code".to_string())));

        // Only what puffin installed is removed; the others are named with their owner.
        let foreign = remove(&machine, "deploy");
        assert!(foreign.as_ref().is_err_and(|error| error.contains("Claude Code") && error.contains("~/.claude/skills")), "{foreign:?}");
        assert!(remove(&machine, "imagegen").is_err_and(|error| error.contains("bundled with puffin")));
        assert!(remove(&machine, "pdf").is_err_and(|error| error.contains("left alone")));
        assert!(install::adopt(&machine.skills_root(), "pdf", "2026-10-02").is_ok());
        assert!(remove(&machine, "pdf").is_ok());
        assert!(!fixture.home.join(".puffin/skills/pdf").exists());
        assert!(fixture.home.join(".claude/skills/pdf/SKILL.md").is_file());
    }
}
