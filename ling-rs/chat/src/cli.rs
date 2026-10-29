//! `ling chat …` (MIGHTLING_CHAT §7).
//!
//! - `serve`: the bridge in the foreground; what the user unit runs.
//! - `start`, `stop`: the user unit `mightling-chat.service`.
//! - `status`: which messengers are set up, who is paired, whether the unit runs.
//! - `telegram setup`: the privacy warning, a typed `yes`, and the bot token read with echo off.
//! - `telegram pair`: an 8-digit code to send the bot as `/pair <code>`, ten minutes, one use.
//! - `telegram users`, `telegram remove <id>`, `telegram off`.
//! - `discord setup`, `discord pair`, `discord users`, `discord remove <id>`, `discord off`: the
//!   same for Discord (MIGHTLING_DISCORD §4), the least private of the three.
//!
//! The Matrix homeserver is `ling-admin matrix …`'s, because containers are; it writes
//! `matrix.json`, which `serve` reads.

use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use tokio::sync::mpsc;

use crate::agent::AgentHandle;
use crate::agent::WebTarget;
use crate::hub::Hub;
use crate::hub::HubConfig;
use crate::store;
use crate::discord;
use crate::store::DiscordConfig;
use crate::store::DiscordState;
use crate::store::MatrixConfig;
use crate::store::TelegramConfig;
use crate::telegram;

pub const UNIT_NAME: &str = "mightling-chat.service";

const USAGE: &str = "Usage: ling chat start | stop | status | serve | telegram setup | telegram pair | telegram users | telegram remove <id> | telegram off | discord setup | discord pair | discord users | discord remove <id> | discord off | repos full-access on | repos full-access off";

pub const TELEGRAM_WARNING: &str = "Telegram is the less private way to reach Mightling:\n\
- everything you send the bot, and every answer, passes through Telegram's servers, unencrypted;\n\
- answers can include what the agent reads for you: mail, files, code;\n\
- Telegram keeps the chat's history until you delete it.\n\
For a private chat, use Matrix instead (`ling-admin matrix start`).";

pub const DISCORD_WARNING: &str = "Discord is the least private way to reach Mightling:\n\
- everything you send the bot, and every answer, is stored on Discord's servers, readable by Discord; Discord does not encrypt text end to end;\n\
- Discord scans messages and attachments, and may keep what you delete;\n\
- answers can include what the agent reads for you: mail, files, code;\n\
- anyone holding the bot's token can read the whole conversation, past messages included.\n\
For a private chat, use Signal (`ling signal setup`) or Matrix (`ling-admin matrix start`) instead.";

const DISCORD_PORTAL_STEPS: &str = "In Discord's Developer Portal (https://discord.com/developers/applications):\n\
1. New Application; give it a name (it is the bot's name).\n\
2. Bot: Reset Token, and copy it.\n\
3. Bot: switch Public Bot off.\n\
4. General Information: leave the Interactions Endpoint URL empty.\n\
5. Installation: keep User Install, and the install link Discord Provided.";

/// What the launcher tells `ling chat`.
#[derive(Clone, Debug)]
pub struct Environment {
    /// `ling`'s home folder (`~/.mightling`).
    pub codex_home: PathBuf,
    /// The user's home folder: the user unit and the user-level config live under it.
    pub home: PathBuf,
    /// The `ling` binary: the unit runs it, and the bridge starts `ling web` with it.
    pub ling: PathBuf,
}

impl Environment {
    pub fn dir(&self) -> PathBuf {
        store::chat_dir(&self.codex_home)
    }

    fn user_config(&self) -> PathBuf {
        self.home.join(".config/dreamference/config.toml")
    }
}

/// The user unit's text.
pub fn unit_text(ling: &Path) -> String {
    format!(
        "# Written by `ling chat start`; `ling chat stop` stops it.\n\
         [Unit]\n\
         Description=Mightling chat bridge (ling chat)\n\
         Wants=mightling-web.service\n\
         After=mightling-web.service\n\
         \n\
         [Service]\n\
         ExecStart={} chat serve\n\
         Restart=on-failure\n\
         RestartSec=5\n\
         \n\
         [Install]\n\
         WantedBy=default.target\n",
        ling.display()
    )
}

fn systemctl(args: &[&str]) -> Result<(), String> {
    let status = std::process::Command::new("systemctl")
        .arg("--user")
        .args(args)
        .status()
        .map_err(|err| format!("could not run systemctl: {err}"))?;
    if status.success() { Ok(()) } else { Err(format!("systemctl --user {} failed ({status})", args.join(" "))) }
}

/// A number from the `[chat]` table of the user's config file.
pub fn chat_setting(text: &str, key: &str) -> Option<u64> {
    let mut in_chat = false;
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or_default().trim();
        if line.starts_with('[') {
            in_chat = line == "[chat]";
            continue;
        }
        if in_chat
            && let Some((name, value)) = line.split_once('=')
            && name.trim() == key
        {
            return value.trim().parse().ok();
        }
    }
    None
}

/// A text value (quoted or not) from the `[chat]` table of the user's config file.
pub fn chat_text(text: &str, key: &str) -> Option<String> {
    let mut in_chat = false;
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or_default().trim();
        if line.starts_with('[') {
            in_chat = line == "[chat]";
            continue;
        }
        if in_chat
            && let Some((name, value)) = line.split_once('=')
            && name.trim() == key
        {
            let value = value.trim().trim_matches('"').trim();
            return (!value.is_empty()).then(|| value.to_string());
        }
    }
    None
}

/// A true/false from the `[chat]` table of the user's config file.
pub fn chat_flag(text: &str, key: &str) -> Option<bool> {
    let mut in_chat = false;
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or_default().trim();
        if line.starts_with('[') {
            in_chat = line == "[chat]";
            continue;
        }
        if in_chat
            && let Some((name, value)) = line.split_once('=')
            && name.trim() == key
        {
            return value.trim().parse().ok();
        }
    }
    None
}

/// The hub's settings: the defaults, with the user's `[chat]` values over them.
pub fn hub_config(environment: &Environment) -> HubConfig {
    let mut config = HubConfig::new(environment.dir());
    config.pair_interval = Duration::from_secs(2);
    let text = std::fs::read_to_string(environment.user_config()).unwrap_or_default();
    if let Some(seconds) = chat_setting(&text, "approval_timeout_s") {
        config.approval_timeout = Duration::from_secs(seconds.max(30));
    }
    if let Some(ms) = chat_setting(&text, "telegram_draft_interval_ms") {
        config.draft_interval = Duration::from_millis(ms.max(250));
    }
    if let Some(n) = chat_setting(&text, "max_threads_listed") {
        config.max_threads = (n as usize).clamp(1, 50);
    }
    // The settings both bridges share (MIGHTLING_CHAT §16), read again every minute.
    config.settings = ling_web_server::chat_settings::ChatSettings::from_toml(&text);
    config.settings_file = Some(environment.user_config());
    config
}

/// Whether the user-level air gap is `on`: the environment, then the user's config file, never a
/// repository's (MIGHTLING_CHAT §3).
pub fn user_airgapped(user_config: &Path) -> bool {
    let configs: Vec<(PathBuf, String)> =
        std::fs::read_to_string(user_config).ok().map(|text| vec![(user_config.to_path_buf(), text)]).unwrap_or_default();
    let environment = std::env::var(ling_airgapped::ENV_VAR).ok();
    ling_airgapped::resolve_from(None, environment.as_deref(), &configs).level == ling_airgapped::Level::On
}

pub async fn run_cli(args: &[String], environment: Environment) -> i32 {
    let dir = environment.dir();
    match args.first().map(String::as_str) {
        Some("serve") => serve(&environment).await,
        Some("start") => start(&environment),
        // Disabled as well as stopped, so it stays off across a reboot until `ling chat start`.
        Some("stop") => match systemctl(&["disable", "--now", UNIT_NAME]) {
            Ok(()) => {
                println!("Mightling chat bridge stopped; it stays off until `ling chat start`.");
                0
            }
            Err(err) => {
                eprintln!("{err}");
                1
            }
        },
        Some("status") => status(&dir),
        Some("telegram") => match args.get(1).map(String::as_str) {
            Some("setup") => telegram_setup(&dir).await,
            Some("pair") => telegram_pair(&dir),
            Some("users") => telegram_users(&dir),
            Some("remove") => match args.get(2) {
                Some(which) => telegram_remove(&dir, which),
                None => {
                    eprintln!("{USAGE}");
                    2
                }
            },
            Some("off") => telegram_off(&dir),
            _ => {
                eprintln!("{USAGE}");
                2
            }
        },
        Some("discord") => match args.get(1).map(String::as_str) {
            Some("setup") => discord_setup(&dir, &environment.user_config()).await,
            Some("pair") => discord_pair(&dir),
            Some("users") => discord_users(&dir),
            Some("remove") => match args.get(2) {
                Some(which) => discord_remove(&dir, which),
                None => {
                    eprintln!("{USAGE}");
                    2
                }
            },
            Some("off") => discord_off(&dir).await,
            _ => {
                eprintln!("{USAGE}");
                2
            }
        },
        _ => {
            eprintln!("{USAGE}");
            2
        }
    }
}

async fn serve(environment: &Environment) -> i32 {
    let dir = environment.dir();
    let telegram_config = TelegramConfig::load(&dir);
    let matrix_config = MatrixConfig::load(&dir);
    let discord_config = DiscordConfig::load(&dir);
    if telegram_config.is_none() && matrix_config.is_none() && discord_config.is_none() {
        eprintln!("No messenger is set up: run `ling chat telegram setup`, `ling chat discord setup`, or `ling-admin matrix start`.");
        return 1;
    }
    let (agent, commands) = AgentHandle::channel();
    let (events_tx, events) = mpsc::unbounded_channel();
    let (inbound_tx, inbound) = mpsc::unbounded_channel();
    let user_config = environment.user_config();
    let mut hub = Hub::new(hub_config(environment), agent, Box::new(move || user_airgapped(&user_config)));
    let mut adapters = 0;
    if let Some(config) = telegram_config {
        let (out_tx, out_rx) = mpsc::unbounded_channel();
        let api = telegram::TelegramApi::new(telegram::API_ROOT, &config.token);
        let text = std::fs::read_to_string(environment.user_config()).unwrap_or_default();
        let defaults = telegram::Options::default();
        let options = telegram::Options {
            topics: chat_flag(&text, "telegram_topics").unwrap_or(defaults.topics),
            rich: chat_flag(&text, "telegram_rich").unwrap_or(defaults.rich),
            mini_app_url: ling_web_server::telegram_app::mini_app_url(&text),
        };
        if options.mini_app_url.is_none() && text.contains("telegram_mini_app_url") {
            eprintln!("ling chat: Telegram: `telegram_mini_app_url` is not a plain https:// address; no app is offered.");
        }
        match telegram::start(api, dir.clone(), inbound_tx.clone(), out_rx, options).await {
            Ok(username) => eprintln!("ling chat: Telegram: answering as @{username}"),
            Err(err) if err.description.contains("Unauthorized") => {
                eprintln!("ling chat: Telegram refused the bot token ({err}); run `ling chat telegram setup` again.")
            }
            Err(err) => eprintln!("ling chat: Telegram is not answering yet ({err}); retrying."),
        }
        hub.add_adapter("telegram", out_tx);
        adapters += 1;
    }
    if let Some(config) = matrix_config {
        let (out_tx, out_rx) = mpsc::unbounded_channel();
        match crate::matrix::start(&config, dir.clone(), inbound_tx.clone(), out_rx).await {
            Ok(()) => {
                hub.add_adapter("matrix", out_tx);
                adapters += 1;
                eprintln!("ling chat: Matrix: answering as {}", config.user_id);
            }
            Err(err) => eprintln!("ling chat: the Matrix homeserver is not answering ({err}); is `ling-admin matrix start` done?"),
        }
    }
    if let Some(config) = discord_config {
        let (out_tx, out_rx) = mpsc::unbounded_channel();
        let api = discord::DiscordApi::new(discord::API_ROOT, &config.token);
        let text = std::fs::read_to_string(environment.user_config()).unwrap_or_default();
        let options = discord_options(&text);
        if options.front_url.is_none() && text.contains("discord_web_url") {
            eprintln!("ling chat: Discord: `discord_web_url` is not a plain https:// address; no links are offered.");
        }
        match discord::start(api, dir.clone(), inbound_tx.clone(), out_rx, options).await {
            Ok(username) => {
                hub.add_adapter("discord", out_tx);
                adapters += 1;
                eprintln!("ling chat: Discord: answering as {username}");
            }
            Err(err) if err.status == 401 => eprintln!("ling chat: Discord refused the bot token ({err}); run `ling chat discord setup` again."),
            Err(err) => eprintln!("ling chat: Discord is not answering ({err}); `ling chat start` tries again."),
        }
    }
    if adapters == 0 {
        return 1;
    }
    let target = WebTarget { state: ling_web_server::auth::state_dir(&environment.codex_home), ling: Some(environment.ling.clone()) };
    tokio::spawn(crate::agent::run_link(target, commands, events_tx));
    drop(inbound_tx);
    tokio::select! {
        () = hub.run(inbound, events) => {}
        _ = tokio::signal::ctrl_c() => {}
    }
    0
}

fn start(environment: &Environment) -> i32 {
    let dir = environment.dir();
    if TelegramConfig::load(&dir).is_none() && MatrixConfig::load(&dir).is_none() && DiscordConfig::load(&dir).is_none() {
        eprintln!(
            "No messenger is set up yet: run `ling-admin matrix start` (private), `ling chat telegram setup` (less private) or `ling chat discord setup` (least private) first."
        );
        return 1;
    }
    let unit = environment.home.join(".config/systemd/user").join(UNIT_NAME);
    let written = unit.parent().map_or(Ok(()), std::fs::create_dir_all).and_then(|()| std::fs::write(&unit, unit_text(&environment.ling)));
    if let Err(err) = written {
        eprintln!("Could not write {}: {err}", unit.display());
        return 1;
    }
    if let Err(err) = systemctl(&["daemon-reload"]).and_then(|()| systemctl(&["enable", UNIT_NAME])).and_then(|()| systemctl(&["restart", UNIT_NAME])) {
        eprintln!("{err}");
        return 1;
    }
    println!("Mightling chat bridge started. `ling chat status` shows what it answers on.");
    if ling_web_server::ocr::program().is_none() {
        println!("💡 To read the text in the images you send, install Tesseract (offline): sudo apt install tesseract-ocr");
    }
    0
}

fn status(dir: &Path) -> i32 {
    let running = std::process::Command::new("systemctl")
        .args(["--user", "is-active", "--quiet", UNIT_NAME])
        .status()
        .is_ok_and(|status| status.success());
    println!("Bridge: {}", if running { "running" } else { "not running (`ling chat start`)" });
    match TelegramConfig::load(dir) {
        Some(config) => println!(
            "Telegram: on (less private), {} paired user(s){}",
            config.users.len(),
            if config.users.is_empty() { "; pair one with `ling chat telegram pair`" } else { "" }
        ),
        None => println!("Telegram: off"),
    }
    match MatrixConfig::load(dir) {
        Some(config) => println!("Matrix: on (private), as {}, {} allowed user(s)", config.user_id, config.allowed.len()),
        None => println!("Matrix: off (`ling-admin matrix start`)"),
    }
    match DiscordConfig::load(dir) {
        Some(config) => println!(
            "Discord: on (least private), {} paired user(s), {} connection(s) in the last 24 hours{}",
            config.users.len(),
            DiscordState::load(dir).identifies_today(store::now_secs()),
            if config.users.is_empty() { "; pair one with `ling chat discord pair`" } else { "" }
        ),
        None => println!("Discord: off"),
    }
    0
}

/// The Discord adapter's settings from the user's config file (DISCORD §5).
pub fn discord_options(text: &str) -> discord::Options {
    let defaults = discord::Options::default();
    discord::Options {
        front_url: ling_web_server::telegram_app::discord_web_url(text),
        status_interval: chat_setting(text, "discord_status_interval_s").map_or(defaults.status_interval, |s| Duration::from_secs(s.max(5))),
        identify_limit: chat_setting(text, "discord_identify_limit").map_or(defaults.identify_limit, |n| n.clamp(1, 900) as usize),
        home_server: chat_text(text, "discord_home_server").filter(|id| discord::is_snowflake(id)),
    }
}

#[cfg(unix)]
fn stdin_is_terminal() -> bool {
    unsafe { libc::isatty(0) == 1 }
}

#[cfg(not(unix))]
fn stdin_is_terminal() -> bool {
    false
}

/// A line from the terminal, with echo off when `secret`.
fn read_line(prompt: &str, secret: bool) -> Option<String> {
    use std::io::Write;
    print!("{prompt}");
    let _ = std::io::stdout().flush();
    #[cfg(unix)]
    let saved = if secret {
        unsafe {
            let mut term: libc::termios = std::mem::zeroed();
            if libc::tcgetattr(0, &mut term) == 0 {
                let saved = term;
                term.c_lflag &= !libc::ECHO;
                libc::tcsetattr(0, libc::TCSANOW, &term);
                Some(saved)
            } else {
                None
            }
        }
    } else {
        None
    };
    let mut line = String::new();
    let read = std::io::stdin().read_line(&mut line);
    #[cfg(unix)]
    if let Some(saved) = saved {
        unsafe {
            libc::tcsetattr(0, libc::TCSANOW, &saved);
        }
        println!();
    }
    read.ok().filter(|n| *n > 0).map(|_| line.trim().to_string())
}

async fn telegram_setup(dir: &Path) -> i32 {
    if !stdin_is_terminal() {
        eprintln!("`ling chat telegram setup` asks for your consent and the bot token at a terminal; run it in one.");
        return 1;
    }
    println!("{TELEGRAM_WARNING}\n");
    if read_line("Type yes to turn Telegram on: ", false).as_deref() != Some("yes") {
        println!("Telegram stays off.");
        return 1;
    }
    println!("\nCreate a bot with @BotFather in Telegram (/newbot) and paste its token here. It is not shown as you type.");
    let Some(token) = read_line("Bot token: ", true).filter(|token| token.contains(':')) else {
        eprintln!("That does not look like a bot token (it has the form 123456:ABC…).");
        return 1;
    };
    let api = telegram::TelegramApi::new(telegram::API_ROOT, &token);
    let me = match api.call("getMe", serde_json::json!({})).await {
        Ok(me) => me,
        Err(err) => {
            eprintln!("Telegram refused that token: {err}");
            return 1;
        }
    };
    // A new token (after /revoke) keeps the paired users.
    let mut config = TelegramConfig::new(token);
    if let Some(previous) = TelegramConfig::load(dir) {
        config.users = previous.users;
    }
    if let Err(err) = config.save(dir) {
        eprintln!("Could not save the token: {err}");
        return 1;
    }
    let username = me.get("username").and_then(serde_json::Value::as_str).unwrap_or_default();
    println!("Telegram is on, as @{username}. Next: `ling chat telegram pair`, then `ling chat start`.");
    0
}

fn telegram_pair(dir: &Path) -> i32 {
    if TelegramConfig::load(dir).is_none() {
        eprintln!("Telegram is off: run `ling chat telegram setup` first.");
        return 1;
    }
    match store::issue_pairing_code(dir) {
        Ok(code) => {
            println!("Send this to your bot in Telegram within 10 minutes:\n\n    /pair {code}\n\nThe code works once.");
            0
        }
        Err(err) => {
            eprintln!("Could not write a pairing code: {err}");
            1
        }
    }
}

fn telegram_users(dir: &Path) -> i32 {
    match TelegramConfig::load(dir) {
        Some(config) if !config.users.is_empty() => {
            for user in config.users {
                println!("{user}");
            }
            0
        }
        Some(_) => {
            println!("No paired users: `ling chat telegram pair`.");
            0
        }
        None => {
            println!("Telegram is off.");
            0
        }
    }
}

fn telegram_remove(dir: &Path, which: &str) -> i32 {
    let Some(mut config) = TelegramConfig::load(dir) else {
        eprintln!("Telegram is off.");
        return 1;
    };
    let Ok(user) = which.parse::<i64>() else {
        eprintln!("A Telegram user id is a number, as `ling chat telegram users` lists it.");
        return 2;
    };
    let before = config.users.len();
    config.users.retain(|u| *u != user);
    if config.users.len() == before {
        eprintln!("{user} is not paired.");
        return 1;
    }
    match config.save(dir) {
        Ok(()) => {
            println!("{user} can no longer use the bot.");
            0
        }
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

fn telegram_off(dir: &Path) -> i32 {
    let _ = std::fs::remove_file(TelegramConfig::path(dir));
    store::withdraw_pairing_codes(dir);
    println!(
        "Telegram is off: the token and the pairings are deleted. Revoke the token in @BotFather (/revoke) too.\n\
         A running bridge stops reading Telegram at once; restart it (`ling chat start`) to drop the adapter entirely."
    );
    0
}

async fn discord_setup(dir: &Path, user_config: &Path) -> i32 {
    if !stdin_is_terminal() {
        eprintln!("`ling chat discord setup` asks for your consent and the bot token at a terminal; run it in one.");
        return 1;
    }
    println!("{DISCORD_WARNING}\n");
    if read_line("Type yes to turn Discord on: ", false).as_deref() != Some("yes") {
        println!("Discord stays off.");
        return 1;
    }
    println!("\n{DISCORD_PORTAL_STEPS}\n\nPaste the bot token here. It is not shown as you type.");
    let Some(token) = read_line("Bot token: ", true).filter(|token| token.len() > 20 && !token.contains(char::is_whitespace)) else {
        eprintln!("That does not look like a bot token.");
        return 1;
    };
    let api = discord::DiscordApi::new(discord::API_ROOT, &token);
    let me = match api.request(reqwest::Method::GET, "/users/@me", None).await {
        Ok(me) => me,
        Err(err) => {
            eprintln!("Discord refused that token: {err}");
            return 1;
        }
    };
    let app = match api.request(reqwest::Method::GET, "/applications/@me", None).await {
        Ok(app) => app,
        Err(err) => {
            eprintln!("Could not read the bot's application: {err}");
            return 1;
        }
    };
    match discord::check_application(&app) {
        Ok(warnings) => warnings.iter().for_each(|warning| println!("⚠️  {warning}")),
        Err(refusal) => {
            eprintln!("❌ Not turned on: {refusal}.");
            return 1;
        }
    }
    let Some(application_id) = app.get("id").and_then(serde_json::Value::as_str).filter(|id| discord::is_snowflake(id)) else {
        eprintln!("Discord named no application id.");
        return 1;
    };
    // A new token (after a reset in the portal) keeps the paired users of the same application.
    let mut config = DiscordConfig::new(token, application_id.to_string());
    if let Some(previous) = DiscordConfig::load(dir).filter(|previous| previous.application_id == application_id) {
        config.users = previous.users;
    }
    if let Err(err) = config.save(dir) {
        eprintln!("Could not save the token: {err}");
        return 1;
    }
    let front = std::fs::read_to_string(user_config).ok().and_then(|text| ling_web_server::telegram_app::discord_web_url(&text)).is_some();
    if let Err(err) = api.request(reqwest::Method::PUT, &format!("/applications/{application_id}/commands"), Some(discord::commands(front))).await {
        eprintln!("⚠️  The commands were not registered yet ({err}); the bridge registers them when it starts.");
    }
    let username = me.get("username").and_then(serde_json::Value::as_str).unwrap_or_default();
    println!(
        "Discord is on, as {username}. Add the bot to your own account with this link (Add to My Apps):\n\n    {}\n\nNext: `ling chat discord pair`, then `ling chat start`.",
        discord::install_url(application_id)
    );
    0
}

fn discord_pair(dir: &Path) -> i32 {
    if DiscordConfig::load(dir).is_none() {
        eprintln!("Discord is off: run `ling chat discord setup` first.");
        return 1;
    }
    match store::issue_discord_pairing_code(dir) {
        Ok(code) => {
            println!("Send this to your bot in a direct message in Discord within 10 minutes:\n\n    /pair {code}\n\nThe code works once.");
            0
        }
        Err(err) => {
            eprintln!("Could not write a pairing code: {err}");
            1
        }
    }
}

fn discord_users(dir: &Path) -> i32 {
    match DiscordConfig::load(dir) {
        Some(config) if !config.users.is_empty() => {
            for user in config.users.keys() {
                println!("{user}");
            }
            0
        }
        Some(_) => {
            println!("No paired users: `ling chat discord pair`.");
            0
        }
        None => {
            println!("Discord is off.");
            0
        }
    }
}

fn discord_remove(dir: &Path, which: &str) -> i32 {
    let Some(mut config) = DiscordConfig::load(dir) else {
        eprintln!("Discord is off.");
        return 1;
    };
    if !discord::is_snowflake(which) {
        eprintln!("A Discord user id is a number, as `ling chat discord users` lists it.");
        return 2;
    }
    if config.users.remove(which).is_none() {
        eprintln!("{which} is not paired.");
        return 1;
    }
    match config.save(dir) {
        Ok(()) => {
            println!("{which} can no longer use the bot.");
            0
        }
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

async fn discord_off(dir: &Path) -> i32 {
    if let Some(config) = DiscordConfig::load(dir).filter(|config| discord::is_snowflake(&config.application_id)) {
        let api = discord::DiscordApi::new(discord::API_ROOT, &config.token);
        let path = format!("/applications/{}/commands", config.application_id);
        if let Err(err) = api.request(reqwest::Method::PUT, &path, Some(serde_json::json!([]))).await {
            eprintln!("⚠️  The bot's commands were not removed ({err}); deleting the application removes them.");
        }
    }
    let _ = std::fs::remove_file(DiscordConfig::path(dir));
    let _ = std::fs::remove_file(DiscordState::path(dir));
    store::withdraw_discord_pairing_codes(dir);
    println!(
        "Discord is off: the token and the pairings are deleted. Delete the application in Discord's Developer Portal too, so the token is gone there.\n\
         A running bridge answers no one on Discord from now on, and does not reconnect to it."
    );
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_unit_runs_the_bridge_after_the_web_server() {
        let text = unit_text(Path::new("/home/u/.local/bin/ling"));
        assert!(text.contains("ExecStart=/home/u/.local/bin/ling chat serve\n"));
        assert!(text.contains("After=mightling-web.service\n"));
    }

    #[test]
    fn chat_settings_come_from_the_chat_table_only() {
        let text = "approval_timeout_s = 5\n[chat]\napproval_timeout_s = 120 # two minutes\n[other]\nmax_threads_listed = 3\n";
        assert_eq!(chat_setting(text, "approval_timeout_s"), Some(120));
        assert_eq!(chat_setting(text, "max_threads_listed"), None);
    }

    #[test]
    fn discords_settings_are_bounded_and_checked() {
        let text = "[chat]\ndiscord_web_url = \"https://dc.example.ts.net/\"\ndiscord_status_interval_s = 1\ndiscord_identify_limit = 5000\ndiscord_home_server = \"123456789012345678\"\n";
        let options = discord_options(text);
        assert_eq!(options.front_url.as_deref(), Some("https://dc.example.ts.net/"));
        assert_eq!(options.status_interval, Duration::from_secs(5));
        assert_eq!(options.identify_limit, 900);
        assert_eq!(options.home_server.as_deref(), Some("123456789012345678"));
        let defaults = discord_options("[chat]\ndiscord_web_url = \"http://dc.example.ts.net/\"\ndiscord_home_server = \"my server\"\n");
        assert_eq!(defaults.front_url, None);
        assert_eq!(defaults.home_server, None);
        assert_eq!(defaults.identify_limit, 100);
        assert!(DISCORD_WARNING.contains("least private") && DISCORD_WARNING.contains("token"));
    }

    #[test]
    fn the_air_gap_is_read_from_the_user_level_file() {
        let dir = std::env::temp_dir().join(format!("ling-chat-airgap-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("config.toml");
        std::fs::write(&file, "mightling_airgapped = \"on\"\n").unwrap();
        if std::env::var(ling_airgapped::ENV_VAR).is_err() {
            assert!(user_airgapped(&file));
            std::fs::write(&file, "mightling_airgapped = \"off\"\n").unwrap();
            assert!(!user_airgapped(&file));
            assert!(!user_airgapped(&dir.join("missing.toml")));
        }
    }
}
