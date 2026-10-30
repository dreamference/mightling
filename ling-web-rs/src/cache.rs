//! A small on-disk cache of SearXNG's answers, and a spacing between the searches it misses.
//!
//! On 2026-10-20 every web engine SearXNG asked refused this machine's address, and an agent
//! researching one question asks the same thing several times over a session. A repeated query
//! within an hour is answered from here, so it reaches no engine at all. Searches that do go out
//! are spaced at least [`MIN_INTERVAL`] apart, across every `ling-search` on this machine: SearXNG
//! sends each search to every general engine once, so this is also each engine's rate limit (at
//! most 30 a minute from this machine, where several agents firing searches at once used to send
//! them in the same second).
//!
//! Everything here is best-effort. A cache that cannot be opened, read or written is a miss and
//! nothing else: a search never fails because of it. Only answers with something in them are kept;
//! an error, a refusal or an empty answer is asked again next time.
//!
//! Where: `LING_SEARCH_CACHE_DIR` (`off` or empty turns the cache off), else
//! `$XDG_CACHE_HOME/dreamference/ling-search` (`~/.cache/...`), else `ling-search-<user>` in the
//! temporary directory. Inside the agent's sandbox `~/.cache` is read-only and the temporary
//! directory is writable, which is why there is a second place. A directory is used only if it is
//! this user's and no one else can write to it: a cache another user could fill would let them
//! choose the results.

use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use serde_json::Value;
use serde_json::json;

/// How long an answer is reused.
pub const TTL: Duration = Duration::from_secs(3600);

/// The least time between two searches that go out to SearXNG from this machine.
pub const MIN_INTERVAL: Duration = Duration::from_secs(2);

/// The longest a search waits for its slot; a burst of five is spread over ten seconds.
pub const MAX_WAIT: Duration = Duration::from_secs(10);

/// Names the cache directory; `off` or empty turns the cache off.
pub const CACHE_DIR_ENV: &str = "LING_SEARCH_CACHE_DIR";

const STAMP_FILE: &str = "last-search";

/// The cache in one directory.
#[derive(Debug, Clone)]
pub struct Cache {
    dir: PathBuf,
}

/// FNV-1a: stable across Rust versions and builds, unlike `DefaultHasher`, so two `ling-search`
/// builds find each other's entries. Collisions are caught by the key stored in the entry.
fn fnv1a(text: &str) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in text.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

fn unix_millis(time: SystemTime) -> u128 {
    time.duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis())
        .unwrap_or_default()
}

impl Cache {
    /// The cache for this process's environment, or None when there is none to use. Never in
    /// this crate's unit tests, which must not write under the real home or wait for a slot.
    pub fn open() -> Option<Cache> {
        if cfg!(test) {
            return None;
        }
        Self::open_from(
            std::env::var(CACHE_DIR_ENV).ok().as_deref(),
            std::env::var("XDG_CACHE_HOME").ok().as_deref(),
            std::env::var("HOME").ok().as_deref(),
            std::env::var("USER").ok().as_deref(),
        )
    }

    /// [`Cache::open`] with its environment given.
    pub fn open_from(
        configured: Option<&str>,
        xdg_cache_home: Option<&str>,
        home: Option<&str>,
        user: Option<&str>,
    ) -> Option<Cache> {
        if let Some(dir) = configured {
            let dir = dir.trim();
            if dir.is_empty() || dir == "off" {
                return None;
            }
            return Self::at(PathBuf::from(dir));
        }
        let cache_home = xdg_cache_home
            .filter(|dir| !dir.trim().is_empty())
            .map(PathBuf::from)
            .or_else(|| {
                home.filter(|dir| !dir.trim().is_empty())
                    .map(|dir| Path::new(dir).join(".cache"))
            });
        let user = user
            .filter(|name| !name.is_empty() && !name.contains('/'))
            .unwrap_or("user");
        cache_home
            .map(|dir| dir.join("dreamference").join("ling-search"))
            .into_iter()
            .chain(std::iter::once(
                std::env::temp_dir().join(format!("ling-search-{user}")),
            ))
            .find_map(Self::at)
    }

    /// The cache in `dir`, created if missing, or None when it cannot be used: not a directory,
    /// writable by others, owned by someone else, or not writable here.
    #[cfg(unix)]
    pub fn at(dir: PathBuf) -> Option<Cache> {
        use std::os::unix::fs::DirBuilderExt;
        use std::os::unix::fs::MetadataExt;
        if !dir.exists() {
            std::fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(&dir)
                .ok()?;
        }
        let meta = std::fs::symlink_metadata(&dir).ok()?;
        if !meta.is_dir() || meta.mode() & 0o022 != 0 {
            return None;
        }
        // Writing a file is both the test that this place is writable (inside the sandbox an
        // existing `~/.cache` is read-only) and the way to learn this process's user without a
        // system call: the file is ours, so the directory must be too.
        let probe = dir.join(".owner");
        std::fs::write(&probe, b"").ok()?;
        let ours = std::fs::metadata(&probe).ok()?.uid() == meta.uid();
        ours.then_some(Cache { dir })
    }

    /// No cache where the ownership of a directory cannot be checked.
    #[cfg(not(unix))]
    pub fn at(_dir: PathBuf) -> Option<Cache> {
        None
    }

    /// The directory this cache keeps its entries in.
    pub fn dir(&self) -> &Path {
        &self.dir
    }

    fn entry(&self, key: &str) -> PathBuf {
        self.dir.join(format!("{:016x}.json", fnv1a(key)))
    }

    /// The answer stored for `key` less than [`TTL`] before `now`.
    pub fn get(&self, key: &str, now: SystemTime) -> Option<Value> {
        let text = std::fs::read_to_string(self.entry(key)).ok()?;
        let entry: Value = serde_json::from_str(&text).ok()?;
        if entry["key"].as_str() != Some(key) {
            return None;
        }
        let saved = entry["saved_ms"].as_u64()? as u128;
        let age = unix_millis(now).checked_sub(saved)?;
        if age >= TTL.as_millis() {
            return None;
        }
        Some(entry["payload"].clone())
    }

    /// Stores `payload` for `key`, and removes entries older than [`TTL`].
    pub fn put(&self, key: &str, payload: &Value, now: SystemTime) {
        let entry = json!({"key": key, "saved_ms": unix_millis(now) as u64, "payload": payload});
        let path = self.entry(key);
        let temporary = path.with_extension(format!("tmp{}", std::process::id()));
        if std::fs::write(&temporary, entry.to_string()).is_ok()
            && std::fs::rename(&temporary, &path).is_err()
        {
            let _ = std::fs::remove_file(&temporary);
        }
        self.prune(now);
    }

    fn prune(&self, now: SystemTime) {
        let Ok(entries) = std::fs::read_dir(&self.dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let stale = entry
                .metadata()
                .and_then(|meta| meta.modified())
                .ok()
                .and_then(|modified| now.duration_since(modified).ok())
                .is_some_and(|age| age > TTL + TTL);
            let ours = path
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.contains(".json") || name.contains(".tmp"));
            if stale && ours {
                let _ = std::fs::remove_file(path);
            }
        }
    }

    /// Takes the next slot for a search that goes out, and returns how long to wait for it: none
    /// when the last one is at least [`MIN_INTERVAL`] old, otherwise the rest of the interval.
    /// Two searches starting in the same instant may both take the same slot; this spaces bursts,
    /// it is not a lock.
    pub fn reserve(&self, now: SystemTime) -> Duration {
        let stamp = self.dir.join(STAMP_FILE);
        let now_ms = unix_millis(now);
        let last = std::fs::read_to_string(&stamp)
            .ok()
            .and_then(|text| text.trim().parse::<u128>().ok())
            .unwrap_or_default();
        let interval = MIN_INTERVAL.as_millis();
        // A stamp far in the future (a clock set back) waits at most MAX_WAIT.
        let slot = (last + interval).clamp(now_ms, now_ms + MAX_WAIT.as_millis());
        let _ = std::fs::write(&stamp, slot.to_string());
        Duration::from_millis((slot - now_ms) as u64)
    }
}

/// The key an answer is stored under: everything that changes what SearXNG answers.
pub fn key(base_url: &str, query: &str, categories: &str, language: &str) -> String {
    format!(
        "{}\n{}\n{}\n{}",
        base_url.trim_end_matches('/'),
        categories,
        language,
        query.trim()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "ling-search-cache-test-{}-{name}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    // Unix only: elsewhere `Cache::at` keeps no cache, since it cannot check who owns the folder.
    #[cfg(unix)]
    #[test]
    fn an_answer_is_reused_within_the_hour_and_not_after() {
        let cache = Cache::at(scratch("ttl")).unwrap();
        let start = SystemTime::now();
        let payload = json!({"results": [{"url": "https://a"}]});
        let key = key("http://127.0.0.1:8888/", "lisbon tram", "general", "en");
        assert_eq!(cache.get(&key, start), None);
        cache.put(&key, &payload, start);
        assert_eq!(
            cache.get(&key, start + Duration::from_secs(60)),
            Some(payload)
        );
        assert_eq!(cache.get(&key, start + TTL), None);
        // Another query, or the same one for another instance, is another entry.
        assert_eq!(
            cache.get(
                &super::key("http://127.0.0.1:8888", "lisbon", "general", "en"),
                start
            ),
            None
        );
        assert_eq!(
            cache.get(
                &super::key("http://10.0.0.2:8888", "lisbon tram", "general", "en"),
                start
            ),
            None
        );
        let _ = std::fs::remove_dir_all(cache.dir());
    }

    // Unix only: elsewhere `Cache::at` keeps no cache, since it cannot check who owns the folder.
    #[cfg(unix)]
    #[test]
    fn searches_that_go_out_are_spaced() {
        let cache = Cache::at(scratch("spacing")).unwrap();
        let start = SystemTime::now();
        assert_eq!(cache.reserve(start), Duration::ZERO);
        assert_eq!(cache.reserve(start), MIN_INTERVAL);
        assert_eq!(cache.reserve(start), MIN_INTERVAL * 2);
        assert_eq!(cache.reserve(start + MIN_INTERVAL * 10), Duration::ZERO);
        let _ = std::fs::remove_dir_all(cache.dir());
    }

    #[cfg(unix)]
    #[test]
    fn a_directory_others_can_write_is_not_used() {
        use std::os::unix::fs::PermissionsExt;
        let dir = scratch("open");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o777)).unwrap();
        assert!(Cache::at(dir.clone()).is_none());
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        assert!(Cache::at(dir.clone()).is_some());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn the_cache_can_be_turned_off() {
        assert!(Cache::open_from(Some("off"), None, None, None).is_none());
        assert!(Cache::open_from(Some(" "), None, None, None).is_none());
    }

    #[cfg(unix)]
    #[test]
    fn a_directory_its_group_can_write_is_not_used() {
        use std::os::unix::fs::PermissionsExt;
        let dir = scratch("group");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o770)).unwrap();
        assert!(Cache::at(dir.clone()).is_none());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[cfg(unix)]
    #[test]
    fn a_symbolic_link_to_a_directory_is_not_used() {
        let target = scratch("link-target");
        let link = scratch("link");
        assert!(Cache::at(target.clone()).is_some());
        std::os::unix::fs::symlink(&target, &link).unwrap();
        assert!(Cache::at(link.clone()).is_none());
        let _ = std::fs::remove_file(link);
        let _ = std::fs::remove_dir_all(target);
    }

    #[cfg(unix)]
    #[test]
    fn an_entry_stored_for_another_key_is_not_returned() {
        let cache = Cache::at(scratch("collision")).unwrap();
        let now = SystemTime::now();
        let stored = key("http://127.0.0.1:8888", "lisbon tram", "general", "en");
        let asked = key("http://127.0.0.1:8888", "porto tram", "general", "en");
        cache.put(&stored, &json!({"results": []}), now);
        // Two keys whose hashes collide share a file; the key kept inside tells them apart.
        std::fs::copy(cache.entry(&stored), cache.entry(&asked)).unwrap();
        assert_eq!(cache.get(&asked, now), None);
        let _ = std::fs::remove_dir_all(cache.dir());
    }
}
