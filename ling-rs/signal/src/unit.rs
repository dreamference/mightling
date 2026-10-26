//! The system unit (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §3): the bridge runs as its own account,
//! with no access to home folders and a fixed memory budget.
//!
//! One node can hold up to four bridges, one per Signal account (§18): each is an [`Instance`] with
//! its own system account, state folder, unit and `ling web` device. The first, `default`, keeps the
//! names a single bridge always had (`mightling-signal`, `/var/lib/mightling-signal`,
//! `mightling-signal.service`), so a node set up before §18 is untouched; the others run from the
//! template unit `mightling-signal@.service` as `mightling-signal-<name>`.

pub const UNIT_NAME: &str = "mightling-signal.service";
pub const ACCOUNT: &str = "mightling-signal";
pub const BRIDGE_PATH: &str = "/usr/local/lib/mightling/ling-signal";
pub const STATE_DIR: &str = "/var/lib/mightling-signal";
pub const RUNTIME_DIR: &str = "/run/mightling-signal";
/// The daemon's exit status when Signal no longer accepts this device; the unit does not restart it.
pub const UNLINKED_EXIT: i32 = 78;

/// Where unit files are written.
pub const SYSTEMD_DIR: &str = "/etc/systemd/system";
/// The template unit every named instance runs from (§18.2).
pub const TEMPLATE_UNIT: &str = "mightling-signal@.service";
/// The first instance's name: today's single bridge.
pub const DEFAULT_INSTANCE: &str = "default";
/// Names a further instance cannot take: `default` is the first one's, and `bridge` would make
/// its `ling web` device `signal-bridge`, the first one's device name, which `ling web revoke`
/// matches by name (§18.2).
pub const RESERVED_NAMES: &[&str] = &[DEFAULT_INSTANCE, "bridge"];
/// At most this many instances on one node (§18.1, decision 7): each is a JVM under `MEMORY_MAX`.
pub const MAX_INSTANCES: usize = 4;
/// Every instance's memory cap and the JVM's heap inside it.
pub const MEMORY_MAX: &str = "768M";
pub const MEMORY_MAX_BYTES: u64 = 768 * 1024 * 1024;
pub const HEAP: &str = "256m";
/// Setup refuses an instance unless `MemAvailable` is at least this much above the existing
/// instances' caps (§18.1, decision 7).
pub const HEADROOM_BYTES: u64 = 2 * 1024 * 1024 * 1024;

/// Whether `name` can name a further instance: 1 to 12 characters, lowercase letters, digits and
/// `-`, starting with a letter, and not reserved. That keeps `mightling-signal-<name>` within
/// useradd's 32 characters and a systemd instance name with no escaping.
pub fn valid_name(name: &str) -> bool {
    (1..=12).contains(&name.len())
        && name.starts_with(|c: char| c.is_ascii_lowercase())
        && name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && !RESERVED_NAMES.contains(&name)
}

/// One bridge: one Signal account, one system account, one state folder, one unit.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Instance(String);

impl Instance {
    /// The first instance, today's single bridge.
    pub fn first() -> Instance {
        Instance(DEFAULT_INSTANCE.to_string())
    }

    /// `default`, or a valid further name; the reason otherwise.
    pub fn parse(name: &str) -> Result<Instance, String> {
        if name == DEFAULT_INSTANCE || valid_name(name) {
            Ok(Instance(name.to_string()))
        } else if RESERVED_NAMES.contains(&name) {
            Err(format!("`{name}` is reserved; choose another name."))
        } else {
            Err(format!("`{name}` is not a bridge name: 1 to 12 lowercase letters, digits or `-`, starting with a letter."))
        }
    }

    /// The instance a state folder under /var/lib belongs to: `mightling-signal` is `default`,
    /// `mightling-signal-<name>` is `<name>`; None for anything else.
    pub fn from_state_folder(folder: &str) -> Option<Instance> {
        if folder == ACCOUNT {
            return Some(Instance::first());
        }
        let name = folder.strip_prefix(ACCOUNT)?.strip_prefix('-')?;
        valid_name(name).then(|| Instance(name.to_string()))
    }

    pub fn name(&self) -> &str {
        &self.0
    }

    pub fn is_first(&self) -> bool {
        self.0 == DEFAULT_INSTANCE
    }

    /// The system account it runs as.
    pub fn account(&self) -> String {
        if self.is_first() { ACCOUNT.to_string() } else { format!("{ACCOUNT}-{}", self.0) }
    }

    /// Its private state folder (0700): the keys, `bridge.json`, the device cookie.
    pub fn state_dir(&self) -> String {
        format!("/var/lib/{}", self.account())
    }

    /// Where the running bridge writes its public `status.json`.
    pub fn runtime_dir(&self) -> String {
        format!("/run/{}", self.account())
    }

    /// The unit systemctl starts and stops.
    pub fn unit_name(&self) -> String {
        if self.is_first() { UNIT_NAME.to_string() } else { format!("mightling-signal@{}.service", self.0) }
    }

    /// The unit file it runs from: its own for `default`, the shared template otherwise.
    pub fn unit_file(&self) -> String {
        format!("{SYSTEMD_DIR}/{}", if self.is_first() { UNIT_NAME } else { TEMPLATE_UNIT })
    }

    /// That file's text.
    pub fn unit_file_text(&self) -> String {
        if self.is_first() { unit_text(MEMORY_MAX, HEAP) } else { template_unit_text(MEMORY_MAX, HEAP) }
    }

    /// Its device in `ling web devices`: `signal-bridge` for `default`, `signal-<name>` otherwise.
    pub fn device_name(&self) -> String {
        crate::agent::device_name(&self.0)
    }

    /// The name the phone shows under Linked devices.
    pub fn link_name(&self, host: &str) -> String {
        match (host.is_empty(), self.is_first()) {
            (true, true) => "Mightling".to_string(),
            (true, false) => format!("Mightling ({})", self.0),
            (false, true) => format!("Mightling ({host})"),
            (false, false) => format!("Mightling ({host}, {})", self.0),
        }
    }
}

impl std::fmt::Display for Instance {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// The first instance's unit file text. `memory_max` is a systemd size (`768M`); `heap` the JVM's
/// `-Xmx`. `setup --refresh` rewrites the live unit from this, so it stays byte for byte what a
/// single bridge always had (a test pins it).
pub fn unit_text(memory_max: &str, heap: &str) -> String {
    format!(
        "# Mightling over Signal (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md). Written by `ling signal setup`;\n\
# `ling signal remove` deletes it. The bridge runs as {ACCOUNT}, so the agent, which runs as you,\n\
# cannot read the Signal keys in {STATE_DIR}, and ProtectHome keeps the bridge out of your files.\n\
[Unit]\n\
Description=Mightling over Signal (ling signal)\n\
{}",
        service_text(ACCOUNT, STATE_DIR, RUNTIME_DIR, memory_max, heap)
    )
}

/// The template unit of every further instance (§18.2): `%i` is the instance's name, so
/// `mightling-signal@work.service` runs as `mightling-signal-work` on `/var/lib/mightling-signal-work`.
/// Otherwise it is the first instance's unit, line for line.
pub fn template_unit_text(memory_max: &str, heap: &str) -> String {
    format!(
        "# Mightling over Signal, one bridge per further Signal account (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §18).\n\
# Written by `ling signal setup --name NAME`; `ling signal remove --name NAME` of the last one deletes it.\n\
# Instance NAME runs as {ACCOUNT}-NAME, so the agent, which runs as you, cannot read its Signal keys\n\
# in /var/lib/{ACCOUNT}-NAME, nor can another instance; ProtectHome keeps the bridge out of your files.\n\
[Unit]\n\
Description=Mightling over Signal (ling signal, %i)\n\
{}",
        service_text(&format!("{ACCOUNT}-%i"), &format!("/var/lib/{ACCOUNT}-%i"), &format!("/run/{ACCOUNT}-%i"), memory_max, heap)
    )
}

/// Everything after `Description=`, shared by both units.
fn service_text(account: &str, state_dir: &str, runtime_dir: &str, memory_max: &str, heap: &str) -> String {
    let state_name = state_dir.trim_start_matches("/var/lib/");
    let runtime_name = runtime_dir.trim_start_matches("/run/");
    format!(
        "After=network-online.target\n\
Wants=network-online.target\n\
\n\
[Service]\n\
User={account}\n\
Group={account}\n\
StateDirectory={state_name}\n\
StateDirectoryMode=0700\n\
RuntimeDirectory={runtime_name}\n\
RuntimeDirectoryMode=0755\n\
ExecStart={BRIDGE_PATH} serve --state {state_dir} --runtime {runtime_dir}\n\
Environment=\"JAVA_TOOL_OPTIONS=-Xmx{heap} -XX:+UseSerialGC\"\n\
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

    /// The unit a single bridge had before §18, byte for byte: `setup --refresh` rewrites the live
    /// unit from `unit_text`, so a node set up before several accounts existed must get it unchanged
    /// (but for the quotes around `JAVA_TOOL_OPTIONS`, 2026-10-26, without which systemd dropped the
    /// collector option).
    const UNIT_BEFORE_SEVERAL_ACCOUNTS: &str = "# Mightling over Signal (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md). Written by `ling signal setup`;
# `ling signal remove` deletes it. The bridge runs as mightling-signal, so the agent, which runs as you,
# cannot read the Signal keys in /var/lib/mightling-signal, and ProtectHome keeps the bridge out of your files.
[Unit]
Description=Mightling over Signal (ling signal)
After=network-online.target
Wants=network-online.target

[Service]
User=mightling-signal
Group=mightling-signal
StateDirectory=mightling-signal
StateDirectoryMode=0700
RuntimeDirectory=mightling-signal
RuntimeDirectoryMode=0755
ExecStart=/usr/local/lib/mightling/ling-signal serve --state /var/lib/mightling-signal --runtime /run/mightling-signal
Environment=\"JAVA_TOOL_OPTIONS=-Xmx256m -XX:+UseSerialGC\"
MemoryMax=768M
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictSUIDSGID=yes
LockPersonality=yes
Restart=on-failure
RestartSec=10
# 78: Signal refused this device (unlinked from the phone, or the account registered again).
# Restarting cannot help; `ling signal status` says what to do.
RestartPreventExitStatus=78

[Install]
WantedBy=multi-user.target
";

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
            // Quoted, or systemd splits it at the space and drops `-XX:+UseSerialGC` (the journal said
            // so on every start until 2026-10-26).
            "Environment=\"JAVA_TOOL_OPTIONS=-Xmx256m -XX:+UseSerialGC\"",
            "ExecStart=/usr/local/lib/mightling/ling-signal serve --state /var/lib/mightling-signal --runtime /run/mightling-signal",
        ] {
            assert!(text.lines().any(|l| l == line), "missing {line}");
        }
        // Nothing runs as root: no prefixed Exec lines (`+`, `!`), no PermissionsStartOnly.
        assert!(!text.contains("ExecStartPre"));
        assert!(!text.contains("=+") && !text.contains("=!"));
        assert!(!text.contains("PermissionsStartOnly"));
    }

    #[test]
    fn the_first_instance_keeps_exactly_the_names_and_unit_it_had() {
        assert_eq!(unit_text(MEMORY_MAX, HEAP), UNIT_BEFORE_SEVERAL_ACCOUNTS);
        let first = Instance::first();
        assert_eq!(Instance::parse("default").unwrap(), first);
        assert_eq!(first.account(), "mightling-signal");
        assert_eq!(first.state_dir(), "/var/lib/mightling-signal");
        assert_eq!(first.runtime_dir(), "/run/mightling-signal");
        assert_eq!(first.unit_name(), "mightling-signal.service");
        assert_eq!(first.unit_file(), "/etc/systemd/system/mightling-signal.service");
        assert_eq!(first.unit_file_text(), UNIT_BEFORE_SEVERAL_ACCOUNTS);
        assert_eq!(first.device_name(), "signal-bridge");
        assert_eq!(first.link_name("gx10"), "Mightling (gx10)");
        assert_eq!(first.link_name(""), "Mightling");
        assert_eq!((first.account(), first.state_dir(), first.runtime_dir(), first.unit_name()), (ACCOUNT.to_string(), STATE_DIR.to_string(), RUNTIME_DIR.to_string(), UNIT_NAME.to_string()));
    }

    #[test]
    fn a_named_instance_has_names_of_its_own() {
        let work = Instance::parse("work").unwrap();
        assert!(!work.is_first());
        assert_eq!(work.account(), "mightling-signal-work");
        assert_eq!(work.state_dir(), "/var/lib/mightling-signal-work");
        assert_eq!(work.runtime_dir(), "/run/mightling-signal-work");
        assert_eq!(work.unit_name(), "mightling-signal@work.service");
        assert_eq!(work.unit_file(), "/etc/systemd/system/mightling-signal@.service");
        assert_eq!(work.device_name(), "signal-work");
        assert_eq!(work.link_name("gx10"), "Mightling (gx10, work)");
        // The longest name keeps the account within useradd's 32 characters.
        assert!(Instance::parse("abcdefghijkl").unwrap().account().len() <= 32);
    }

    #[test]
    fn invalid_and_reserved_names_are_refused() {
        for bad in ["", "Work", "1st", "-x", "abcdefghijklm", "a b", "a_b", "a.b", "a@b", "a/b", "bridge"] {
            assert!(Instance::parse(bad).is_err(), "{bad:?}");
            assert!(!valid_name(bad), "{bad:?}");
        }
        assert!(Instance::parse("bridge").unwrap_err().contains("reserved"));
        assert!(!valid_name("default"), "`default` names the first instance only");
        for good in ["work", "w", "home-2", "a1"] {
            assert!(Instance::parse(good).is_ok(), "{good}");
        }
    }

    #[test]
    fn state_folders_name_their_instances() {
        assert_eq!(Instance::from_state_folder("mightling-signal"), Some(Instance::first()));
        assert_eq!(Instance::from_state_folder("mightling-signal-work"), Some(Instance::parse("work").unwrap()));
        for other in ["mightling-signalx", "mightling-signal-", "mightling-signal-Bad", "mightling-signal-default", "mightling-signal-bridge", "mightling-chat", "mysql"] {
            assert_eq!(Instance::from_state_folder(other), None, "{other}");
        }
    }

    #[test]
    fn the_template_unit_is_the_first_ones_with_the_instance_name() {
        let text = template_unit_text(MEMORY_MAX, HEAP);
        for line in [
            "User=mightling-signal-%i",
            "Group=mightling-signal-%i",
            "StateDirectory=mightling-signal-%i",
            "RuntimeDirectory=mightling-signal-%i",
            "ExecStart=/usr/local/lib/mightling/ling-signal serve --state /var/lib/mightling-signal-%i --runtime /run/mightling-signal-%i",
            "Description=Mightling over Signal (ling signal, %i)",
        ] {
            assert!(text.lines().any(|l| l == line), "missing {line}");
        }
        // §3's hardening, unchanged: every line of the first unit from `After=` on is in the template,
        // with only the account and folder names differing.
        let service = |t: &str| t.split_once("After=").unwrap().1.replace("mightling-signal-%i", "mightling-signal");
        assert_eq!(service(&text), service(UNIT_BEFORE_SEVERAL_ACCOUNTS));
        // `%` appears only as `%i` (anything else would be another systemd specifier).
        assert!(text.match_indices('%').all(|(i, _)| text[i..].starts_with("%i")));
        assert!(!text.contains("ExecStartPre") && !text.contains("=+") && !text.contains("=!"));
    }
}
