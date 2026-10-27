//! Per-turn bounds and daily per-chat model token budgets for repository threads
//! (`specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md` §7.1, §7.2, §7.3, §8.2).
//!
//! Pure and clock-driven like `ling-rs/chat/src/limits.rs`: the caller supplies `now_ms` (for turn
//! watches) and `now` in Unix seconds (for the daily token budget). Unlike Ask threads, a
//! `chat-work` turn that hits its bound is steered once (`"Stop here and summarise what changed so
//! far."`) and interrupted 60 s later if still running, with no final toolless turn.

use std::collections::HashMap;

use crate::chat_repos::ChatReposSettings;

/// How long after `Steer` before `Interrupt` when the turn is still running (60 s; §7.1).
pub const GRACE_MS: u64 = 60_000;
/// Default daily token budget per chat across `chat-work` threads (3,000,000; §7.2, §14).
pub const DEFAULT_TOKENS_PER_DAY: u64 = 3_000_000;
/// Maximum `add` value accepted by `POST /api/repos/budget` (100,000,000).
pub const MAX_BUDGET_RAISE: u64 = 100_000_000;
/// Steering prompt sent when a `chat-work` turn hits a bound or exhausts the chat's budget (§7.1, §7.2).
pub const STEER_TEXT: &str = "Stop here and summarise what changed so far.";

/// The model gate's refusal while a SWE-bench run holds the model
/// (`dreamference/vllm_server/model_gate_service.py`, `message`; copied from
/// `ling-rs/signal/src/bridge.rs`): "The model is running a benchmark (…)."
pub const BENCHMARK_REFUSAL_MARK: &str = "The model is running a benchmark (";

/// Per-turn bounds for a `chat-work` thread (`None` means `0` / no limit).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RepoLimits {
    pub answer_ms: Option<u64>,
    pub tool_calls: Option<u32>,
    pub command_ms: Option<u64>,
    pub grace_ms: u64,
}

impl Default for RepoLimits {
    fn default() -> Self {
        Self::from_settings(&ChatReposSettings::default())
    }
}

impl RepoLimits {
    /// Builds [`RepoLimits`] from `[chat_repos]` settings (`0` = no limit; grace is 60 s).
    pub fn from_settings(settings: &ChatReposSettings) -> Self {
        Self {
            answer_ms: (settings.work_answer_seconds > 0)
                .then(|| settings.work_answer_seconds.saturating_mul(1_000)),
            tool_calls: (settings.work_tool_calls > 0)
                .then(|| settings.work_tool_calls.min(u32::MAX as u64) as u32),
            command_ms: (settings.work_command_seconds > 0)
                .then(|| settings.work_command_seconds.saturating_mul(1_000)),
            grace_ms: GRACE_MS,
        }
    }
}

/// What `ling web` should send to the app-server when a `chat-work` turn crosses a bound.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RepoVerdict {
    /// Send `turn/steer` with this text; the 60 s grace window starts now.
    Steer(String),
    /// Send `turn/interrupt`; the watch is finished (no final turn).
    Interrupt(String),
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Stage {
    Running,
    Steered { at_ms: u64, reason: String },
    Interrupted,
}

/// Per-turn bound tracker for one `chat-work` turn.
#[derive(Clone, Debug)]
pub struct RepoWatch {
    started_ms: u64,
    last_now_ms: u64,
    tools: u32,
    commands: HashMap<String, u64>,
    limits: RepoLimits,
    stage: Stage,
}

impl RepoWatch {
    /// Starts watching a turn at `started_ms` with `limits`.
    pub fn new(started_ms: u64, limits: RepoLimits) -> Self {
        Self {
            started_ms,
            last_now_ms: started_ms,
            tools: 0,
            commands: HashMap::new(),
            limits,
            stage: Stage::Running,
        }
    }

    /// Notes that a tool item started in this turn.
    pub fn on_tool_started(&mut self) {
        self.tools = self.tools.saturating_add(1);
    }

    /// Notes that a `commandExecution` item started at the watch's current clock reading.
    pub fn on_command_started(&mut self, item_id: &str) {
        self.commands.insert(item_id.to_string(), self.last_now_ms);
    }

    /// Notes that a `commandExecution` item started at `now_ms`.
    pub fn on_command_started_at(&mut self, item_id: &str, now_ms: u64) {
        self.last_now_ms = self.last_now_ms.max(now_ms);
        self.commands.insert(item_id.to_string(), now_ms);
    }

    /// Notes that a `commandExecution` item finished.
    pub fn on_command_finished(&mut self, item_id: &str) {
        self.commands.remove(item_id);
    }

    /// Evaluates the turn's bounds at `now_ms`. Each verdict (`Steer`, then `Interrupt`) is
    /// returned at most once; after `Interrupt`, `check` always returns `None`.
    pub fn check(&mut self, now_ms: u64) -> Option<RepoVerdict> {
        self.last_now_ms = self.last_now_ms.max(now_ms);
        match &self.stage {
            Stage::Running => {
                let reason = self.hit(now_ms)?;
                self.stage = Stage::Steered { at_ms: now_ms, reason };
                Some(RepoVerdict::Steer(STEER_TEXT.to_string()))
            }
            Stage::Steered { at_ms, reason } => {
                if now_ms.saturating_sub(*at_ms) >= self.limits.grace_ms {
                    let reason = reason.clone();
                    self.stage = Stage::Interrupted;
                    Some(RepoVerdict::Interrupt(reason))
                } else {
                    None
                }
            }
            Stage::Interrupted => None,
        }
    }

    /// Steers a running turn immediately when its chat's daily budget reaches 100%, starting the
    /// same 60 s grace countdown as a per-turn bound. Returns `None` if already steered or
    /// interrupted.
    pub fn steer_for_budget(&mut self, now_ms: u64) -> Option<RepoVerdict> {
        self.last_now_ms = self.last_now_ms.max(now_ms);
        if matches!(self.stage, Stage::Running) {
            self.stage = Stage::Steered {
                at_ms: now_ms,
                reason: "today's model budget for this chat is used up".to_string(),
            };
            Some(RepoVerdict::Steer(STEER_TEXT.to_string()))
        } else {
            None
        }
    }

    fn hit(&self, now_ms: u64) -> Option<String> {
        if let Some(max_ms) = self.limits.command_ms
            && self
                .commands
                .values()
                .any(|started| now_ms.saturating_sub(*started) >= max_ms)
        {
            return Some(format!("command ran over {}s", max_ms / 1_000));
        }
        if let Some(max_calls) = self.limits.tool_calls
            && self.tools >= max_calls
        {
            return Some(format!("reached {max_calls} tool calls"));
        }
        if let Some(max_ms) = self.limits.answer_ms
            && now_ms.saturating_sub(self.started_ms) >= max_ms
        {
            return Some(format!("ran over {}s", max_ms / 1_000));
        }
        None
    }
}

/// Whether an `item/started` item type counts as a tool call (same classification as Ask bounds).
pub fn is_tool(kind: &str) -> bool {
    !matches!(
        kind,
        "userMessage"
            | "agentMessage"
            | "reasoning"
            | "plan"
            | "contextCompaction"
            | "enteredReviewMode"
            | "exitedReviewMode"
            | "hookPrompt"
    )
}

#[derive(Clone, Debug, Default)]
struct ChatBudgetState {
    resets_at: u64,
    used: u64,
    raised: u64,
    warned_80: bool,
}

/// Daily per-chat token budget across all `chat-work` threads of a chat (`<bridge>:<chat>`).
#[derive(Clone, Debug)]
pub struct Budget {
    tokens_per_day: u64,
    utc_offset_seconds: Option<i64>,
    chats: HashMap<String, ChatBudgetState>,
    thread_totals: HashMap<String, u64>,
}

impl Default for Budget {
    fn default() -> Self {
        Self::new(DEFAULT_TOKENS_PER_DAY)
    }
}

impl Budget {
    /// Creates a budget with `tokens_per_day` using the node's local midnight (`libc::localtime_r`).
    pub fn new(tokens_per_day: u64) -> Self {
        Self {
            tokens_per_day,
            utc_offset_seconds: None,
            chats: HashMap::new(),
            thread_totals: HashMap::new(),
        }
    }

    /// Creates a budget with `tokens_per_day` and a fixed UTC offset in seconds for midnight calculation.
    pub fn with_utc_offset(tokens_per_day: u64, utc_offset_seconds: i64) -> Self {
        Self {
            tokens_per_day,
            utc_offset_seconds: Some(utc_offset_seconds),
            chats: HashMap::new(),
            thread_totals: HashMap::new(),
        }
    }

    /// Updates the base daily token limit (`[chat_repos] tokens_per_day`).
    pub fn set_tokens_per_day(&mut self, tokens_per_day: u64) {
        self.tokens_per_day = tokens_per_day;
    }

    /// Sets or clears a fixed UTC offset in seconds for local midnight calculation.
    pub fn set_utc_offset(&mut self, utc_offset_seconds: Option<i64>) {
        self.utc_offset_seconds = utc_offset_seconds;
    }

    /// Unix timestamp (seconds) of the next local midnight after `now`.
    pub fn resets_at(&self, now: u64) -> u64 {
        match self.utc_offset_seconds {
            Some(offset) => next_midnight_with_offset(now, offset),
            None => next_local_midnight(now),
        }
    }

    /// Formats the next reset boundary at `now` as `HH:MM` in node local time.
    pub fn reset_time_hm(&self, now: u64) -> String {
        let reset = self.resets_at(now);
        format_time_hm(reset, self.utc_offset_seconds)
    }

    /// Refusal message when a new turn is started on an exhausted chat.
    pub fn refusal_message(&self, now: u64) -> String {
        format!(
            "today's model budget for this chat is used up; it resets at {}",
            self.reset_time_hm(now)
        )
    }

    /// Records a thread token-usage notification's cumulative total for `(chat, thread)` at `now`,
    /// adding the increase since the last cumulative total seen for `thread`. The first usage
    /// notification seen for `thread` after start-up sets its baseline and charges nothing.
    pub fn add(&mut self, chat: &str, thread: &str, cumulative_total: u64, now: u64) -> u64 {
        let next_reset = self.resets_at(now);
        let Some(prev) = self.thread_totals.insert(thread.to_string(), cumulative_total) else {
            let entry = self.entry_mut(chat, now, next_reset);
            return entry.used;
        };
        let delta = if cumulative_total >= prev {
            cumulative_total - prev
        } else {
            cumulative_total
        };

        let entry = self.entry_mut(chat, now, next_reset);
        entry.used = entry.used.saturating_add(delta);
        entry.used
    }

    /// Tokens used today by `chat` at `now`.
    pub fn used(&self, chat: &str, now: u64) -> u64 {
        self.chats
            .get(chat)
            .filter(|e| now < e.resets_at)
            .map_or(0, |e| e.used)
    }

    /// Effective token limit today for `chat` at `now` (base `tokens_per_day` plus any raise made today).
    pub fn limit(&self, chat: &str, now: u64) -> u64 {
        let raised = self
            .chats
            .get(chat)
            .filter(|e| now < e.resets_at)
            .map_or(0, |e| e.raised);
        self.tokens_per_day.saturating_add(raised)
    }

    /// Raises `chat`'s budget by `extra` tokens until local midnight. Returns the new effective limit.
    pub fn raise(&mut self, chat: &str, extra: u64, now: u64) -> u64 {
        let next_reset = self.resets_at(now);
        let base = self.tokens_per_day;
        let entry = self.entry_mut(chat, now, next_reset);
        entry.raised = entry.raised.saturating_add(extra);
        base.saturating_add(entry.raised)
    }

    /// Returns `true` at most once per chat per day when `chat`'s usage has reached 80% of its limit.
    pub fn crossed_80(&mut self, chat: &str) -> bool {
        let base = self.tokens_per_day;
        let Some(entry) = self.chats.get_mut(chat) else {
            return false;
        };
        if entry.warned_80 {
            return false;
        }
        let limit = base.saturating_add(entry.raised);
        if limit == 0 {
            return false;
        }
        if entry.used.saturating_mul(5) >= limit.saturating_mul(4) {
            entry.warned_80 = true;
            true
        } else {
            false
        }
    }

    /// Whether `chat` has reached 100% of its effective daily limit at `now`.
    pub fn exhausted(&self, chat: &str, now: u64) -> bool {
        let limit = self.limit(chat, now);
        limit > 0 && self.used(chat, now) >= limit
    }

    fn entry_mut(&mut self, chat: &str, now: u64, next_reset: u64) -> &mut ChatBudgetState {
        let entry = self.chats.entry(chat.to_string()).or_default();
        if entry.resets_at == 0 || now >= entry.resets_at {
            entry.resets_at = next_reset;
            entry.used = 0;
            entry.raised = 0;
            entry.warned_80 = false;
        }
        entry
    }
}

/// Computes the next midnight Unix timestamp (seconds) for a fixed UTC offset in seconds.
pub fn next_midnight_with_offset(now_secs: u64, utc_offset_seconds: i64) -> u64 {
    let local = (now_secs as i64).saturating_add(utc_offset_seconds);
    let next_local_midnight = (local.div_euclid(86_400) + 1) * 86_400;
    let next_utc = next_local_midnight.saturating_sub(utc_offset_seconds);
    next_utc.max(0) as u64
}

/// Computes the next local midnight Unix timestamp (seconds) on the node using `libc::localtime_r`.
pub fn next_local_midnight(now_secs: u64) -> u64 {
    #[cfg(unix)]
    {
        let t = now_secs as libc::time_t;
        let mut tm: libc::tm = unsafe { std::mem::zeroed() };
        if unsafe { libc::localtime_r(&t, &mut tm) }.is_null() {
            return ((now_secs / 86_400) + 1) * 86_400;
        }
        tm.tm_hour = 0;
        tm.tm_min = 0;
        tm.tm_sec = 0;
        tm.tm_mday += 1;
        tm.tm_isdst = -1;
        let next = unsafe { libc::mktime(&mut tm) };
        if next > t {
            next as u64
        } else {
            now_secs + (86_400 - (now_secs % 86_400))
        }
    }
    #[cfg(not(unix))]
    {
        ((now_secs / 86_400) + 1) * 86_400
    }
}

/// Formats a Unix timestamp (seconds) as `HH:MM` in node local time (or `utc_offset_seconds`).
pub fn format_time_hm(ts_secs: u64, utc_offset_seconds: Option<i64>) -> String {
    if let Some(offset) = utc_offset_seconds {
        let local_day_secs = (ts_secs as i64).saturating_add(offset).rem_euclid(86_400);
        let hour = local_day_secs / 3_600;
        let min = (local_day_secs % 3_600) / 60;
        return format!("{hour:02}:{min:02}");
    }
    #[cfg(unix)]
    {
        let t = ts_secs as libc::time_t;
        let mut tm: libc::tm = unsafe { std::mem::zeroed() };
        if unsafe { libc::localtime_r(&t, &mut tm) }.is_null() {
            return "00:00".to_string();
        }
        format!("{:02}:{:02}", tm.tm_hour, tm.tm_min)
    }
    #[cfg(not(unix))]
    {
        "00:00".to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn limits_from_settings_and_zero_means_unlimited() {
        let defaults = RepoLimits::default();
        assert_eq!(defaults.answer_ms, Some(1_800_000));
        assert_eq!(defaults.tool_calls, Some(200));
        assert_eq!(defaults.command_ms, Some(600_000));
        assert_eq!(defaults.grace_ms, 60_000);

        let unlimited = RepoLimits::from_settings(&ChatReposSettings {
            work_answer_seconds: 0,
            work_tool_calls: 0,
            work_command_seconds: 0,
            ..ChatReposSettings::default()
        });
        assert_eq!(unlimited.answer_ms, None);
        assert_eq!(unlimited.tool_calls, None);
        assert_eq!(unlimited.command_ms, None);
        assert_eq!(unlimited.grace_ms, 60_000);
    }

    #[test]
    fn watch_tool_calls_steers_then_interrupts_after_grace_with_no_final_turn() {
        let limits = RepoLimits {
            tool_calls: Some(3),
            ..RepoLimits::default()
        };
        let mut watch = RepoWatch::new(1_000, limits);
        watch.on_tool_started();
        watch.on_tool_started();
        assert_eq!(watch.check(2_000), None);

        watch.on_tool_started();
        assert_eq!(
            watch.check(3_000),
            Some(RepoVerdict::Steer("Stop here and summarise what changed so far.".to_string()))
        );
        // Repeated checks during the 60 s grace window issue no further Steer.
        watch.on_tool_started();
        assert_eq!(watch.check(3_500), None);
        assert_eq!(watch.check(62_999), None);

        // At 60 s after Steer, Interrupt is issued once and the watch is done forever.
        assert_eq!(
            watch.check(63_000),
            Some(RepoVerdict::Interrupt("reached 3 tool calls".to_string()))
        );
        assert_eq!(watch.check(64_000), None);
        assert_eq!(watch.check(200_000), None);
    }

    #[test]
    fn watch_command_bound_and_answer_bound_steer_then_interrupt() {
        let limits = RepoLimits::default();
        let mut cmd_watch = RepoWatch::new(0, limits.clone());
        assert_eq!(cmd_watch.check(10_000), None);
        cmd_watch.on_command_started("cmd-fast");
        assert_eq!(cmd_watch.check(200_000), None);
        cmd_watch.on_command_finished("cmd-fast");
        // Finished command never triggers the 600 s bound.
        assert_eq!(cmd_watch.check(700_000), None);

        cmd_watch.on_command_started("cmd-hung");
        assert_eq!(cmd_watch.check(1_299_999), None);
        assert_eq!(
            cmd_watch.check(1_300_000),
            Some(RepoVerdict::Steer("Stop here and summarise what changed so far.".to_string()))
        );
        assert_eq!(cmd_watch.check(1_359_999), None);
        assert_eq!(
            cmd_watch.check(1_360_000),
            Some(RepoVerdict::Interrupt("command ran over 600s".to_string()))
        );
        assert_eq!(cmd_watch.check(1_500_000), None);

        // Answer-time bound (1800 s), then 60 s grace.
        let mut ans_watch = RepoWatch::new(5_000, limits);
        assert_eq!(ans_watch.check(1_804_999), None);
        assert_eq!(
            ans_watch.check(1_805_000),
            Some(RepoVerdict::Steer("Stop here and summarise what changed so far.".to_string()))
        );
        assert_eq!(ans_watch.check(1_864_999), None);
        assert_eq!(
            ans_watch.check(1_865_000),
            Some(RepoVerdict::Interrupt("ran over 1800s".to_string()))
        );
        assert_eq!(ans_watch.check(2_000_000), None);
    }

    #[test]
    fn budget_tracks_cumulative_deltas_warns_80_once_exhausts_raises_and_resets_at_midnight() {
        // Fixed UTC offset +02:00 (7200 s): day 1 ends at 2026-10-20 22:00:00 UTC.
        let day1_morning = 1_792_483_200u64; // 2026-10-20 08:00:00 UTC (10:00 local)
        let mut budget = Budget::with_utc_offset(DEFAULT_TOKENS_PER_DAY, 7_200);
        let midnight = budget.resets_at(day1_morning);
        assert_eq!(budget.reset_time_hm(day1_morning), "00:00");
        assert!(midnight > day1_morning && midnight - day1_morning == 14 * 3_600);

        // First usage notification seen for each thread sets its baseline and charges nothing.
        assert_eq!(budget.add("signal:c1", "thread-a", 0, day1_morning), 0);
        assert_eq!(budget.add("signal:c1", "thread-b", 0, day1_morning), 0);

        // Cumulative totals on thread-a and thread-b of chat "signal:c1".
        assert_eq!(budget.add("signal:c1", "thread-a", 1_000_000, day1_morning), 1_000_000);
        assert!(!budget.crossed_80("signal:c1"));
        assert!(!budget.exhausted("signal:c1", day1_morning));

        // Increase on thread-a from 1,000,000 -> 1,500,000 (+500,000) and thread-b 900,000 -> total 2,400,000 (80%).
        assert_eq!(budget.add("signal:c1", "thread-a", 1_500_000, day1_morning + 10), 1_500_000);
        assert_eq!(budget.add("signal:c1", "thread-b", 900_000, day1_morning + 20), 2_400_000);
        assert!(budget.crossed_80("signal:c1"));
        // Exactly once per chat per day.
        assert!(!budget.crossed_80("signal:c1"));
        assert_eq!(budget.add("signal:c1", "thread-a", 1_600_000, day1_morning + 30), 2_500_000);
        assert!(!budget.crossed_80("signal:c1"));

        // Reach 100% (3,000,000).
        assert_eq!(budget.add("signal:c1", "thread-b", 1_400_000, day1_morning + 40), 3_000_000);
        assert!(budget.exhausted("signal:c1", day1_morning + 40));
        assert_eq!(
            budget.refusal_message(day1_morning + 40),
            "today's model budget for this chat is used up; it resets at 00:00"
        );

        // Raise by 1,000,000 lifts refusal until midnight.
        assert_eq!(budget.raise("signal:c1", 1_000_000, day1_morning + 50), 4_000_000);
        assert_eq!(budget.limit("signal:c1", day1_morning + 50), 4_000_000);
        assert!(!budget.exhausted("signal:c1", day1_morning + 50));
        // Still does not re-fire 80% on the same day.
        assert!(!budget.crossed_80("signal:c1"));

        // At midnight, usage and raise both reset, while thread cumulative baseline is preserved.
        assert_eq!(budget.used("signal:c1", midnight), 0);
        assert_eq!(budget.limit("signal:c1", midnight), DEFAULT_TOKENS_PER_DAY);
        assert!(!budget.exhausted("signal:c1", midnight));

        // On day 2, thread-a advances from 1,600,000 -> 4,000,000 (+2,400,000 = 80% of day 2).
        assert_eq!(budget.add("signal:c1", "thread-a", 4_000_000, midnight + 60), 2_400_000);
        assert!(budget.crossed_80("signal:c1"));
        assert!(!budget.crossed_80("signal:c1"));
    }

    #[test]
    fn first_usage_notification_after_restart_sets_baseline_and_charges_nothing() {
        let now = 1_792_483_200u64;
        let mut budget = Budget::with_utc_offset(DEFAULT_TOKENS_PER_DAY, 0);
        // After a restart, a resumed thread reports its lifetime total (e.g. 9,500,000 tokens).
        assert_eq!(budget.add("signal:c1", "resumed-thread", 9_500_000, now), 0);
        assert_eq!(budget.used("signal:c1", now), 0);
        assert!(!budget.crossed_80("signal:c1"));
        assert!(!budget.exhausted("signal:c1", now));

        // Subsequent notification charges only the delta since the baseline (+200,000).
        assert_eq!(budget.add("signal:c1", "resumed-thread", 9_700_000, now + 5), 200_000);
        assert_eq!(budget.used("signal:c1", now + 5), 200_000);
    }
}
