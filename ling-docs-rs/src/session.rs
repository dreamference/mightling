//! `ling-docs session`: the process that keeps the collections indexed while a session lives
//! (spec §6, §9). The launcher starts it outside the agent's sandbox and continues.
//!
//! One per user: it takes `docs/session.lock` (a second one exits at once). It adds the default
//! collections when their folders exist (§14.1), starts a change scan of every collection, then
//! every few seconds drains the request queue and, every `docs_scan_interval_min`, scans again.
//! At most one `ling-docs index` runs at a time; none starts while Night Shift or a benchmark
//! holds the runner lock. It exits when its parent does; an index run in flight finishes alone.

use std::fs::File;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use anyhow::Result;

use crate::collections::DocsToml;
use crate::config::{self, Settings};
use crate::host::flock;
use crate::requests::{self, Request};

const TICK: Duration = Duration::from_secs(3);

/// The arguments of the next `ling-docs index`, taking what it covers out of `pending`.
pub fn next_run(pending: &mut Vec<Request>) -> Option<Vec<String>> {
    if pending.is_empty() {
        return None;
    }
    pending.sort();
    pending.dedup();
    if let Some(index) = pending.iter().position(|r| matches!(r, Request::Rebuild(_))) {
        let Request::Rebuild(name) = pending.remove(index) else { unreachable!() };
        pending.retain(|r| *r != Request::One(name.clone()));
        return Some(vec!["index".into(), "--collection".into(), name, "--rebuild".into()]);
    }
    if pending.contains(&Request::All) {
        pending.retain(|r| matches!(r, Request::Rebuild(_)));
        return Some(vec!["index".into()]);
    }
    let Request::One(name) = pending.remove(0) else { unreachable!() };
    Some(vec!["index".into(), "--collection".into(), name])
}

pub fn run(parent: Option<i32>) -> Result<()> {
    let settings = Settings::load();
    if !settings.enabled {
        return Ok(());
    }
    let dir = config::docs_dir();
    crate::store::create_private_dir(&dir)?;
    let lock = File::create(dir.join("session.lock"))?;
    if flock(&lock, false).is_err() {
        return Ok(()); // another session keeps the index
    }
    let mut docs = DocsToml::load()?;
    if !docs.ensure_defaults(&config::user_home()).is_empty() {
        docs.save()?;
    }
    let exe = std::env::current_exe()?;
    let interval = Duration::from_secs(settings.scan_interval_min * 60);
    let mut pending = vec![Request::All];
    let mut last_scan = Instant::now();
    let mut child: Option<Child> = None;
    loop {
        if let Some(pid) = parent {
            if unsafe { libc::kill(pid, 0) } != 0 {
                return Ok(());
            }
        }
        pending.extend(requests::drain(&dir));
        if last_scan.elapsed() >= interval {
            pending.push(Request::All);
            last_scan = Instant::now();
        }
        let busy = match child.as_mut() {
            Some(c) => c.try_wait()?.is_none(),
            None => false,
        };
        if !busy && !pending.is_empty() && !crate::index::night_shift_active(&config::night_dir()) {
            if let Some(args) = next_run(&mut pending) {
                let log = File::create(dir.join("index.log"))?;
                child = Some(spawn_detached(&exe, &args, log)?);
            }
        }
        std::thread::sleep(TICK);
    }
}

/// Starts `ling-docs <args>` in a session of its own, so it outlives this one.
pub fn spawn_detached(exe: &std::path::Path, args: &[String], log: File) -> Result<Child> {
    use std::os::unix::process::CommandExt;
    let child = unsafe {
        Command::new(exe)
            .args(args)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .pre_exec(|| {
                libc::setsid();
                Ok(())
            })
            .spawn()?
    };
    Ok(child)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requests_coalesce_into_runs() {
        let mut pending = vec![Request::One("a".into()), Request::All, Request::One("b".into())];
        assert_eq!(next_run(&mut pending), Some(vec!["index".to_string()]));
        assert!(pending.is_empty());
        let mut pending = vec![Request::One("a".into()), Request::Rebuild("a".into()), Request::One("b".into())];
        assert_eq!(next_run(&mut pending), Some(vec!["index".into(), "--collection".into(), "a".into(), "--rebuild".into()]));
        assert_eq!(next_run(&mut pending), Some(vec!["index".into(), "--collection".into(), "b".into()]));
        assert_eq!(next_run(&mut pending), None);
    }
}
