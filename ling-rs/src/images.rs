//! The `image_search` tool in a session (specs/DREAMFERENCE_MIGHTLING_ASK.md §6).
//!
//! The tool is served by `ling-admin images mcp` (`dreamference/chat/image_search_mcp.py`), which
//! asks the image search sidecar on loopback; this module only decides whether a session gets it
//! and adds the `-c mcp_servers.mightling_images.…` arguments that make Codex start it. A session
//! gets it when all of these hold, none of which costs a connection (the egress audit counts one
//! to the sidecar's port as a finding):
//! - this machine is a node and the model server is local, as for `/apps`: the sidecar and its
//!   store live there;
//! - the configured air gap is not `on` (a session switched to `on` later still has the server,
//!   and each call refuses, as `/apps`' calls do);
//! - `ling-admin images start` has run: it writes the secret file the server reads;
//! - `ling-admin` is linked where `ling-admin codex build` and the installer put it;
//! - `DREAMFERENCE_MIGHTLING_IMAGES` is not off.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// The MCP server's name in Codex's configuration; the tool reaches the model as `image_search`
/// (patch 0020).
pub const MCP_SERVER: &str = "mightling_images";

/// Switches the tool off for a session when set to `0`, `false`, `no` or `off`.
pub const ENV_SWITCH: &str = "DREAMFERENCE_MIGHTLING_IMAGES";

/// What `ling-admin images mcp` must see beyond Codex's short allow-list: the home (the secret and
/// the settings), the runtime folder (a session's air-gap seal), and the air-gap settings.
const FORWARDED_ENV: &[&str] = &[
    "HOME",
    "XDG_RUNTIME_DIR",
    "CODEX_HOME",
    "DREAMFERENCE_CONFIG_PATH",
    "DREAMFERENCE_MIGHTLING_AIRGAPPED",
];

/// The sidecar's secret, relative to the home folder (`image_search_sidecar.py`).
const SECRET_FILE: &str = ".config/dreamference/image-search/secret";

/// `ling-admin`, relative to the home folder: the link `ling-admin codex build` makes.
const ADMIN_LINK: &str = ".local/bin/ling-admin";

/// The `ling-admin` that serves the tool, when image search is set up in `home`.
pub fn admin_for(home: &Path) -> Option<PathBuf> {
    if !home.join(SECRET_FILE).is_file() {
        return None;
    }
    let admin = home.join(ADMIN_LINK);
    admin.is_file().then_some(admin)
}

/// Whether the switch turns the tool off.
fn switched_off(value: Option<&str>) -> bool {
    value.is_some_and(|value| matches!(value.trim().to_ascii_lowercase().as_str(), "0" | "false" | "no" | "off"))
}

/// The `ling-admin` to declare for this session, or None when the session does not get the tool.
pub fn offered(air_gapped: bool, host_is_local: bool) -> Option<PathBuf> {
    if air_gapped || !host_is_local || !ling_apps::is_node() {
        return None;
    }
    if switched_off(std::env::var(ENV_SWITCH).ok().as_deref()) {
        return None;
    }
    admin_for(&ling_node_locator::home_dir()?)
}

/// Adds the arguments that make Codex start `ling-admin images mcp` for the session. A
/// declaration of the user's own for the same server wins: nothing is added.
pub fn with_server(args: Vec<OsString>, admin: Option<&Path>) -> Vec<OsString> {
    let Some(admin) = admin else { return args };
    let key = format!("mcp_servers.{MCP_SERVER}.");
    if args.iter().any(|arg| arg.to_string_lossy().contains(&key)) {
        return args;
    }
    let quoted = |text: &str| toml::Value::String(text.to_string()).to_string();
    let forwarded: Vec<String> = FORWARDED_ENV.iter().map(|name| quoted(name)).collect();
    let settings = [
        format!("{key}command={}", quoted(&admin.to_string_lossy())),
        format!("{key}args=[\"images\", \"mcp\"]"),
        format!("{key}env_vars=[{}]", forwarded.join(", ")),
    ];
    let mut args = args.into_iter();
    let mut out: Vec<OsString> = args.next().into_iter().collect();
    for setting in settings {
        out.extend(["-c".into(), OsString::from(setting)]);
    }
    out.extend(args);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(args: &[OsString]) -> Vec<String> {
        args.iter().map(|arg| arg.to_string_lossy().into_owned()).collect()
    }

    #[test]
    fn the_server_is_declared_after_the_program_name() {
        let args: Vec<OsString> = vec!["ling".into(), "exec".into(), "hi".into()];
        let out = strings(&with_server(args, Some(Path::new("/home/u/.local/bin/ling-admin"))));
        assert_eq!(out[0], "ling");
        assert_eq!(out[1], "-c");
        assert_eq!(out[2], "mcp_servers.mightling_images.command=\"/home/u/.local/bin/ling-admin\"");
        assert_eq!(out[4], "mcp_servers.mightling_images.args=[\"images\", \"mcp\"]");
        assert!(out[6].starts_with("mcp_servers.mightling_images.env_vars=[\"HOME\", \"XDG_RUNTIME_DIR\""), "{}", out[6]);
        assert_eq!(&out[7..], ["exec", "hi"]);
    }

    #[test]
    fn nothing_is_added_without_the_tool_and_the_users_own_declaration_wins() {
        let args: Vec<OsString> = vec!["ling".into()];
        assert_eq!(with_server(args.clone(), None), args);
        let own: Vec<OsString> = vec!["ling".into(), "-c".into(), "mcp_servers.mightling_images.enabled=false".into()];
        assert_eq!(with_server(own.clone(), Some(Path::new("/x/ling-admin"))), own);
    }

    #[test]
    fn the_tool_needs_the_secret_and_ling_admin() {
        let home = std::env::temp_dir().join(format!("ling-images-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(home.join(".local/bin")).unwrap();
        assert_eq!(admin_for(&home), None);
        std::fs::write(home.join(ADMIN_LINK), "#!/bin/sh\n").unwrap();
        assert_eq!(admin_for(&home), None, "not set up until `ling-admin images start` wrote the secret");
        std::fs::create_dir_all(home.join(".config/dreamference/image-search")).unwrap();
        std::fs::write(home.join(SECRET_FILE), "s3cret").unwrap();
        assert_eq!(admin_for(&home), Some(home.join(ADMIN_LINK)));
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn the_switch_turns_it_off() {
        assert!(switched_off(Some("off")) && switched_off(Some(" FALSE ")) && switched_off(Some("0")));
        assert!(!switched_off(None) && !switched_off(Some("on")) && !switched_off(Some("")));
    }

    #[test]
    fn a_client_or_a_configured_on_gets_nothing() {
        assert_eq!(offered(true, true), None);
        assert_eq!(offered(false, false), None);
    }
}
