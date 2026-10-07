//! Every skill `puffin` can see, and which of them the model is offered (spec §3, §4, §7).
//!
//! One pass finds the skills in the order of their precedence, reads each one's frontmatter,
//! applies what the user decided, the preflight and the collision rule, and then the catalogue
//! budget. The result says, for every skill, whether it is offered and if not why; the links and
//! the `config.toml` entries follow from it.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::path::Path;
use std::path::PathBuf;

use crate::budget;
use crate::frontmatter;
use crate::frontmatter::Frontmatter;
use crate::install;
use crate::install::Provenance;
use crate::links::LINK_PREFIX;
use crate::links::LinkSet;
use crate::links::Source;
use crate::preflight;
use crate::preflight::Host;
use crate::preflight::Verdict;
use crate::settings::Settings;

/// How deep Codex scans below one of its own roots (`MAX_SCAN_DEPTH` in the pinned source).
const CODEX_SCAN_DEPTH: usize = 6;
/// How deep a foreign folder is walked: Hermes keeps skills under a category, and Claude Code
/// keeps the ones synced from claude.ai under `synced/`.
const FOREIGN_SCAN_DEPTH: usize = 3;

/// An agent whose skills folder is linked, in the order of precedence between them (spec §7).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Agent {
    /// `claude`: the name in `from-claude` and in `puffin skill source claude off`.
    pub id: &'static str,
    /// Its skills folder, relative to the home folder, or to each folder from the repository's
    /// root down to the working directory.
    pub folder: &'static str,
    /// What its owner is called, and the command that removes a skill there.
    pub product: &'static str,
    pub remove_hint: &'static str,
    pub scope: Scope,
}

/// Where an agent's folder is looked for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    Home,
    /// In the repository being worked in, and only when the user trusts it (spec §8.5).
    Repository,
}

/// The id `puffin skill source` takes for both repository folders.
pub const REPOSITORY_SOURCE: &str = "repo";

const REPOSITORY_HINT: &str = "it is part of the repository; puffin skill source repo off stops linking it";

pub const AGENTS: &[Agent] = &[
    // A repository's `.claude/skills` and `.gemini/skills` come first among linked sources: they
    // are about the code at hand (spec §7).
    Agent {
        id: "repo-claude",
        folder: ".claude/skills",
        product: "this repository's .claude/skills",
        remove_hint: REPOSITORY_HINT,
        scope: Scope::Repository,
    },
    Agent {
        id: "repo-gemini",
        folder: ".gemini/skills",
        product: "this repository's .gemini/skills",
        remove_hint: REPOSITORY_HINT,
        scope: Scope::Repository,
    },
    Agent {
        id: "claude",
        folder: ".claude/skills",
        product: "Claude Code",
        remove_hint: "delete its folder under ~/.claude/skills, or /plugin uninstall in Claude Code",
        scope: Scope::Home,
    },
    Agent {
        id: "gemini",
        folder: ".gemini/skills",
        product: "Gemini CLI",
        remove_hint: "gemini skills uninstall <name>",
        scope: Scope::Home,
    },
    Agent {
        id: "openclaw",
        folder: ".openclaw/skills",
        product: "OpenClaw",
        remove_hint: "openclaw skills uninstall <name>",
        scope: Scope::Home,
    },
    Agent {
        id: "hermes",
        folder: ".hermes/skills",
        product: "Hermes Agent",
        remove_hint: "hermes skills uninstall <name>",
        scope: Scope::Home,
    },
];

pub fn agent(id: &str) -> Option<&'static Agent> {
    AGENTS.iter().find(|agent| agent.id == id)
}

/// The agents `puffin skill source <id>` switches: one, or both repository folders for `repo`.
pub fn sources(id: &str) -> Vec<&'static Agent> {
    if id == REPOSITORY_SOURCE {
        AGENTS.iter().filter(|agent| agent.scope == Scope::Repository).collect()
    } else {
        agent(id).into_iter().collect()
    }
}

/// Where a skill was found. The variants are in the order of precedence: for one name, the first
/// wins.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Origin {
    /// `.agents/skills` or `.codex/skills` of the repository being worked in.
    Repository,
    /// `$CODEX_HOME/skills/<name>`: installed for `puffin` itself.
    Installed,
    /// `~/.agents/skills`, shared with Gemini CLI and OpenClaw.
    SharedAgents,
    /// Another agent's own folder, seen through a `from-<agent>` link.
    Linked(&'static Agent),
    /// Codex's bundled skills, `$CODEX_HOME/skills/.system`.
    Bundled,
}

impl Origin {
    pub fn label(&self) -> String {
        match self {
            Origin::Repository => "this repository".to_string(),
            Origin::Installed => "puffin".to_string(),
            Origin::SharedAgents => "~/.agents/skills".to_string(),
            Origin::Linked(agent) => agent.product.to_string(),
            Origin::Bundled => "bundled".to_string(),
        }
    }
}

/// One skill on disk.
#[derive(Debug, Clone)]
pub struct Skill {
    /// The frontmatter's name, or the folder's when it has none.
    pub name: String,
    /// The folder holding `SKILL.md`, as it was found.
    pub dir: PathBuf,
    /// The canonical path of its `SKILL.md`: what Codex shows the model and what a
    /// `[[skills.config]]` entry selects.
    pub skill_md: PathBuf,
    pub origin: Origin,
    /// `Err` with the reason when the frontmatter could not be read.
    pub frontmatter: Result<Frontmatter, String>,
    /// For an installed skill, the record `puffin skill add` left (`.puffin-origin.toml`).
    pub record: Option<install::Origin>,
}

impl Skill {
    /// For an installed skill, how it stands against its record. This hashes every file of the
    /// skill, so it is asked for where it is shown or acted on, not at every start.
    pub fn provenance(&self) -> Option<Provenance> {
        (self.origin == Origin::Installed).then(|| install::provenance(&self.dir))
    }

    pub fn description(&self) -> &str {
        self.frontmatter.as_ref().map(|frontmatter| frontmatter.description.as_str()).unwrap_or_default()
    }
}

/// Whether the model is offered a skill, and if not, why.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Status {
    Offered,
    /// The preflight: `needs gh`, `for macos only`.
    Unavailable(String),
    /// `disable-model-invocation` in its own agent.
    ManualOnly,
    /// A skill of higher precedence has the same name; the text names whose that one is and where.
    Shadowed(String),
    /// `puffin skill disable`.
    Disabled,
    /// `puffin skill source <agent> off`.
    SourceOff,
    /// Left out so the catalogue fits its budget.
    OverBudget,
    /// Its `SKILL.md` could not be read as a skill.
    Unreadable(String),
}

impl Status {
    /// The words `puffin skill list` shows, and the reason written beside a `config.toml` entry.
    pub fn reason(&self) -> String {
        match self {
            Status::Offered => "offered".to_string(),
            Status::Unavailable(why) => format!("unavailable: {why}"),
            Status::ManualOnly => "manual-only in its own agent".to_string(),
            Status::Shadowed(by) => format!("shadowed by {by}"),
            Status::Disabled => "switched off with puffin skill disable".to_string(),
            Status::SourceOff => "its source is switched off".to_string(),
            Status::OverBudget => "left out to fit the catalogue budget".to_string(),
            Status::Unreadable(why) => format!("not a readable skill: {why}"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Entry {
    pub skill: Skill,
    pub status: Status,
    /// `needs FOO_API_KEY`: shown, never a reason to withhold.
    pub notes: Vec<String>,
    /// What its catalogue line costs, in tokens.
    pub cost: usize,
}

/// The machine a plan is made for. Tests build their own.
#[derive(Debug, Clone)]
pub struct Machine {
    pub codex_home: PathBuf,
    pub home: PathBuf,
    pub cwd: PathBuf,
    pub host: Host,
    /// The context window the launcher advertises for the served model; `None` when it is not
    /// known (a `puffin skill list` with no model server), which leaves the budget out.
    pub context_window: Option<u64>,
}

impl Machine {
    pub fn skills_root(&self) -> PathBuf {
        self.codex_home.join("skills")
    }
}

#[derive(Debug, Clone)]
pub struct Plan {
    pub entries: Vec<Entry>,
    /// The catalogue budget in tokens, when the context window is known.
    pub limit: Option<usize>,
    /// The repository being worked in, when it has a `.claude/skills` or `.gemini/skills`.
    pub repository: Option<Repository>,
}

/// A repository with skill folders of other agents', and whether they may be linked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Repository {
    pub root: PathBuf,
    pub trusted: bool,
}

impl Plan {
    pub fn offered(&self) -> impl Iterator<Item = &Entry> {
        self.entries.iter().filter(|entry| entry.status == Status::Offered)
    }

    /// Tokens the offered skills' catalogue lines cost.
    pub fn used(&self) -> usize {
        self.offered().map(|entry| entry.cost).sum()
    }

    /// The links each agent's `from-` folder should hold: its offered skills, and nothing else,
    /// so a foreign skill that is not offered needs no `config.toml` entry.
    pub fn link_sets(&self, home: &Path) -> Vec<LinkSet> {
        AGENTS
            .iter()
            .map(|agent| LinkSet {
                agent: agent.id.to_string(),
                source: match agent.scope {
                    Scope::Home => Source::Folder(home.join(agent.folder)),
                    Scope::Repository => Source::AnyRepository(agent.folder),
                },
                links: self
                    .offered()
                    .filter(|entry| entry.skill.origin == Origin::Linked(agent))
                    .map(|entry| (link_name(&entry.skill), entry.skill.dir.clone()))
                    .collect(),
            })
            .collect()
    }

    /// The `SKILL.md` files to switch off in `config.toml`, each with its reason: skills Codex
    /// finds by itself (everything but the linked ones) that must not be offered.
    pub fn config_entries(&self) -> Vec<(PathBuf, String)> {
        let offered: BTreeSet<&Path> = self.offered().map(|entry| entry.skill.skill_md.as_path()).collect();
        self.entries
            .iter()
            .filter(|entry| !matches!(entry.skill.origin, Origin::Linked(_)))
            .filter(|entry| !matches!(entry.status, Status::Offered | Status::Unreadable(_)))
            // One file reached by two routes is one skill to Codex: never switch off an offered one.
            .filter(|entry| !offered.contains(entry.skill.skill_md.as_path()))
            .map(|entry| (entry.skill.skill_md.clone(), entry.status.reason()))
            .collect()
    }

    /// Whether the model is offered a skill written for another agent: a linked one, or an
    /// installed one whose record names a source other than OpenAI's catalogue. The glossary of
    /// spec §5 is added to the prompt only then.
    pub fn offers_foreign_skill(&self) -> bool {
        self.offered().any(|entry| match (&entry.skill.origin, &entry.skill.record) {
            (Origin::Linked(_), _) => true,
            (Origin::Installed, Some(record)) => record.source != "adopted" && !record.source.starts_with("openai/"),
            _ => false,
        })
    }

    /// How many linked skills were left out for the budget, per agent, in the order they were
    /// dropped.
    pub fn left_out(&self) -> Vec<(&'static str, usize)> {
        let mut counts: Vec<(&'static str, usize)> = Vec::new();
        for entry in self.entries.iter().rev() {
            if let (Status::OverBudget, Origin::Linked(agent)) = (&entry.status, &entry.skill.origin) {
                match counts.iter_mut().find(|(id, _)| *id == agent.id) {
                    Some((_, count)) => *count += 1,
                    None => counts.push((agent.id, 1)),
                }
            }
        }
        counts
    }

    /// Finds a skill by name, or by the path of its folder or its `SKILL.md`. An offered one is
    /// preferred when several share the name.
    pub fn find(&self, wanted: &str) -> Option<&Entry> {
        let matches = |entry: &&Entry| {
            entry.skill.name == wanted
                || entry.skill.dir == Path::new(wanted)
                || entry.skill.skill_md == Path::new(wanted)
        };
        self.offered().find(matches).or_else(|| self.entries.iter().find(matches))
    }
}

/// A file name for a skill's link: its name, or its folder's when the name could not be one.
fn link_name(skill: &Skill) -> String {
    if !skill.name.is_empty() && !skill.name.starts_with('.') && !skill.name.contains(['/', '\\', '\0']) {
        skill.name.clone()
    } else {
        skill.dir.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
    }
}

/// The folders holding a `SKILL.md` below `root`, sorted, hidden folders skipped, links followed,
/// to `max_depth` levels. A skill's own subfolders are not searched for more skills.
pub fn skill_dirs(root: &Path, max_depth: usize) -> Vec<PathBuf> {
    fn walk(dir: &Path, depth: usize, max_depth: usize, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        let mut children: Vec<PathBuf> = entries
            .flatten()
            .filter(|entry| !entry.file_name().to_string_lossy().starts_with('.'))
            .map(|entry| entry.path())
            .filter(|path| path.is_dir())
            .collect();
        children.sort();
        for child in children {
            if child.join("SKILL.md").is_file() {
                out.push(child);
            } else if depth < max_depth {
                walk(&child, depth + 1, max_depth, out);
            }
        }
    }
    let mut out = Vec::new();
    walk(root, 1, max_depth, &mut out);
    out
}

fn load(dir: &Path, origin: Origin) -> Skill {
    let skill_md = dir.join("SKILL.md");
    let frontmatter = std::fs::read_to_string(&skill_md)
        .map_err(|error| error.to_string())
        .and_then(|text| frontmatter::parse(&text));
    let folder = dir.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
    Skill {
        name: frontmatter.as_ref().ok().and_then(|frontmatter| frontmatter.name.clone()).unwrap_or(folder),
        record: if origin == Origin::Installed { install::Origin::read(dir) } else { None },
        skill_md: std::fs::canonicalize(&skill_md).unwrap_or(skill_md),
        dir: dir.to_path_buf(),
        origin,
        frontmatter,
    }
}

/// The folders from the project root (the nearest one holding `.git`) down to `cwd`, root first.
fn project_dirs(cwd: &Path) -> Vec<&Path> {
    let Some(project) = cwd.ancestors().find(|dir| dir.join(".git").exists()) else {
        return Vec::new();
    };
    let mut dirs: Vec<&Path> = cwd.ancestors().take_while(|dir| dir.starts_with(project)).collect();
    dirs.reverse();
    dirs
}

/// The skill folders `relative` names in each folder from the project root down to `cwd`, as
/// Codex looks for `.agents/skills`.
fn repository_folders(cwd: &Path, relative: &[&str]) -> Vec<PathBuf> {
    project_dirs(cwd)
        .iter()
        .flat_map(|dir| relative.iter().map(|folder| dir.join(folder)))
        .filter(|root| root.is_dir())
        .collect()
}

/// The repository skill roots Codex scans for `cwd`: `.agents/skills` and `.codex/skills`.
fn repository_roots(cwd: &Path) -> Vec<PathBuf> {
    repository_folders(cwd, &[".agents/skills", ".codex/skills"])
}

/// The repository `cwd` is in, when another agent's skill folder is in it.
fn foreign_repository(machine: &Machine) -> Option<Repository> {
    let folders: Vec<&str> =
        AGENTS.iter().filter(|agent| agent.scope == Scope::Repository).map(|agent| agent.folder).collect();
    if repository_folders(&machine.cwd, &folders).is_empty() {
        return None;
    }
    let root = project_dirs(&machine.cwd).first()?.to_path_buf();
    let trusted = is_trusted(&machine.codex_home, &root);
    Some(Repository { root, trusted })
}

/// Whether the user has trusted `root` in `puffin`: `[projects."<path>"] trust_level = "trusted"`
/// in `$CODEX_HOME/config.toml`, for the repository or, in a linked worktree, for the repository
/// it belongs to (which is the key Codex records). It is the code index's rule (spec §8.5):
/// nothing inside the repository can grant it, since a clone or the agent could write it there.
pub fn is_trusted(codex_home: &Path, root: &Path) -> bool {
    let Ok(text) = std::fs::read_to_string(codex_home.join("config.toml")) else { return false };
    let Ok(table) = text.parse::<toml::Table>() else { return false };
    let Some(projects) = table.get("projects").and_then(toml::Value::as_table) else { return false };
    let canonical = |path: &Path| std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let mut roots = vec![canonical(root)];
    if let Some(main) = main_worktree(root) {
        roots.push(canonical(&main));
    }
    projects.iter().any(|(path, entry)| {
        roots.contains(&canonical(Path::new(path)))
            && entry.get("trust_level").and_then(toml::Value::as_str) == Some("trusted")
    })
}

/// For a linked worktree, whose `.git` is a file saying `gitdir: <main>/.git/worktrees/<name>`,
/// the main worktree's root.
fn main_worktree(root: &Path) -> Option<PathBuf> {
    let text = std::fs::read_to_string(root.join(".git")).ok()?;
    let gitdir = PathBuf::from(text.strip_prefix("gitdir:")?.trim());
    let gitdir = if gitdir.is_absolute() { gitdir } else { root.join(gitdir) };
    let worktrees = gitdir.parent()?;
    (worktrees.file_name()? == "worktrees").then_some(())?;
    let dot_git = worktrees.parent()?;
    (dot_git.file_name()? == ".git").then(|| dot_git.parent().map(Path::to_path_buf))?
}

/// Finds every skill and decides which are offered.
pub fn plan(machine: &Machine, settings: &Settings) -> Plan {
    let skills_root = machine.skills_root();
    let mut skills: Vec<Skill> = Vec::new();
    let mut add = |dirs: Vec<PathBuf>, origin: Origin| {
        skills.extend(dirs.iter().map(|dir| load(dir, origin.clone())));
    };
    for root in repository_roots(&machine.cwd) {
        add(skill_dirs(&root, CODEX_SCAN_DEPTH), Origin::Repository);
    }
    add(
        skill_dirs(&skills_root, CODEX_SCAN_DEPTH)
            .into_iter()
            .filter(|dir| {
                let top = dir.strip_prefix(&skills_root).ok().and_then(|rest| rest.components().next());
                !top.is_some_and(|top| top.as_os_str().to_string_lossy().starts_with(LINK_PREFIX))
            })
            .collect(),
        Origin::Installed,
    );
    add(skill_dirs(&machine.home.join(".agents/skills"), CODEX_SCAN_DEPTH), Origin::SharedAgents);
    let repository = foreign_repository(machine);
    for agent in AGENTS {
        match agent.scope {
            Scope::Home => add(skill_dirs(&machine.home.join(agent.folder), FOREIGN_SCAN_DEPTH), Origin::Linked(agent)),
            // An untrusted repository's skills are not even read: the plan shows the folder as
            // waiting for trust, not its skills.
            Scope::Repository if repository.as_ref().is_some_and(|repository| repository.trusted) => {
                for folder in repository_folders(&machine.cwd, &[agent.folder]) {
                    add(skill_dirs(&folder, FOREIGN_SCAN_DEPTH), Origin::Linked(agent));
                }
            }
            Scope::Repository => {}
        }
    }
    add(skill_dirs(&skills_root.join(".system"), CODEX_SCAN_DEPTH), Origin::Bundled);

    let mut entries: Vec<Entry> = Vec::new();
    let mut seen_files: BTreeSet<PathBuf> = BTreeSet::new();
    let mut winners: BTreeMap<String, String> = BTreeMap::new();
    for skill in skills {
        // The same file through a second route (a folder linked into another) is the same skill.
        if !seen_files.insert(skill.skill_md.clone()) {
            continue;
        }
        let paths = [skill.dir.as_path(), skill.skill_md.as_path()];
        let forced = Settings::names(&settings.enabled, &skill.name, &paths);
        let mut notes = Vec::new();
        let status = match &skill.frontmatter {
            Err(error) => Status::Unreadable(error.clone()),
            Ok(_) if Settings::names(&settings.disabled, &skill.name, &paths) => Status::Disabled,
            Ok(_) if matches!(&skill.origin, Origin::Linked(agent) if settings.sources_off.contains(agent.id)) => {
                Status::SourceOff
            }
            Ok(frontmatter) => {
                let (verdict, found) = preflight::check(frontmatter, &machine.host);
                notes = found;
                match verdict {
                    Verdict::Unavailable(why) if !forced => Status::Unavailable(why),
                    Verdict::ManualOnly if !forced => Status::ManualOnly,
                    // `enable` also overrides a collision: the user asked for this one by path.
                    _ if forced => Status::Offered,
                    _ => match winners.get(&skill.name) {
                        Some(winner) => Status::Shadowed(winner.clone()),
                        None => Status::Offered,
                    },
                }
            }
        };
        if status == Status::Offered {
            // Where the winner is, not only whose it is: two folders of one agent can hold the same
            // name (Claude Code keeps one `synced/` folder per account).
            let place = crate::report::short_path(&skill.dir, &machine.home);
            winners.insert(skill.name.clone(), format!("{} ({place})", skill.origin.label()));
        }
        let cost = budget::line_cost(&skill.name, skill.description(), &skill.skill_md.to_string_lossy());
        entries.push(Entry { skill, status, notes, cost });
    }

    let limit = machine.context_window.map(budget::limit);
    if let Some(limit) = limit {
        let mut used: usize = entries.iter().filter(|entry| entry.status == Status::Offered).map(|entry| entry.cost).sum();
        // Linked skills go first, from the last source backwards; Codex's own are not the
        // launcher's to drop.
        for entry in entries.iter_mut().rev() {
            if used <= limit {
                break;
            }
            if entry.status == Status::Offered && matches!(entry.skill.origin, Origin::Linked(_)) {
                entry.status = Status::OverBudget;
                used -= entry.cost;
            }
        }
    }
    Plan { entries, limit, repository }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::Fixture;
    use crate::testing::write_skill;

    #[test]
    fn skills_are_found_in_every_place_and_foreign_ones_through_their_categories() {
        let fixture = Fixture::new("catalog-found");
        fixture.skill(".puffin/skills/pdf", "pdf", "");
        fixture.skill(".agents/skills/caveman", "caveman", "");
        fixture.skill(".claude/skills/synced/internal-comms", "internal-comms", "");
        fixture.skill(".claude/skills/direct", "direct", "");
        fixture.skill(".gemini/skills/gem", "gem", "");
        fixture.skill(".openclaw/skills/claw", "claw", "");
        fixture.skill(".hermes/skills/research/arxiv", "arxiv", "");
        fixture.skill(".puffin/skills/.system/skill-creator", "skill-creator", "");
        // Hidden folders, staging and quarantine are never skills.
        fixture.skill(".claude/skills/.hidden/secret", "secret", "");
        fixture.skill(".puffin/skills/.staging/t1/half", "half", "");
        fixture.skill(".puffin/skills/.quarantine/t0/from-claude/planted", "planted", "");
        // A skill's own subfolders are not searched.
        fixture.skill(".claude/skills/direct/examples/nested", "nested", "");

        let plan = plan(&fixture.machine(), &Settings::default());
        let found: Vec<(String, String)> =
            plan.entries.iter().map(|entry| (entry.skill.name.clone(), entry.skill.origin.label())).collect();
        let expect = |name: &str, label: &str| (name.to_string(), label.to_string());
        assert_eq!(
            found,
            vec![
                expect("pdf", "puffin"),
                expect("caveman", "~/.agents/skills"),
                expect("direct", "Claude Code"),
                expect("internal-comms", "Claude Code"),
                expect("gem", "Gemini CLI"),
                expect("claw", "OpenClaw"),
                expect("arxiv", "Hermes Agent"),
                expect("skill-creator", "bundled"),
            ]
        );
        assert!(plan.entries.iter().all(|entry| entry.status == Status::Offered));

        // One link per offered foreign skill, flattened: Hermes's category and Claude's `synced`
        // level are gone from the link's name.
        let sets = plan.link_sets(&fixture.home);
        let links = |agent: &str| -> Vec<(String, PathBuf)> {
            sets.iter()
                .find(|set| set.agent == agent)
                .map(|set| set.links.clone().into_iter().collect())
                .unwrap_or_default()
        };
        assert_eq!(
            links("claude"),
            vec![
                ("direct".to_string(), fixture.home.join(".claude/skills/direct")),
                ("internal-comms".to_string(), fixture.home.join(".claude/skills/synced/internal-comms")),
            ]
        );
        assert_eq!(links("hermes"), vec![("arxiv".to_string(), fixture.home.join(".hermes/skills/research/arxiv"))]);
        assert!(plan.config_entries().is_empty());
        assert!(plan.offers_foreign_skill());
    }

    #[test]
    fn a_skill_that_cannot_work_here_is_not_offered() {
        let fixture = Fixture::new("catalog-preflight");
        fixture.skill(".hermes/skills/apple/imessage", "imessage", "platforms: [macos]\n");
        fixture.skill(".openclaw/skills/gh-tool", "gh-tool", "metadata:\n  openclaw:\n    requires:\n      bins: [gh-not-installed]\n");
        fixture.skill(".claude/skills/deploy", "deploy", "disable-model-invocation: true\n");
        fixture.skill(".agents/skills/mac-only", "mac-only", "platforms: [macos]\n");
        fixture.skill(".agents/skills/keyed", "keyed", "required_environment_variables: [FOO_API_KEY]\n");
        std::fs::create_dir_all(fixture.home.join(".claude/skills/broken")).unwrap_or_default();
        std::fs::write(fixture.home.join(".claude/skills/broken/SKILL.md"), "no frontmatter").unwrap_or_default();

        let plan = plan(&fixture.machine(), &Settings::default());
        let status = |name: &str| plan.find(name).map(|entry| entry.status.reason()).unwrap_or_default();
        assert_eq!(status("imessage"), "unavailable: for macos only");
        assert_eq!(status("gh-tool"), "unavailable: needs gh-not-installed");
        assert_eq!(status("deploy"), "manual-only in its own agent");
        assert_eq!(status("mac-only"), "unavailable: for macos only");
        assert!(status("broken").starts_with("not a readable skill"));
        // A missing variable is a note; the skill is offered.
        assert_eq!(status("keyed"), "offered");
        assert_eq!(plan.find("keyed").map(|entry| entry.notes.clone()), Some(vec!["needs FOO_API_KEY".to_string()]));

        // Foreign ones get no link; the one Codex scans by itself gets a config entry.
        assert!(plan.link_sets(&fixture.home).iter().all(|set| set.links.is_empty()));
        assert_eq!(
            plan.config_entries(),
            vec![(fixture.home.join(".agents/skills/mac-only/SKILL.md"), "unavailable: for macos only".to_string())]
        );
        assert!(!plan.offers_foreign_skill());

        // The user's `enable` overrides the preflight and the manual-only mark.
        let settings = Settings { enabled: ["deploy".to_string(), "mac-only".to_string()].into(), ..Settings::default() };
        let forced = super::plan(&fixture.machine(), &settings);
        assert_eq!(forced.find("deploy").map(|entry| entry.status.clone()), Some(Status::Offered));
        assert!(forced.config_entries().is_empty());
    }

    #[test]
    fn one_name_is_offered_once_in_the_order_of_precedence() {
        let fixture = Fixture::new("catalog-collisions");
        fixture.repository_skill(".agents/skills/lint", "lint");
        for place in [".puffin/skills/lint", ".agents/skills/lint", ".claude/skills/lint", ".hermes/skills/dev/lint"] {
            fixture.skill(place, "lint", "");
        }
        for place in [".puffin/skills/pdf", ".claude/skills/pdf", ".hermes/skills/docs/pdf", ".puffin/skills/.system/pdf"] {
            fixture.skill(place, "pdf", "");
        }
        fixture.skill(".gemini/skills/only-here", "only-here", "");
        fixture.skill(".openclaw/skills/only-here", "only-here", "");

        let plan = plan(&fixture.machine(), &Settings::default());
        let offered: Vec<(String, String)> =
            plan.offered().map(|entry| (entry.skill.name.clone(), entry.skill.origin.label())).collect();
        assert_eq!(
            offered,
            vec![
                ("lint".to_string(), "this repository".to_string()),
                ("pdf".to_string(), "puffin".to_string()),
                ("only-here".to_string(), "Gemini CLI".to_string()),
            ]
        );
        let shadowed = plan.entries.iter().filter(|entry| matches!(entry.status, Status::Shadowed(_))).count();
        assert_eq!(shadowed, 8);
        // Losers Codex scans by itself are switched off in config.toml; foreign losers get no link.
        let switched_off: Vec<PathBuf> = plan.config_entries().into_iter().map(|(path, _)| path).collect();
        assert_eq!(
            switched_off,
            vec![
                fixture.home.join(".puffin/skills/lint/SKILL.md"),
                fixture.home.join(".agents/skills/lint/SKILL.md"),
                fixture.home.join(".puffin/skills/.system/pdf/SKILL.md"),
            ]
        );
        let links: Vec<String> = plan.link_sets(&fixture.home).into_iter().flat_map(|set| set.links.into_keys()).collect();
        assert_eq!(links, vec!["only-here".to_string()]);

        // `disable` withholds the winner; the next in line is then offered.
        let settings = Settings { disabled: [fixture.home.join(".puffin/skills/pdf").to_string_lossy().into_owned()].into(), ..Settings::default() };
        let plan = super::plan(&fixture.machine(), &settings);
        assert_eq!(plan.find("pdf").map(|entry| entry.skill.origin.label()), Some("Claude Code".to_string()));
    }

    #[test]
    fn a_switched_off_source_is_not_linked() {
        let fixture = Fixture::new("catalog-source-off");
        fixture.skill(".claude/skills/a", "a", "");
        fixture.skill(".hermes/skills/x/b", "b", "");
        let settings = Settings { sources_off: ["hermes".to_string()].into(), ..Settings::default() };
        let plan = plan(&fixture.machine(), &settings);
        assert_eq!(plan.find("b").map(|entry| entry.status.clone()), Some(Status::SourceOff));
        let linked: Vec<String> = plan.link_sets(&fixture.home).into_iter().flat_map(|set| set.links.into_keys()).collect();
        assert_eq!(linked, vec!["a".to_string()]);
    }

    #[test]
    fn linked_skills_are_left_out_from_the_last_source_until_the_catalogue_fits() {
        let fixture = Fixture::new("catalog-budget");
        fixture.skill(".puffin/skills/mine", "mine", "");
        for index in 0..4 {
            fixture.skill(&format!(".claude/skills/c{index}"), &format!("c{index}"), "");
            fixture.skill(&format!(".hermes/skills/cat/h{index}"), &format!("h{index}"), "");
        }
        let mut machine = fixture.machine();
        let unlimited = plan(&machine, &Settings::default());
        assert_eq!(unlimited.limit, None);
        assert_eq!(unlimited.offered().count(), 9);
        let each = unlimited.entries[0].cost;
        assert!(unlimited.entries.iter().all(|entry| entry.cost.abs_diff(each) <= 3), "fixture lines differ too much");

        // Room for about six lines: the window whose 2% is that many tokens.
        machine.context_window = Some((unlimited.used() as u64 * 100 / 2) * 6 / 9);
        let fitted = plan(&machine, &Settings::default());
        assert!(fitted.used() <= fitted.limit.unwrap_or(0));
        let dropped: Vec<String> = fitted
            .entries
            .iter()
            .filter(|entry| entry.status == Status::OverBudget)
            .map(|entry| entry.skill.name.clone())
            .collect();
        assert!(dropped.len() >= 3 && dropped.iter().take(4).all(|name| name.starts_with('h')), "{dropped:?}");
        assert_eq!(fitted.left_out().first().map(|(agent, _)| *agent), Some("hermes"));
        assert_eq!(fitted.find("mine").map(|entry| entry.status.clone()), Some(Status::Offered));

        // A window too small even without linked skills: they are all left out, the rest stays.
        machine.context_window = Some(100);
        let tiny = plan(&machine, &Settings::default());
        assert_eq!(tiny.offered().map(|entry| entry.skill.name.clone()).collect::<Vec<_>>(), vec!["mine".to_string()]);
        assert!(tiny.used() > tiny.limit.unwrap_or(0));
    }

    #[cfg(unix)]
    #[test]
    fn the_same_file_through_two_routes_is_one_skill() {
        let fixture = Fixture::new("catalog-same-file");
        fixture.skill(".claude/skills/shared", "shared", "");
        std::fs::create_dir_all(fixture.home.join(".agents/skills")).unwrap_or_default();
        std::os::unix::fs::symlink(fixture.home.join(".claude/skills/shared"), fixture.home.join(".agents/skills/shared"))
            .unwrap_or_default();
        let plan = plan(&fixture.machine(), &Settings::default());
        assert_eq!(plan.entries.len(), 1);
        assert_eq!(plan.entries[0].skill.origin, Origin::SharedAgents);
        assert!(plan.config_entries().is_empty());
        assert!(plan.link_sets(&fixture.home).iter().all(|set| set.links.is_empty()));
    }

    #[test]
    fn an_installed_skill_from_another_catalogue_counts_as_foreign() {
        let fixture = Fixture::new("catalog-foreign-installed");
        fixture.skill(".puffin/skills/pdf", "pdf", "");
        let record = |source: &str| {
            install::Origin { source: source.to_string(), installed: "2026-10-02".into(), ..install::Origin::default() }
                .write(&fixture.home.join(".puffin/skills/pdf"))
                .unwrap_or_default();
        };
        assert!(!plan(&fixture.machine(), &Settings::default()).offers_foreign_skill());
        record("openai/pdf");
        assert!(!plan(&fixture.machine(), &Settings::default()).offers_foreign_skill());
        record("adopted");
        assert!(!plan(&fixture.machine(), &Settings::default()).offers_foreign_skill());
        record("anthropic/pdf");
        assert!(plan(&fixture.machine(), &Settings::default()).offers_foreign_skill());
    }

    fn trust(fixture: &Fixture, root: &Path) {
        // A literal string: a Windows path's backslashes are not TOML escapes there.
        let config = format!("[projects.'{}']\ntrust_level = \"trusted\"\n", root.display());
        std::fs::write(fixture.codex_home.join("config.toml"), config).unwrap_or_default();
    }

    #[test]
    fn a_repositorys_claude_and_gemini_skills_are_linked_only_once_it_is_trusted() {
        let fixture = Fixture::new("catalog-repository");
        fixture.repository_skill(".claude/skills/release", "release");
        fixture.repository_skill(".gemini/skills/review", "review");
        fixture.repository_skill(".agents/skills/own", "own");
        // The same name in the user's own Claude folder loses to the repository's.
        fixture.skill(".claude/skills/release", "release", "");

        let untrusted = plan(&fixture.machine(), &Settings::default());
        assert_eq!(untrusted.repository, Some(Repository { root: fixture.cwd.clone(), trusted: false }));
        assert!(untrusted.find("review").is_none(), "an untrusted repository's skills are not read");
        assert_eq!(untrusted.find("release").map(|entry| entry.skill.origin.label()), Some("Claude Code".to_string()));
        assert!(untrusted.link_sets(&fixture.home).iter().all(|set| !set.agent.starts_with("repo-") || set.links.is_empty()));

        trust(&fixture, &fixture.cwd);
        let trusted = plan(&fixture.machine(), &Settings::default());
        assert_eq!(trusted.repository.as_ref().map(|repository| repository.trusted), Some(true));
        let label = |name: &str| trusted.find(name).map(|entry| entry.skill.origin.label()).unwrap_or_default();
        assert_eq!(label("release"), "this repository's .claude/skills");
        assert_eq!(label("review"), "this repository's .gemini/skills");
        assert_eq!(label("own"), "this repository");
        let sets = trusted.link_sets(&fixture.home);
        let links = |agent: &str| sets.iter().find(|set| set.agent == agent).map(|set| set.links.clone()).unwrap_or_default();
        assert_eq!(links("repo-claude").get("release"), Some(&fixture.cwd.join(".claude/skills/release")));
        assert_eq!(links("repo-gemini").get("review"), Some(&fixture.cwd.join(".gemini/skills/review")));
        assert!(links("claude").is_empty(), "the user's own release is shadowed");
        assert!(trusted.offers_foreign_skill());

        // `puffin skill source repo off` switches both folders off.
        let settings = Settings { sources_off: sources("repo").iter().map(|agent| agent.id.to_string()).collect(), ..Settings::default() };
        let off = plan(&fixture.machine(), &settings);
        assert_eq!(off.find("review").map(|entry| entry.status.clone()), Some(Status::SourceOff));
        assert_eq!(off.find("release").map(|entry| entry.skill.origin.label()), Some("Claude Code".to_string()));
    }

    #[test]
    fn a_linked_worktree_is_trusted_through_its_main_repository() {
        let fixture = Fixture::new("catalog-worktree");
        let main = fixture.cwd.join("main");
        let tree = fixture.cwd.join("tree");
        std::fs::create_dir_all(main.join(".git/worktrees/tree")).unwrap_or_default();
        std::fs::create_dir_all(&tree).unwrap_or_default();
        std::fs::write(tree.join(".git"), format!("gitdir: {}\n", main.join(".git/worktrees/tree").display())).unwrap_or_default();
        write_skill(&tree.join(".claude/skills/fmt"), "fmt", "");
        let mut machine = fixture.machine();
        machine.cwd = tree.clone();

        assert!(plan(&machine, &Settings::default()).find("fmt").is_none());
        trust(&fixture, &main);
        assert_eq!(plan(&machine, &Settings::default()).find("fmt").map(|entry| entry.status.clone()), Some(Status::Offered));

        // A repository's own files cannot grant trust: only `$CODEX_HOME/config.toml` is read.
        let other = fixture.cwd.join("other");
        std::fs::create_dir_all(other.join(".git")).unwrap_or_default();
        write_skill(&other.join(".claude/skills/planted"), "planted", "");
        let grant = format!("[projects.\"{}\"]\ntrust_level = \"trusted\"\n", other.display());
        for file in ["config.toml", ".codex/config.toml", ".puffin/config.toml"] {
            std::fs::create_dir_all(other.join(file).parent().unwrap_or(&other)).unwrap_or_default();
            std::fs::write(other.join(file), &grant).unwrap_or_default();
        }
        machine.cwd = other;
        assert!(plan(&machine, &Settings::default()).find("planted").is_none());
    }
}
