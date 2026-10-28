//! The `X-Ling-Share-Scope` header: which requests may share a cached prefix on the model server.
//!
//! The engine (ling-serve, P4c) shares a cached prefix only between requests with the same scope;
//! requests without the header share only with each other. The scope is an opaque hash of the
//! owner (the node id, else the local user id) and the repository (the git toplevel's `origin`
//! URL, normalized, else the toplevel's canonical path, else the working folder). No path, name or
//! URL leaves the machine. The header rides on the provider's `http_headers`, set per launch with
//! a `-c` override (the repository changes with the working folder, `config.toml` is global), so
//! Codex needs no patch. SGLang and vLLM ignore unknown headers.

use std::ffi::OsString;
use std::path::Path;

use sha2::{Digest, Sha256};

/// The header name.
pub const HEADER: &str = "X-Ling-Share-Scope";

/// Hex characters kept from the SHA-256 digest (128 bits).
const SCOPE_LEN: usize = 32;

/// `origin`'s URL reduced to `host/path`: no scheme, user, `.git` or trailing slash, lower case.
pub fn normalize_remote(url: &str) -> String {
    let url = url.trim();
    let (scheme, rest) = url.split_once("://").map_or((false, url), |(_, rest)| (true, rest));
    let rest = rest.split('/').next().and_then(|host| rest.strip_prefix(host).map(|path| (host, path)));
    let Some((host, path)) = rest else { return url.to_lowercase() };
    let host = host.rsplit_once('@').map_or(host, |(_, after)| after);
    let (host, path) = if scheme {
        (host.split(':').next().unwrap_or(host), path.to_string())
    } else if let Some((host, scp_path)) = host.split_once(':') {
        (host, format!("/{scp_path}{path}"))
    } else {
        (host, path.to_string())
    };
    let path = path.trim_end_matches('/').trim_end_matches(".git").trim_end_matches('/');
    format!("{host}{path}").to_lowercase()
}

/// The hash of `owner` and `repo` (any strings), 32 hex characters.
pub fn scope_hash(owner: &str, repo: &str) -> String {
    let mut hasher = Sha256::new();
    for part in ["ling-share-scope-v1", owner, repo] {
        hasher.update(part.as_bytes());
        hasher.update([0]);
    }
    let digest = hasher.finalize();
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    hex[..SCOPE_LEN].to_string()
}

/// The node id of this machine if it is a node, else the local user id.
fn owner() -> String {
    ling_node_locator::node_id_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .map(|id| id.trim().to_string())
        .filter(|id| !id.is_empty())
        .map(|id| format!("node:{id}"))
        .unwrap_or_else(|| format!("uid:{}", unsafe { libc_euid() }))
}

#[cfg(unix)]
unsafe fn libc_euid() -> u32 {
    unsafe { libc::geteuid() }
}

#[cfg(not(unix))]
unsafe fn libc_euid() -> u32 {
    0
}

/// The repository's identity as text: normalized `origin`, else the toplevel's canonical path,
/// else `dir` itself. Never sent; only hashed.
fn repository(dir: &Path) -> String {
    let git = |args: &[&str]| {
        let output = ling_safegit::output(dir, args).ok().filter(|output| output.status.success())?;
        let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
        (!text.is_empty()).then_some(text)
    };
    if let Some(url) = git(&["config", "--get", "remote.origin.url"]) {
        return format!("origin:{}", normalize_remote(&url));
    }
    let top = git(&["rev-parse", "--show-toplevel"]).map(std::path::PathBuf::from).unwrap_or_else(|| dir.to_path_buf());
    format!("path:{}", std::fs::canonicalize(&top).unwrap_or(top).display())
}

/// The scope for the session in `dir`.
pub fn scope_for(dir: &Path) -> String {
    scope_hash(&owner(), &repository(dir))
}

/// `args` with the provider's header set to `scope`, unless the user's command line already sets it.
pub fn with_scope(args: Vec<OsString>, scope: &str) -> Vec<OsString> {
    let key = format!("model_providers.{}.http_headers.{HEADER}", crate::PROVIDER);
    if args.iter().any(|arg| arg.to_string_lossy().contains(&key)) {
        return args;
    }
    crate::compaction::with_assignment(args, &key, toml::Value::String(scope.to_string()))
}

/// For this launch: the scope of the session in `dir`.
pub fn prepare(args: Vec<OsString>, dir: &Path) -> Vec<OsString> {
    with_scope(args, &scope_for(dir))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn remotes_that_name_one_repository_normalize_alike() {
        let same = [
            "https://github.com/Org/Repo.git",
            "git@github.com:org/repo.git",
            "ssh://git@github.com/org/repo",
            "https://user:tok@GitHub.com/org/repo/",
        ];
        for url in same {
            assert_eq!(normalize_remote(url), "github.com/org/repo", "{url}");
        }
        assert_ne!(normalize_remote("https://github.com/org/other"), "github.com/org/repo");
    }

    #[test]
    fn the_scope_is_32_hex_characters_and_depends_on_owner_and_repository() {
        let a = scope_hash("node:1", "origin:github.com/org/repo");
        assert_eq!(a.len(), 32);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_eq!(a, scope_hash("node:1", "origin:github.com/org/repo"));
        assert_ne!(a, scope_hash("node:2", "origin:github.com/org/repo"));
        assert_ne!(a, scope_hash("node:1", "origin:github.com/org/other"));
        // The parts are delimited: moving a character across the boundary changes the hash.
        assert_ne!(scope_hash("ab", "c"), scope_hash("a", "bc"));
    }

    #[test]
    fn the_header_is_an_override_of_the_provider_and_carries_only_the_hash() {
        let args = vec![OsString::from("ling"), OsString::from("exec")];
        let out = with_scope(args, "abc123");
        assert_eq!(out[1], OsString::from("-c"));
        let text = out[2].to_string_lossy().to_string();
        assert_eq!(text, "model_providers.openai-custom.http_headers.X-Ling-Share-Scope=\"abc123\"");
        let (key, raw) = text.split_once('=').unwrap_or_default();
        let table: toml::Table = format!("_x_ = {raw}").parse().unwrap_or_default();
        assert_eq!(table["_x_"].as_str(), Some("abc123"));
        assert!(key.starts_with("model_providers.openai-custom."));
        // A header the user set on the command line is kept.
        assert_eq!(with_scope(out.clone(), "other"), out);
    }

    #[test]
    fn nothing_readable_leaves_in_the_scope() {
        let dir = std::env::temp_dir();
        let scope = scope_for(&dir);
        assert_eq!(scope.len(), 32);
        assert!(!scope.contains(&dir.to_string_lossy().to_string()));
    }
}
