//! `ling-signal` (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §6).
//!
//! As the user:
//! - `setup --number +… [--voice] [--port N] [--dry-run]`: install, register, pair, start (§3, §4.1)
//! - `status`: what the running bridge reports (`/run/mightling-signal/status.json`)
//! - `start`, `stop`: the system unit
//! - `unit`: print the unit file
//!
//! As the bridge's own account (setup runs these through `sudo -u mightling-signal`):
//! - `serve --state DIR [--runtime DIR]`: the daemon, what the unit runs
//! - `init --state DIR --account +… --node NAME --port N --signal-cli PATH --version V [--env K=V]… [--vision]`
//! - `pair --state DIR --port N --code CODE`: trade a `ling web pair` code for the device cookie
//! - `bind --state DIR`: a fresh six-digit code that makes its first sender the owner

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

const USAGE: &str = "Usage: ling-signal setup --number +NUMBER [--voice] [--port N] [--dry-run] | status | start | stop | unit\n       ling-signal serve --state DIR [--runtime DIR]   (what the system unit runs)";

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
        Err(err) => {
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
    let config = Config {
        mode: Mode::Dedicated,
        account,
        own_uuid: previous.as_ref().and_then(|p| p.own_uuid.clone()),
        owner: previous.as_ref().and_then(|p| p.owner.clone()),
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
    println!("  strangers ignored: {}", status.get("strangersIgnored").and_then(serde_json::Value::as_u64).unwrap_or(0));
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
    let Some(number) = flag(args, "--number") else {
        eprintln!("setup needs --number +NUMBER: the dedicated number Mightling will answer on (specs §4.1).");
        return 2;
    };
    if !setup::valid_number(&number) {
        eprintln!("{number} is not a number in international form, e.g. +15551234567.");
        return 2;
    }
    let port: u16 = flag(args, "--port").and_then(|p| p.parse().ok()).unwrap_or(3100);
    let arch = std::env::consts::ARCH;
    let bridge = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("ling-signal"));
    let work = std::env::temp_dir().join(format!("ling-signal-setup-{}", std::process::id()));
    let account_exists = Command::new("id").arg(unit::ACCOUNT).stdout(Stdio::null()).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false);
    let java_present = Path::new(&setup::signal_cli_env(arch)[0].1).exists();
    let plan = setup::install_plan(&bridge, &work, arch, account_exists, java_present);

    println!("Mightling over Signal: setup for {number}\n");
    println!("This installs signal-cli and a small bridge that runs as its own system account,");
    println!("`{}`, so the agent can never read the Signal keys. It changes these things:\n", unit::ACCOUNT);
    for step in &plan {
        if !step.what.is_empty() {
            println!("  • {}", step.what);
        }
        println!("      {}", step.display());
    }
    println!("\nThen it registers {number} with Signal (an SMS or voice code), pairs the bridge with `ling web`,");
    println!("and starts `{}`. The bridge connects to Signal's servers only; it runs only while", unit::UNIT_NAME);
    println!("the air gap is off.");
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

    // Register the number: a CAPTCHA first if Signal wants one, then the SMS or voice code.
    let mut register: Vec<&str> = vec!["register"];
    if has(args, "--voice") {
        register.push("--voice");
    }
    let mut result = run(&setup::signal_cli_command(arch, &number, &register), None);
    if let Err(output) = &result
        && output.to_ascii_lowercase().contains("captcha")
    {
        println!("\nSignal asks for a CAPTCHA. Open https://signalcaptchas.org/registration/generate.html,");
        println!("solve it, then right-click \"Open Signal\" and copy the link (it starts with signalcaptcha://).");
        let Some(token) = setup::captcha_token(&prompt("Paste the link:")) else {
            eprintln!("That is not a signalcaptcha:// link.");
            return 1;
        };
        let mut again = register.clone();
        again.extend(["--captcha", token.as_str()]);
        result = run(&setup::signal_cli_command(arch, &number, &again), None);
    }
    if let Err(output) = result {
        eprintln!("❌ Signal refused the registration:\n{output}");
        return 1;
    }
    let code = prompt(&format!("Signal sent a code to {number}. Type it:"));
    if let Err(output) = run(&setup::signal_cli_command(arch, &number, &["verify", code.trim()]), None) {
        eprintln!("❌ The code was not accepted:\n{output}");
        return 1;
    }
    // Lock the account against re-registration, name it, hide it from number discovery.
    let pin = format!("{:08}", rand::random_range(0..100_000_000u32));
    for (what, args) in [
        ("registration lock", vec!["setPin", pin.as_str()]),
        ("profile name", vec!["updateProfile", "--given-name", "Mightling"]),
        ("number discovery off", vec!["updateAccount", "--discoverable-by-number", "false"]),
    ] {
        if let Err(output) = run(&setup::signal_cli_command(arch, &number, &args), None) {
            eprintln!("⚠️  Could not set the {what}: {output}");
        }
    }
    let pin_path = format!("{}/pin", unit::STATE_DIR);
    let _ = run(&setup::as_bridge(&["sh".to_string(), "-c".to_string(), format!("umask 077; cat > {pin_path}")]), Some(&pin));

    // Pair the bridge with `ling web` as a device, then write its settings and an owner code.
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
    let node = run(&["hostname".to_string()], None).unwrap_or_default().trim().to_string();
    let mut init = vec![
        bridge_bin.clone(),
        "init".into(),
        "--state".into(),
        state_dir.clone(),
        "--account".into(),
        number.clone(),
        "--node".into(),
        node,
        "--port".into(),
        port.to_string(),
        "--signal-cli".into(),
        setup::signal_cli_program().to_string_lossy().into_owned(),
        "--version".into(),
        setup::SIGNAL_CLI_VERSION.into(),
    ];
    for (key, value) in setup::signal_cli_env(arch) {
        init.push("--env".into());
        init.push(format!("{key}={value}"));
    }
    if let Err(output) = run(&setup::as_bridge(&init), None) {
        eprintln!("❌ {output}");
        return 1;
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
    println!("\n📱 From your phone, send this code to {number} in Signal within ten minutes:\n\n      {owner_code}\n");
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
