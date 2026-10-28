//! Class `W1` (`watch`): waits for one condition from a closed list and returns once
//! with `{met, observed, at}`, using no model (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §4.2, §5).

use std::fmt;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use chrono::{SecondsFormat, Utc};
use regex_lite::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Default maximum wait duration: 30 minutes (`1,800` seconds, spec §4.2).
pub const DEFAULT_TIMEOUT_S: u64 = 1_800;

/// Hard maximum wait duration: 4 hours (`14,400` seconds, spec §4.2).
pub const MAX_TIMEOUT_S: u64 = 14_400;

/// Default poll interval for local file, PID, Night Shift task file, and loopback HTTP checks (spec §4.2).
pub const DEFAULT_FAST_POLL_S: u64 = 1;

/// Minimum poll interval for subprocess/command conditions (`gh_run`, `systemd_unit`, `command`, `git_ref`, spec §4.2).
pub const MIN_COMMAND_POLL_S: u64 = 15;

/// Maximum bytes read from a watched file on a single check (64 MiB).
pub const MAX_FILE_BYTES: u64 = 64 * 1024 * 1024;

/// Maximum characters returned in `observed` (~1,500 tokens, spec §5.1).
pub const MAX_OBSERVED_CHARS: usize = 6_000;

/// Closed list of canonical condition kinds accepted by `watch`.
pub const CLOSED_KINDS: &[&str] = &[
    "file_line",
    "pid_exit",
    "http_status",
    "gh_run",
    "systemd_unit",
    "command",
    "night_task",
    "git_ref",
    "timeout",
];

/// Terminal Night Shift task statuses (`ling-rs/src/night.rs`).
pub const FINISHED_NIGHT_STATUSES: &[&str] = &[
    "done",
    "no-change",
    "stalled",
    "failed",
    "interrupted",
    "cancelled",
];

/// Closed enum of condition kinds supported by `watch`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    /// A file or log line matching a literal substring or regular expression.
    FileLine,
    /// A process (by PID) exiting.
    PidExit,
    /// An HTTP endpoint on loopback returning a target HTTP status code.
    HttpStatus,
    /// A GitHub Actions run finishing (`gh run view <id> --json status,conclusion`).
    GhRun,
    /// A systemd user unit reaching a target state (`systemctl --user is-active <unit>`).
    SystemdUnit,
    /// An allowlisted command reaching an exit code or printing a matching line.
    Command,
    /// A Night Shift task under `$CODEX_HOME/night/tasks/<id>.json` reaching a finished status.
    NightTask,
    /// A git ref moving from its previous commit hash.
    GitRef,
    /// A bounded timer wait.
    Timeout,
}

impl Kind {
    /// Parses a condition kind name from the closed list (including documented short aliases).
    pub fn parse(raw: &str) -> Result<Self, WatchError> {
        match raw.trim() {
            "file_line" | "file" | "log_line" => Ok(Self::FileLine),
            "pid_exit" | "pid" | "process_exit" => Ok(Self::PidExit),
            "http_status" | "http" => Ok(Self::HttpStatus),
            "gh_run" => Ok(Self::GhRun),
            "systemd_unit" | "unit" => Ok(Self::SystemdUnit),
            "command" => Ok(Self::Command),
            "night_task" | "night" | "task" => Ok(Self::NightTask),
            "git_ref" => Ok(Self::GitRef),
            "timeout" => Ok(Self::Timeout),
            other => Err(WatchError::UnknownKind(other.to_string())),
        }
    }

    /// Canonical snake_case identifier for this kind.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::FileLine => "file_line",
            Self::PidExit => "pid_exit",
            Self::HttpStatus => "http_status",
            Self::GhRun => "gh_run",
            Self::SystemdUnit => "systemd_unit",
            Self::Command => "command",
            Self::NightTask => "night_task",
            Self::GitRef => "git_ref",
            Self::Timeout => "timeout",
        }
    }

    /// Minimum poll interval in seconds for this condition kind.
    pub fn min_poll_interval_s(self) -> u64 {
        match self {
            Self::FileLine | Self::PidExit | Self::HttpStatus | Self::NightTask | Self::Timeout => {
                DEFAULT_FAST_POLL_S
            }
            Self::GhRun | Self::SystemdUnit | Self::Command | Self::GitRef => MIN_COMMAND_POLL_S,
        }
    }
}

/// Raw request payload accepted by `watch` (from MCP JSON arguments or CLI flags).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct WatchRequest {
    /// Condition kind from [`CLOSED_KINDS`].
    pub kind: String,
    /// Generic target string (`path`, `pid`, `url`, `run_id`, `unit`, `command`, `id`, `ref`, or seconds).
    #[serde(default)]
    pub target: Option<String>,
    /// Line pattern to match (`file_line`, `command`) or expected status/state when passed via `pattern`.
    #[serde(default)]
    pub pattern: Option<String>,
    /// Whether `pattern` should be compiled as a regular expression (`regex-lite`) rather than a substring.
    #[serde(default)]
    pub regex: bool,
    /// Explicit file path for `file_line`.
    #[serde(default)]
    pub path: Option<String>,
    /// Explicit process ID for `pid_exit`.
    #[serde(default)]
    pub pid: Option<i32>,
    /// Explicit URL for `http_status`.
    #[serde(default)]
    pub url: Option<String>,
    /// Expected HTTP status code for `http_status` (default `200`).
    #[serde(default)]
    pub status: Option<u16>,
    /// Explicit GitHub Actions run ID for `gh_run`.
    #[serde(default, alias = "run")]
    pub run_id: Option<String>,
    /// Optional `owner/repo` for `gh_run` or repository directory for `git_ref`.
    #[serde(default)]
    pub repo: Option<String>,
    /// Explicit systemd unit name for `systemd_unit`.
    #[serde(default)]
    pub unit: Option<String>,
    /// Expected systemd unit state for `systemd_unit` (default `"active"`).
    #[serde(default)]
    pub state: Option<String>,
    /// Explicit allowlisted command string for `command`.
    #[serde(default)]
    pub command: Option<String>,
    /// Expected exit code for `command`.
    #[serde(default)]
    pub exit_code: Option<i32>,
    /// Explicit Night Shift task ID for `night_task`.
    #[serde(default, alias = "task_id")]
    pub id: Option<String>,
    /// Explicit git ref name for `git_ref`.
    #[serde(default, rename = "ref", alias = "git_ref")]
    pub git_ref: Option<String>,
    /// Baseline git commit hash for `git_ref` (if omitted, the first observed hash is used).
    #[serde(default)]
    pub from: Option<String>,
    /// Maximum wait duration in seconds (default `1800`, hard max `14400`).
    #[serde(default)]
    pub timeout_s: Option<u64>,
    /// Poll interval in seconds (clamped to [`Kind::min_poll_interval_s`]).
    #[serde(default, alias = "interval_s")]
    pub poll_interval_s: Option<u64>,
}

/// Validated, self-contained condition ready for execution.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ValidatedCondition {
    FileLine {
        path: PathBuf,
        pattern: String,
        is_regex: bool,
    },
    PidExit {
        pid: i32,
    },
    HttpStatus {
        url: String,
        host_port: (String, u16),
        request_path: String,
        expected_status: u16,
    },
    GhRun {
        run_id: String,
        repo: Option<String>,
    },
    SystemdUnit {
        unit: String,
        expected_state: String,
    },
    Command {
        argv: Vec<String>,
        expected_exit: Option<i32>,
        pattern: Option<String>,
        is_regex: bool,
    },
    NightTask {
        id: String,
    },
    GitRef {
        git_ref: String,
        from: Option<String>,
        repo: Option<PathBuf>,
    },
    Timeout {
        duration_s: u64,
    },
}

/// Validated watch job with bounded timeout and poll interval.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedWatch {
    pub kind: Kind,
    pub condition: ValidatedCondition,
    pub timeout_s: u64,
    pub poll_interval_s: u64,
}

/// Single return object from `watch` (`{met, observed, at}`, spec §4.2).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WatchResult {
    pub met: bool,
    pub observed: String,
    pub at: String,
}

/// Errors produced when validating or executing a watch request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WatchError {
    /// Condition kind is not in [`CLOSED_KINDS`].
    UnknownKind(String),
    /// Missing or invalid parameter for the chosen condition kind.
    InvalidArgument(String),
    /// Requested `timeout_s` exceeds [`MAX_TIMEOUT_S`] or is zero.
    TimeoutOutOfRange(u64),
    /// Command or URL is not on the allowlist.
    NotAllowed(String),
}

impl fmt::Display for WatchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnknownKind(k) => write!(
                f,
                "unknown watch condition kind {:?}; allowed kinds (closed list): {}",
                k,
                CLOSED_KINDS.join(", ")
            ),
            Self::InvalidArgument(msg) => write!(f, "invalid watch argument: {msg}"),
            Self::TimeoutOutOfRange(t) => write!(
                f,
                "watch timeout_s ({t}s) is out of range; must be between 1s and {MAX_TIMEOUT_S}s (4h)"
            ),
            Self::NotAllowed(msg) => write!(f, "watch target not allowed: {msg}"),
        }
    }
}

impl std::error::Error for WatchError {}

impl WatchRequest {
    /// Validates the request against the closed condition list, allowlists, and resource bounds.
    pub fn validate(&self) -> Result<ValidatedWatch, WatchError> {
        let kind = Kind::parse(&self.kind)?;

        let timeout_s = match self.timeout_s {
            Some(0) => return Err(WatchError::TimeoutOutOfRange(0)),
            Some(t) if t > MAX_TIMEOUT_S => return Err(WatchError::TimeoutOutOfRange(t)),
            Some(t) => t,
            None => DEFAULT_TIMEOUT_S,
        };

        let min_poll = kind.min_poll_interval_s();
        let poll_interval_s = self
            .poll_interval_s
            .unwrap_or(min_poll)
            .max(min_poll)
            .min(timeout_s.max(1));

        let condition = match kind {
            Kind::FileLine => {
                let raw_path = self
                    .path
                    .as_deref()
                    .or(self.target.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| {
                        WatchError::InvalidArgument(
                            "file_line requires `path` (or `target`)".to_string(),
                        )
                    })?;
                let pattern = self
                    .pattern
                    .as_deref()
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| {
                        WatchError::InvalidArgument(
                            "file_line requires non-empty `pattern`".to_string(),
                        )
                    })?
                    .to_string();
                if self.regex {
                    Regex::new(&pattern).map_err(|e| {
                        WatchError::InvalidArgument(format!("invalid regex pattern: {e}"))
                    })?;
                }
                ValidatedCondition::FileLine {
                    path: PathBuf::from(raw_path),
                    pattern,
                    is_regex: self.regex,
                }
            }
            Kind::PidExit => {
                let pid = match self.pid {
                    Some(p) => p,
                    None => {
                        let raw = self
                            .target
                            .as_deref()
                            .map(str::trim)
                            .filter(|s| !s.is_empty())
                            .ok_or_else(|| {
                                WatchError::InvalidArgument(
                                    "pid_exit requires `pid` (or `target`)".to_string(),
                                )
                            })?;
                        raw.parse::<i32>().map_err(|_| {
                            WatchError::InvalidArgument(format!("invalid pid {raw:?}"))
                        })?
                    }
                };
                if pid <= 0 {
                    return Err(WatchError::InvalidArgument(format!(
                        "pid must be a positive integer, got {pid}"
                    )));
                }
                ValidatedCondition::PidExit { pid }
            }
            Kind::HttpStatus => {
                let raw_url = self
                    .url
                    .as_deref()
                    .or(self.target.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| {
                        WatchError::InvalidArgument(
                            "http_status requires `url` (or `target`)".to_string(),
                        )
                    })?;
                let (host, port, request_path) = parse_and_check_watch_url(raw_url)?;
                let expected_status = match self.status {
                    Some(code) => code,
                    None => match self.pattern.as_deref().map(str::trim) {
                        Some(s) if !s.is_empty() => s.parse::<u16>().map_err(|_| {
                            WatchError::InvalidArgument(format!("invalid HTTP status code {s:?}"))
                        })?,
                        _ => 200,
                    },
                };
                if !(100..=599).contains(&expected_status) {
                    return Err(WatchError::InvalidArgument(format!(
                        "HTTP status code must be in 100..=599, got {expected_status}"
                    )));
                }
                ValidatedCondition::HttpStatus {
                    url: raw_url.to_string(),
                    host_port: (host, port),
                    request_path,
                    expected_status,
                }
            }
            Kind::GhRun => {
                let run_id = self
                    .run_id
                    .as_deref()
                    .or(self.target.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| {
                        WatchError::InvalidArgument(
                            "gh_run requires `run_id` (or `target`)".to_string(),
                        )
                    })?;
                if !run_id.chars().all(|c| c.is_ascii_digit()) {
                    return Err(WatchError::InvalidArgument(format!(
                        "gh_run `run_id` must be numeric digits, got {run_id:?}"
                    )));
                }
                let repo = match self.repo.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
                    Some(r) => {
                        validate_repo_slug(r)?;
                        Some(r.to_string())
                    }
                    None => None,
                };
                ValidatedCondition::GhRun {
                    run_id: run_id.to_string(),
                    repo,
                }
            }
            Kind::SystemdUnit => {
                let unit = self
                    .unit
                    .as_deref()
                    .or(self.target.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| {
                        WatchError::InvalidArgument(
                            "systemd_unit requires `unit` (or `target`)".to_string(),
                        )
                    })?;
                validate_unit_name(unit)?;
                let expected_state = self
                    .state
                    .as_deref()
                    .or(self.pattern.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .unwrap_or("active")
                    .to_string();
                ValidatedCondition::SystemdUnit {
                    unit: unit.to_string(),
                    expected_state,
                }
            }
            Kind::Command => {
                let raw_cmd = self
                    .command
                    .as_deref()
                    .or(self.target.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| {
                        WatchError::InvalidArgument(
                            "command requires `command` (or `target`)".to_string(),
                        )
                    })?;
                let argv = parse_and_check_allowlisted_command(raw_cmd)?;
                let pattern = self.pattern.clone().filter(|s| !s.is_empty());
                if self.regex {
                    if let Some(ref p) = pattern {
                        Regex::new(p).map_err(|e| {
                            WatchError::InvalidArgument(format!("invalid regex pattern: {e}"))
                        })?;
                    }
                }
                let expected_exit = match (self.exit_code, &pattern) {
                    (Some(code), _) => Some(code),
                    (None, None) => Some(0),
                    (None, Some(_)) => None,
                };
                ValidatedCondition::Command {
                    argv,
                    expected_exit,
                    pattern,
                    is_regex: self.regex,
                }
            }
            Kind::NightTask => {
                let id = self
                    .id
                    .as_deref()
                    .or(self.target.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| {
                        WatchError::InvalidArgument(
                            "night_task requires `id` (or `target`)".to_string(),
                        )
                    })?;
                validate_night_id(id)?;
                ValidatedCondition::NightTask { id: id.to_string() }
            }
            Kind::GitRef => {
                let git_ref = self
                    .git_ref
                    .as_deref()
                    .or(self.target.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| {
                        WatchError::InvalidArgument(
                            "git_ref requires `ref` (or `target`)".to_string(),
                        )
                    })?;
                validate_git_ref_name(git_ref)?;
                let from = self
                    .from
                    .as_deref()
                    .or(self.pattern.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(ToString::to_string);
                let repo = self
                    .repo
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(PathBuf::from);
                ValidatedCondition::GitRef {
                    git_ref: git_ref.to_string(),
                    from,
                    repo,
                }
            }
            Kind::Timeout => {
                let duration_s = match self.target.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
                    Some(raw) => {
                        let parsed = raw.parse::<u64>().map_err(|_| {
                            WatchError::InvalidArgument(format!(
                                "timeout target must be seconds, got {raw:?}"
                            ))
                        })?;
                        if parsed == 0 || parsed > MAX_TIMEOUT_S {
                            return Err(WatchError::TimeoutOutOfRange(parsed));
                        }
                        parsed
                    }
                    None => timeout_s,
                };
                ValidatedCondition::Timeout { duration_s }
            }
        };

        Ok(ValidatedWatch {
            kind,
            condition,
            timeout_s,
            poll_interval_s,
        })
    }
}

/// Output of a command run by a [`Probe`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandOutput {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// Abstraction over operating-system probes and clock for deterministic unit testing.
pub trait Probe {
    /// Current wall-clock time in seconds (monotonic or epoch seconds).
    fn now_s(&mut self) -> u64;

    /// RFC 3339 UTC timestamp string for `WatchResult.at`.
    fn timestamp_rfc3339(&mut self) -> String {
        Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
    }

    /// Sleeps for `seconds`.
    fn sleep_s(&mut self, seconds: u64);

    /// Reads lines of `path` (bounded by [`MAX_FILE_BYTES`]).
    fn read_file_lines(&mut self, path: &Path) -> Result<Vec<String>, String>;

    /// Returns `true` if process `pid` is still alive, `false` if it has exited.
    fn is_pid_alive(&mut self, pid: i32) -> bool;

    /// Performs an HTTP GET against `(host, port)` and `request_path`, returning the status code.
    fn http_status(
        &mut self,
        host: &str,
        port: u16,
        request_path: &str,
        url: &str,
    ) -> Result<u16, String>;

    /// Queries `gh run view <run_id> [--repo <repo>] --json status,conclusion`.
    fn gh_run_view(&mut self, run_id: &str, repo: Option<&str>) -> Result<CommandOutput, String>;

    /// Queries `systemctl --user is-active <unit>`.
    fn systemd_unit_state(&mut self, unit: &str) -> Result<CommandOutput, String>;

    /// Runs an allowlisted command (`argv`).
    fn run_command(&mut self, argv: &[String]) -> Result<CommandOutput, String>;

    /// Reads the `status` field of `$CODEX_HOME/night/tasks/<id>.json`.
    fn night_task_status(&mut self, id: &str) -> Result<Option<String>, String>;

    /// Resolves `git rev-parse --verify <git_ref>` in `repo` (or current directory).
    fn git_rev_parse(&mut self, git_ref: &str, repo: Option<&Path>) -> Result<String, String>;
}

/// Real operating-system probe used in production (`ling delegate mcp` and `ling delegate watch`).
pub struct SystemProbe {
    start_instant: std::time::Instant,
    codex_home: PathBuf,
}

impl SystemProbe {
    pub fn new() -> Self {
        let codex_home = std::env::var_os("CODEX_HOME")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .or_else(|| std::env::var_os("USERPROFILE"))
                    .map(|h| PathBuf::from(h).join(".mightling"))
            })
            .unwrap_or_else(|| PathBuf::from(".mightling"));
        Self {
            start_instant: std::time::Instant::now(),
            codex_home,
        }
    }

    pub fn with_codex_home(codex_home: PathBuf) -> Self {
        Self {
            start_instant: std::time::Instant::now(),
            codex_home,
        }
    }
}

impl Default for SystemProbe {
    fn default() -> Self {
        Self::new()
    }
}

impl Probe for SystemProbe {
    fn now_s(&mut self) -> u64 {
        self.start_instant.elapsed().as_secs()
    }

    fn sleep_s(&mut self, seconds: u64) {
        if seconds > 0 {
            std::thread::sleep(Duration::from_secs(seconds));
        }
    }

    fn read_file_lines(&mut self, path: &Path) -> Result<Vec<String>, String> {
        let file = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let limited = file.take(MAX_FILE_BYTES);
        let reader = BufReader::new(limited);
        let mut lines = Vec::new();
        for line in reader.lines() {
            match line {
                Ok(l) => lines.push(l),
                Err(e) => return Err(format!("{}: {e}", path.display())),
            }
        }
        Ok(lines)
    }

    fn is_pid_alive(&mut self, pid: i32) -> bool {
        let proc_stat = PathBuf::from(format!("/proc/{pid}/stat"));
        if proc_stat.exists() {
            if let Ok(contents) = std::fs::read_to_string(&proc_stat) {
                if let Some(rparen) = contents.rfind(')') {
                    let rest = contents[rparen + 1..].trim_start();
                    if rest.starts_with('Z') {
                        return false;
                    }
                }
            }
            return true;
        }
        #[cfg(target_os = "linux")]
        {
            false
        }
        #[cfg(not(target_os = "linux"))]
        {
            Command::new("kill")
                .args(["-0", &pid.to_string()])
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false)
        }
    }

    fn http_status(
        &mut self,
        host: &str,
        port: u16,
        request_path: &str,
        _url: &str,
    ) -> Result<u16, String> {
        // Loopback only, and never through a resolver: a name lookup could leave the machine.
        let ip = loopback_ip(host).ok_or_else(|| format!("{host} is not loopback"))?;
        let addr = &SocketAddr::new(ip, port);
        let timeout = Duration::from_secs(5);
        let mut stream =
            TcpStream::connect_timeout(addr, timeout).map_err(|e| format!("connect: {e}"))?;
        let _ = stream.set_read_timeout(Some(timeout));
        let _ = stream.set_write_timeout(Some(timeout));
        let req = format!(
            "GET {request_path} HTTP/1.1\r\nHost: {host}:{port}\r\nUser-Agent: ling-delegate/0.1\r\nConnection: close\r\n\r\n"
        );
        stream
            .write_all(req.as_bytes())
            .map_err(|e| format!("write: {e}"))?;
        let mut reader = BufReader::new(stream);
        let mut status_line = String::new();
        reader
            .read_line(&mut status_line)
            .map_err(|e| format!("read: {e}"))?;
        parse_http_status_line(&status_line)
    }

    fn gh_run_view(&mut self, run_id: &str, repo: Option<&str>) -> Result<CommandOutput, String> {
        let mut argv = vec![
            "gh".to_string(),
            "run".to_string(),
            "view".to_string(),
            run_id.to_string(),
        ];
        if let Some(r) = repo {
            argv.push("--repo".to_string());
            argv.push(r.to_string());
        }
        argv.push("--json".to_string());
        argv.push("status,conclusion".to_string());
        self.run_command(&argv)
    }

    fn systemd_unit_state(&mut self, unit: &str) -> Result<CommandOutput, String> {
        let argv = vec![
            "systemctl".to_string(),
            "--user".to_string(),
            "is-active".to_string(),
            unit.to_string(),
        ];
        self.run_command(&argv)
    }

    fn run_command(&mut self, argv: &[String]) -> Result<CommandOutput, String> {
        let (prog, args) = argv
            .split_first()
            .ok_or_else(|| "empty command".to_string())?;
        // An allowlisted `git [-C dir] <read-only subcommand>` still reads the checkout's config,
        // which can name programs (diff.external, a filter, fsmonitor): run it through the helper.
        let output = if prog == "git" {
            let (dir, rest) = match args {
                [flag, dir, rest @ ..] if flag == "-C" => (Path::new(dir.as_str()), rest),
                _ => (Path::new("."), args),
            };
            let rest: Vec<&str> = rest.iter().map(String::as_str).collect();
            ling_safegit::output(dir, &rest)
        } else {
            Command::new(prog).args(args).output()
        }
        .map_err(|e| format!("spawn {prog}: {e}"))?;
        Ok(CommandOutput {
            exit_code: output.status.code().unwrap_or(-1),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        })
    }

    fn night_task_status(&mut self, id: &str) -> Result<Option<String>, String> {
        let path = self
            .codex_home
            .join("night")
            .join("tasks")
            .join(format!("{id}.json"));
        if !path.exists() {
            return Ok(None);
        }
        let raw = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let val: Value =
            serde_json::from_str(&raw).map_err(|e| format!("{}: {e}", path.display()))?;
        Ok(val
            .get("status")
            .and_then(Value::as_str)
            .map(ToString::to_string))
    }

    fn git_rev_parse(&mut self, git_ref: &str, repo: Option<&Path>) -> Result<String, String> {
        let args = ["rev-parse", "--verify", git_ref];
        let output = match repo {
            Some(dir) => ling_safegit::output(dir, &args),
            None => ling_safegit::output(Path::new("."), &args),
        }
        .map_err(|e| format!("spawn git: {e}"))?;
        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr).trim().to_string();
            return Err(if err.is_empty() {
                format!("git rev-parse exited with {}", output.status)
            } else {
                err
            });
        }
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    }
}

/// Validates and executes a [`WatchRequest`] against `probe`, blocking until the condition is
/// met or `timeout_s` expires.
pub fn execute(req: &WatchRequest, probe: &mut dyn Probe) -> Result<WatchResult, WatchError> {
    let validated = req.validate()?;
    execute_validated(&validated, probe)
}

/// Executes an already-validated [`ValidatedWatch`] against `probe`.
pub fn execute_validated(
    watch: &ValidatedWatch,
    probe: &mut dyn Probe,
) -> Result<WatchResult, WatchError> {
    if let ValidatedCondition::Timeout { duration_s } = watch.condition {
        let wait_s = duration_s.min(watch.timeout_s);
        probe.sleep_s(wait_s);
        return Ok(WatchResult {
            met: true,
            observed: truncate_observed(&format!("timeout elapsed ({wait_s}s)")),
            at: probe.timestamp_rfc3339(),
        });
    }

    let start_s = probe.now_s();
    let deadline_s = start_s.saturating_add(watch.timeout_s);
    let mut initial_git_ref: Option<String> = match &watch.condition {
        ValidatedCondition::GitRef { from, .. } => from.clone(),
        _ => None,
    };
    loop {
        let last_observation = match check_once(&watch.condition, &mut initial_git_ref, probe) {
            PollStep::Met(observed) => {
                return Ok(WatchResult {
                    met: true,
                    observed: truncate_observed(&observed),
                    at: probe.timestamp_rfc3339(),
                });
            }
            PollStep::Pending(obs) => obs,
        };

        let now_s = probe.now_s();
        if now_s >= deadline_s {
            return Ok(WatchResult {
                met: false,
                observed: truncate_observed(&format!(
                    "timeout after {}s (last: {})",
                    watch.timeout_s, last_observation
                )),
                at: probe.timestamp_rfc3339(),
            });
        }

        let remaining_s = deadline_s - now_s;
        let sleep_s = watch.poll_interval_s.min(remaining_s).max(1);
        probe.sleep_s(sleep_s);
    }
}

enum PollStep {
    Met(String),
    Pending(String),
}

fn check_once(
    condition: &ValidatedCondition,
    initial_git_ref: &mut Option<String>,
    probe: &mut dyn Probe,
) -> PollStep {
    match condition {
        ValidatedCondition::FileLine {
            path,
            pattern,
            is_regex,
        } => match probe.read_file_lines(path) {
            Ok(lines) => {
                let compiled = if *is_regex {
                    Regex::new(pattern).ok()
                } else {
                    None
                };
                for (idx, line) in lines.iter().enumerate() {
                    let matched = match &compiled {
                        Some(re) => re.is_match(line),
                        None => line.contains(pattern.as_str()),
                    };
                    if matched {
                        let line_no = idx + 1;
                        return PollStep::Met(format!("{}:{}: {}", path.display(), line_no, line));
                    }
                }
                PollStep::Pending(format!(
                    "{}: {} lines read, no match for {:?}",
                    path.display(),
                    lines.len(),
                    pattern
                ))
            }
            Err(err) => PollStep::Pending(err),
        },
        ValidatedCondition::PidExit { pid } => {
            if !probe.is_pid_alive(*pid) {
                PollStep::Met(format!("pid {pid} exited"))
            } else {
                PollStep::Pending(format!("pid {pid} running"))
            }
        }
        ValidatedCondition::HttpStatus {
            url,
            host_port: (host, port),
            request_path,
            expected_status,
        } => match probe.http_status(host, *port, request_path, url) {
            Ok(code) if code == *expected_status => {
                PollStep::Met(format!("HTTP {code} from {url}"))
            }
            Ok(code) => PollStep::Pending(format!(
                "HTTP {code} from {url} (waiting for {expected_status})"
            )),
            Err(err) => PollStep::Pending(format!("{url}: {err}")),
        },
        ValidatedCondition::GhRun { run_id, repo } => {
            match probe.gh_run_view(run_id, repo.as_deref()) {
                Ok(out) if out.exit_code == 0 => match parse_gh_run_json(&out.stdout) {
                    Ok((status, conclusion)) => {
                        if status == "completed" {
                            let conc = conclusion.unwrap_or_else(|| "unknown".to_string());
                            PollStep::Met(format!("run {run_id} completed: {conc}"))
                        } else {
                            PollStep::Pending(format!("run {run_id} status: {status}"))
                        }
                    }
                    Err(e) => PollStep::Pending(format!("run {run_id}: {e}")),
                },
                Ok(out) => PollStep::Pending(format!(
                    "gh run view {run_id} exited {}: {}",
                    out.exit_code,
                    out.stderr.trim()
                )),
                Err(e) => PollStep::Pending(e),
            }
        }
        ValidatedCondition::SystemdUnit {
            unit,
            expected_state,
        } => match probe.systemd_unit_state(unit) {
            Ok(out) => {
                let state = out.stdout.lines().next().unwrap_or("").trim();
                let state = if state.is_empty() {
                    out.stderr.lines().next().unwrap_or("unknown").trim()
                } else {
                    state
                };
                if state == expected_state {
                    PollStep::Met(format!("unit {unit}: {state}"))
                } else {
                    PollStep::Pending(format!(
                        "unit {unit}: {state} (waiting for {expected_state})"
                    ))
                }
            }
            Err(e) => PollStep::Pending(e),
        },
        ValidatedCondition::Command {
            argv,
            expected_exit,
            pattern,
            is_regex,
        } => match probe.run_command(argv) {
            Ok(out) => {
                let exit_ok = expected_exit.map(|c| c == out.exit_code).unwrap_or(true);
                let combined = if out.stderr.is_empty() {
                    out.stdout.clone()
                } else if out.stdout.is_empty() {
                    out.stderr.clone()
                } else {
                    format!("{}\n{}", out.stdout, out.stderr)
                };
                match pattern {
                    Some(pat) => {
                        let compiled = if *is_regex {
                            Regex::new(pat).ok()
                        } else {
                            None
                        };
                        let matched_line = combined.lines().find(|line| match &compiled {
                            Some(re) => re.is_match(line),
                            None => line.contains(pat.as_str()),
                        });
                        match (exit_ok, matched_line) {
                            (true, Some(line)) => {
                                PollStep::Met(format!("exit {}: {}", out.exit_code, line))
                            }
                            _ => PollStep::Pending(format!(
                                "exit {} (no match for {:?})",
                                out.exit_code, pat
                            )),
                        }
                    }
                    None => {
                        if exit_ok {
                            let first_line = combined.lines().next().unwrap_or("").trim();
                            if first_line.is_empty() {
                                PollStep::Met(format!("exit {}", out.exit_code))
                            } else {
                                PollStep::Met(format!("exit {}: {}", out.exit_code, first_line))
                            }
                        } else {
                            PollStep::Pending(format!("exit {}", out.exit_code))
                        }
                    }
                }
            }
            Err(e) => PollStep::Pending(e),
        },
        ValidatedCondition::NightTask { id } => match probe.night_task_status(id) {
            Ok(Some(status)) => {
                if FINISHED_NIGHT_STATUSES.contains(&status.as_str()) {
                    PollStep::Met(format!("night {id}: {status}"))
                } else {
                    PollStep::Pending(format!("night {id}: {status}"))
                }
            }
            Ok(None) => PollStep::Pending(format!("night {id}: task file not found")),
            Err(e) => PollStep::Pending(e),
        },
        ValidatedCondition::GitRef { git_ref, repo, .. } => {
            match probe.git_rev_parse(git_ref, repo.as_deref()) {
                Ok(current) if !current.is_empty() => match initial_git_ref {
                    None => {
                        *initial_git_ref = Some(current.clone());
                        PollStep::Pending(format!("{git_ref} at {current}"))
                    }
                    Some(prev) if *prev != current => {
                        PollStep::Met(format!("{git_ref} moved from {prev} to {current}"))
                    }
                    Some(prev) => PollStep::Pending(format!("{git_ref} unchanged at {prev}")),
                },
                Ok(_) => PollStep::Pending(format!("{git_ref}: empty revision")),
                Err(e) => PollStep::Pending(format!("{git_ref}: {e}")),
            }
        }
        ValidatedCondition::Timeout { duration_s } => {
            PollStep::Met(format!("timeout elapsed ({duration_s}s)"))
        }
    }
}

fn parse_gh_run_json(stdout: &str) -> Result<(String, Option<String>), String> {
    let val: Value =
        serde_json::from_str(stdout.trim()).map_err(|e| format!("invalid gh json: {e}"))?;
    let status = val
        .get("status")
        .and_then(Value::as_str)
        .ok_or_else(|| "missing `status` in gh output".to_string())?
        .to_string();
    let conclusion = val
        .get("conclusion")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string);
    Ok((status, conclusion))
}

fn parse_http_status_line(line: &str) -> Result<u16, String> {
    let trimmed = line.trim();
    let mut parts = trimmed.split_whitespace();
    let proto = parts
        .next()
        .ok_or_else(|| "empty HTTP response".to_string())?;
    if !proto.starts_with("HTTP/") {
        return Err(format!("invalid HTTP status line: {trimmed:?}"));
    }
    let code_str = parts
        .next()
        .ok_or_else(|| format!("missing status code in {trimmed:?}"))?;
    code_str
        .parse::<u16>()
        .map_err(|_| format!("invalid status code {code_str:?}"))
}

fn parse_and_check_watch_url(raw_url: &str) -> Result<(String, u16, String), WatchError> {
    let rest = raw_url.strip_prefix("http://").ok_or_else(|| {
        WatchError::NotAllowed(format!(
            "http_status URL must use http:// on loopback, got {raw_url:?}"
        ))
    })?;
    if rest.contains('@') || rest.chars().any(char::is_whitespace) {
        return Err(WatchError::NotAllowed(format!(
            "URL must not contain credentials or whitespace: {raw_url:?}"
        )));
    }
    let (authority, path_part) = match rest.find('/') {
        Some(idx) => (&rest[..idx], &rest[idx..]),
        None => (rest, "/"),
    };
    let (host, port) = parse_authority(authority)?;
    if loopback_ip(&host).is_none() {
        return Err(WatchError::NotAllowed(format!(
            "http_status host {host:?} is not loopback (127.0.0.1, localhost, ::1); a node by name is not built in P1"
        )));
    }
    Ok((host, port, path_part.to_string()))
}

fn parse_authority(authority: &str) -> Result<(String, u16), WatchError> {
    if authority.is_empty() {
        return Err(WatchError::InvalidArgument(
            "URL host is empty".to_string(),
        ));
    }
    if let Some(after_bracket) = authority.strip_prefix('[') {
        let close = after_bracket.find(']').ok_or_else(|| {
            WatchError::InvalidArgument(format!("unclosed IPv6 bracket in {authority:?}"))
        })?;
        let host = &after_bracket[..close];
        let remainder = &after_bracket[close + 1..];
        let port = if remainder.is_empty() {
            80
        } else if let Some(p) = remainder.strip_prefix(':') {
            p.parse::<u16>().map_err(|_| {
                WatchError::InvalidArgument(format!("invalid port in {authority:?}"))
            })?
        } else {
            return Err(WatchError::InvalidArgument(format!(
                "invalid authority {authority:?}"
            )));
        };
        return Ok((host.to_string(), port));
    }

    match authority.rsplit_once(':') {
        Some((h, p)) => {
            if h.is_empty() {
                return Err(WatchError::InvalidArgument(format!(
                    "empty host in {authority:?}"
                )));
            }
            let port = p.parse::<u16>().map_err(|_| {
                WatchError::InvalidArgument(format!("invalid port {p:?} in {authority:?}"))
            })?;
            Ok((h.to_string(), port))
        }
        None => Ok((authority.to_string(), 80)),
    }
}

/// The address of a loopback host, without a name lookup: `127.0.0.1`, `::1` or `localhost`.
fn loopback_ip(host: &str) -> Option<IpAddr> {
    match host.to_ascii_lowercase().as_str() {
        "127.0.0.1" | "localhost" => Some(IpAddr::V4(Ipv4Addr::LOCALHOST)),
        "::1" => Some(IpAddr::V6(Ipv6Addr::LOCALHOST)),
        _ => None,
    }
}

fn validate_repo_slug(repo: &str) -> Result<(), WatchError> {
    let Some((owner, name)) = repo.split_once('/') else {
        return Err(WatchError::InvalidArgument(format!(
            "repo must have shape `owner/name`, got {repo:?}"
        )));
    };
    let ok_part = |s: &str| {
        !s.is_empty()
            && !s.starts_with('-')
            && !s.starts_with('.')
            && s.chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
    };
    if !ok_part(owner) || !ok_part(name) || name.contains('/') {
        return Err(WatchError::InvalidArgument(format!(
            "invalid repo slug {repo:?}"
        )));
    }
    Ok(())
}

fn validate_unit_name(unit: &str) -> Result<(), WatchError> {
    if unit.is_empty()
        || unit.starts_with('-')
        || unit.contains('/')
        || unit.contains("..")
        || !unit
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '@' | ':'))
    {
        return Err(WatchError::InvalidArgument(format!(
            "invalid systemd unit name {unit:?}"
        )));
    }
    Ok(())
}

fn validate_night_id(id: &str) -> Result<(), WatchError> {
    if id.is_empty()
        || id.starts_with('.')
        || id.contains('/')
        || id.contains('\\')
        || id.contains("..")
        || !id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
    {
        return Err(WatchError::InvalidArgument(format!(
            "invalid night task id {id:?}"
        )));
    }
    Ok(())
}

fn validate_git_ref_name(git_ref: &str) -> Result<(), WatchError> {
    if git_ref.is_empty()
        || git_ref.starts_with('-')
        || git_ref.contains("..")
        || git_ref.contains("@{")
        || git_ref.chars().any(|c| c.is_control() || c.is_whitespace())
    {
        return Err(WatchError::InvalidArgument(format!(
            "invalid git ref {git_ref:?}"
        )));
    }
    Ok(())
}

/// Tokenizes and validates an allowlisted command string (spec §4.2).
///
/// Allowed prefixes:
/// - `git <status|rev-parse|log|diff|branch|tag|show-ref|ls-files> …`
/// - `gh run view …`
/// - `ling night <show|status> …`
/// - `ling-admin night <status|show> …`
/// - `systemctl --user is-active <unit>`
/// - `curl …` (read-only GET/HEAD to loopback only)
pub fn parse_and_check_allowlisted_command(raw: &str) -> Result<Vec<String>, WatchError> {
    const FORBIDDEN_METACHARS: &[char] = &[';', '|', '&', '`', '$', '>', '<', '\n', '\r', '\0'];
    if raw.chars().any(|c| FORBIDDEN_METACHARS.contains(&c)) {
        return Err(WatchError::NotAllowed(
            "command must not contain shell metacharacters or redirections".to_string(),
        ));
    }

    let argv = split_simple_words(raw)?;
    if argv.is_empty() {
        return Err(WatchError::InvalidArgument("empty command".to_string()));
    }

    let prog = argv[0].as_str();
    match prog {
        "git" => {
            let mut idx = 1;
            if argv.get(idx).map(String::as_str) == Some("-C") {
                idx += 2;
            }
            let sub = argv.get(idx).map(String::as_str).ok_or_else(|| {
                WatchError::NotAllowed("git command requires a read-only subcommand".to_string())
            })?;
            const ALLOWED_GIT: &[&str] = &[
                "status",
                "rev-parse",
                "log",
                "diff",
                "show-ref",
                "ls-files",
            ];
            if !ALLOWED_GIT.contains(&sub) {
                return Err(WatchError::NotAllowed(format!(
                    "git subcommand {sub:?} is not in the watch allowlist ({})",
                    ALLOWED_GIT.join(", ")
                )));
            }
            // `--output` writes a file; an external diff or text conversion runs a program.
            if let Some(flag) = argv[idx + 1..].iter().find(|arg| {
                arg.starts_with("--output") || arg.starts_with("--ext-diff") || arg.starts_with("--textconv")
            }) {
                return Err(WatchError::NotAllowed(format!("git flag {flag:?} is not allowed in watch")));
            }
            Ok(argv)
        }
        "gh" => {
            if argv.get(1).map(String::as_str) != Some("run")
                || argv.get(2).map(String::as_str) != Some("view")
                || argv.len() < 4
            {
                return Err(WatchError::NotAllowed(
                    "only `gh run view <id> …` is allowlisted for gh".to_string(),
                ));
            }
            Ok(argv)
        }
        "ling" => {
            if argv.get(1).map(String::as_str) != Some("night")
                || !matches!(argv.get(2).map(String::as_str), Some("show" | "status"))
            {
                return Err(WatchError::NotAllowed(
                    "only `ling night show …` and `ling night status` are allowlisted for ling"
                        .to_string(),
                ));
            }
            Ok(argv)
        }
        "ling-admin" => {
            if argv.get(1).map(String::as_str) != Some("night")
                || !matches!(argv.get(2).map(String::as_str), Some("status" | "show"))
            {
                return Err(WatchError::NotAllowed(
                    "only `ling-admin night status` and `ling-admin night show` are allowlisted"
                        .to_string(),
                ));
            }
            Ok(argv)
        }
        "systemctl" => {
            if argv.get(1).map(String::as_str) != Some("--user")
                || argv.get(2).map(String::as_str) != Some("is-active")
                || argv.len() != 4
            {
                return Err(WatchError::NotAllowed(
                    "only `systemctl --user is-active <unit>` is allowlisted".to_string(),
                ));
            }
            validate_unit_name(&argv[3])?;
            Ok(argv)
        }
        "curl" => {
            // A closed set of flags that take no value and neither write a file nor follow a
            // redirect away from loopback; every other flag (`-o`, `-L`, `-x`, `--resolve`, an
            // attached value such as `-o/path`) is refused.
            const ALLOWED_CURL_FLAGS: &[&str] = &[
                "--silent",
                "--show-error",
                "--fail",
                "--head",
                "--include",
            ];
            const ALLOWED_CURL_SHORT: &str = "sSfIi";
            let mut saw_url = false;
            for arg in &argv[1..] {
                let allowed_flag = ALLOWED_CURL_FLAGS.contains(&arg.as_str())
                    || (arg.len() > 1
                        && !arg.starts_with("--")
                        && arg[1..].chars().all(|c| ALLOWED_CURL_SHORT.contains(c)));
                if arg.starts_with('-') && !allowed_flag {
                    return Err(WatchError::NotAllowed(format!(
                        "curl flag {arg:?} is not allowed in watch (only {} and -{ALLOWED_CURL_SHORT})",
                        ALLOWED_CURL_FLAGS.join(", ")
                    )));
                }
                if !arg.starts_with('-') {
                    let (host, _, _) = parse_and_check_watch_url(arg)?;
                    let lower = host.to_ascii_lowercase();
                    if lower != "127.0.0.1" && lower != "localhost" && lower != "::1" {
                        return Err(WatchError::NotAllowed(format!(
                            "curl in watch is restricted to loopback, got host {host:?}"
                        )));
                    }
                    saw_url = true;
                }
            }
            if !saw_url {
                return Err(WatchError::InvalidArgument(
                    "curl command requires a loopback http:// URL".to_string(),
                ));
            }
            Ok(argv)
        }
        other => Err(WatchError::NotAllowed(format!(
            "command {other:?} is not in the watch allowlist (git, gh run view, ling night show, ling-admin night status, systemctl --user is-active, curl to loopback)"
        ))),
    }
}

fn split_simple_words(raw: &str) -> Result<Vec<String>, WatchError> {
    let mut words = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;

    for ch in raw.chars() {
        match quote {
            Some(q) => {
                if ch == q {
                    quote = None;
                } else {
                    current.push(ch);
                }
            }
            None => {
                if ch == '\'' || ch == '"' {
                    quote = Some(ch);
                } else if ch.is_ascii_whitespace() {
                    if !current.is_empty() {
                        words.push(std::mem::take(&mut current));
                    }
                } else {
                    current.push(ch);
                }
            }
        }
    }

    if quote.is_some() {
        return Err(WatchError::InvalidArgument(
            "unclosed quote in command".to_string(),
        ));
    }
    if !current.is_empty() {
        words.push(current);
    }
    Ok(words)
}

fn truncate_observed(s: &str) -> String {
    if s.chars().count() <= MAX_OBSERVED_CHARS {
        return s.to_string();
    }
    let truncated: String = s.chars().take(MAX_OBSERVED_CHARS).collect();
    format!("{truncated}…[truncated]")
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;

    use super::*;

    struct FakeProbe {
        now: u64,
        fixed_timestamp: String,
        file_polls: VecDeque<Result<Vec<String>, String>>,
        pid_alive_polls: VecDeque<bool>,
        http_polls: VecDeque<Result<u16, String>>,
        gh_polls: VecDeque<Result<CommandOutput, String>>,
        gh_seen_args: Vec<(String, Option<String>)>,
        unit_polls: VecDeque<Result<CommandOutput, String>>,
        cmd_polls: VecDeque<Result<CommandOutput, String>>,
        night_polls: VecDeque<Result<Option<String>, String>>,
        git_polls: VecDeque<Result<String, String>>,
        sleeps: Vec<u64>,
    }

    impl FakeProbe {
        fn new() -> Self {
            Self {
                now: 0,
                fixed_timestamp: "2026-10-27T12:00:00Z".to_string(),
                file_polls: VecDeque::new(),
                pid_alive_polls: VecDeque::new(),
                http_polls: VecDeque::new(),
                gh_polls: VecDeque::new(),
                gh_seen_args: Vec::new(),
                unit_polls: VecDeque::new(),
                cmd_polls: VecDeque::new(),
                night_polls: VecDeque::new(),
                git_polls: VecDeque::new(),
                sleeps: Vec::new(),
            }
        }
    }

    impl Probe for FakeProbe {
        fn now_s(&mut self) -> u64 {
            self.now
        }

        fn timestamp_rfc3339(&mut self) -> String {
            self.fixed_timestamp.clone()
        }

        fn sleep_s(&mut self, seconds: u64) {
            self.sleeps.push(seconds);
            self.now = self.now.saturating_add(seconds);
        }

        fn read_file_lines(&mut self, _path: &Path) -> Result<Vec<String>, String> {
            self.file_polls
                .pop_front()
                .unwrap_or_else(|| Ok(Vec::new()))
        }

        fn is_pid_alive(&mut self, _pid: i32) -> bool {
            self.pid_alive_polls.pop_front().unwrap_or(false)
        }

        fn http_status(
            &mut self,
            _host: &str,
            _port: u16,
            _request_path: &str,
            _url: &str,
        ) -> Result<u16, String> {
            self.http_polls
                .pop_front()
                .unwrap_or_else(|| Err("connection refused".to_string()))
        }

        fn gh_run_view(
            &mut self,
            run_id: &str,
            repo: Option<&str>,
        ) -> Result<CommandOutput, String> {
            self.gh_seen_args
                .push((run_id.to_string(), repo.map(ToString::to_string)));
            self.gh_polls.pop_front().unwrap_or_else(|| {
                Ok(CommandOutput {
                    exit_code: 0,
                    stdout: r#"{"status":"in_progress","conclusion":null}"#.to_string(),
                    stderr: String::new(),
                })
            })
        }

        fn systemd_unit_state(&mut self, _unit: &str) -> Result<CommandOutput, String> {
            self.unit_polls.pop_front().unwrap_or_else(|| {
                Ok(CommandOutput {
                    exit_code: 3,
                    stdout: "inactive\n".to_string(),
                    stderr: String::new(),
                })
            })
        }

        fn run_command(&mut self, _argv: &[String]) -> Result<CommandOutput, String> {
            self.cmd_polls.pop_front().unwrap_or_else(|| {
                Ok(CommandOutput {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: String::new(),
                })
            })
        }

        fn night_task_status(&mut self, _id: &str) -> Result<Option<String>, String> {
            self.night_polls.pop_front().unwrap_or(Ok(None))
        }

        fn git_rev_parse(&mut self, _git_ref: &str, _repo: Option<&Path>) -> Result<String, String> {
            self.git_polls
                .pop_front()
                .unwrap_or_else(|| Err("unknown revision".to_string()))
        }
    }

    #[test]
    fn closed_list_rejects_unknown_condition_kinds() {
        for bad in ["shell", "eval", "docker", "arbitrary_check", ""] {
            let req = WatchRequest {
                kind: bad.to_string(),
                target: Some("x".to_string()),
                ..Default::default()
            };
            let err = req.validate().unwrap_err();
            assert!(
                matches!(err, WatchError::UnknownKind(_)),
                "expected UnknownKind for {bad:?}, got {err:?}"
            );
            assert!(err.to_string().contains("closed list"));
        }
    }

    #[test]
    fn file_line_returns_verbatim_line_with_file_and_line_number() {
        let mut probe = FakeProbe::new();
        probe.file_polls.push_back(Ok(vec![
            "booting server".to_string(),
            "loading weights".to_string(),
        ]));
        probe.file_polls.push_back(Ok(vec![
            "booting server".to_string(),
            "loading weights".to_string(),
            "INFO: Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)".to_string(),
        ]));

        let req = WatchRequest {
            kind: "file_line".to_string(),
            path: Some("/tmp/server.log".to_string()),
            pattern: Some("Uvicorn running".to_string()),
            timeout_s: Some(30),
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert_eq!(
            res,
            WatchResult {
                met: true,
                observed:
                    "/tmp/server.log:3: INFO: Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)"
                        .to_string(),
                at: "2026-10-27T12:00:00Z".to_string(),
            }
        );
        assert_eq!(probe.sleeps, vec![1]);
    }

    #[test]
    fn file_line_supports_regex_matching() {
        let mut probe = FakeProbe::new();
        probe.file_polls.push_back(Ok(vec![
            "step 1: warmup".to_string(),
            "epoch 42 loss=0.0123".to_string(),
        ]));

        let req = WatchRequest {
            kind: "file_line".to_string(),
            target: Some("/tmp/train.log".to_string()),
            pattern: Some(r"^epoch \d+ loss=0\.01".to_string()),
            regex: true,
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert!(res.met);
        assert_eq!(res.observed, "/tmp/train.log:2: epoch 42 loss=0.0123");
    }

    #[test]
    fn pid_exit_waits_until_process_exits() {
        let mut probe = FakeProbe::new();
        probe.pid_alive_polls.push_back(true);
        probe.pid_alive_polls.push_back(true);
        probe.pid_alive_polls.push_back(false);

        let req = WatchRequest {
            kind: "pid_exit".to_string(),
            pid: Some(4242),
            timeout_s: Some(10),
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert!(res.met);
        assert_eq!(res.observed, "pid 4242 exited");
        assert_eq!(probe.sleeps, vec![1, 1]);
    }

    #[test]
    fn http_status_waits_for_target_status_and_refuses_external_hosts() {
        let mut probe = FakeProbe::new();
        probe
            .http_polls
            .push_back(Err("connection refused".to_string()));
        probe.http_polls.push_back(Ok(503));
        probe.http_polls.push_back(Ok(200));

        let req = WatchRequest {
            kind: "http_status".to_string(),
            url: Some("http://127.0.0.1:8000/health".to_string()),
            status: Some(200),
            timeout_s: Some(10),
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert!(res.met);
        assert_eq!(res.observed, "HTTP 200 from http://127.0.0.1:8000/health");
        assert_eq!(probe.sleeps, vec![1, 1]);

        let external = WatchRequest {
            kind: "http_status".to_string(),
            url: Some("http://example.com/health".to_string()),
            ..Default::default()
        };
        assert!(matches!(
            external.validate(),
            Err(WatchError::NotAllowed(_))
        ));
        // Anything that would need a name lookup (a single label, mDNS, another address) is refused.
        for url in ["http://node-b:8000/", "http://box.local/", "http://10.0.0.1/", "http://[::2]/"] {
            let req = WatchRequest { kind: "http_status".to_string(), url: Some(url.to_string()), ..Default::default() };
            assert!(matches!(req.validate(), Err(WatchError::NotAllowed(_))), "{url}");
        }
    }

    #[test]
    fn gh_run_polls_mock_runner_until_completed_and_enforces_15s_floor() {
        let mut probe = FakeProbe::new();
        probe.gh_polls.push_back(Ok(CommandOutput {
            exit_code: 0,
            stdout: r#"{"status":"in_progress","conclusion":null}"#.to_string(),
            stderr: String::new(),
        }));
        probe.gh_polls.push_back(Ok(CommandOutput {
            exit_code: 0,
            stdout: r#"{"status":"completed","conclusion":"success"}"#.to_string(),
            stderr: String::new(),
        }));

        let req = WatchRequest {
            kind: "gh_run".to_string(),
            run_id: Some("987654321".to_string()),
            repo: Some("dreamference/mightling".to_string()),
            poll_interval_s: Some(1), // Clamped to MIN_COMMAND_POLL_S (15s)
            timeout_s: Some(60),
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert!(res.met);
        assert_eq!(res.observed, "run 987654321 completed: success");
        assert_eq!(probe.sleeps, vec![15]);
        assert_eq!(
            probe.gh_seen_args,
            vec![
                (
                    "987654321".to_string(),
                    Some("dreamference/mightling".to_string())
                ),
                (
                    "987654321".to_string(),
                    Some("dreamference/mightling".to_string())
                ),
            ]
        );
    }

    #[test]
    fn systemd_unit_waits_for_target_state() {
        let mut probe = FakeProbe::new();
        probe.unit_polls.push_back(Ok(CommandOutput {
            exit_code: 3,
            stdout: "activating\n".to_string(),
            stderr: String::new(),
        }));
        probe.unit_polls.push_back(Ok(CommandOutput {
            exit_code: 0,
            stdout: "active\n".to_string(),
            stderr: String::new(),
        }));

        let req = WatchRequest {
            kind: "systemd_unit".to_string(),
            unit: Some("mightling-night.service".to_string()),
            state: Some("active".to_string()),
            timeout_s: Some(60),
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert!(res.met);
        assert_eq!(res.observed, "unit mightling-night.service: active");
        assert_eq!(probe.sleeps, vec![15]);
    }

    #[test]
    fn command_checks_allowlist_and_matches_exit_or_pattern() {
        let mut probe = FakeProbe::new();
        probe.cmd_polls.push_back(Ok(CommandOutput {
            exit_code: 0,
            stdout: "queue: 1 running\n".to_string(),
            stderr: String::new(),
        }));
        probe.cmd_polls.push_back(Ok(CommandOutput {
            exit_code: 0,
            stdout: "queue: 0 running, 1 done\n".to_string(),
            stderr: String::new(),
        }));

        let req = WatchRequest {
            kind: "command".to_string(),
            command: Some("ling-admin night status".to_string()),
            pattern: Some("0 running".to_string()),
            exit_code: Some(0),
            timeout_s: Some(60),
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert!(res.met);
        assert_eq!(res.observed, "exit 0: queue: 0 running, 1 done");
        assert_eq!(probe.sleeps, vec![15]);

        for disallowed in [
            "bash -c 'echo hi'",
            "python3 -c 'print(1)'",
            "git push origin main",
            "curl http://example.com",
            "curl -X POST http://127.0.0.1:8000/v1/chat/completions",
            "ling night status; id",
            "curl -o/tmp/x http://127.0.0.1:8000/",
            "curl -sSo/tmp/x http://127.0.0.1:8000/",
            "curl -L http://127.0.0.1:8000/",
            "curl -x http://127.0.0.1:1 http://127.0.0.1:8000/",
            "curl --resolve x http://127.0.0.1:8000/",
            "git branch -D main",
            "git tag v9",
            "git diff --output=/tmp/x",
            "git -C . log --output /tmp/x",
            "git diff --ext-diff",
        ] {
            let bad = WatchRequest {
                kind: "command".to_string(),
                command: Some(disallowed.to_string()),
                ..Default::default()
            };
            assert!(
                matches!(bad.validate(), Err(WatchError::NotAllowed(_))),
                "expected NotAllowed for {disallowed:?}"
            );
        }
    }

    #[test]
    fn night_task_waits_for_finished_status() {
        let mut probe = FakeProbe::new();
        probe
            .night_polls
            .push_back(Ok(Some("running".to_string())));
        probe.night_polls.push_back(Ok(Some("done".to_string())));

        let req = WatchRequest {
            kind: "night_task".to_string(),
            id: Some("20261027-01".to_string()),
            timeout_s: Some(30),
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert!(res.met);
        assert_eq!(res.observed, "night 20261027-01: done");
    }

    #[test]
    fn git_ref_waits_until_revision_moves() {
        let mut probe = FakeProbe::new();
        probe.git_polls.push_back(Ok("1111111".to_string()));
        probe.git_polls.push_back(Ok("1111111".to_string()));
        probe.git_polls.push_back(Ok("2222222".to_string()));

        let req = WatchRequest {
            kind: "git_ref".to_string(),
            git_ref: Some("refs/heads/main".to_string()),
            timeout_s: Some(60),
            ..Default::default()
        };

        let res = execute(&req, &mut probe).unwrap();
        assert!(res.met);
        assert_eq!(
            res.observed,
            "refs/heads/main moved from 1111111 to 2222222"
        );
        assert_eq!(probe.sleeps, vec![15, 15]);
    }

    #[test]
    fn timeout_kind_and_unmet_deadline_behavior() {
        // 1. Pure timeout kind waits for the requested duration and returns met: true.
        let mut probe = FakeProbe::new();
        let timer_req = WatchRequest {
            kind: "timeout".to_string(),
            target: Some("5".to_string()),
            timeout_s: Some(30),
            ..Default::default()
        };
        let timer_res = execute(&timer_req, &mut probe).unwrap();
        assert!(timer_res.met);
        assert_eq!(timer_res.observed, "timeout elapsed (5s)");
        assert_eq!(probe.sleeps, vec![5]);

        // 2. Unmet condition stops at timeout_s and returns met: false.
        let mut probe2 = FakeProbe::new();
        for _ in 0..5 {
            probe2.pid_alive_polls.push_back(true);
        }
        let unmet_req = WatchRequest {
            kind: "pid_exit".to_string(),
            pid: Some(999),
            timeout_s: Some(3),
            ..Default::default()
        };
        let unmet_res = execute(&unmet_req, &mut probe2).unwrap();
        assert!(!unmet_res.met);
        assert_eq!(
            unmet_res.observed,
            "timeout after 3s (last: pid 999 running)"
        );
        assert_eq!(probe2.now, 3);
    }

    #[test]
    fn hard_maximum_duration_and_result_json_shape() {
        let too_long = WatchRequest {
            kind: "pid_exit".to_string(),
            pid: Some(100),
            timeout_s: Some(MAX_TIMEOUT_S + 1),
            ..Default::default()
        };
        assert_eq!(
            too_long.validate(),
            Err(WatchError::TimeoutOutOfRange(MAX_TIMEOUT_S + 1))
        );

        let zero = WatchRequest {
            kind: "pid_exit".to_string(),
            pid: Some(100),
            timeout_s: Some(0),
            ..Default::default()
        };
        assert_eq!(zero.validate(), Err(WatchError::TimeoutOutOfRange(0)));

        let mut probe = FakeProbe::new();
        probe.pid_alive_polls.push_back(false);
        let ok_req = WatchRequest {
            kind: "pid_exit".to_string(),
            pid: Some(100),
            timeout_s: Some(10),
            ..Default::default()
        };
        let res = execute(&ok_req, &mut probe).unwrap();
        let json_val = serde_json::to_value(&res).unwrap();
        let obj = json_val.as_object().unwrap();
        assert_eq!(obj.len(), 3);
        assert_eq!(obj.get("met").and_then(Value::as_bool), Some(true));
        assert_eq!(
            obj.get("observed").and_then(Value::as_str),
            Some("pid 100 exited")
        );
        assert_eq!(
            obj.get("at").and_then(Value::as_str),
            Some("2026-10-27T12:00:00Z")
        );
    }

    #[test]
    fn read_only_curl_and_git_forms_are_allowed() {
        for allowed in ["curl -sSf http://127.0.0.1:8000/health", "curl --head http://localhost:3100/", "git -C . log -1", "git diff --stat"] {
            assert!(parse_and_check_allowlisted_command(allowed).is_ok(), "{allowed}");
        }
    }

    #[test]
    fn watch_has_no_benchmark_gate_because_it_uses_no_model() {
        // Spec D4: model-free work runs at any time, a benchmark night included.
        let mut probe = FakeProbe::new();
        probe.pid_alive_polls.push_back(false);
        let req = WatchRequest { kind: "pid_exit".to_string(), pid: Some(77), ..Default::default() };
        assert!(execute(&req, &mut probe).unwrap().met);
    }

    #[cfg(unix)]
    #[test]
    fn an_allowlisted_git_command_runs_no_program_the_checkouts_config_names() {
        use std::os::unix::fs::PermissionsExt;
        let root = std::env::temp_dir().join(format!("ling-watch-git-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let repo = root.join("repo");
        std::fs::create_dir_all(&repo).unwrap();
        let marker = root.join("marker");
        let tool = root.join("external");
        std::fs::write(&tool, format!("#!/bin/sh\necho ran >> '{}'\n", marker.display())).unwrap();
        std::fs::set_permissions(&tool, std::fs::Permissions::from_mode(0o755)).unwrap();
        let git = |args: &[&str]| {
            let ok = Command::new("git")
                .arg("-C")
                .arg(&repo)
                .args(["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"])
                .args(args)
                .env("GIT_CONFIG_NOSYSTEM", "1")
                .status()
                .unwrap()
                .success();
            assert!(ok, "git {args:?}");
        };
        git(&["init", "-q"]);
        std::fs::write(repo.join("a.txt"), "one\n").unwrap();
        git(&["add", "-A"]);
        git(&["commit", "-q", "-m", "init"]);
        git(&["config", "diff.external", tool.to_str().unwrap()]);
        std::fs::write(repo.join("a.txt"), "two\n").unwrap();

        let argv: Vec<String> = ["git", "-C", repo.to_str().unwrap(), "diff"].iter().map(|s| s.to_string()).collect();
        let out = SystemProbe::new().run_command(&argv).unwrap();
        assert_eq!(out.exit_code, 0, "{}", out.stderr);
        assert!(out.stdout.contains("+two"), "{}", out.stdout);
        assert!(!marker.exists(), "diff.external ran");
        let _ = std::fs::remove_dir_all(&root);
    }
}
