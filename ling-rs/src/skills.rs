//! Skills from other agents: the launcher's side of `ling-skills`
//! (specs/DREAMFERENCE_MIGHTLING_SKILLS.md).
//!
//! The crate in `../skills` does everything on disk: which skills there are, which the model is
//! offered, the `from-<agent>` links, the entries that switch a skill off in `config.toml`, and
//! installing one from a folder or a tarball. This module adds what needs the rest of `ling`:
//!
//! - [`start`], run before every session, which brings the links and entries up to date and
//!   returns the tool glossary when a skill written for another agent is offered;
//! - [`run_cli`], `ling skill …`, which never reaches Codex and needs no model server;
//! - the downloads of `ling skill add` and `search` (GitHub, which also serves Hermes's
//!   catalogue, and ClawHub), which follow `/airgapped`: at `on` they are refused before any
//!   request is made.
//!
//! No Codex patch: skills are found by Codex's own loader, unmodified.

use std::io::IsTerminal;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use ling_airgapped::Level;
use ling_skills::Request;
use ling_skills::catalog;
use ling_skills::catalog::Machine;
use ling_skills::install;
use ling_skills::install::Source;
use ling_skills::preflight::Host;
use ling_skills::report;
use ling_skills::settings::Settings;

/// A repository tarball larger than this is not downloaded: a skill is a folder, and the
/// catalogues `ling` knows are a few megabytes.
const MAX_TARBALL_BYTES: u64 = 200 * 1024 * 1024;

/// The catalogues on GitHub that `ling skill search` reads: repository, the folders holding
/// skills with the prefix `ling skill add` takes for them, and how many folders deep a skill may
/// sit (Hermes keeps its skills under a category, sometimes two).
const CATALOGUES: &[(&str, &str, &[(&str, &str)], usize)] = &[
    ("openai", "skills", &[("skills/.curated", "openai"), ("skills/.experimental", "openai")], 1),
    ("anthropics", "skills", &[("skills", "anthropic")], 1),
    ("NousResearch", "hermes-agent", &[("skills", "hermes"), ("optional-skills", "hermes")], 3),
];

/// How many of ClawHub's results `search` shows: its search ranks by meaning, so the tail is
/// rarely what was asked for.
const CLAWHUB_RESULTS: usize = 10;

/// Where the catalogues are: GitHub, and ClawHub, the one that is not a repository. Tests point
/// all of them at a stand-in server.
#[derive(Debug, Clone)]
pub struct GitHub {
    pub api: String,
    pub codeload: String,
    pub token: Option<String>,
    /// ClawHub's API base (`apiBase` in its `/.well-known/clawhub.json`).
    pub clawhub: String,
}

impl GitHub {
    /// github.com, with `GITHUB_TOKEN` or `GH_TOKEN` when one is set (a private repository, or the
    /// unauthenticated rate limit), and clawhub.ai.
    pub fn real() -> GitHub {
        GitHub {
            api: "https://api.github.com".to_string(),
            codeload: "https://codeload.github.com".to_string(),
            token: ["GITHUB_TOKEN", "GH_TOKEN"].iter().find_map(|name| std::env::var(name).ok()).filter(|token| !token.is_empty()),
            clawhub: "https://clawhub.ai".to_string(),
        }
    }

    fn client(&self) -> Result<reqwest::Client, String> {
        reqwest::Client::builder()
            .user_agent("mightling-skill")
            .connect_timeout(Duration::from_secs(15))
            .timeout(Duration::from_secs(300))
            .build()
            .map_err(|error| error.to_string())
    }

    fn authorised(&self, request: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        match &self.token {
            Some(token) => request.bearer_auth(token),
            None => request,
        }
    }

    /// The commit a branch, tag or commit names (`HEAD`, the default branch, when there is none),
    /// so what is installed can be recorded exactly.
    pub async fn commit(&self, owner: &str, repo: &str, reference: Option<&str>) -> Result<String, String> {
        let url = format!("{}/repos/{owner}/{repo}/commits/{}", self.api, reference.unwrap_or("HEAD"));
        let response = self
            .authorised(self.client()?.get(&url).header("Accept", "application/vnd.github.sha"))
            .send()
            .await
            .map_err(|error| format!("could not reach GitHub: {error}"))?;
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        let sha = body.trim();
        if status.is_success() && sha.len() >= 7 && sha.chars().all(|ch| ch.is_ascii_hexdigit()) {
            return Ok(sha.to_string());
        }
        Err(match status.as_u16() {
            404 | 422 => format!("{owner}/{repo}{}: no such repository or reference on GitHub", reference.map(|r| format!("@{r}")).unwrap_or_default()),
            403 | 429 => "GitHub refused the request (rate limit); set GITHUB_TOKEN, or try again later".to_string(),
            code => format!("GitHub answered {code} for {owner}/{repo}"),
        })
    }

    /// The repository at `sha`, as a gzipped tarball.
    pub async fn tarball(&self, owner: &str, repo: &str, sha: &str) -> Result<Vec<u8>, String> {
        let url = format!("{}/{owner}/{repo}/tar.gz/{sha}", self.codeload);
        let mut response = self
            .authorised(self.client()?.get(&url))
            .send()
            .await
            .map_err(|error| format!("could not download {owner}/{repo}: {error}"))?;
        if !response.status().is_success() {
            return Err(format!("GitHub answered {} for the download of {owner}/{repo}", response.status().as_u16()));
        }
        let too_large = || {
            format!(
                "{owner}/{repo} is larger than {} MB to download; clone it and run ling skill add <the skill's folder>",
                MAX_TARBALL_BYTES / (1024 * 1024)
            )
        };
        if response.content_length().is_some_and(|length| length > MAX_TARBALL_BYTES) {
            return Err(too_large());
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|error| format!("the download broke off: {error}"))? {
            bytes.extend_from_slice(&chunk);
            if bytes.len() as u64 > MAX_TARBALL_BYTES {
                return Err(too_large());
            }
        }
        Ok(bytes)
    }
}

/// A skill on ClawHub, as `add` needs it: the latest version and what ClawHub says about it.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ClawHubSkill {
    pub owner: String,
    pub slug: String,
    pub version: String,
    /// ClawHub blocks it as malware, or its scan says malicious: never installed.
    pub malicious: bool,
    /// Anything short of a clean scan: installed only when a person confirms it.
    pub clean: bool,
    /// The verdict in a few words, for the summary and the origin record.
    pub verdict: String,
}

impl GitHub {
    async fn clawhub_json(&self, path: &str, query: &[(&str, &str)]) -> Result<(u16, serde_json::Value), String> {
        let response = self
            .client()?
            .get(format!("{}{path}", self.clawhub))
            .query(query)
            .send()
            .await
            .map_err(|error| format!("could not reach ClawHub: {error}"))?;
        let status = response.status().as_u16();
        let text = response.text().await.unwrap_or_default();
        Ok((status, serde_json::from_str(&text).unwrap_or(serde_json::Value::String(text))))
    }

    /// The skill `clawhub/<owner>/<slug>` names, its latest version, and ClawHub's verdict on that
    /// version: its moderation state, then its security scan (spec §8.7).
    pub async fn clawhub_skill(&self, owner: Option<&str>, slug: &str) -> Result<ClawHubSkill, String> {
        let mut query = Vec::new();
        if let Some(owner) = owner {
            query.push(("owner", owner));
        }
        let (status, body) = self.clawhub_json(&format!("/api/v1/skills/{slug}"), &query).await?;
        let named = format!("clawhub/{}{slug}", owner.map(|owner| format!("{owner}/")).unwrap_or_default());
        match status {
            200 => {}
            _ if body["code"] == "AMBIGUOUS_SKILL_SLUG" => {
                let owners: Vec<String> = body["matches"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|found| Some(format!("clawhub/{}/{slug}", found["ownerHandle"].as_str()?)))
                    .collect();
                return Err(format!("{named}: several ClawHub skills have that name; name the owner: {}", owners.join(", ")));
            }
            404 => return Err(format!("{named}: no such skill on ClawHub")),
            429 => return Err("ClawHub refused the request (rate limit); try again in a minute".to_string()),
            code => return Err(format!("ClawHub answered {code} for {named}")),
        }
        let owner = body["owner"]["handle"].as_str().or(owner).unwrap_or_default().to_string();
        let version = body["latestVersion"]["version"].as_str().unwrap_or_default().to_string();
        if version.is_empty() {
            return Err(format!("{named}: ClawHub lists no version of it to download"));
        }
        let moderation = &body["moderation"];
        let mut skill = ClawHubSkill {
            owner,
            slug: slug.to_string(),
            version,
            malicious: moderation["isMalwareBlocked"] == true || moderation["verdict"] == "malicious",
            ..ClawHubSkill::default()
        };
        let suspicious = moderation["isSuspicious"] == true || moderation["verdict"] == "suspicious";

        let query = [("owner", skill.owner.as_str()), ("version", skill.version.as_str())];
        let (scan, verdict) = match self.clawhub_json(&format!("/api/v1/skills/{slug}/verify"), &query).await {
            Ok((200, body)) => {
                let security = &body["security"];
                let status = security["status"].as_str().unwrap_or("not scanned").to_string();
                let summary = security["summary"].as_str().unwrap_or_default().trim().to_string();
                let words = match security["verdict"].as_str() {
                    Some(verdict) if verdict != status => format!("{status} ({verdict})"),
                    _ => status.clone(),
                };
                (status, if summary.is_empty() { words } else { format!("{words}: {summary}") })
            }
            Ok((code, _)) => ("not scanned".to_string(), format!("no scan result (ClawHub answered {code})")),
            Err(error) => ("not scanned".to_string(), format!("no scan result ({error})")),
        };
        skill.malicious |= scan == "malicious";
        skill.clean = scan == "clean" && !suspicious && !skill.malicious;
        skill.verdict = match (suspicious, moderation["summary"].as_str()) {
            (true, Some(summary)) => format!("flagged suspicious ({summary}); scan {verdict}"),
            (true, None) => format!("flagged suspicious; scan {verdict}"),
            (false, _) => verdict,
        };
        Ok(skill)
    }

    /// The zip of one version of a ClawHub skill.
    pub async fn clawhub_zip(&self, skill: &ClawHubSkill) -> Result<Vec<u8>, String> {
        let query = [("slug", skill.slug.as_str()), ("owner", skill.owner.as_str()), ("version", skill.version.as_str())];
        let mut response = self
            .client()?
            .get(format!("{}/api/v1/download", self.clawhub))
            .query(&query)
            .send()
            .await
            .map_err(|error| format!("could not download from ClawHub: {error}"))?;
        if !response.status().is_success() {
            return Err(format!("ClawHub answered {} for the download of {}/{}", response.status().as_u16(), skill.owner, skill.slug));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|error| format!("the download broke off: {error}"))? {
            bytes.extend_from_slice(&chunk);
            if bytes.len() as u64 > install::MAX_BUNDLE_BYTES {
                return Err(format!("refused: the skill is larger than {} MB", install::MAX_BUNDLE_BYTES / (1024 * 1024)));
            }
        }
        Ok(bytes)
    }

    /// ClawHub's own search: `(source, summary)`, its skills only (it also lists skills.sh's).
    pub async fn clawhub_search(&self, words: &[String]) -> Result<Vec<(String, String)>, String> {
        let limit = (CLAWHUB_RESULTS * 2).to_string();
        let query = words.join(" ");
        let (status, body) = self.clawhub_json("/api/v1/search", &[("q", query.as_str()), ("limit", limit.as_str())]).await?;
        if status != 200 {
            return Err(format!("ClawHub answered {status}"));
        }
        Ok(body["results"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|result| result["install"]["kind"] == "clawhub")
            .filter_map(|result| {
                let reference = result["install"]["reference"].as_str()?;
                let summary = result["native"]["skill"]["summary"].as_str().or(result["summary"].as_str()).unwrap_or_default();
                Some((format!("clawhub/{reference}"), summary.to_string()))
            })
            .take(CLAWHUB_RESULTS)
            .collect())
    }
}

/// This machine, as the skills crate sees it. `None` when there is no home folder to look in.
pub fn machine(codex_home: &Path, context_window: Option<u64>) -> Option<Machine> {
    Some(Machine {
        codex_home: codex_home.to_path_buf(),
        home: std::env::home_dir()?,
        cwd: std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
        host: Host::current(),
        context_window,
    })
}

/// The context window the launcher last advertised, from the catalog it wrote: what the catalogue
/// budget is 2% of. `ling skill` asks no model server.
fn advertised_window(codex_home: &Path) -> Option<u64> {
    let text = std::fs::read_to_string(codex_home.join("model_catalog.json")).ok()?;
    let catalog: serde_json::Value = serde_json::from_str(&text).ok()?;
    catalog["models"][0]["max_context_window"].as_u64()
}

/// The start-up pass. Prints what the user should know and returns the prompt block to append:
/// the tool glossary when a skill written for another agent is offered, otherwise nothing.
pub fn start(codex_home: &Path, interactive: bool, context_window: u64) -> &'static str {
    let Some(machine) = machine(codex_home, Some(context_window)) else { return "" };
    let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S").to_string();
    let started = ling_skills::start(&machine, interactive, &stamp);
    for line in &started.lines {
        crate::notice::say(line);
    }
    if started.glossary { ling_skills::glossary::GLOSSARY } else { "" }
}

/// Runs `ling skill …` from a shell. Returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let codex_home = match codex_utils_home_dir::find_codex_home() {
        Ok(home) => home.as_path().to_path_buf(),
        Err(_) => {
            eprintln!("ling skill: could not resolve CODEX_HOME");
            return 1;
        }
    };
    let Some(machine) = machine(&codex_home, advertised_window(&codex_home)) else {
        eprintln!("ling skill: this account has no home folder to look for skills in");
        return 1;
    };
    let outcome = run(&machine, Request::parse(args), &GitHub::real(), ling_airgapped::resolve_for_command().level).await;
    match outcome {
        Ok(lines) => {
            for line in lines {
                println!("{line}");
            }
            0
        }
        Err((code, message)) => {
            eprintln!("{message}");
            code
        }
    }
}

type Outcome = Result<Vec<String>, (i32, String)>;

fn failed(message: String) -> (i32, String) {
    (1, message)
}

/// Does what `request` asks. `level` is the air-gap level in force for this command.
pub async fn run(machine: &Machine, request: Request, github: &GitHub, level: Level) -> Outcome {
    let settings = Settings::load(&machine.codex_home);
    match request {
        Request::Help => Ok(ling_skills::USAGE.lines().map(str::to_string).collect()),
        Request::Usage(message) => Err((2, format!("{message}\n{}", ling_skills::USAGE))),
        Request::List { all } => Ok(report::list(&catalog::plan(machine, &settings), machine, &settings, all)),
        Request::Show(name) => {
            let plan = catalog::plan(machine, &settings);
            let entry = plan.find(&name).ok_or_else(|| failed(format!("{name}: no such skill (ling skill list --all)")))?;
            Ok(report::show(entry, machine))
        }
        Request::Enable(name) => ling_skills::enable(machine, &name).map_err(failed),
        Request::Disable(name) => ling_skills::disable(machine, &name).map_err(failed),
        Request::Source { agent, on } => ling_skills::set_source(machine, &agent, on).map_err(failed),
        Request::Remove(name) => ling_skills::remove(machine, &name).map_err(failed),
        Request::Adopt(name) => {
            let origin = install::adopt(&machine.skills_root(), &name, &today()).map_err(failed)?;
            Ok(vec![format!("{name} is recorded as known ({} files); a later change to it will be reported.", origin.files.len())])
        }
        Request::Search(words) => {
            refuse_when_sealed(level, "search")?;
            search(github, &words).await.map_err(failed)
        }
        Request::Add { source, yes } => {
            let source = install::parse_source(&source, &machine.cwd).map_err(failed)?;
            // A local folder is a copy, not a download: it works at every level.
            if !matches!(source, Source::Local(_)) {
                refuse_when_sealed(level, "add")?;
            }
            add(machine, github, source, yes).await.map_err(failed)
        }
    }
}

/// At `/airgapped on` nothing is downloaded (spec §6.2 step 1, §8.9).
fn refuse_when_sealed(level: Level, what: &str) -> Result<(), (i32, String)> {
    if level == Level::On {
        return Err(failed(format!(
            "ling skill {what} needs the network, and the air-gap level is on (ling airgapped). \
             Installed skills keep working; a skill in a folder can still be installed with ling skill add <folder>."
        )));
    }
    Ok(())
}

fn today() -> String {
    chrono::Local::now().format("%Y-%m-%d").to_string()
}

async fn search(github: &GitHub, words: &[String]) -> Result<Vec<String>, String> {
    let mut found: Vec<(String, String)> = Vec::new();
    let mut unreachable = Vec::new();
    for (owner, repo, folders, depth) in CATALOGUES {
        let listed = async {
            let sha = github.commit(owner, repo, None).await?;
            install::catalogue_to_depth(github.tarball(owner, repo, &sha).await?.as_slice(), folders, *depth)
        }
        .await;
        match listed {
            Ok(skills) => found.extend(
                skills.into_iter().filter(|skill| skill.matches(words)).map(|skill| (skill.source, skill.description)),
            ),
            Err(error) => unreachable.push(format!("{owner}/{repo}: {error}")),
        }
    }
    match github.clawhub_search(words).await {
        Ok(skills) => found.extend(skills),
        Err(error) => unreachable.push(format!("ClawHub: {error}")),
    }
    let width = found.iter().map(|(source, _)| source.len()).max().unwrap_or(0);
    let mut lines: Vec<String> = found
        .iter()
        .map(|(source, description)| {
            let description: String = description.lines().next().unwrap_or("").chars().take(90).collect();
            format!("{source:<width$}  {description}")
        })
        .collect();
    lines.push(match found.len() {
        0 => format!("No skill in the public skill catalogues or on ClawHub matches {}.", words.join(" ")),
        1 => "1 skill matches. Install it with ling skill add <name>.".to_string(),
        count => format!("{count} skills match. Install one with ling skill add <name>."),
    });
    lines.extend(unreachable.into_iter().map(|error| format!("Not searched: {error}")));
    Ok(lines)
}

async fn add(machine: &Machine, github: &GitHub, source: Source, yes: bool) -> Result<Vec<String>, String> {
    let skills_root = machine.skills_root();
    let staging = install::staging_dir(&skills_root, &format!("{}-{}", std::process::id(), chrono::Local::now().format("%H%M%S")))?;
    let outcome = add_from(machine, github, source, yes, &staging).await;
    let _ = std::fs::remove_dir_all(&staging);
    outcome
}

async fn add_from(machine: &Machine, github: &GitHub, source: Source, yes: bool, staging: &Path) -> Result<Vec<String>, String> {
    let unpacked = staging.join("skill");
    let mut origin = install::Origin { installed: today(), ..install::Origin::default() };
    match &source {
        Source::Local(folder) => {
            install::copy_local(folder, &unpacked)?;
            origin.source = folder.display().to_string();
        }
        Source::GitHub { owner, repo, reference, paths, label } => {
            let sha = github.commit(owner, repo, reference.as_deref()).await?;
            let tarball = github.tarball(owner, repo, &sha).await?;
            let path = install::unpack(tarball.as_slice(), paths, &unpacked)?;
            origin.source = label.clone();
            origin.repository = Some(format!("{owner}/{repo}"));
            origin.path = Some(path);
            origin.commit = Some(sha);
        }
        Source::ClawHub { owner, slug, .. } => {
            let skill = github.clawhub_skill(owner.as_deref(), slug).await?;
            let named = format!("clawhub/{}/{}", skill.owner, skill.slug);
            // Spec §8.7: a skill ClawHub marks malicious is never installed, whatever the flags.
            if skill.malicious {
                return Err(format!("refused: ClawHub marks {named} {} as malicious: {}", skill.version, skill.verdict));
            }
            if !skill.clean && yes {
                return Err(format!(
                    "Not installed: ClawHub's check of {named} {} is not clean ({}). A skill like that is installed \
                     only when you confirm it yourself: run ling skill add {named} without --yes.",
                    skill.version, skill.verdict
                ));
            }
            install::unzip(&github.clawhub_zip(&skill).await?, &unpacked)?;
            origin.source = named;
            origin.version = Some(skill.version);
            origin.verdict = Some(skill.verdict);
        }
    }
    let staged = install::validate(&unpacked)?;
    let destination = machine.skills_root().join(&staged.name);
    if destination.symlink_metadata().is_ok() {
        return Err(format!(
            "{} is already installed ({}); ling skill remove {} first",
            staged.name,
            report::short_path(&destination, &machine.home),
            staged.name
        ));
    }
    let plan = catalog::plan(machine, &Settings::load(&machine.codex_home));
    let mut lines = report::staged(&staged, &origin, &plan, machine);
    if !yes && !confirmed(&lines, &report::short_path(&destination, &machine.home))? {
        return Err("Not installed.".to_string());
    }
    let installed = install::commit(&staged, &machine.skills_root(), origin)?;
    if yes {
        lines.push(String::new());
    } else {
        lines.clear();
    }
    lines.push(format!(
        "Installed {} in {}. The model is offered it from the next ling start (ling skill list).",
        staged.name,
        report::short_path(&installed, &machine.home)
    ));
    Ok(lines)
}

/// Shows what is about to be installed and asks. Without a terminal there is nobody to ask, so
/// the answer has to be given as `--yes`.
fn confirmed(summary: &[String], destination: &str) -> Result<bool, String> {
    for line in summary {
        println!("{line}");
    }
    if !std::io::stdin().is_terminal() {
        return Err("Not installed: nobody to ask. Run it again with --yes after reading the above.".to_string());
    }
    print!("Install into {destination}? [y/N] ");
    let _ = std::io::Write::flush(&mut std::io::stdout());
    let mut answer = String::new();
    std::io::stdin().read_line(&mut answer).map_err(|error| error.to_string())?;
    Ok(matches!(answer.trim().to_ascii_lowercase().as_str(), "y" | "yes"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::io::Write;
    use std::net::TcpListener;

    const SHA: &str = "0123456789abcdef0123456789abcdef01234567";

    /// A stand-in for GitHub and ClawHub: answers each request with the body `route` gives for its
    /// path, and records the paths asked for.
    fn stand_in(route: fn(&str) -> Option<Vec<u8>>) -> (GitHub, std::sync::Arc<std::sync::Mutex<Vec<String>>>) {
        serve(route, None)
    }

    /// [`stand_in`] for a route that also chooses the status.
    fn stand_in_with_status(
        route: fn(&str) -> Option<(u16, Vec<u8>)>,
    ) -> (GitHub, std::sync::Arc<std::sync::Mutex<Vec<String>>>) {
        serve(|_| None, Some(route))
    }

    fn serve(
        route: fn(&str) -> Option<Vec<u8>>,
        with_status: Option<fn(&str) -> Option<(u16, Vec<u8>)>>,
    ) -> (GitHub, std::sync::Arc<std::sync::Mutex<Vec<String>>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap_or_else(|error| panic!("{error}"));
        let address = listener.local_addr().map(|address| address.to_string()).unwrap_or_default();
        let asked = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let log = asked.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { continue };
                let mut buffer = [0u8; 4096];
                let read = stream.read(&mut buffer).unwrap_or(0);
                let request = String::from_utf8_lossy(&buffer[..read]).into_owned();
                let path = request.split_whitespace().nth(1).unwrap_or("").to_string();
                if let Ok(mut log) = log.lock() {
                    log.push(path.clone());
                }
                let reply = match with_status {
                    Some(route) => route(&path),
                    None => route(&path).map(|body| (200, body)),
                };
                let (status, body) = reply.unwrap_or((404, b"Not Found".to_vec()));
                let head = format!("HTTP/1.1 {status} X\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
                let _ = stream.write_all(head.as_bytes());
                let _ = stream.write_all(&body);
            }
        });
        let base = format!("http://{address}");
        let github = GitHub {
            api: format!("{base}/api"),
            codeload: format!("{base}/codeload"),
            token: None,
            clawhub: format!("{base}/clawhub"),
        };
        (github, asked)
    }

    fn tarball(files: &[(&str, &str)]) -> Vec<u8> {
        let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast()));
        for (path, contents) in files {
            let mut header = tar::Header::new_gnu();
            header.set_size(contents.len() as u64);
            header.set_mode(0o644);
            header.set_cksum();
            let _ = builder.append_data(&mut header, path, contents.as_bytes());
        }
        builder.into_inner().and_then(|encoder| encoder.finish()).unwrap_or_default()
    }

    const COMMS: &str = "---\nname: internal-comms\ndescription: Write internal updates.\n---\n# Body\n";
    const ARXIV: &str = "---\nname: arxiv\ndescription: Search arXiv for papers.\n---\n# arXiv\n";

    fn zip_of(files: &[(&str, &str)]) -> Vec<u8> {
        let mut writer = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
        for (name, contents) in files {
            let _ = writer.start_file(*name, zip::write::SimpleFileOptions::default());
            let _ = writer.write_all(contents.as_bytes());
        }
        writer.finish().map(|cursor| cursor.into_inner()).unwrap_or_default()
    }

    /// ClawHub as it answered on 2026-10-03, for three skills of `acme`: one clean, one flagged
    /// suspicious, one blocked as malware; and `pdf`, which several owners publish.
    fn clawhub(path: &str) -> Option<(u16, Vec<u8>)> {
        let json = |value: serde_json::Value| value.to_string().into_bytes();
        let skill = |slug: &str, moderation: serde_json::Value| {
            json(serde_json::json!({
                "skill": {"slug": slug}, "owner": {"handle": "acme"},
                "latestVersion": {"version": "1.2.0"}, "moderation": moderation,
            }))
        };
        let verify = |status: &str, summary: &str| {
            json(serde_json::json!({"ok": status == "clean", "security": {"status": status, "verdict": "benign", "summary": summary}}))
        };
        Some(match path {
            "/clawhub/api/v1/skills/pdf" => (409, json(serde_json::json!({
                "code": "AMBIGUOUS_SKILL_SLUG",
                "matches": [{"ownerHandle": "awspace"}, {"ownerHandle": "thcjp"}],
            }))),
            "/clawhub/api/v1/skills/clean?owner=acme" => (200, skill("clean", serde_json::Value::Null)),
            "/clawhub/api/v1/skills/clean/verify?owner=acme&version=1.2.0" => (200, verify("clean", "A plain guide.")),
            "/clawhub/api/v1/download?slug=clean&owner=acme&version=1.2.0" => (
                200,
                zip_of(&[
                    ("SKILL.md", "---\nname: clean\ndescription: A clean skill.\n---\n"),
                    ("skill-card.md", "card"),
                ]),
            ),
            "/clawhub/api/v1/skills/flagged?owner=acme" => (
                200,
                skill("flagged", serde_json::json!({"isSuspicious": true, "isMalwareBlocked": false, "verdict": "suspicious",
                    "summary": "Detected: suspicious.dynamic_code_execution"})),
            ),
            "/clawhub/api/v1/skills/flagged/verify?owner=acme&version=1.2.0" => (200, verify("suspicious", "Runs downloaded code.")),
            "/clawhub/api/v1/skills/bad?owner=acme" => (
                200,
                skill("bad", serde_json::json!({"isSuspicious": true, "isMalwareBlocked": true, "verdict": "malicious"})),
            ),
            "/clawhub/api/v1/skills/bad/verify?owner=acme&version=1.2.0" => (200, verify("malicious", "Steals tokens.")),
            _ => return None,
        })
    }

    fn anthropic(path: &str) -> Option<Vec<u8>> {
        match path {
            "/api/repos/anthropics/skills/commits/HEAD" | "/api/repos/openai/skills/commits/HEAD" => Some(SHA.as_bytes().to_vec()),
            path if path == format!("/codeload/anthropics/skills/tar.gz/{SHA}") => Some(tarball(&[
                ("skills-0123456/skills/internal-comms/SKILL.md", COMMS),
                ("skills-0123456/skills/internal-comms/examples/3p.md", "Progress, Plans, Problems"),
            ])),
            path if path == format!("/codeload/openai/skills/tar.gz/{SHA}") => Some(tarball(&[(
                "skills-0123456/skills/.curated/pdf/SKILL.md",
                "---\nname: pdf\ndescription: Read and fill PDF forms.\n---\n",
            )])),
            "/api/repos/NousResearch/hermes-agent/commits/HEAD" => Some(SHA.as_bytes().to_vec()),
            path if path == format!("/codeload/NousResearch/hermes-agent/tar.gz/{SHA}") => Some(tarball(&[
                ("hermes-0123456/skills/AGENTS.md", "not a skill"),
                ("hermes-0123456/optional-skills/research/arxiv/SKILL.md", ARXIV),
                ("hermes-0123456/optional-skills/research/arxiv/scripts/fetch.py", "print('x')\n"),
            ])),
            path if path.starts_with("/clawhub/api/v1/search?") => Some(
                serde_json::json!({"results": [
                    {"install": {"kind": "clawhub", "reference": "acme/pdf-tools"},
                     "native": {"skill": {"summary": "Merge and split PDF files."}}},
                    {"install": {"kind": "skills-sh", "reference": "skills-sh:x/y/pdf"}, "summary": "Not ClawHub's."},
                ]})
                .to_string()
                .into_bytes(),
            ),
            _ => None,
        }
    }

    fn scratch_machine(name: &str) -> Machine {
        let root = std::env::temp_dir().join(format!("mightling-skill-cli-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let home = root.join("home");
        std::fs::create_dir_all(home.join(".mightling")).unwrap_or_default();
        std::fs::create_dir_all(root.join("work")).unwrap_or_default();
        Machine {
            codex_home: home.join(".mightling"),
            home,
            cwd: root.join("work"),
            host: Host { os: "linux".to_string(), ..Host::default() },
            context_window: Some(262_144),
        }
    }

    fn request(words: &[&str]) -> Request {
        Request::parse(&words.iter().map(|word| word.to_string()).collect::<Vec<_>>())
    }

    #[tokio::test]
    async fn add_downloads_one_folder_records_the_commit_and_the_model_is_offered_it() {
        let (github, asked) = stand_in(anthropic);
        let machine = scratch_machine("add");
        let lines = run(&machine, request(&["add", "anthropic/internal-comms", "--yes"]), &github, Level::Off).await;
        let lines = lines.unwrap_or_else(|(_, error)| panic!("{error}"));
        assert!(lines[0].starts_with("internal-comms  (anthropic/internal-comms, anthropics/skills@0123456)"), "{lines:?}");
        assert!(lines.last().is_some_and(|line| line.starts_with("Installed internal-comms in ~/.mightling/skills/internal-comms.")), "{lines:?}");
        let installed = machine.skills_root().join("internal-comms");
        assert!(installed.join("examples/3p.md").is_file());
        let origin = install::Origin::read(&installed).unwrap_or_default();
        assert_eq!(origin.commit.as_deref(), Some(SHA));
        assert_eq!(origin.path.as_deref(), Some("skills/internal-comms"));
        // Staging is emptied, and exactly two requests were made: the commit, then the tarball.
        assert_eq!(std::fs::read_dir(machine.skills_root().join(".staging")).map(Iterator::count).unwrap_or(0), 0);
        assert_eq!(asked.lock().map(|asked| asked.len()).unwrap_or(0), 2);

        let plan = catalog::plan(&machine, &Settings::default());
        assert!(plan.find("internal-comms").is_some_and(|entry| entry.status == catalog::Status::Offered));
        assert!(plan.offers_foreign_skill());

        // A second add is refused; remove deletes it; a skill that is not there says so.
        let again = run(&machine, request(&["add", "anthropic/internal-comms", "--yes"]), &github, Level::Off).await;
        assert!(again.is_err_and(|(_, error)| error.contains("already installed")));
        assert!(run(&machine, request(&["remove", "internal-comms"]), &github, Level::Off).await.is_ok());
        assert!(!installed.exists());
        let missing = run(&machine, request(&["add", "anthropic/no-such-skill", "--yes"]), &github, Level::Off).await;
        assert!(missing.is_err_and(|(_, error)| error.contains("no such folder in the repository")));
    }

    #[tokio::test]
    async fn at_airgapped_on_nothing_is_requested() {
        let (github, asked) = stand_in(anthropic);
        let machine = scratch_machine("sealed");
        for words in [vec!["add", "anthropic/internal-comms", "--yes"], vec!["search", "pdf"]] {
            let refused = run(&machine, request(&words), &github, Level::On).await;
            assert!(refused.is_err_and(|(code, error)| code == 1 && error.contains("the air-gap level is on")), "{words:?}");
        }
        assert_eq!(asked.lock().map(|asked| asked.len()).unwrap_or(1), 0);
        assert!(!machine.skills_root().join("internal-comms").exists());

        // A folder on this machine is a copy, not a download, and `off` downloads as before.
        let local = machine.cwd.join("mine");
        std::fs::create_dir_all(&local).unwrap_or_default();
        std::fs::write(local.join("SKILL.md"), "---\nname: mine\ndescription: Mine.\n---\n").unwrap_or_default();
        assert!(run(&machine, request(&["add", "./mine", "--yes"]), &github, Level::On).await.is_ok());
        assert!(machine.skills_root().join("mine/SKILL.md").is_file());
        assert!(run(&machine, request(&["add", "anthropic/internal-comms", "--yes"]), &github, Level::Off).await.is_ok());
    }

    #[tokio::test]
    async fn search_reads_every_catalogue() {
        let (github, _) = stand_in(anthropic);
        let machine = scratch_machine("search");
        // ClawHub's results come from its own search, which ranks by meaning; the stand-in answers
        // every query with the same one.
        let lines = run(&machine, request(&["search", "pdf"]), &github, Level::Off).await.unwrap_or_default();
        assert_eq!(lines, vec![
            "openai/pdf              Read and fill PDF forms.".to_string(),
            "clawhub/acme/pdf-tools  Merge and split PDF files.".to_string(),
            "2 skills match. Install one with ling skill add <name>.".to_string(),
        ]);
        let all = run(&machine, request(&["search", "i"]), &github, Level::Off).await.unwrap_or_default();
        assert!(all.iter().any(|line| line.starts_with("anthropic/internal-comms")), "{all:?}");
        assert!(all.iter().any(|line| line.starts_with("hermes/research/arxiv")), "{all:?}");
        let papers = run(&machine, request(&["search", "arxiv"]), &github, Level::Off).await.unwrap_or_default();
        assert!(papers[0].starts_with("hermes/research/arxiv"), "{papers:?}");
    }

    #[tokio::test]
    async fn without_yes_and_without_a_terminal_nothing_is_installed() {
        let (github, _) = stand_in(anthropic);
        let machine = scratch_machine("unconfirmed");
        // The test harness's stdin may be a terminal when run by hand; then this is not the case
        // under test.
        if std::io::stdin().is_terminal() {
            return;
        }
        let refused = run(&machine, request(&["add", "anthropic/internal-comms"]), &github, Level::Off).await;
        assert!(refused.is_err_and(|(_, error)| error.contains("--yes")));
        assert!(!machine.skills_root().join("internal-comms").exists());
        assert_eq!(std::fs::read_dir(machine.skills_root().join(".staging")).map(Iterator::count).unwrap_or(0), 0);
    }

    #[tokio::test]
    async fn the_local_commands_answer_without_github() {
        let github = GitHub {
            api: "http://127.0.0.1:9".into(),
            codeload: "http://127.0.0.1:9".into(),
            token: None,
            clawhub: "http://127.0.0.1:9".into(),
        };
        let machine = scratch_machine("local");
        let skill = machine.home.join(".claude/skills/comms");
        std::fs::create_dir_all(&skill).unwrap_or_default();
        std::fs::write(skill.join("SKILL.md"), COMMS).unwrap_or_default();
        let list = run(&machine, request(&["list"]), &github, Level::On).await.unwrap_or_default();
        assert!(list[0].starts_with("Skills the model is offered: 1 ("), "{list:?}");
        let show = run(&machine, request(&["show", "internal-comms"]), &github, Level::On).await.unwrap_or_default();
        assert!(show[0].starts_with("internal-comms: offered"), "{show:?}");
        assert!(run(&machine, request(&["source", "claude", "off"]), &github, Level::On).await.is_ok());
        assert!(run(&machine, request(&["show", "nope"]), &github, Level::On).await.is_err());
        let usage = run(&machine, request(&["frobnicate"]), &github, Level::On).await;
        assert!(usage.is_err_and(|(code, error)| code == 2 && error.contains("Usage: ling skill")));
    }

    #[tokio::test]
    async fn a_hermes_skill_is_found_in_its_optional_skills_and_installed() {
        let (github, asked) = stand_in(anthropic);
        let machine = scratch_machine("hermes");
        let lines = run(&machine, request(&["add", "hermes/research/arxiv", "--yes"]), &github, Level::Off).await;
        let lines = lines.unwrap_or_else(|(_, error)| panic!("{error}"));
        assert!(lines[0].starts_with("arxiv  (hermes/research/arxiv, NousResearch/hermes-agent@0123456)"), "{lines:?}");
        let installed = machine.skills_root().join("arxiv");
        assert!(installed.join("scripts/fetch.py").is_file());
        let origin = install::Origin::read(&installed).unwrap_or_default();
        assert_eq!(origin.path.as_deref(), Some("optional-skills/research/arxiv"));
        assert_eq!(asked.lock().map(|asked| asked.len()).unwrap_or(0), 2);
    }

    #[tokio::test]
    async fn a_clawhub_skill_is_installed_only_as_far_as_its_verdict_allows() {
        let (github, asked) = stand_in_with_status(clawhub);
        let machine = scratch_machine("clawhub");

        // Clean: installed with its version and verdict recorded.
        let lines = run(&machine, request(&["add", "clawhub/@acme/clean", "--yes"]), &github, Level::Off).await;
        let lines = lines.unwrap_or_else(|(_, error)| panic!("{error}"));
        assert!(lines[0].starts_with("clean  (clawhub/acme/clean, version 1.2.0)"), "{lines:?}");
        assert!(lines.iter().any(|line| line == "  ClawHub:     clean (benign): A plain guide."), "{lines:?}");
        let origin = install::Origin::read(&machine.skills_root().join("clean")).unwrap_or_default();
        assert_eq!((origin.source.as_str(), origin.version.as_deref()), ("clawhub/acme/clean", Some("1.2.0")));
        assert!(machine.skills_root().join("clean/skill-card.md").is_file());

        // Flagged: not with --yes, and nothing is downloaded.
        let before = asked.lock().map(|asked| asked.len()).unwrap_or(0);
        let flagged = run(&machine, request(&["add", "clawhub/acme/flagged", "--yes"]), &github, Level::Off).await;
        assert!(flagged.is_err_and(|(_, error)| error.contains("not clean") && error.contains("without --yes")), "flagged");
        // Malicious: never, whatever the flags.
        let bad = run(&machine, request(&["add", "clawhub/acme/bad", "--yes"]), &github, Level::Off).await;
        assert!(bad.is_err_and(|(_, error)| error.starts_with("refused: ClawHub marks clawhub/acme/bad 1.2.0 as malicious")));
        let downloads = asked.lock().map(|asked| asked[before..].iter().filter(|path| path.contains("/download")).count());
        assert_eq!(downloads.unwrap_or(1), 0);
        assert!(!machine.skills_root().join("flagged").exists() && !machine.skills_root().join("bad").exists());

        // A slug several owners publish: the owners are named.
        let ambiguous = run(&machine, request(&["add", "clawhub/pdf", "--yes"]), &github, Level::Off).await;
        assert!(ambiguous.is_err_and(|(_, error)| error.contains("clawhub/awspace/pdf, clawhub/thcjp/pdf")));
        // At `on`, ClawHub is not asked either.
        let before = asked.lock().map(|asked| asked.len()).unwrap_or(0);
        let sealed = run(&machine, request(&["add", "clawhub/acme/clean", "--yes"]), &github, Level::On).await;
        assert!(sealed.is_err_and(|(_, error)| error.contains("the air-gap level is on")));
        assert_eq!(asked.lock().map(|asked| asked.len()).unwrap_or(0), before);
    }

    /// The start-up pass writes its entries into `config.toml`, and `configure_codex_home` then
    /// parses and rewrites the same file with toml_edit at every start. If that round trip ever
    /// reshaped an entry, the launcher would stop recognising its own entries and pile up stale
    /// ones.
    #[test]
    fn the_launchers_config_entries_survive_the_config_rewrite_and_are_still_removable() {
        use ling_skills::config_entries;
        let entries = vec![
            (PathBuf::from("/h/.agents/skills/mac-only/SKILL.md"), "unavailable: for macos only".to_string()),
            (PathBuf::from("/h/.mightling/skills/.system/skill-creator/SKILL.md"), "shadowed by Claude Code (~/.claude/skills/x)".to_string()),
        ];
        let catalog = Path::new("/h/.mightling/model_catalog.json");
        for user in [
            // A fresh home: the launcher's tables do not exist yet, so toml_edit appends them
            // after the entries.
            "",
            // A settled file with a table, a user entry and a comment of the user's own.
            "model = \"m\"\n\n[features]\ncode_mode = true\n\n# mine\n[[skills.config]]\nname = \"x\"\nenabled = false\n",
        ] {
            let with_entries = config_entries::with_managed(user, &entries).unwrap_or_else(|error| panic!("{error}"));
            let rewritten = crate::updated_config(&with_entries, catalog, "http://localhost:8000").unwrap_or_else(|error| panic!("{error}"));
            // Codex would parse it, and both entries are still there, each under its marker.
            let table: toml::Table = rewritten.parse().unwrap_or_else(|error| panic!("{error}\n{rewritten}"));
            let config = table["skills"]["config"].as_array().map(Vec::len).unwrap_or(0);
            assert_eq!(config, 2 + usize::from(!user.is_empty()), "{rewritten}");
            assert_eq!(rewritten.matches(config_entries::MARKER).count(), 2, "{rewritten}");

            // The next start takes exactly its own entries out again; what is left is what the
            // config rewrite alone produces from the user's file.
            let alone = crate::updated_config(user, catalog, "http://localhost:8000").unwrap_or_else(|error| panic!("{error}"));
            // (Compared as TOML: taking an entry out also takes the blank line after it.)
            let cleaned = config_entries::without_managed(&rewritten);
            assert!(!cleaned.contains(config_entries::MARKER), "{cleaned}");
            assert_eq!(cleaned.parse::<toml::Table>().ok(), alone.parse::<toml::Table>().ok(), "{cleaned}");
            // And a second pass over the rewritten file is stable.
            let again = config_entries::with_managed(&rewritten, &entries).unwrap_or_else(|error| panic!("{error}"));
            let settled = crate::updated_config(&again, catalog, "http://localhost:8000").unwrap_or_else(|error| panic!("{error}"));
            assert_eq!(config_entries::with_managed(&settled, &entries).as_deref(), Ok(settled.as_str()), "{settled}");
        }
    }

    #[test]
    fn the_budget_window_is_read_from_the_catalog_the_launcher_wrote() {
        let machine = scratch_machine("window");
        assert_eq!(advertised_window(&machine.codex_home), None);
        std::fs::write(
            machine.codex_home.join("model_catalog.json"),
            r#"{"models": [{"id": "m", "max_context_window": 262144}]}"#,
        )
        .unwrap_or_default();
        assert_eq!(advertised_window(&machine.codex_home), Some(262_144));
    }
}
