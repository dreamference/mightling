//! The paths a `task` never touches and the dependency changes it never makes
//! (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §6.1, §6.2 item 5).
//!
//! The protected paths are a built-in list (sandboxing, /airgapped, the egress allowlists and
//! audit, keys and credentials, the installers, permission and approval logic, the delegation
//! policy itself, the release workflows) plus whatever `[claude_plugin] exclude` adds, in the
//! repository's `dreamference.toml` or the user's `~/.config/dreamference/config.toml`. The
//! config only adds: a default is never removed, and an entry starting with `!` is ignored.
//!
//! Dependencies: a lockfile or requirements file is never changed; a manifest (`Cargo.toml`,
//! `package.json`, `pyproject.toml`, `setup.py`) may change, but not its dependency sections,
//! which are compared parsed, before and after, so an edit elsewhere in the file still passes.

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::Value as Json;
use toml::Value as Toml;

/// The built-in protected paths: `(what, glob)`. A glob is matched, case-insensitively, against
/// a path relative to the repository and against each of its parent folders; `**` is any number
/// of folders, `*` and `?` stay within one name.
pub const DEFAULT_EXCLUDES: &[(&str, &str)] = &[
    ("sandboxing", "**/*sandbox*"),
    ("sandboxing", "**/*apparmor*"),
    ("sandboxing", "**/*bwrap*"),
    ("sandboxing", "ling-rs/safegit"),
    (
        "sandboxing and the vendor channels (the Codex patches)",
        "codex-patches",
    ),
    ("/airgapped", "**/*airgap*"),
    ("the egress allowlists and the egress audit", "**/*egress*"),
    (
        "the egress allowlists and the egress audit",
        "dreamference/audit",
    ),
    (
        "the egress allowlists and the egress audit",
        "ling-rs/src/audit.rs",
    ),
    (
        "the egress allowlists and the egress audit",
        "ling-rs/web/policy.json",
    ),
    (
        "the egress allowlists and the egress audit",
        "ling-rs/web/src/policy.rs",
    ),
    (
        "the egress allowlists and the egress audit",
        "desktop/electron/src/policy.ts",
    ),
    ("keys and credentials", "**/*.pub"),
    ("keys and credentials", "**/*.pem"),
    ("keys and credentials", "**/*.key"),
    ("keys and credentials", "**/*credential*"),
    ("keys and credentials", "**/*secret*"),
    ("keys and credentials", "**/*password*"),
    ("keys and credentials", "**/*pairing*"),
    ("keys and credentials", "**/*certificate*"),
    ("keys and credentials", "**/*enrol*"),
    ("keys and credentials", "**/auth"),
    ("keys and credentials", "**/auth.*"),
    ("keys and credentials", "**/*signing*"),
    ("keys and credentials", "**/*signature*"),
    ("keys and credentials", "**/sign.*"),
    ("keys and credentials", "**/sign_*"),
    ("keys and credentials", "**/*-sign.*"),
    ("keys and credentials", "**/resign-*"),
    ("the installers", "install.sh"),
    ("the installers", "install.ps1"),
    ("the installers", "**/*install*"),
    (
        "the installers",
        "dreamference/vllm_server/host_safety_setup.py",
    ),
    ("permission and approval logic", "**/*approval*"),
    ("permission and approval logic", "**/*permission*"),
    ("the delegation policy itself", "ling-rs/delegate"),
    ("the delegation policy itself", "claude-plugin"),
    ("the delegation policy itself", ".claude-plugin"),
    ("the delegation policy itself", "dreamference.toml"),
    ("release signing and publishing", ".github/workflows"),
];

/// Lockfiles: any change is refused. `(file name, what)`.
const LOCKFILES: &[(&str, &str)] = &[
    ("cargo.lock", "Cargo's lockfile"),
    ("package-lock.json", "npm's lockfile"),
    ("npm-shrinkwrap.json", "npm's lockfile"),
    ("yarn.lock", "Yarn's lockfile"),
    ("pnpm-lock.yaml", "pnpm's lockfile"),
    ("bun.lock", "Bun's lockfile"),
    ("bun.lockb", "Bun's lockfile"),
    ("poetry.lock", "Poetry's lockfile"),
    ("uv.lock", "uv's lockfile"),
    ("pdm.lock", "PDM's lockfile"),
    ("pipfile.lock", "Pipenv's lockfile"),
    ("pipfile", "Pipenv's dependency list"),
    ("setup.cfg", "setuptools' dependency config"),
    ("go.mod", "Go's dependency list"),
    ("go.sum", "Go's lockfile"),
    ("gemfile", "Bundler's dependency list"),
    ("gemfile.lock", "Bundler's lockfile"),
];

/// Where a rule comes from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Source {
    BuiltIn,
    /// `[claude_plugin] exclude` in the named file.
    Config(String),
}

/// One protected-path rule.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rule {
    pub glob: String,
    /// What the rule protects, in plain words.
    pub what: String,
    pub source: Source,
}

impl Rule {
    /// Why `path` is refused, naming the rule: "`x` matches the built-in rule `g` (what)".
    pub fn explain(&self, path: &str) -> String {
        match &self.source {
            Source::BuiltIn => format!(
                "{path} is protected: it matches the built-in rule `{}` ({})",
                self.glob, self.what
            ),
            Source::Config(file) => format!(
                "{path} is protected: it matches `{}`, a `[claude_plugin] exclude` rule in {file}",
                self.glob
            ),
        }
    }
}

/// The protected-path rules of one task, fixed before the model runs.
#[derive(Debug, Clone)]
pub struct Rules {
    pub rules: Vec<Rule>,
}

impl Rules {
    /// The built-in rules only.
    pub fn defaults() -> Self {
        let rules = DEFAULT_EXCLUDES
            .iter()
            .map(|(what, glob)| Rule {
                glob: glob.to_string(),
                what: what.to_string(),
                source: Source::BuiltIn,
            })
            .collect();
        Self { rules }
    }

    /// The built-in rules plus `[claude_plugin] exclude` of the worktree's `dreamference.toml`
    /// and of the user's config file. A file that exists but cannot be read, or whose
    /// `exclude` is not a list of strings, is an error: the task is refused, never run with
    /// fewer rules than the owner wrote.
    pub fn load(worktree: &Path, user_config: Option<&Path>) -> Result<Self, String> {
        let mut out = Self::defaults();
        let repo = worktree.join("dreamference.toml");
        let mut files = vec![(repo.as_path(), "dreamference.toml".to_string())];
        if let Some(user) = user_config {
            files.push((user, "~/.config/dreamference/config.toml".to_string()));
        }
        for (file, label) in files {
            let text = match std::fs::read_to_string(file) {
                Ok(t) => t,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
                Err(e) => {
                    return Err(format!(
                        "cannot read {label} for `[claude_plugin] exclude`: {e}"
                    ));
                }
            };
            for glob in config_excludes(&text).map_err(|e| format!("{label}: {e}"))? {
                out.rules.push(Rule {
                    glob,
                    what: String::new(),
                    source: Source::Config(label.clone()),
                });
            }
        }
        Ok(out)
    }

    /// The first rule `path` (or one of its parent folders) matches.
    pub fn matching(&self, path: &str) -> Option<&Rule> {
        self.rules.iter().find(|r| path_matches(&r.glob, path))
    }
}

/// The globs of `[claude_plugin] exclude` in a TOML text; `!` entries and blanks are dropped.
pub fn config_excludes(text: &str) -> Result<Vec<String>, String> {
    let doc: Toml = toml::from_str(text).map_err(|e| format!("not valid TOML ({e})"))?;
    let Some(list) = doc.get("claude_plugin").and_then(|t| t.get("exclude")) else {
        return Ok(vec![]);
    };
    let Some(items) = list.as_array() else {
        return Err("`[claude_plugin] exclude` is not a list".to_string());
    };
    let mut out = Vec::new();
    for item in items {
        let Some(s) = item.as_str() else {
            return Err("`[claude_plugin] exclude` holds a value that is not a string".to_string());
        };
        let s = s.trim();
        if !s.is_empty() && !s.starts_with('!') {
            out.push(s.to_string());
        }
    }
    Ok(out)
}

fn segments(text: &str) -> Vec<String> {
    text.replace('\\', "/")
        .trim_start_matches("./")
        .split('/')
        .filter(|s| !s.is_empty() && *s != ".")
        .map(|s| s.to_ascii_lowercase())
        .collect()
}

/// True when `path` or one of its parent folders matches `glob`.
pub fn path_matches(glob: &str, path: &str) -> bool {
    let pat = segments(glob);
    let parts = segments(path);
    if pat.is_empty() || parts.is_empty() {
        return false;
    }
    (1..=parts.len()).any(|n| match_segments(&pat, &parts[..n]))
}

fn match_segments(pat: &[String], path: &[String]) -> bool {
    match pat.split_first() {
        None => path.is_empty(),
        Some((p, rest)) if p == "**" => (0..=path.len()).any(|i| match_segments(rest, &path[i..])),
        Some((p, rest)) => match path.split_first() {
            Some((s, tail)) => match_name(p.as_bytes(), s.as_bytes()) && match_segments(rest, tail),
            None => false,
        },
    }
}

fn match_name(pat: &[u8], name: &[u8]) -> bool {
    match pat.split_first() {
        None => name.is_empty(),
        Some((b'*', rest)) => (0..=name.len()).any(|i| match_name(rest, &name[i..])),
        Some((b'?', rest)) => !name.is_empty() && match_name(rest, &name[1..]),
        Some((c, rest)) => name.first() == Some(c) && match_name(rest, &name[1..]),
    }
}

fn file_name(path: &str) -> String {
    path.rsplit(['/', '\\'])
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
}

/// The lockfile or requirements file `path` is, in plain words: any change to one is refused.
pub fn lockfile(path: &str) -> Option<String> {
    let name = file_name(path);
    if let Some((_, what)) = LOCKFILES.iter().find(|(n, _)| *n == name) {
        return Some(what.to_string());
    }
    let pip = |prefix: &str| {
        name.starts_with(prefix) && (name.ends_with(".txt") || name.ends_with(".in"))
    };
    if pip("requirements") || pip("constraints") || path_matches("**/requirements/*.txt", path) {
        return Some("a pip requirements file".to_string());
    }
    None
}

/// True for a manifest whose dependency sections [`dependency_change`] compares.
pub fn is_manifest(path: &str) -> bool {
    matches!(
        file_name(path).as_str(),
        "cargo.toml" | "package.json" | "pyproject.toml" | "setup.py"
    )
}

/// Why a change from `before` to `after` (`None`: the file does not exist) changes a
/// dependency, if it does. Lockfiles and requirements files always do.
pub fn dependency_change(path: &str, before: Option<&str>, after: Option<&str>) -> Option<String> {
    if let Some(what) = lockfile(path) {
        return Some(format!(
            "{path} is {what}; a task never changes a lockfile or a dependency list"
        ));
    }
    let extract: fn(&str) -> Result<BTreeMap<String, String>, String> =
        match file_name(path).as_str() {
            "cargo.toml" => cargo_dependencies,
            "pyproject.toml" => pyproject_dependencies,
            "package.json" => package_json_dependencies,
            "setup.py" => setup_py_dependencies,
            _ => return None,
        };
    let sections = |text: Option<&str>| text.map(extract).unwrap_or_else(|| Ok(BTreeMap::new()));
    match (sections(before), sections(after)) {
        (Ok(a), Ok(b)) if a == b => None,
        (Ok(a), Ok(b)) => {
            let mut changed: Vec<&str> = a
                .keys()
                .chain(b.keys())
                .filter(|k| a.get(*k) != b.get(*k))
                .map(String::as_str)
                .collect();
            changed.dedup();
            Some(format!(
                "{path}: the dependency section {} changed; a task adds, removes or changes no dependency",
                changed.join(", ")
            ))
        }
        (_, Err(e)) | (Err(e), _) => Some(format!(
            "{path}: its dependencies cannot be checked ({e}), so the change is refused"
        )),
    }
}

fn toml_sections(text: &str, keys: &[&[&str]]) -> Result<BTreeMap<String, String>, String> {
    let doc: Toml = toml::from_str(text).map_err(|e| format!("not valid TOML: {e}"))?;
    let mut out = BTreeMap::new();
    for key in keys {
        let mut v = Some(&doc);
        for k in *key {
            v = v.and_then(|t| t.get(*k));
        }
        if let Some(v) = v {
            out.insert(key.join("."), v.to_string());
        }
    }
    Ok(out)
}

const CARGO_DEP_TABLES: &[&str] = &[
    "dependencies",
    "dev-dependencies",
    "dev_dependencies",
    "build-dependencies",
    "build_dependencies",
];

/// `Cargo.toml`: the dependency tables, top level and per target, the workspace's, and the
/// `patch` and `replace` overrides.
pub fn cargo_dependencies(text: &str) -> Result<BTreeMap<String, String>, String> {
    let mut keys: Vec<Vec<&str>> = CARGO_DEP_TABLES.iter().map(|t| vec![*t]).collect();
    keys.extend([
        vec!["workspace", "dependencies"],
        vec!["patch"],
        vec!["replace"],
    ]);
    let refs: Vec<&[&str]> = keys.iter().map(Vec::as_slice).collect();
    let mut out = toml_sections(text, &refs)?;
    let doc: Toml = toml::from_str(text).map_err(|e| format!("not valid TOML: {e}"))?;
    if let Some(targets) = doc.get("target").and_then(Toml::as_table) {
        for (name, table) in targets {
            for t in CARGO_DEP_TABLES {
                if let Some(v) = table.get(*t) {
                    out.insert(format!("target.{name}.{t}"), v.to_string());
                }
            }
        }
    }
    Ok(out)
}

/// `pyproject.toml`: PEP 621 and 735 dependencies, the build requirements, Poetry's, PDM's
/// and uv's.
pub fn pyproject_dependencies(text: &str) -> Result<BTreeMap<String, String>, String> {
    toml_sections(
        text,
        &[
            &["project", "dependencies"],
            &["project", "optional-dependencies"],
            &["build-system"],
            &["dependency-groups"],
            &["tool", "poetry", "dependencies"],
            &["tool", "poetry", "dev-dependencies"],
            &["tool", "poetry", "group"],
            &["tool", "pdm", "dev-dependencies"],
            &["tool", "uv", "dev-dependencies"],
            &["tool", "uv", "sources"],
        ],
    )
}

/// `package.json`: every dependency map, and the overrides of a transitive version.
pub fn package_json_dependencies(text: &str) -> Result<BTreeMap<String, String>, String> {
    let doc: Json = serde_json::from_str(text).map_err(|e| format!("not valid JSON: {e}"))?;
    let mut out = BTreeMap::new();
    for key in [
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
        "bundleDependencies",
        "bundledDependencies",
        "overrides",
        "resolutions",
    ] {
        if let Some(v) = doc.get(key) {
            out.insert(key.to_string(), v.to_string());
        }
    }
    Ok(out)
}

const SETUP_PY_KEYS: &[&str] = &[
    "install_requires",
    "extras_require",
    "setup_requires",
    "tests_require",
    "python_requires",
    "dependency_links",
];

/// `setup.py`: the literal value of each dependency keyword, with whitespace and comments
/// dropped. A keyword given a name instead of a literal (`install_requires=REQS`) cannot be
/// compared, so it is an error and the change is refused.
pub fn setup_py_dependencies(text: &str) -> Result<BTreeMap<String, String>, String> {
    let mut out = BTreeMap::new();
    for key in SETUP_PY_KEYS {
        let mut from = 0;
        while let Some(at) = text[from..].find(key) {
            let start = from + at + key.len();
            from = start;
            let rest = text[start..].trim_start();
            let Some(value) = rest.strip_prefix('=') else {
                continue;
            };
            let value = value.trim_start();
            let literal = literal_prefix(value)
                .ok_or_else(|| format!("`{key}` is not given a literal list, dict or string"))?;
            out.insert(key.to_string(), literal);
            break;
        }
    }
    Ok(out)
}

/// The bracketed or quoted Python literal at the start of `text`, whitespace and comments
/// outside strings removed.
fn literal_prefix(text: &str) -> Option<String> {
    let first = text.chars().next()?;
    if !matches!(first, '[' | '{' | '(' | '"' | '\'') {
        return None;
    }
    let mut out = String::new();
    let mut depth = 0usize;
    let mut quote: Option<char> = None;
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        if let Some(q) = quote {
            out.push(c);
            if c == '\\' {
                out.extend(chars.next());
            } else if c == q {
                quote = None;
                if depth == 0 {
                    return Some(out);
                }
            }
            continue;
        }
        match c {
            '"' | '\'' => {
                quote = Some(c);
                out.push(c);
            }
            '#' => {
                for n in chars.by_ref() {
                    if n == '\n' {
                        break;
                    }
                }
            }
            '[' | '{' | '(' => {
                depth += 1;
                out.push(c);
            }
            ']' | '}' | ')' => {
                depth = depth.checked_sub(1)?;
                out.push(c);
                if depth == 0 {
                    return Some(out);
                }
            }
            c if c.is_whitespace() => {}
            c => out.push(c),
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_defaults_cover_the_spec_categories_on_this_repository() {
        let r = Rules::defaults();
        for (path, what) in [
            ("ling-rs/airgapped/src/lib.rs", "/airgapped"),
            ("ling-rs/src/airgapped.rs", "/airgapped"),
            ("ling-web-rs/src/airgapped.rs", "/airgapped"),
            ("desktop/electron/src/airgapped.ts", "/airgapped"),
            ("ling-docs-rs/src/sandbox.rs", "sandboxing"),
            ("ling-rs/src/sandbox_share.rs", "sandboxing"),
            (
                "dreamference/vllm_server/sandbox_prerequisite.py",
                "sandboxing",
            ),
            ("ling-rs/safegit/src/lib.rs", "sandboxing"),
            ("codex-patches/0019-airgapped.patch", "the Codex patches"),
            ("codex-patches/0013-no-vendor.patch", "the Codex patches"),
            ("dreamference/audit/egress_audit.py", "egress"),
            ("dreamference/audit/strace_parser.py", "egress"),
            ("desktop/electron/src/egress.ts", "egress"),
            ("ling-rs/web/policy.json", "egress"),
            ("ling-rs/src/audit.rs", "egress"),
            ("ling-rs/release-signing.pub", "keys"),
            ("ling-rs/src/release_signature.rs", "keys"),
            ("dreamference/node/node_pairing.py", "keys"),
            ("dreamference/chat/gmail_credentials.py", "keys"),
            (
                "dreamference/remote/remote_certificate_authority.py",
                "keys",
            ),
            ("dreamference/remote/remote_enrolment.py", "keys"),
            ("ling-rs/web/src/auth.rs", "keys"),
            ("scripts/adapters/sign_manifest.py", "keys"),
            ("desktop/electron/mac/sign.mjs", "keys"),
            ("install.sh", "installers"),
            ("install.ps1", "installers"),
            ("dreamference/runner/codex_installer.py", "installers"),
            ("dreamference/chat/desktop_installer.py", "installers"),
            ("desktop/electron/windows/installer.nsi", "installers"),
            ("scripts/install_gb10.sh", "installers"),
            (
                "dreamference/vllm_server/host_safety_setup.py",
                "installers",
            ),
            ("desktop/ui/src/approvals.ts", "approval"),
            ("ling-rs/delegate/src/task.rs", "delegation policy"),
            ("claude-plugin/.mcp.json", "delegation policy"),
            ("dreamference.toml", "delegation policy"),
            (".github/workflows/release-build.yml", "release"),
        ] {
            let rule = r
                .matching(path)
                .unwrap_or_else(|| panic!("{path} is not protected"));
            assert!(rule.what.contains(what), "{path}: {rule:?}");
        }
        for path in [
            "src/a.py",
            "dreamference/hardware/model_matrix_registry.py",
            "ling-rs/src/lib.rs",
            "docs/dev/claude-plugin.md",
            "tests/test_night_shift.py",
            "desktop/ui/src/keymap.ts",
        ] {
            assert_eq!(r.matching(path), None, "{path}");
        }
    }

    #[test]
    fn globs_match_names_folders_and_parents_case_insensitively() {
        assert!(path_matches("**/*sandbox*", "a/b/Sandbox.ts"));
        assert!(path_matches("**/*sandbox*", "sandbox.rs"));
        assert!(
            path_matches("**/*sandbox*", "x/my-sandbox/deep/file.rs"),
            "a parent folder"
        );
        assert!(path_matches("vendor/**", "vendor/a/b.rs"));
        assert!(path_matches("vendor/", "vendor/a/b.rs"));
        assert!(path_matches("vendor", "vendor/a/b.rs"));
        assert!(path_matches("./keys/*.json", "keys/a.json"));
        assert!(path_matches("a/?.rs", "a/b.rs"));
        assert!(!path_matches("a/?.rs", "a/bc.rs"));
        assert!(
            !path_matches("install.sh", "scripts/install.sh"),
            "a rooted glob"
        );
        assert!(
            !path_matches("vendor/*.rs", "vendor/a/b.rs"),
            "`*` stays in one name"
        );
        assert!(!path_matches("", "a"));
    }

    #[test]
    fn the_config_adds_rules_and_never_removes_a_default() {
        let base = std::env::temp_dir().join(format!("ling-exclude-cfg-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        std::fs::write(
            base.join("dreamference.toml"),
            "model = \"m\"\n[claude_plugin]\nexclude = [\"vendor/**\", \"!install.sh\", \"!**\"]\n",
        )
        .unwrap();
        let user = base.join("config.toml");
        std::fs::write(&user, "[claude_plugin]\nexclude = [\"secrets.toml\"]\n").unwrap();
        let r = Rules::load(&base, Some(&user)).unwrap();
        assert_eq!(r.rules.len(), DEFAULT_EXCLUDES.len() + 2);
        let v = r.matching("vendor/x.rs").unwrap();
        assert_eq!(v.source, Source::Config("dreamference.toml".into()));
        assert_eq!(
            v.explain("vendor/x.rs"),
            "vendor/x.rs is protected: it matches `vendor/**`, a `[claude_plugin] exclude` rule in dreamference.toml"
        );
        assert!(r.matching("secrets.toml").is_some());
        let i = r.matching("install.sh").unwrap();
        assert_eq!(i.source, Source::BuiltIn, "`!install.sh` removes nothing");
        assert_eq!(
            i.explain("install.sh"),
            "install.sh is protected: it matches the built-in rule `install.sh` (the installers)"
        );
        // A config that cannot be read as a list of strings refuses, never runs with fewer rules.
        std::fs::write(&user, "[claude_plugin]\nexclude = \"vendor\"\n").unwrap();
        assert!(
            Rules::load(&base, Some(&user))
                .unwrap_err()
                .contains("not a list")
        );
        std::fs::write(&user, "[claude_plugin\n").unwrap();
        assert!(
            Rules::load(&base, Some(&user))
                .unwrap_err()
                .contains("not valid TOML")
        );
        // No file at all: the defaults.
        let none = base.join("none");
        assert_eq!(
            Rules::load(&none, Some(&none.join("c.toml")))
                .unwrap()
                .rules
                .len(),
            DEFAULT_EXCLUDES.len()
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn lockfiles_and_requirements_files_are_never_changed() {
        for path in [
            "Cargo.lock",
            "ling-rs/delegate/Cargo.lock",
            "desktop/ui/package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "poetry.lock",
            "uv.lock",
            "requirements.txt",
            "requirements-dev.txt",
            "docker/requirements.in",
            "constraints.txt",
            "requirements/base.txt",
            "Pipfile",
            "setup.cfg",
        ] {
            let why =
                dependency_change(path, Some("a"), Some("b")).unwrap_or_else(|| panic!("{path}"));
            assert!(why.starts_with(&format!("{path} is ")), "{why}");
            assert!(lockfile(path).is_some(), "{path}");
        }
        assert_eq!(lockfile("src/requirements.py"), None);
        assert_eq!(dependency_change("src/a.py", Some("a"), Some("b")), None);
    }

    #[test]
    fn a_cargo_toml_may_change_outside_its_dependency_tables() {
        let before =
            "[package]\nname = \"a\"\nversion = \"0.1.0\"\n\n[dependencies]\nserde = \"1\"\n";
        let edited = "[package]\nname = \"a\"\nversion = \"0.1.0\"\ndescription = \"x\"\n\n[dependencies]\nserde = \"1\"\n";
        assert_eq!(
            dependency_change("Cargo.toml", Some(before), Some(edited)),
            None
        );
        let reflowed = "[package]\nname = \"a\"\nversion = \"0.1.0\"\n\n[dependencies]\nserde = { version = \"1\" }\n";
        assert!(
            dependency_change("Cargo.toml", Some(before), Some(reflowed)).is_some(),
            "a different value"
        );
        for after in [
            format!("{before}regex = \"1\"\n"),
            before.replace("\"1\"", "\"2\""),
            format!("{before}\n[dev-dependencies]\ntempfile = \"3\"\n"),
            format!("{before}\n[target.'cfg(unix)'.dependencies]\nlibc = \"0.2\"\n"),
            format!("{before}\n[workspace.dependencies]\nlibc = \"0.2\"\n"),
            format!("{before}\n[patch.crates-io]\nserde = {{ path = \"../serde\" }}\n"),
        ] {
            let why = dependency_change("x/Cargo.toml", Some(before), Some(&after))
                .unwrap_or_else(|| panic!("{after}"));
            assert!(
                why.starts_with("x/Cargo.toml: the dependency section "),
                "{why}"
            );
        }
        let why = dependency_change("Cargo.toml", Some(before), Some("[dependencies\n")).unwrap();
        assert!(why.contains("cannot be checked"), "{why}");
        assert!(
            dependency_change("new/Cargo.toml", None, Some(before)).is_some(),
            "a new crate brings serde"
        );
    }

    #[test]
    fn package_json_pyproject_and_setup_py_dependencies_are_compared() {
        let pkg = "{\"name\": \"a\", \"scripts\": {\"test\": \"jest\"}, \"dependencies\": {\"x\": \"1\"}}";
        let scripts = pkg.replace("jest", "vitest");
        assert_eq!(
            dependency_change("desktop/ui/package.json", Some(pkg), Some(&scripts)),
            None
        );
        let dev = pkg.replace("}}", "}, \"devDependencies\": {\"y\": \"2\"}}");
        let why = dependency_change("desktop/ui/package.json", Some(pkg), Some(&dev)).unwrap();
        assert!(why.contains("devDependencies"), "{why}");

        let py =
            "[project]\nname = \"a\"\ndependencies = [\"rich\"]\n[tool.ruff]\nline-length = 100\n";
        assert_eq!(
            dependency_change("pyproject.toml", Some(py), Some(&py.replace("100", "120"))),
            None
        );
        let why = dependency_change(
            "pyproject.toml",
            Some(py),
            Some(&py.replace("\"rich\"", "\"rich\", \"httpx\"")),
        )
        .unwrap();
        assert!(why.contains("project.dependencies"), "{why}");
        let opt = format!("{py}[project.optional-dependencies]\ndev = [\"pytest\"]\n");
        assert!(dependency_change("pyproject.toml", Some(py), Some(&opt)).is_some());

        let setup = "setup(\n    name=\"a\",\n    version=\"1.0\",\n    install_requires=[\n        \"rich\",  # the console\n    ],\n    extras_require={\"dev\": [\"pytest\"]},\n)\n";
        let version = setup.replace("1.0", "1.1");
        assert_eq!(
            dependency_change("setup.py", Some(setup), Some(&version)),
            None
        );
        let comment = setup.replace("# the console", "# output");
        assert_eq!(
            dependency_change("setup.py", Some(setup), Some(&comment)),
            None,
            "a comment is not a dependency"
        );
        let added = setup.replace("\"rich\",", "\"rich\", \"httpx\",");
        let why = dependency_change("setup.py", Some(setup), Some(&added)).unwrap();
        assert!(why.contains("install_requires"), "{why}");
        let extra = setup.replace("[\"pytest\"]", "[\"pytest\", \"mypy\"]");
        assert!(
            dependency_change("setup.py", Some(setup), Some(&extra))
                .unwrap()
                .contains("extras_require")
        );
        // A name instead of a literal cannot be compared.
        let by_name = "REQS = [\"rich\"]\nsetup(install_requires=REQS)\n";
        let why = dependency_change(
            "setup.py",
            Some(by_name),
            Some(&by_name.replace("rich", "httpx")),
        )
        .unwrap();
        assert!(why.contains("cannot be checked"), "{why}");
    }
}
