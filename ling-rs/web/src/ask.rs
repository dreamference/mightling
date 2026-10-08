//! Ask threads' scratch folders (specs/DREAMFERENCE_MIGHTLING_ASK.md §3.1), under
//! `$CODEX_HOME/ask/`.
//!
//! The thread id does not exist yet when `thread/start` is vetted, and a running session's `cwd`
//! cannot be renamed, so each folder is created under a random name and, once the server answers
//! with the new thread's id, `ask/<thread-id>` is made a link to it. The link is also how an Ask
//! thread is recognised later: a thread with one is resumed and forked in its folder, whatever the
//! UI asks for, and uploads for it land there.

use std::path::Path;
use std::path::PathBuf;
use std::sync::Mutex;

use crate::policy::PromptSource;

/// Whether a thread id is safe as a file name here.
pub fn valid_thread_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 128 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// The root of every Ask folder.
pub fn root(codex_home: &Path) -> PathBuf {
    codex_home.join("ask")
}

/// Creates a new, empty folder for one Ask thread.
pub fn new_folder(root: &Path) -> std::io::Result<PathBuf> {
    crate::auth::private_dir(root)?;
    let folder = root.join(format!("q-{}", crate::auth::random_hex(8)));
    std::fs::create_dir(&folder)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&folder, std::fs::Permissions::from_mode(0o700))?;
    }
    folder.canonicalize()
}

/// The folder of an Ask thread, or None for any other thread.
pub fn folder_of(root: &Path, thread_id: &str) -> Option<PathBuf> {
    if !valid_thread_id(thread_id) {
        return None;
    }
    let folder = root.join(thread_id).canonicalize().ok()?;
    let root = root.canonicalize().ok()?;
    (folder.is_dir() && folder.parent() == Some(root.as_path())).then_some(folder)
}

/// Whether a path is one of the Ask folders.
pub fn is_folder(root: &Path, path: &Path) -> bool {
    let (Ok(path), Ok(root)) = (path.canonicalize(), root.canonicalize()) else { return false };
    path.is_dir() && path.parent() == Some(root.as_path())
}

/// Names a folder after the thread that works in it: `ask/<thread-id>` → the folder.
pub fn link_thread(root: &Path, thread_id: &str, folder: &Path) -> std::io::Result<()> {
    if !valid_thread_id(thread_id) {
        return Err(std::io::Error::other(format!("not a thread id: {thread_id}")));
    }
    let link = root.join(thread_id);
    if link.symlink_metadata().is_ok() {
        return Ok(());
    }
    let target = folder.file_name().map(PathBuf::from).unwrap_or_else(|| folder.to_path_buf());
    #[cfg(unix)]
    return std::os::unix::fs::symlink(target, link);
    #[cfg(not(unix))]
    return std::fs::write(link, target.to_string_lossy().as_bytes());
}

/// What the policy layer asks while vetting one message: the named prompt's text, fetched before
/// the message is vetted, and the Ask folders.
pub struct MessagePrompts<'a> {
    pub root: &'a Path,
    /// The prompt the message names, and its composed text.
    pub composed: Option<(String, String)>,
    /// The folder a vetted `thread/start` created, if it created one.
    pub created: Mutex<Option<PathBuf>>,
}

impl PromptSource for MessagePrompts<'_> {
    fn composed(&self, name: &str) -> Result<String, String> {
        match &self.composed {
            Some((composed_name, text)) if composed_name == name => Ok(text.clone()),
            _ => Err(format!("the prompt {name} could not be composed")),
        }
    }

    fn new_scratch_folder(&self) -> Result<String, String> {
        let folder = new_folder(self.root).map_err(|err| format!("could not create an Ask folder: {err}"))?;
        *self.created.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(folder.clone());
        Ok(folder.to_string_lossy().into_owned())
    }

    fn scratch_folder_of(&self, thread_id: &str) -> Option<String> {
        folder_of(self.root, thread_id).map(|folder| folder.to_string_lossy().into_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ling-web-ask-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_folder_is_named_after_its_thread_once_the_thread_exists() {
        let home = scratch("link");
        let root = root(&home);
        let folder = new_folder(&root).unwrap();
        assert!(is_folder(&root, &folder));
        assert_eq!(folder_of(&root, "0199-thread"), None);
        link_thread(&root, "0199-thread", &folder).unwrap();
        assert_eq!(folder_of(&root, "0199-thread"), Some(folder.clone()));
        // A second link for the same thread changes nothing.
        link_thread(&root, "0199-thread", &folder).unwrap();
        assert!(link_thread(&root, "../escape", &folder).is_err());
        assert_eq!(folder_of(&root, "../escape"), None);
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn a_link_out_of_the_ask_root_is_not_an_ask_folder() {
        let home = scratch("outside");
        let root = root(&home);
        std::fs::create_dir_all(&root).unwrap();
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&home, root.join("planted")).unwrap();
            assert_eq!(folder_of(&root, "planted"), None);
            assert!(!is_folder(&root, &home));
        }
        let _ = std::fs::remove_dir_all(&home);
    }
}
