//! `ling signal …` (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §6): the one command surface.
//!
//! The launcher routes `ling signal …` here, and the small `ling-signal` binary (`main.rs`) calls the
//! same function: one set of commands, two ways in.
//!
//! As the user (`ling signal …`); every command takes `--name NAME`, the bridge instance (§18):
//! - `setup [--name N] [--number +… [--voice]] [--port N] [--dry-run]`: install, link to the
//!   owner's account (the default, §4.2) or register a dedicated number (§4.1), pair with the
//!   user's own `ling web`, start. The first instance is `default`; a further one needs `--name`
//! - `setup --refresh [--dry-run]`: after `ling update`, install the new bridge binary and the
//!   pinned runtime, and refresh and restart every instance
//! - `status`: what the running bridge reports (`/run/mightling-signal/status.json`); with
//!   several instances and no `--name`, one line each
//! - `start`, `stop`: the system unit
//! - `trust`: accept the owner's new safety number after comparing it (§5.3)
//! - `remove [--dry-run]`: undo setup; the Ask threads stay
//! - `unit`: print the unit file
//!
//! With more than one instance, `start`, `stop`, `trust` and `remove` need `--name`. `setup`,
//! `trust` and `remove` of an instance are for the Linux user it serves (§18.3).
//! - `verify-signal-cli --file TARBALL --signature ASC`: setup's check of the downloaded signal-cli
//!   against its maintainer's OpenPGP signature (`release_signature.rs`); run by setup as the user,
//!   through the bridge binary it is about to install
//!
//! As the bridge's own account, only through the `ling-signal` binary in /usr/local/lib/mightling
//! (setup runs these through `sudo -u mightling-signal`; `ling signal` refuses them):
//! - `serve --state DIR [--runtime DIR]`: the daemon, what the unit runs
//! - `init --state DIR --account +… --node NAME --port N --signal-cli PATH --version V [--env K=V]… [--vision]
//!   [--instance NAME --user USER]`
//! - `retool --state DIR --signal-cli PATH --version V [--env K=V]…`: `setup --refresh`'s update of
//!   the runtime the bridge starts
//! - `pair --state DIR --port N --code CODE [--device NAME]`: trade a `ling web pair` code for the
//!   device cookie, as `signal-bridge` or the instance's own device name
//! - `account --state DIR`: the number, the mode, the user served and the account id (§18.4)
//! - `bind --state DIR`: a fresh six-digit code that makes its first sender the owner
//! - `trust-owner --state DIR`: §5.3 on the bridge's data, with the unit stopped

use std::io::BufRead;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;

use crate::gate;
use crate::gate::Binding;
use crate::gate::Mode;
use crate::serve;
use crate::setup;
use crate::state;
use crate::state::Config;
use crate::unit;
use crate::unit::Instance;

const USAGE: &str = "Usage: ling signal setup [--name NAME] [--number +NUMBER [--voice]] [--port N] [--dry-run] | setup --refresh [--dry-run] | status [--name NAME] | start [--name NAME] | stop [--name NAME] | trust [--name NAME] | remove [--name NAME] [--dry-run] | unit [--name NAME]";
const BRIDGE_USAGE: &str = "       ling-signal serve --state DIR [--runtime DIR]   (what the system unit runs; nobody types it)";

/// The commands only the bridge's own account runs, through the `ling-signal` binary.
pub const BRIDGE_COMMANDS: &[&str] = &["serve", "init", "retool", "pair", "bind", "trust-owner", "account"];

/// How `run` was reached.
#[derive(Clone, Debug)]
pub struct Invocation {
    /// The `ling-signal` binary setup installs for the system account: this program when it is
    /// `ling-signal`, the one installed beside `ling` when it is `ling signal`. None when it is not
    /// installed; only `setup` needs it.
    pub bridge: Option<PathBuf>,
    /// Whether the bridge's own commands (`serve`, `init`, …) are accepted: only from the
    /// `ling-signal` binary, never from `ling signal`.
    pub bridge_commands: bool,
    /// Where the node's files are read: `/`, or a scratch folder in tests.
    pub host: Host,
}

impl Invocation {
    /// On the node itself.
    pub fn new(bridge: Option<PathBuf>, bridge_commands: bool) -> Invocation {
        Invocation { bridge, bridge_commands, host: Host::system() }
    }
}

/// What the commands read of the node to find its instances (§18): the state folders under
/// /var/lib, the system accounts, the unit files and the public status files, and the memory left.
/// Reads only; everything that changes the node goes through sudo in a printed plan. `root` is `/`
/// on a node, so the tests can give a scratch folder and never see a real bridge.
#[derive(Clone, Debug)]
pub struct Host {
    pub root: PathBuf,
}

impl Host {
    pub fn system() -> Host {
        Host { root: PathBuf::from("/") }
    }

    pub fn path(&self, absolute: &str) -> PathBuf {
        self.root.join(absolute.trim_start_matches('/'))
    }

    /// The instances on this node, by their state folders under /var/lib (names only: the folders
    /// themselves are 0700), `default` first.
    pub fn instances(&self) -> Vec<Instance> {
        let mut found: Vec<Instance> = std::fs::read_dir(self.path("/var/lib"))
            .map(|entries| entries.filter_map(Result::ok).filter_map(|e| Instance::from_state_folder(&e.file_name().to_string_lossy())).collect())
            .unwrap_or_default();
        found.sort_by_key(|i| (!i.is_first(), i.name().to_string()));
        found
    }

    /// Whether a system account exists (`/etc/passwd`, where `useradd --system` writes it).
    pub fn account_exists(&self, account: &str) -> bool {
        std::fs::read_to_string(self.path("/etc/passwd")).is_ok_and(|text| text.lines().any(|line| line.split(':').next() == Some(account)))
    }

    /// Anything of an instance on this node: its account, its state folder, or its unit (the
    /// first one's file, or a further one's enablement link). A half-removed instance counts.
    pub fn trace_of(&self, instance: &Instance) -> Option<String> {
        if self.path(&instance.state_dir()).exists() {
            return Some(instance.state_dir());
        }
        if self.account_exists(&instance.account()) {
            return Some(format!("the account {}", instance.account()));
        }
        let unit = if instance.is_first() {
            instance.unit_file()
        } else {
            format!("{}/multi-user.target.wants/{}", unit::SYSTEMD_DIR, instance.unit_name())
        };
        self.path(&unit).exists().then_some(unit)
    }

    /// `MemAvailable`, in bytes.
    pub fn mem_available(&self) -> Option<u64> {
        setup::mem_available(&std::fs::read_to_string(self.path("/proc/meminfo")).ok()?)
    }

    /// Whether the instance's unit runs (`systemctl is-active`). A scratch root has no units.
    pub fn unit_active(&self, instance: &Instance) -> bool {
        self.root == Path::new("/")
            && Command::new("systemctl").args(["is-active", "--quiet", &instance.unit_name()]).status().map(|s| s.success()).unwrap_or(false)
    }

    /// The instance's public `status.json` (0644, no secret and no text, §7), while it runs.
    pub fn status_of(&self, instance: &Instance) -> Option<serde_json::Value> {
        serde_json::from_str(&std::fs::read_to_string(self.path(&instance.runtime_dir()).join("status.json")).ok()?).ok()
    }
}


fn flag(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
}

fn has(args: &[String], name: &str) -> bool {
    args.iter().any(|a| a == name)
}

/// The names, for a message.
fn names(instances: &[Instance]) -> String {
    instances.iter().map(Instance::name).collect::<Vec<_>>().join(", ")
}

/// The instance `start`, `stop`, `trust`, `remove`, `status` and `unit` act on (§18.3): `--name`,
/// or the only one there is, or `default` on a node with none. With several and no `--name`,
/// refused with the names. Err is the exit code, the message already printed.
fn chosen(args: &[String], host: &Host) -> Result<Instance, i32> {
    if let Some(name) = flag(args, "--name") {
        return Instance::parse(&name).map_err(|err| {
            eprintln!("{err}");
            2
        });
    }
    let instances = host.instances();
    match instances.as_slice() {
        [] => Ok(Instance::first()),
        [only] => Ok(only.clone()),
        several => {
            eprintln!("This node has {} Signal bridges ({}); say which with --name.", several.len(), names(several));
            Err(2)
        }
    }
}

/// The Linux user running this command.
fn current_user() -> String {
    run_command(&["id".to_string(), "-un".to_string()], None).map(|out| out.trim().to_string()).unwrap_or_default()
}

/// Runs one `ling signal …` (or `ling-signal …`) command line, without the program name, and
/// returns the exit code. Every command is synchronous; the ones that need an async runtime build
/// their own, which is why the launcher calls this from a blocking thread.
pub fn run(args: &[String], invocation: &Invocation) -> i32 {
    let command = args.first().map(String::as_str).unwrap_or_default();
    if BRIDGE_COMMANDS.contains(&command) && !invocation.bridge_commands {
        eprintln!("`{command}` is run by the bridge's own account, through {}; `ling signal setup` does that.", unit::BRIDGE_PATH);
        return 2;
    }
    match args.first().map(String::as_str) {
        Some("serve") => run_serve(&args[1..]),
        Some("init") => run_init(&args[1..]),
        Some("retool") => run_retool(&args[1..]),
        Some("pair") => run_pair(&args[1..]),
        Some("bind") => run_bind(&args[1..]),
        Some("trust-owner") => run_trust_owner(&args[1..]),
        Some("account") => match Config::load(Path::new(&flag(&args[1..], "--state").unwrap_or_else(|| unit::STATE_DIR.to_string()))) {
            Ok(config) => {
                let linked = matches!(config.mode, Mode::Linked { .. });
                // The account id is the owner's own in linked mode; a dedicated number's is not kept.
                let aci = if linked { config.own_uuid.clone() } else { None };
                println!("{}", setup::bridge_account_line(&setup::BridgeAccount { number: config.account, linked, user: config.user, aci }));
                0
            }
            Err(err) => {
                eprintln!("{err}");
                1
            }
        },
        Some("verify-signal-cli") => run_verify_signal_cli(&args[1..]),
        Some("trust") => match chosen(&args[1..], &invocation.host) {
            Ok(instance) => run_trust(&instance),
            Err(code) => code,
        },
        Some("remove") => run_remove(&args[1..], &invocation.host),
        Some("setup") if has(&args[1..], "--refresh") => run_refresh(&args[1..], invocation),
        Some("setup") => run_setup(&args[1..], invocation),
        Some("status") => run_status(&args[1..], &invocation.host),
        Some("start") => match chosen(&args[1..], &invocation.host) {
            Ok(instance) => systemctl(&["enable", "--now", &instance.unit_name()]),
            Err(code) => code,
        },
        // Disabled as well as stopped, so it stays off across a reboot until `ling signal start`.
        Some("stop") => match chosen(&args[1..], &invocation.host) {
            Ok(instance) => systemctl(&["disable", "--now", &instance.unit_name()]),
            Err(code) => code,
        },
        Some("unit") => match chosen(&args[1..], &invocation.host) {
            Ok(instance) => {
                print!("{}", instance.unit_file_text());
                0
            }
            Err(code) => code,
        },
        Some("--help") | Some("-h") | Some("help") => {
            println!("{USAGE}");
            if invocation.bridge_commands {
                println!("{BRIDGE_USAGE}");
            }
            0
        }
        _ => {
            eprintln!("{USAGE}");
            2
        }
    }
}

/// The bridge binary for setup, or a message saying how to get it.
fn bridge_binary(invocation: &Invocation) -> Result<PathBuf, i32> {
    match &invocation.bridge {
        Some(path) if path.is_file() => Ok(path.clone()),
        _ => {
            eprintln!("❌ The bridge binary, `ling-signal`, is not installed beside `ling`.");
            eprintln!("💡 `ling update` installs it from a release; in a checkout, `ling-admin codex build` builds it.");
            Err(1)
        }
    }
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("a tokio runtime")
}

fn run_serve(args: &[String]) -> i32 {
    let state = PathBuf::from(flag(args, "--state").unwrap_or_else(|| unit::STATE_DIR.to_string()));
    let runtime_dir = flag(args, "--runtime").map(PathBuf::from);
    match runtime().block_on(serve::serve(&state, runtime_dir.as_deref())) {
        Ok(()) => 0,
        Err(serve::Stop::Unlinked) => unit::UNLINKED_EXIT,
        Err(serve::Stop::Failed(err)) => {
            eprintln!("ling-signal: {err}");
            1
        }
    }
}

fn run_init(args: &[String]) -> i32 {
    let (Some(state_dir), Some(account), Some(program)) = (flag(args, "--state"), flag(args, "--account"), flag(args, "--signal-cli")) else {
        eprintln!("init needs --state, --account and --signal-cli");
        return 2;
    };
    let state_dir = PathBuf::from(state_dir);
    let env = env_pairs(args);
    let previous = Config::load(&state_dir).ok();
    let own_uuid = flag(args, "--own-uuid").or_else(|| previous.as_ref().and_then(|p| p.own_uuid.clone()));
    let (mode, owner) = if has(args, "--linked") {
        let (Some(device), Some(uuid)) = (flag(args, "--own-device").and_then(|d| d.parse().ok()), own_uuid.clone()) else {
            eprintln!("--linked needs --own-uuid and --own-device");
            return 2;
        };
        // Linked: the owner is the account itself, and no identity key is compared (gate.rs).
        (Mode::Linked { own_device: device }, Some(gate::Owner { aci: uuid, fingerprint: String::new() }))
    } else {
        (Mode::Dedicated, previous.as_ref().and_then(|p| p.owner.clone()))
    };
    let config = Config {
        mode,
        account,
        own_uuid,
        owner,
        binding: None,
        port: flag(args, "--port").and_then(|p| p.parse().ok()).unwrap_or(3100),
        node: flag(args, "--node").unwrap_or_default(),
        signal_cli: PathBuf::from(program),
        signal_cli_env: env,
        signal_cli_version: flag(args, "--version").unwrap_or_default(),
        vision: has(args, "--vision"),
        instance: flag(args, "--instance").or_else(|| previous.as_ref().and_then(|p| p.instance.clone())),
        user: flag(args, "--user").or_else(|| previous.as_ref().and_then(|p| p.user.clone())),
    };
    match config.save(&state_dir) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("Could not write bridge.json: {err}");
            1
        }
    }
}

/// `retool`: points the bridge's settings at another signal-cli and Java (`setup --refresh`). The
/// account, the owner and the pairing stay as they are.
fn run_retool(args: &[String]) -> i32 {
    let (Some(state_dir), Some(program), Some(version)) = (flag(args, "--state"), flag(args, "--signal-cli"), flag(args, "--version")) else {
        eprintln!("retool needs --state, --signal-cli and --version");
        return 2;
    };
    let state_dir = PathBuf::from(state_dir);
    let mut config = match Config::load(&state_dir) {
        Ok(config) => config,
        Err(err) => {
            eprintln!("{err}");
            return 1;
        }
    };
    config.signal_cli = PathBuf::from(program);
    config.signal_cli_version = version;
    config.signal_cli_env = env_pairs(args);
    match config.save(&state_dir) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("Could not write bridge.json: {err}");
            1
        }
    }
}

/// Every `--env K=V` on a command line.
fn env_pairs(args: &[String]) -> Vec<(String, String)> {
    let mut env = Vec::new();
    for (i, arg) in args.iter().enumerate() {
        if arg == "--env"
            && let Some((key, value)) = args.get(i + 1).and_then(|pair| pair.split_once('='))
        {
            env.push((key.to_string(), value.to_string()));
        }
    }
    env
}

/// `setup --refresh` (§3, §18.3): the bridge binary and the pinned runtime again, once, then every
/// instance's settings and unit, restarting each that was running.
fn run_refresh(args: &[String], invocation: &Invocation) -> i32 {
    let mut instances = invocation.host.instances();
    if instances.is_empty() {
        if !has(args, "--dry-run") {
            eprintln!("Mightling over Signal is not set up here; `ling signal setup` sets it up.");
            return 1;
        }
        instances.push(Instance::first());
    }
    let bridge = match bridge_binary(invocation) {
        Ok(path) => path,
        Err(code) => return code,
    };
    let arch = std::env::consts::ARCH;
    let work = std::env::temp_dir().join(format!("ling-signal-refresh-{}", std::process::id()));
    let java_present = setup::java_home().join("bin/java").exists();
    let signal_cli_present = setup::signal_cli_program().exists() && (arch != "aarch64" || setup::native_dir().join("libsignal_jni.so").exists());
    let plan = setup::refresh_plan(&bridge, &work, arch, java_present, signal_cli_present, &instances);
    let accounts: Vec<String> = instances.iter().map(|i| format!("`{}`", i.account())).collect();
    println!("This installs the current bridge and the pinned signal-cli {} and Java {} for {},", setup::SIGNAL_CLI_VERSION, setup::JAVA_VERSION, accounts.join(", "));
    println!("then restarts {}. The links, the keys and the pairings stay. It runs:\n", if instances.len() > 1 { "each bridge that runs" } else { "it" });
    for step in &plan {
        if !step.what.is_empty() {
            println!("  • {}", step.what);
        }
        println!("      {}", step.display());
    }
    if has(args, "--dry-run") {
        return 0;
    }
    if prompt("\nGo ahead? [y/N]").to_ascii_lowercase() != "y" {
        println!("Nothing was changed.");
        return 1;
    }
    let _ = std::fs::create_dir_all(&work);
    for step in &plan {
        println!("🔑 {}", step.display());
        let mut command = step.command.clone();
        if step.root {
            command.insert(0, "sudo".to_string());
        }
        if let Err(output) = run_command(&command, step.stdin.as_deref()) {
            eprintln!("❌ That step failed:\n{output}");
            return 1;
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    println!("✅ The bridge is current.");
    0
}

/// `verify-signal-cli`: exit 0 only when the tarball carries a good signature by the pinned key.
fn run_verify_signal_cli(args: &[String]) -> i32 {
    let (Some(file), Some(signature)) = (flag(args, "--file"), flag(args, "--signature")) else {
        eprintln!("verify-signal-cli needs --file and --signature");
        return 2;
    };
    match crate::release_signature::verify_signal_cli(Path::new(&file), Path::new(&signature)) {
        Ok(()) => {
            println!("✅ Good signature by the signal-cli maintainer's pinned key {}.", crate::release_signature::SIGNAL_CLI_KEY_FINGERPRINT);
            0
        }
        Err(err) => {
            eprintln!("❌ signal-cli's signature check failed, so nothing is unpacked: {err}");
            1
        }
    }
}

fn run_pair(args: &[String]) -> i32 {
    let (Some(state_dir), Some(code)) = (flag(args, "--state"), flag(args, "--code")) else {
        eprintln!("pair needs --state and --code");
        return 2;
    };
    let port = flag(args, "--port").and_then(|p| p.parse().ok()).unwrap_or(3100);
    let device = flag(args, "--device").unwrap_or_else(|| crate::agent::DEVICE_NAME.to_string());
    match runtime().block_on(crate::agent::pair(port, &code, &device)) {
        Ok(cookie) => match state::save_cookie(Path::new(&state_dir), &cookie) {
            Ok(()) => {
                println!("✅ Paired with ling web as `{device}`.");
                0
            }
            Err(err) => {
                eprintln!("Could not keep the device cookie: {err}");
                1
            }
        },
        Err(err) => {
            eprintln!("❌ {err}");
            1
        }
    }
}

fn run_bind(args: &[String]) -> i32 {
    let state_dir = PathBuf::from(flag(args, "--state").unwrap_or_else(|| unit::STATE_DIR.to_string()));
    let mut config = match Config::load(&state_dir) {
        Ok(config) => config,
        Err(err) => {
            eprintln!("{err}");
            return 1;
        }
    };
    let code = gate::pairing_code();
    config.owner = None;
    config.binding = Some(Binding { code: code.clone(), expires_ms: serve::now_ms() + gate::BINDING_MS });
    match config.save(&state_dir) {
        Ok(()) => {
            println!("{code}");
            0
        }
        Err(err) => {
            eprintln!("Could not write bridge.json: {err}");
            1
        }
    }
}

fn systemctl(args: &[&str]) -> i32 {
    let status = Command::new("sudo").arg("systemctl").args(args).status();
    status.map(|s| s.code().unwrap_or(1)).unwrap_or(1)
}

/// `status` (§18.3): with `--name`, or with one instance at most, the detail as it always was;
/// with several, one line each, from their public status files.
fn run_status(args: &[String], host: &Host) -> i32 {
    let instances = host.instances();
    if flag(args, "--name").is_none() && instances.len() > 1 {
        println!("Mightling over Signal: {} bridges", instances.len());
        for instance in &instances {
            let status = host.status_of(instance);
            println!("  {}", status_line(instance, status.as_ref(), host.unit_active(instance), serve::now_ms()));
        }
        println!("`ling signal status --name NAME` shows one in detail.");
        return 0;
    }
    let instance = match chosen(args, host) {
        Ok(instance) => instance,
        Err(code) => return code,
    };
    let runtime_dir = flag(args, "--runtime").map(PathBuf::from).unwrap_or_else(|| host.path(&instance.runtime_dir()));
    let active = host.unit_active(&instance);
    let label = if instance.is_first() { String::new() } else { format!(" `{instance}`") };
    let Ok(text) = std::fs::read_to_string(runtime_dir.join("status.json")) else {
        println!("Mightling over Signal{label}: {}", if active { "starting" } else { "not running (`ling signal setup` sets it up)" });
        return if active { 0 } else { 3 };
    };
    let status: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
    let yes = |key: &str| status.get(key).and_then(serde_json::Value::as_bool).unwrap_or(false);
    println!("Mightling over Signal{label}: {}", if active && yes("running") { "running" } else { "stopped" });
    println!("  mode:              {}", status.get("mode").and_then(|v| v.as_str()).unwrap_or("?"));
    if let Some(user) = status.get("user").and_then(|v| v.as_str()) {
        println!("  answers with:      {user}'s ling web");
    }
    println!("  owner paired:      {}", if yes("ownerPaired") { "yes" } else { "no (`ling signal setup` shows a code to send)" });
    println!("  ling web:          {}", if yes("webConnected") { "connected" } else { "not answering" });
    println!("  air gap:           {}", if yes("airgapped") { "on: nothing is sent or answered" } else { "off" });
    if let Some(ms) = status.get("lastOwnerMessageMs").and_then(serde_json::Value::as_u64) {
        let ago = serve::now_ms().saturating_sub(ms) / 60_000;
        println!("  last message:      {ago} min ago");
    }
    if let Some(ignored) = status.get("strangersIgnored").and_then(serde_json::Value::as_u64) {
        println!("  strangers ignored: {ignored}");
    }
    let refusals = status.get("identityRefusals").and_then(serde_json::Value::as_u64).unwrap_or(0);
    if refusals > 0 {
        println!("  ⚠️  {refusals} message(s) refused: the owner's safety number changed.");
    }
    if let Some(error) = status.get("lastError").and_then(|v| v.as_str()) {
        println!("  last error:        {error}");
    }
    println!(
        "  versions:          bridge {}, signal-cli {}",
        status.get("bridgeVersion").and_then(|v| v.as_str()).unwrap_or("?"),
        status.get("signalCliVersion").and_then(|v| v.as_str()).unwrap_or("?")
    );
    if env!("CARGO_PKG_VERSION") != status.get("bridgeVersion").and_then(|v| v.as_str()).unwrap_or("") {
        println!("  💡 This ling-signal is {}; `ling signal setup --refresh` installs it for the bridge.", env!("CARGO_PKG_VERSION"));
    }
    0
}

/// One instance's line in `status` with several (§18.3): its name, mode, the user it serves,
/// whether it runs, and its last owner message. The status file holds no account id and no text.
pub fn status_line(instance: &Instance, status: Option<&serde_json::Value>, active: bool, now_ms: u64) -> String {
    let text = |key: &str| status.and_then(|s| s.get(key)).and_then(|v| v.as_str()).map(str::to_string);
    let running = active && status.and_then(|s| s.get("running")).and_then(serde_json::Value::as_bool).unwrap_or(false);
    let last = status
        .and_then(|s| s.get("lastOwnerMessageMs"))
        .and_then(serde_json::Value::as_u64)
        .map(|ms| format!("last message {} min ago", now_ms.saturating_sub(ms) / 60_000))
        .unwrap_or_else(|| "no message yet".to_string());
    format!(
        "{:<12} {:<9} {:<12} {:<11} {last}",
        instance.name(),
        text("mode").unwrap_or_else(|| "?".to_string()),
        text("user").unwrap_or_else(|| "-".to_string()),
        if running { "running" } else if active { "starting" } else { "not running" },
    )
}

/// Runs one command and returns its combined output; prints it as it would appear.
fn run_command(command: &[String], stdin: Option<&str>) -> Result<String, String> {
    let mut child = Command::new(&command[0])
        .args(&command[1..])
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::inherit() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("{}: {err}", command[0]))?;
    if let (Some(text), Some(mut pipe)) = (stdin, child.stdin.take()) {
        pipe.write_all(text.as_bytes()).map_err(|err| err.to_string())?;
    }
    let output = child.wait_with_output().map_err(|err| err.to_string())?;
    let text = format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    if output.status.success() { Ok(text) } else { Err(text) }
}

fn prompt(question: &str) -> String {
    print!("{question} ");
    let _ = std::io::stdout().flush();
    let mut line = String::new();
    let _ = std::io::stdin().lock().read_line(&mut line);
    line.trim().to_string()
}

/// The instance `setup` makes (§18.3, §18.4 check 1): `--name`, or `default` on a node without
/// one. Refused when the name is taken, even half (an account, a state folder or a unit left by a
/// failed run): setup never writes into another bridge's state.
fn setup_instance(args: &[String], host: &Host) -> Result<Instance, i32> {
    let instance = match flag(args, "--name") {
        Some(name) => Instance::parse(&name).map_err(|err| {
            eprintln!("{err}");
            2
        })?,
        None if host.trace_of(&Instance::first()).is_some() => {
            eprintln!("This node already has a Signal bridge (`default`). To link another Signal account, give it a name:");
            eprintln!("  ling signal setup --name NAME    (1 to 12 lowercase letters, digits or `-`)");
            eprintln!("To set `default` up again, `ling signal remove` first.");
            return Err(2);
        }
        None => Instance::first(),
    };
    if let Some(trace) = host.trace_of(&instance) {
        let name = if instance.is_first() { String::new() } else { format!(" --name {instance}") };
        eprintln!("A Signal bridge named `{instance}` is already here ({trace}). `ling signal remove{name}` removes it first.");
        return Err(2);
    }
    Ok(instance)
}

/// §18.4 check 2: fewer than four instances, and the memory rule.
fn room_for_another(host: &Host) -> Result<(), String> {
    let existing = host.instances();
    if existing.len() >= unit::MAX_INSTANCES {
        return Err(format!("This node already has {} Signal bridges ({}), the most it runs; `ling signal remove --name NAME` one first.", existing.len(), names(&existing)));
    }
    match host.mem_available() {
        Some(available) if !setup::memory_allows(available, existing.len()) => Err(format!(
            "Only {} MB of memory is available; a bridge needs 2 GB to spare above the {} MB of the {} already here (each is capped at {}).",
            available / (1024 * 1024),
            unit::MEMORY_MAX_BYTES * existing.len() as u64 / (1024 * 1024),
            existing.len(),
            unit::MEMORY_MAX
        )),
        Some(_) => Ok(()),
        None => {
            println!("⚠️  Could not read the memory available (/proc/meminfo); going on.");
            Ok(())
        }
    }
}

/// §18.4 check 3: the invoking user's `ling web` answers; its port (`--port`, or the one it runs on).
fn own_web_port(args: &[String]) -> Result<u16, String> {
    let output = run_command(&["ling".to_string(), "web".to_string(), "status".to_string()], None)
        .map_err(|_| "Your `ling web` is not running; the bridge answers through it. `ling web start` starts it.".to_string())?;
    match flag(args, "--port") {
        Some(port) => port.parse().map_err(|_| "--port takes a port number".to_string()),
        None => setup::web_port_in(&output).ok_or_else(|| format!("Could not read the port from `ling web status`:\n{output}")),
    }
}

/// The other instances' accounts, each read through its own account (their state folders are 0700),
/// for §18.4 check 4 and the duplicate number check.
fn other_accounts(instance: &Instance, host: &Host) -> Vec<(Instance, setup::BridgeAccount)> {
    host.instances().into_iter().filter(|other| other != instance).filter_map(|other| bridge_account_of(&other).map(|held| (other, held))).collect()
}

/// A setup that failed half-way leaves no half instance (§18.4): its unit, state and account go,
/// and the shared runtime stays if another instance uses it. Never `unregister`: the number may be
/// the owner's own, or another instance's.
fn abandon(instance: &Instance, host: &Host, linked: bool) {
    let others: Vec<Instance> = host.instances().into_iter().filter(|other| other != instance).collect();
    eprintln!("\nUndoing what this setup made for `{instance}`, so nothing half set up is left:");
    for step in setup::remove_plan(instance, std::env::consts::ARCH, None, true, &others) {
        // The `ling web` device was never paired if setup stopped before pairing; a failure is fine.
        let mut command = step.command.clone();
        if step.root {
            command.insert(0, "sudo".to_string());
        }
        println!("🔑 {}", step.display());
        if let Err(output) = run_command(&command, None) {
            eprintln!("   ({})", output.trim());
        }
    }
    if linked {
        eprintln!("If the phone shows a new linked device for this, remove it there too: Signal → Settings → Linked devices.");
    }
}

fn run_setup(args: &[String], invocation: &Invocation) -> i32 {
    let host = &invocation.host;
    // Linked to the owner's own account by default (the user's choice, 2026-10-08); `--number`
    // registers a dedicated number instead.
    let number = flag(args, "--number");
    let linked = number.is_none();
    if let Some(number) = &number
        && !setup::valid_number(number)
    {
        eprintln!("{number} is not a number in international form, e.g. +15551234567.");
        return 2;
    }
    let dry_run = has(args, "--dry-run");
    let instance = match setup_instance(args, host) {
        Ok(instance) => instance,
        Err(code) => return code,
    };
    if let Err(err) = room_for_another(host) {
        eprintln!("❌ {err}");
        return 1;
    }
    let arch = std::env::consts::ARCH;
    let bridge = match bridge_binary(invocation) {
        Ok(path) => path,
        Err(code) => return code,
    };
    // A dry run starts nothing: it neither runs `ling` nor reads another bridge through sudo.
    let port = if dry_run {
        flag(args, "--port").and_then(|p| p.parse().ok()).unwrap_or(3100)
    } else {
        match own_web_port(args) {
            Ok(port) => port,
            Err(err) => {
                eprintln!("❌ {err}");
                return 1;
            }
        }
    };
    let user = if dry_run { String::new() } else { current_user() };
    let work = std::env::temp_dir().join(format!("ling-signal-setup-{}", std::process::id()));
    let account_exists = host.account_exists(&instance.account());
    let java_present = setup::java_home().join("bin/java").exists();
    let signal_cli_present = setup::signal_cli_program().exists() && (arch != "aarch64" || setup::native_dir().join("libsignal_jni.so").exists());
    let qrencode_present = Command::new("sh").args(["-c", "command -v qrencode"]).stdout(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
    let plan = setup::install_plan(&instance, &bridge, &work, arch, account_exists, java_present, signal_cli_present, linked && !qrencode_present);

    let named = if instance.is_first() { String::new() } else { format!(" `{instance}`") };
    match &number {
        Some(number) => println!("Mightling over Signal{named}: setup with its own number, {number}\n"),
        None => println!("Mightling over Signal{named}: setup linked to your own Signal account\n"),
    }
    println!("This installs signal-cli and a small bridge that runs as its own system account,");
    println!("`{}`, so the agent can never read the Signal keys. Every download is checked before it is", instance.account());
    println!("unpacked; signal-cli twice, by its pinned SHA-256 and by its maintainer's OpenPGP signature.");
    if !instance.is_first() {
        println!("signal-cli and Java are shared with the other bridges and fetched only if missing; this");
        println!("bridge's account, keys, unit and `ling web` device ({}) are its own.", instance.device_name());
    }
    println!("It changes these things:\n");
    for step in &plan {
        if !step.what.is_empty() {
            println!("  • {}", step.what);
        }
        println!("      {}", step.display());
    }
    if linked {
        println!("\nThen it links the bridge to your Signal account as a new device (you scan a QR code");
        println!("with your phone), and you talk to Mightling in Note to Self. Read this first:");
        println!("  • A linked device can read every message your account receives from now on, and send as you.");
        println!("    signal-cli decrypts everything that arrives; the bridge acts on Note to Self only and drops");
        println!("    the rest without storing or answering it. Its keys are keys to your whole account. They");
        println!("    live in {} (0700, its own account),", instance.state_dir());
        println!("    out of the agent's reach. `ling signal remove` deletes them.");
        println!("  • If your phone offers to transfer your message history, choose \"Don't transfer\".");
        println!("  • Mightling's replies in Note to Self start with {}so you can tell them from your notes.", crate::format::LINKED_MARKER);
    } else {
        println!("\nThen it registers the number with Signal (an SMS or voice code) and asks you to send a");
        println!("pairing code from your phone.");
    }
    println!("\nIt pairs the bridge with your `ling web` and starts `{}`. The bridge connects to Signal's", instance.unit_name());
    println!("servers only, and acts only while the air gap is off.");
    if dry_run {
        println!("\n(A real run first checks that your `ling web` answers, and that no other bridge here holds the account.)");
        return 0;
    }
    if prompt("\nGo ahead? [y/N]").to_ascii_lowercase() != "y" {
        println!("Nothing was changed.");
        return 1;
    }
    // The other bridges' settings are read through their own accounts (sudo), so only after the
    // owner has seen the plan and said yes; still before anything is changed.
    let others = other_accounts(&instance, host);
    if let Some((other, served)) = setup::serves_someone_else(&user, &others) {
        eprintln!("❌ The bridge `{other}` here answers with {served}'s agent. Bridges for several people on one node");
        eprintln!("   are not turned on yet (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §18.7); every bridge here serves one user.");
        eprintln!("Nothing was changed.");
        return 1;
    }
    if let Some(number) = &number
        && let Some(holder) = setup::held_by(number, None, &others)
    {
        eprintln!("❌ {number} is already the bridge `{holder}`'s; one Signal account has one bridge. Nothing was changed.");
        return 1;
    }
    let _ = std::fs::create_dir_all(&work);
    for step in &plan {
        println!("🔑 {}", step.display());
        let mut command = step.command.clone();
        if step.root {
            command.insert(0, "sudo".to_string());
        }
        if let Err(output) = run_command(&command, step.stdin.as_deref()) {
            eprintln!("❌ That step failed:\n{output}");
            let _ = std::fs::remove_dir_all(&work);
            abandon(&instance, host, false);
            return 1;
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    match finish_setup(&instance, host, args, number, port, &user) {
        Ok(()) => 0,
        Err(Abandon { linked_device }) => {
            abandon(&instance, host, linked_device);
            1
        }
    }
}

/// Why `finish_setup` stopped: the instance is then removed again.
struct Abandon {
    /// Whether the phone already shows a linked device for it.
    linked_device: bool,
}

/// After the install: link or register, check that no other instance holds the account, pair
/// with the user's `ling web`, write the settings and start the unit.
fn finish_setup(instance: &Instance, host: &Host, args: &[String], number: Option<String>, port: u16, user: &str) -> Result<(), Abandon> {
    let arch = std::env::consts::ARCH;
    let linked = number.is_none();
    let node = run_command(&["hostname".to_string()], None).unwrap_or_default().trim().to_string();
    let (account, own_uuid, own_device) = if let Some(number) = number {
        if !register(instance, arch, &number, has(args, "--voice")) {
            return Err(Abandon { linked_device: false });
        }
        (number, None, None)
    } else {
        match link(instance, arch, &node) {
            Some(linked) => linked,
            None => return Err(Abandon { linked_device: false }),
        }
    };
    // §18.4 check 4: one Signal account, one bridge.
    if let Some(holder) = setup::held_by(&account, own_uuid.as_deref(), &other_accounts(instance, host)) {
        eprintln!("❌ {account} is already linked to the bridge `{holder}` on this node; one Signal account has one bridge.");
        return Err(Abandon { linked_device: linked });
    }
    if linked {
        set_note_to_self_timer(instance, arch, &account);
    }

    // Pair the bridge with the user's own `ling web` as a device, then write its settings.
    let pairing = match run_command(&["ling".to_string(), "web".to_string(), "pair".to_string()], None) {
        Ok(output) => setup::pairing_code_in(&output),
        Err(output) => {
            eprintln!("❌ `ling web pair` failed (is `ling web start` running?):\n{output}");
            return Err(Abandon { linked_device: linked });
        }
    };
    let Some(pairing) = pairing else {
        eprintln!("❌ `ling web pair` printed no code.");
        return Err(Abandon { linked_device: linked });
    };
    let bridge_bin = unit::BRIDGE_PATH.to_string();
    let state_dir = instance.state_dir();
    let pair = [bridge_bin.clone(), "pair".into(), "--state".into(), state_dir.clone(), "--port".into(), port.to_string(), "--code".into(), pairing, "--device".into(), instance.device_name()];
    if let Err(output) = run_command(&setup::as_bridge(instance, &pair), None) {
        eprintln!("❌ {output}");
        return Err(Abandon { linked_device: linked });
    }
    let mut init = vec![
        bridge_bin.clone(),
        "init".into(),
        "--state".into(),
        state_dir.clone(),
        "--account".into(),
        account.clone(),
        "--node".into(),
        node,
        "--port".into(),
        port.to_string(),
        "--signal-cli".into(),
        setup::signal_cli_program().to_string_lossy().into_owned(),
        "--version".into(),
        setup::SIGNAL_CLI_VERSION.into(),
        "--instance".into(),
        instance.name().to_string(),
    ];
    if !user.is_empty() {
        init.extend(["--user".into(), user.to_string()]);
    }
    if let (Some(uuid), Some(device)) = (&own_uuid, own_device) {
        init.extend(["--linked".into(), "--own-uuid".into(), uuid.clone(), "--own-device".into(), device.to_string()]);
    }
    for (key, value) in setup::signal_cli_env(arch) {
        init.push("--env".into());
        init.push(format!("{key}={value}"));
    }
    if let Err(output) = run_command(&setup::as_bridge(instance, &init), None) {
        eprintln!("❌ {output}");
        return Err(Abandon { linked_device: linked });
    }
    let unit_name = instance.unit_name();
    if linked {
        if systemctl(&["enable", "--now", &unit_name]) != 0 {
            eprintln!("❌ The unit did not start: `journalctl -u {unit_name}`.");
            return Err(Abandon { linked_device: true });
        }
        println!("\n✅ Linked. In Signal, open Note to Self and write /help. Mightling's replies start with {}", crate::format::LINKED_MARKER);
        return Ok(());
    }
    let owner_code = match run_command(&setup::as_bridge(instance, &[bridge_bin, "bind".into(), "--state".into(), state_dir]), None) {
        Ok(output) => output.trim().to_string(),
        Err(output) => {
            eprintln!("❌ {output}");
            return Err(Abandon { linked_device: false });
        }
    };
    if systemctl(&["enable", "--now", &unit_name]) != 0 {
        eprintln!("❌ The unit did not start: `journalctl -u {unit_name}`.");
        return Err(Abandon { linked_device: false });
    }
    println!("\n📱 From your phone, send this code to {account} in Signal within ten minutes:\n\n      {owner_code}\n");
    println!("The first account to send it becomes Mightling's owner; anyone else is ignored.");
    let deadline = std::time::Instant::now() + Duration::from_millis(gate::BINDING_MS);
    while std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_secs(3));
        let paired = host.status_of(instance).and_then(|v| v.get("ownerPaired").and_then(serde_json::Value::as_bool)).unwrap_or(false);
        if paired {
            println!("✅ Paired. Ask Mightling anything from Signal; /help lists the commands.");
            return Ok(());
        }
    }
    // The bridge is set up and running; only the owner's code is missing, so it stays.
    println!("⚠️  No code arrived in ten minutes. Run `sudo -u {} {} bind --state {}` for a new one, then restart the unit.", instance.account(), unit::BRIDGE_PATH, instance.state_dir());
    Ok(())
}

/// Dedicated mode: registers the number (a CAPTCHA if Signal wants one, then the SMS or voice
/// code), sets the registration lock, the profile name, and number discovery off.
fn register(instance: &Instance, arch: &str, number: &str, voice: bool) -> bool {
    let mut register: Vec<&str> = vec!["register"];
    if voice {
        register.push("--voice");
    }
    let mut result = run_command(&setup::signal_cli_command(instance, arch, number, &register), None);
    if let Err(output) = &result
        && output.to_ascii_lowercase().contains("captcha")
    {
        println!("\nSignal asks for a CAPTCHA. Open https://signalcaptchas.org/registration/generate.html,");
        println!("solve it, then right-click \"Open Signal\" and copy the link (it starts with signalcaptcha://).");
        let Some(token) = setup::captcha_token(&prompt("Paste the link:")) else {
            eprintln!("That is not a signalcaptcha:// link.");
            return false;
        };
        let mut again = register.clone();
        again.extend(["--captcha", token.as_str()]);
        result = run_command(&setup::signal_cli_command(instance, arch, number, &again), None);
    }
    if let Err(output) = result {
        eprintln!("❌ Signal refused the registration:\n{output}");
        return false;
    }
    let code = prompt(&format!("Signal sent a code to {number}. Type it:"));
    if let Err(output) = run_command(&setup::signal_cli_command(instance, arch, number, &["verify", code.trim()]), None) {
        eprintln!("❌ The code was not accepted:\n{output}");
        return false;
    }
    let pin = format!("{:08}", rand::random_range(0..100_000_000u32));
    for (what, args) in [
        ("registration lock", vec!["setPin", pin.as_str()]),
        ("profile name", vec!["updateProfile", "--given-name", "Mightling"]),
        ("number discovery off", vec!["updateAccount", "--discoverable-by-number", "false"]),
    ] {
        if let Err(output) = run_command(&setup::signal_cli_command(instance, arch, number, &args), None) {
            eprintln!("⚠️  Could not set the {what}: {output}");
        }
    }
    let pin_path = format!("{}/pin", instance.state_dir());
    let _ = run_command(&setup::as_bridge(instance, &["sh".to_string(), "-c".to_string(), format!("umask 077; cat > {pin_path}")]), Some(&pin));
    true
}

/// Linked mode: `signal-cli link`, its QR code shown here, until the phone has scanned it. Returns
/// the account's number, its id and this device's id.
fn link(instance: &Instance, arch: &str, node: &str) -> Option<(String, Option<String>, Option<u64>)> {
    let name = instance.link_name(node);
    let command = setup::signal_cli_bare(instance, arch, &["link", "-n", &name]);
    let mut child = match Command::new(&command[0]).args(&command[1..]).stdout(Stdio::piped()).stderr(Stdio::inherit()).spawn() {
        Ok(child) => child,
        Err(err) => {
            eprintln!("❌ Could not start signal-cli: {err}");
            return None;
        }
    };
    let stdout = child.stdout.take()?;
    let mut number = None;
    for line in std::io::BufReader::new(stdout).lines().map_while(Result::ok) {
        if line.starts_with("sgnl://") {
            println!("\nOn your phone: Signal → Settings → Linked devices → Link new device, and scan:\n");
            let shown = Command::new("qrencode").args(["-t", "ANSIUTF8", "-m", "2", &line]).status().map(|s| s.success()).unwrap_or(false);
            if !shown {
                println!("(qrencode is not installed; make a QR code of this link with any tool:)");
            }
            println!("{line}\n");
            println!("If the phone offers to transfer your message history, choose \"Don't transfer\".");
            println!("Waiting for the phone…");
        } else if let Some(associated) = setup::associated_number(&line) {
            number = Some(associated);
        }
    }
    let _ = child.wait();
    let Some(number) = number else {
        eprintln!("❌ The link was not completed (it times out after a few minutes; run setup again).");
        return None;
    };
    println!("✅ Linked to {number}.");
    let aci = run_command(&setup::signal_cli_bare(instance, arch, &["-o", "json", "listAccounts"]), None).ok().and_then(|out| setup::account_aci(&out, &number));
    let device = run_command(&setup::signal_cli_command(instance, arch, &number, &["listDevices"]), None).ok().and_then(|out| setup::this_device(&out));
    if aci.is_none() || device.is_none() {
        eprintln!("❌ signal-cli did not say this account's id or this device's number.");
        return None;
    }
    Some((number, aci, device))
}

/// Note to Self has one disappearing-message timer, shared by the owner's own notes and
/// Mightling's replies, and it reaches every device of the account (§4.2). Set only with consent.
fn set_note_to_self_timer(instance: &Instance, arch: &str, number: &str) {
    println!("\nDisappearing messages: Mightling's replies can disappear after a week, so code and mail");
    println!("shown in them don't stay on your phone. Note to Self has one timer for the whole");
    println!("conversation: your own notes written there from now on would disappear after a week too");
    println!("(notes already there stay), on every device. You can change it on the phone at any time.");
    if prompt("Set Note to Self to disappear after one week? [Y/n]").to_ascii_lowercase() == "n" {
        println!("Left as it is.");
        return;
    }
    let seconds = 7 * 24 * 3600;
    match run_command(&setup::signal_cli_command(instance, arch, number, &["updateContact", number, "--expiration", &seconds.to_string()]), None) {
        Ok(_) => println!("✅ Note to Self now disappears after one week."),
        Err(output) => eprintln!("⚠️  Could not set it ({output}); set it on the phone: Note to Self → the name at the top → Disappearing messages."),
    }
}

/// An instance's settings, read through its own account (the state folder is 0700).
fn bridge_account_of(instance: &Instance) -> Option<setup::BridgeAccount> {
    let output = run_command(&setup::as_bridge(instance, &[unit::BRIDGE_PATH.to_string(), "account".to_string(), "--state".to_string(), instance.state_dir()]), None).ok()?;
    setup::parse_bridge_account(&output)
}

fn run_remove(args: &[String], host: &Host) -> i32 {
    let instance = match chosen(args, host) {
        Ok(instance) => instance,
        Err(code) => return code,
    };
    let others: Vec<Instance> = host.instances().into_iter().filter(|other| *other != instance).collect();
    let arch = std::env::consts::ARCH;
    // A dry run starts nothing as root: the number is looked up only for a real removal.
    let dry_run = has(args, "--dry-run");
    let found = if dry_run { None } else { bridge_account_of(&instance) };
    if let Some(found) = &found
        && let Err(err) = setup::may_act(&instance, found.user.as_deref(), &current_user())
    {
        eprintln!("❌ {err}");
        return 1;
    }
    // Unknown (a dry run, or no state): treated as linked, so `unregister` is never run on a guess.
    let linked = found.as_ref().is_none_or(|found| found.linked);
    let plan = setup::remove_plan(&instance, arch, found.as_ref().map(|found| found.number.as_str()), linked, &others);
    let which = if instance.is_first() && others.is_empty() { String::new() } else { format!(" (the bridge `{instance}`)") };
    println!("This removes Mightling over Signal{which} from this machine. Your Ask threads stay. It runs:\n");
    for step in &plan {
        if !step.what.is_empty() {
            println!("  • {}", step.what);
        }
        println!("      {}", step.display());
    }
    if !others.is_empty() {
        println!("\nThe other bridges ({}) and the signal-cli they share stay.", names(&others));
    }
    if linked {
        println!("\nLinked to your own account: on your phone, also open Signal → Settings → Linked devices and");
        let device = if instance.is_first() { "Mightling".to_string() } else { format!("Mightling (…, {instance})") };
        println!("unlink \"{device}\". Your account itself is never unregistered by this.");
    }
    if dry_run {
        println!("\n(With its own number, a real removal also takes that number off Signal with `unregister`.)");
        return 0;
    }
    if prompt("\nRemove it? [y/N]").to_ascii_lowercase() != "y" {
        println!("Nothing was changed.");
        return 1;
    }
    let mut failed = false;
    for step in &plan {
        let mut command = step.command.clone();
        if step.root {
            command.insert(0, "sudo".to_string());
        }
        if let Err(output) = run_command(&command, None) {
            eprintln!("⚠️  {} failed:\n{output}", step.display());
            failed = true;
        }
    }
    if failed { 1 } else { 0 }
}

/// `trust`, as the user the instance serves: stops the bridge, runs `trust-owner` as its account,
/// starts it again.
fn run_trust(instance: &Instance) -> i32 {
    if let Some(found) = bridge_account_of(instance)
        && let Err(err) = setup::may_act(instance, found.user.as_deref(), &current_user())
    {
        eprintln!("❌ {err}");
        return 1;
    }
    let unit_name = instance.unit_name();
    if systemctl(&["stop", &unit_name]) != 0 {
        return 1;
    }
    let status = Command::new("sudo")
        .args(["-u", &instance.account(), unit::BRIDGE_PATH, "trust-owner", "--state", &instance.state_dir()])
        .status()
        .map(|s| s.code().unwrap_or(1))
        .unwrap_or(1);
    let started = systemctl(&["start", &unit_name]);
    if status == 0 && started == 0 { 0 } else { 1 }
}

/// §5.3 on the bridge's data: shows the owner's current safety number, and after the user has
/// compared it with their phone, trusts that key and records its fingerprint.
fn run_trust_owner(args: &[String]) -> i32 {
    let state_dir = PathBuf::from(flag(args, "--state").unwrap_or_else(|| unit::STATE_DIR.to_string()));
    let mut config = match Config::load(&state_dir) {
        Ok(config) => config,
        Err(err) => {
            eprintln!("{err}");
            return 1;
        }
    };
    if let Mode::Linked { .. } = config.mode {
        println!("Linked to your own account, there is no safety number to trust: if the account is registered");
        println!("again, Signal unlinks this device, and `ling signal setup` links it anew.");
        return 0;
    }
    let Some(owner) = config.owner.clone() else {
        eprintln!("No owner is paired yet; `ling signal setup` pairs one.");
        return 1;
    };
    let base = |extra: &[&str]| -> Vec<String> {
        let mut command = vec!["env".to_string()];
        command.extend(config.signal_cli_env.iter().map(|(k, v)| format!("{k}={v}")));
        command.push(config.signal_cli.to_string_lossy().into_owned());
        command.extend(["--config".to_string(), state_dir.join("signal-cli").to_string_lossy().into_owned(), "-o".to_string(), "json".to_string()]);
        command.extend(["-a".to_string(), config.account.clone()]);
        command.extend(extra.iter().map(|s| s.to_string()));
        command
    };
    let identities = match run_command(&base(&["listIdentities", "-n", &owner.aci]), None) {
        Ok(output) => serde_json::from_str::<serde_json::Value>(output.trim()).unwrap_or_default(),
        Err(output) => {
            eprintln!("❌ {output}");
            return 1;
        }
    };
    let Some(current) = identities.as_array().and_then(|list| list.iter().find(|i| i.get("uuid").and_then(|u| u.as_str()) == Some(owner.aci.as_str()))) else {
        eprintln!("signal-cli knows no key for the owner.");
        return 1;
    };
    let safety = current.get("safetyNumber").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    let fingerprint = current.get("fingerprint").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    if fingerprint == owner.fingerprint {
        println!("The owner's key has not changed; nothing to do.");
        return 0;
    }
    println!("The owner's safety number is now:\n\n    {safety}\n");
    println!("On your phone: Signal → the conversation with Mightling → View safety number.");
    if prompt("Do the numbers match? [y/N]").to_ascii_lowercase() != "y" {
        println!("Not trusted. Messages from that account stay refused.");
        return 1;
    }
    if let Err(output) = run_command(&base(&["trust", &owner.aci, "-v", &safety]), None) {
        eprintln!("❌ {output}");
        return 1;
    }
    config.owner = Some(gate::Owner { aci: owner.aci, fingerprint });
    match config.save(&state_dir) {
        Ok(()) => {
            println!("✅ Trusted. The bridge accepts the owner's messages again.");
            0
        }
        Err(err) => {
            eprintln!("Could not write bridge.json: {err}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|w| w.to_string()).collect()
    }

    /// A node of its own for one test: state folders, system accounts, unit files and memory in a
    /// scratch folder, so no test ever sees (or acts on) a real bridge.
    struct Node(PathBuf);

    impl Drop for Node {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    impl Node {
        fn new(tag: &str) -> Node {
            let root = std::env::temp_dir().join(format!("ling-signal-node-{tag}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            for dir in ["var/lib", "etc/systemd/system/multi-user.target.wants", "proc", "run"] {
                std::fs::create_dir_all(root.join(dir)).unwrap();
            }
            std::fs::write(root.join("etc/passwd"), "root:x:0:0::/root:/bin/bash\n").unwrap();
            std::fs::write(root.join("proc/meminfo"), "MemTotal: 127535892 kB\nMemAvailable: 40000000 kB\n").unwrap();
            // Unrelated folders are not bridges.
            std::fs::create_dir_all(root.join("var/lib/mightling-chat")).unwrap();
            std::fs::create_dir_all(root.join("var/lib/mightling-signalx")).unwrap();
            Node(root)
        }

        /// An instance as setup leaves it: its state folder and its account.
        fn with(self, name: &str) -> Node {
            let instance = Instance::parse(name).unwrap();
            std::fs::create_dir_all(self.0.join(instance.state_dir().trim_start_matches('/'))).unwrap();
            self.account(&instance.account())
        }

        fn account(self, account: &str) -> Node {
            let passwd = self.0.join("etc/passwd");
            let text = std::fs::read_to_string(&passwd).unwrap() + &format!("{account}:x:990:990::/nonexistent:/usr/sbin/nologin\n");
            std::fs::write(passwd, text).unwrap();
            self
        }

        fn memory_kib(self, available: u64) -> Node {
            std::fs::write(self.0.join("proc/meminfo"), format!("MemAvailable: {available} kB\n")).unwrap();
            self
        }

        fn host(&self) -> Host {
            Host { root: self.0.clone() }
        }

        fn invocation(&self) -> Invocation {
            Invocation { bridge: std::env::current_exe().ok(), bridge_commands: false, host: self.host() }
        }
    }

    #[test]
    fn ling_signal_refuses_the_bridges_own_commands() {
        let node = Node::new("own");
        let from_ling = Invocation { bridge: None, ..node.invocation() };
        for command in BRIDGE_COMMANDS {
            assert_eq!(run(&args(&[command, "--state", "/nonexistent"]), &from_ling), 2, "{command}");
        }
    }

    #[test]
    fn setup_without_the_bridge_binary_says_how_to_get_it_and_changes_nothing() {
        let node = Node::new("missing");
        let missing = Invocation { bridge: Some(PathBuf::from("/nonexistent/ling-signal")), ..node.invocation() };
        assert_eq!(run(&args(&["setup", "--dry-run"]), &missing), 1);
        assert_eq!(run(&args(&["setup", "--refresh", "--dry-run"]), &missing), 1);
        let none = Invocation { bridge: None, ..node.invocation() };
        assert_eq!(run(&args(&["setup", "--dry-run"]), &none), 1);
    }

    #[test]
    fn a_dry_run_with_the_binary_present_only_prints() {
        let node = Node::new("dry");
        let present = node.invocation();
        assert_eq!(run(&args(&["setup", "--dry-run"]), &present), 0);
        assert_eq!(run(&args(&["setup", "--refresh", "--dry-run"]), &present), 0);
        assert_eq!(run(&args(&["remove", "--dry-run"]), &present), 0);
        assert_eq!(run(&args(&["unknown"]), &present), 2);
        assert_eq!(run(&args(&["help"]), &present), 0);
        // Nothing was made.
        assert!(node.host().instances().is_empty());
    }

    #[test]
    fn instances_are_found_by_their_state_folders_default_first() {
        let node = Node::new("list").with("work").with("default").with("home");
        let found: Vec<String> = node.host().instances().iter().map(|i| i.name().to_string()).collect();
        assert_eq!(found, ["default", "home", "work"]);
        assert!(node.host().account_exists("mightling-signal-work"));
        assert!(!node.host().account_exists("mightling-signal-other"));
    }

    #[test]
    fn setup_beside_the_first_bridge_needs_a_name_and_a_free_one() {
        let node = Node::new("name").with("default");
        let invocation = node.invocation();
        assert_eq!(run(&args(&["setup", "--dry-run"]), &invocation), 2, "default is taken: --name");
        assert_eq!(run(&args(&["setup", "--name", "default", "--dry-run"]), &invocation), 2);
        assert_eq!(run(&args(&["setup", "--name", "bridge", "--dry-run"]), &invocation), 2, "reserved");
        assert_eq!(run(&args(&["setup", "--name", "Work", "--dry-run"]), &invocation), 2, "invalid");
        assert_eq!(run(&args(&["setup", "--name", "work", "--dry-run"]), &invocation), 0);
        assert_eq!(run(&args(&["setup", "--name", "work", "--number", "+15551234567", "--dry-run"]), &invocation), 0);
        // A first bridge left half removed (its account only) still takes the name.
        let half = Node::new("half").account("mightling-signal");
        assert_eq!(run(&args(&["setup", "--dry-run"]), &half.invocation()), 2);
        // So does a further one whose unit is still enabled.
        let enabled = Node::new("enabled").with("default");
        std::fs::write(enabled.0.join("etc/systemd/system/multi-user.target.wants/mightling-signal@work.service"), "").unwrap();
        assert_eq!(run(&args(&["setup", "--name", "work", "--dry-run"]), &enabled.invocation()), 2);
        assert_eq!(run(&args(&["setup", "--name", "home", "--dry-run"]), &enabled.invocation()), 0);
    }

    #[test]
    fn a_fifth_bridge_and_a_short_memory_are_refused() {
        let four = Node::new("four").with("default").with("a").with("b").with("c");
        assert_eq!(run(&args(&["setup", "--name", "d", "--dry-run"]), &four.invocation()), 1);
        let three = Node::new("three").with("default").with("a").with("b");
        assert_eq!(run(&args(&["setup", "--name", "d", "--dry-run"]), &three.invocation()), 0);
        // 2 GB above two caps of 768 MB is 3,670,016 kB.
        let tight = Node::new("tight").with("default").with("a").memory_kib(3_670_015);
        assert_eq!(run(&args(&["setup", "--name", "b", "--dry-run"]), &tight.invocation()), 1);
        let enough = Node::new("enough").with("default").with("a").memory_kib(3_670_016);
        assert_eq!(run(&args(&["setup", "--name", "b", "--dry-run"]), &enough.invocation()), 0);
        // The rule holds for the first bridge too: 2 GB to spare.
        let first = Node::new("first").memory_kib(1_000_000);
        assert_eq!(run(&args(&["setup", "--dry-run"]), &first.invocation()), 1);
    }

    #[test]
    fn with_several_bridges_the_commands_need_a_name() {
        let node = Node::new("several").with("default").with("work");
        let invocation = node.invocation();
        // Refused before anything runs: no sudo, no systemctl.
        for command in ["start", "stop", "trust", "remove", "unit"] {
            assert_eq!(run(&args(&[command]), &invocation), 2, "{command}");
        }
        assert_eq!(run(&args(&["remove", "--dry-run"]), &invocation), 2);
        assert_eq!(run(&args(&["remove", "--name", "work", "--dry-run"]), &invocation), 0);
        assert_eq!(run(&args(&["unit", "--name", "work"]), &invocation), 0);
        assert_eq!(run(&args(&["start", "--name", "Bad"]), &invocation), 2);
        // `status` lists them; one in detail reads its status file under the node's /run.
        assert_eq!(run(&args(&["status"]), &invocation), 0);
        assert_eq!(run(&args(&["status", "--name", "work"]), &invocation), 3, "not running");
        let run_dir = node.0.join("run/mightling-signal-work");
        std::fs::create_dir_all(&run_dir).unwrap();
        std::fs::write(run_dir.join("status.json"), r#"{"running":true,"mode":"linked","instance":"work","user":"owner"}"#).unwrap();
        assert_eq!(run(&args(&["status", "--name", "work"]), &invocation), 0);
        // One bridge: no name needed, as before.
        let one = Node::new("one").with("work");
        assert_eq!(run(&args(&["remove", "--dry-run"]), &one.invocation()), 0);
        assert_eq!(run(&args(&["unit"]), &one.invocation()), 0);
    }

    #[test]
    fn the_status_line_names_no_account() {
        let work = Instance::parse("work").unwrap();
        let status = serde_json::json!({"running": true, "mode": "linked", "instance": "work", "user": "owner", "lastOwnerMessageMs": 1_000});
        let line = status_line(&work, Some(&status), true, 1_000 + 5 * 60_000);
        assert!(line.starts_with("work") && line.contains("linked") && line.contains("owner") && line.contains("running") && line.contains("5 min ago"), "{line}");
        let line = status_line(&work, None, false, 0);
        assert!(line.contains("not running") && line.contains("no message yet"), "{line}");
    }

    #[test]
    fn the_signature_check_fails_closed() {
        let invocation = Invocation { bridge: None, bridge_commands: true, host: Host::system() };
        assert_eq!(run(&args(&["verify-signal-cli", "--file", "/nonexistent/a.tar.gz"]), &invocation), 2);
        assert_eq!(run(&args(&["verify-signal-cli", "--file", "/nonexistent/a.tar.gz", "--signature", "/nonexistent/a.tar.gz.asc"]), &invocation), 1);
        // The real signature over anything but the real tarball.
        let dir = std::env::temp_dir().join(format!("ling-signal-verify-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let tarball = dir.join("signal-cli.tar.gz");
        std::fs::write(&tarball, b"not signal-cli").unwrap();
        let asc = dir.join("signal-cli.tar.gz.asc");
        std::fs::write(&asc, include_str!("../testdata/signal-cli-0.14.9.tar.gz.asc")).unwrap();
        let code = run(&args(&["verify-signal-cli", "--file", &tarball.to_string_lossy(), "--signature", &asc.to_string_lossy()]), &invocation);
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(code, 1);
    }

    #[test]
    fn the_account_command_says_who_is_served() {
        let node = Node::new("account");
        let state = node.0.join("state");
        std::fs::create_dir_all(&state).unwrap();
        let bridge = Invocation { bridge: None, bridge_commands: true, host: node.host() };
        let init = [
            "init", "--state", &state.to_string_lossy(), "--account", "+15550000", "--signal-cli", "/x/signal-cli", "--version", "0.14.9",
            "--linked", "--own-uuid", "aci-1", "--own-device", "4", "--instance", "work", "--user", "owner",
        ];
        assert_eq!(run(&args(&init), &bridge), 0);
        let config = Config::load(&state).unwrap();
        assert_eq!((config.instance.as_deref(), config.user.as_deref()), (Some("work"), Some("owner")));
        // `retool` keeps them.
        assert_eq!(run(&args(&["retool", "--state", &state.to_string_lossy(), "--signal-cli", "/y/signal-cli", "--version", "0.15.0"]), &bridge), 0);
        let config = Config::load(&state).unwrap();
        assert_eq!((config.instance.as_deref(), config.user.as_deref(), config.signal_cli_version.as_str()), (Some("work"), Some("owner"), "0.15.0"));
    }
}
