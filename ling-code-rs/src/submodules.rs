//! Submodules: your code, or what you ask for (spec §4.3).
//!
//! A submodule is indexed only when it belongs to the same organisation as the repository that
//! contains it, or when the user asks for it by name. "The same organisation" is two tests,
//! because the URL alone is not enough (a fork of someone else's project lives under your
//! namespace too):
//! - **namespace**: the submodule's host and owner equal the superproject's;
//! - **authorship**: most of the submodule's recent commits are by the superproject's authors.
//!
//! The user's own choice (`ling-code submodules include|exclude|auto <path>`) is kept in
//! `$CODEX_HOME/ling-code.toml`, outside the workspace: a file in the repository can be shipped
//! in a clone, and the agent can write it from inside the workspace-write sandbox.
//!
//! Nothing here is cached. The tests are a handful of local git calls and are recomputed whenever
//! they are needed; a cache would live in the workspace, where the agent could edit it.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};

use crate::config::Settings;
use crate::paths::{self, git, Repo};

/// Commits of the superproject whose authors and committers are "ours".
const OUR_COMMITS: &str = "-1000";
/// Commits of a submodule the authorship test reads.
const THEIR_COMMITS: &str = "-200";

/// Mail providers whose domain says nothing about an organisation.
const PUBLIC_MAIL_DOMAINS: &[&str] = &[
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "yahoo.com",
    "ymail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    "pm.me",
    "gmx.com",
    "gmx.de",
    "gmx.net",
    "mail.com",
    "aol.com",
    "qq.com",
    "163.com",
    "126.com",
    "yandex.ru",
    "yandex.com",
    "fastmail.com",
    "hey.com",
    "tutanota.com",
    "users.noreply.github.com",
    "users.noreply.gitlab.com",
    "localhost",
];

/// GitHub's web-flow committer: it commits for everyone, so it identifies no one.
const WEB_FLOW: &str = "noreply@github.com";

/// The outcome of the namespace test.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Namespace {
    Same,
    Different,
    /// Either side has no remote, or the URL is a local path: authorship alone decides.
    Unavailable,
}

/// Why a submodule is, or is not, indexed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reason {
    Yours,
    YoursByAuthorship,
    ThirdParty,
    OtherOrganisation,
    Unknown,
    TooLarge(usize),
    NotCheckedOut,
    ParentNotIndexed,
    IncludedByYou,
    ExcludedByYou,
}

impl Reason {
    pub fn label(&self) -> String {
        match self {
            Reason::Yours => "yours".into(),
            Reason::YoursByAuthorship => "yours (by authorship)".into(),
            Reason::ThirdParty => "third-party".into(),
            Reason::OtherOrganisation => "other organisation".into(),
            Reason::Unknown => "unknown".into(),
            Reason::TooLarge(files) => format!("too large: {files} files"),
            Reason::NotCheckedOut => "not checked out".into(),
            Reason::ParentNotIndexed => "parent not indexed".into(),
            Reason::IncludedByYou => "included by you".into(),
            Reason::ExcludedByYou => "excluded by you".into(),
        }
    }
}

/// One submodule, its decision and the evidence for it.
#[derive(Debug, Clone)]
pub struct Submodule {
    /// Path relative to the superproject's root (nested submodules included).
    pub path: String,
    /// Its name in the `.gitmodules` that declares it.
    pub name: String,
    pub indexed: bool,
    pub reason: Reason,
    pub namespace: Namespace,
    /// `host/owner` of the superproject and of the submodule, where a remote says.
    pub our_namespace: Option<String>,
    pub their_namespace: Option<String>,
    /// Commits read by the authorship test, and how many of them are ours.
    pub commits: usize,
    pub ours: usize,
    /// Tracked files.
    pub files: usize,
    /// Corroborating only: `shallow = true` in `.gitmodules`, and the tag `HEAD` is detached at.
    pub shallow: bool,
    pub tag: Option<String>,
    /// Whether the executing indexers may treat it as the superproject is treated: both tests
    /// passed with a namespace to compare. Including is not trusting.
    pub inherits_trust: bool,
}

impl Submodule {
    /// The evidence, as `ling-code submodules` prints it.
    pub fn evidence(&self) -> String {
        let mut parts = Vec::new();
        match (self.namespace, &self.our_namespace, &self.their_namespace) {
            (Namespace::Same, Some(ours), Some(theirs)) if ours == theirs => {
                parts.push(format!("namespace {ours}"))
            }
            (Namespace::Same, Some(ours), _) => {
                parts.push(format!("namespace {ours} (relative URL)"))
            }
            (Namespace::Different, Some(ours), Some(theirs)) => {
                parts.push(format!("namespace {theirs}, yours is {ours}"))
            }
            _ => parts.push("no namespace to compare".to_string()),
        }
        if self.reason != Reason::NotCheckedOut {
            parts.push(format!("{} of {} commits yours", self.ours, self.commits));
            parts.push(format!("{} files", self.files));
        }
        if self.shallow {
            parts.push("shallow".to_string());
        }
        if let Some(tag) = &self.tag {
            parts.push(format!("detached at {tag}"));
        }
        parts.join("; ")
    }
}

/// `host/owner` and the repository name of a remote URL, with any credentials dropped.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RemoteId {
    pub host: String,
    pub owner: String,
    /// `host/owner/repo`: the only form of a URL that is ever printed.
    pub display: String,
}

impl RemoteId {
    pub fn namespace(&self) -> String {
        format!("{}/{}", self.host, self.owner)
    }
}

/// Parses `https://`, `ssh://`, `git://` and scp-like `user@host:path` remotes. A local path or a
/// `file://` URL names no organisation and yields None.
pub fn parse_remote(url: &str) -> Option<RemoteId> {
    let url = url.trim();
    let (authority, path) = if let Some((scheme, rest)) = url.split_once("://") {
        if !matches!(
            scheme.to_ascii_lowercase().as_str(),
            "https" | "http" | "ssh" | "git" | "git+ssh" | "ssh+git"
        ) {
            return None;
        }
        let (authority, path) = rest.split_once('/')?;
        (authority, path)
    } else {
        // scp-like: `[user@]host:path`, where the colon comes before any slash.
        let colon = url.find(':')?;
        if url.starts_with('/') || url.starts_with('.') || url[..colon].contains('/') {
            return None;
        }
        (&url[..colon], &url[colon + 1..])
    };
    // Credentials never leave this function.
    let host = authority.rsplit('@').next()?;
    let host = match host.strip_prefix('[') {
        Some(v6) => v6.split(']').next()?.to_string(),
        None => host.split(':').next()?.to_string(),
    }
    .to_ascii_lowercase();
    let mut segments: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
    // Azure DevOps over ssh puts a protocol version first: `git@ssh.dev.azure.com:v3/org/project/repo`.
    if host == "ssh.dev.azure.com" && segments.first() == Some(&"v3") {
        segments.remove(0);
    }
    if host.is_empty() || segments.len() < 2 {
        return None;
    }
    let owner = segments[0].trim_start_matches('~').to_ascii_lowercase();
    let repo = segments[segments.len() - 1].trim_end_matches(".git");
    Some(RemoteId {
        display: format!("{host}/{owner}/{repo}"),
        host,
        owner,
    })
}

fn is_relative_url(url: &str) -> bool {
    url.starts_with("./") || url.starts_with("../")
}

/// A relative submodule URL resolved as git does against the declaring repository's remote:
/// each `../` drops one component of `base`, so `../../other/x` leaves the owner.
fn resolve_relative_url(base: &str, relative: &str) -> String {
    let mut base = base.trim().trim_end_matches('/');
    let mut rest = relative;
    loop {
        if let Some(r) = rest.strip_prefix("./") {
            rest = r;
        } else if let Some(r) = rest.strip_prefix("../") {
            base = &base[..base.rfind(['/', ':']).unwrap_or(0)];
            rest = r;
        } else {
            break;
        }
    }
    format!("{base}/{rest}")
}

/// The superproject's remote: that of the current branch, else `origin`, else the only remote.
fn superproject_remote(root: &Path) -> Option<RemoteId> {
    parse_remote(&remote_url(root)?)
}

/// The URL of the remote git resolves relative submodule URLs against, `insteadOf` applied.
fn remote_url(root: &Path) -> Option<String> {
    let remotes: Vec<String> = git(root, &["remote"])
        .ok()?
        .lines()
        .map(str::to_string)
        .collect();
    let branch_remote = git(root, &["symbolic-ref", "--quiet", "--short", "HEAD"])
        .ok()
        .and_then(|branch| {
            git(
                root,
                &[
                    "config",
                    "--get",
                    &format!("branch.{}.remote", branch.trim()),
                ],
            )
            .ok()
        })
        .map(|r| r.trim().to_string())
        .filter(|r| remotes.contains(r));
    let remote = branch_remote
        .or_else(|| remotes.iter().find(|r| *r == "origin").cloned())
        .or_else(|| (remotes.len() == 1).then(|| remotes[0].clone()))?;
    // `ls-remote --get-url` applies `url.<base>.insteadOf` and makes no network request.
    git(root, &["ls-remote", "--get-url", &remote])
        .ok()
        .map(|u| u.trim().to_string())
}

/// The addresses and organisation domains of the people who write the superproject.
#[derive(Debug, Clone, Default)]
pub struct Authors {
    pub addresses: BTreeSet<String>,
    pub domains: BTreeSet<String>,
}

impl Authors {
    pub fn from_addresses(addresses: impl IntoIterator<Item = String>) -> Authors {
        let addresses: BTreeSet<String> = addresses
            .into_iter()
            .map(|a| a.trim().to_ascii_lowercase())
            .filter(|a| !a.is_empty() && a != WEB_FLOW)
            .collect();
        let domains = addresses
            .iter()
            .filter_map(|a| a.rsplit_once('@').map(|(_, domain)| domain.to_string()))
            .filter(|d| !PUBLIC_MAIL_DOMAINS.contains(&d.as_str()))
            .collect();
        Authors { addresses, domains }
    }

    fn of(root: &Path) -> Authors {
        let mut addresses: Vec<String> = git(root, &["log", OUR_COMMITS, "--format=%ae%n%ce"])
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect();
        if let Ok(own) = git(root, &["config", "--get", "user.email"]) {
            addresses.push(own);
        }
        Authors::from_addresses(addresses)
    }

    pub fn wrote(&self, address: &str) -> bool {
        let address = address.trim().to_ascii_lowercase();
        self.addresses.contains(&address)
            || address
                .rsplit_once('@')
                .is_some_and(|(_, domain)| self.domains.contains(domain))
    }
}

/// The decision of §4.3's table, before the size guard and the user's choice.
pub fn decide(namespace: Namespace, ours: usize, commits: usize) -> (bool, Reason) {
    let authorship = commits > 0 && ours * 2 > commits;
    match (namespace, authorship) {
        (Namespace::Same, true) => (true, Reason::Yours),
        (Namespace::Same, false) => (false, Reason::ThirdParty),
        (Namespace::Different, _) => (false, Reason::OtherOrganisation),
        (Namespace::Unavailable, true) => (true, Reason::YoursByAuthorship),
        (Namespace::Unavailable, false) if commits == 0 => (false, Reason::Unknown),
        (Namespace::Unavailable, false) => (false, Reason::ThirdParty),
    }
}

/// The user's recorded choice for a submodule.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Choice {
    Include,
    Exclude,
}

/// `$CODEX_HOME/ling-code.toml`: a file of Mightling's own, not Codex's `config.toml`.
pub fn choices_file() -> PathBuf {
    paths::codex_home().join("ling-code.toml")
}

fn project_key(repo: &Repo) -> String {
    repo.main_root
        .canonicalize()
        .unwrap_or_else(|_| repo.main_root.clone())
        .to_string_lossy()
        .into_owned()
}

/// The user's choices for this repository, keyed by submodule path or name.
pub fn choices(repo: &Repo) -> Vec<(String, Choice)> {
    choices_in(&choices_file(), repo)
}

fn choices_in(file: &Path, repo: &Repo) -> Vec<(String, Choice)> {
    let Some(table) = std::fs::read_to_string(file)
        .ok()
        .and_then(|t| t.parse::<toml::Table>().ok())
    else {
        return Vec::new();
    };
    let Some(submodules) = table
        .get("projects")
        .and_then(|p| p.get(project_key(repo)))
        .and_then(|p| p.get("submodules"))
        .and_then(toml::Value::as_table)
    else {
        return Vec::new();
    };
    submodules
        .iter()
        .filter_map(|(key, value)| match value.as_str() {
            Some("include") => Some((key.clone(), Choice::Include)),
            Some("exclude") => Some((key.clone(), Choice::Exclude)),
            _ => None,
        })
        .collect()
}

/// Records (or, with `None`, removes) the user's choice for one submodule.
///
/// Fails where `$CODEX_HOME` cannot be written, which is the case inside Codex's sandbox: what is
/// indexed is the user's decision, so the agent cannot change it.
pub fn set_choice(repo: &Repo, submodule: &Submodule, choice: Option<Choice>) -> Result<()> {
    let file = choices_file();
    let mut table = match std::fs::read_to_string(&file) {
        Ok(text) => text
            .parse::<toml::Table>()
            .with_context(|| format!("{} is not valid TOML", file.display()))?,
        Err(_) => toml::Table::new(),
    };
    let as_table = |value: &mut toml::Value| -> Result<()> {
        if !value.is_table() {
            bail!("{} has an unexpected shape", file.display());
        }
        Ok(())
    };
    let projects = table
        .entry("projects")
        .or_insert_with(|| toml::Value::Table(toml::Table::new()));
    as_table(projects)?;
    let project = projects
        .as_table_mut()
        .unwrap()
        .entry(project_key(repo))
        .or_insert_with(|| toml::Value::Table(toml::Table::new()));
    as_table(project)?;
    let submodules = project
        .as_table_mut()
        .unwrap()
        .entry("submodules")
        .or_insert_with(|| toml::Value::Table(toml::Table::new()));
    as_table(submodules)?;
    let submodules = submodules.as_table_mut().unwrap();
    // One entry per submodule, under its path; an older entry under its name goes, unless that
    // name is another submodule's path.
    if !list(&repo.root)
        .iter()
        .any(|entry| entry.1 == submodule.name)
    {
        submodules.remove(&submodule.name);
    }
    submodules.remove(&submodule.path);
    match choice {
        Some(Choice::Include) => submodules.insert(
            submodule.path.clone(),
            toml::Value::String("include".into()),
        ),
        Some(Choice::Exclude) => submodules.insert(
            submodule.path.clone(),
            toml::Value::String("exclude".into()),
        ),
        None => None,
    };
    let text = toml::to_string(&table)?;
    let write = || -> std::io::Result<()> {
        if let Some(parent) = file.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let staging = file.with_extension(format!("toml.{}.tmp", std::process::id()));
        std::fs::write(&staging, &text)?;
        std::fs::rename(&staging, &file).inspect_err(|_| {
            let _ = std::fs::remove_file(&staging);
        })
    };
    write().with_context(|| format!("writing {}", file.display()))
}

/// The submodules `dir`'s `.gitmodules` declares: `(name, path relative to dir)`.
///
/// Read from the file, not from `git submodule status`, which starts a git process per submodule
/// and costs 30 ms here on its own: this runs on every query.
fn declared(dir: &Path) -> Vec<(String, String)> {
    if !dir.join(".gitmodules").is_file() {
        return Vec::new();
    }
    let Ok(listing) = git(
        dir,
        &[
            "config",
            "-f",
            ".gitmodules",
            "--null",
            "--get-regexp",
            r"^submodule\..*\.path$",
        ],
    ) else {
        return Vec::new();
    };
    // `--null` ends the key with a newline and the entry with a NUL: a name may hold a space.
    listing
        .split('\0')
        .filter_map(|entry| {
            let (key, value) = entry.split_once('\n')?;
            let name = key.strip_prefix("submodule.")?.strip_suffix(".path")?;
            let value = value.trim_end_matches('/');
            // The file is the repository's own text: a path must stay inside it.
            let inside = !value.is_empty()
                && Path::new(value)
                    .components()
                    .all(|c| matches!(c, std::path::Component::Normal(_)));
            inside.then(|| (name.to_string(), value.to_string()))
        })
        .collect()
}

/// Every submodule under `root`, nested ones included: `(name, path relative to root, the
/// directory of the repository that declares it, checked out)`, in path order. A submodule is
/// checked out when its directory has a `.git` (git writes a file there that points at the
/// superproject's `modules/`).
fn list(root: &Path) -> Vec<(String, String, PathBuf, bool)> {
    let mut out = Vec::new();
    let real_root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    let mut pending: Vec<(PathBuf, String)> = vec![(root.to_path_buf(), String::new())];
    while let Some((dir, prefix)) = pending.pop() {
        for (name, rel) in declared(&dir) {
            let path = format!("{prefix}{rel}");
            let checkout = root.join(&path);
            // A link the repository ships may point anywhere; a checkout lies inside the root.
            let checked_out = checkout.join(".git").exists()
                && checkout
                    .canonicalize()
                    .is_ok_and(|real| real.starts_with(&real_root));
            if checked_out {
                pending.push((checkout, format!("{path}/")));
            }
            out.push((name, path, dir.clone(), checked_out));
        }
    }
    out.sort_by(|a, b| a.1.cmp(&b.1));
    out
}

/// Whether the settings' `code_index_submodule_max_files` may be honoured: only from a file
/// outside the repository, whichever way that file was found.
fn max_files(repo: &Repo, settings: &Settings) -> usize {
    let inside = settings.config_path.as_ref().is_some_and(|path| {
        let path = path.canonicalize().unwrap_or_else(|_| path.clone());
        [&repo.root, &repo.main_root].iter().any(|root| {
            path.starts_with(root.canonicalize().unwrap_or_else(|_| root.to_path_buf()))
        })
    });
    if inside {
        Settings::default().submodule_max_files
    } else {
        settings.submodule_max_files
    }
}

/// Every submodule of the repository with its decision (spec §4.3), in path order.
pub fn evaluate(repo: &Repo, settings: &Settings) -> Vec<Submodule> {
    evaluate_with(repo, max_files(repo, settings), &choices(repo))
}

/// [`evaluate`] with the size guard and the user's choices given.
pub fn evaluate_with(
    repo: &Repo,
    max_files: usize,
    choices: &[(String, Choice)],
) -> Vec<Submodule> {
    if !repo.is_git {
        return Vec::new();
    }
    let listed = list(&repo.root);
    if listed.is_empty() {
        return Vec::new();
    }
    let paths: Vec<String> = listed.iter().map(|entry| entry.1.clone()).collect();
    let our_remote = superproject_remote(&repo.root);
    let authors = Authors::of(&repo.root);
    let mut out: Vec<Submodule> = Vec::new();
    for (name, path, parent_dir, checked_out) in listed {
        // The declaring repository: the superproject, or the submodule this one is nested in.
        let parent = out
            .iter()
            .filter(|s| path.starts_with(&format!("{}/", s.path)))
            .max_by_key(|s| s.path.len())
            .cloned();
        let dir = repo.root.join(&path);
        let mut submodule = Submodule {
            path: path.clone(),
            name: name.clone(),
            indexed: false,
            reason: Reason::Unknown,
            namespace: Namespace::Unavailable,
            our_namespace: our_remote.as_ref().map(RemoteId::namespace),
            their_namespace: None,
            commits: 0,
            ours: 0,
            files: 0,
            shallow: git(
                &parent_dir,
                &[
                    "config",
                    "-f",
                    ".gitmodules",
                    "--bool",
                    "--get",
                    &format!("submodule.{name}.shallow"),
                ],
            )
            .map(|v| v.trim() == "true")
            .unwrap_or(false),
            tag: None,
            inherits_trust: false,
        };
        // A submodule nested in one that is not indexed is never examined, whatever is recorded.
        if parent.as_ref().is_some_and(|p| !p.indexed) {
            submodule.reason = Reason::ParentNotIndexed;
            out.push(submodule);
            continue;
        }
        if !checked_out {
            submodule.reason = Reason::NotCheckedOut;
            out.push(submodule);
            continue;
        }
        // Namespace: the effective URL (the user's own override first), expanded by git.
        let raw_url = git(
            &parent_dir,
            &["config", "--get", &format!("submodule.{name}.url")],
        )
        .or_else(|_| {
            git(
                &parent_dir,
                &[
                    "config",
                    "-f",
                    ".gitmodules",
                    "--get",
                    &format!("submodule.{name}.url"),
                ],
            )
        })
        .map(|u| u.trim().to_string())
        .unwrap_or_default();
        submodule.namespace = if raw_url.is_empty() || our_remote.is_none() {
            Namespace::Unavailable
        } else {
            let expanded = if is_relative_url(&raw_url) {
                // Against the declaring repository's remote; none makes the namespace unknown.
                remote_url(&parent_dir)
                    .map(|base| resolve_relative_url(&base, &raw_url))
                    .unwrap_or_default()
            } else {
                git(&parent_dir, &["ls-remote", "--get-url", &raw_url])
                    .map(|u| u.trim().to_string())
                    .unwrap_or(raw_url)
            };
            match (parse_remote(&expanded), &our_remote) {
                (Some(theirs), Some(ours)) => {
                    submodule.their_namespace = Some(theirs.namespace());
                    if theirs.host == ours.host && theirs.owner == ours.owner {
                        Namespace::Same
                    } else {
                        Namespace::Different
                    }
                }
                _ => Namespace::Unavailable,
            }
        };
        // Authorship: author addresses of its recent non-merge commits; its merges when it has
        // nothing else (a shallow clone is decided by the commits it has).
        let log = |extra: &[&str]| -> Vec<String> {
            let mut args = vec!["log", THEIR_COMMITS];
            args.extend_from_slice(extra);
            args.push("--format=%ae");
            git(&dir, &args)
                .unwrap_or_default()
                .lines()
                .map(str::to_string)
                .collect()
        };
        let mut addresses = log(&["--no-merges"]);
        if addresses.is_empty() {
            addresses = log(&[]);
        }
        submodule.commits = addresses.len();
        submodule.ours = addresses.iter().filter(|a| authors.wrote(a)).count();
        submodule.files = git(&dir, &["ls-files", "-z"])
            .map(|out| out.split('\0').filter(|f| !f.is_empty()).count())
            .unwrap_or(0);
        if git(&dir, &["symbolic-ref", "--quiet", "HEAD"]).is_err() {
            submodule.tag = git(&dir, &["describe", "--tags", "--exact-match", "HEAD"])
                .ok()
                .map(|t| t.trim().to_string())
                .filter(|t| !t.is_empty());
        }
        let (indexed, reason) = decide(submodule.namespace, submodule.ours, submodule.commits);
        submodule.inherits_trust = indexed && submodule.namespace == Namespace::Same;
        (submodule.indexed, submodule.reason) = (indexed, reason);
        // The size guard applies to automatic inclusion only.
        if submodule.indexed && submodule.files > max_files {
            submodule.indexed = false;
            submodule.reason = Reason::TooLarge(submodule.files);
        }
        // An explicit choice beats the tests.
        match choices
            .iter()
            .find(|(key, _)| *key == submodule.path)
            // An older entry under the name, unless that name is another submodule's path.
            .or_else(|| {
                choices
                    .iter()
                    .find(|(key, _)| *key == submodule.name && !paths.contains(key))
            })
            .map(|(_, choice)| *choice)
        {
            Some(Choice::Include) => {
                submodule.indexed = true;
                submodule.reason = Reason::IncludedByYou;
            }
            Some(Choice::Exclude) => {
                submodule.indexed = false;
                submodule.reason = Reason::ExcludedByYou;
            }
            None => {}
        }
        out.push(submodule);
    }
    out
}

/// The paths of the submodules that are indexed.
pub fn included(decisions: &[Submodule]) -> Vec<String> {
    decisions
        .iter()
        .filter(|s| s.indexed)
        .map(|s| s.path.clone())
        .collect()
}

/// The line every answer and the prompt block carry when a submodule is left out:
/// `codex/ (third-party)`, at most three names and `+N more`. None when all are indexed.
pub fn not_indexed_line(decisions: &[Submodule]) -> Option<String> {
    let left_out: Vec<&Submodule> = decisions.iter().filter(|s| !s.indexed).collect();
    if left_out.is_empty() {
        return None;
    }
    let mut names: Vec<String> = left_out
        .iter()
        .take(3)
        .map(|s| format!("{}/ ({})", s.path, s.reason.label()))
        .collect();
    if left_out.len() > 3 {
        names.push(format!("+{} more", left_out.len() - 3));
    }
    Some(names.join(", "))
}

/// Finds a submodule by its path or name; an unknown one is an error that lists the known ones.
pub fn find<'a>(decisions: &'a [Submodule], wanted: &str) -> Result<&'a Submodule> {
    let wanted = wanted.trim_end_matches('/');
    match decisions
        .iter()
        .find(|s| s.path == wanted)
        .or_else(|| decisions.iter().find(|s| s.name == wanted))
    {
        Some(submodule) => Ok(submodule),
        None if decisions.is_empty() => bail!("this repository has no submodules"),
        None => bail!(
            "no submodule `{wanted}`; this repository has: {}",
            decisions
                .iter()
                .map(|s| s.path.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

/// `ling-code submodules`: every submodule with its decision, its reason and the evidence.
pub fn listing(repo: &Repo, decisions: &[Submodule]) -> Vec<String> {
    if decisions.is_empty() {
        return vec!["this repository has no submodules".to_string()];
    }
    let mut lines = Vec::new();
    for submodule in decisions {
        let decision = if submodule.indexed {
            "indexed"
        } else {
            "not indexed"
        };
        lines.push(format!(
            "{}  {decision} ({})  {}",
            submodule.path,
            submodule.reason.label(),
            submodule.evidence()
        ));
        if submodule.indexed {
            // The executing indexers run on a scratch copy of the submodule, on its own trust.
            let sub_repo = Repo {
                root: repo.root.join(&submodule.path),
                main_root: repo.root.join(&submodule.path),
                is_git: true,
            };
            let executing: BTreeSet<&str> = crate::index::plan::detect(&sub_repo)
                .iter()
                .filter(|t| t.kind == crate::index::host::Kind::Executing)
                .map(|t| t.indexer)
                .collect();
            if !executing.is_empty() {
                // Including is not trusting: the indexers that build the project say on whose
                // trust they would run.
                let dir = repo.main_root.join(&submodule.path);
                let superproject = crate::config::is_trusted(&repo.main_root);
                let trust = if crate::config::is_trusted(&dir) {
                    "trusted by its own entry".to_string()
                } else if submodule.inherits_trust && superproject {
                    "it inherits this repository's trust".to_string()
                } else if submodule.inherits_trust {
                    "it would inherit this repository's trust, which is not given".to_string()
                } else {
                    format!("not run: it needs its own trust entry (`[projects.\"{}\"] trust_level = \"trusted\"` in $CODEX_HOME/config.toml)", dir.display())
                };
                lines.push(format!(
                    "  {}: on a scratch copy of the submodule, with `ling-code index --exact`; {trust}",
                    executing.into_iter().collect::<Vec<_>>().join(", ")
                ));
            }
        }
    }
    lines.push(
        "change: `ling-code submodules include|exclude|auto <path>` (in your own shell)"
            .to_string(),
    );
    lines
}

/// `ling-code submodules include|exclude|auto <path>`.
pub fn change(repo: &Repo, settings: &Settings, verb: &str, wanted: &str) -> Result<Vec<String>> {
    let choice = match verb {
        "include" => Some(Choice::Include),
        "exclude" => Some(Choice::Exclude),
        "auto" => None,
        other => bail!("`{other}` is not one of include, exclude, auto"),
    };
    let decisions = evaluate(repo, settings);
    let submodule = find(&decisions, wanted)?.clone();
    if let Err(error) = set_choice(repo, &submodule, choice) {
        bail!(
            "what is indexed is your decision: run `ling-code submodules {verb} {}` in your own shell ({error:#})",
            submodule.path
        );
    }
    let after = evaluate(repo, settings);
    let now = find(&after, &submodule.path)?;
    let mut lines = vec![format!(
        "{}  {} ({})",
        now.path,
        if now.indexed {
            "indexed"
        } else {
            "not indexed"
        },
        now.reason.label()
    )];
    if now.indexed != submodule.indexed {
        lines.push(if crate::paths::indexing_here() {
            "re-index: `ling-code index` (a running ling session does it by itself)".to_string()
        } else {
            format!("re-index: {}", crate::paths::BUILT_ON_LINUX)
        });
    }
    Ok(lines)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relative_urls_resolve_against_the_remote_as_git_does() {
        let owner =
            |base: &str, rel: &str| parse_remote(&resolve_relative_url(base, rel)).map(|r| r.owner);
        let https = "https://example.com/acme/super.git";
        let scp = "git@example.com:acme/super.git";
        assert_eq!(owner(https, "../lib.git").as_deref(), Some("acme"));
        assert_eq!(owner(scp, "./../lib.git").as_deref(), Some("acme"));
        assert_eq!(
            owner(https, "../../other/lib.git").as_deref(),
            Some("other")
        );
        assert_eq!(owner(scp, "../../other/lib.git"), None);
        assert_eq!(owner(https, "../../../../../x/y"), None);
    }

    #[test]
    fn remotes_of_every_form_give_host_and_owner() {
        let id = |url: &str| parse_remote(url).map(|r| (r.host, r.owner, r.display));
        let expect = |host: &str, owner: &str, repo: &str| {
            Some((
                host.to_string(),
                owner.to_string(),
                format!("{host}/{owner}/{repo}"),
            ))
        };
        assert_eq!(
            id("https://github.com/DGXcoder/fano.git"),
            expect("github.com", "dgxcoder", "fano")
        );
        assert_eq!(
            id("git@GitHub.com:dgxcoder/codex.git"),
            expect("github.com", "dgxcoder", "codex")
        );
        assert_eq!(
            id("ssh://git@gitlab.example.com:2222/group/sub/project.git"),
            expect("gitlab.example.com", "group", "project")
        );
        assert_eq!(
            id("git://host.example/owner/repo"),
            expect("host.example", "owner", "repo")
        );
        assert_eq!(
            id("git@ssh.dev.azure.com:v3/org/project/repo"),
            expect("ssh.dev.azure.com", "org", "repo")
        );
        assert_eq!(
            id("https://dev.azure.com/org/project/_git/repo"),
            expect("dev.azure.com", "org", "repo")
        );
        // A local path or a file URL names no organisation.
        for local in [
            "/srv/git/repo.git",
            "./sibling",
            "../sibling",
            "file:///srv/git/repo.git",
            "C:/repo",
        ] {
            assert_eq!(id(local), None, "{local}");
        }
    }

    #[test]
    fn credentials_never_leave_the_parser() {
        let id = parse_remote("https://owner:ghp_SECRET@github.com/dgxcoder/fano.git").unwrap();
        assert_eq!(id.display, "github.com/dgxcoder/fano");
        assert!(!format!("{id:?}").contains("SECRET"));
    }

    #[test]
    fn a_remote_without_a_host_names_no_organisation() {
        for url in [
            "file://server/acme/repo.git",
            "https:///acme/repo.git",
            ":acme/repo.git",
            "git@:acme/repo.git",
        ] {
            assert!(parse_remote(url).is_none(), "{url}");
        }
    }

    #[test]
    fn a_local_path_with_a_colon_is_not_an_scp_remote() {
        // git reads `host:path` as scp-like only when no slash comes before the colon.
        assert!(parse_remote("srv/git:acme/repo.git").is_none());
    }

    #[test]
    fn a_bracketed_host_and_a_padded_url_are_read_as_git_reads_them() {
        let id = |url: &str| parse_remote(url).map(|r| (r.host, r.owner));
        let expect = |host: &str| Some((host.to_string(), "acme".to_string()));
        assert_eq!(
            id("ssh://git@[2001:db8::1]:2222/acme/repo.git"),
            expect("2001:db8::1")
        );
        assert_eq!(
            id(" https://example.com/acme/repo.git\n"),
            expect("example.com")
        );
    }

    #[test]
    fn a_leading_dot_slash_does_not_stop_the_climb() {
        let owner = |rel: &str| {
            parse_remote(&resolve_relative_url(
                "https://example.com/acme/super.git",
                rel,
            ))
            .map(|r| r.owner)
        };
        assert_eq!(owner("./../../other/lib.git").as_deref(), Some("other"));
    }

    #[test]
    fn an_address_is_compared_without_case_or_padding() {
        let authors = Authors::from_addresses(["dev@acme.example".to_string()]);
        assert!(authors.wrote(" Dev@ACME.example\n"));
    }

    #[test]
    fn our_domains_leave_out_public_mail_and_web_flow() {
        let authors = Authors::from_addresses(
            [
                "Owner@Kestrel.example",
                "friend@gmail.com",
                "noreply@github.com",
                "1+bob@users.noreply.github.com",
            ]
            .map(String::from),
        );
        assert!(
            authors.wrote("colleague@kestrel.example"),
            "an organisation domain counts"
        );
        assert!(
            authors.wrote("friend@gmail.com"),
            "an address counts as itself"
        );
        assert!(
            !authors.wrote("stranger@gmail.com"),
            "a public provider's domain does not"
        );
        assert!(
            authors.wrote("1+bob@users.noreply.github.com")
                && !authors.wrote("2+eve@users.noreply.github.com")
        );
        assert!(!authors.wrote("noreply@github.com"));
    }

    #[test]
    fn the_decision_table() {
        use Namespace::*;
        assert_eq!(decide(Same, 200, 200), (true, Reason::Yours));
        // A fork carrying a few of our commits fails the majority test.
        assert_eq!(decide(Same, 4, 200), (false, Reason::ThirdParty));
        assert_eq!(
            decide(Same, 100, 200),
            (false, Reason::ThirdParty),
            "exactly half is not most"
        );
        assert_eq!(
            decide(Different, 200, 200),
            (false, Reason::OtherOrganisation)
        );
        assert_eq!(decide(Unavailable, 3, 4), (true, Reason::YoursByAuthorship));
        assert_eq!(decide(Unavailable, 0, 4), (false, Reason::ThirdParty));
        assert_eq!(decide(Unavailable, 0, 0), (false, Reason::Unknown));
    }

    fn sub(path: &str, indexed: bool, reason: Reason) -> Submodule {
        Submodule {
            path: path.into(),
            name: path.into(),
            indexed,
            reason,
            namespace: Namespace::Same,
            our_namespace: None,
            their_namespace: None,
            commits: 0,
            ours: 0,
            files: 0,
            shallow: false,
            tag: None,
            inherits_trust: false,
        }
    }

    #[test]
    fn the_line_names_at_most_three() {
        assert_eq!(not_indexed_line(&[sub("fano", true, Reason::Yours)]), None);
        assert_eq!(
            not_indexed_line(&[
                sub("codex", false, Reason::ThirdParty),
                sub("fano", true, Reason::Yours)
            ])
            .as_deref(),
            Some("codex/ (third-party)")
        );
        let many: Vec<Submodule> = ["a", "b", "c", "d", "e"]
            .iter()
            .map(|p| sub(p, false, Reason::OtherOrganisation))
            .collect();
        let line = not_indexed_line(&many).unwrap();
        assert!(line.ends_with("c/ (other organisation), +2 more"), "{line}");
    }
}
