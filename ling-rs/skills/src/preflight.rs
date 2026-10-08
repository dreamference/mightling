//! Whether a skill can work on this machine (spec §4, "Honour as a preflight").
//!
//! A skill that names another operating system, or a program that is not installed, is not
//! offered to the model. A missing environment variable is only noted: the user may set it in the
//! session.

use std::collections::BTreeSet;
use std::path::Path;
use std::path::PathBuf;

use crate::frontmatter::Frontmatter;

/// The facts about this machine a preflight compares against. Tests build their own.
#[derive(Debug, Clone, Default)]
pub struct Host {
    /// `linux`, `macos` or `windows`.
    pub os: String,
    /// The directories of `PATH`.
    pub path: Vec<PathBuf>,
    /// Names of the environment variables that are set.
    pub env: BTreeSet<String>,
}

impl Host {
    /// This process's operating system, `PATH` and environment.
    pub fn current() -> Host {
        Host {
            os: match std::env::consts::OS {
                "macos" => "macos",
                "windows" => "windows",
                _ => "linux",
            }
            .to_string(),
            path: std::env::var_os("PATH")
                .map(|path| std::env::split_paths(&path).collect())
                .unwrap_or_default(),
            env: std::env::vars_os().map(|(name, _)| name.to_string_lossy().into_owned()).collect(),
        }
    }

    /// Whether a program of this name is on `PATH`.
    pub fn has_program(&self, name: &str) -> bool {
        // A skill names a program, not a path; anything else is not looked up.
        if name.is_empty() || name.contains(['/', '\\']) {
            return false;
        }
        self.path.iter().any(|dir| is_executable(&dir.join(name)))
    }
}

#[cfg(unix)]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    path.metadata().is_ok_and(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn is_executable(path: &Path) -> bool {
    path.is_file() || path.with_extension("exe").is_file()
}

/// The outcome for one skill.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Nothing it declares rules it out here.
    Fits,
    /// It cannot work here; the text says why, e.g. `needs gh`.
    Unavailable(String),
    /// Its author marked it `disable-model-invocation`.
    ManualOnly,
}

/// Checks what a skill declares against `host`. Returns the verdict and the notes to show beside
/// it (`needs FOO_API_KEY`).
pub fn check(frontmatter: &Frontmatter, host: &Host) -> (Verdict, Vec<String>) {
    let notes: Vec<String> = frontmatter
        .required_env
        .iter()
        .filter(|name| !host.env.contains(*name))
        .map(|name| format!("needs {name}"))
        .collect();
    if !frontmatter.platforms.is_empty() && !frontmatter.platforms.contains(&host.os) {
        return (Verdict::Unavailable(format!("for {} only", frontmatter.platforms.join(", "))), notes);
    }
    let missing: Vec<&str> = frontmatter
        .required_bins
        .iter()
        .filter(|name| !host.has_program(name))
        .map(String::as_str)
        .collect();
    if !missing.is_empty() {
        return (Verdict::Unavailable(format!("needs {}", missing.join(", "))), notes);
    }
    if !frontmatter.any_bins.is_empty() && !frontmatter.any_bins.iter().any(|name| host.has_program(name)) {
        return (Verdict::Unavailable(format!("needs one of {}", frontmatter.any_bins.join(", "))), notes);
    }
    if frontmatter.manual_only {
        return (Verdict::ManualOnly, notes);
    }
    (Verdict::Fits, notes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::frontmatter::parse;

    fn host_with(test: &str, programs: &[&str], env: &[&str]) -> (Host, PathBuf) {
        let dir = crate::testing::scratch(&format!("preflight-{test}"));
        for program in programs {
            crate::testing::write_executable(&dir.join(program), "#!/bin/sh\n");
        }
        std::fs::write(dir.join("not-executable"), "").unwrap_or_default();
        let host = Host {
            os: "linux".to_string(),
            path: vec![dir.clone()],
            env: env.iter().map(|name| name.to_string()).collect(),
        };
        (host, dir)
    }

    fn verdict(frontmatter: &str, host: &Host) -> (Verdict, Vec<String>) {
        let text = format!("---\nname: probe\ndescription: A probe.\n{frontmatter}---\n");
        check(&parse(&text).unwrap_or_default(), host)
    }

    #[test]
    fn a_skill_for_another_system_is_unavailable() {
        let (host, _) = host_with("system", &[], &[]);
        assert_eq!(verdict("platforms: [macos]\n", &host).0, Verdict::Unavailable("for macos only".into()));
        assert_eq!(verdict("platforms: [macos, linux]\n", &host).0, Verdict::Fits);
        assert_eq!(
            verdict("metadata:\n  openclaw:\n    os: [darwin, win32]\n", &host).0,
            Verdict::Unavailable("for macos, windows only".into())
        );
    }

    #[test]
    fn a_missing_program_makes_it_unavailable_and_an_installed_one_does_not() {
        let (host, _) = host_with("programs", &["gh"], &[]);
        let requires = |bins: &str| format!("metadata:\n  openclaw:\n    requires:\n      {bins}\n");
        assert_eq!(verdict(&requires("bins: [gh]"), &host).0, Verdict::Fits);
        assert_eq!(
            verdict(&requires("bins: [gh, imsg, jq]"), &host).0,
            Verdict::Unavailable("needs imsg, jq".into())
        );
        assert_eq!(verdict(&requires("anyBins: [rg, gh]"), &host).0, Verdict::Fits);
        assert_eq!(
            verdict(&requires("anyBins: [rg, ag]"), &host).0,
            Verdict::Unavailable("needs one of rg, ag".into())
        );
        // A file on PATH that cannot be executed is not a program, and a path is not looked up.
        assert!(!host.has_program("not-executable"));
        assert!(!host.has_program("../gh"));
    }

    #[test]
    fn a_missing_variable_is_a_note_not_a_refusal() {
        let (host, _) = host_with("variables", &[], &["HAVE_IT"]);
        let (verdict, notes) = verdict("required_environment_variables: [HAVE_IT, FOO_API_KEY]\n", &host);
        assert_eq!(verdict, Verdict::Fits);
        assert_eq!(notes, vec!["needs FOO_API_KEY".to_string()]);
    }

    #[test]
    fn a_manual_only_skill_is_not_offered_but_a_wrong_system_is_named_first() {
        let (host, _) = host_with("manual", &[], &[]);
        assert_eq!(verdict("disable-model-invocation: true\n", &host).0, Verdict::ManualOnly);
        assert_eq!(
            verdict("disable-model-invocation: true\nplatforms: [windows]\n", &host).0,
            Verdict::Unavailable("for windows only".into())
        );
    }
}
