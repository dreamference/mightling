//! Web access for the `ling` agent: `ling-search` (with `--read`, which also reads the top
//! results) and `ling-fetch`.
//!
//! These are the two shell commands Mightling's system prompt tells the model to use for the web
//! (`WEB_ACCESS_INSTRUCTIONS` in `ling-rs/src/lib.rs`). They were Python until 2026-09-30 --
//! `ling-search` a console script of the repository's virtualenv and fetching a `ling-admin`
//! subcommand -- which made the model's web access depend on that virtualenv staying where it was:
//! when `ling-admin` was not on the shell's `PATH`, every call ended in exit 127 and the model
//! concluded it had no web. As static binaries installed beside `ling`, they work from any shell
//! and are updated with it.
//!
//! Behaviour is carried over from `dreamference/mcp_server/web_tools.py`, which the MCP server still
//! uses: the same SearXNG endpoint and parameters, the same size and time limits, the same text
//! extraction (non-content tags dropped, text nodes joined by newlines, blank runs collapsed), and
//! the same errors, including the one that names every failed search engine. Where this differs it
//! says so at the function.

pub use ling_airgapped as airgapped;
pub mod cache;
pub mod fetch;
pub mod html_text;
pub mod node_locator;
pub mod read;
pub mod search;
pub mod share_links;

use std::time::Duration;

use airgapped::Level;

/// Printed, with nothing sent, when the session's level is `on`.
pub const AIRGAPPED_ON_MESSAGE: &str = "Web access is off in this session (/airgapped on). Only the user can change that, with /airgapped.";

/// Printed, with nothing sent, when the sandbox this command runs in has no network and the level
/// is not `on`: naming `/airgapped on` here would name a setting nobody chose. Below `on` every
/// sandbox has the network unless the user switched it off, so that is what is named.
pub const NO_NETWORK_SANDBOX_MESSAGE: &str = "This command has no network: the user switched the sandbox's network off (sandbox_workspace_write.network_access = false, or DREAMFERENCE_MIGHTLING_READ_ONLY_NETWORK=off). Only the user can change that.";

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
    let disabled = std::env::var_os(airgapped::SANDBOX_NETWORK_DISABLED_ENV_VAR)
        .is_some_and(|value| !value.is_empty());
    refusal(level(), disabled)
}

/// Sent on every request. Some sites serve a stub or a challenge page to unknown agents, and the
/// point of a fetch is to return what a person would see.
pub const USER_AGENT: &str = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) \
     Chrome/140.0 Safari/537.36";

/// Applied to connecting, as `requests` applies its `timeout`; [`REQUEST_DEADLINE`] bounds the rest.
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(25);

/// The most one request may take, from connecting to the last byte of the body, redirects
/// included. A per-read timeout alone let a server that sends a byte every few seconds hold a
/// command for as long as it liked: a page is up to 5 MiB, and its headers up to 100 KB, at a byte
/// per 24 s. ureq's deadline replaces the per-read timeout once the request is under way, so a
/// response that stalls part-way ends here rather than after [`REQUEST_TIMEOUT`]. It is
/// `pdf_search`'s limit for a whole `ling-fetch --download` too. `web_tools.py` has the same.
pub const REQUEST_DEADLINE: Duration = Duration::from_secs(120);

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
    agent_with_redirects(proxy, 10)
}

/// [`agent`], following at most `redirects` redirects; with 0 a redirect is returned as the
/// response it is, which is how `share_links` reads each `Location` before requesting it.
pub fn agent_with_redirects(proxy: Option<ureq::Proxy>, redirects: u32) -> ureq::Agent {
    agent_within(proxy, redirects, REQUEST_DEADLINE)
}

/// [`agent_with_redirects`] with each request ended after `deadline`.
fn agent_within(proxy: Option<ureq::Proxy>, redirects: u32, deadline: Duration) -> ureq::Agent {
    let builder = ureq::AgentBuilder::new()
        .user_agent(USER_AGENT)
        .timeout_connect(REQUEST_TIMEOUT)
        .timeout_read(REQUEST_TIMEOUT)
        .timeout(deadline)
        .redirects(redirects);
    match proxy {
        Some(proxy) => builder.proxy(proxy),
        None => builder,
    }
    .build()
}

/// The proxy for `url` from the environment, as `requests` chooses it: `<scheme>_proxy`, then
/// `all_proxy`, each lower case over upper case, and none for a host `no_proxy` names (an exact
/// host, a domain suffix with or without a leading dot, or `*`). Address ranges in `no_proxy`
/// (`10.0.0.0/8`) are not supported. A proxy ureq cannot use (`https://`, `socks5h://`) is an
/// error naming the variable, never its value (it can hold a password): connecting without the
/// proxy someone set would bypass it.
pub fn proxy_for(
    url: &str,
    env: impl Fn(&str) -> Option<String>,
) -> Result<Option<ureq::Proxy>, String> {
    let lookup = |name: &str| {
        let upper = name.to_uppercase();
        env(name)
            .map(|value| (name.to_string(), value))
            .or_else(|| env(&upper).map(|value| (upper, value)))
            .filter(|(_, value)| !value.trim().is_empty())
    };
    let Some((scheme, rest)) = url.split_once("://") else {
        return Ok(None);
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let host_port = authority
        .rsplit_once('@')
        .map_or(authority, |(_, host)| host);
    let host = match host_port.strip_prefix('[') {
        Some(bracketed) => bracketed.split(']').next().unwrap_or_default(),
        None => host_port.split(':').next().unwrap_or_default(),
    }
    .to_ascii_lowercase();
    if let Some((_, no_proxy)) = lookup("no_proxy") {
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
            return Ok(None);
        }
    }
    let Some((name, value)) =
        lookup(&format!("{}_proxy", scheme.to_ascii_lowercase())).or_else(|| lookup("all_proxy"))
    else {
        return Ok(None);
    };
    ureq::Proxy::new(value).map(Some).map_err(|_| {
        format!(
            "request failed: the proxy in {name} is not one ling can use \
             (http://, socks4://, socks4a:// or socks5://)"
        )
    })
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
        matches!(proxy_for(url, env(pairs)), Ok(Some(_)))
    }

    #[test]
    fn a_proxy_that_cannot_be_used_is_an_error_not_a_direct_connection() {
        // ureq takes http, socks4, socks4a and socks5 proxies; curl's `socks5h://` is not one.
        let unusable = [("https_proxy", "socks5h://user:secret@p:1080")];
        let error = proxy_for("https://a.com/x", env(&unusable)).err().unwrap();
        assert!(error.contains("https_proxy"), "{error}");
        assert!(
            !error.contains("secret"),
            "the value can hold a password: {error}"
        );
        // A host that no_proxy names needs no proxy, so it does not fail.
        let bypassed = [unusable[0], ("no_proxy", "a.com")];
        assert!(matches!(
            proxy_for("https://a.com/x", env(&bypassed)),
            Ok(None)
        ));
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
    fn an_empty_proxy_variable_counts_as_unset() {
        assert!(proxied(
            "https://a.com/x",
            &[("https_proxy", ""), ("ALL_PROXY", "http://p:3128")]
        ));
    }

    #[test]
    fn the_lower_case_variable_wins_over_the_upper_case_one() {
        let both = [
            ("https_proxy", "http://p:3128"),
            ("HTTPS_PROXY", "socks5h://p:1080"),
        ];
        assert!(matches!(
            proxy_for("https://a.com/x", env(&both)),
            Ok(Some(_))
        ));
    }

    #[test]
    fn no_proxy_reads_the_host_after_a_user_and_password() {
        let proxy = ("https_proxy", "http://p:3128");
        assert!(!proxied(
            "https://user:secret@a.com/x",
            &[proxy, ("no_proxy", "a.com")]
        ));
    }

    #[test]
    fn a_no_proxy_entry_with_a_port_names_its_host() {
        let proxy = ("https_proxy", "http://p:3128");
        assert!(!proxied(
            "https://a.com:8443/x",
            &[proxy, ("no_proxy", "a.com:8443")]
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

    /// A stand-in on loopback that answers once with `head`, then sends one more byte of `drip`
    /// every 50 ms for at most 30 s. Never joined: the client gives up first.
    fn dripping_server(head: &'static [u8], drip: u8) -> String {
        use std::io::{BufRead, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}/", listener.local_addr().unwrap());
        std::thread::spawn(move || {
            let Ok((mut stream, _)) = listener.accept() else {
                return;
            };
            let mut reader = std::io::BufReader::new(stream.try_clone().unwrap());
            let mut line = String::new();
            while reader.read_line(&mut line).is_ok_and(|n| n > 2) {
                line.clear();
            }
            if stream.write_all(head).is_err() {
                return;
            }
            for _ in 0..600 {
                std::thread::sleep(Duration::from_millis(50));
                if stream.write_all(&[drip]).is_err() {
                    return;
                }
            }
        });
        base
    }

    /// The deadline under test is 2 s; without it each of these ran for the stand-in's 30 s.
    const SHORT: Duration = Duration::from_secs(2);

    #[test]
    fn a_body_sent_a_byte_at_a_time_ends_at_the_deadline() {
        use std::io::Read;
        let url = dripping_server(b"HTTP/1.1 200 OK\r\nContent-Length: 5000000\r\n\r\n", b'a');
        let started = std::time::Instant::now();
        let response = agent_within(None, 10, SHORT).get(&url).call().unwrap();
        let mut body = Vec::new();
        let read = response
            .into_reader()
            .take(5 * 1024 * 1024)
            .read_to_end(&mut body);
        assert!(
            read.is_err(),
            "the body was read whole: {} bytes",
            body.len()
        );
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "{:?}",
            started.elapsed()
        );
    }

    #[test]
    fn headers_sent_a_byte_at_a_time_end_at_the_deadline() {
        let url = dripping_server(b"HTTP/1.1 200 OK\r\nX-Slow: ", b'a');
        let started = std::time::Instant::now();
        assert!(agent_within(None, 10, SHORT).get(&url).call().is_err());
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "{:?}",
            started.elapsed()
        );
    }

    #[test]
    fn characters_not_bytes_are_counted() {
        assert_eq!(take_chars("héllo", 2), "hé");
        assert_eq!(take_chars("hé", 5), "hé");
    }

    #[test]
    fn an_empty_proxy_variable_is_no_proxy_and_the_next_one_is_read() {
        assert!(matches!(
            proxy_for("https://a.com/x", env(&[("https_proxy", " ")])),
            Ok(None)
        ));
        let unusable = [("https_proxy", ""), ("ALL_PROXY", "socks5h://p:1080")];
        let error = proxy_for("https://a.com/x", env(&unusable)).err().unwrap();
        assert!(error.contains("ALL_PROXY"), "{error}");
    }
}
