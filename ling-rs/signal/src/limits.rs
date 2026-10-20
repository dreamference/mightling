//! An answer's bounds on a phone (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §19.3): its time, its tool
//! calls, and each command's time. Pure, driven by a clock in milliseconds.
//!
//! On 2026-10-20 one answer took sixteen minutes and 92 tool calls, fifteen of those minutes a
//! model call that decoded a repetition loop without streaming a word. Nothing inside the turn
//! could hear anything meanwhile, so the bridge bounds it from outside:
//!
//! 1. a limit reached: the agent is told, through `turn/steer`, to answer now with what it has;
//! 2. [`GRACE_MS`] later, still running: the turn is interrupted, and a final turn asks for the
//!    answer from what was found, with no tools;
//! 3. the final turn past [`FINAL_BUDGET_MS`]: interrupted, and the user gets what was written.
//!
//! `ling chat` keeps the same rules (`ling-rs/chat/src/limits.rs`); `tests/test_chat_limits.py` holds the two
//! files to the same text from [`Verdict`] on.

use std::collections::HashMap;

use crate::agent::ChatSettings;

/// After the agent is told to answer now, how long it has before the turn is interrupted.
pub const GRACE_MS: u64 = 30_000;
/// The final turn's time.
pub const FINAL_BUDGET_MS: u64 = 90_000;

/// The bounds, from the user's `[chat]` settings.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Limits {
    pub answer_ms: Option<u64>,
    pub tool_calls: Option<u32>,
    pub command_ms: Option<u64>,
    /// [`GRACE_MS`] and [`FINAL_BUDGET_MS`], shorter in tests.
    pub grace_ms: u64,
    pub final_ms: u64,
}

impl Limits {
    pub fn from_settings(settings: &ChatSettings) -> Limits {
        Limits {
            answer_ms: settings.answer_ms,
            tool_calls: settings.tool_calls,
            command_ms: settings.command_ms,
            grace_ms: GRACE_MS,
            final_ms: FINAL_BUDGET_MS,
        }
    }
}

impl Default for Limits {
    fn default() -> Limits {
        Limits::from_settings(&ChatSettings::default())
    }
}

/// What the bridge does about a turn.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// Tell the agent to answer now (`turn/steer` with this text).
    Steer(String),
    /// Interrupt the turn; `reason` says why, for the final turn and the user.
    Interrupt(String),
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Stage {
    Running,
    Steered { at_ms: u64, reason: String },
    Interrupted,
}

/// One turn, watched.
#[derive(Clone, Debug)]
pub struct Watch {
    started_ms: u64,
    tools: u32,
    /// Commands still running: item id → (started, the command).
    commands: HashMap<String, (u64, String)>,
    stage: Stage,
    /// The final turn after an interrupted one.
    pub final_turn: bool,
}

/// "3 min", "45 s".
fn span(ms: u64) -> String {
    let s = ms / 1000;
    if s >= 60 && s % 60 == 0 { format!("{} min", s / 60) } else if s >= 60 { format!("{} min {} s", s / 60, s % 60) } else { format!("{s} s") }
}

fn short(command: &str) -> String {
    let one_line = command.split_whitespace().collect::<Vec<_>>().join(" ");
    if one_line.chars().count() <= 60 {
        return one_line;
    }
    let mut cut: String = one_line.chars().take(60).collect();
    cut.push('…');
    cut
}

/// Whether a thread item of this type is a tool call: anything the agent does but say or think.
pub fn is_tool(kind: &str) -> bool {
    !matches!(kind, "userMessage" | "agentMessage" | "reasoning" | "plan" | "contextCompaction" | "enteredReviewMode" | "exitedReviewMode" | "hookPrompt")
}

/// The steer that tells the agent to answer now.
pub fn steer_text(reason: &str) -> String {
    format!(
        "[Mightling: {reason}. Stop now: run no more tools, and answer with what you have found, in the user's language. If something was blocked or could not be found, say so in one line.]"
    )
}

/// The final turn's text after an interrupted one.
pub fn final_text(reason: &str) -> String {
    format!(
        "[Mightling: the last answer was stopped because {reason}. Answer now from what you found so far, in the user's language, without running any tool. If you could not find it, say so briefly and say what was blocked.]"
    )
}

/// The user's line when a turn is interrupted for its time.
pub fn interrupted_notice(reason: &str) -> String {
    format!("This is taking too long ({reason}), so I asked for an answer with what was found so far.")
}

/// The user's line when the final turn ran out too.
pub const GAVE_UP: &str = "No answer came in time, so I stopped it. Ask again, maybe more narrowly.";

impl Watch {
    pub fn new(now_ms: u64) -> Watch {
        Watch { started_ms: now_ms, tools: 0, commands: HashMap::new(), stage: Stage::Running, final_turn: false }
    }

    pub fn final_turn(now_ms: u64) -> Watch {
        Watch { final_turn: true, ..Watch::new(now_ms) }
    }

    /// A tool call started; `command` is a command's text, for its own time limit.
    pub fn tool_started(&mut self, item: &str, command: Option<&str>, now_ms: u64) {
        self.tools += 1;
        if let Some(command) = command {
            self.commands.insert(item.to_string(), (now_ms, command.to_string()));
        }
    }

    pub fn tool_completed(&mut self, item: &str) {
        self.commands.remove(item);
    }

    /// Whether the turn was interrupted by these limits (then its end starts the final turn, or,
    /// for the final turn, ends the answer).
    pub fn interrupted(&self) -> bool {
        self.stage == Stage::Interrupted
    }

    /// Which limit, if any, the turn is past now.
    fn over(&self, now_ms: u64, limits: &Limits) -> Option<String> {
        let elapsed = now_ms.saturating_sub(self.started_ms);
        if let Some(budget) = limits.answer_ms
            && elapsed >= budget
        {
            return Some(format!("this answer has taken {}", span(budget)));
        }
        if let Some(cap) = limits.tool_calls
            && self.tools >= cap
        {
            return Some(format!("this answer has used {cap} tool calls"));
        }
        if let Some(limit) = limits.command_ms {
            let mut late: Vec<&(u64, String)> = self.commands.values().filter(|(started, _)| now_ms.saturating_sub(*started) >= limit).collect();
            late.sort();
            if let Some((_, command)) = late.first() {
                return Some(format!("the command `{}` has run over {}", short(command), span(limit)));
            }
        }
        None
    }

    /// The clock: what to do now, at most once per stage.
    pub fn check(&mut self, now_ms: u64, limits: &Limits) -> Option<Verdict> {
        if self.final_turn {
            if self.stage != Stage::Interrupted && now_ms.saturating_sub(self.started_ms) >= limits.final_ms {
                self.stage = Stage::Interrupted;
                return Some(Verdict::Interrupt(format!("the final answer took over {}", span(limits.final_ms))));
            }
            return None;
        }
        match self.stage.clone() {
            Stage::Running => {
                let reason = self.over(now_ms, limits)?;
                self.stage = Stage::Steered { at_ms: now_ms, reason: reason.clone() };
                Some(Verdict::Steer(steer_text(&reason)))
            }
            Stage::Steered { at_ms, reason } if now_ms >= at_ms + limits.grace_ms => {
                self.stage = Stage::Interrupted;
                Some(Verdict::Interrupt(reason))
            }
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIMITS: Limits = Limits { answer_ms: Some(180_000), tool_calls: Some(25), command_ms: Some(60_000), grace_ms: GRACE_MS, final_ms: FINAL_BUDGET_MS };

    #[test]
    fn time_up_steers_then_interrupts_once() {
        let mut watch = Watch::new(0);
        assert_eq!(watch.check(179_999, &LIMITS), None);
        assert_eq!(watch.check(180_000, &LIMITS), Some(Verdict::Steer(steer_text("this answer has taken 3 min"))));
        assert_eq!(watch.check(200_000, &LIMITS), None, "steered once");
        assert_eq!(watch.check(210_000, &LIMITS), Some(Verdict::Interrupt("this answer has taken 3 min".to_string())));
        assert!(watch.interrupted());
        assert_eq!(watch.check(400_000, &LIMITS), None);
    }

    #[test]
    fn the_tool_cap_and_a_long_command_count_too() {
        let mut watch = Watch::new(0);
        for n in 0..24 {
            watch.tool_started(&format!("i{n}"), None, 0);
        }
        assert_eq!(watch.check(1_000, &LIMITS), None);
        watch.tool_started("i24", None, 1_000);
        assert_eq!(watch.check(1_000, &LIMITS), Some(Verdict::Steer(steer_text("this answer has used 25 tool calls"))));

        let mut watch = Watch::new(0);
        watch.tool_started("c1", Some("python3 parse.py   page.html"), 5_000);
        assert_eq!(watch.check(64_999, &LIMITS), None);
        let Some(Verdict::Steer(text)) = watch.check(65_000, &LIMITS) else { panic!() };
        assert!(text.contains("the command `python3 parse.py page.html` has run over 1 min"), "{text}");
        // A command that ended in time counts for nothing.
        let mut watch = Watch::new(0);
        watch.tool_started("c1", Some("ls"), 0);
        watch.tool_completed("c1");
        assert_eq!(watch.check(100_000, &LIMITS), None);
        // No limits: never.
        let none = Limits { answer_ms: None, tool_calls: None, command_ms: None, ..LIMITS };
        assert_eq!(Watch::new(0).check(10_000_000, &none), None);
    }

    #[test]
    fn the_final_turn_has_its_own_short_budget_and_no_steer() {
        let mut watch = Watch::final_turn(1_000);
        for n in 0..30 {
            watch.tool_started(&format!("i{n}"), None, 1_000);
        }
        assert_eq!(watch.check(1_000 + FINAL_BUDGET_MS - 1, &LIMITS), None);
        assert!(matches!(watch.check(1_000 + FINAL_BUDGET_MS, &LIMITS), Some(Verdict::Interrupt(_))));
        assert!(watch.interrupted());
        assert!(final_text("this answer has taken 3 min").contains("without running any tool"));
    }
}
