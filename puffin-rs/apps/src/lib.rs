//! Puffin's own apps: Gmail, Google Drive and Google Calendar, read through the local Google
//! service on `127.0.0.1:8767` (specs/DREAMFERENCE_PUFFIN_APPS.md).
//!
//! Two callers, two halves:
//! - **Codex's `/apps`** (patch 0022): [`offered`] opens the command's gate without a ChatGPT
//!   sign-in, and [`list`] answers the app server's `app/list` with Puffin's rows before any
//!   request to `chatgpt.com` is made (§4).
//! - **The tools** ([`mcp`]): `puffin apps serve <app>` is an MCP server over stdio offering
//!   read-only tools for one app (§6).
//!
//! The service's token carries write permission for Drive and Calendar, because GNOME's OAuth
//! client is allowed only the full scopes (§11.1 item 5); nothing here or in the service writes.

pub mod http;
pub mod mcp;

use serde_json::{Value, json};
use std::path::PathBuf;

/// The Google service, published on loopback only.
pub const SERVICE_ADDR: &str = "127.0.0.1:8767";

/// Where the browser connects an account. `localhost`, not `127.0.0.1`: Google's redirect after
/// consent is registered for `http://localhost:8767/`, and the page and the redirect must agree.
pub const CONNECT_URL: &str = "http://localhost:8767/connect";

/// Switches Puffin's apps off entirely: no `/apps` rows, no tools (`0`, `false`, `no`, `off`).
pub const ENV_SWITCH: &str = "DREAMFERENCE_PUFFIN_APPS";

/// The command that starts the service when `/status` does not answer.
pub const START_HINT: &str = "puffin-admin google start";

/// One of Puffin's apps.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum App {
    Gmail,
    Drive,
    Calendar,
}

impl App {
    /// Every app, in the order `/apps` lists them.
    pub const ALL: [App; 3] = [App::Gmail, App::Drive, App::Calendar];

    /// The short name used on command lines and in the connect page's `app=` parameter.
    pub fn key(self) -> &'static str {
        match self {
            App::Gmail => "gmail",
            App::Drive => "drive",
            App::Calendar => "calendar",
        }
    }

    /// The app id Codex records, in `[apps.<id>] enabled` among other places.
    pub fn id(self) -> &'static str {
        match self {
            App::Gmail => "puffin_gmail",
            App::Drive => "puffin_drive",
            App::Calendar => "puffin_calendar",
        }
    }

    /// The name `/apps` shows.
    pub fn name(self) -> &'static str {
        match self {
            App::Gmail => "Gmail",
            App::Drive => "Google Drive",
            App::Calendar => "Google Calendar",
        }
    }

    /// The OAuth scope an account must hold for the app to be connected. Full scopes: GNOME's
    /// client is refused the read-only ones.
    pub fn scope(self) -> &'static str {
        match self {
            App::Gmail => "https://mail.google.com/",
            App::Drive => "https://www.googleapis.com/auth/drive",
            App::Calendar => "https://www.googleapis.com/auth/calendar",
        }
    }

    /// What `/apps` says of an app nobody has connected.
    pub fn blurb(self) -> &'static str {
        match self {
            App::Gmail => "Read-only search of your mail, on this machine",
            App::Drive => "Read-only search of your files, on this machine",
            App::Calendar => "Read-only view of your calendars, on this machine",
        }
    }

    /// The environment variable that switches one app off for a session (§7).
    pub fn env_var(self) -> &'static str {
        match self {
            App::Gmail => "DREAMFERENCE_PUFFIN_GMAIL",
            App::Drive => "DREAMFERENCE_PUFFIN_DRIVE",
            App::Calendar => "DREAMFERENCE_PUFFIN_CALENDAR",
        }
    }

    /// The app a key or id names.
    pub fn parse(name: &str) -> Option<App> {
        App::ALL.into_iter().find(|app| app.key() == name || app.id() == name)
    }

    /// Where `/apps` sends the browser to connect an account.
    pub fn install_url(self) -> String {
        format!("{CONNECT_URL}?app={}", self.key())
    }
}

/// A connected Google account, as the service's `/status` reports it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Account {
    pub email: String,
    pub scopes: Vec<String>,
}

/// What the service said, or that it did not answer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Service {
    Up(Vec<Account>),
    Down,
}

/// The accounts in a `/status` answer.
///
/// A service that reports `accounts` (with each grant's scopes) is read as such. One that predates
/// them reports only `email`, comma-separated, and knew nothing but Gmail, so its accounts are
/// read as holding the Gmail scope alone.
pub fn parse_status(body: &Value) -> Vec<Account> {
    if let Some(accounts) = body.get("accounts").and_then(Value::as_array) {
        return accounts
            .iter()
            .filter_map(|account| {
                let email = account.get("email")?.as_str()?.trim().to_string();
                let scopes = account
                    .get("scopes")
                    .and_then(Value::as_array)
                    .map(|scopes| scopes.iter().filter_map(Value::as_str).map(str::to_string).collect())
                    .unwrap_or_default();
                (!email.is_empty()).then_some(Account { email, scopes })
            })
            .collect();
    }
    if body.get("connected").and_then(Value::as_bool) != Some(true) {
        return Vec::new();
    }
    body.get("email")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|email| !email.is_empty())
        .map(|email| Account { email: email.to_string(), scopes: vec![App::Gmail.scope().to_string()] })
        .collect()
}

/// The addresses whose grant covers `app`.
pub fn holders(accounts: &[Account], app: App) -> Vec<String> {
    accounts
        .iter()
        .filter(|account| account.scopes.iter().any(|scope| scope == app.scope()))
        .map(|account| account.email.clone())
        .collect()
}

/// The `/apps` rows, as the connectors crate's `AppInfo` serialises (camelCase; a missing
/// `isEnabled` defaults to true, and Codex applies `[apps.<id>] enabled` itself).
pub fn rows(service: &Service, air_gapped: bool) -> Value {
    let rows: Vec<Value> = App::ALL
        .into_iter()
        .map(|app| {
            let (description, install_url, accessible) = if air_gapped {
                ("Unavailable at /airgapped on".to_string(), None, false)
            } else {
                match service {
                    Service::Down => (format!("Not running: `{START_HINT}`"), None, false),
                    Service::Up(accounts) => {
                        let connected = holders(accounts, app);
                        if connected.is_empty() {
                            (app.blurb().to_string(), Some(app.install_url()), false)
                        } else {
                            (connected.join(", "), Some(app.install_url()), true)
                        }
                    }
                }
            };
            json!({
                "id": app.id(),
                "name": app.name(),
                "description": description,
                "installUrl": install_url,
                "isAccessible": accessible,
                "distributionChannel": "puffin",
            })
        })
        .collect();
    Value::Array(rows)
}

/// Whether a setting's value means "off".
pub fn is_off(value: &str) -> bool {
    matches!(value.trim().to_lowercase().as_str(), "0" | "false" | "no" | "off")
}

/// Whether this machine is a Puffin node: the Google service and its token live on the node, so a
/// client offers no apps (§3).
pub fn is_node() -> bool {
    node_id_file().is_some_and(|path| path.is_file())
}

fn node_id_file() -> Option<PathBuf> {
    Some(puffin_node_locator::home_dir()?.join(".config/dreamference/node-id"))
}

/// Whether Puffin offers apps here. Cheap and without network: the TUI asks it on every gate check.
pub fn offered() -> bool {
    if std::env::var(ENV_SWITCH).is_ok_and(|value| is_off(&value)) {
        return false;
    }
    is_node()
}

/// Whether a seal written by process `pid` holds a session at `on`. A seal's content is its
/// writer's pid (the launcher's airgapped module), so the process that runs a session, and an MCP
/// server whose parent is that process, can find it without knowing the thread id.
pub fn sealed_by(pid: u32) -> bool {
    let Some(dir) = puffin_airgapped::seal_dir() else { return false };
    let Ok(entries) = std::fs::read_dir(dir) else { return false };
    let own = pid.to_string();
    entries
        .flatten()
        .any(|entry| std::fs::read_to_string(entry.path()).is_ok_and(|text| text.trim() == own))
}

/// Whether any session holds a seal: what a caller that cannot name its session asks.
pub fn any_sealed() -> bool {
    let Some(dir) = puffin_airgapped::seal_dir() else { return false };
    std::fs::read_dir(dir).is_ok_and(|mut entries| entries.next().is_some())
}

/// Whether the air gap is `on` for the session process `pid`: the configured level, or a seal it
/// wrote.
pub fn air_gapped_for(pid: u32) -> bool {
    puffin_airgapped::resolve(&[]).level == puffin_airgapped::Level::On || sealed_by(pid)
}

/// Asks the service which accounts are connected. Unauthenticated: `/status` shows addresses and
/// scope names, never mail.
pub fn service_status() -> Service {
    match http::get(SERVICE_ADDR, "/status", None, std::time::Duration::from_secs(2)) {
        Ok((200, body)) => match serde_json::from_str::<Value>(&body) {
            Ok(value) => Service::Up(parse_status(&value)),
            Err(_) => Service::Down,
        },
        _ => Service::Down,
    }
}

/// Puffin's answer to `app/list`, or `None` when Puffin offers no apps here, so Codex's own path
/// runs (which, with no ChatGPT sign-in, answers with an empty list).
pub fn list() -> Option<Value> {
    if !offered() {
        return None;
    }
    let air_gapped = air_gapped_for(std::process::id());
    let service = if air_gapped { Service::Up(Vec::new()) } else { service_status() };
    Some(rows(&service, air_gapped))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn account(email: &str, scopes: &[App]) -> Account {
        Account { email: email.into(), scopes: scopes.iter().map(|app| app.scope().to_string()).collect() }
    }

    #[test]
    fn apps_parse_by_key_and_id() {
        assert_eq!(App::parse("drive"), Some(App::Drive));
        assert_eq!(App::parse("puffin_calendar"), Some(App::Calendar));
        assert_eq!(App::parse("slack"), None);
        assert_eq!(App::Gmail.install_url(), "http://localhost:8767/connect?app=gmail");
    }

    #[test]
    fn status_with_scopes_is_read_per_account() {
        let body = json!({"accounts": [
            {"email": "a@x.com", "scopes": [App::Gmail.scope(), App::Drive.scope()]},
            {"email": "b@y.com", "scopes": [App::Calendar.scope()]},
            {"email": "", "scopes": []},
        ]});
        let accounts = parse_status(&body);
        assert_eq!(accounts.len(), 2);
        assert_eq!(holders(&accounts, App::Drive), vec!["a@x.com"]);
        assert_eq!(holders(&accounts, App::Calendar), vec!["b@y.com"]);
    }

    #[test]
    fn a_service_that_predates_scopes_means_gmail_only() {
        let body = json!({"connected": true, "email": "a@x.com, b@y.com"});
        let accounts = parse_status(&body);
        assert_eq!(holders(&accounts, App::Gmail), vec!["a@x.com", "b@y.com"]);
        assert!(holders(&accounts, App::Drive).is_empty());
        assert!(parse_status(&json!({"connected": false, "email": null})).is_empty());
        assert!(parse_status(&json!({"error": "unauthorised"})).is_empty());
    }

    #[test]
    fn rows_are_camel_case_and_say_what_is_connected() {
        let service = Service::Up(vec![account("a@x.com", &[App::Gmail])]);
        let rows = rows(&service, false);
        let gmail = &rows[0];
        assert_eq!(gmail["id"], "puffin_gmail");
        assert_eq!(gmail["isAccessible"], true);
        assert_eq!(gmail["description"], "a@x.com");
        assert_eq!(gmail["installUrl"], "http://localhost:8767/connect?app=gmail");
        assert_eq!(gmail["distributionChannel"], "puffin");
        assert!(gmail.get("is_accessible").is_none() && gmail.get("isEnabled").is_none());
        let drive = &rows[1];
        assert_eq!(drive["isAccessible"], false);
        assert_eq!(drive["description"], App::Drive.blurb());
        assert_eq!(rows.as_array().map(Vec::len), Some(3));
    }

    #[test]
    fn a_stopped_service_names_the_command_and_offers_no_link() {
        let rows = rows(&Service::Down, false);
        for row in rows.as_array().into_iter().flatten() {
            assert_eq!(row["isAccessible"], false);
            assert!(row["installUrl"].is_null());
            assert!(row["description"].as_str().unwrap_or_default().contains(START_HINT));
        }
    }

    #[test]
    fn at_on_every_row_is_unavailable_and_has_no_link() {
        let service = Service::Up(vec![account("a@x.com", &App::ALL)]);
        for row in rows(&service, true).as_array().into_iter().flatten() {
            assert_eq!(row["isAccessible"], false);
            assert!(row["installUrl"].is_null());
            assert_eq!(row["description"], "Unavailable at /airgapped on");
        }
    }

    #[test]
    fn off_values() {
        for value in ["0", "false", "No", " off "] {
            assert!(is_off(value), "{value}");
        }
        for value in ["1", "true", "on", ""] {
            assert!(!is_off(value), "{value}");
        }
    }

    #[test]
    fn rows_deserialise_as_the_connectors_crate_reads_them() {
        // The fields H2 hands to serde for `AppInfo`; every one is optional there except id and
        // name, and `isAccessible`/`isEnabled` default. A typo in a key would leave it None.
        let rows = rows(&Service::Up(vec![account("a@x.com", &[App::Drive])]), false);
        let keys: Vec<&str> = rows[1].as_object().map(|o| o.keys().map(String::as_str).collect()).unwrap_or_default();
        for key in ["id", "name", "description", "installUrl", "isAccessible", "distributionChannel"] {
            assert!(keys.contains(&key), "{key}");
        }
    }
}
