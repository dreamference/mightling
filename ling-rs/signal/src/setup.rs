//! `ling-signal setup` (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §3, §4.1, §6): installs the bridge
//! and registers its number. Run as the user; every step that changes the machine outside the home
//! folder runs under sudo and is printed before it runs, as `ling-admin host setup` does.
//!
//! The install is a list of [`Step`]s built by [`install_plan`], so `--dry-run` prints exactly what
//! a real run would do and the tests check the list. Registering the number, the CAPTCHA, the SMS
//! code and pairing the owner are interactive and come after the plan.

use std::path::Path;
use std::path::PathBuf;

use crate::unit;

/// The signal-cli release this bridge was tested with, and the SHA-256 of its tarball.
pub const SIGNAL_CLI_VERSION: &str = "0.14.9";
pub const SIGNAL_CLI_SHA256: &str = "c32b87f587198cbd277c9d71228716ee6047ee2b63e8e88728e8280d9407d65a";
/// The libsignal version that signal-cli release bundles (`lib/libsignal-client-0.103.0.jar`), and
/// the arm64 build of its JNI library (exquo/signal-libs-build), which signal-cli does not ship.
pub const LIBSIGNAL_VERSION: &str = "0.103.0";
pub const LIBSIGNAL_AARCH64_SHA256: &str = "bc112187bef1989ac5a3de14a0e43c53ba6c0c7fbc20b97cc363e92a7012728f";
pub const JAVA_PACKAGE: &str = "openjdk-25-jre-headless";
pub const OPT_DIR: &str = "/opt/mightling";

pub fn signal_cli_url() -> String {
    format!("https://github.com/AsamK/signal-cli/releases/download/v{SIGNAL_CLI_VERSION}/signal-cli-{SIGNAL_CLI_VERSION}.tar.gz")
}

pub fn libsignal_url() -> String {
    format!(
        "https://github.com/exquo/signal-libs-build/releases/download/libsignal_v{LIBSIGNAL_VERSION}/libsignal_jni.so-v{LIBSIGNAL_VERSION}-aarch64-unknown-linux-gnu.tar.gz"
    )
}

pub fn signal_cli_home() -> PathBuf {
    PathBuf::from(OPT_DIR).join(format!("signal-cli-{SIGNAL_CLI_VERSION}"))
}

pub fn signal_cli_program() -> PathBuf {
    signal_cli_home().join("bin").join("signal-cli")
}

pub fn native_dir() -> PathBuf {
    signal_cli_home().join("lib").join("native")
}

/// The environment signal-cli runs with: Java 25, and on arm64 the library path of the JNI build.
pub fn signal_cli_env(arch: &str) -> Vec<(String, String)> {
    let java_arch = if arch == "aarch64" { "arm64" } else { "amd64" };
    let mut env = vec![("JAVA_HOME".to_string(), format!("/usr/lib/jvm/java-25-openjdk-{java_arch}"))];
    if arch == "aarch64" {
        env.push(("JAVA_OPTS".to_string(), format!("-Djava.library.path={}", native_dir().display())));
    }
    env
}

/// One step of the install.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Step {
    pub what: String,
    pub command: Vec<String>,
    /// Runs under sudo.
    pub root: bool,
    /// Text written to the command's standard input (a unit file through `tee`).
    pub stdin: Option<String>,
}

impl Step {
    fn user(what: &str, command: &[&str]) -> Step {
        Step { what: what.to_string(), command: command.iter().map(|s| s.to_string()).collect(), root: false, stdin: None }
    }

    fn root(what: &str, command: &[&str]) -> Step {
        Step { root: true, ..Step::user(what, command) }
    }

    pub fn display(&self) -> String {
        let quoted: Vec<String> = self.command.iter().map(|part| shell_quote(part)).collect();
        format!("{}{}", if self.root { "sudo " } else { "" }, quoted.join(" "))
    }
}

pub fn shell_quote(part: &str) -> String {
    if !part.is_empty() && part.chars().all(|c| c.is_ascii_alphanumeric() || "-_./=:,+@%".contains(c)) {
        part.to_string()
    } else {
        format!("'{}'", part.replace('\'', r"'\''"))
    }
}

/// Everything up to registering the number. `bridge` is the `ling-signal` binary being installed,
/// `work` a scratch folder of the user's for downloads, `arch` `std::env::consts::ARCH`.
pub fn install_plan(bridge: &Path, work: &Path, arch: &str, account_exists: bool, java_present: bool) -> Vec<Step> {
    let tarball = work.join(format!("signal-cli-{SIGNAL_CLI_VERSION}.tar.gz"));
    let tarball = tarball.to_string_lossy().into_owned();
    let mut steps = Vec::new();
    if !account_exists {
        steps.push(Step::root(
            "Create the system account the bridge runs as (no login, no home folder)",
            &["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", unit::ACCOUNT],
        ));
    }
    if !java_present {
        steps.push(Step::root("Install the Java runtime signal-cli needs (Java 25)", &["apt-get", "install", "-y", JAVA_PACKAGE]));
    }
    steps.push(Step::user("Download signal-cli", &["curl", "-fsSL", "-o", &tarball, &signal_cli_url()]));
    steps.push(Step::user("Check it against the pinned SHA-256", &["sh", "-c", &format!("echo '{SIGNAL_CLI_SHA256}  {tarball}' | sha256sum -c -")]));
    steps.push(Step::root("Unpack it under /opt/mightling", &["install", "-d", "-m", "0755", OPT_DIR]));
    steps.push(Step::root("", &["tar", "-xzf", &tarball, "-C", OPT_DIR, "--no-same-owner"]));
    if arch == "aarch64" {
        let lib = work.join("libsignal_jni.tar.gz").to_string_lossy().into_owned();
        let native = native_dir().to_string_lossy().into_owned();
        steps.push(Step::user("Download libsignal's arm64 JNI library (signal-cli ships it for x86_64 only)", &["curl", "-fsSL", "-o", &lib, &libsignal_url()]));
        steps.push(Step::user("Check it against the pinned SHA-256", &["sh", "-c", &format!("echo '{LIBSIGNAL_AARCH64_SHA256}  {lib}' | sha256sum -c -")]));
        steps.push(Step::user("", &["tar", "-xzf", &lib, "-C", &work.to_string_lossy()]));
        steps.push(Step::root("Put it beside signal-cli", &["install", "-D", "-m", "0644", &work.join("libsignal_jni.so").to_string_lossy(), &format!("{native}/libsignal_jni.so")]));
    }
    steps.push(Step::root("Install the bridge where the system account can run it", &["install", "-D", "-m", "0755", &bridge.to_string_lossy(), unit::BRIDGE_PATH]));
    steps.push(Step::root("Create its private state folder", &["install", "-d", "-m", "0700", "-o", unit::ACCOUNT, "-g", unit::ACCOUNT, unit::STATE_DIR]));
    let mut write_unit = Step::root("Write the system unit", &["tee", &format!("/etc/systemd/system/{}", unit::UNIT_NAME)]);
    write_unit.stdin = Some(unit::unit_text("768M", "256m"));
    steps.push(write_unit);
    steps.push(Step::root("", &["systemctl", "daemon-reload"]));
    steps
}

/// `remove` (§6): stop and disable the unit, take the number off Signal, delete the bridge's data,
/// the unit, the installed files and the account. The Ask threads stay. `unregister` needs the
/// network and a working account, so a failure there is reported and the rest still runs.
pub fn remove_plan(arch: &str, account: Option<&str>) -> Vec<Step> {
    let mut steps = vec![Step::root("Stop and disable the bridge", &["systemctl", "disable", "--now", unit::UNIT_NAME])];
    if let Some(account) = account {
        let command = signal_cli_command(arch, account, &["unregister"]);
        // `signal_cli_command` already starts with sudo -u; run it as given.
        steps.push(Step { what: format!("Take {account} off Signal (unregister)"), command, root: false, stdin: None });
    }
    steps.push(Step::user("End the bridge's pairing with `ling web`", &["ling", "web", "revoke", crate::agent::DEVICE_NAME]));
    steps.push(Step::root("Delete the bridge's data: keys, state, the device cookie", &["rm", "-rf", "--one-file-system", unit::STATE_DIR]));
    steps.push(Step::root("Delete the unit", &["rm", "-f", &format!("/etc/systemd/system/{}", unit::UNIT_NAME)]));
    steps.push(Step::root("", &["systemctl", "daemon-reload"]));
    steps.push(Step::root("Delete signal-cli and the bridge binary", &["rm", "-rf", "--one-file-system", &signal_cli_home().to_string_lossy(), unit::BRIDGE_PATH]));
    steps.push(Step::root("Delete the system account", &["userdel", unit::ACCOUNT]));
    steps
}

/// A command run as the bridge's account, e.g. signal-cli on its data.
pub fn as_bridge(command: &[String]) -> Vec<String> {
    let mut full = vec!["sudo".to_string(), "-u".to_string(), unit::ACCOUNT.to_string()];
    full.extend(command.iter().cloned());
    full
}

/// signal-cli on the bridge's data, with its environment, as the bridge's account.
pub fn signal_cli_command(arch: &str, account: &str, args: &[&str]) -> Vec<String> {
    let mut command = vec!["env".to_string()];
    for (key, value) in signal_cli_env(arch) {
        command.push(format!("{key}={value}"));
    }
    command.push(signal_cli_program().to_string_lossy().into_owned());
    command.push("--config".to_string());
    command.push(format!("{}/signal-cli", unit::STATE_DIR));
    command.push("-a".to_string());
    command.push(account.to_string());
    command.extend(args.iter().map(|s| s.to_string()));
    as_bridge(&command)
}

/// Whether a number is written the way Signal wants it: `+`, then 7 to 15 digits.
pub fn valid_number(number: &str) -> bool {
    let digits = number.strip_prefix('+').unwrap_or("");
    (7..=15).contains(&digits.len()) && digits.chars().all(|c| c.is_ascii_digit()) && !digits.starts_with('0')
}

/// A `signalcaptcha://…` link as signal-cli takes it.
pub fn captcha_token(link: &str) -> Option<String> {
    let link = link.trim();
    link.starts_with("signalcaptcha://").then(|| link.to_string()).filter(|l| l.len() > "signalcaptcha://".len())
}

/// The eight-digit code in `ling web pair`'s output (`Pairing code: 1234 5678`).
pub fn pairing_code_in(output: &str) -> Option<String> {
    output.lines().find_map(|line| {
        let rest = line.split_once("Pairing code:")?.1;
        let digits: String = rest.chars().filter(char::is_ascii_digit).collect();
        (digits.len() == 8).then_some(digits)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_plan_on_arm64_brings_the_jni_library_and_prints_every_root_step() {
        let plan = install_plan(Path::new("/home/u/.local/share/dreamference/mightling/bin/ling-signal"), Path::new("/tmp/w"), "aarch64", false, false);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(shown[0].starts_with("sudo useradd --system --no-create-home"), "{shown:?}");
        assert!(shown.iter().any(|s| s == "sudo apt-get install -y openjdk-25-jre-headless"));
        assert!(shown.iter().any(|s| s.contains(SIGNAL_CLI_SHA256) && s.contains("sha256sum -c")));
        assert!(shown.iter().any(|s| s.contains(LIBSIGNAL_AARCH64_SHA256)));
        assert!(shown.iter().any(|s| s.ends_with("/opt/mightling/signal-cli-0.14.9/lib/native/libsignal_jni.so")));
        assert!(shown.iter().any(|s| s == "sudo install -d -m 0700 -o mightling-signal -g mightling-signal /var/lib/mightling-signal"));
        let unit = plan.iter().find(|s| s.stdin.is_some()).unwrap();
        assert!(unit.root && unit.stdin.as_ref().unwrap().contains("ProtectHome=yes"));
        // Every download is checked before anything is unpacked as root.
        let download = shown.iter().position(|s| s.starts_with("curl") && s.contains("signal-cli-0.14.9")).unwrap();
        let check = shown.iter().position(|s| s.contains(SIGNAL_CLI_SHA256)).unwrap();
        let unpack = shown.iter().position(|s| s.starts_with("sudo tar")).unwrap();
        assert!(download < check && check < unpack);
        // Nothing is opened to everyone.
        assert!(!shown.iter().any(|s| s.contains("777") || s.contains("chmod")));
    }

    #[test]
    fn an_existing_account_and_java_are_not_redone_and_x86_needs_no_library() {
        let plan = install_plan(Path::new("/b"), Path::new("/w"), "x86_64", true, true);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(!shown.iter().any(|s| s.contains("useradd") || s.contains("apt-get") || s.contains("libsignal")));
    }

    #[test]
    fn signal_cli_runs_as_the_bridges_account_with_java_25() {
        let command = signal_cli_command("aarch64", "+15550000", &["register", "--voice"]);
        let text = command.join(" ");
        assert!(text.starts_with("sudo -u mightling-signal env JAVA_HOME=/usr/lib/jvm/java-25-openjdk-arm64 JAVA_OPTS=-Djava.library.path=/opt/mightling/signal-cli-0.14.9/lib/native "));
        assert!(text.ends_with("--config /var/lib/mightling-signal/signal-cli -a +15550000 register --voice"));
    }

    #[test]
    fn remove_deletes_only_what_setup_made() {
        let plan = remove_plan("aarch64", Some("+15550000"));
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert_eq!(shown[0], "sudo systemctl disable --now mightling-signal.service");
        assert!(shown[1].starts_with("sudo -u mightling-signal env ") && shown[1].ends_with("-a +15550000 unregister"), "{}", shown[1]);
        for line in &shown {
            if line.contains("rm ") {
                assert!(
                    line.contains("/var/lib/mightling-signal") || line.contains("/etc/systemd/system/mightling-signal.service") || line.contains("/opt/mightling/signal-cli-0.14.9"),
                    "{line}"
                );
                assert!(!line.contains(" /home") && !line.contains(".mightling/ask"), "the Ask threads stay: {line}");
            }
        }
        assert!(shown.contains(&"ling web revoke signal-bridge".to_string()));
        assert_eq!(shown.last().unwrap(), "sudo userdel mightling-signal");
        assert!(!remove_plan("aarch64", None).iter().any(|s| s.display().contains("unregister")));
    }

    #[test]
    fn inputs_are_checked() {
        assert!(valid_number("+15551234567"));
        assert!(!valid_number("15551234567"));
        assert!(!valid_number("+1555"));
        assert!(!valid_number("+0123456789"));
        assert!(!valid_number("+1555-123-4567"));
        assert_eq!(captcha_token(" signalcaptcha://signal-hcaptcha.abc \n").as_deref(), Some("signalcaptcha://signal-hcaptcha.abc"));
        assert_eq!(captcha_token("https://example.com"), None);
        assert_eq!(pairing_code_in("Pairing code: 1234 5678\nIt works once, for ten minutes.").as_deref(), Some("12345678"));
        assert_eq!(pairing_code_in("no code here 12345678"), None);
        assert_eq!(shell_quote("it's"), r"'it'\''s'");
        assert_eq!(shell_quote("plain-path/a.b"), "plain-path/a.b");
    }
}
