//! Codex plugins and the air gap (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §14.12).
//!
//! Plugins are Codex's own extensions: marketplaces, `ling plugin add`, `/plugins`, and a plugin's
//! skills, MCP servers, hooks and apps. They are allowed while `/airgapped` is off (the owner's
//! decision of 2026-10-22), from a local folder or a marketplace in a git repository. What stays
//! closed at every level is the upstream vendor's: the curated catalogue's startup sync and the
//! featured list (patch 0015), and the remote catalogue and plugin sharing, which talk to the
//! vendor's backend and are switched off in `config.toml` here ([`VENDOR_FEATURES`]), with
//! `chatgpt_base_url` pointed at a closed port behind them.
//!
//! At a configured `on`:
//! - a session (the TUI, `exec`, the app server behind the desktop app and `ling web`) starts with
//!   `features.plugins=false`: no installed plugin's skills, MCP servers, hooks or apps load, and
//!   `/plugins` is not offered;
//! - `ling plugin add` and `ling plugin marketplace add|upgrade` are refused before Codex parses
//!   them: `git` fetches a marketplace or a plugin in `ling`'s own process, outside the command
//!   sandbox that takes the network away at `on`.
//!
//! A session switched to `on` later keeps the plugins it loaded until it is restarted: an MCP
//! server keeps the network it started with, as one the user configured does, while the commands a
//! plugin's skills run get no network, as every sandboxed command. `/airgapped` says so
//! ([`STATUS_LINE`]). The bridge in front of the app server (`ling-rs/web/policy.json`) refuses
//! installs at `on` for the desktop app and `ling web` and starts their threads without plugins.

use std::ffi::OsString;

use crate::airgapped::Level;

/// The override a session started at `on` gets.
pub const OFF_AT_ON: &str = "features.plugins=false";

/// Codex's plugin features that only talk to the upstream vendor's backend: its remote catalogue
/// and sharing a plugin through its accounts. Always off, not only when absent, as
/// `chatgpt_base_url` is: a privacy boundary, not a preference.
pub const VENDOR_FEATURES: [&str; 2] = ["remote_plugin", "plugin_sharing"];

/// What `/airgapped` adds at `on`.
pub const STATUS_LINE: &str = "Plugins: off in a session started at on; a session switched to on keeps the plugins it loaded (their MCP servers keep the network) until it is restarted.";

/// The `ling plugin …` commands that fetch nothing: listing and removing.
const OFFLINE_FORMS: &[&[&str]] = &[&["list"], &["remove"], &["marketplace", "list"], &["marketplace", "remove"], &["help"]];

/// The words of a `ling plugin …` command line after `plugin`, flags left out.
fn words(rest: &[String]) -> Vec<&str> {
    rest.iter().map(String::as_str).filter(|word| !word.starts_with('-')).collect()
}

/// Why a `ling plugin …` command may not run at `level`, if it may not. `rest` is what follows
/// `plugin`. Anything but listing, removing and help fetches a marketplace or a plugin with `git`
/// in this process, which no sandbox holds, so at `on` it is refused, and a form a later Codex adds
/// is refused too until it is listed here.
pub fn install_refusal(rest: &[String], level: Level, source: &str) -> Option<String> {
    if level != Level::On || rest.iter().any(|arg| matches!(arg.as_str(), "--help" | "-h")) {
        return None;
    }
    let words = words(rest);
    if words.is_empty() || OFFLINE_FORMS.iter().any(|form| words.starts_with(form)) {
        return None;
    }
    Some(format!(
        "Plugins not changed: airgapped is on ({source}). Installing a plugin or adding or upgrading a marketplace fetches it with git from outside the sandbox, which is what takes the network away at on.\nTo install one, turn the air gap off first (ling airgapped shows where on comes from), then run this again."
    ))
}

/// Whether the user turned plugins on for this run: `-c features.plugins=true` or `--enable plugins`.
fn enabled_by_user(user_args: &[String]) -> bool {
    let configured = crate::option_values(user_args, &["-c", "--config"]).any(|value| {
        value
            .split_once('=')
            .is_some_and(|(key, value)| key.trim() == "features.plugins" && value.trim() == "true")
    });
    configured || crate::option_values(user_args, &["--enable"]).any(|feature| feature == "plugins")
}

/// A session at `level`: at `on`, `features.plugins=false` in front of the user's arguments, and a
/// refusal if they turned plugins on themselves.
pub fn with_level(args: Vec<OsString>, user_args: &[String], level: Level) -> Result<Vec<OsString>, String> {
    if level != Level::On {
        return Ok(args);
    }
    if enabled_by_user(user_args) {
        return Err("Plugins are off while airgapped is on: a plugin's MCP servers run outside the sandbox, with the network. Drop the plugins setting, or turn the air gap off first (ling airgapped shows where on comes from).".to_string());
    }
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    out.extend(["-c".into(), OFF_AT_ON.into()]);
    out.extend(args);
    Ok(out)
}

/// Switches the vendor-only plugin features off in `config.toml`'s `[features]` table.
pub fn close_vendor_features(features: &mut toml_edit::Table) {
    for key in VENDOR_FEATURES {
        features[key] = toml_edit::value(false);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    #[test]
    fn installs_are_refused_at_on_and_allowed_at_off() {
        for form in [&["add", "lint@acme"][..], &["marketplace", "add", "acme/plugins"], &["marketplace", "upgrade"], &["add", "--json", "x@y"], &["future-form"]] {
            let rest = strings(form);
            let reason = install_refusal(&rest, Level::On, "DREAMFERENCE_MIGHTLING_AIRGAPPED").unwrap_or_else(|| panic!("{form:?}"));
            assert!(reason.starts_with("Plugins not changed: airgapped is on (DREAMFERENCE_MIGHTLING_AIRGAPPED)."), "{reason}");
            assert!(reason.contains("turn the air gap off first"));
            assert_eq!(install_refusal(&rest, Level::Off, "default"), None, "{form:?}");
        }
    }

    #[test]
    fn listing_removing_and_help_run_at_on() {
        for form in [&["list"][..], &["remove", "lint@acme"], &["marketplace", "list"], &["marketplace", "remove", "acme"], &["--json", "list"], &["add", "--help"], &["help"], &[]] {
            assert_eq!(install_refusal(&strings(form), Level::On, "x"), None, "{form:?}");
        }
    }

    #[test]
    fn a_session_at_on_starts_without_plugins() {
        let args: Vec<OsString> = ["ling", "exec", "hi"].iter().map(OsString::from).collect();
        let user = strings(&["exec", "hi"]);
        let out = with_level(args.clone(), &user, Level::On).unwrap();
        assert_eq!(out, ["ling", "-c", OFF_AT_ON, "exec", "hi"].iter().map(OsString::from).collect::<Vec<_>>());
        assert_eq!(with_level(args, &user, Level::Off).unwrap().len(), 3, "nothing changes at off");
    }

    #[test]
    fn plugins_turned_on_by_the_user_are_refused_at_on_only() {
        for user in [&["-c", "features.plugins=true", "app-server"][..], &["--config=features.plugins = true"], &["--enable", "plugins"], &["--enable=plugins", "exec", "x"]] {
            let user = strings(user);
            let args: Vec<OsString> = std::iter::once("ling".to_string()).chain(user.iter().cloned()).map(OsString::from).collect();
            assert!(with_level(args.clone(), &user, Level::On).is_err(), "{user:?}");
            assert_eq!(with_level(args.clone(), &user, Level::Off).unwrap(), args);
        }
        let user = strings(&["-c", "features.plugins=false"]);
        assert!(with_level(vec![OsString::from("ling")], &user, Level::On).is_ok());
    }

    #[test]
    fn the_vendor_features_are_forced_off() {
        let mut doc: toml_edit::DocumentMut = "[features]\nremote_plugin = true\ncode_mode = true\n".parse().unwrap();
        close_vendor_features(doc["features"].as_table_mut().unwrap());
        let parsed: toml::Value = toml::from_str(&doc.to_string()).unwrap();
        assert_eq!(parsed["features"]["remote_plugin"].as_bool(), Some(false));
        assert_eq!(parsed["features"]["plugin_sharing"].as_bool(), Some(false));
        assert_eq!(parsed["features"]["code_mode"].as_bool(), Some(true));
        // The keys exist in this Codex: a renamed one would be refused as unknown at start.
        let features = include_str!("../../features/src/lib.rs");
        for key in VENDOR_FEATURES.iter().chain(["plugins"].iter()) {
            assert!(features.contains(&format!("key: \"{key}\"")), "{key}");
        }
    }
}
