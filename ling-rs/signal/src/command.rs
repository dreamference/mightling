//! The owner's commands (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §5.2). A message that is exactly
//! a command is one; anything else is a question. None of them reaches the node's administration.

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Input {
    New,
    Stop,
    Status,
    Threads,
    Use(usize),
    Help,
    /// `/night` and what follows it: Night Shift from the phone (§19.7).
    Night(String),
    /// Only while an approval is pending; otherwise "yes" is a question like any other.
    Yes,
    No,
    /// A number, only while a choice is pending (a repository for `/night add`).
    Choice(usize),
    Question(String),
}

pub const HELP: &str = "Write a question and Mightling answers it in this conversation's thread.\n\n\
/new — start a new thread (the last one stays in your history)\n\
/stop — stop the answer being written\n\
/status — what is running\n\
/threads — your recent threads\n\
/use N — continue thread N\n\
/night add <task> — queue a task for Night Shift tonight, with this thread's last exchange; /night status — the queue\n\
/help — this list\n\n\
When Mightling asks to run something, reply YES or NO.";

/// Reads one message. `approval_pending` makes YES and NO answers instead of questions.
pub fn parse(text: &str, approval_pending: bool) -> Input {
    let trimmed = text.trim();
    if approval_pending {
        match trimmed.trim_end_matches(['.', '!']).to_ascii_lowercase().as_str() {
            "yes" | "y" => return Input::Yes,
            "no" | "n" => return Input::No,
            number => {
                if let Ok(n) = number.parse::<usize>()
                    && (1..=9).contains(&n)
                {
                    return Input::Choice(n);
                }
            }
        }
    }
    let lower = trimmed.to_ascii_lowercase();
    match lower.as_str() {
        "/new" => return Input::New,
        "/stop" => return Input::Stop,
        "/status" => return Input::Status,
        "/threads" => return Input::Threads,
        "/help" => return Input::Help,
        _ => {}
    }
    if lower == "/night" || lower.starts_with("/night ") {
        return Input::Night(trimmed["/night".len()..].trim().to_string());
    }
    if let Some(number) = lower.strip_prefix("/use ")
        && let Ok(n) = number.trim().parse::<usize>()
        && n >= 1
    {
        return Input::Use(n);
    }
    Input::Question(text.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commands_are_whole_messages() {
        assert_eq!(parse("/new", false), Input::New);
        assert_eq!(parse("  /STOP ", false), Input::Stop);
        assert_eq!(parse("/status", false), Input::Status);
        assert_eq!(parse("/threads", false), Input::Threads);
        assert_eq!(parse("/use 3", false), Input::Use(3));
        assert_eq!(parse("/help", false), Input::Help);
        assert_eq!(parse("/use 0", false), Input::Question("/use 0".to_string()));
        assert_eq!(parse("/new thread please", false), Input::Question("/new thread please".to_string()));
        assert_eq!(parse("what does /stop do?", false), Input::Question("what does /stop do?".to_string()));
    }

    #[test]
    fn yes_and_no_answer_only_a_pending_approval() {
        assert_eq!(parse("YES", true), Input::Yes);
        assert_eq!(parse("no.", true), Input::No);
        assert_eq!(parse("y", true), Input::Yes);
        assert_eq!(parse("yes", false), Input::Question("yes".to_string()));
        assert_eq!(parse("yes, but why?", true), Input::Question("yes, but why?".to_string()));
        assert_eq!(parse("/stop", true), Input::Stop);
        assert_eq!(parse("2", true), Input::Choice(2));
        assert_eq!(parse("2", false), Input::Question("2".to_string()));
        assert_eq!(parse("/night add Fix it", false), Input::Night("add Fix it".to_string()));
        assert_eq!(parse("/nightly", false), Input::Question("/nightly".to_string()));
    }
}
