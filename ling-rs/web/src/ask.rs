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
    new_folder_for(root, "ask")
}

/// The prefix of a new folder's name for a prompt: `chat-` for a phone bridge's thread, `q-` for
/// every other. The name is how the model gate's extension tells a phone's thread from a tab's, by
/// its working folder (specs/DREAMFERENCE_MIGHTLING_CHAT.md §16.3): nothing extra reaches the model.
pub fn folder_prefix(prompt: &str) -> &'static str {
    if prompt == "chat" { "chat-" } else { "q-" }
}

/// Creates a new, empty folder for one Ask thread started with the prompt `prompt`.
pub fn new_folder_for(root: &Path, prompt: &str) -> std::io::Result<PathBuf> {
    crate::auth::private_dir(root)?;
    let folder = root.join(format!("{}{}", folder_prefix(prompt), crate::auth::random_hex(8)));
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

/// Whether a paired device's name is a phone bridge's device name (`signal-bridge`, or a named
/// Signal bridge instance `signal-<instance>`).
pub fn is_bridge_device(name: &str) -> bool {
    if name == "signal-bridge" {
        return true;
    }
    let Some(instance) = name.strip_prefix("signal-") else {
        return false;
    };
    !instance.is_empty()
        && instance.len() <= 12
        && instance != "default"
        && instance.as_bytes()[0].is_ascii_lowercase()
        && instance.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// What the policy layer asks while vetting one message: the named prompt's text, fetched before
/// the message is vetted, and the Ask folders.
pub struct MessagePrompts<'a> {
    pub root: &'a Path,
    /// The prompt the message names, and its composed text.
    pub composed: Option<(String, String)>,
    /// The folder a vetted `thread/start` created, if it created one.
    pub created: Mutex<Option<PathBuf>>,
    /// Whether the configured air-gap level is `on`, read when the message arrived.
    pub airgapped_on: bool,
    /// The folder the thread the message names works in, when the server has said: a thread's
    /// air-gap level is read with that repository's configuration file too.
    pub thread_folder: Option<PathBuf>,
}

impl<'a> MessagePrompts<'a> {
    fn codex_home(&self) -> &Path {
        self.root.parent().unwrap_or(self.root)
    }

    /// Marks whether this message came from a paired phone bridge tab.
    pub fn with_bridge(self, bridge: bool) -> TabPrompts<'a> {
        TabPrompts { inner: self, bridge }
    }
}

/// [`MessagePrompts`] with the tab's phone-bridge flag attached.
pub struct TabPrompts<'a> {
    pub inner: MessagePrompts<'a>,
    pub bridge: bool,
}

impl PromptSource for MessagePrompts<'_> {
    fn composed(&self, name: &str) -> Result<String, String> {
        match &self.composed {
            Some((composed_name, text)) if composed_name == name => Ok(text.clone()),
            _ => Err(format!("the prompt {name} could not be composed")),
        }
    }

    fn new_scratch_folder(&self, prompt: &str) -> Result<String, String> {
        let folder = new_folder_for(self.root, prompt).map_err(|err| format!("could not create an Ask folder: {err}"))?;
        *self.created.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(folder.clone());
        Ok(folder.to_string_lossy().into_owned())
    }

    fn scratch_folder_of(&self, thread_id: &str) -> Option<String> {
        folder_of(self.root, thread_id).map(|folder| folder.to_string_lossy().into_owned())
    }

    fn airgapped_on(&self, thread_id: Option<&str>) -> bool {
        match thread_id {
            None => self.airgapped_on,
            Some(thread) => crate::server::thread_airgapped_on(Some(thread), self.thread_folder.as_deref()),
        }
    }

    fn repo_checkout(&self, cwd: &str) -> Option<bool> {
        crate::chat_repos::repo_checkout(self.codex_home(), Path::new(cwd))
    }

    fn repo_thread_cwd(&self, thread_id: &str) -> Option<String> {
        crate::chat_repos::repo_thread_cwd(self.codex_home(), thread_id)
    }

    fn is_bridge(&self) -> bool {
        false
    }
}

impl PromptSource for TabPrompts<'_> {
    fn composed(&self, name: &str) -> Result<String, String> {
        self.inner.composed(name)
    }

    fn new_scratch_folder(&self, prompt: &str) -> Result<String, String> {
        self.inner.new_scratch_folder(prompt)
    }

    fn scratch_folder_of(&self, thread_id: &str) -> Option<String> {
        self.inner.scratch_folder_of(thread_id)
    }

    fn airgapped_on(&self, thread_id: Option<&str>) -> bool {
        self.inner.airgapped_on(thread_id)
    }

    fn repo_checkout(&self, cwd: &str) -> Option<bool> {
        self.inner.repo_checkout(cwd)
    }

    fn repo_thread_cwd(&self, thread_id: &str) -> Option<String> {
        self.inner.repo_thread_cwd(thread_id)
    }

    fn is_bridge(&self) -> bool {
        self.bridge
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
    fn a_phone_threads_folder_says_so_in_its_name() {
        let home = scratch("prefix");
        let root = root(&home);
        let chat = new_folder_for(&root, "chat").unwrap();
        let ask = new_folder_for(&root, "ask").unwrap();
        let name = |p: &Path| p.file_name().unwrap().to_string_lossy().into_owned();
        assert!(name(&chat).starts_with("chat-") && name(&chat).len() == 5 + 16, "{chat:?}");
        assert!(name(&ask).starts_with("q-") && is_folder(&root, &chat) && is_folder(&root, &ask));
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

    #[test]
    fn bridge_device_names_and_message_prompts_repo_checks() {
        assert!(is_bridge_device("signal-bridge"));
        assert!(is_bridge_device("signal-work"));
        assert!(!is_bridge_device("signal-default"));
        assert!(!is_bridge_device("signal-"));
        assert!(!is_bridge_device("owner"));
        assert!(!is_bridge_device("My iPhone"));

        let home = scratch("repo-prompts");
        let root = root(&home);
        std::fs::create_dir_all(&root).unwrap();
        let checkout = crate::chat_repos::root_dir(&home).join("org_widget").join("deadbeef");
        let outside = home.join("outside");
        std::fs::create_dir_all(&checkout).unwrap();
        std::fs::create_dir_all(&outside).unwrap();

        let record = crate::chat_repos::CheckoutRecord {
            thread: "deadbeef".to_string(),
            repo: "org/widget".to_string(),
            bridge: "signal".to_string(),
            chat: "note-to-self".to_string(),
            device: "signal-bridge".to_string(),
            dir: checkout.to_string_lossy().into_owned(),
            branch: Some("phone/deadbeef".to_string()),
            base: "main".to_string(),
            app_threads: Vec::new(),
            read_only: false,
            created: 1,
            last_used: 1,
        };
        crate::chat_repos::save_index(&home, &[record]).unwrap();

        let prompts = MessagePrompts {
            root: &root,
            composed: None,
            created: Mutex::new(None),
            airgapped_on: false,
            thread_folder: None,
        }
        .with_bridge(true);

        let canonical_checkout = checkout.canonicalize().unwrap().to_string_lossy().into_owned();
        assert!(prompts.is_bridge());
        assert_eq!(prompts.repo_checkout(&checkout.to_string_lossy()), Some(false));
        assert_eq!(prompts.repo_checkout(&outside.to_string_lossy()), None);
        assert_eq!(prompts.repo_thread_cwd("app-t1"), None);
        assert!(crate::chat_repos::record_app_thread(&home, &checkout, "app-t1").unwrap());
        assert_eq!(prompts.repo_thread_cwd("app-t1"), Some(canonical_checkout.clone()));
        assert!(crate::chat_repos::record_app_thread(&home, &checkout, "app-t2").unwrap());
        assert_eq!(prompts.repo_thread_cwd("app-t1"), Some(canonical_checkout.clone()));
        assert_eq!(prompts.repo_thread_cwd("app-t2"), Some(canonical_checkout));

        let _ = std::fs::remove_dir_all(&home);
    }
}
