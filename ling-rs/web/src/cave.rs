//! How terse answers are, from outside a session (specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md §11.5):
//! Settings › General in a browser, and `/cavemode` in the phone bridges, which cannot read or
//! write the home folder. `/api/cavemode` answers the level in force (for a thread, or for a new
//! one) and sets it, for one thread or as the default for new sessions; a bridge passes what the
//! owner typed after `/cavemode` and gets back the lines `/cavemode` prints in the terminal. Every answer and change is
//! `ling cavemode`'s, the launcher's own resolver and writer, so there is no second copy of the
//! level's tiers here.

use std::process::Stdio;
use std::time::Duration;

use serde_json::Value;
use tokio::process::Command;

use crate::app_server::Launch;

/// The levels `ling cavemode` takes, and what removes the configured default.
pub const LEVELS: [&str; 4] = ["off", "lite", "full", "ultra"];
pub const BUILT_IN: &str = "built-in";

/// What a bridge answers a `/cavemode` it cannot pass on.
pub const USAGE: &str = "Usage: /cavemode [off|lite|full|ultra] for this thread, or /cavemode default <level|built-in> for new ones.";

/// What a client may ask for: the level in force, or a change, for one thread or for new sessions.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Request {
    Status { thread: Option<String> },
    SetThread { thread: String, level: String },
    SetDefault { level: String },
    /// `/cavemode <words>` from a phone bridge, for its current thread (None before the first
    /// question): what the terminal's `/cavemode` takes, and nothing else.
    Command { thread: Option<String>, words: Vec<String> },
}

impl Request {
    /// A POST's JSON body: `{"level": …}` for the default, `{"level": …, "thread": …}` for one
    /// thread. Anything else is refused with the reason.
    pub fn change(body: &Value) -> Result<Request, String> {
        if let Some(command) = body.get("command").and_then(Value::as_str) {
            return Request::command(body.get("thread").and_then(Value::as_str).filter(|id| !id.is_empty()), command);
        }
        let level = body.get("level").and_then(Value::as_str).map(str::to_ascii_lowercase).unwrap_or_default();
        let thread = body.get("thread").and_then(Value::as_str).filter(|id| !id.is_empty());
        match thread {
            Some(id) if !crate::ask::valid_thread_id(id) => Err("no such thread".to_string()),
            Some(id) if LEVELS.contains(&level.as_str()) => Ok(Request::SetThread { thread: id.to_string(), level }),
            Some(_) => Err("the level is off, lite, full or ultra".to_string()),
            None if LEVELS.contains(&level.as_str()) || level == BUILT_IN => Ok(Request::SetDefault { level }),
            None => Err("the level is off, lite, full, ultra or built-in".to_string()),
        }
    }

    /// What a bridge's owner typed after `/cavemode`: at most two words, each a level, `default` or
    /// `built-in`, so nothing but `/cavemode`'s own forms reaches the launcher.
    pub fn command(thread: Option<&str>, text: &str) -> Result<Request, String> {
        if thread.is_some_and(|id| !crate::ask::valid_thread_id(id)) {
            return Err("no such thread".to_string());
        }
        let words: Vec<String> = text.split_whitespace().map(str::to_ascii_lowercase).collect();
        let known = |word: &String| LEVELS.contains(&word.as_str()) || word == "default" || word == BUILT_IN;
        if words.len() > 2 || !words.iter().all(known) {
            return Err(USAGE.to_string());
        }
        Ok(Request::Command { thread: thread.map(str::to_string), words })
    }

    /// `ling cavemode`'s arguments for it.
    pub fn args(&self) -> Vec<String> {
        match self {
            Request::Status { thread: Some(id) } => vec!["--thread".to_string(), id.clone(), "--json".to_string()],
            Request::Status { thread: None } => vec!["--json".to_string()],
            Request::SetThread { thread, level } => vec!["--thread".to_string(), thread.clone(), level.clone()],
            Request::SetDefault { level } => vec!["default".to_string(), level.clone()],
            Request::Command { thread, words } => {
                let mut args = thread.iter().flat_map(|id| ["--thread".to_string(), id.clone()]).collect::<Vec<_>>();
                args.extend(words.iter().cloned());
                args
            }
        }
    }
}

/// Runs `ling cavemode` for `request`: what it printed, or why it failed.
pub async fn run(launch: &Launch, request: &Request) -> Result<String, String> {
    let mut command = Command::new(&launch.ling);
    command
        .arg("cavemode")
        .args(request.args())
        .envs(launch.env.iter().map(|(key, value)| (key.as_str(), value.as_str())))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let output = tokio::time::timeout(Duration::from_secs(15), command.output())
        .await
        .map_err(|_| "ling cavemode took too long to answer".to_string())?
        .map_err(|err| format!("could not run {}: {err}", launch.ling.display()))?;
    let said = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() {
        let reason = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if reason.is_empty() { said } else { reason });
    }
    Ok(said)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_change_names_a_level_and_at_most_a_valid_thread() {
        assert_eq!(Request::change(&json!({ "level": "Full" })), Ok(Request::SetDefault { level: "full".to_string() }));
        assert_eq!(Request::change(&json!({ "level": "built-in" })), Ok(Request::SetDefault { level: BUILT_IN.to_string() }));
        assert_eq!(
            Request::change(&json!({ "level": "off", "thread": "019a-b2" })),
            Ok(Request::SetThread { thread: "019a-b2".to_string(), level: "off".to_string() })
        );
        assert!(Request::change(&json!({ "level": "built-in", "thread": "019a" })).is_err());
        assert!(Request::change(&json!({ "level": "loud" })).is_err());
        assert!(Request::change(&json!({ "level": "lite", "thread": "../../etc" })).is_err());
        assert!(Request::change(&json!({})).is_err());
    }

    #[test]
    fn a_bridges_command_passes_only_cavemodes_own_forms() {
        assert_eq!(
            Request::change(&json!({ "command": "Full", "thread": "t1" })),
            Ok(Request::Command { thread: Some("t1".to_string()), words: vec!["full".to_string()] })
        );
        assert_eq!(Request::command(None, ""), Ok(Request::Command { thread: None, words: vec![] }));
        assert_eq!(Request::command(None, "default built-in").map(|r| r.args()), Ok(vec!["default".to_string(), "built-in".to_string()]));
        assert_eq!(Request::command(Some("t1"), "lite").map(|r| r.args()), Ok(vec!["--thread".to_string(), "t1".to_string(), "lite".to_string()]));
        assert_eq!(Request::command(None, "--json"), Err(USAGE.to_string()));
        assert_eq!(Request::command(None, "default full now"), Err(USAGE.to_string()));
        assert!(Request::command(Some("a/b"), "lite").is_err());
    }

    #[test]
    fn each_request_becomes_the_launchers_arguments() {
        assert_eq!(Request::Status { thread: None }.args(), vec!["--json"]);
        assert_eq!(Request::Status { thread: Some("t1".to_string()) }.args(), vec!["--thread", "t1", "--json"]);
        assert_eq!(Request::SetThread { thread: "t1".to_string(), level: "full".to_string() }.args(), vec!["--thread", "t1", "full"]);
        assert_eq!(Request::SetDefault { level: "built-in".to_string() }.args(), vec!["default", "built-in"]);
    }
}
