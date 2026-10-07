//! `SKILL.md` frontmatter, read once for every dialect (spec §4).
//!
//! Codex reads three keys (`name`, `description`, `metadata.short-description`). This reads those
//! and the handful the launcher acts on: what a skill says it needs (Hermes `platforms`, OpenClaw
//! `metadata.openclaw.os` and `requires`), and whether its author kept it out of the model's hands
//! (`disable-model-invocation`). Every other key is ignored; a few are recorded by name only, so
//! `mling skill show` can say they were never acted on.

use serde_yaml::Value;

/// Keys that would run something or grant something in their own agent. `mling` never does
/// either (spec §4, "Neutralise"); they are listed so `show` can say so.
const NEUTRALISED_KEYS: &[&str] = &["hooks", "allowed-tools", "disallowed-tools"];

/// What the launcher reads from one `SKILL.md`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Frontmatter {
    pub name: Option<String>,
    pub description: String,
    pub short_description: Option<String>,
    pub license: Option<String>,
    pub compatibility: Option<String>,
    /// Operating systems the skill names, normalised to `linux`, `macos`, `windows`. Empty means
    /// it names none, i.e. any.
    pub platforms: Vec<String>,
    /// Programs that must all be on `PATH`.
    pub required_bins: Vec<String>,
    /// Programs of which one must be on `PATH`.
    pub any_bins: Vec<String>,
    /// Environment variables the skill asks for. Shown, never a reason to withhold it.
    pub required_env: Vec<String>,
    /// `disable-model-invocation: true`: its author did not want a model to start it.
    pub manual_only: bool,
    /// Keys of [`NEUTRALISED_KEYS`] that are present.
    pub neutralised: Vec<String>,
}

/// Splits a `SKILL.md` into its YAML frontmatter and its body. `None` when there is no
/// frontmatter block at the top.
pub fn split(text: &str) -> Option<(&str, &str)> {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let rest = text.strip_prefix("---")?;
    let rest = rest.strip_prefix('\n').or_else(|| rest.strip_prefix("\r\n"))?;
    let mut offset = 0;
    for line in rest.split_inclusive('\n') {
        if line.trim_end() == "---" {
            return Some((&rest[..offset], &rest[offset + line.len()..]));
        }
        offset += line.len();
    }
    None
}

/// Reads the frontmatter of a `SKILL.md`.
///
/// Third-party skills often write a description with an unquoted colon, which is not YAML; like
/// Codex's own loader, a line-oriented reading of `name` and `description` is tried before giving
/// up, so a skill Codex loads is not one this refuses.
pub fn parse(text: &str) -> Result<Frontmatter, String> {
    let (yaml, _) = split(text).ok_or("no frontmatter block (--- … ---) at the top")?;
    let frontmatter = match serde_yaml::from_str::<Value>(yaml) {
        Ok(root @ Value::Mapping(_)) => from_yaml(&root),
        Ok(_) => return Err("the frontmatter is not a mapping".to_string()),
        Err(error) => from_lines(yaml).ok_or_else(|| format!("the frontmatter is not valid YAML: {error}"))?,
    };
    if frontmatter.description.trim().is_empty() {
        return Err("the frontmatter has no description".to_string());
    }
    Ok(frontmatter)
}

fn from_yaml(root: &Value) -> Frontmatter {
    let metadata = root.get("metadata");
    let openclaw = metadata.and_then(|metadata| metadata.get("openclaw"));
    let requires = openclaw.and_then(|openclaw| openclaw.get("requires"));

    let mut platforms = strings(root.get("platforms"));
    platforms.extend(strings(openclaw.and_then(|openclaw| openclaw.get("os"))));
    let mut platforms: Vec<String> = platforms.iter().filter_map(|name| normalise_os(name)).collect();
    platforms.sort();
    platforms.dedup();

    let mut required_env = names(root.get("required_environment_variables"));
    required_env.extend(names(requires.and_then(|requires| requires.get("env"))));
    required_env.dedup();

    Frontmatter {
        name: text(root.get("name")),
        description: text(root.get("description")).unwrap_or_default(),
        short_description: text(metadata.and_then(|metadata| metadata.get("short-description"))),
        license: text(root.get("license")),
        compatibility: text(root.get("compatibility")),
        platforms,
        required_bins: strings(requires.and_then(|requires| requires.get("bins"))),
        any_bins: strings(requires.and_then(|requires| requires.get("anyBins"))),
        required_env,
        manual_only: root.get("disable-model-invocation").and_then(Value::as_bool) == Some(true),
        neutralised: NEUTRALISED_KEYS
            .iter()
            .filter(|key| root.get(**key).is_some())
            .map(|key| key.to_string())
            .collect(),
    }
}

/// The repair path: top-level `name:` and `description:` lines, taken verbatim.
fn from_lines(yaml: &str) -> Option<Frontmatter> {
    let value_of = |key: &str| {
        yaml.lines()
            .find_map(|line| line.strip_prefix(key)?.strip_prefix(':'))
            .map(|value| value.trim().trim_matches(['"', '\'']).to_string())
            .filter(|value| !value.is_empty())
    };
    Some(Frontmatter {
        name: value_of("name"),
        description: value_of("description")?,
        manual_only: value_of("disable-model-invocation").as_deref() == Some("true"),
        ..Frontmatter::default()
    })
}

fn text(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(text) => Some(text.trim().to_string()).filter(|text| !text.is_empty()),
        Value::Number(number) => Some(number.to_string()),
        Value::Bool(flag) => Some(flag.to_string()),
        _ => None,
    }
}

/// A list of strings, or a single string standing for a list of one.
fn strings(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Sequence(items)) => items.iter().filter_map(|item| text(Some(item))).collect(),
        Some(other) => text(Some(other)).into_iter().collect(),
        None => Vec::new(),
    }
}

/// A list whose items are names, or maps carrying one under `name` (Hermes writes
/// `required_environment_variables` both ways).
fn names(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Sequence(items)) => items
            .iter()
            .filter_map(|item| text(Some(item)).or_else(|| text(item.get("name"))))
            .collect(),
        other => strings(other),
    }
}

/// `darwin`, `macos`, `win32`, … as the three names the preflight compares.
pub fn normalise_os(name: &str) -> Option<String> {
    match name.trim().to_ascii_lowercase().as_str() {
        "linux" => Some("linux"),
        "macos" | "darwin" | "mac" | "osx" => Some("macos"),
        "windows" | "win32" | "win" => Some("windows"),
        _ => None,
    }
    .map(str::to_string)
}

/// Whether `name` is a legal skill name under the Agent Skills standard: at most 64 characters,
/// lowercase letters, digits and single hyphens, not starting or ending with a hyphen.
pub fn legal_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name.split('-').all(|part| {
            !part.is_empty() && part.chars().all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit())
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_standard_keys_are_read() {
        let parsed = parse(
            "---\nname: pdf\ndescription: Read and write PDF files.\nlicense: Apache-2.0\nmetadata:\n  short-description: PDFs\n---\n# Body\n",
        )
        .unwrap_or_default();
        assert_eq!(parsed.name.as_deref(), Some("pdf"));
        assert_eq!(parsed.description, "Read and write PDF files.");
        assert_eq!(parsed.short_description.as_deref(), Some("PDFs"));
        assert_eq!(parsed.license.as_deref(), Some("Apache-2.0"));
        assert!(parsed.platforms.is_empty() && !parsed.manual_only);
    }

    #[test]
    fn claudes_dialect_is_read_and_its_hooks_only_named() {
        let parsed = parse(
            "---\nname: deploy\ndescription: Deploy the site.\ndisable-model-invocation: true\nallowed-tools: Bash(git *)\ncontext: fork\nmodel: opus\nhooks:\n  PreToolUse:\n    - command: touch /tmp/x\n---\n!`touch /tmp/y`\n",
        )
        .unwrap_or_default();
        assert!(parsed.manual_only);
        assert_eq!(parsed.neutralised, vec!["hooks".to_string(), "allowed-tools".to_string()]);
    }

    #[test]
    fn hermes_and_openclaw_requirements_are_read() {
        let parsed = parse(
            "---\nname: imessage\ndescription: Send messages.\nplatforms: [macos]\nrequired_environment_variables:\n  - name: IMSG_TOKEN\n    prompt: token\n  - OTHER_KEY\nmetadata:\n  hermes:\n    tags: [chat]\n  openclaw:\n    os: [darwin, linux]\n    always: true\n    requires:\n      bins: [imsg]\n      anyBins: [rg, grep]\n      env: [CLAW_KEY]\n---\n",
        )
        .unwrap_or_default();
        assert_eq!(parsed.platforms, vec!["linux".to_string(), "macos".to_string()]);
        assert_eq!(parsed.required_bins, vec!["imsg".to_string()]);
        assert_eq!(parsed.any_bins, vec!["rg".to_string(), "grep".to_string()]);
        assert_eq!(
            parsed.required_env,
            vec!["IMSG_TOKEN".to_string(), "OTHER_KEY".to_string(), "CLAW_KEY".to_string()]
        );
    }

    #[test]
    fn an_unquoted_colon_in_a_description_is_repaired() {
        let parsed = parse("---\nname: notes\ndescription: Use when: the user asks for notes\n---\n");
        assert_eq!(parsed.map(|parsed| parsed.description).as_deref(), Ok("Use when: the user asks for notes"));
    }

    #[test]
    fn a_file_without_frontmatter_or_description_is_refused() {
        assert!(parse("# Just a heading\n").is_err());
        assert!(parse("---\nname: x\n---\n").is_err());
        assert!(parse("---\n- a\n- b\n---\n").is_err());
    }

    #[test]
    fn names_follow_the_standard() {
        for name in ["pdf", "mcp-builder", "a1-b2"] {
            assert!(legal_name(name), "{name}");
        }
        for name in ["", "PDF", "a--b", "-a", "a-", "a_b", "a b", &"x".repeat(65)] {
            assert!(!legal_name(name), "{name}");
        }
    }
}
