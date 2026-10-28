//! Typed repository notices for Telegram and Discord (CHAT_REPOS §2, §5.3, §12.2).
//!
//! `RepoNotice` has no free-text field. Every string component (`RepoName`, `BranchName`,
//! `AppThreadId`) is validated by a strict parser before construction, counts are `u32`, and
//! actions are the fixed `RefusedAction` enum.

use std::fmt;

use ling_web_server::telegram_app::valid_open_thread_id;
use serde_json::{Value, json};

/// A validated forge repository name `<owner>/<name>`.
///
/// Matches `^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}$` with no `.` or `..` segments and no
/// `..` substring.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct RepoName(String);

impl RepoName {
    pub fn parse(s: &str) -> Option<Self> {
        let (owner, name) = s.split_once('/')?;
        if name.contains('/') || s.contains("..") {
            return None;
        }
        if !valid_repo_segment(owner) || !valid_repo_segment(name) {
            return None;
        }
        Some(Self(s.to_string()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

fn valid_repo_segment(s: &str) -> bool {
    if s.is_empty() || s.len() > 100 || s == "." || s == ".." {
        return false;
    }
    s.bytes()
        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

impl fmt::Display for RepoName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A validated git branch name.
///
/// Matches `^[A-Za-z0-9._/-]{1,100}$`, does not contain `..` or `//`, does not start with `-` or
/// `/`, does not end with `/`, and is not `.`.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct BranchName(String);

impl BranchName {
    pub fn parse(s: &str) -> Option<Self> {
        if s.is_empty()
            || s.len() > 100
            || s == "."
            || s.starts_with('-')
            || s.starts_with('/')
            || s.ends_with('/')
            || s.contains("..")
            || s.contains("//")
        {
            return None;
        }
        if !s
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'/' | b'-'))
        {
            return None;
        }
        Some(Self(s.to_string()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for BranchName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A validated app-server thread identifier (`telegram_app::valid_open_thread_id`).
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct AppThreadId(String);

impl AppThreadId {
    pub fn parse(s: &str) -> Option<Self> {
        if !valid_open_thread_id(s) || s.contains("..") {
            return None;
        }
        Some(Self(s.to_string()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for AppThreadId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Fixed set of repository actions that can be refused (CHAT_REPOS §5.3).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum RefusedAction {
    Commit,
    Push,
    Update,
    Merge,
    Pr,
    Branch,
}

impl RefusedAction {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "Commit" | "commit" => Some(Self::Commit),
            "Push" | "push" => Some(Self::Push),
            "Update" | "update" => Some(Self::Update),
            "Merge" | "merge" => Some(Self::Merge),
            "Pr" | "pr" | "PR" => Some(Self::Pr),
            "Branch" | "branch" => Some(Self::Branch),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Commit => "Commit",
            Self::Push => "Push",
            Self::Update => "Update",
            Self::Merge => "Merge",
            Self::Pr => "Pr",
            Self::Branch => "Branch",
        }
    }
}

impl fmt::Display for RefusedAction {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Fixed repository notice kinds (CHAT_REPOS §5.3).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RepoNoticeKind {
    Switched { branch: BranchName },
    TurnFinished { files: u32 },
    TurnStopped,
    TurnFailed,
    ApprovalWaiting,
    ApprovalDeclineSoon,
    Pushed { commits: u32 },
    Merged { branch: BranchName, into: BranchName },
    Refused { action: RefusedAction },
    Budget80,
    Busy,
    RepoOff { uncommitted: u32 },
}

impl RepoNoticeKind {
    pub fn kind_name(&self) -> &'static str {
        match self {
            Self::Switched { .. } => "switched",
            Self::TurnFinished { .. } => "turn_finished",
            Self::TurnStopped => "turn_stopped",
            Self::TurnFailed => "turn_failed",
            Self::ApprovalWaiting => "approval_waiting",
            Self::ApprovalDeclineSoon => "approval_decline_soon",
            Self::Pushed { .. } => "pushed",
            Self::Merged { .. } => "merged",
            Self::Refused { .. } => "refused",
            Self::Budget80 => "budget80",
            Self::Busy => "busy",
            Self::RepoOff { .. } => "repo_off",
        }
    }
}

/// SIGNAL §17.3 fixed benchmark refusal line.
pub const BUSY_BENCHMARK_TEXT: &str = "The model on the node is busy with a benchmark run, so this question was not answered. Ask again when the run has finished.";

/// Typed repository notice delivered to Telegram or Discord (CHAT_REPOS §5.3, §12.2).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RepoNotice {
    pub seq: u64,
    pub chat_key: String,
    pub repo: RepoName,
    pub app_thread: Option<AppThreadId>,
    pub kind: RepoNoticeKind,
}

impl RepoNotice {
    /// Renders the notice text using the exact §5.3 templates.
    pub fn text(&self) -> String {
        let repo = &self.repo;
        match &self.kind {
            RepoNoticeKind::Switched { branch } => {
                format!("Working in {repo} on {branch}.")
            }
            RepoNoticeKind::TurnFinished { files } => {
                format!("Turn finished in {repo}: {files} files changed.")
            }
            RepoNoticeKind::TurnStopped => {
                format!("Turn stopped in {repo}.")
            }
            RepoNoticeKind::TurnFailed => {
                format!("Turn failed in {repo}; open the app.")
            }
            RepoNoticeKind::ApprovalWaiting => {
                format!("Approval waiting in {repo}.")
            }
            RepoNoticeKind::ApprovalDeclineSoon => {
                "It will be declined in 10 minutes.".to_string()
            }
            RepoNoticeKind::Pushed { commits } => {
                format!("Pushed {commits} commits of {repo}.")
            }
            RepoNoticeKind::Merged { branch, into } => {
                format!("Merged {branch} into {into} in {repo}.")
            }
            RepoNoticeKind::Refused { action } => {
                format!("{action} refused in {repo}; open the app.")
            }
            RepoNoticeKind::Budget80 => {
                format!("{repo}: 80% of today's model budget used.")
            }
            RepoNoticeKind::Busy => BUSY_BENCHMARK_TEXT.to_string(),
            RepoNoticeKind::RepoOff { uncommitted } => {
                format!("Back to Ask; {uncommitted} files uncommitted in {repo}.")
            }
        }
    }

    /// True when the notice's Open button should deep-link directly to the approval strip.
    pub fn is_approval(&self) -> bool {
        matches!(
            self.kind,
            RepoNoticeKind::ApprovalWaiting | RepoNoticeKind::ApprovalDeclineSoon
        )
    }

    /// Serializes this notice to its canonical JSON representation.
    pub fn to_json(&self) -> Value {
        let mut obj = serde_json::Map::new();
        obj.insert("seq".to_string(), json!(self.seq));
        obj.insert("chat_key".to_string(), json!(self.chat_key));
        obj.insert("repo".to_string(), json!(self.repo.as_str()));
        obj.insert(
            "app_thread".to_string(),
            match &self.app_thread {
                Some(t) => json!(t.as_str()),
                None => Value::Null,
            },
        );
        obj.insert("kind".to_string(), json!(self.kind.kind_name()));
        match &self.kind {
            RepoNoticeKind::Switched { branch } => {
                obj.insert("branch".to_string(), json!(branch.as_str()));
            }
            RepoNoticeKind::TurnFinished { files } => {
                obj.insert("files".to_string(), json!(files));
            }
            RepoNoticeKind::TurnStopped
            | RepoNoticeKind::TurnFailed
            | RepoNoticeKind::ApprovalWaiting
            | RepoNoticeKind::ApprovalDeclineSoon
            | RepoNoticeKind::Budget80
            | RepoNoticeKind::Busy => {}
            RepoNoticeKind::Pushed { commits } => {
                obj.insert("commits".to_string(), json!(commits));
            }
            RepoNoticeKind::Merged { branch, into } => {
                obj.insert("branch".to_string(), json!(branch.as_str()));
                obj.insert("into".to_string(), json!(into.as_str()));
            }
            RepoNoticeKind::Refused { action } => {
                obj.insert("action".to_string(), json!(action.as_str()));
            }
            RepoNoticeKind::RepoOff { uncommitted } => {
                obj.insert("uncommitted".to_string(), json!(uncommitted));
            }
        }
        Value::Object(obj)
    }

    /// Strictly parses a `RepoNotice` from JSON.
    ///
    /// Any field that fails its parser, or any extra or unknown field, drops the entire notice and
    /// logs only the sanitized kind.
    pub fn parse(json: &Value) -> Option<Self> {
        let Some(obj) = json.as_object() else {
            log_dropped("unknown");
            return None;
        };
        let raw_kind = obj.get("kind").and_then(Value::as_str).unwrap_or("unknown");
        let safe_kind = sanitize_kind_for_log(raw_kind);
        let parsed = Self::parse_inner(obj, raw_kind);
        if parsed.is_none() {
            log_dropped(safe_kind);
        }
        parsed
    }

    fn parse_inner(obj: &serde_json::Map<String, Value>, raw_kind: &str) -> Option<Self> {
        let allowed_extra: &[&str] = match raw_kind {
            "switched" => &["branch"],
            "turn_finished" => &["files"],
            "turn_stopped"
            | "turn_failed"
            | "approval_waiting"
            | "approval_decline_soon"
            | "budget80"
            | "busy" => &[],
            "pushed" => &["commits"],
            "merged" => &["branch", "into"],
            "refused" => &["action"],
            "repo_off" => &["uncommitted"],
            _ => return None,
        };

        const BASE_KEYS: &[&str] = &["seq", "chat_key", "repo", "app_thread", "kind"];
        for key in obj.keys() {
            let k = key.as_str();
            if !BASE_KEYS.contains(&k) && !allowed_extra.contains(&k) {
                return None;
            }
        }

        let seq = obj.get("seq")?.as_u64()?;
        let chat_key = obj.get("chat_key")?.as_str()?;
        if !valid_chat_key(chat_key) {
            return None;
        }
        let repo = RepoName::parse(obj.get("repo")?.as_str()?)?;
        let app_thread = match obj.get("app_thread") {
            None | Some(Value::Null) => None,
            Some(Value::String(s)) => Some(AppThreadId::parse(s)?),
            Some(_) => return None,
        };

        let kind = match raw_kind {
            "switched" => {
                let branch = BranchName::parse(obj.get("branch")?.as_str()?)?;
                RepoNoticeKind::Switched { branch }
            }
            "turn_finished" => {
                let files = parse_u32(obj.get("files")?)?;
                RepoNoticeKind::TurnFinished { files }
            }
            "turn_stopped" => RepoNoticeKind::TurnStopped,
            "turn_failed" => RepoNoticeKind::TurnFailed,
            "approval_waiting" => RepoNoticeKind::ApprovalWaiting,
            "approval_decline_soon" => RepoNoticeKind::ApprovalDeclineSoon,
            "pushed" => {
                let commits = parse_u32(obj.get("commits")?)?;
                RepoNoticeKind::Pushed { commits }
            }
            "merged" => {
                let branch = BranchName::parse(obj.get("branch")?.as_str()?)?;
                let into = BranchName::parse(obj.get("into")?.as_str()?)?;
                RepoNoticeKind::Merged { branch, into }
            }
            "refused" => {
                let action = RefusedAction::parse(obj.get("action")?.as_str()?)?;
                RepoNoticeKind::Refused { action }
            }
            "budget80" => RepoNoticeKind::Budget80,
            "busy" => RepoNoticeKind::Busy,
            "repo_off" => {
                let uncommitted = parse_u32(obj.get("uncommitted")?)?;
                RepoNoticeKind::RepoOff { uncommitted }
            }
            _ => return None,
        };

        Some(Self {
            seq,
            chat_key: chat_key.to_string(),
            repo,
            app_thread,
            kind,
        })
    }
}

fn parse_u32(v: &Value) -> Option<u32> {
    let n = v.as_u64()?;
    u32::try_from(n).ok()
}

fn valid_chat_key(s: &str) -> bool {
    let Some((bridge, chat)) = s.split_once(':') else {
        return false;
    };
    if !matches!(bridge, "telegram" | "discord" | "signal" | "matrix" | "web") {
        return false;
    }
    if chat.is_empty() || chat.len() > 64 || chat.contains("..") {
        return false;
    }
    chat.bytes()
        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b':' | b'.'))
}

fn sanitize_kind_for_log(raw: &str) -> &'static str {
    match raw {
        "switched" => "switched",
        "turn_finished" => "turn_finished",
        "turn_stopped" => "turn_stopped",
        "turn_failed" => "turn_failed",
        "approval_waiting" => "approval_waiting",
        "approval_decline_soon" => "approval_decline_soon",
        "pushed" => "pushed",
        "merged" => "merged",
        "refused" => "refused",
        "budget80" => "budget80",
        "busy" => "busy",
        "repo_off" => "repo_off",
        _ => "unknown",
    }
}

fn log_dropped(kind: &'static str) {
    eprintln!("ling-chat: dropped invalid repo notice (kind={kind})");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_notice(kind: RepoNoticeKind) -> RepoNotice {
        RepoNotice {
            seq: 7,
            chat_key: "telegram:12345".to_string(),
            repo: RepoName::parse("org/mightling").unwrap(),
            app_thread: Some(AppThreadId::parse("0195f0a0-1234").unwrap()),
            kind,
        }
    }

    #[test]
    fn templates_match_spec_word_for_word() {
        let switched = sample_notice(RepoNoticeKind::Switched {
            branch: BranchName::parse("phone/3f2a9c1e").unwrap(),
        });
        assert_eq!(
            switched.text(),
            "Working in org/mightling on phone/3f2a9c1e."
        );

        let finished = sample_notice(RepoNoticeKind::TurnFinished { files: 3 });
        assert_eq!(
            finished.text(),
            "Turn finished in org/mightling: 3 files changed."
        );

        let stopped = sample_notice(RepoNoticeKind::TurnStopped);
        assert_eq!(stopped.text(), "Turn stopped in org/mightling.");

        let failed = sample_notice(RepoNoticeKind::TurnFailed);
        assert_eq!(
            failed.text(),
            "Turn failed in org/mightling; open the app."
        );

        let waiting = sample_notice(RepoNoticeKind::ApprovalWaiting);
        assert_eq!(waiting.text(), "Approval waiting in org/mightling.");

        let decline_soon = sample_notice(RepoNoticeKind::ApprovalDeclineSoon);
        assert_eq!(decline_soon.text(), "It will be declined in 10 minutes.");

        let pushed = sample_notice(RepoNoticeKind::Pushed { commits: 2 });
        assert_eq!(pushed.text(), "Pushed 2 commits of org/mightling.");

        let merged = sample_notice(RepoNoticeKind::Merged {
            branch: BranchName::parse("phone/3f2a9c1e").unwrap(),
            into: BranchName::parse("main").unwrap(),
        });
        assert_eq!(
            merged.text(),
            "Merged phone/3f2a9c1e into main in org/mightling."
        );

        for (action, word) in [
            (RefusedAction::Commit, "Commit"),
            (RefusedAction::Push, "Push"),
            (RefusedAction::Update, "Update"),
            (RefusedAction::Merge, "Merge"),
            (RefusedAction::Pr, "Pr"),
            (RefusedAction::Branch, "Branch"),
        ] {
            let refused = sample_notice(RepoNoticeKind::Refused { action });
            assert_eq!(
                refused.text(),
                format!("{word} refused in org/mightling; open the app.")
            );
        }

        let budget = sample_notice(RepoNoticeKind::Budget80);
        assert_eq!(
            budget.text(),
            "org/mightling: 80% of today's model budget used."
        );

        let busy = sample_notice(RepoNoticeKind::Busy);
        assert_eq!(
            busy.text(),
            "The model on the node is busy with a benchmark run, so this question was not answered. Ask again when the run has finished."
        );

        let off = sample_notice(RepoNoticeKind::RepoOff { uncommitted: 4 });
        assert_eq!(
            off.text(),
            "Back to Ask; 4 files uncommitted in org/mightling."
        );
    }

    #[test]
    fn parsers_refuse_space_newline_dotdot_html_and_long_names() {
        let long_101 = "a".repeat(101);
        let long_repo = format!("org/{long_101}");
        let long_owner = format!("{long_101}/repo");

        for bad in [
            "",
            "org",
            "org/repo/extra",
            "org/re po",
            "org/re\npo",
            "org/..",
            "../repo",
            "org/re..po",
            "org/<script>",
            "<b>/repo",
            &long_repo,
            &long_owner,
        ] {
            assert!(
                RepoName::parse(bad).is_none(),
                "RepoName should refuse {bad:?}"
            );
        }
        assert!(RepoName::parse(&format!("org/{}", "a".repeat(100))).is_some());

        for bad in [
            "",
            "main branch",
            "main\nbranch",
            "../main",
            "phone/../main",
            "phone/a..b",
            "<img src=x>",
            "-flag",
            "/leading",
            "trailing/",
            "phone//branch",
            ".",
            &long_101,
        ] {
            assert!(
                BranchName::parse(bad).is_none(),
                "BranchName should refuse {bad:?}"
            );
        }
        assert!(BranchName::parse(&"b".repeat(100)).is_some());

        for bad in [
            "",
            "thr 1",
            "thr\n1",
            "../thr",
            "thr..1",
            "<script>",
            "org/repo",
            &long_101,
        ] {
            assert!(
                AppThreadId::parse(bad).is_none(),
                "AppThreadId should refuse {bad:?}"
            );
        }
        assert!(AppThreadId::parse("0195f0a0-1234").is_some());
        assert!(AppThreadId::parse("3f2a9c1e").is_some());
    }

    #[test]
    fn parse_roundtrips_and_drops_extra_or_unknown_fields() {
        let notice = sample_notice(RepoNoticeKind::TurnFinished { files: 5 });
        let serialized = notice.to_json();
        assert_eq!(RepoNotice::parse(&serialized), Some(notice));

        // Extra field on a valid kind is dropped.
        let mut with_extra = serialized.clone();
        with_extra
            .as_object_mut()
            .unwrap()
            .insert("extra".to_string(), json!("secret"));
        assert!(RepoNotice::parse(&with_extra).is_none());

        // Field belonging to another kind is dropped.
        let mut wrong_field = serialized.clone();
        wrong_field
            .as_object_mut()
            .unwrap()
            .insert("branch".to_string(), json!("main"));
        assert!(RepoNotice::parse(&wrong_field).is_none());

        // Unknown kind is dropped.
        let unknown_kind = json!({
            "seq": 1,
            "chat_key": "telegram:12345",
            "repo": "org/mightling",
            "app_thread": "0195f0a0-1234",
            "kind": "custom_text"
        });
        assert!(RepoNotice::parse(&unknown_kind).is_none());

        // Invalid field value drops the whole notice.
        let bad_repo = json!({
            "seq": 1,
            "chat_key": "telegram:12345",
            "repo": "org/bad repo",
            "app_thread": "0195f0a0-1234",
            "kind": "turn_stopped"
        });
        assert!(RepoNotice::parse(&bad_repo).is_none());
    }
}
