//! The host control sockets a sandboxed command at `on` is denied, found under a made-up root and
//! runtime folder: every name on the lists by its literal path, so a misspelt one leaves a socket
//! reachable and fails here.

#![cfg(unix)]

use ling_airgapped::*;
use std::path::{Path, PathBuf};

fn base(name: &str) -> PathBuf {
    let base = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join(format!("airgapped-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    std::fs::canonicalize(&base).unwrap()
}

fn file(path: &Path) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, "").unwrap();
}

#[test]
fn every_listed_socket_is_found_by_its_own_path() {
    let base = base("sockets");
    let (root, runtime) = (base.join("root"), base.join("runtime"));
    let files = [
        "run/docker.sock",
        "run/containerd/containerd.sock",
        "run/dbus/system_bus_socket",
        "var/run/dbus/system_bus_socket",
        "run/snapd.socket",
        "run/snapd-snap.socket",
    ];
    for path in files {
        file(&root.join(path));
    }
    std::fs::create_dir_all(root.join("run/podman")).unwrap();
    file(&runtime.join("bus"));
    file(&runtime.join("docker.sock"));
    let folders = ["systemd", "docker", "podman", "containerd", "mightling"];
    for name in folders {
        std::fs::create_dir_all(runtime.join(name)).unwrap();
    }
    let mut expected: Vec<PathBuf> = files
        .iter()
        .chain(&["run/podman"])
        .map(|path| root.join(path))
        .chain(
            ["bus", "docker.sock"]
                .iter()
                .chain(&folders)
                .map(|name| runtime.join(name)),
        )
        .collect();
    expected.sort();
    assert_eq!(host_sockets_in(&root, Some(&runtime)), expected);
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn a_folder_this_user_cannot_open_is_not_a_socket() {
    use std::os::unix::fs::PermissionsExt;
    let base = base("closed");
    let podman = base.join("run/podman");
    std::fs::create_dir_all(&podman).unwrap();
    std::fs::set_permissions(&podman, std::fs::Permissions::from_mode(0o000)).unwrap();
    if std::fs::read_dir(&podman).is_ok() {
        eprintln!("skipped: this user opens any folder");
    } else {
        assert_eq!(host_sockets_in(&base, None), Vec::<PathBuf>::new());
    }
    std::fs::set_permissions(&podman, std::fs::Permissions::from_mode(0o755)).unwrap();
    let _ = std::fs::remove_dir_all(&base);
}
