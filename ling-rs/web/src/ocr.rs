//! The text on an image a phone bridge uploads (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16): a
//! screenshot's words reach the model as text, whether or not the model takes images.
//!
//! The reader is Tesseract (`tesseract`, the distribution's package, installed by `ling signal
//! setup` and `ling chat start`), run as a child with a time limit, one thread and a capped
//! address space, so a large or hostile image cannot hold the machine beside a resident model
//! server. It reads every language whose data is installed (`tesseract --list-langs`), so adding a
//! language is `apt install tesseract-ocr-<code>`. Nothing here touches a network.
//!
//! [`OCR_ENV`] names another program with Tesseract's command line, for the tests' stand-in.

use std::path::Path;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::OnceLock;
use std::time::Duration;

use tokio::process::Command;

/// Another program to run in Tesseract's place (the tests' stand-in), or `off` for none.
pub const OCR_ENV: &str = "DREAMFERENCE_MIGHTLING_OCR";
/// One image is read in this long, or not at all.
pub const OCR_TIMEOUT: Duration = Duration::from_secs(30);
/// The reader's address space: Tesseract's LSTM reader needs a few hundred MB on one thread.
pub const OCR_MEMORY_BYTES: u64 = 1536 << 20;
/// At most this many installed languages are read at once (each slows the reader down).
pub const MAX_LANGUAGES: usize = 6;
/// The longest text passed on: a screenshot's worth, not a scanned book's.
pub const TEXT_LIMIT: usize = 4_000;

/// The reader to run: [`OCR_ENV`]'s program, else `tesseract` on `PATH`; None when there is none
/// or the variable says `off`.
pub fn program() -> Option<PathBuf> {
    match std::env::var(OCR_ENV) {
        Ok(value) if value.trim().eq_ignore_ascii_case("off") => None,
        Ok(value) if !value.trim().is_empty() => Some(PathBuf::from(value.trim())),
        _ => find_on_path("tesseract"),
    }
}

fn find_on_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).map(|dir| dir.join(name)).find(|candidate| candidate.is_file())
}

/// The languages to read, from `tesseract --list-langs`: every installed one but `osd` (page
/// orientation data, not a language), English first, at most [`MAX_LANGUAGES`].
pub fn languages_from_list(output: &str) -> Vec<String> {
    let mut languages: Vec<String> = output
        .lines()
        .skip_while(|line| line.contains("List of available languages"))
        .map(str::trim)
        .filter(|line| !line.is_empty() && *line != "osd" && *line != "equ" && line.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
        .map(str::to_string)
        .collect();
    languages.sort_by_key(|language| (language != "eng", language.clone()));
    languages.dedup();
    languages.truncate(MAX_LANGUAGES);
    languages
}

fn limited(program: &Path) -> Command {
    let mut command = Command::new(program);
    command
        .env("OMP_THREAD_LIMIT", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    #[cfg(unix)]
    {
        // SAFETY: setrlimit is async-signal-safe and touches only the child about to exec.
        unsafe {
            command.pre_exec(|| {
                let limit = libc::rlimit { rlim_cur: OCR_MEMORY_BYTES as libc::rlim_t, rlim_max: OCR_MEMORY_BYTES as libc::rlim_t };
                libc::setrlimit(libc::RLIMIT_AS, &limit);
                Ok(())
            });
        }
    }
    command
}

async fn installed_languages(program: &Path) -> Vec<String> {
    static CACHED: OnceLock<Vec<String>> = OnceLock::new();
    if let Some(languages) = CACHED.get() {
        return languages.clone();
    }
    let mut command = limited(program);
    command.arg("--list-langs");
    let languages = match tokio::time::timeout(OCR_TIMEOUT, command.output()).await {
        Ok(Ok(output)) if output.status.success() => languages_from_list(&String::from_utf8_lossy(&output.stdout)),
        _ => Vec::new(),
    };
    if !languages.is_empty() {
        let _ = CACHED.set(languages.clone());
    }
    languages
}

/// What the reader printed, made fit to pass on: each line's spaces collapsed, empty lines merged,
/// cut at [`TEXT_LIMIT`]. None when fewer than three letters or digits were read, which is what a
/// photo without writing gives.
pub fn clean(raw: &str) -> Option<String> {
    let mut lines: Vec<String> = Vec::new();
    for line in raw.lines() {
        let line = line.split_whitespace().collect::<Vec<_>>().join(" ");
        if line.is_empty() {
            if lines.last().is_some_and(|last| !last.is_empty()) {
                lines.push(String::new());
            }
        } else {
            lines.push(line);
        }
    }
    let text = lines.join("\n").trim().to_string();
    if text.chars().filter(|c| c.is_alphanumeric()).count() < 3 {
        return None;
    }
    if text.chars().count() <= TEXT_LIMIT {
        return Some(text);
    }
    let mut cut: String = text.chars().take(TEXT_LIMIT).collect();
    cut.push('…');
    Some(cut)
}

/// The text on the image at `image`, if a reader is installed and it finds any.
pub async fn read_text(image: &Path) -> Option<String> {
    let program = program()?;
    let languages = installed_languages(&program).await;
    let mut command = limited(&program);
    command.arg(image).arg("stdout");
    if !languages.is_empty() {
        command.arg("-l").arg(languages.join("+"));
    }
    match tokio::time::timeout(OCR_TIMEOUT, command.output()).await {
        Ok(Ok(output)) if output.status.success() => clean(&String::from_utf8_lossy(&output.stdout)),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn languages_skip_orientation_data_and_put_english_first() {
        let listed = "List of available languages in \"/usr/share/tesseract-ocr/5/tessdata/\" (4):\nosd\npor\neng\nrus\n";
        assert_eq!(languages_from_list(listed), ["eng", "por", "rus"]);
        let many: String = (0..10).map(|n| format!("l{n}\n")).collect();
        assert_eq!(languages_from_list(&many).len(), MAX_LANGUAGES);
        assert!(languages_from_list("").is_empty());
    }

    #[test]
    fn read_text_is_tidied_and_noise_is_none() {
        assert_eq!(clean("  Air   purifier  \n\n\n\nHEPA 13 filter\n").as_deref(), Some("Air purifier\n\nHEPA 13 filter"));
        assert_eq!(clean(" | , ."), None);
        assert_eq!(clean(""), None);
        let long = "word ".repeat(2_000);
        let cut = clean(&long).unwrap();
        assert!(cut.ends_with('…') && cut.chars().count() == TEXT_LIMIT + 1);
    }
}
