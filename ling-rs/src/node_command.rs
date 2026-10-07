//! `/node` in the TUI: manage Mightling nodes without leaving `ling` (specs/DREAMFERENCE_MIGHTLING_NODE.md).
//!
//! The command is handled by the TUI and never reaches the model, as `/night` is. It suspends the
//! full-screen interface and runs `ling-admin node …` in the real terminal, outside the agent's
//! sandbox, with the terminal's own stdin, stdout and stderr: provisioning asks for the other
//! machine's password with `getpass` and SSH's own prompt, and those must read the keyboard
//! directly. Nothing the command prints, and nothing typed at its prompts, passes through the TUI,
//! the session, its history or its rollout; the only trace left in the conversation is the one-line
//! summary [`Run::execute`] returns.
//!
//! The Codex side (patch `0025`) is a slash-command entry, an app event, and a call to the TUI's own
//! suspend-and-restore (`Tui::with_restored`, used by the external editor). That function keeps the
//! terminal in raw mode, which suits an editor but not a password prompt (Enter sends `\r`, so a
//! line never ends) or a progress table (no carriage returns), so [`Run::execute`] puts the
//! terminal in cooked mode with `stty sane` for the child and restores the saved settings after.
//! Cooked mode also turns Ctrl-C into SIGINT for the whole foreground process group, `ling`
//! included, so `ling` ignores it until the user is back (the child keeps the default).
//!
//! `/node` is allowed at `/airgapped on`: the user trusts the local network, and the command is the
//! user's own, run outside the agent's sandbox, like a `!` command. It says so before it runs.

use std::ffi::OsString;
use std::io::BufRead;
use std::io::IsTerminal;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;

/// The subcommands `/node` passes to `ling-admin node`.
pub const SUBCOMMANDS: [&str; 5] = ["provision", "add", "list", "status", "remove"];

/// Printed in the terminal, and kept in the history, when the session is at `/airgapped on`.
pub const AIRGAPPED_NOTICE: &str = "airgapped is on: /node talks to machines on your local network only";

const HELP: [&str; 7] = [
    "/node: manage Mightling nodes. Runs ling-admin node in this terminal, outside the agent's sandbox;",
    "nothing it prints or asks for reaches the model.",
    "  /node provision [host…]    install and pair new Sparks (lists the ones on the network without a host)",
    "  /node add <name>           pair with a node that already runs Mightling",
    "  /node list                 the nodes on the network and the paired ones",
    "  /node status [<name>]      what a node serves",
    "  /node remove <name>        unpair a node",
];

/// What `/node <args>` will run, once it is allowed to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Run {
    /// `ling-admin` and its arguments, starting with `node`.
    pub argv: Vec<OsString>,
    /// Printed before the command runs, and kept in the history.
    pub notice: Option<String>,
    /// The words after `/node`, for the summary line.
    pub label: String,
}

/// Decides what `/node <args>` does in the session of `thread_id`: a command to run in the terminal,
/// or the lines to print instead (the help, or why it cannot run here).
pub fn plan<T: std::fmt::Display>(args: &str, thread_id: Option<T>) -> Result<Run, Vec<String>> {
    let thread_id = thread_id.map(|id| id.to_string());
    let airgapped_on = crate::airgapped::resolve(thread_id.as_deref()).level == crate::airgapped::Level::On;
    let interactive = std::io::stdin().is_terminal() && std::io::stdout().is_terminal();
    plan_with(args, interactive, airgapped_on, admin_path())
}

/// [`plan`] with every reading of the machine passed in, for the tests.
pub fn plan_with(
    args: &str,
    interactive: bool,
    airgapped_on: bool,
    admin: Option<PathBuf>,
) -> Result<Run, Vec<String>> {
    let words: Vec<&str> = args.split_whitespace().collect();
    let Some((&subcommand, rest)) = words.split_first() else {
        return Err(help());
    };
    let subcommand = subcommand.to_ascii_lowercase();
    if matches!(subcommand.as_str(), "help" | "-h" | "--help") {
        return Err(help());
    }
    if !SUBCOMMANDS.contains(&subcommand.as_str()) {
        return Err(vec![format!("/node: unknown subcommand {subcommand:?}. {}", usage_line())]);
    }
    if matches!(subcommand.as_str(), "add" | "remove") && rest.is_empty() {
        return Err(vec![format!("/node {subcommand} needs a node's name (see /node list).")]);
    }
    if let Some(bad) = rest.iter().find(|word| word.chars().any(char::is_control)) {
        return Err(vec![format!("/node: {bad:?} is not a host name or an option.")]);
    }
    if !interactive {
        return Err(vec![format!(
            "/node needs a terminal, and this session has none. Run `ling-admin node {}` in a shell.",
            words.join(" ")
        )]);
    }
    let Some(admin) = admin else {
        return Err(vec![
            "/node needs ling-admin, which is not installed on this machine. Run it on a Mightling node, \
             or install the node half with install.sh --role node."
                .to_string(),
        ]);
    };
    let mut argv: Vec<OsString> = vec![admin.into_os_string(), "node".into(), subcommand.clone().into()];
    argv.extend(rest.iter().map(OsString::from));
    let label = std::iter::once(subcommand.as_str()).chain(rest.iter().copied()).collect::<Vec<_>>().join(" ");
    Ok(Run { argv, notice: airgapped_on.then(|| AIRGAPPED_NOTICE.to_string()), label })
}

/// The help `/node` alone prints.
pub fn help() -> Vec<String> {
    HELP.iter().map(|line| line.to_string()).collect()
}

fn usage_line() -> String {
    format!("Usage: /node <{}> [args]", SUBCOMMANDS.join("|"))
}

/// `ling-admin`: the link `ling-admin codex build` and `install.sh` create in `~/.local/bin`, else
/// the first one on `PATH`.
pub fn admin_path() -> Option<PathBuf> {
    let linked = ling_node_locator::home_dir().map(|home| home.join(".local/bin/ling-admin"));
    if let Some(linked) = linked.filter(|path| path.exists()) {
        return Some(linked);
    }
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).map(|dir| dir.join("ling-admin")).find(|candidate| is_executable(candidate))
}

fn is_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata().is_ok_and(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

impl Run {
    /// Runs the command in the terminal and returns the lines for the history: the notice, if any,
    /// and one summary line. Call it while the TUI has handed the terminal back (`with_restored`).
    pub async fn execute(self) -> Vec<String> {
        let label = self.label.clone();
        tokio::task::spawn_blocking(move || self.execute_blocking())
            .await
            .unwrap_or_else(|err| vec![format!("node {label}: could not run ({err})")])
    }

    /// [`Run::execute`] without the runtime: blocks until the command exits and Enter is pressed.
    pub fn execute_blocking(self) -> Vec<String> {
        self.run_with(&mut std::io::stdin().lock(), true)
    }

    /// Runs the command, then reads one line from `enter`. With `cook`, the terminal is in cooked
    /// mode meanwhile; the tests pass neither the terminal nor `cook`, so they never touch a tty.
    fn run_with(self, enter: &mut impl BufRead, cook: bool) -> Vec<String> {
        let saved = cook.then(Terminal::cooked);
        let mut out = std::io::stdout();
        let _ = writeln!(out);
        if let Some(notice) = &self.notice {
            let _ = writeln!(out, "{notice}");
        }
        let _ = out.flush();
        let mut command = Command::new(&self.argv[0]);
        command.args(&self.argv[1..]).stdin(Stdio::inherit()).stdout(Stdio::inherit()).stderr(Stdio::inherit());
        let interrupts = Interrupts::for_child(&mut command);
        let status = command.status();
        let summary = match &status {
            Ok(status) if status.success() => format!("node {}: done (exit 0)", self.label),
            Ok(status) => match status.code() {
                Some(code) => format!("node {}: failed (exit {code})", self.label),
                None => format!("node {}: stopped by a signal", self.label),
            },
            Err(err) => format!("node {}: could not start {} ({err})", self.label, Path::new(&self.argv[0]).display()),
        };
        let _ = write!(out, "\nPress Enter to return to ling. ");
        let _ = out.flush();
        let mut line = String::new();
        let _ = enter.read_line(&mut line);
        // Held until here: Ctrl-C at "Press Enter" must not end the session either.
        drop(interrupts);
        drop(saved);
        self.notice.into_iter().chain(std::iter::once(summary)).collect()
    }
}

/// The terminal's settings while `/node` runs: cooked (`stty sane`) until dropped, then what they
/// were before. Without `stty` (or a terminal) it does nothing.
struct Terminal {
    saved: Option<String>,
}

impl Terminal {
    fn cooked() -> Self {
        let saved = stty(&["-g"]).filter(|settings| !settings.is_empty());
        if saved.is_some() {
            let _ = stty(&["sane"]);
        }
        Terminal { saved }
    }
}

impl Drop for Terminal {
    fn drop(&mut self) {
        if let Some(saved) = &self.saved {
            let _ = stty(&[saved.as_str()]);
        }
    }
}

/// Ctrl-C and Ctrl-\ while the command runs. In cooked mode the terminal turns them into SIGINT and
/// SIGQUIT for its whole foreground process group, which is `ling` as well as the child: without
/// this, Ctrl-C at a password prompt ended the session (seen in tmux). `ling` ignores both until
/// dropped, and the child gets the default back before it starts, so it can still be interrupted.
struct Interrupts {
    #[cfg(unix)]
    saved: Vec<(libc::c_int, libc::sigaction)>,
}

#[cfg(unix)]
const INTERRUPTS: [libc::c_int; 2] = [libc::SIGINT, libc::SIGQUIT];

impl Interrupts {
    #[cfg(unix)]
    fn for_child(command: &mut Command) -> Self {
        use std::os::unix::process::CommandExt;
        // SAFETY: the closure runs between fork and exec and calls only signal(), which is
        // async-signal-safe.
        unsafe {
            command.pre_exec(|| {
                for signal in INTERRUPTS {
                    libc::signal(signal, libc::SIG_DFL);
                }
                Ok(())
            });
        }
        let mut saved = Vec::new();
        for signal in INTERRUPTS {
            // SAFETY: sigaction with a zeroed, then filled, action; the old one is kept to restore.
            unsafe {
                let mut ignore: libc::sigaction = std::mem::zeroed();
                ignore.sa_sigaction = libc::SIG_IGN;
                libc::sigemptyset(&mut ignore.sa_mask);
                let mut old: libc::sigaction = std::mem::zeroed();
                if libc::sigaction(signal, &ignore, &mut old) == 0 {
                    saved.push((signal, old));
                }
            }
        }
        Interrupts { saved }
    }

    #[cfg(not(unix))]
    fn for_child(_command: &mut Command) -> Self {
        Interrupts {}
    }
}

impl Drop for Interrupts {
    fn drop(&mut self) {
        #[cfg(unix)]
        for (signal, old) in &self.saved {
            // SAFETY: restores the action saved in for_child.
            unsafe {
                libc::sigaction(*signal, old, std::ptr::null_mut());
            }
        }
    }
}

/// Runs `stty <args>` on this process's terminal and returns its output.
fn stty(args: &[&str]) -> Option<String> {
    let output = Command::new("stty").args(args).stdin(Stdio::inherit()).stderr(Stdio::null()).output().ok()?;
    output.status.success().then(|| String::from_utf8_lossy(&output.stdout).trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn admin() -> Option<PathBuf> {
        Some(PathBuf::from("/home/u/.local/bin/ling-admin"))
    }

    fn argv(run: &Run) -> Vec<String> {
        run.argv.iter().map(|arg| arg.to_string_lossy().into_owned()).collect()
    }

    #[test]
    fn no_arguments_prints_the_help() {
        let lines = plan_with("", true, false, admin()).unwrap_err();
        assert!(lines[0].starts_with("/node: manage Mightling nodes"));
        assert!(lines.iter().any(|line| line.contains("/node provision")));
        assert_eq!(plan_with("  help ", true, false, admin()).unwrap_err(), lines);
    }

    #[test]
    fn each_subcommand_builds_the_ling_admin_argv() {
        let cases = [
            ("provision", vec!["node", "provision"]),
            ("provision spark-1a2b gx10-77c0", vec!["node", "provision", "spark-1a2b", "gx10-77c0"]),
            ("add gx10-77c0", vec!["node", "add", "gx10-77c0"]),
            ("add gx10-77c0 --user owner --ssh-port 2222", vec!["node", "add", "gx10-77c0", "--user", "owner", "--ssh-port", "2222"]),
            ("list", vec!["node", "list"]),
            ("status", vec!["node", "status"]),
            ("STATUS spark-1", vec!["node", "status", "spark-1"]),
            ("remove spark-1", vec!["node", "remove", "spark-1"]),
        ];
        for (args, expected) in cases {
            let run = plan_with(args, true, false, admin()).unwrap();
            let got = argv(&run);
            assert_eq!(got[0], "/home/u/.local/bin/ling-admin", "{args}");
            assert_eq!(got[1..], expected, "{args}");
            assert_eq!(run.notice, None, "{args}");
        }
    }

    #[test]
    fn the_label_is_the_words_after_node() {
        let run = plan_with("  provision   spark-1a2b ", true, false, admin()).unwrap();
        assert_eq!(run.label, "provision spark-1a2b");
    }

    #[test]
    fn unknown_subcommands_and_missing_names_are_refused() {
        let unknown = plan_with("reboot spark-1", true, false, admin()).unwrap_err();
        assert!(unknown[0].contains("unknown subcommand \"reboot\""), "{unknown:?}");
        // Anything after it is ling-admin's to judge: an option's value cannot be told from a name here.
        for args in ["add", "remove"] {
            let refused = plan_with(args, true, false, admin()).unwrap_err();
            assert!(refused[0].contains("needs a node's name"), "{args}: {refused:?}");
        }
        let control = plan_with("add spark\u{7}1", true, false, admin()).unwrap_err();
        assert!(control[0].contains("is not a host name"), "{control:?}");
    }

    #[test]
    fn at_airgapped_on_it_runs_and_says_why_that_is_allowed() {
        let run = plan_with("provision spark-1a2b", true, true, admin()).unwrap();
        assert_eq!(run.notice.as_deref(), Some(AIRGAPPED_NOTICE));
        assert_eq!(argv(&run)[1..], ["node", "provision", "spark-1a2b"]);
        assert_eq!(AIRGAPPED_NOTICE, "airgapped is on: /node talks to machines on your local network only");
    }

    #[test]
    fn without_a_terminal_it_says_to_use_a_shell() {
        let refused = plan_with("list", false, false, admin()).unwrap_err();
        assert_eq!(refused, ["/node needs a terminal, and this session has none. Run `ling-admin node list` in a shell."]);
        // The help needs no terminal.
        assert!(plan_with("", false, false, admin()).is_err_and(|lines| lines == help()));
    }

    #[test]
    fn without_ling_admin_it_says_where_to_run_it() {
        let refused = plan_with("list", true, false, None).unwrap_err();
        assert!(refused[0].contains("ling-admin, which is not installed"), "{refused:?}");
    }

    #[cfg(unix)]
    #[test]
    fn execute_returns_the_summary_and_the_notice() {
        // `true` and `false` stand in for ling-admin, and a reader holding one newline for Enter. They
        // inherit the test's stdin, which they never read.
        let enter = || std::io::Cursor::new(b"\n".to_vec());
        let ok = Run { argv: vec!["true".into()], notice: Some(AIRGAPPED_NOTICE.to_string()), label: "list".into() };
        assert_eq!(ok.run_with(&mut enter(), false), [AIRGAPPED_NOTICE, "node list: done (exit 0)"]);
        let failed = Run { argv: vec!["false".into()], notice: None, label: "add spark-1".into() };
        assert_eq!(failed.run_with(&mut enter(), false), ["node add spark-1: failed (exit 1)"]);
        let missing = Run { argv: vec!["/nonexistent/ling-admin".into()], notice: None, label: "list".into() };
        assert!(missing.run_with(&mut enter(), false)[0].starts_with("node list: could not start /nonexistent/ling-admin"));
    }

    #[cfg(unix)]
    #[test]
    fn the_child_can_still_be_interrupted_while_ling_ignores_ctrl_c() {
        // A shell started with SIGINT ignored would survive this and exit 0.
        let enter = || std::io::Cursor::new(b"\n".to_vec());
        let argv = ["sh", "-c", "kill -INT $$; exit 0"].map(OsString::from).to_vec();
        let interrupted = Run { argv, notice: None, label: "provision".into() };
        assert_eq!(interrupted.run_with(&mut enter(), false), ["node provision: stopped by a signal"]);
    }
}
