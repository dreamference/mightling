//! Local traffic never goes through a proxy (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §11).
//!
//! An `HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY` left in a shell would also send the requests to
//! the model server and to the loopback services (SearXNG, Gmail, the code index) to that proxy:
//! at best a detour, at worst a refusal that looks like a dead server, or the prompt leaving the
//! machine. `NO_PROXY` is the one channel every client here reads: Codex for each request
//! (`NO_PROXY`, then `no_proxy`: upstream's `http-client/src/outbound_proxy.rs`), `ling-search`
//! and `ling-fetch` (`no_proxy`, then `NO_PROXY`), reqwest in the launcher, and Python's
//! `requests` and `urllib` in anything a session runs. Upstream Codex has no per-provider proxy
//! setting, so the variable is the way. Both spellings are set, each to the union of what either
//! held and the local hosts, because the two readers prefer opposite ones.

use std::collections::HashSet;

/// The names this machine answers to, always exempt.
pub const LOOPBACK: [&str; 3] = ["localhost", "127.0.0.1", "::1"];

/// Adds loopback and the model server's host to `NO_PROXY` and `no_proxy`.
///
/// Called from `home::use_mightling_home`, first thing in Codex's `main()`, before any other thread
/// exists: the only point at which setting a variable is sound. The model server is not resolved
/// yet, so the hosts are the ones known without a browse: `DREAMFERENCE_VLLM_HOST` or `vllm_host`,
/// and the remembered node. A node found by a browse for the first time is remembered before Codex
/// starts, so it is exempt from the next start on; the launcher's own requests to it never use a
/// proxy (`direct_client`).
pub fn export() {
    let mut hosts: Vec<String> = Vec::new();
    hosts.extend(crate::configured_vllm_host());
    hosts.extend(ling_node_locator::remembered().map(|node| node.model_url()));
    let upper = std::env::var("NO_PROXY").ok();
    let lower = std::env::var("no_proxy").ok();
    let merged = merged(upper.as_deref(), lower.as_deref(), &hosts);
    // SAFETY: see above; no other thread can be reading the environment yet.
    unsafe {
        std::env::set_var("NO_PROXY", &merged);
        std::env::set_var("no_proxy", &merged);
    }
}

/// A client for the model server and the loopback services: it never uses a proxy, whatever the
/// environment says, so it also covers a node the environment does not name yet.
pub fn direct_client(timeout: std::time::Duration) -> reqwest::Result<reqwest::Client> {
    reqwest::Client::builder().no_proxy().timeout(timeout).build()
}

/// The `NO_PROXY` value: every entry of `upper` and `lower` in their order without repeats, then
/// loopback and the host of each URL in `hosts` that is not already there.
pub fn merged(upper: Option<&str>, lower: Option<&str>, hosts: &[String]) -> String {
    let mut seen = HashSet::new();
    let mut entries: Vec<String> = Vec::new();
    let existing = [upper, lower].into_iter().flatten().flat_map(|value| value.split(','));
    let local = LOOPBACK.iter().map(|host| host.to_string()).chain(hosts.iter().filter_map(|url| host_name(url)));
    for entry in existing.map(|entry| entry.trim().to_string()).chain(local) {
        if !entry.is_empty() && seen.insert(entry.to_ascii_lowercase()) {
            entries.push(entry);
        }
    }
    entries.join(",")
}

/// The host of a URL such as `http://192.168.1.20:8000/v1`, without scheme, port, path or the
/// brackets of an IPv6 address; `None` when there is none.
pub fn host_name(url: &str) -> Option<String> {
    let rest = url.trim();
    let rest = rest.split_once("://").map_or(rest, |(_, after)| after);
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let authority = authority.rsplit_once('@').map_or(authority, |(_, host)| host);
    let host = match authority.strip_prefix('[') {
        Some(bracketed) => bracketed.split(']').next().unwrap_or_default(),
        None => authority.split(':').next().unwrap_or_default(),
    };
    (!host.is_empty()).then(|| host.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hosts_come_out_of_urls() {
        assert_eq!(host_name("http://192.168.1.20:8000/v1").as_deref(), Some("192.168.1.20"));
        assert_eq!(host_name("http://spark.local:8000").as_deref(), Some("spark.local"));
        assert_eq!(host_name("https://[fe80::1]:8000/").as_deref(), Some("fe80::1"));
        assert_eq!(host_name("gb10:8000").as_deref(), Some("gb10"));
        assert_eq!(host_name("http://user:secret@host:1/").as_deref(), Some("host"));
        assert_eq!(host_name(""), None);
    }

    #[test]
    fn existing_entries_stay_first_and_local_hosts_are_added_once() {
        let hosts = vec!["http://192.168.1.20:8000".to_string(), "http://localhost:8000".to_string()];
        assert_eq!(
            merged(Some("corp.example, .internal"), Some("other.example,corp.example"), &hosts),
            "corp.example,.internal,other.example,localhost,127.0.0.1,::1,192.168.1.20"
        );
    }

    #[test]
    fn with_nothing_set_loopback_alone_is_exempt() {
        assert_eq!(merged(None, Some(""), &[]), "localhost,127.0.0.1,::1");
    }

    #[test]
    fn a_second_merge_changes_nothing() {
        let hosts = vec!["http://gb10:8000".to_string()];
        let once = merged(Some("a.example"), None, &hosts);
        assert_eq!(merged(Some(&once), Some(&once), &hosts), once);
    }
}
