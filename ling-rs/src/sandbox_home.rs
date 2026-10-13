//! `ling sandbox-home`: what of the home folder the commands `ling` runs can read
//! (specs/DREAMFERENCE_MIGHTLING_SANDBOX_HOME.md).
//!
//! The rule itself lives in the leaf crate `ling-sandbox-home` (`sandbox-home/`), because Codex's
//! sandbox helper applies it to every command (patch 0019) and must not depend on the launcher. This
//! is the user's view of it: the paths a command started here would see, those hidden again, and
//! how to add one. It reads the same inputs the helper reads, from this process's environment and
//! working directory, which a command inherits.

use std::path::Path;

pub use ling_sandbox_home::MODE_KEY;
pub use ling_sandbox_home::READABLE_KEY;
pub use ling_sandbox_home::SECTION;
use ling_sandbox_home::Plan;

const USAGE: &str = "Usage: ling sandbox-home";

/// `ling sandbox-home`.
pub fn run_cli(args: &[String]) -> i32 {
    if !args.is_empty() {
        println!("{USAGE}");
        return 2;
    }
    if !cfg!(target_os = "linux") {
        println!("The home folder is hidden from sandboxed commands on Linux only; this system's sandbox is unchanged.");
        return 0;
    }
    let plan = ling_sandbox_home::Inputs::from_process().and_then(|inputs| ling_sandbox_home::plan(&inputs));
    for line in lines(plan.as_ref()) {
        println!("{line}");
    }
    0
}

/// What `ling sandbox-home` prints for `plan` (`None`: the rule is off or there is no home folder).
pub fn lines(plan: Option<&Plan>) -> Vec<String> {
    let config = "~/.config/dreamference/config.toml";
    let Some(plan) = plan else {
        return vec![
            "Sandboxed commands can read the whole home folder.".to_string(),
            format!("The rule is off ([{SECTION}] {MODE_KEY} = \"visible\" in {config}), or there is no home folder."),
        ];
    };
    let home = plan.home.display().to_string();
    let show = |path: &Path| match path.strip_prefix(&plan.home) {
        Ok(rest) => format!("~/{}", rest.display()),
        Err(_) => path.display().to_string(),
    };
    let mut lines = vec![
        format!("Under the read-only and workspace-write sandboxes, commands cannot read {home}, except:"),
        "  the workspace and every folder they may write".to_string(),
    ];
    lines.extend(plan.readable.iter().map(|path| format!("  {}", show(path))));
    if !plan.hidden.is_empty() {
        lines.push("Hidden again inside those:".to_string());
        lines.extend(plan.hidden.iter().map(|path| format!("  {}", show(path))));
    }
    lines.push(format!("Everything outside {home} reads as before. Full Access has no sandbox and sees everything."));
    lines.push(format!("To add a path (read-only): [{SECTION}] {READABLE_KEY} = [\"~/data\"] in {config}."));
    lines.push(format!("To turn this off: [{SECTION}] {MODE_KEY} = \"visible\" there. A repository's dreamference.toml is never read for either."));
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn the_status_names_what_stays_readable_and_how_to_add_a_path() {
        let plan = Plan {
            home: PathBuf::from("/home/u"),
            outside: vec![PathBuf::from("/usr")],
            readable: vec![PathBuf::from("/home/u/.cargo"), PathBuf::from("/home/u/projects/app")],
            hidden: vec![PathBuf::from("/home/u/.cargo/credentials.toml")],
        };
        let text = lines(Some(&plan)).join("\n");
        assert!(text.contains("cannot read /home/u, except:"), "{text}");
        assert!(text.contains("  ~/.cargo\n") && text.contains("  ~/projects/app\n"), "{text}");
        assert!(text.contains("Hidden again inside those:\n  ~/.cargo/credentials.toml"), "{text}");
        assert!(text.contains("[sandbox] readable = [") && text.contains("[sandbox] home = \"visible\""), "{text}");
        assert!(!text.contains("/usr"), "{text}");
    }

    #[test]
    fn with_the_rule_off_the_status_says_so() {
        let text = lines(None).join("\n");
        assert!(text.starts_with("Sandboxed commands can read the whole home folder."), "{text}");
    }
}
