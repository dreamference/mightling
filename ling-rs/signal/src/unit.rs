//! The system unit (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §3): the bridge runs as its own account,
//! with no access to home folders and a fixed memory budget.

pub const UNIT_NAME: &str = "mightling-signal.service";
pub const ACCOUNT: &str = "mightling-signal";
pub const BRIDGE_PATH: &str = "/usr/local/lib/mightling/ling-signal";
pub const STATE_DIR: &str = "/var/lib/mightling-signal";
pub const RUNTIME_DIR: &str = "/run/mightling-signal";
/// The daemon's exit status when Signal no longer accepts this device; the unit does not restart it.
pub const UNLINKED_EXIT: i32 = 78;

/// The unit file's text. `memory_max` is a systemd size (`768M`); `heap` the JVM's `-Xmx`.
pub fn unit_text(memory_max: &str, heap: &str) -> String {
    format!(
        "# Mightling over Signal (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md). Written by `ling signal setup`;\n\
# `ling signal remove` deletes it. The bridge runs as {ACCOUNT}, so the agent, which runs as you,\n\
# cannot read the Signal keys in {STATE_DIR}, and ProtectHome keeps the bridge out of your files.\n\
[Unit]\n\
Description=Mightling over Signal (ling signal)\n\
After=network-online.target\n\
Wants=network-online.target\n\
\n\
[Service]\n\
User={ACCOUNT}\n\
Group={ACCOUNT}\n\
StateDirectory=mightling-signal\n\
StateDirectoryMode=0700\n\
RuntimeDirectory=mightling-signal\n\
RuntimeDirectoryMode=0755\n\
ExecStart={BRIDGE_PATH} serve --state {STATE_DIR} --runtime {RUNTIME_DIR}\n\
Environment=JAVA_TOOL_OPTIONS=-Xmx{heap} -XX:+UseSerialGC\n\
MemoryMax={memory_max}\n\
NoNewPrivileges=yes\n\
ProtectSystem=strict\n\
ProtectHome=yes\n\
PrivateTmp=yes\n\
PrivateDevices=yes\n\
ProtectKernelTunables=yes\n\
ProtectKernelModules=yes\n\
ProtectControlGroups=yes\n\
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX\n\
RestrictSUIDSGID=yes\n\
LockPersonality=yes\n\
Restart=on-failure\n\
RestartSec=10\n\
# 78: Signal refused this device (unlinked from the phone, or the account registered again).\n\
# Restarting cannot help; `ling signal status` says what to do.\n\
RestartPreventExitStatus=78\n\
\n\
[Install]\n\
WantedBy=multi-user.target\n"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_unit_runs_as_its_own_account_out_of_home_folders() {
        let text = unit_text("768M", "256m");
        for line in [
            "User=mightling-signal",
            "ProtectHome=yes",
            "MemoryMax=768M",
            "StateDirectoryMode=0700",
            "NoNewPrivileges=yes",
            "RestartPreventExitStatus=78",
            "Environment=JAVA_TOOL_OPTIONS=-Xmx256m -XX:+UseSerialGC",
            "ExecStart=/usr/local/lib/mightling/ling-signal serve --state /var/lib/mightling-signal --runtime /run/mightling-signal",
        ] {
            assert!(text.lines().any(|l| l == line), "missing {line}");
        }
        // Nothing runs as root: no prefixed Exec lines (`+`, `!`), no PermissionsStartOnly.
        assert!(!text.contains("ExecStartPre"));
        assert!(!text.contains("=+") && !text.contains("=!"));
        assert!(!text.contains("PermissionsStartOnly"));
    }
}
