//! Citations that open in a browser (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §8.3): a passage
//! the local file index cites links to `docs:<collection>/<document>`, which the UI turns into
//! `/docs/<collection>/<document>` here. The file is found by `ling docs locate`, which answers
//! only for a document of a configured collection whose resolved path stays under the
//! collection's root; this module never takes a path from the request.
//!
//! What a browser may run is kept from this origin, where the viewer's credential lives: a PDF is
//! shown inline (the browser's viewer runs a PDF's scripts, if any, in its own sandbox), plain
//! text inline under `sandbox`, and every other kind (Office files, HTML pages, mail) is a
//! download, so a saved web page's scripts never run as `ling web`.

use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use tokio::process::Command;

use crate::app_server::Launch;

/// The largest document served (a collection indexes files up to 50 MB by default).
pub const DOCUMENT_CAP: u64 = 200 * 1024 * 1024;
const LOCATE_TIMEOUT: Duration = Duration::from_secs(20);

/// A collection's name and a document's number, as `ling-docs` writes them.
pub fn valid(collection: &str, id: &str) -> bool {
    !collection.is_empty()
        && collection.len() <= 64
        && collection.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
        && !collection.starts_with('.')
        && !id.is_empty()
        && id.len() <= 18
        && id.chars().all(|c| c.is_ascii_digit())
}

/// What `ling docs locate --json` answers.
#[derive(Debug, PartialEq)]
pub struct Located {
    pub path: PathBuf,
    pub rel: String,
    pub kind: String,
    pub size: u64,
}

pub async fn locate(launch: &Launch, collection: &str, id: &str) -> Result<Located, String> {
    let mut command = Command::new(&launch.ling);
    command
        .args(["docs", "locate", &format!("{collection}/{id}"), "--json"])
        .envs(launch.env.iter().map(|(key, value)| (key.as_str(), value.as_str())))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let output = tokio::time::timeout(LOCATE_TIMEOUT, command.output())
        .await
        .map_err(|_| "finding the document took too long".to_string())?
        .map_err(|err| format!("could not run {}: {err}", launch.ling.display()))?;
    if !output.status.success() {
        let reason = String::from_utf8_lossy(&output.stderr).trim().trim_start_matches("ling-docs: ").to_string();
        return Err(if reason.is_empty() { "no such document".to_string() } else { reason });
    }
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|_| "the index gave no answer".to_string())?;
    let text = |key: &str| value.get(key).and_then(|v| v.as_str()).map(str::to_string);
    match (text("path"), text("rel"), text("kind"), value.get("size").and_then(|v| v.as_u64())) {
        (Some(path), Some(rel), Some(kind), Some(size)) if path.starts_with('/') => Ok(Located { path: PathBuf::from(path), rel, kind, size }),
        _ => Err("the index gave no answer".to_string()),
    }
}

/// Settings › Your documents (LOCAL_INDEX §8.3): `ling docs <args>`, its output or why it failed.
/// Every change to the collections goes through `ling docs`, which applies the index's rules
/// (§5: never `~`, `/` or a secret folder; removing deletes the index, never the files).
pub async fn docs_cli(launch: &Launch, args: &[&str]) -> Result<String, String> {
    let mut command = Command::new(&launch.ling);
    command
        .arg("docs")
        .args(args)
        .envs(launch.env.iter().map(|(key, value)| (key.as_str(), value.as_str())))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let output = tokio::time::timeout(Duration::from_secs(30), command.output())
        .await
        .map_err(|_| "the local file index took too long to answer".to_string())?
        .map_err(|err| format!("could not run {}: {err}", launch.ling.display()))?;
    let said = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() {
        let reason = String::from_utf8_lossy(&output.stderr).trim().trim_start_matches("ling-docs: ").to_string();
        return Err(if reason.is_empty() { said } else { reason });
    }
    Ok(said)
}

/// A collection's name, as `ling-docs` makes them.
pub fn valid_collection(name: &str) -> bool {
    valid(name, "1")
}

/// How a kind is served: its type, whether inline, and its content security policy.
pub fn presentation(kind: &str) -> (&'static str, bool, &'static str) {
    const TEXT_POLICY: &str = "sandbox; default-src 'none'";
    match kind {
        "pdf" => ("application/pdf", true, "default-src 'none'; object-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
        "text" | "markdown" | "rst" | "org" | "tex" | "csv" => ("text/plain; charset=utf-8", true, TEXT_POLICY),
        "docx" => ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", false, TEXT_POLICY),
        "odt" => ("application/vnd.oasis.opendocument.text", false, TEXT_POLICY),
        "xlsx" => ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", false, TEXT_POLICY),
        "ods" => ("application/vnd.oasis.opendocument.spreadsheet", false, TEXT_POLICY),
        "pptx" => ("application/vnd.openxmlformats-officedocument.presentationml.presentation", false, TEXT_POLICY),
        "odp" => ("application/vnd.oasis.opendocument.presentation", false, TEXT_POLICY),
        "eml" => ("message/rfc822", false, TEXT_POLICY),
        _ => ("application/octet-stream", false, TEXT_POLICY),
    }
}

/// `Content-Disposition` for a file name: ASCII fallback and the UTF-8 name, percent-encoded.
pub fn disposition(inline: bool, rel: &str) -> String {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    let ascii: String = name.chars().map(|c| if c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_' | ' ') { c } else { '_' }).collect();
    let encoded: String = name
        .bytes()
        .map(|b| if b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'_') { (b as char).to_string() } else { format!("%{b:02X}") })
        .collect();
    format!("{}; filename=\"{ascii}\"; filename*=UTF-8''{encoded}", if inline { "inline" } else { "attachment" })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_collection_name_and_a_number_pass() {
        assert!(valid("documents", "42"));
        assert!(valid("design-notes", "1"));
        for (collection, id) in [("..", "1"), ("a/b", "1"), ("documents", "1.pdf"), ("", "1"), ("documents", ""), (".hidden", "1"), ("documents", "-1")] {
            assert!(!valid(collection, id), "{collection} {id}");
        }
    }

    #[test]
    fn only_pdfs_and_plain_text_are_shown_and_never_a_page() {
        assert_eq!(presentation("pdf").1, true);
        assert_eq!(presentation("markdown"), ("text/plain; charset=utf-8", true, "sandbox; default-src 'none'"));
        assert_eq!(presentation("html").1, false, "a saved page's scripts never run here");
        assert_eq!(disposition(false, "mail/Re: été.eml"), "attachment; filename=\"Re_ _t_.eml\"; filename*=UTF-8''Re%3A%20%C3%A9t%C3%A9.eml");
    }
}
