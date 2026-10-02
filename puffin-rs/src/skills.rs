//! Skills from other agents: the launcher's side of `puffin-skills`
//! (specs/DREAMFERENCE_PUFFIN_SKILLS.md).
//!
//! The crate in `../skills` does everything on disk: which skills there are, which the model is
//! offered, the `from-<agent>` links, the entries that switch a skill off in `config.toml`, and
//! installing one from a folder or a tarball. This module adds what needs the rest of `puffin`:
//!
//! - [`start`], run before every session, which brings the links and entries up to date and
//!   returns the tool glossary when a skill written for another agent is offered;
//! - [`run_cli`], `puffin skill …`, which never reaches Codex and needs no model server;
//! - the downloads of `puffin skill add` and `search`, which follow `/airgapped`: at `on` they are
//!   refused before any request is made.
//!
//! No Codex patch: skills are found by Codex's own loader, unmodified.

use std::io::IsTerminal;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use puffin_airgapped::Level;
use puffin_skills::Request;
use puffin_skills::catalog;
use puffin_skills::catalog::Machine;
use puffin_skills::install;
use puffin_skills::install::Source;
use puffin_skills::preflight::Host;
use puffin_skills::report;
use puffin_skills::settings::Settings;

/// A repository tarball larger than this is not downloaded: a skill is a folder, and the
/// catalogues `puffin` knows are a few megabytes.
const MAX_TARBALL_BYTES: u64 = 200 * 1024 * 1024;

/// The catalogues `puffin skill search` reads: repository, the folders holding skills, and the
/// prefix `puffin skill add` takes for them.
const CATALOGUES: &[(&str, &str, &[(&str, &str)])] = &[
    ("openai", "skills", &[("skills/.curated", "openai"), ("skills/.experimental", "openai")]),
    ("anthropics", "skills", &[("skills", "anthropic")]),
];

/// Where GitHub is. Tests point both at a stand-in server.
#[derive(Debug, Clone)]
pub struct GitHub {
    pub api: String,
    pub codeload: String,
    pub token: Option<String>,
}

impl GitHub {
    /// github.com, with `GITHUB_TOKEN` or `GH_TOKEN` when one is set (a private repository, or the
    /// unauthenticated rate limit).
    pub fn real() -> GitHub {
        GitHub {
            api: "https://api.github.com".to_string(),
            codeload: "https://codeload.github.com".to_string(),
            token: ["GITHUB_TOKEN", "GH_TOKEN"].iter().find_map(|name| std::env::var(name).ok()).filter(|token| !token.is_empty()),
        }
    }

    fn client(&self) -> Result<reqwest::Client, String> {
        reqwest::Client::builder()
            .user_agent("puffin-skill")
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
                "{owner}/{repo} is larger than {} MB to download; clone it and run puffin skill add <the skill's folder>",
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
/// budget is 2% of. `puffin skill` asks no model server.
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
    let started = puffin_skills::start(&machine, interactive, &stamp);
    for line in &started.lines {
        eprintln!("{line}");
    }
    if started.glossary { puffin_skills::glossary::GLOSSARY } else { "" }
}

/// Runs `puffin skill …` from a shell. Returns the exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let codex_home = match codex_utils_home_dir::find_codex_home() {
        Ok(home) => home.as_path().to_path_buf(),
        Err(_) => {
            eprintln!("puffin skill: could not resolve CODEX_HOME");
            return 1;
        }
    };
    let Some(machine) = machine(&codex_home, advertised_window(&codex_home)) else {
        eprintln!("puffin skill: this account has no home folder to look for skills in");
        return 1;
    };
    let outcome = run(&machine, Request::parse(args), &GitHub::real(), puffin_airgapped::resolve_for_command().level).await;
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
        Request::Help => Ok(puffin_skills::USAGE.lines().map(str::to_string).collect()),
        Request::Usage(message) => Err((2, format!("{message}\n{}", puffin_skills::USAGE))),
        Request::List { all } => Ok(report::list(&catalog::plan(machine, &settings), machine, &settings, all)),
        Request::Show(name) => {
            let plan = catalog::plan(machine, &settings);
            let entry = plan.find(&name).ok_or_else(|| failed(format!("{name}: no such skill (puffin skill list --all)")))?;
            Ok(report::show(entry, machine))
        }
        Request::Enable(name) => puffin_skills::enable(machine, &name).map_err(failed),
        Request::Disable(name) => puffin_skills::disable(machine, &name).map_err(failed),
        Request::Source { agent, on } => puffin_skills::set_source(machine, &agent, on).map_err(failed),
        Request::Remove(name) => puffin_skills::remove(machine, &name).map_err(failed),
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
            if matches!(source, Source::GitHub { .. }) {
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
            "puffin skill {what} needs the network, and the air-gap level is on (puffin airgapped). \
             Installed skills keep working; a skill in a folder can still be installed with puffin skill add <folder>."
        )));
    }
    Ok(())
}

fn today() -> String {
    chrono::Local::now().format("%Y-%m-%d").to_string()
}

async fn search(github: &GitHub, words: &[String]) -> Result<Vec<String>, String> {
    let mut found = Vec::new();
    let mut unreachable = Vec::new();
    for (owner, repo, folders) in CATALOGUES {
        let listed = async {
            let sha = github.commit(owner, repo, None).await?;
            install::catalogue(github.tarball(owner, repo, &sha).await?.as_slice(), folders)
        }
        .await;
        match listed {
            Ok(skills) => found.extend(skills.into_iter().filter(|skill| skill.matches(words))),
            Err(error) => unreachable.push(format!("{owner}/{repo}: {error}")),
        }
    }
    let width = found.iter().map(|skill| skill.source.len()).max().unwrap_or(0);
    let mut lines: Vec<String> = found
        .iter()
        .map(|skill| {
            let description: String = skill.description.lines().next().unwrap_or("").chars().take(90).collect();
            format!("{:<width$}  {description}", skill.source)
        })
        .collect();
    lines.push(match found.len() {
        0 => format!("No skill in OpenAI's or Anthropic's catalogue matches {}.", words.join(" ")),
        1 => "1 skill matches. Install it with puffin skill add <name>.".to_string(),
        count => format!("{count} skills match. Install one with puffin skill add <name>."),
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
    }
    let staged = install::validate(&unpacked)?;
    let destination = machine.skills_root().join(&staged.name);
    if destination.symlink_metadata().is_ok() {
        return Err(format!(
            "{} is already installed ({}); puffin skill remove {} first",
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
        "Installed {} in {}. The model is offered it from the next puffin start (puffin skill list).",
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

    /// A stand-in for GitHub: answers each request with the body `route` gives for its path, and
    /// records the paths asked for.
    fn stand_in(route: fn(&str) -> Option<Vec<u8>>) -> (GitHub, std::sync::Arc<std::sync::Mutex<Vec<String>>>) {
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
                let (status, body) = match route(&path) {
                    Some(body) => ("200 OK", body),
                    None => ("404 Not Found", b"Not Found".to_vec()),
                };
                let head = format!("HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
                let _ = stream.write_all(head.as_bytes());
                let _ = stream.write_all(&body);
            }
        });
        let base = format!("http://{address}");
        (GitHub { api: format!("{base}/api"), codeload: format!("{base}/codeload"), token: None }, asked)
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
            _ => None,
        }
    }

    fn scratch_machine(name: &str) -> Machine {
        let root = std::env::temp_dir().join(format!("puffin-skill-cli-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let home = root.join("home");
        std::fs::create_dir_all(home.join(".puffin")).unwrap_or_default();
        std::fs::create_dir_all(root.join("work")).unwrap_or_default();
        Machine {
            codex_home: home.join(".puffin"),
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
        assert!(lines.last().is_some_and(|line| line.starts_with("Installed internal-comms in ~/.puffin/skills/internal-comms.")), "{lines:?}");
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

        // A folder on this machine is a copy, not a download, and `duckduckgo` governs search
        // engines, not this.
        let local = machine.cwd.join("mine");
        std::fs::create_dir_all(&local).unwrap_or_default();
        std::fs::write(local.join("SKILL.md"), "---\nname: mine\ndescription: Mine.\n---\n").unwrap_or_default();
        assert!(run(&machine, request(&["add", "./mine", "--yes"]), &github, Level::On).await.is_ok());
        assert!(machine.skills_root().join("mine/SKILL.md").is_file());
        assert!(run(&machine, request(&["add", "anthropic/internal-comms", "--yes"]), &github, Level::DuckDuckGo).await.is_ok());
    }

    #[tokio::test]
    async fn search_reads_both_catalogues() {
        let (github, _) = stand_in(anthropic);
        let machine = scratch_machine("search");
        let lines = run(&machine, request(&["search", "pdf"]), &github, Level::Off).await.unwrap_or_default();
        assert_eq!(lines, vec![
            "openai/pdf  Read and fill PDF forms.".to_string(),
            "1 skill matches. Install it with puffin skill add <name>.".to_string(),
        ]);
        let all = run(&machine, request(&["search", "i"]), &github, Level::Off).await.unwrap_or_default();
        assert!(all.iter().any(|line| line.starts_with("anthropic/internal-comms")), "{all:?}");
        let none = run(&machine, request(&["search", "zebra"]), &github, Level::Off).await.unwrap_or_default();
        assert_eq!(none, vec!["No skill in OpenAI's or Anthropic's catalogue matches zebra.".to_string()]);
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
        let github = GitHub { api: "http://127.0.0.1:9".into(), codeload: "http://127.0.0.1:9".into(), token: None };
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
        assert!(usage.is_err_and(|(code, error)| code == 2 && error.contains("Usage: puffin skill")));
    }

    /// The start-up pass writes its entries into `config.toml`, and `configure_codex_home` then
    /// parses and rewrites the same file with toml_edit at every start. If that round trip ever
    /// reshaped an entry, the launcher would stop recognising its own entries and pile up stale
    /// ones.
    #[test]
    fn the_launchers_config_entries_survive_the_config_rewrite_and_are_still_removable() {
        use puffin_skills::config_entries;
        let entries = vec![
            (PathBuf::from("/h/.agents/skills/mac-only/SKILL.md"), "unavailable: for macos only".to_string()),
            (PathBuf::from("/h/.puffin/skills/.system/skill-creator/SKILL.md"), "shadowed by Claude Code (~/.claude/skills/x)".to_string()),
        ];
        let catalog = Path::new("/h/.puffin/model_catalog.json");
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
