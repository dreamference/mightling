//! Named system prompts (specs/DREAMFERENCE_PUFFIN_PROMPT.md, Phase 1): which prompt a new
//! `puffin` session starts with, and `puffin prompt list|show|use`.
//!
//! A prompt is a core text plus the launcher blocks appended to it (`web`, `email`, `code`). Two
//! are built in: `default`, Codex's own template renamed, byte for byte what `puffin` sent before
//! this module existed, and `high-swe`, a short method for resolving a defined task in a
//! repository (`prompts/high-swe.md`). A file `$CODEX_HOME/system-prompts/<name>.md` is a custom
//! prompt of that name; never one from the repository or the model, which cannot write there.
//!
//! The choice for a new session is, first match wins: `DREAMFERENCE_PUFFIN_PROMPT`, then
//! `puffin_prompt` in the Dreamference TOML file, then `default`. An unknown name is skipped with
//! one line naming it and its tier, so a typo never starts a session with an empty prompt.
//!
//! **How the choice reaches Codex.** Codex fixes the prompt when a session starts, from the
//! model catalog, and a resumed session keeps the prompt it recorded (§1.2). `default` is written
//! to `model_catalog.json` as before and nothing is added to the command line; any other prompt
//! gets a catalog of its own, `model_catalog.<name>.json`, named by `-c model_catalog_json=` for
//! this process alone. Not `model_instructions_file`: as an override it would also replace the
//! prompt of every session this launch resumes.

use std::ffi::OsString;
use std::path::Path;
use std::path::PathBuf;

use crate::code_index;

/// The prompt a new session gets when nothing is configured.
pub const DEFAULT_PROMPT: &str = "default";

/// The environment variable that overrides the TOML file for one launch.
pub const ENV_VAR: &str = "DREAMFERENCE_PUFFIN_PROMPT";

/// The TOML key `puffin prompt use` writes.
pub const TOML_KEY: &str = "puffin_prompt";

/// Where custom prompts live, under `$CODEX_HOME`.
pub const CUSTOM_DIR: &str = "system-prompts";

/// The `high-swe` text. A Python test keeps it equal to the spec's Appendix A with the two edits
/// §6.4 lists.
pub const HIGH_SWE: &str = include_str!("../prompts/high-swe.md");

const BLOCKS_PREFIX: &str = "<!-- puffin: blocks=";
const BLOCKS_SUFFIX: &str = "-->";
const USAGE: &str = "Usage: puffin prompt [list] | puffin prompt show [<name>] | puffin prompt use <name>";

/// Which of the launcher's blocks follow the core.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Blocks {
    pub web: bool,
    pub email: bool,
    pub code: bool,
}

impl Blocks {
    pub const ALL: Blocks = Blocks { web: true, email: true, code: true };

    fn names(self) -> String {
        let names: Vec<&str> = [(self.web, "web"), (self.email, "email"), (self.code, "code")]
            .into_iter()
            .filter_map(|(on, name)| on.then_some(name))
            .collect();
        if names.is_empty() { "none".to_string() } else { names.join(", ") }
    }
}

/// The text a prompt starts from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Core {
    /// Codex's longest bundled template, renamed (`crate::rebrand`).
    Codex,
    Text(String),
}

/// Where a prompt is defined.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Origin {
    BuiltIn(&'static str),
    File(PathBuf),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Prompt {
    pub name: String,
    pub core: Core,
    pub blocks: Blocks,
    pub origin: Origin,
}

impl Prompt {
    pub fn default_prompt() -> Prompt {
        Prompt {
            name: DEFAULT_PROMPT.to_string(),
            core: Core::Codex,
            blocks: Blocks::ALL,
            origin: Origin::BuiltIn("Codex's own prompt: conversational, web and email"),
        }
    }

    pub fn high_swe() -> Prompt {
        Prompt {
            name: "high-swe".to_string(),
            core: Core::Text(HIGH_SWE.to_string()),
            blocks: Blocks { web: false, email: false, code: true },
            origin: Origin::BuiltIn("repository tasks: reproduce, fix at the root, verify; no web or email"),
        }
    }

    pub fn is_default(&self) -> bool {
        self.name == DEFAULT_PROMPT
    }

    fn summary(&self) -> String {
        match &self.origin {
            Origin::BuiltIn(summary) => (*summary).to_string(),
            Origin::File(path) => format!("{} (blocks: {})", path.display(), self.blocks.names()),
        }
    }
}

/// Whether a name may be a prompt's: lowercase letters, digits and hyphens.
pub fn valid_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && !name.starts_with('-')
        && name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

/// Every prompt this machine has: the built-ins, then the custom ones by name, and notes on files
/// that were passed over.
#[derive(Clone, Debug, Default)]
pub struct Installed {
    pub prompts: Vec<Prompt>,
    pub notes: Vec<String>,
}

impl Installed {
    pub fn built_in() -> Installed {
        Installed { prompts: vec![Prompt::default_prompt(), Prompt::high_swe()], notes: Vec::new() }
    }

    /// The built-ins plus `<codex_home>/system-prompts/*.md`.
    pub fn load(codex_home: &Path) -> Installed {
        let mut installed = Installed::built_in();
        let dir = codex_home.join(CUSTOM_DIR);
        let Ok(entries) = std::fs::read_dir(&dir) else { return installed };
        let mut paths: Vec<PathBuf> = entries
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| path.extension().is_some_and(|ext| ext == "md"))
            .collect();
        paths.sort();
        for path in paths {
            let Some(name) = path.file_stem().map(|stem| stem.to_string_lossy().into_owned()) else { continue };
            if !valid_name(&name) {
                installed.notes.push(format!(
                    "{} is not a prompt: names are lowercase letters, digits and hyphens",
                    path.display()
                ));
                continue;
            }
            if installed.get(&name).is_some() {
                installed.notes.push(format!("{} is ignored: {name} is built in", path.display()));
                continue;
            }
            match std::fs::read_to_string(&path) {
                Ok(text) => match parse_custom(&text) {
                    Ok((core, blocks, unknown)) => {
                        if !unknown.is_empty() {
                            installed.notes.push(format!(
                                "{}: unknown block(s) {} ignored",
                                path.display(),
                                unknown.join(", ")
                            ));
                        }
                        installed.prompts.push(Prompt { name, core: Core::Text(core), blocks, origin: Origin::File(path) });
                    }
                    Err(reason) => installed.notes.push(format!("{} is not a prompt: {reason}", path.display())),
                },
                Err(err) => installed.notes.push(format!("{} could not be read: {err}", path.display())),
            }
        }
        installed
    }

    pub fn get(&self, name: &str) -> Option<&Prompt> {
        self.prompts.iter().find(|prompt| prompt.name == name)
    }

    fn names(&self) -> String {
        self.prompts.iter().map(|prompt| prompt.name.as_str()).collect::<Vec<_>>().join(", ")
    }
}

/// Splits a custom prompt file into its core and blocks. The first line may be
/// `<!-- puffin: blocks=web,email,code -->` (any subset); without it no block is appended.
/// Returns the block names it did not know, which are ignored.
fn parse_custom(text: &str) -> Result<(String, Blocks, Vec<String>), &'static str> {
    let (first, rest) = text.split_once('\n').unwrap_or((text, ""));
    let mut blocks = Blocks::default();
    let mut unknown = Vec::new();
    let core = match first.trim().strip_prefix(BLOCKS_PREFIX).and_then(|line| line.strip_suffix(BLOCKS_SUFFIX)) {
        Some(list) => {
            for name in list.split(',').map(str::trim).filter(|name| !name.is_empty()) {
                match name {
                    "web" => blocks.web = true,
                    "email" => blocks.email = true,
                    "code" => blocks.code = true,
                    other => unknown.push(other.to_string()),
                }
            }
            rest
        }
        None => text,
    };
    if core.trim().is_empty() {
        return Err("it has no text");
    }
    Ok((core.to_string(), blocks, unknown))
}

/// Where the choice came from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    Environment,
    ConfigFile(PathBuf),
    Default,
}

impl Source {
    fn label(&self) -> String {
        match self {
            Source::Environment => ENV_VAR.to_string(),
            Source::ConfigFile(path) => format!("{TOML_KEY} in {}", path.display()),
            Source::Default => "default".to_string(),
        }
    }
}

/// The prompt new sessions get, where that came from, and the names passed over on the way.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Resolved {
    pub prompt: Prompt,
    pub source: Source,
    pub skipped: Vec<String>,
}

/// Resolves the prompt from already-read tiers; [`resolve`] supplies the real ones.
pub fn resolve_from(environment: Option<&str>, config: Option<(&Path, &str)>, installed: &Installed) -> Resolved {
    let mut skipped = Vec::new();
    let mut tier = |value: Option<&str>, source: Source| -> Option<(Prompt, Source)> {
        let value = value?.trim();
        if value.is_empty() {
            return None;
        }
        match installed.get(value) {
            Some(prompt) => Some((prompt.clone(), source)),
            None => {
                skipped.push(format!(
                    "prompt \"{value}\" from {} is not installed (installed: {})",
                    source.label(),
                    installed.names()
                ));
                None
            }
        }
    };
    let found = tier(environment, Source::Environment).or_else(|| {
        let (path, text) = config?;
        let value = toml::from_str::<toml::Table>(text).ok()?.get(TOML_KEY)?.as_str()?.to_string();
        tier(Some(&value), Source::ConfigFile(path.to_path_buf()))
    });
    let (prompt, source) = found.unwrap_or_else(|| (Prompt::default_prompt(), Source::Default));
    Resolved { prompt, source, skipped }
}

/// Resolves the prompt new sessions get, reading every tier.
pub fn resolve(codex_home: &Path) -> Resolved {
    let environment = std::env::var(ENV_VAR).ok();
    let config = crate::config_file().and_then(|path| std::fs::read_to_string(&path).ok().map(|text| (path, text)));
    resolve_from(
        environment.as_deref(),
        config.as_ref().map(|(path, text)| (path.as_path(), text.as_str())),
        &Installed::load(codex_home),
    )
}

/// What the launcher found on this machine for the blocks: each is empty when absent.
#[derive(Clone, Debug, Default)]
pub struct Parts {
    /// The email block (`crate::gmail_access_instructions`), when an account is connected.
    pub email: String,
    /// The code-navigation block from `puffin-code prompt-block`, when it is installed.
    pub code: String,
    /// The skills glossary. Not a prompt block: the skills list reaches every prompt, so its
    /// glossary does too.
    pub glossary: String,
    pub rg_installed: bool,
}

/// The whole system prompt a session under `prompt` receives.
///
/// For `default` this is exactly what the launcher composed before named prompts existed: Codex's
/// template and the web block, with the search sentence rewritten for the index, then email, code
/// and the glossary.
pub fn compose(prompt: &Prompt, parts: &Parts) -> String {
    let mut text = match &prompt.core {
        Core::Codex => crate::rebrand(&crate::codex_template()),
        Core::Text(core) => core.trim_end().to_string(),
    };
    if prompt.blocks.web {
        text.push_str(crate::WEB_ACCESS_INSTRUCTIONS);
    }
    // The rewritten sentence points at the Code navigation section, so it is only rewritten for a
    // prompt that carries that section.
    let index = if prompt.blocks.code { code_index::named_in(&parts.code) } else { None };
    let mut text = code_index::search_habit(&text, index, parts.rg_installed);
    if prompt.blocks.email {
        text.push_str(&parts.email);
    }
    if prompt.blocks.code {
        text.push_str(&parts.code);
    }
    text.push_str(&parts.glossary);
    text
}

/// The catalog file a prompt is written to under `$CODEX_HOME`.
pub fn catalog_file(codex_home: &Path, prompt: &Prompt) -> PathBuf {
    if prompt.is_default() {
        codex_home.join("model_catalog.json")
    } else {
        codex_home.join(format!("model_catalog.{}.json", prompt.name))
    }
}

/// Names a prompt's catalog for this process, unless the user named one of their own.
pub fn with_catalog(args: Vec<OsString>, catalog: &Path) -> Vec<OsString> {
    let user_args: Vec<String> = args.iter().skip(1).map(|arg| arg.to_string_lossy().into_owned()).collect();
    let own = crate::option_values(&user_args, &["-c", "--config"])
        .any(|value| value.split_once('=').is_some_and(|(key, _)| key.trim() == "model_catalog_json"));
    if own {
        return args;
    }
    let setting = format!(
        "model_catalog_json={}",
        toml::Value::String(catalog.to_string_lossy().into_owned())
    );
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    out.extend(["-c".into(), OsString::from(setting)]);
    out.extend(args);
    out
}

/// The lines printed at launch: names passed over, and which prompt an interactive session starts
/// with when it is not `default`.
pub fn startup_lines(resolved: &Resolved, interactive: bool) -> Vec<String> {
    let mut lines: Vec<String> = resolved.skipped.iter().map(|note| format!("⚠️  {note}; skipped.")).collect();
    if interactive && !resolved.prompt.is_default() {
        lines.push(format!("Prompt: {} ({}).", resolved.prompt.name, resolved.source.label()));
    }
    lines
}

/// `puffin prompt …` from a shell. Returns the exit code. It never waits for a model server.
pub async fn run_cli(args: &[String]) -> i32 {
    let Some(codex_home) = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf()) else {
        eprintln!("Could not resolve CODEX_HOME.");
        return 1;
    };
    let words: Vec<&str> = args.iter().map(String::as_str).collect();
    match words.as_slice() {
        [] | ["list"] => {
            for line in list_lines(&resolve(&codex_home), &Installed::load(&codex_home)) {
                println!("{line}");
            }
            0
        }
        ["show"] => show(&resolve(&codex_home).prompt).await,
        ["show", name] => match Installed::load(&codex_home).get(name) {
            Some(prompt) => show(prompt).await,
            None => not_installed(name, &Installed::load(&codex_home)),
        },
        ["use", name] => {
            let installed = Installed::load(&codex_home);
            if installed.get(name).is_none() {
                return not_installed(name, &installed);
            }
            let (lines, code) = use_prompt(name);
            for line in lines {
                println!("{line}");
            }
            code
        }
        ["-h" | "--help" | "help"] => {
            println!("{USAGE}");
            0
        }
        _ => {
            eprintln!("{USAGE}");
            2
        }
    }
}

fn not_installed(name: &str, installed: &Installed) -> i32 {
    eprintln!("No prompt named \"{name}\". Installed: {}.", installed.names());
    1
}

fn list_lines(resolved: &Resolved, installed: &Installed) -> Vec<String> {
    let mut lines = vec![format!("Prompt for new sessions: {} ({})", resolved.prompt.name, resolved.source.label())];
    let width = installed.prompts.iter().map(|prompt| prompt.name.len()).max().unwrap_or(0);
    for prompt in &installed.prompts {
        let marker = if prompt.name == resolved.prompt.name { "   ← new sessions" } else { "" };
        lines.push(format!("  {:<width$}  {}{marker}", prompt.name, prompt.summary()));
    }
    lines.extend(resolved.skipped.iter().map(|note| format!("Note: {note}; skipped.")));
    lines.extend(installed.notes.iter().map(|note| format!("Note: {note}.")));
    lines.push(format!(
        "Change it: puffin prompt use <name>, or {ENV_VAR}=<name> for one launch. A session keeps the prompt it started with."
    ));
    lines
}

/// Prints the composed text on stdout, as a session on this machine would receive it now, and
/// its size on stderr.
async fn show(prompt: &Prompt) -> i32 {
    let dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let host = crate::vllm_host();
    let email = if prompt.blocks.email && crate::puffin_gmail_enabled() && crate::host_is_local(&host) {
        crate::connected_gmail_accounts()
            .await
            .map(|accounts| crate::gmail_access_instructions(&accounts))
            .unwrap_or_default()
    } else {
        String::new()
    };
    let code = if prompt.blocks.code { code_index::prompt_block(code_index::tools_enabled(), &dir) } else { String::new() };
    let parts = Parts { email, code, glossary: String::new(), rg_installed: code_index::rg_installed() };
    let text = compose(prompt, &parts);
    println!("{text}");
    eprintln!(
        "{}: {} chars; blocks: {} (email only with a connected account, code only with puffin-code installed; the skills glossary, when on, is added at launch).",
        prompt.name,
        text.chars().count(),
        prompt.blocks.names()
    );
    0
}

/// Writes `puffin_prompt = "<name>"` to the configuration file. Returns the lines and the code.
fn use_prompt(name: &str) -> (Vec<String>, i32) {
    let path = crate::config_file().or_else(|| {
        Some(PathBuf::from(std::env::var_os("HOME")?).join(".config/dreamference/config.toml"))
    });
    let Some(path) = path else {
        return (vec!["Could not find a configuration file to write: HOME is not set.".to_string()], 1);
    };
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let updated = match with_choice(&existing, name) {
        Ok(updated) => updated,
        Err(err) => return (vec![format!("Could not update {}: {err}", path.display())], 1),
    };
    let written = path
        .parent()
        .map_or(Ok(()), std::fs::create_dir_all)
        .and_then(|()| std::fs::write(&path, updated));
    if let Err(err) = written {
        return (vec![format!("Could not write {}: {err}", path.display())], 1);
    }
    let mut lines = vec![format!(
        "Prompt: {name} for new sessions ({TOML_KEY} in {}). Sessions already started keep theirs.",
        path.display()
    )];
    if std::env::var(ENV_VAR).is_ok_and(|value| !value.trim().is_empty()) {
        lines.push(format!("{ENV_VAR} is set, and it still wins for new sessions."));
    }
    (lines, 0)
}

/// The TOML text with `puffin_prompt` set to `name`, everything else kept as written.
fn with_choice(existing: &str, name: &str) -> Result<String, toml_edit::TomlError> {
    let mut document: toml_edit::DocumentMut = existing.parse()?;
    document[TOML_KEY] = toml_edit::value(name);
    Ok(document.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("puffin-prompt-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join(CUSTOM_DIR)).unwrap();
        dir
    }

    fn parts(email: &str, code: &str) -> Parts {
        Parts { email: email.to_string(), code: code.to_string(), glossary: String::new(), rg_installed: true }
    }

    #[test]
    fn default_composes_to_what_the_launcher_sent_before() {
        let code = "\n\n# Code navigation\n\nbehind the `code_*` tools\n";
        let email = crate::gmail_access_instructions("a@x.com");
        for (email, code, rg) in [("", "", true), (email.as_str(), code, false), ("", code, true)] {
            let parts = Parts { email: email.to_string(), code: code.to_string(), glossary: "\n\nG\n".to_string(), rg_installed: rg };
            let before = code_index::search_habit(&crate::base_instructions(), code_index::named_in(code), rg)
                + email
                + code
                + "\n\nG\n";
            assert_eq!(compose(&Prompt::default_prompt(), &parts), before);
        }
    }

    #[test]
    fn high_swe_is_short_names_only_real_tools_and_carries_only_the_code_block() {
        assert!(HIGH_SWE.len() < 5_000, "{} chars", HIGH_SWE.len());
        assert!(HIGH_SWE.starts_with("You are Puffin, a coding agent."));
        for absent in ["multi_tool_use", "commentary", "`final`", "Codex", "puffin-search", "puffin-fetch"] {
            assert!(!HIGH_SWE.contains(absent), "{absent}");
        }
        let code = "\n\n# Code navigation\n\n`puffin-code def`\n";
        let email = crate::gmail_access_instructions("a@x.com");
        let text = compose(&Prompt::high_swe(), &parts(&email, code));
        assert!(text.starts_with(HIGH_SWE.trim_end()));
        assert!(text.ends_with(code));
        assert!(!text.contains("# Web access") && !text.contains("# Email access"));
        // Without an index the text ends where the file does.
        assert_eq!(compose(&Prompt::high_swe(), &parts("", "")), HIGH_SWE.trim_end());
    }

    #[test]
    fn each_tier_shadows_the_next_and_unknown_names_fall_through() {
        let installed = Installed::built_in();
        let path = Path::new("/tmp/dreamference.toml");
        let toml = "puffin_prompt = \"high-swe\"\n";
        let env = resolve_from(Some("default"), Some((path, toml)), &installed);
        assert_eq!((env.prompt.name.as_str(), env.source.clone()), ("default", Source::Environment));
        let file = resolve_from(None, Some((path, toml)), &installed);
        assert_eq!((file.prompt.name.as_str(), file.source.clone()), ("high-swe", Source::ConfigFile(path.to_path_buf())));
        let nothing = resolve_from(Some(" "), Some((path, "vllm_host = \"x\"\n")), &installed);
        assert_eq!((nothing.prompt.name.as_str(), nothing.source), ("default", Source::Default));
        // A typo is named with its tier, and the next tier is used.
        let typo = resolve_from(Some("high-sw"), Some((path, toml)), &installed);
        assert_eq!(typo.prompt.name, "high-swe");
        assert_eq!(typo.skipped.len(), 1);
        assert!(typo.skipped[0].contains("\"high-sw\" from DREAMFERENCE_PUFFIN_PROMPT"), "{}", typo.skipped[0]);
        let both = resolve_from(Some("x"), Some((path, "puffin_prompt = \"y\"\n")), &installed);
        assert_eq!((both.prompt.name.as_str(), both.skipped.len()), ("default", 2));
        assert_eq!(startup_lines(&both, false).len(), 2);
        assert_eq!(startup_lines(&file, true), vec![format!("Prompt: high-swe ({TOML_KEY} in /tmp/dreamference.toml).")]);
        assert!(startup_lines(&env, true).is_empty(), "default says nothing");
    }

    #[test]
    fn custom_prompts_follow_the_name_and_block_rules_and_never_shadow_a_built_in() {
        let home = scratch("custom");
        let dir = home.join(CUSTOM_DIR);
        std::fs::write(dir.join("mine.md"), "<!-- puffin: blocks=web, code, sparkles -->\nBe brief.\n").unwrap();
        std::fs::write(dir.join("plain.md"), "<!-- just a comment -->\nAll of it.\n").unwrap();
        std::fs::write(dir.join("high-swe.md"), "shadow").unwrap();
        std::fs::write(dir.join("Bad_Name.md"), "x").unwrap();
        std::fs::write(dir.join("empty.md"), "<!-- puffin: blocks=web -->\n\n").unwrap();
        std::fs::write(dir.join("notes.txt"), "not a prompt").unwrap();
        let installed = Installed::load(&home);
        let names: Vec<&str> = installed.prompts.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, ["default", "high-swe", "mine", "plain"]);
        let mine = installed.get("mine").unwrap();
        assert_eq!(mine.blocks, Blocks { web: true, email: false, code: true });
        assert_eq!(mine.core, Core::Text("Be brief.\n".to_string()));
        let plain = installed.get("plain").unwrap();
        assert_eq!(plain.blocks, Blocks::default());
        assert_eq!(compose(plain, &parts("E", "\n\n# Code navigation\n")), "<!-- just a comment -->\nAll of it.");
        let composed = compose(mine, &parts("EMAIL-BLOCK", ""));
        assert!(composed.starts_with("Be brief.\n\n# Web access") && !composed.contains("EMAIL-BLOCK"));
        assert_eq!(installed.get("high-swe"), Some(&Prompt::high_swe()));
        assert_eq!(installed.notes.len(), 4, "{:?}", installed.notes);
        assert!(installed.notes.iter().any(|note| note.contains("sparkles")));
        assert!(installed.notes.iter().any(|note| note.contains("high-swe is built in")));
        assert!(installed.notes.iter().any(|note| note.contains("Bad_Name.md")));
        assert!(installed.notes.iter().any(|note| note.contains("no text")));
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn names_are_lowercase_letters_digits_and_hyphens() {
        for good in ["default", "high-swe", "v2", "a-b-c"] {
            assert!(valid_name(good), "{good}");
        }
        for bad in ["", "High", "a_b", "a.b", "../x", "-x", "a b"] {
            assert!(!valid_name(bad), "{bad}");
        }
    }

    #[test]
    fn default_adds_no_argument_and_another_prompt_names_its_own_catalog() {
        let home = Path::new("/h/.puffin");
        assert_eq!(catalog_file(home, &Prompt::default_prompt()), home.join("model_catalog.json"));
        let catalog = catalog_file(home, &Prompt::high_swe());
        assert_eq!(catalog, home.join("model_catalog.high-swe.json"));
        let args: Vec<OsString> = vec!["puffin".into(), "exec".into(), "hi".into()];
        let out: Vec<String> = with_catalog(args, &catalog).iter().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(out, ["puffin", "-c", "model_catalog_json=\"/h/.puffin/model_catalog.high-swe.json\"", "exec", "hi"]);
        let (_, value) = out[2].split_once('=').unwrap();
        assert!(format!("v = {value}").parse::<toml::Table>().is_ok());
        // The user's own catalog wins.
        let own: Vec<OsString> = vec!["puffin".into(), "-c".into(), "model_catalog_json=\"/mine.json\"".into()];
        assert_eq!(with_catalog(own.clone(), &catalog), own);
    }

    #[test]
    fn the_list_marks_the_choice_and_use_keeps_the_rest_of_the_file() {
        let installed = Installed::built_in();
        let resolved = resolve_from(Some("high-swe"), None, &installed);
        let lines = list_lines(&resolved, &installed);
        assert_eq!(lines[0], format!("Prompt for new sessions: high-swe ({ENV_VAR})"));
        assert!(lines[1].starts_with("  default   Codex's own prompt") && !lines[1].contains('←'));
        assert!(lines[2].starts_with("  high-swe  repository tasks") && lines[2].ends_with("← new sessions"));
        let updated = with_choice("# mine\nvllm_host = \"http://h:8000\"\n\n[night]\nprompt = \"x\"\n", "high-swe").unwrap();
        assert!(updated.starts_with("# mine\nvllm_host = \"http://h:8000\"\n"));
        let parsed: toml::Table = toml::from_str(&updated).unwrap();
        assert_eq!(parsed[TOML_KEY].as_str(), Some("high-swe"));
        assert_eq!(parsed["night"]["prompt"].as_str(), Some("x"));
        let again = with_choice(&updated, "default").unwrap();
        assert_eq!(again.matches(TOML_KEY).count(), 1);
    }
}
