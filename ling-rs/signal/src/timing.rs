//! How long a question took, stage by stage, for the journal (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md
//! §19.8). Only the message's id (the owner's timestamp, which is its id in Signal) and durations
//! are written, never its text, its sender or the answer.
//!
//! Two lines per question: one when the bridge receives it, from Signal's own timestamps (the
//! phone's, the server's on receipt and on delivery to this device), and one when the answer is
//! sent, with each stage measured from the moment the bridge received it. The phone's clock and the
//! node's are not the same clock, so a stage that crosses them can be off by their difference.

/// The question in hand, from its receipt to the answer's send.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Timing {
    /// The owner's message timestamp (its id in Signal); 0 for a question that waited in the queue.
    pub id: u64,
    pub received_ms: u64,
    /// When the answer's message was first on the phone (§19.8): the status line in linked mode,
    /// the answer's first words in dedicated mode.
    pub status_ms: Option<u64>,
    /// When the turn was handed to the agent (`turn/start` sent).
    pub handed_ms: Option<u64>,
    /// The agent's first output: a tool call started or the first words of a message.
    pub first_output_ms: Option<u64>,
    /// When the turn ended.
    pub answered_ms: Option<u64>,
}

impl Timing {
    pub fn new(id: u64, received_ms: u64) -> Timing {
        Timing { id, received_ms, status_ms: None, handed_ms: None, first_output_ms: None, answered_ms: None }
    }
}

/// "1.23s", signed: a stage across two clocks can come out negative.
pub fn seconds(ms: i64) -> String {
    format!("{}{}.{:02}s", if ms < 0 { "-" } else { "" }, ms.unsigned_abs() / 1000, ms.unsigned_abs() % 1000 / 10)
}

fn between(from: u64, to: u64) -> String {
    seconds(to as i64 - from as i64)
}

/// The line for a message the bridge just accepted: how long Signal took to bring it here.
pub fn received_line(id: u64, server_received: Option<u64>, server_delivered: Option<u64>, now: u64) -> String {
    let mut line = format!("ling-signal: message {id} received");
    match (server_received, server_delivered) {
        (Some(received), Some(delivered)) => line.push_str(&format!(
            ": phone to Signal's server {}, held there {}, to the bridge {}; {} since the phone's timestamp",
            between(id, received),
            between(received, delivered),
            between(delivered, now),
            between(id, now)
        )),
        _ => line.push_str(&format!(": {} since the phone's timestamp (Signal gave no server times)", between(id, now))),
    }
    line
}

/// The line for an answer signal-cli has acknowledged sending (`acked`), stage by stage.
pub fn answered_line(timing: &Timing, acked: u64) -> String {
    let id = if timing.id == 0 { "from the queue".to_string() } else { timing.id.to_string() };
    let mut stages = Vec::new();
    let mut stage = |name: &str, at: Option<u64>| {
        if let Some(at) = at {
            stages.push(format!("{name} {}", between(timing.received_ms, at)));
        }
    };
    stage("first shown", timing.status_ms);
    stage("turn started", timing.handed_ms);
    stage("first output", timing.first_output_ms);
    stage("turn ended", timing.answered_ms);
    stage("answer acked", Some(acked));
    let mut line = format!("ling-signal: message {id} answered: {}", stages.join(", "));
    if let Some(ended) = timing.answered_ms {
        line.push_str(&format!(" (sending took {})", between(ended, acked)));
    }
    if timing.id != 0 {
        line.push_str(&format!("; end to end {} from the phone's timestamp", between(timing.id, acked)));
    }
    line
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seconds_read_plainly_and_keep_their_sign() {
        assert_eq!(seconds(1234), "1.23s");
        assert_eq!(seconds(40_100), "40.10s");
        assert_eq!(seconds(-250), "-0.25s");
        assert_eq!(seconds(0), "0.00s");
    }

    #[test]
    fn the_receipt_line_splits_signals_part_from_the_bridges() {
        let line = received_line(1_000_000, Some(1_000_400), Some(1_000_500), 1_000_650);
        assert_eq!(line, "ling-signal: message 1000000 received: phone to Signal's server 0.40s, held there 0.10s, to the bridge 0.15s; 0.65s since the phone's timestamp");
        assert!(received_line(5, None, None, 1_005).ends_with("1.00s since the phone's timestamp (Signal gave no server times)"));
    }

    #[test]
    fn the_answer_line_names_each_stage_from_the_receipt() {
        let timing = Timing { id: 1_000_000, received_ms: 1_000_650, status_ms: Some(1_001_150), handed_ms: Some(1_001_900), first_output_ms: Some(1_005_000), answered_ms: Some(1_040_000) };
        assert_eq!(
            answered_line(&timing, 1_040_700),
            "ling-signal: message 1000000 answered: first shown 0.50s, turn started 1.25s, first output 4.35s, turn ended 39.35s, answer acked 40.05s (sending took 0.70s); end to end 40.70s from the phone's timestamp"
        );
        let queued = Timing { id: 0, ..Timing::new(0, 10) };
        assert_eq!(answered_line(&queued, 2_010), "ling-signal: message from the queue answered: answer acked 2.00s");
    }

    #[test]
    fn no_line_carries_anything_but_ids_and_durations() {
        let line = answered_line(&Timing::new(7, 7), 9);
        assert!(line.chars().all(|c| c.is_ascii()), "{line}");
    }
}
