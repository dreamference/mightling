//! `ling-signal` (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §6).
//!
//! As the user:
//! - `setup [--number +… [--voice]] [--port N] [--dry-run]`: install, link to the owner's account
//!   (the default, §4.2) or register a dedicated number (§4.1), pair with `ling web`, start
//! - `status`: what the running bridge reports (`/run/mightling-signal/status.json`)
//! - `start`, `stop`: the system unit
//! - `trust`: accept the owner's new safety number after comparing it (§5.3)
//! - `remove [--dry-run]`: undo setup; the Ask threads stay
//! - `unit`: print the unit file
//!
//! As the bridge's own account (setup runs these through `sudo -u mightling-signal`):
//! - `serve --state DIR [--runtime DIR]`: the daemon, what the unit runs
//! - `init --state DIR --account +… --node NAME --port N --signal-cli PATH --version V [--env K=V]… [--vision]`
//! - `pair --state DIR --port N --code CODE`: trade a `ling web pair` code for the device cookie
//! - `bind --state DIR`: a fresh six-digit code that makes its first sender the owner
//! - `trust-owner --state DIR`: §5.3 on the bridge's data, with the unit stopped

use std::io::BufRead;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;

use ling_signal::gate;
use ling_signal::gate::Binding;
use ling_signal::gate::Mode;
use ling_signal::serve;
use ling_signal::setup;
use ling_signal::state;
use ling_signal::state::Config;
use ling_signal::unit;

const USAGE: &str = "Usage: ling-signal setup [--number +NUMBER [--voice]] [--port N] [--dry-run] | status | start | stop | trust | remove [--dry-run] | unit\n       ling-signal serve --state DIR [--runtime DIR]   (what the system unit runs)";

fn flag(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
}

fn has(args: &[String], name: &str) -> bool {
    args.iter().any(|a| a == name)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match args.first().map(String::as_str) {
        Some("serve") => run_serve(&args[1..]),
        Some("init") => run_init(&args[1..]),
        Some("pair") => run_pair(&args[1..]),
        Some("bind") => run_bind(&args[1..]),
        Some("trust-owner") => run_trust_owner(&args[1..]),
        Some("account") => match Config::load(Path::new(&flag(&args[1..], "--state").unwrap_or_else(|| unit::STATE_DIR.to_string()))) {
            Ok(config) => {
                println!("{} {}", config.account, if matches!(config.mode, Mode::Linked { .. }) { "linked" } else { "dedicated" });
                0
            }
            Err(err) => {
                eprintln!("{err}");
                1
            }
        },
        Some("trust") => run_trust(),
        Some("remove") => run_remove(&args[1..]),
        Some("setup") => run_setup(&args[1..]),
        Some("status") => run_status(&args[1..]),
        Some("start") => systemctl(&["enable", "--now", unit::UNIT_NAME]),
        Some("stop") => systemctl(&["stop", unit::UNIT_NAME]),
        Some("unit") => {
            print!("{}", unit::unit_text("768M", "256m"));
            0
        }
        Some("--help") | Some("-h") | Some("help") => {
            println!("{USAGE}");
            0
        }
        _ => {
            eprintln!("{USAGE}");
            2
        }
    };
    std::process::exit(code);
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
    let mut env = Vec::new();
    for (i, arg) in args.iter().enumerate() {
        if arg == "--env"
            && let Some((key, value)) = args.get(i + 1).and_then(|pair| pair.split_once('='))
        {
            env.push((key.to_string(), value.to_string()));
        }
    }
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
    };
    match config.save(&state_dir) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("Could not write bridge.json: {err}");
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
    match runtime().block_on(ling_signal::agent::pair(port, &code)) {
        Ok(cookie) => match state::save_cookie(Path::new(&state_dir), &cookie) {
            Ok(()) => {
                println!("✅ Paired with ling web as `{}`.", ling_signal::agent::DEVICE_NAME);
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

fn run_status(args: &[String]) -> i32 {
    let runtime_dir = PathBuf::from(flag(args, "--runtime").unwrap_or_else(|| unit::RUNTIME_DIR.to_string()));
    let active = Command::new("systemctl").args(["is-active", "--quiet", unit::UNIT_NAME]).status().map(|s| s.success()).unwrap_or(false);
    let Ok(text) = std::fs::read_to_string(runtime_dir.join("status.json")) else {
        println!("Mightling over Signal: {}", if active { "starting" } else { "not running (`ling-signal setup` sets it up)" });
        return if active { 0 } else { 3 };
    };
    let status: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
    let yes = |key: &str| status.get(key).and_then(serde_json::Value::as_bool).unwrap_or(false);
    println!("Mightling over Signal: {}", if active && yes("running") { "running" } else { "stopped" });
    println!("  mode:              {}", status.get("mode").and_then(|v| v.as_str()).unwrap_or("?"));
    println!("  owner paired:      {}", if yes("ownerPaired") { "yes" } else { "no (`ling-signal setup` shows a code to send)" });
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
        println!("  💡 This ling-signal is {}; `ling-signal setup --refresh` installs it for the bridge.", env!("CARGO_PKG_VERSION"));
    }
    0
}

/// Runs one command and returns its combined output; prints it as it would appear.
fn run(command: &[String], stdin: Option<&str>) -> Result<String, String> {
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

fn run_setup(args: &[String]) -> i32 {
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
    let port: u16 = flag(args, "--port").and_then(|p| p.parse().ok()).unwrap_or(3100);
    let arch = std::env::consts::ARCH;
    let bridge = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("ling-signal"));
    let work = std::env::temp_dir().join(format!("ling-signal-setup-{}", std::process::id()));
    let account_exists = Command::new("id").arg(unit::ACCOUNT).stdout(Stdio::null()).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
    let java_present = Path::new(&setup::signal_cli_env(arch)[0].1).exists();
    let qrencode_present = Command::new("sh").args(["-c", "command -v qrencode"]).stdout(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
    let plan = setup::install_plan(&bridge, &work, arch, account_exists, java_present, linked && !qrencode_present);

    match &number {
        Some(number) => println!("Mightling over Signal: setup with its own number, {number}\n"),
        None => println!("Mightling over Signal: setup linked to your own Signal account\n"),
    }
    println!("This installs signal-cli and a small bridge that runs as its own system account,");
    println!("`{}`, so the agent can never read the Signal keys. It changes these things:\n", unit::ACCOUNT);
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
        println!("    The bridge acts on Note to Self only and drops everything else unread, but its keys are");
        println!("    keys to your whole account. They live in {} (0700, its own account),", unit::STATE_DIR);
        println!("    out of the agent's reach. `ling-signal remove` deletes them.");
        println!("  • If your phone offers to transfer your message history, choose \"Don't transfer\".");
        println!("  • Mightling's replies in Note to Self start with {}so you can tell them from your notes.", ling_signal::format::LINKED_MARKER);
    } else {
        println!("\nThen it registers the number with Signal (an SMS or voice code) and asks you to send a");
        println!("pairing code from your phone.");
    }
    println!("\nIt pairs the bridge with `ling web` and starts `{}`. The bridge connects to Signal's", unit::UNIT_NAME);
    println!("servers only, and acts only while the air gap is off.");
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
        if let Err(output) = run(&command, step.stdin.as_deref()) {
            eprintln!("❌ That step failed:\n{output}");
            return 1;
        }
    }
    let _ = std::fs::remove_dir_all(&work);

    let node = run(&["hostname".to_string()], None).unwrap_or_default().trim().to_string();
    let (account, own_uuid, own_device) = if let Some(number) = number {
        if !register(arch, &number, has(args, "--voice")) {
            return 1;
        }
        (number, None, None)
    } else {
        match link(arch, &node) {
            Some(linked) => linked,
            None => return 1,
        }
    };
    if linked {
        set_note_to_self_timer(arch, &account);
    }

    // Pair the bridge with `ling web` as a device, then write its settings.
    let pairing = match run(&["ling".to_string(), "web".to_string(), "pair".to_string()], None) {
        Ok(output) => setup::pairing_code_in(&output),
        Err(output) => {
            eprintln!("❌ `ling web pair` failed (is `ling web start` running?):\n{output}");
            return 1;
        }
    };
    let Some(pairing) = pairing else {
        eprintln!("❌ `ling web pair` printed no code.");
        return 1;
    };
    let bridge_bin = unit::BRIDGE_PATH.to_string();
    let state_dir = unit::STATE_DIR.to_string();
    if let Err(output) = run(&setup::as_bridge(&[bridge_bin.clone(), "pair".into(), "--state".into(), state_dir.clone(), "--port".into(), port.to_string(), "--code".into(), pairing]), None) {
        eprintln!("❌ {output}");
        return 1;
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
    ];
    if let (Some(uuid), Some(device)) = (&own_uuid, own_device) {
        init.extend(["--linked".into(), "--own-uuid".into(), uuid.clone(), "--own-device".into(), device.to_string()]);
    }
    for (key, value) in setup::signal_cli_env(arch) {
        init.push("--env".into());
        init.push(format!("{key}={value}"));
    }
    if let Err(output) = run(&setup::as_bridge(&init), None) {
        eprintln!("❌ {output}");
        return 1;
    }
    if linked {
        if systemctl(&["enable", "--now", unit::UNIT_NAME]) != 0 {
            eprintln!("❌ The unit did not start: `journalctl -u {}`.", unit::UNIT_NAME);
            return 1;
        }
        println!("\n✅ Linked. In Signal, open Note to Self and write /help. Mightling's replies start with {}", ling_signal::format::LINKED_MARKER);
        return 0;
    }
    let owner_code = match run(&setup::as_bridge(&[bridge_bin, "bind".into(), "--state".into(), state_dir]), None) {
        Ok(output) => output.trim().to_string(),
        Err(output) => {
            eprintln!("❌ {output}");
            return 1;
        }
    };
    if systemctl(&["enable", "--now", unit::UNIT_NAME]) != 0 {
        eprintln!("❌ The unit did not start: `journalctl -u {}`.", unit::UNIT_NAME);
        return 1;
    }
    println!("\n📱 From your phone, send this code to {account} in Signal within ten minutes:\n\n      {owner_code}\n");
    println!("The first account to send it becomes Mightling's owner; anyone else is ignored.");
    let deadline = std::time::Instant::now() + Duration::from_millis(gate::BINDING_MS);
    while std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_secs(3));
        let paired = std::fs::read_to_string(Path::new(unit::RUNTIME_DIR).join("status.json"))
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .and_then(|v| v.get("ownerPaired").and_then(serde_json::Value::as_bool))
            .unwrap_or(false);
        if paired {
            println!("✅ Paired. Ask Mightling anything from Signal; /help lists the commands.");
            return 0;
        }
    }
    println!("⚠️  No code arrived in ten minutes. Run `sudo -u {} {} bind` for a new one, then restart the unit.", unit::ACCOUNT, unit::BRIDGE_PATH);
    1
}

/// Dedicated mode: registers the number (a CAPTCHA if Signal wants one, then the SMS or voice
/// code), sets the registration lock, the profile name, and number discovery off.
fn register(arch: &str, number: &str, voice: bool) -> bool {
    let mut register: Vec<&str> = vec!["register"];
    if voice {
        register.push("--voice");
    }
    let mut result = run(&setup::signal_cli_command(arch, number, &register), None);
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
        result = run(&setup::signal_cli_command(arch, number, &again), None);
    }
    if let Err(output) = result {
        eprintln!("❌ Signal refused the registration:\n{output}");
        return false;
    }
    let code = prompt(&format!("Signal sent a code to {number}. Type it:"));
    if let Err(output) = run(&setup::signal_cli_command(arch, number, &["verify", code.trim()]), None) {
        eprintln!("❌ The code was not accepted:\n{output}");
        return false;
    }
    let pin = format!("{:08}", rand::random_range(0..100_000_000u32));
    for (what, args) in [
        ("registration lock", vec!["setPin", pin.as_str()]),
        ("profile name", vec!["updateProfile", "--given-name", "Mightling"]),
        ("number discovery off", vec!["updateAccount", "--discoverable-by-number", "false"]),
    ] {
        if let Err(output) = run(&setup::signal_cli_command(arch, number, &args), None) {
            eprintln!("⚠️  Could not set the {what}: {output}");
        }
    }
    let pin_path = format!("{}/pin", unit::STATE_DIR);
    let _ = run(&setup::as_bridge(&["sh".to_string(), "-c".to_string(), format!("umask 077; cat > {pin_path}")]), Some(&pin));
    true
}

/// Linked mode: `signal-cli link`, its QR code shown here, until the phone has scanned it. Returns
/// the account's number, its id and this device's id.
fn link(arch: &str, node: &str) -> Option<(String, Option<String>, Option<u64>)> {
    let name = if node.is_empty() { "Mightling".to_string() } else { format!("Mightling ({node})") };
    let command = setup::signal_cli_bare(arch, &["link", "-n", &name]);
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
    let aci = run(&setup::signal_cli_bare(arch, &["-o", "json", "listAccounts"]), None).ok().and_then(|out| setup::account_aci(&out, &number));
    let device = run(&setup::signal_cli_command(arch, &number, &["listDevices"]), None).ok().and_then(|out| setup::this_device(&out));
    if aci.is_none() || device.is_none() {
        eprintln!("❌ signal-cli did not say this account's id or this device's number; `ling-signal remove`, then setup again.");
        return None;
    }
    Some((number, aci, device))
}

/// Note to Self has one disappearing-message timer, shared by the owner's own notes and
/// Mightling's replies, and it reaches every device of the account (§4.2). Set only with consent.
fn set_note_to_self_timer(arch: &str, number: &str) {
    println!("\nDisappearing messages: Mightling's replies can disappear after a week, so code and mail");
    println!("shown in them don't stay on your phone. Note to Self has one timer for the whole");
    println!("conversation: your own notes written there from now on would disappear after a week too");
    println!("(notes already there stay), on every device. You can change it on the phone at any time.");
    if prompt("Set Note to Self to disappear after one week? [Y/n]").to_ascii_lowercase() == "n" {
        println!("Left as it is.");
        return;
    }
    let seconds = 7 * 24 * 3600;
    match run(&setup::signal_cli_command(arch, number, &["updateContact", number, "--expiration", &seconds.to_string()]), None) {
        Ok(_) => println!("✅ Note to Self now disappears after one week."),
        Err(output) => eprintln!("⚠️  Could not set it ({output}); set it on the phone: Note to Self → the name at the top → Disappearing messages."),
    }
}

/// The bridge's settings, read through its own account (the state folder is 0700).
fn bridge_account_of_setup() -> Option<(String, bool)> {
    let output = run(&setup::as_bridge(&[unit::BRIDGE_PATH.to_string(), "account".to_string(), "--state".to_string(), unit::STATE_DIR.to_string()]), None).ok()?;
    let mut words = output.split_whitespace();
    let account = words.next()?.to_string();
    let linked = words.next() == Some("linked");
    setup::valid_number(&account).then_some((account, linked))
}

fn run_remove(args: &[String]) -> i32 {
    let arch = std::env::consts::ARCH;
    // A dry run starts nothing as root: the number is looked up only for a real removal.
    let dry_run = has(args, "--dry-run");
    let found = if dry_run { None } else { bridge_account_of_setup() };
    // Unknown (a dry run, or no state): treated as linked, so `unregister` is never run on a guess.
    let linked = found.as_ref().is_none_or(|(_, linked)| *linked);
    let plan = setup::remove_plan(arch, found.as_ref().map(|(account, _)| account.as_str()), linked);
    println!("This removes Mightling over Signal from this machine. Your Ask threads stay. It runs:\n");
    for step in &plan {
        if !step.what.is_empty() {
            println!("  • {}", step.what);
        }
        println!("      {}", step.display());
    }
    if linked {
        println!("\nLinked to your own account: on your phone, also open Signal → Settings → Linked devices and");
        println!("unlink \"Mightling\". Your account itself is never unregistered by this.");
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
        if let Err(output) = run(&command, None) {
            eprintln!("⚠️  {} failed:\n{output}", step.display());
            failed = true;
        }
    }
    if failed { 1 } else { 0 }
}

/// `trust`, as the user: stops the bridge, runs `trust-owner` as its account, starts it again.
fn run_trust() -> i32 {
    if systemctl(&["stop", unit::UNIT_NAME]) != 0 {
        return 1;
    }
    let status = Command::new("sudo")
        .args(["-u", unit::ACCOUNT, unit::BRIDGE_PATH, "trust-owner", "--state", unit::STATE_DIR])
        .status()
        .map(|s| s.code().unwrap_or(1))
        .unwrap_or(1);
    let started = systemctl(&["start", unit::UNIT_NAME]);
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
        println!("again, Signal unlinks this device, and `ling-signal setup` links it anew.");
        return 0;
    }
    let Some(owner) = config.owner.clone() else {
        eprintln!("No owner is paired yet; `ling-signal setup` pairs one.");
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
    let identities = match run(&base(&["listIdentities", "-n", &owner.aci]), None) {
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
    if let Err(output) = run(&base(&["trust", &owner.aci, "-v", &safety]), None) {
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
