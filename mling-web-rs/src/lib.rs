//! Web access for the `mling` agent: `mling-search` and `mling-fetch`.
//!
//! These are the two shell commands Mightling's system prompt tells the model to use for the web
//! (`WEB_ACCESS_INSTRUCTIONS` in `mling-rs/src/lib.rs`). They were Python until 2026-09-30 --
//! `mling-search` a console script of the repository's virtualenv and fetching a `mling-admin`
//! subcommand -- which made the model's web access depend on that virtualenv staying where it was:
//! when `mling-admin` was not on the shell's `PATH`, every call ended in exit 127 and the model
//! concluded it had no web. As static binaries installed beside `mling`, they work from any shell
//! and are updated with it.
//!
//! Behaviour is carried over from `dreamference/mcp_server/web_tools.py`, which the MCP server still
//! uses: the same SearXNG endpoint and parameters, the same size and time limits, the same text
//! extraction (non-content tags dropped, text nodes joined by newlines, blank runs collapsed), and
//! the same errors, including the one that names every failed search engine. Where this differs it
//! says so at the function.

pub mod airgapped;
pub mod fetch;
pub mod html_text;
pub mod node_locator;
pub mod search;

use std::time::Duration;

use airgapped::Level;

/// Printed, with nothing sent, when the session's level is `on`.
pub const AIRGAPPED_ON_MESSAGE: &str = "Web access is off in this session (/airgapped on). Only the user can change that, with /airgapped.";

/// Printed, with nothing sent, when the sandbox this command runs in has no network and the level
/// is not `on`: naming `/airgapped on` here would name a setting nobody chose.
pub const NO_NETWORK_SANDBOX_MESSAGE: &str = "This command has no network: the sandbox it runs in does not allow it. Web access needs the workspace-write sandbox (mling exec -s workspace-write).";

/// The session's air-gap level, as the launcher and the sandbox resolve it
/// (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §6.2).
pub fn level() -> Level {
    airgapped::resolve_for_command().level
}

/// Why a web command must send nothing, if it must: the level is `on`, or Codex started this
/// command without a network (`CODEX_SANDBOX_NETWORK_DISABLED`). No restart hint goes with it:
/// nothing is broken.
pub fn refusal(level: Level, sandbox_network_disabled: bool) -> Option<&'static str> {
    match (level, sandbox_network_disabled) {
        (Level::On, _) => Some(AIRGAPPED_ON_MESSAGE),
        (_, true) => Some(NO_NETWORK_SANDBOX_MESSAGE),
        _ => None,
    }
}

/// [`refusal`] for this process's environment.
pub fn refusal_now() -> Option<&'static str> {
    let disabled = std::env::var_os(airgapped::SANDBOX_NETWORK_DISABLED_ENV_VAR).is_some_and(|value| !value.is_empty());
    refusal(level(), disabled)
}

/// Sent on every request. Some sites serve a stub or a challenge page to unknown agents, and the
/// point of a fetch is to return what a person would see.
pub const USER_AGENT: &str = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) \
     Chrome/140.0 Safari/537.36";

/// Applied to connecting and to each read, as `requests` applies its `timeout`: a slow but live
/// download is not cut off at a fixed total.
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(25);

/// An HTTP agent with Mightling's user agent and timeouts, sending through `proxy` when given.
///
/// The local SearXNG is always reached directly. Fetches use `proxy_for`, which picks the proxy
/// the way `requests` does; ureq's own detection would ignore `NO_PROXY` and send every URL
/// through the first variable it finds.
///
/// The agent keeps cookies (ureq's `cookies` feature) for as long as it lives, which is one
/// command: some sites redirect to set a cookie and expect it back, as `requests` sends it within
/// a redirect chain. Without it theweathernetwork.com ended on a `?_guid_iss_=1` bounce URL.
/// Nothing is written to disk.
pub fn agent(proxy: Option<ureq::Proxy>) -> ureq::Agent {
    let builder = ureq::AgentBuilder::new()
        .user_agent(USER_AGENT)
        .timeout_connect(REQUEST_TIMEOUT)
        .timeout_read(REQUEST_TIMEOUT)
        .redirects(10);
    match proxy {
        Some(proxy) => builder.proxy(proxy),
        None => builder,
    }
    .build()
}

/// The proxy for `url` from the environment, as `requests` chooses it: `<scheme>_proxy`, then
/// `all_proxy`, each lower case over upper case, and none for a host `no_proxy` names (an exact
/// host, a domain suffix with or without a leading dot, or `*`). Address ranges in `no_proxy`
/// (`10.0.0.0/8`) are not supported.
pub fn proxy_for(url: &str, env: impl Fn(&str) -> Option<String>) -> Option<ureq::Proxy> {
    let lookup = |name: &str| {
        env(name)
            .or_else(|| env(&name.to_uppercase()))
            .filter(|value| !value.trim().is_empty())
    };
    let (scheme, rest) = url.split_once("://")?;
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let host_port = authority
        .rsplit_once('@')
        .map_or(authority, |(_, host)| host);
    let host = match host_port.strip_prefix('[') {
        Some(bracketed) => bracketed.split(']').next().unwrap_or_default(),
        None => host_port.split(':').next().unwrap_or_default(),
    }
    .to_ascii_lowercase();
    if let Some(no_proxy) = lookup("no_proxy") {
        let bypass = no_proxy.split(',').map(str::trim).any(|entry| {
            // A port is dropped from `host:port`, but not from an IPv6 address, which is all colons.
            let entry = match entry.matches(':').count() {
                1 => entry.split(':').next().unwrap_or_default(),
                _ => entry.trim_start_matches('[').trim_end_matches(']'),
            }
            .trim_start_matches('.');
            entry == "*"
                || (!entry.is_empty()
                    && (host == entry.to_ascii_lowercase()
                        || host.ends_with(&format!(".{}", entry.to_ascii_lowercase()))))
        });
        if bypass {
            return None;
        }
    }
    let value = lookup(&format!("{}_proxy", scheme.to_ascii_lowercase()))
        .or_else(|| lookup("all_proxy"))?;
    ureq::Proxy::new(value).ok()
}

/// The first `limit` characters of `text` (characters, not bytes, as Python's slicing counts).
pub fn take_chars(text: &str, limit: usize) -> &str {
    match text.char_indices().nth(limit) {
        Some((end, _)) => &text[..end],
        None => text,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        move |name| map.get(name).cloned()
    }

    fn proxied(url: &str, pairs: &[(&str, &str)]) -> bool {
        proxy_for(url, env(pairs)).is_some()
    }

    #[test]
    fn the_proxy_follows_the_scheme_then_all_proxy() {
        assert!(proxied(
            "https://a.com/x",
            &[("HTTPS_PROXY", "http://p:3128")]
        ));
        assert!(!proxied(
            "http://a.com/x",
            &[("HTTPS_PROXY", "http://p:3128")]
        ));
        assert!(proxied("http://a.com/x", &[("ALL_PROXY", "http://p:3128")]));
        assert!(!proxied("https://a.com/x", &[]));
    }

    #[test]
    fn no_proxy_matches_hosts_suffixes_and_star() {
        let proxy = ("https_proxy", "http://p:3128");
        assert!(!proxied("https://a.com/x", &[proxy, ("no_proxy", "a.com")]));
        assert!(!proxied(
            "https://www.a.com/x",
            &[proxy, ("NO_PROXY", ".a.com")]
        ));
        assert!(!proxied(
            "https://user@www.a.com:8443/x",
            &[proxy, ("no_proxy", "b.org, a.com")]
        ));
        assert!(proxied(
            "https://nota.com/x",
            &[proxy, ("no_proxy", "a.com")]
        ));
        assert!(!proxied("https://a.com/x", &[proxy, ("no_proxy", "*")]));
        assert!(!proxied(
            "https://[::1]:8080/x",
            &[proxy, ("no_proxy", "::1")]
        ));
    }

    #[test]
    fn a_web_command_refuses_at_on_and_in_a_sandbox_without_network() {
        assert_eq!(refusal(Level::On, false), Some(AIRGAPPED_ON_MESSAGE));
        assert_eq!(refusal(Level::On, true), Some(AIRGAPPED_ON_MESSAGE));
        assert_eq!(refusal(Level::Off, true), Some(NO_NETWORK_SANDBOX_MESSAGE));
        assert_eq!(refusal(Level::Off, false), None);
        assert!(!NO_NETWORK_SANDBOX_MESSAGE.contains("airgapped"));
    }

    #[test]
    fn characters_not_bytes_are_counted() {
        assert_eq!(take_chars("héllo", 2), "hé");
        assert_eq!(take_chars("hé", 5), "hé");
    }
}
