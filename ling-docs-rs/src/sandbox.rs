//! The extraction and embedding sandbox (spec §7.1, §10.2): bwrap with an empty network
//! namespace, the home directory hidden, `/run` hidden (the Docker socket and the user's systemd
//! bus live there, and a Unix socket on a read-only bind is still connectable), and nothing
//! writable except the run's scratch directory and the paths a run names explicitly (the
//! collection's database for the embedder). A copy of `ling-code-rs/src/index/sandbox.rs`.
//!
//! The argument list is built by a pure function, so its safety properties are tested on the list
//! itself: tmpfs mounts come before every bind (bwrap hides a bind made under a later tmpfs), and
//! no read-write bind lies outside the allowed set.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// What one indexer may see and write.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Spec {
    /// The user's home directory: replaced by an empty tmpfs.
    pub home: PathBuf,
    /// The run's own directory: the only place the indexer writes by default.
    pub scratch: PathBuf,
    /// Bound back read-only after the tmpfs mounts: the source, toolchains, caches, tools.
    pub read_only: Vec<PathBuf>,
    /// Bound read-write. Each must be under `scratch`, or listed in `writable_outside_scratch`.
    pub writable: Vec<PathBuf>,
    /// Read-write binds allowed outside scratch, with the reason (codebase-memory's own cache).
    pub writable_outside_scratch: Vec<(PathBuf, String)>,
    pub env: Vec<(String, String)>,
    pub cwd: PathBuf,
    pub argv: Vec<String>,
}

/// The bwrap command line for a spec.
pub fn bwrap_args(spec: &Spec) -> Vec<String> {
    let s = |p: &Path| p.to_string_lossy().into_owned();
    let mut args: Vec<String> = [
        "bwrap", "--die-with-parent", "--new-session", "--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
    ]
    .iter()
    .map(|a| a.to_string())
    .collect();
    // Every tmpfs first: a bind made before a tmpfs that covers it would be hidden again.
    for dir in ["/tmp", "/run", "/var/tmp", "/dev/shm"] {
        args.extend(["--tmpfs".to_string(), dir.to_string()]);
    }
    args.extend(["--tmpfs".to_string(), s(&spec.home)]);
    // Then the binds, parents before children, read-only before read-write at the same depth.
    let mut binds: Vec<(PathBuf, bool)> = spec.read_only.iter().map(|p| (p.clone(), false)).collect();
    binds.push((spec.scratch.clone(), true));
    binds.extend(spec.writable.iter().map(|p| (p.clone(), true)));
    binds.extend(spec.writable_outside_scratch.iter().map(|(p, _)| (p.clone(), true)));
    binds.sort_by_key(|(p, rw)| (p.components().count(), *rw));
    binds.dedup_by(|a, b| a.0 == b.0 && a.1 == b.1);
    for (path, writable) in binds {
        let flag = if writable { "--bind" } else { "--ro-bind" };
        args.extend([flag.to_string(), s(&path), s(&path)]);
    }
    args.push("--clearenv".to_string());
    for (key, value) in &spec.env {
        args.extend(["--setenv".to_string(), key.clone(), value.clone()]);
    }
    args.extend(["--chdir".to_string(), s(&spec.cwd), "--".to_string()]);
    args.extend(spec.argv.iter().cloned());
    args
}

/// The read-write binds of an argument list that are neither the scratch directory (or under it)
/// nor explicitly allowed. Empty for a safe list.
pub fn unexpected_writable(args: &[String], spec: &Spec) -> Vec<String> {
    let allowed: Vec<&PathBuf> = spec.writable_outside_scratch.iter().map(|(p, _)| p).collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--" {
            break;
        }
        if args[i] == "--bind" {
            let path = PathBuf::from(&args[i + 1]);
            if !path.starts_with(&spec.scratch) && !allowed.contains(&&path) {
                out.push(args[i + 1].clone());
            }
            i += 3;
            continue;
        }
        i += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> Spec {
        Spec {
            home: "/home/u".into(),
            scratch: "/tmp/mightling-index-x".into(),
            read_only: vec!["/home/u/.cargo".into(), "/home/u/.rustup".into(), "/home/u/src/repo".into()],
            writable: vec!["/tmp/mightling-index-x/out".into()],
            writable_outside_scratch: vec![],
            env: vec![("PATH".into(), "/usr/bin".into())],
            cwd: "/home/u/src/repo".into(),
            argv: vec!["rust-analyzer".into(), "scip".into(), ".".into()],
        }
    }

    #[test]
    fn nothing_writable_outside_scratch() {
        let spec = spec();
        let args = bwrap_args(&spec);
        assert!(unexpected_writable(&args, &spec).is_empty(), "{args:?}");
        let mut bad = spec.clone();
        bad.writable.push("/home/u/.cargo".into());
        assert_eq!(unexpected_writable(&bwrap_args(&bad), &bad), vec!["/home/u/.cargo".to_string()]);
    }

    #[test]
    fn tmpfs_mounts_come_before_every_bind() {
        let args = bwrap_args(&spec());
        let last_tmpfs = args.iter().rposition(|a| a == "--tmpfs").unwrap();
        // The only bind before the tmpfs mounts is the read-only root `/ /` itself.
        let early: Vec<usize> = args[..last_tmpfs].iter().enumerate().filter(|(_, a)| *a == "--bind" || *a == "--ro-bind").map(|(i, _)| i).collect();
        assert_eq!(early.len(), 1, "{args:?}");
        assert_eq!(&args[early[0] + 1..early[0] + 3], ["/", "/"]);
        // So the scratch directory under /tmp and the toolchain under $HOME stay visible.
        let scratch = args.iter().position(|a| a == "/tmp/mightling-index-x").unwrap();
        let cargo = args.iter().position(|a| a == "/home/u/.cargo").unwrap();
        assert!(scratch > last_tmpfs && cargo > last_tmpfs);
    }

    #[test]
    fn no_network_no_run_no_home() {
        let args = bwrap_args(&spec());
        assert!(args.contains(&"--unshare-net".to_string()));
        let tmpfs: Vec<&String> = args.windows(2).filter(|w| w[0] == "--tmpfs").map(|w| &w[1]).collect();
        for hidden in ["/run", "/tmp", "/home/u"] {
            assert!(tmpfs.iter().any(|t| *t == hidden), "{hidden} not hidden: {args:?}");
        }
        assert!(args.contains(&"--clearenv".to_string()));
    }
}
