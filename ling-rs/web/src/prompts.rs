//! A named prompt's text, as the bridge policy sets it (specs/DREAMFERENCE_MIGHTLING_ASK.md §3.2):
//! `ling prompt show <name> --composed`, run in the Ask root so the blocks it composes (the code
//! block reads the folder it runs in) are what a thread there would get. The UI only ever names
//! the prompt; the text comes from here.

use std::path::Path;
use std::process::Stdio;
use std::time::Duration;

use tokio::process::Command;

use crate::app_server::Launch;

/// Composing reads the apps' state and the code index; it never takes this long.
const COMPOSE_TIMEOUT: Duration = Duration::from_secs(60);

pub async fn composed(launch: &Launch, name: &str, cwd: &Path) -> Result<String, String> {
    crate::auth::private_dir(cwd).map_err(|err| format!("could not prepare {}: {err}", cwd.display()))?;
    let mut command = Command::new(&launch.ling);
    command
        .args(["prompt", "show", name, "--composed"])
        .current_dir(cwd)
        .envs(launch.env.iter().map(|(key, value)| (key.as_str(), value.as_str())))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let output = tokio::time::timeout(COMPOSE_TIMEOUT, command.output())
        .await
        .map_err(|_| format!("composing the {name} prompt took too long"))?
        .map_err(|err| format!("could not run {}: {err}", launch.ling.display()))?;
    let text = String::from_utf8_lossy(&output.stdout).into_owned();
    if !output.status.success() || text.trim().is_empty() {
        let reason = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(format!("the {name} prompt could not be composed: {reason}"));
    }
    Ok(text)
}
