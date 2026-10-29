//! `ling signal setup` (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §3, §4.1, §6): installs the bridge
//! and registers its number. Run as the user; every step that changes the machine outside the home
//! folder runs under sudo and is printed before it runs, as `ling-admin host setup` does.
//!
//! The install is a list of [`Step`]s built by [`install_plan`], so `--dry-run` prints exactly what
//! a real run would do and the tests check the list. Registering the number, the CAPTCHA, the SMS
//! code and pairing the owner are interactive and come after the plan.

use std::path::Path;
use std::path::PathBuf;

use crate::unit;
use crate::unit::Instance;

/// The signal-cli release this bridge was tested with, and the SHA-256 of its tarball.
pub const SIGNAL_CLI_VERSION: &str = "0.14.9";
pub const SIGNAL_CLI_SHA256: &str = "c32b87f587198cbd277c9d71228716ee6047ee2b63e8e88728e8280d9407d65a";
/// The libsignal version that signal-cli release bundles (`lib/libsignal-client-0.103.0.jar`), and
/// the arm64 build of its JNI library (exquo/signal-libs-build), which signal-cli does not ship.
pub const LIBSIGNAL_VERSION: &str = "0.103.0";
pub const LIBSIGNAL_AARCH64_SHA256: &str = "bc112187bef1989ac5a3de14a0e43c53ba6c0c7fbc20b97cc363e92a7012728f";
/// The Java runtime signal-cli 0.14 needs (Java 25 or later): Eclipse Temurin's JRE, fetched by
/// setup and pinned by URL and SHA-256 like everything else it unpacks, never bundled and never
/// taken from the distribution, so every machine runs the build Phase 0 measured on (§12.1) and
/// setup needs no package manager for it. The hashes were computed from the downloaded archives and
/// match the ones Adoptium publishes beside them.
pub const JAVA_VERSION: &str = "25.0.4.1+1";
pub const JRE_AARCH64_SHA256: &str = "34828cbb93ed31c281c84ecb31ddab655d11a802f263c1fc019d42e9e0230fed";
pub const JRE_X86_64_SHA256: &str = "1731a34baadec5479258ea0202e4d5d865d2efeee60cb0c7d7eb056fe96ca219";
pub const OPT_DIR: &str = "/opt/mightling";

pub fn signal_cli_url() -> String {
    format!("https://github.com/AsamK/signal-cli/releases/download/v{SIGNAL_CLI_VERSION}/signal-cli-{SIGNAL_CLI_VERSION}.tar.gz")
}

/// The maintainer's detached OpenPGP signature of that tarball, published beside it.
pub fn signal_cli_signature_url() -> String {
    format!("{}.asc", signal_cli_url())
}

pub fn libsignal_url() -> String {
    format!(
        "https://github.com/exquo/signal-libs-build/releases/download/libsignal_v{LIBSIGNAL_VERSION}/libsignal_jni.so-v{LIBSIGNAL_VERSION}-aarch64-unknown-linux-gnu.tar.gz"
    )
}

/// The JRE archive for this machine and its pinned SHA-256; None where no build is pinned.
pub fn jre_archive(arch: &str) -> Option<(String, &'static str)> {
    let (name, sha) = match arch {
        "aarch64" => ("aarch64", JRE_AARCH64_SHA256),
        "x86_64" => ("x64", JRE_X86_64_SHA256),
        _ => return None,
    };
    let tag = JAVA_VERSION.replace('+', "%2B");
    let file = JAVA_VERSION.replace('+', "_");
    Some((format!("https://github.com/adoptium/temurin25-binaries/releases/download/jdk-{tag}/OpenJDK25U-jre_{name}_linux_hotspot_{file}.tar.gz"), sha))
}

/// Where the JRE is unpacked: the archive's own top folder under /opt/mightling.
pub fn java_home() -> PathBuf {
    PathBuf::from(OPT_DIR).join(format!("jdk-{JAVA_VERSION}-jre"))
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

/// The environment signal-cli runs with: the pinned Java 25, and on arm64 the library path of the
/// JNI build.
pub fn signal_cli_env(arch: &str) -> Vec<(String, String)> {
    let mut env = vec![("JAVA_HOME".to_string(), java_home().to_string_lossy().into_owned())];
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

/// Downloading, checking and unpacking the pinned runtime: the JRE (unless `java_present`), signal-cli
/// (unless `signal_cli_present`) and on arm64 libsignal's JNI library. Every archive is checked
/// against its pin as the user before anything is unpacked as root; signal-cli is also checked
/// against its maintainer's OpenPGP signature by `bridge` (`verify-signal-cli`, with the key and its
/// fingerprint compiled in), and both checks must pass.
fn runtime_steps(bridge: &Path, work: &Path, arch: &str, java_present: bool, signal_cli_present: bool) -> Vec<Step> {
    let mut steps = Vec::new();
    if !java_present || !signal_cli_present {
        steps.push(Step::root("Make /opt/mightling for the pinned runtime", &["install", "-d", "-m", "0755", OPT_DIR]));
    }
    if !java_present && let Some((url, sha)) = jre_archive(arch) {
        let jre = work.join("jre.tar.gz").to_string_lossy().into_owned();
        steps.push(Step::user(&format!("Download the Java runtime signal-cli needs (Temurin JRE {JAVA_VERSION})"), &["curl", "-fsSL", "-o", &jre, &url]));
        steps.push(Step::user("Check it against the pinned SHA-256", &["sh", "-c", &format!("echo '{sha}  {jre}' | sha256sum -c -")]));
        steps.push(Step::root("Unpack it under /opt/mightling", &["tar", "-xzf", &jre, "-C", OPT_DIR, "--no-same-owner"]));
    }
    if signal_cli_present {
        return steps;
    }
    let tarball = work.join(format!("signal-cli-{SIGNAL_CLI_VERSION}.tar.gz"));
    let tarball = tarball.to_string_lossy().into_owned();
    let signature = format!("{tarball}.asc");
    steps.push(Step::user("Download signal-cli", &["curl", "-fsSL", "-o", &tarball, &signal_cli_url()]));
    steps.push(Step::user("Check it against the pinned SHA-256", &["sh", "-c", &format!("echo '{SIGNAL_CLI_SHA256}  {tarball}' | sha256sum -c -")]));
    steps.push(Step::user("Download its OpenPGP signature", &["curl", "-fsSL", "-o", &signature, &signal_cli_signature_url()]));
    steps.push(Step::user(
        &format!("Check the signature with the maintainer's pinned key ({})", crate::release_signature::SIGNAL_CLI_KEY_FINGERPRINT),
        &[&bridge.to_string_lossy(), "verify-signal-cli", "--file", &tarball, "--signature", &signature],
    ));
    steps.push(Step::root("Unpack it under /opt/mightling", &["tar", "-xzf", &tarball, "-C", OPT_DIR, "--no-same-owner"]));
    if arch == "aarch64" {
        let lib = work.join("libsignal_jni.tar.gz").to_string_lossy().into_owned();
        let native = native_dir().to_string_lossy().into_owned();
        steps.push(Step::user("Download libsignal's arm64 JNI library (signal-cli ships it for x86_64 only)", &["curl", "-fsSL", "-o", &lib, &libsignal_url()]));
        steps.push(Step::user("Check it against the pinned SHA-256", &["sh", "-c", &format!("echo '{LIBSIGNAL_AARCH64_SHA256}  {lib}' | sha256sum -c -")]));
        steps.push(Step::user("", &["tar", "-xzf", &lib, "-C", &work.to_string_lossy()]));
        steps.push(Step::root("Put it beside signal-cli", &["install", "-D", "-m", "0644", &work.join("libsignal_jni.so").to_string_lossy(), &format!("{native}/libsignal_jni.so")]));
    }
    steps
}

/// The scratch folder for the downloads, under `parent` (the temporary directory), named so no one
/// can make it ahead: root unpacks from it what was checked as the user, so another local user who
/// owned it could swap an archive between the check and the unpacking.
pub fn work_dir(parent: &Path, prefix: &str) -> PathBuf {
    use rand::Rng;
    parent.join(format!("{prefix}-{}-{:016x}", std::process::id(), rand::rng().random::<u64>()))
}

/// Makes `work` the user's own (0700), refusing one that already exists, a link included.
pub fn make_work_dir(work: &Path) -> std::io::Result<()> {
    let mut builder = std::fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(work)
}

/// The bridge binary already installed in /usr/local/lib/mightling, against the one setup would
/// install (§18.8).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InstalledBridge {
    /// None there (or unreadable): installed.
    Missing,
    /// The same file, by SHA-256: left alone, so a second setup does not rewrite the binary under
    /// the bridges already running.
    Same,
    /// Another build: installed, and the plan says that it replaces the binary under them.
    Different,
}

/// A file's SHA-256, in hex; None when it cannot be read.
pub fn sha256_of(path: &Path) -> Option<String> {
    use sha2::Digest;
    let mut file = std::fs::File::open(path).ok()?;
    let mut hasher = sha2::Sha256::new();
    std::io::copy(&mut file, &mut hasher).ok()?;
    Some(hasher.finalize().iter().map(|byte| format!("{byte:02x}")).collect())
}

/// Compares `installed` (the bridge's path on the node) with `bridge` (the binary setup installs).
pub fn installed_bridge(installed: &Path, bridge: &Path) -> InstalledBridge {
    match (sha256_of(installed), sha256_of(bridge)) {
        (None, _) => InstalledBridge::Missing,
        (Some(have), Some(want)) if have == want => InstalledBridge::Same,
        _ => InstalledBridge::Different,
    }
}

/// Everything up to registering the number, for one bridge instance (§18). `bridge` is the
/// `ling-signal` binary being installed, `work` a scratch folder of the user's for downloads, `arch`
/// `std::env::consts::ARCH`. The shared runtime (Java, signal-cli, libsignal) is fetched only where
/// it is missing, so a second instance downloads nothing. The bridge binary is installed unless
/// the one installed is the same file (`installed`, by SHA-256): a further instance needs the
/// binary that knows instances (an older one would pair as the first instance's device), but
/// rewriting an identical one would only touch the file the bridges already here (`others`) run
/// from. When it differs and bridges are here, the step says that it replaces their binary. The
/// account, the state folder and the unit are the instance's own; a further instance's unit is the
/// shared template, written again with the same text.
#[allow(clippy::too_many_arguments)]
pub fn install_plan(
    instance: &Instance,
    bridge: &Path,
    work: &Path,
    arch: &str,
    account_exists: bool,
    java_present: bool,
    signal_cli_present: bool,
    need_qrencode: bool,
    installed: InstalledBridge,
    others: &[Instance],
) -> Vec<Step> {
    let account = instance.account();
    let state_dir = instance.state_dir();
    let mut steps = Vec::new();
    if !account_exists {
        steps.push(Step::root(
            "Create the system account the bridge runs as (no login, no home folder)",
            &["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", &account],
        ));
    }
    if need_qrencode {
        steps.push(Step::root("Install qrencode, to show the linking QR code in this terminal", &["apt-get", "install", "-y", "qrencode"]));
    }
    steps.extend(runtime_steps(bridge, work, arch, java_present, signal_cli_present));
    let install_bridge = ["install", "-D", "-m", "0755", &bridge.to_string_lossy(), unit::BRIDGE_PATH];
    match installed {
        InstalledBridge::Same => {}
        InstalledBridge::Different if !others.is_empty() => {
            let names: Vec<String> = others.iter().map(|other| format!("`{other}`")).collect();
            let (them, runs) = if others.len() > 1 { ("bridges", "run") } else { ("bridge", "runs") };
            let what = format!(
                "Replace the bridge binary the {them} already here ({}) {runs} from: the installed one is another build (its SHA-256 differs). A running bridge keeps the old build until it restarts, then runs this one",
                names.join(", "),
            );
            steps.push(Step::root(&what, &install_bridge));
        }
        InstalledBridge::Different | InstalledBridge::Missing => steps.push(Step::root("Install the bridge where the system account can run it", &install_bridge)),
    }
    steps.push(Step::root("Create its private state folder", &["install", "-d", "-m", "0700", "-o", &account, "-g", &account, &state_dir]));
    let what = if instance.is_first() { "Write the system unit" } else { "Write the system unit every further bridge runs from" };
    let mut write_unit = Step::root(what, &["tee", &instance.unit_file()]);
    write_unit.stdin = Some(instance.unit_file_text());
    steps.push(write_unit);
    steps.push(Step::root("", &["systemctl", "daemon-reload"]));
    steps
}

/// `setup --refresh` (§3, §18.3): after `ling update`, the bridge's copy in /usr/local/lib/mightling
/// is the old one, and a new Mightling may pin a newer signal-cli or Java. This fetches whatever
/// pinned runtime is not there yet (checked as in setup) and installs the bridge binary, once for
/// the node; then, for every instance, points its settings at the runtime (`retool`, as that
/// instance's account), rewrites the unit files and restarts each instance that was running. The
/// accounts, their keys and the pairings are left alone.
/// The step that installs the offline text reader `ling web` reads screenshots with (its
/// `ocr.rs`; specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §19.1). The distribution's package, as for
/// qrencode: signed by the archive, and updated with the system. Its English data comes with it;
/// another language is `apt install tesseract-ocr-<code>`, read from then on.
pub fn ocr_step() -> Step {
    Step::root("Install Tesseract, which reads the text in the images you send (offline, on this machine)", &["apt-get", "install", "-y", "tesseract-ocr"])
}

/// Whether `tesseract` is on PATH.
pub fn tesseract_present() -> bool {
    std::process::Command::new("sh")
        .args(["-c", "command -v tesseract"])
        .stdout(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

pub fn refresh_plan(bridge: &Path, work: &Path, arch: &str, java_present: bool, signal_cli_present: bool, instances: &[Instance]) -> Vec<Step> {
    let mut steps = runtime_steps(bridge, work, arch, java_present, signal_cli_present);
    steps.push(Step::root("Install the current bridge where the system account can run it", &["install", "-D", "-m", "0755", &bridge.to_string_lossy(), unit::BRIDGE_PATH]));
    for instance in instances {
        let mut retool = vec![
            unit::BRIDGE_PATH.to_string(),
            "retool".to_string(),
            "--state".to_string(),
            instance.state_dir(),
            "--signal-cli".to_string(),
            signal_cli_program().to_string_lossy().into_owned(),
            "--version".to_string(),
            SIGNAL_CLI_VERSION.to_string(),
        ];
        for (key, value) in signal_cli_env(arch) {
            retool.push("--env".to_string());
            retool.push(format!("{key}={value}"));
        }
        let what = if instances.len() > 1 { format!("Point bridge `{instance}` at the pinned signal-cli and Java") } else { "Point the bridge at the pinned signal-cli and Java".to_string() };
        steps.push(Step { what, command: as_bridge(instance, &retool), root: false, stdin: None });
    }
    let mut unit_files: Vec<(String, String)> = instances.iter().map(|i| (i.unit_file(), i.unit_file_text())).collect();
    unit_files.dedup();
    for (file, text) in unit_files {
        let mut write_unit = Step::root("Write the system unit again", &["tee", &file]);
        write_unit.stdin = Some(text);
        steps.push(write_unit);
    }
    steps.push(Step::root("", &["systemctl", "daemon-reload"]));
    let mut restart = vec!["systemctl".to_string(), "try-restart".to_string()];
    restart.extend(instances.iter().map(Instance::unit_name));
    let what = if instances.len() > 1 { "Restart each bridge that runs" } else { "Restart the bridge if it runs" };
    steps.push(Step { what: what.to_string(), command: restart, root: true, stdin: None });
    steps
}

/// `remove` (§6, §18.3) of one instance: stop and disable its unit, take its number off Signal,
/// end its `ling web` pairing, delete its data, its account, and its unit file. `others` are the
/// instances that stay: the template unit is deleted only with the last further instance, and the
/// shared runtime and the bridge binary only with the last instance of all. The Ask threads stay.
/// `unregister` needs the network and a working account, so a failure there is reported and the
/// rest still runs.
///
/// Linked to the owner's account (`linked`), `unregister` is never run: on that account it would
/// act on the owner's own registration. The device is unlinked from the phone instead (Settings →
/// Linked devices), which `ling signal remove` asks the owner to do; deleting the state deletes the
/// keys either way.
pub fn remove_plan(instance: &Instance, arch: &str, account: Option<&str>, linked: bool, others: &[Instance]) -> Vec<Step> {
    let mut steps = vec![Step::root("Stop and disable the bridge", &["systemctl", "disable", "--now", &instance.unit_name()])];
    if let Some(account) = account.filter(|_| !linked) {
        let command = signal_cli_command(instance, arch, account, &["unregister"]);
        // `signal_cli_command` already starts with sudo -u; run it as given.
        steps.push(Step { what: format!("Take {account} off Signal (unregister)"), command, root: false, stdin: None });
    }
    steps.push(Step::user("End the bridge's pairing with `ling web`", &["ling", "web", "revoke", &instance.device_name()]));
    steps.push(Step::root("Delete the bridge's data: keys, state, the device cookie", &["rm", "-rf", "--one-file-system", &instance.state_dir()]));
    if instance.is_first() || others.iter().all(Instance::is_first) {
        let what = if instance.is_first() { "Delete the unit" } else { "Delete the unit further bridges run from (this was the last)" };
        steps.push(Step::root(what, &["rm", "-f", &instance.unit_file()]));
    }
    steps.push(Step::root("", &["systemctl", "daemon-reload"]));
    if others.is_empty() {
        steps.push(Step::root(
            "Delete signal-cli, its Java runtime and the bridge binary",
            &["rm", "-rf", "--one-file-system", &signal_cli_home().to_string_lossy(), &java_home().to_string_lossy(), unit::BRIDGE_PATH],
        ));
    }
    steps.push(Step::root("Delete the system account", &["userdel", &instance.account()]));
    steps
}

/// signal-cli on the bridge's data with no account named: `link`, `listAccounts`.
pub fn signal_cli_bare(instance: &Instance, arch: &str, args: &[&str]) -> Vec<String> {
    let mut command = vec!["env".to_string()];
    for (key, value) in signal_cli_env(arch) {
        command.push(format!("{key}={value}"));
    }
    command.push(signal_cli_program().to_string_lossy().into_owned());
    command.push("--config".to_string());
    command.push(format!("{}/signal-cli", instance.state_dir()));
    command.extend(args.iter().map(|s| s.to_string()));
    as_bridge(instance, &command)
}

/// The account `link` was associated with: its `Associated with: +…` line.
pub fn associated_number(line: &str) -> Option<String> {
    let number = line.trim().strip_prefix("Associated with:")?.trim();
    (!number.is_empty()).then(|| number.to_string())
}

/// The account's id in `listAccounts -o json` (`[{"number": …, "aci": …}]`). The output given is
/// stdout and stderr together, and signal-cli logs to stderr (after a fresh link always
/// `INFO  AccountHelper - The Signal protocol expects that incoming messages are regularly
/// received.`), so the JSON is the line that parses, not the whole text.
pub fn account_aci(output: &str, number: &str) -> Option<String> {
    output.lines().filter_map(|line| serde_json::from_str::<serde_json::Value>(line.trim()).ok()).find_map(|value| {
        value.as_array()?.iter().find(|a| a.get("number").and_then(|n| n.as_str()) == Some(number)).and_then(|a| a.get("aci")?.as_str().map(str::to_string))
    })
}

/// This device's id in `listDevices`' text (`- Device 3 (this device):`); its JSON form leaves
/// that mark out.
pub fn this_device(text: &str) -> Option<u64> {
    text.lines().find_map(|line| {
        let rest = line.trim().strip_prefix("- Device ")?;
        let (id, tail) = rest.split_once(' ')?;
        tail.starts_with("(this device)").then(|| id.parse().ok()).flatten()
    })
}

/// A command run as the instance's account, e.g. signal-cli on its data.
pub fn as_bridge(instance: &Instance, command: &[String]) -> Vec<String> {
    let mut full = vec!["sudo".to_string(), "-u".to_string(), instance.account()];
    full.extend(command.iter().cloned());
    full
}

/// signal-cli on the bridge's data, with its environment, as the bridge's account.
pub fn signal_cli_command(instance: &Instance, arch: &str, account: &str, args: &[&str]) -> Vec<String> {
    let mut command = vec!["env".to_string()];
    for (key, value) in signal_cli_env(arch) {
        command.push(format!("{key}={value}"));
    }
    command.push(signal_cli_program().to_string_lossy().into_owned());
    command.push("--config".to_string());
    command.push(format!("{}/signal-cli", instance.state_dir()));
    command.push("-a".to_string());
    command.push(account.to_string());
    command.extend(args.iter().map(|s| s.to_string()));
    as_bridge(instance, &command)
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

/// What `ling-signal account --state DIR` prints about an instance: its number, its mode, the
/// Linux user it serves and its account id (§18.4). A bridge binary from before §18 prints the
/// first two words only, so the last two are optional.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BridgeAccount {
    pub number: String,
    pub linked: bool,
    pub user: Option<String>,
    pub aci: Option<String>,
}

/// The line `account` prints for a `bridge.json`: `<number> <linked|dedicated> <user|-> <aci|->`.
pub fn bridge_account_line(account: &BridgeAccount) -> String {
    format!(
        "{} {} {} {}",
        account.number,
        if account.linked { "linked" } else { "dedicated" },
        account.user.as_deref().unwrap_or("-"),
        account.aci.as_deref().unwrap_or("-")
    )
}

/// Reads that line back; None when it names no valid number.
pub fn parse_bridge_account(output: &str) -> Option<BridgeAccount> {
    let mut words = output.split_whitespace();
    let number = words.next()?.to_string();
    if !valid_number(&number) {
        return None;
    }
    let linked = words.next() == Some("linked");
    let known = |word: Option<&str>| word.filter(|w| *w != "-").map(str::to_string);
    let user = known(words.next());
    let aci = known(words.next());
    Some(BridgeAccount { number, linked, user, aci })
}

/// The instance among `others` that already holds the Signal account `number` (or, when known,
/// `aci`), if any (§18.1, decision 6): two bridges on one account would both answer every note.
pub fn held_by<'a>(number: &str, aci: Option<&str>, others: &'a [(Instance, BridgeAccount)]) -> Option<&'a Instance> {
    others
        .iter()
        .find(|(_, held)| held.number == number || (aci.is_some() && held.aci.as_deref() == aci))
        .map(|(instance, _)| instance)
}

/// Whether `current` may set up again, trust or remove an instance that serves `served` (§18.3):
/// those pair or unpair a `ling web` device, so only the user it serves may. An instance set up
/// before §18 records no user, and anyone with sudo may act on it, as before.
pub fn may_act(instance: &Instance, served: Option<&str>, current: &str) -> Result<(), String> {
    match served {
        Some(user) if user != current => Err(format!(
            "Bridge `{instance}` answers with {user}'s agent, so only {user} can change it; `ling signal stop --name {instance}` stops it."
        )),
        _ => Ok(()),
    }
}

/// Several people on one node, each with bridges answering with their own agent, is §18.1's
/// decision 3 second case, which §18.7 item 1 leaves for the owner to confirm. Until then the
/// conservative reading is built: every bridge on a node serves one Linux user. Returns the bridge
/// among `others` that serves somebody else than `user`, if any; one from before §18 records nobody
/// and never counts.
pub fn serves_someone_else<'a>(user: &str, others: &'a [(Instance, BridgeAccount)]) -> Option<(&'a Instance, &'a str)> {
    others.iter().find_map(|(instance, held)| held.user.as_deref().filter(|served| *served != user).map(|served| (instance, served)))
}

/// The port in `ling web status`' answer (`… running on port 3100 (loopback only) …`).
pub fn web_port_in(output: &str) -> Option<u16> {
    let rest = output.split_once("running on port ")?.1;
    rest.split(|c: char| !c.is_ascii_digit()).next()?.parse().ok()
}

/// `MemAvailable` in /proc/meminfo, in bytes.
pub fn mem_available(meminfo: &str) -> Option<u64> {
    let line = meminfo.lines().find(|line| line.starts_with("MemAvailable:"))?;
    let kib: u64 = line.split_whitespace().nth(1)?.parse().ok()?;
    Some(kib * 1024)
}

/// The memory rule of §18.1, decision 7: a further JVM fits when `MemAvailable` is at least
/// `HEADROOM_BYTES` above the caps of the instances already there. Applies to the first instance
/// too (its rule is then `HEADROOM_BYTES` alone): on unified memory the model's headroom is the host's.
pub fn memory_allows(available: u64, existing: usize) -> bool {
    available >= unit::HEADROOM_BYTES + unit::MEMORY_MAX_BYTES * existing as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn first() -> Instance {
        Instance::first()
    }

    fn named(name: &str) -> Instance {
        Instance::parse(name).unwrap()
    }

    #[test]
    fn the_plan_on_arm64_brings_the_jni_library_and_prints_every_root_step() {
        let plan = install_plan(&first(), Path::new("/home/u/.local/share/dreamference/mightling/bin/ling-signal"), Path::new("/tmp/w"), "aarch64", false, false, false, true, InstalledBridge::Missing, &[]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(shown[0].starts_with("sudo useradd --system --no-create-home"), "{shown:?}");
        // Java is fetched pinned, never from the package manager.
        assert!(!shown.iter().any(|s| s.contains("openjdk")));
        assert!(shown.iter().any(|s| s.starts_with("curl") && s.contains("OpenJDK25U-jre_aarch64_linux_hotspot_25.0.4.1_1.tar.gz") && s.contains("jdk-25.0.4.1%2B1")));
        assert!(shown.iter().any(|s| s.contains(JRE_AARCH64_SHA256) && s.contains("sha256sum -c")));
        assert!(shown.iter().any(|s| s == "sudo apt-get install -y qrencode"));
        assert!(shown.iter().any(|s| s.contains(SIGNAL_CLI_SHA256) && s.contains("sha256sum -c")));
        assert!(shown.iter().any(|s| s.contains(LIBSIGNAL_AARCH64_SHA256)));
        assert!(shown.iter().any(|s| s.ends_with("/opt/mightling/signal-cli-0.14.9/lib/native/libsignal_jni.so")));
        assert!(shown.iter().any(|s| s == "sudo install -d -m 0700 -o mightling-signal -g mightling-signal /var/lib/mightling-signal"));
        let unit = plan.iter().find(|s| s.stdin.is_some()).unwrap();
        assert!(unit.root && unit.stdin.as_ref().unwrap().contains("ProtectHome=yes"));
        // Every download is checked before anything is unpacked as root: signal-cli both by its
        // SHA-256 and by its maintainer's signature, checked as the user by the bridge being installed.
        let download = shown.iter().position(|s| s.starts_with("curl") && s.ends_with("/signal-cli-0.14.9.tar.gz")).unwrap();
        let download_asc = shown
            .iter()
            .position(|s| s.starts_with("curl -fsSL -o /tmp/w/signal-cli-0.14.9.tar.gz.asc ") && s.ends_with("/v0.14.9/signal-cli-0.14.9.tar.gz.asc"))
            .unwrap();
        let check = shown.iter().position(|s| s.contains(SIGNAL_CLI_SHA256)).unwrap();
        let verify = "/home/u/.local/share/dreamference/mightling/bin/ling-signal verify-signal-cli --file /tmp/w/signal-cli-0.14.9.tar.gz --signature /tmp/w/signal-cli-0.14.9.tar.gz.asc";
        let signature = shown.iter().position(|s| s == verify).unwrap();
        assert!(!plan[signature].root && plan[signature].what.contains(crate::release_signature::SIGNAL_CLI_KEY_FINGERPRINT));
        let unpack = shown.iter().position(|s| s.starts_with("sudo tar") && s.contains("signal-cli-0.14.9")).unwrap();
        assert!(download < check && download_asc < signature && check < unpack && signature < unpack);
        let jre_check = shown.iter().position(|s| s.contains(JRE_AARCH64_SHA256)).unwrap();
        let jre_unpack = shown.iter().position(|s| s.starts_with("sudo tar") && s.contains("jre.tar.gz")).unwrap();
        assert!(jre_check < jre_unpack);
        // Nothing is opened to everyone.
        assert!(!shown.iter().any(|s| s.contains("777") || s.contains("chmod")));
    }

    #[test]
    fn the_text_reader_is_the_distributions_package() {
        let step = ocr_step();
        assert!(step.root);
        assert_eq!(step.display(), "sudo apt-get install -y tesseract-ocr");
    }

    #[test]
    fn an_existing_account_and_java_are_not_redone_and_x86_needs_no_library() {
        let plan = install_plan(&first(), Path::new("/b"), Path::new("/w"), "x86_64", true, true, false, false, InstalledBridge::Missing, &[]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(!shown.iter().any(|s| s.contains("useradd") || s.contains("apt-get") || s.contains("libsignal") || s.contains("OpenJDK25U")));
        // x86_64 gets its own pinned JRE when it has none.
        let plan = install_plan(&first(), Path::new("/b"), Path::new("/w"), "x86_64", true, false, false, false, InstalledBridge::Missing, &[]);
        assert!(plan.iter().any(|s| s.display().contains(JRE_X86_64_SHA256)));
        assert!(plan.iter().any(|s| s.display().contains("OpenJDK25U-jre_x64_linux_hotspot_25.0.4.1_1.tar.gz")));
        assert!(jre_archive("riscv64").is_none());
    }

    #[test]
    fn refresh_reinstalls_the_bridge_and_fetches_only_what_is_missing() {
        let plan = refresh_plan(Path::new("/home/u/.local/share/dreamference/mightling/bin/ling-signal"), Path::new("/w"), "aarch64", true, true, &[first()]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(!shown.iter().any(|s| s.starts_with("curl") || s.contains("useradd") || s.contains("rm ")), "{shown:?}");
        assert!(shown.iter().any(|s| s == "sudo install -D -m 0755 /home/u/.local/share/dreamference/mightling/bin/ling-signal /usr/local/lib/mightling/ling-signal"));
        let retool = shown.iter().find(|s| s.contains(" retool ")).unwrap();
        assert!(retool.starts_with("sudo -u mightling-signal /usr/local/lib/mightling/ling-signal retool --state /var/lib/mightling-signal"), "{retool}");
        assert!(retool.contains("--env JAVA_HOME=/opt/mightling/jdk-25.0.4.1+1-jre"), "{retool}");
        assert_eq!(shown.last().unwrap(), "sudo systemctl try-restart mightling-signal.service");
        // A new pin is fetched and checked before it is unpacked.
        let plan = refresh_plan(Path::new("/b"), Path::new("/w"), "aarch64", false, false, &[first()]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(shown.iter().any(|s| s.contains(JRE_AARCH64_SHA256)) && shown.iter().any(|s| s.contains(SIGNAL_CLI_SHA256)));
        let signature = shown.iter().position(|s| s.starts_with("/b verify-signal-cli ")).unwrap();
        let unpack = shown.iter().position(|s| s.starts_with("sudo tar") && s.contains("signal-cli-0.14.9")).unwrap();
        assert!(signature < unpack);
    }

    #[test]
    fn signal_cli_runs_as_the_bridges_account_with_java_25() {
        let command = signal_cli_command(&first(), "aarch64", "+15550000", &["register", "--voice"]);
        let text = command.join(" ");
        assert!(text.starts_with("sudo -u mightling-signal env JAVA_HOME=/opt/mightling/jdk-25.0.4.1+1-jre JAVA_OPTS=-Djava.library.path=/opt/mightling/signal-cli-0.14.9/lib/native "));
        assert!(text.ends_with("--config /var/lib/mightling-signal/signal-cli -a +15550000 register --voice"));
    }

    #[test]
    fn remove_deletes_only_what_setup_made() {
        let plan = remove_plan(&first(), "aarch64", Some("+15550000"), false, &[]);
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
        assert!(!remove_plan(&first(), "aarch64", None, false, &[]).iter().any(|s| s.display().contains("unregister")));
    }

    #[test]
    fn removing_a_linked_bridge_never_unregisters_the_owners_account() {
        let plan = remove_plan(&first(), "aarch64", Some("+15550000"), true, &[]);
        assert!(!plan.iter().any(|s| s.display().contains("unregister")), "unregister would act on the owner's own account");
        assert!(plan.iter().any(|s| s.display().contains("rm -rf --one-file-system /var/lib/mightling-signal")), "the keys are deleted");
    }

    #[test]
    fn what_linking_prints_is_read() {
        assert_eq!(associated_number("Associated with: +15550000\n").as_deref(), Some("+15550000"));
        assert_eq!(associated_number("sgnl://linkdevice?uuid=x"), None);
        assert_eq!(account_aci(r#"[{"number":"+15550000","aci":"aci-1"},{"number":"+1999","aci":"x"}]"#, "+15550000").as_deref(), Some("aci-1"));
        assert_eq!(account_aci("not json", "+1"), None);
        // What signal-cli 0.14.9 gave right after a real link (2026-10-11): the JSON, then its log line.
        let after_link = "[{\"number\":\"+15550000\",\"aci\":\"aci-1\"}]\nINFO  AccountHelper - The Signal protocol expects that incoming messages are regularly received.\n";
        assert_eq!(account_aci(after_link, "+15550000").as_deref(), Some("aci-1"));
        let devices = "- Device 1:\n  Name: Pixel\n- Device 4 (this device):\n  Name: Mightling (gx10)\n";
        assert_eq!(this_device(devices), Some(4));
        assert_eq!(this_device("- Device 1:\n"), None);
        let link = signal_cli_bare(&first(), "aarch64", &["link", "-n", "Mightling (gx10)"]).join(" ");
        assert!(link.starts_with("sudo -u mightling-signal env ") && link.ends_with("--config /var/lib/mightling-signal/signal-cli link -n Mightling (gx10)"));
        assert!(!link.contains(" -a "));
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
    #[test]
    fn a_second_instance_downloads_nothing_and_makes_only_its_own_things() {
        let work = named("work");
        let plan = install_plan(&work, Path::new("/b"), Path::new("/w"), "aarch64", false, true, true, false, InstalledBridge::Missing, &[]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(!shown.iter().any(|s| s.starts_with("curl") || s.contains("sudo tar") || s.contains("/opt/mightling")), "{shown:?}");
        assert!(shown.iter().any(|s| s == "sudo useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin mightling-signal-work"));
        assert!(shown.iter().any(|s| s == "sudo install -d -m 0700 -o mightling-signal-work -g mightling-signal-work /var/lib/mightling-signal-work"));
        assert!(shown.iter().any(|s| s == "sudo tee /etc/systemd/system/mightling-signal@.service"));
        let unit = plan.iter().find(|s| s.stdin.is_some()).unwrap();
        assert!(unit.stdin.as_ref().unwrap().contains("User=mightling-signal-%i"));
        // Nothing of the first instance is touched.
        for line in &shown {
            assert!(!line.contains("/var/lib/mightling-signal ") && !line.ends_with("/var/lib/mightling-signal"), "{line}");
            assert!(!line.contains("mightling-signal.service"), "{line}");
            assert!(!line.ends_with(" mightling-signal"), "{line}");
        }
        // signal-cli runs as the instance's account on the instance's data.
        let link = signal_cli_bare(&work, "aarch64", &["link", "-n", "Mightling (gx10, work)"]).join(" ");
        assert!(link.starts_with("sudo -u mightling-signal-work env ") && link.contains("--config /var/lib/mightling-signal-work/signal-cli link"), "{link}");
    }

    #[test]
    fn the_bridge_binary_is_compared_by_hash() {
        let dir = std::env::temp_dir().join(format!("ling-signal-hash-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (new, same, other) = (dir.join("new"), dir.join("same"), dir.join("other"));
        std::fs::write(&new, b"ling-signal build 2").unwrap();
        std::fs::write(&same, b"ling-signal build 2").unwrap();
        std::fs::write(&other, b"ling-signal build 1").unwrap();
        let found = (installed_bridge(&dir.join("absent"), &new), installed_bridge(&same, &new), installed_bridge(&other, &new));
        let digest = sha256_of(&new);
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(found, (InstalledBridge::Missing, InstalledBridge::Same, InstalledBridge::Different));
        // The SHA-256 of the bytes, in hex (as `sha256sum` prints it).
        assert_eq!(digest.as_deref(), Some("c6e7b4f83cfe2169910422f47b941d2386297109ffad0f18014455cfbfed3b5f"));
    }

    #[test]
    fn the_scratch_folder_is_unguessable_private_and_never_one_made_before() {
        let parent = std::env::temp_dir().join(format!("ling-signal-work-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&parent);
        std::fs::create_dir_all(&parent).unwrap();
        let (first, second) = (work_dir(&parent, "ling-signal-setup"), work_dir(&parent, "ling-signal-setup"));
        assert_ne!(first, second, "a name another user can predict lets them make the folder first");
        assert_eq!(first.parent(), Some(parent.as_path()));
        make_work_dir(&first).unwrap();
        #[cfg(unix)]
        let mode = {
            use std::os::unix::fs::PermissionsExt;
            std::fs::metadata(&first).unwrap().permissions().mode() & 0o777
        };
        // A folder someone made first (here: the one just made) is refused, and so is a link.
        let again = make_work_dir(&first);
        #[cfg(unix)]
        let linked = {
            std::os::unix::fs::symlink(&first, &second).unwrap();
            make_work_dir(&second)
        };
        let _ = std::fs::remove_dir_all(&parent);
        assert!(again.is_err());
        #[cfg(unix)]
        {
            assert_eq!(mode, 0o700);
            assert!(linked.is_err());
        }
    }

    #[test]
    fn a_second_setup_leaves_an_identical_bridge_binary_alone_and_names_what_a_different_one_replaces() {
        let work = named("work");
        let install = |plan: &[Step]| plan.iter().filter(|s| s.command.last().map(String::as_str) == Some(unit::BRIDGE_PATH)).cloned().collect::<Vec<Step>>();
        // The same build: not installed again under the running `default`.
        let plan = install_plan(&work, Path::new("/b"), Path::new("/w"), "aarch64", false, true, true, false, InstalledBridge::Same, &[first()]);
        assert!(install(&plan).is_empty(), "{plan:?}");
        assert!(!plan.iter().any(|s| s.display().contains(unit::BRIDGE_PATH)));
        // Another build: installed, and the plan says it replaces the binary under `default`.
        let plan = install_plan(&work, Path::new("/b"), Path::new("/w"), "aarch64", false, true, true, false, InstalledBridge::Different, &[first()]);
        let step = install(&plan);
        assert_eq!(step.len(), 1);
        assert_eq!(step[0].display(), "sudo install -D -m 0755 /b /usr/local/lib/mightling/ling-signal");
        assert!(step[0].what.starts_with("Replace the bridge binary the bridge already here (`default`) runs from") && step[0].what.contains("SHA-256"), "{}", step[0].what);
        let plan = install_plan(&named("home"), Path::new("/b"), Path::new("/w"), "aarch64", false, true, true, false, InstalledBridge::Different, &[first(), work.clone()]);
        assert!(install(&plan)[0].what.contains("the bridges already here (`default`, `work`) run from"), "{:?}", install(&plan));
        // None installed, or a different one with no bridge using it: a plain install.
        for (installed, others) in [(InstalledBridge::Missing, vec![]), (InstalledBridge::Missing, vec![first()]), (InstalledBridge::Different, vec![])] {
            let plan = install_plan(&first(), Path::new("/b"), Path::new("/w"), "aarch64", true, true, true, false, installed, &others);
            let step = install(&plan);
            assert_eq!((step.len(), step[0].what.as_str()), (1, "Install the bridge where the system account can run it"), "{installed:?}");
        }
    }

    #[test]
    fn removing_one_of_two_keeps_the_shared_runtime_and_the_last_one_deletes_it() {
        let work = named("work");
        let plan = remove_plan(&work, "aarch64", Some("+15550001"), true, &[first()]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert_eq!(shown[0], "sudo systemctl disable --now mightling-signal@work.service");
        assert!(shown.contains(&"ling web revoke signal-work".to_string()), "{shown:?}");
        assert!(!shown.iter().any(|s| s.contains("signal-bridge")), "the first instance's device stays paired");
        assert!(!shown.iter().any(|s| s.contains("/opt/mightling") || s.contains(unit::BRIDGE_PATH)), "{shown:?}");
        assert!(shown.iter().any(|s| s == "sudo rm -rf --one-file-system /var/lib/mightling-signal-work"));
        // With only the first instance left, the template goes too.
        assert!(shown.iter().any(|s| s == "sudo rm -f /etc/systemd/system/mightling-signal@.service"));
        assert_eq!(shown.last().unwrap(), "sudo userdel mightling-signal-work");
        // Another further instance stays: the template stays.
        let plan = remove_plan(&work, "aarch64", None, true, &[first(), named("home")]);
        assert!(!plan.iter().any(|s| s.display().contains("mightling-signal@.service")));
        // The first instance removed beside a further one: its own unit only, the runtime stays.
        let plan = remove_plan(&first(), "aarch64", None, true, &[work.clone()]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(shown.iter().any(|s| s == "sudo rm -f /etc/systemd/system/mightling-signal.service"));
        assert!(!shown.iter().any(|s| s.contains("mightling-signal@.service") || s.contains("/opt/mightling")), "{shown:?}");
        assert!(shown.contains(&"ling web revoke signal-bridge".to_string()));
        // The last instance of all deletes the runtime and the bridge binary.
        let plan = remove_plan(&work, "aarch64", None, true, &[]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert!(shown.iter().any(|s| s.contains("/opt/mightling/signal-cli-0.14.9") && s.contains(unit::BRIDGE_PATH)));
        assert!(shown.iter().any(|s| s == "sudo rm -f /etc/systemd/system/mightling-signal@.service"));
    }

    #[test]
    fn refresh_covers_every_instance_and_fetches_the_runtime_once() {
        let plan = refresh_plan(Path::new("/b"), Path::new("/w"), "aarch64", false, false, &[first(), named("home"), named("work")]);
        let shown: Vec<String> = plan.iter().map(Step::display).collect();
        assert_eq!(shown.iter().filter(|s| s.starts_with("sudo tar") && s.contains("signal-cli-0.14.9")).count(), 1);
        assert_eq!(shown.iter().filter(|s| s.starts_with("sudo install -D -m 0755 /b ")).count(), 1);
        for (account, state) in [("mightling-signal", "/var/lib/mightling-signal"), ("mightling-signal-home", "/var/lib/mightling-signal-home"), ("mightling-signal-work", "/var/lib/mightling-signal-work")] {
            assert!(shown.iter().any(|s| s.starts_with(&format!("sudo -u {account} /usr/local/lib/mightling/ling-signal retool --state {state} "))), "{account}");
        }
        assert_eq!(shown.iter().filter(|s| s.as_str() == "sudo tee /etc/systemd/system/mightling-signal.service").count(), 1);
        assert_eq!(shown.iter().filter(|s| s.as_str() == "sudo tee /etc/systemd/system/mightling-signal@.service").count(), 1);
        assert_eq!(shown.last().unwrap(), "sudo systemctl try-restart mightling-signal.service mightling-signal@home.service mightling-signal@work.service");
    }

    #[test]
    fn the_account_line_and_the_refusals() {
        let held = BridgeAccount { number: "+15550000".to_string(), linked: true, user: Some("owner".to_string()), aci: Some("aci-1".to_string()) };
        let line = bridge_account_line(&held);
        assert_eq!(line, "+15550000 linked owner aci-1");
        assert_eq!(parse_bridge_account(&line), Some(held.clone()));
        // A bridge binary from before §18 prints two words.
        assert_eq!(parse_bridge_account("+15550000 linked\n"), Some(BridgeAccount { number: "+15550000".to_string(), linked: true, user: None, aci: None }));
        assert_eq!(parse_bridge_account("+15550000 dedicated - -").unwrap().user, None);
        assert_eq!(parse_bridge_account("bridge.json: No such file"), None);
        // One account, one instance: by number, or by account id when the new one's is known.
        let others = vec![(first(), held.clone())];
        assert_eq!(held_by("+15550000", None, &others), Some(&first()));
        // The id after a fresh link, read from listAccounts with signal-cli's stderr line beside it.
        let after_link = "[{\"number\":\"+15559999\",\"aci\":\"aci-1\"}]\nINFO  AccountHelper - The Signal protocol expects that incoming messages are regularly received.\n";
        let aci = account_aci(after_link, "+15559999");
        assert_eq!(held_by("+15559999", aci.as_deref(), &others), Some(&first()));
        assert_eq!(held_by("+15551111", Some("aci-2"), &others), None);
        assert_eq!(held_by("+15551111", None, &[(first(), BridgeAccount { aci: None, ..held.clone() })]), None);
        // Only the user an instance serves may change it; one from before §18 records nobody.
        assert!(may_act(&named("work"), Some("owner"), "owner").is_ok());
        let refused = may_act(&named("work"), Some("owner"), "guest").unwrap_err();
        assert!(refused.contains("only owner can change it") && refused.contains("--name work"), "{refused}");
        assert!(may_act(&first(), None, "anyone").is_ok());
        // One Linux user per node until the owner confirms several (§18.7 item 1).
        let mine = vec![(first(), BridgeAccount { user: None, ..held.clone() }), (named("work"), held.clone())];
        assert_eq!(serves_someone_else("owner", &mine), None);
        assert_eq!(serves_someone_else("guest", &mine), Some((&named("work"), "owner")));
        assert_eq!(serves_someone_else("guest", &mine[..1]), None, "a bridge from before §18 records nobody");
    }

    #[test]
    fn the_web_port_and_the_memory_rule() {
        assert_eq!(web_port_in("Mightling web server: running on port 3101 (loopback only), pid 9; 2 paired device(s).\n"), Some(3101));
        assert_eq!(web_port_in("Mightling web server: not running (start it with `ling web start`); 0 paired device(s)."), None);
        let meminfo = "MemTotal:       127535892 kB\nMemFree:        11000000 kB\nMemAvailable:    3000000 kB\n";
        assert_eq!(mem_available(meminfo), Some(3_000_000 * 1024));
        assert_eq!(mem_available("MemTotal: 1 kB\n"), None);
        let gib = 1024 * 1024 * 1024;
        assert!(memory_allows(2 * gib, 0));
        assert!(!memory_allows(2 * gib - 1, 0));
        assert!(!memory_allows(2 * gib + 1, 1), "2 GB above one existing instance's 768 MB cap");
        assert!(memory_allows(2 * gib + unit::MEMORY_MAX_BYTES * 3, 3));
    }
}
