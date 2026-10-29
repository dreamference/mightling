//! The user units (§7.6): `mightling-monitor.timer` runs one check every 5 minutes, bounded beside
//! the model server (512 MB, nice 10, idle I/O); `mightling-monitor-digest.timer` is written for the
//! hourly digest of phase M1 and left disabled until that phase.

use std::path::Path;
use std::path::PathBuf;

pub const CHECK_SERVICE: &str = "mightling-monitor.service";
pub const CHECK_TIMER: &str = "mightling-monitor.timer";
pub const DIGEST_SERVICE: &str = "mightling-monitor-digest.service";
pub const DIGEST_TIMER: &str = "mightling-monitor-digest.timer";

/// `~/.config/systemd/user` under a home.
pub fn unit_dir(home: &Path) -> PathBuf {
    home.join(".config/systemd/user")
}

fn exec_line(exec_prefix: &[String], command: &str) -> String {
    let mut words: Vec<String> = exec_prefix
        .iter()
        .map(|w| if w.contains(' ') { format!("\"{w}\"") } else { w.clone() })
        .collect();
    words.push(command.to_string());
    words.join(" ")
}

fn service(description: &str, exec: &str) -> String {
    format!(
        "[Unit]\nDescription={description}\n\n[Service]\nType=oneshot\nExecStart={exec}\nMemoryMax=512M\nNice=10\n\
         IOSchedulingClass=idle\nTimeoutStartSec=120\n"
    )
}

/// The four unit files, by name, for a monitor started as `exec_prefix check` (and `… digest`).
pub fn files(exec_prefix: &[String]) -> Vec<(&'static str, String)> {
    vec![
        (CHECK_SERVICE, service("Mightling session monitor: one check of the agents' transcripts", &exec_line(exec_prefix, "check"))),
        (
            CHECK_TIMER,
            "[Unit]\nDescription=Mightling session monitor, every 5 minutes\n\n[Timer]\nOnBootSec=2min\nOnUnitActiveSec=5min\n\
             AccuracySec=30s\n\n[Install]\nWantedBy=timers.target\n"
                .to_string(),
        ),
        (DIGEST_SERVICE, service("Mightling session monitor: the hourly digest (phase M1)", &exec_line(exec_prefix, "digest"))),
        (
            DIGEST_TIMER,
            "[Unit]\nDescription=Mightling session monitor digest, hourly (phase M1)\n\n[Timer]\nOnCalendar=hourly\n\
             RandomizedDelaySec=120\n\n[Install]\nWantedBy=timers.target\n"
                .to_string(),
        ),
    ]
}

/// Writes the unit files into `dir`.
pub fn write(dir: &Path, exec_prefix: &[String]) -> std::io::Result<()> {
    std::fs::create_dir_all(dir)?;
    for (name, text) in files(exec_prefix) {
        std::fs::write(dir.join(name), text)?;
    }
    Ok(())
}

/// Removes the unit files from `dir`; a missing one is fine.
pub fn remove(dir: &Path) {
    for (name, _) in files(&[]) {
        let _ = std::fs::remove_file(dir.join(name));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_check_unit_is_bounded_and_runs_every_five_minutes() {
        let units = files(&["/opt/x/ling".to_string(), "monitor".to_string()]);
        let service = &units.iter().find(|(n, _)| *n == CHECK_SERVICE).unwrap().1;
        assert!(service.contains("ExecStart=/opt/x/ling monitor check\n"));
        for bound in ["MemoryMax=512M", "Nice=10", "IOSchedulingClass=idle"] {
            assert!(service.contains(bound), "{bound}");
        }
        assert!(units.iter().find(|(n, _)| *n == CHECK_TIMER).unwrap().1.contains("OnUnitActiveSec=5min"));
        let digest = &units.iter().find(|(n, _)| *n == DIGEST_SERVICE).unwrap().1;
        assert!(digest.contains("monitor digest") && digest.contains("MemoryMax=512M"));
        let dir = std::env::temp_dir().join(format!("ling-monitor-units-{}", std::process::id()));
        write(&dir, &["/opt/x/ling-monitor".to_string()]).unwrap();
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 4);
        remove(&dir);
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 0);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_digest_unit_is_hourly_and_bounded_like_the_check() {
        let units = files(&["/opt/x/ling".to_string(), "monitor".to_string()]);
        let text = |name: &str| units.iter().find(|(n, _)| *n == name).unwrap().1.clone();
        assert!(text(DIGEST_TIMER).contains("OnCalendar=hourly"));
        let digest = text(DIGEST_SERVICE);
        assert!(digest.contains("ExecStart=/opt/x/ling monitor digest\n"));
        for bound in ["MemoryMax=512M", "Nice=10", "IOSchedulingClass=idle"] {
            assert!(digest.contains(bound), "{bound}");
        }
        // Both timers install under timers.target; only user units are written.
        for name in [CHECK_TIMER, DIGEST_TIMER] {
            assert!(text(name).contains("WantedBy=timers.target"));
        }
        assert_eq!(
            unit_dir(Path::new("/home/u")),
            PathBuf::from("/home/u/.config/systemd/user")
        );
    }
}
