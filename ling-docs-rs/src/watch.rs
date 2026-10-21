//! The change watch (spec §9, Phase 2): inotify as an accelerator of the periodic scan, never
//! the source of truth.
//!
//! The session watches the folders of every enabled collection, the ones discovery would enter
//! (hidden, package, build and secret folders are left out, symlinks are not followed), within a
//! budget of watches: a quarter of `fs.inotify.max_user_watches`, at most [`MAX_WATCHES`], so an
//! editor or a build tool beside it keeps the rest. A file of a kind the index reads that is
//! written, moved or deleted, or a folder that comes or goes, marks its collection; once it has
//! been quiet for [`QUIET`] (or changing for [`LONGEST`]), the session asks for an index run of
//! that collection, which is the ordinary change scan. Whatever the watch misses (a folder past
//! the budget, an overflowed queue, a network file system) the periodic scan still finds.

use std::collections::HashMap;
use std::ffi::CString;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::collections::{secret_dirs, Collection};
use crate::discover::{self, Kind};

/// Watches one session takes at most.
pub const MAX_WATCHES: usize = 65_536;
/// A collection is asked for once it has been quiet this long…
pub const QUIET: Duration = Duration::from_secs(15);
/// …or has kept changing this long.
pub const LONGEST: Duration = Duration::from_secs(120);

const MASK: u32 = libc::IN_CLOSE_WRITE | libc::IN_MOVED_TO | libc::IN_MOVED_FROM | libc::IN_DELETE | libc::IN_CREATE | libc::IN_DELETE_SELF | libc::IN_ONLYDIR;

/// The kernel's watch limit for this user, when it can be read.
pub fn max_user_watches() -> Option<usize> {
    std::fs::read_to_string("/proc/sys/fs/inotify/max_user_watches").ok()?.trim().parse().ok()
}

/// The watches this session may take.
pub fn budget() -> usize {
    max_user_watches().map(|n| n / 4).unwrap_or(8192).min(MAX_WATCHES)
}

/// An inotify instance over the collections' folders.
pub struct Watcher {
    fd: libc::c_int,
    /// Watch descriptor → collection and folder.
    watches: HashMap<i32, (String, PathBuf)>,
    budget: usize,
    /// Collections with changes not yet asked for: first and last change.
    dirty: HashMap<String, (Instant, Instant)>,
    /// Folders left unwatched for the budget.
    pub over_budget: usize,
}

impl Drop for Watcher {
    fn drop(&mut self) {
        unsafe { libc::close(self.fd) };
    }
}

impl Watcher {
    /// None where inotify is unavailable; the periodic scan then does all the work.
    pub fn new(budget: usize) -> Option<Watcher> {
        let fd = unsafe { libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC) };
        (fd >= 0).then(|| Watcher { fd, watches: HashMap::new(), budget, dirty: HashMap::new(), over_budget: 0 })
    }

    pub fn watched(&self) -> usize {
        self.watches.len()
    }

    fn add(&mut self, collection: &str, dir: &Path) -> bool {
        if self.watches.len() >= self.budget {
            self.over_budget += 1;
            return false;
        }
        let Ok(path) = CString::new(dir.as_os_str().as_bytes()) else { return false };
        let wd = unsafe { libc::inotify_add_watch(self.fd, path.as_ptr(), MASK | libc::IN_DONT_FOLLOW) };
        if wd < 0 {
            return false;
        }
        self.watches.insert(wd, (collection.to_string(), dir.to_path_buf()));
        true
    }

    /// Watches `dir` and the folders under it that discovery enters.
    fn add_tree(&mut self, collection: &str, dir: &Path, home: &Path) {
        let secret: Vec<PathBuf> = secret_dirs(dir, home);
        let mut stack = vec![dir.to_path_buf()];
        while let Some(dir) = stack.pop() {
            if !self.add(collection, &dir) {
                if self.watches.len() >= self.budget {
                    return;
                }
                continue;
            }
            let Ok(entries) = std::fs::read_dir(&dir) else { continue };
            for entry in entries.flatten() {
                let Ok(kind) = entry.file_type() else { continue };
                let path = entry.path();
                let name = entry.file_name().to_string_lossy().into_owned();
                if kind.is_dir() && discover::entered(&name, &path) && !secret.iter().any(|s| path.starts_with(s)) {
                    stack.push(path);
                }
            }
        }
    }

    /// Watches every enabled collection afresh (after `docs.toml` changed).
    pub fn watch(&mut self, collections: &[Collection], home: &Path) {
        for wd in self.watches.keys().copied().collect::<Vec<_>>() {
            unsafe { libc::inotify_rm_watch(self.fd, wd) };
        }
        self.watches.clear();
        self.over_budget = 0;
        let home = home.canonicalize().unwrap_or_else(|_| home.to_path_buf());
        for collection in collections.iter().filter(|c| c.enabled) {
            if let Ok(root) = collection.root.canonicalize() {
                self.add_tree(&collection.name, &root, &home);
            }
        }
    }

    /// Reads the events that came since the last call and returns the collections due for a run.
    pub fn poll(&mut self, home: &Path) -> Vec<String> {
        let now = Instant::now();
        let mut buf = vec![0u8; 64 * 1024];
        loop {
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr() as *mut libc::c_void, buf.len()) };
            if n <= 0 {
                break;
            }
            let mut at = 0usize;
            let header = std::mem::size_of::<libc::inotify_event>();
            while at + header <= n as usize {
                // SAFETY: the kernel writes whole events; the header is read unaligned.
                let event: libc::inotify_event = unsafe { std::ptr::read_unaligned(buf[at..].as_ptr() as *const libc::inotify_event) };
                let name_bytes = &buf[at + header..at + header + event.len as usize];
                let name = String::from_utf8_lossy(name_bytes.split(|b| *b == 0).next().unwrap_or(&[])).into_owned();
                at += header + event.len as usize;
                if event.mask & libc::IN_Q_OVERFLOW != 0 {
                    // Lost events: every collection is scanned.
                    let all: Vec<String> = self.watches.values().map(|(c, _)| c.clone()).collect();
                    for collection in all {
                        self.mark(&collection, now);
                    }
                    continue;
                }
                let Some((collection, dir)) = self.watches.get(&event.wd).cloned() else { continue };
                if event.mask & libc::IN_IGNORED != 0 {
                    self.watches.remove(&event.wd);
                    continue;
                }
                let is_dir = event.mask & libc::IN_ISDIR != 0;
                if is_dir {
                    if !name.is_empty() && !discover::entered(&name, &dir.join(&name)) {
                        continue;
                    }
                    if event.mask & (libc::IN_CREATE | libc::IN_MOVED_TO) != 0 {
                        let home = home.canonicalize().unwrap_or_else(|_| home.to_path_buf());
                        self.add_tree(&collection, &dir.join(&name), &home);
                    }
                    self.mark(&collection, now);
                } else if event.mask & libc::IN_DELETE_SELF != 0 || (!name.starts_with('.') && Kind::of(&name).is_some()) {
                    self.mark(&collection, now);
                }
            }
        }
        let due: Vec<String> = self.dirty.iter().filter(|(_, (first, last))| now.duration_since(*last) >= QUIET || now.duration_since(*first) >= LONGEST).map(|(c, _)| c.clone()).collect();
        for collection in &due {
            self.dirty.remove(collection);
        }
        due
    }

    fn mark(&mut self, collection: &str, now: Instant) {
        self.dirty.entry(collection.to_string()).and_modify(|e| e.1 = now).or_insert((now, now));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_change_to_a_readable_file_marks_its_collection_once_it_is_quiet() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().canonicalize().unwrap();
        let root = home.join("Documents");
        std::fs::create_dir_all(root.join("sub")).unwrap();
        std::fs::create_dir_all(root.join("node_modules/pkg")).unwrap();
        std::fs::create_dir_all(root.join(".hidden")).unwrap();
        let Some(mut watcher) = Watcher::new(100) else { return };
        watcher.watch(&[Collection::new("documents", root.clone())], &home);
        assert_eq!(watcher.watched(), 2, "the root and sub/, not node_modules or .hidden");
        // A file the index does not read, and one in a hidden folder: nothing.
        std::fs::write(root.join("movie.mkv"), b"x").unwrap();
        std::fs::write(root.join(".hidden/a.md"), b"x").unwrap();
        std::thread::sleep(Duration::from_millis(50));
        assert!(watcher.poll(&home).is_empty());
        assert!(watcher.dirty.is_empty());
        // A note, and a new folder with a report in it.
        std::fs::write(root.join("sub/note.md"), b"# x").unwrap();
        std::fs::create_dir(root.join("new")).unwrap();
        std::thread::sleep(Duration::from_millis(50));
        assert!(watcher.poll(&home).is_empty(), "not quiet yet");
        assert!(watcher.dirty.contains_key("documents"));
        assert_eq!(watcher.watched(), 3, "the new folder is watched");
        // Once quiet, it is due, once.
        let (first, _) = watcher.dirty["documents"];
        watcher.dirty.insert("documents".into(), (first - QUIET, Instant::now() - QUIET));
        assert_eq!(watcher.poll(&home), ["documents"]);
        assert!(watcher.poll(&home).is_empty());
    }

    #[test]
    fn the_budget_bounds_the_watches() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().canonicalize().unwrap();
        let root = home.join("Documents");
        for i in 0..5 {
            std::fs::create_dir_all(root.join(format!("d{i}"))).unwrap();
        }
        let Some(mut watcher) = Watcher::new(3) else { return };
        watcher.watch(&[Collection::new("documents", root)], &home);
        assert_eq!(watcher.watched(), 3);
        assert!(watcher.over_budget >= 1);
        assert!(budget() <= MAX_WATCHES);
    }
}
