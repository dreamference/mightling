//! Owner alerts (§7.5): deduplicated by episode, rate-limited, quiet at night except when urgent.
//!
//! An episode is one finding's kind and subject from the check that first sees it to the check that
//! no longer does; it is sent at most once. At most one alert per kind and subject per hour and
//! `max_alerts_per_hour` in all; past that, one message says how many more wait in the summary.
//! During quiet hours a non-urgent episode is held: it stays in the summary and is never sent.

use std::collections::BTreeMap;

use serde::Deserialize;
use serde::Serialize;

use crate::checks::Finding;

/// One open episode.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Episode {
    pub kind: String,
    pub opened: i64,
    /// Checks in a row that have seen it.
    pub seen: u32,
    /// When it was sent; `None` while unsent.
    pub sent: Option<i64>,
    /// Opened or due during quiet hours, or before the monitor's first check: never sent.
    pub held: bool,
    pub text: String,
    pub urgent: bool,
    pub alert: bool,
}

/// The alert state kept between checks.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Alerts {
    pub episodes: BTreeMap<String, Episode>,
    /// Sends in the last hour: time and episode key.
    pub sent_log: Vec<(i64, String)>,
    /// When the last "more in the summary" message went out.
    pub overflow_sent: Option<i64>,
}

/// The messages one check sends, in order.
pub type Outbox = Vec<String>;

impl Alerts {
    /// Advances every episode by one check and returns what to send. With `baseline`, everything
    /// found is marked as already known and nothing is sent (the first check on a machine).
    pub fn step(&mut self, findings: &[Finding], now: i64, quiet: bool, max_per_hour: usize, baseline: bool) -> Outbox {
        let mut present = std::collections::BTreeSet::new();
        for finding in findings {
            let key = format!("{}|{}", finding.kind, finding.subject);
            present.insert(key.clone());
            let episode = self.episodes.entry(key).or_insert_with(|| Episode {
                kind: finding.kind.to_string(),
                opened: now,
                seen: 0,
                sent: None,
                held: baseline,
                text: finding.text.clone(),
                urgent: finding.urgent,
                alert: finding.alert,
            });
            episode.seen += 1;
            episode.text = finding.text.clone();
            episode.urgent = finding.urgent;
            if finding.alert && !episode.alert {
                // It became alert-worthy (a hand-off overdue for an hour): its own episode from here.
                episode.alert = true;
                episode.seen = 1;
            }
            if episode.alert && episode.seen >= finding.consecutive && episode.sent.is_none() && quiet && !finding.urgent {
                episode.held = true;
            }
        }
        self.episodes.retain(|key, _| present.contains(key));
        self.sent_log.retain(|(at, _)| now - at < 3600);
        let mut outbox = Vec::new();
        let mut over_cap = 0;
        // Urgent first, then oldest first.
        let mut due: Vec<(String, bool, i64)> = Vec::new();
        for finding in findings {
            let key = format!("{}|{}", finding.kind, finding.subject);
            let Some(episode) = self.episodes.get(&key) else { continue };
            if !episode.alert || episode.held || episode.sent.is_some() || episode.seen < finding.consecutive {
                continue;
            }
            if !due.iter().any(|(k, _, _)| *k == key) {
                due.push((key, finding.urgent, episode.opened));
            }
        }
        due.sort_by_key(|(key, urgent, opened)| (!urgent, *opened, key.clone()));
        for (key, urgent, _) in due {
            if self.sent_log.iter().any(|(_, sent)| *sent == key) {
                continue;
            }
            // An urgent alert (a usage limit, a failed release) is never held back by the cap.
            if !urgent && self.sent_log.len() >= max_per_hour {
                over_cap += 1;
                continue;
            }
            let episode = self.episodes.get_mut(&key).expect("an open episode");
            outbox.push(episode.text.clone());
            episode.sent = Some(now);
            self.sent_log.push((now, key));
        }
        if over_cap > 0 && self.overflow_sent.is_none_or(|at| now - at >= 3600) {
            outbox.push(format!("{over_cap} more alert{} in the monitor's summary", if over_cap == 1 { "" } else { "s" }));
            self.overflow_sent = Some(now);
        }
        if baseline {
            outbox.clear();
        }
        outbox
    }

    /// Marks a message as not delivered, so its episode is tried again at the next check.
    pub fn unsend(&mut self, text: &str) {
        for (key, episode) in self.episodes.iter_mut() {
            if episode.text == text && episode.sent.is_some() {
                episode.sent = None;
                self.sent_log.retain(|(_, sent)| sent != key);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::checks::Section;

    fn finding(kind: &'static str, subject: &str, urgent: bool, consecutive: u32) -> Finding {
        Finding {
            check: "C",
            kind,
            subject: subject.to_string(),
            section: Section::Stalled,
            text: format!("{kind} {subject}"),
            alert: true,
            urgent,
            consecutive,
        }
    }

    #[test]
    fn one_alert_per_episode_until_the_condition_clears() {
        let mut alerts = Alerts::default();
        let f = [finding("stall", "a", false, 1)];
        assert_eq!(alerts.step(&f, 1000, false, 6, false), vec!["stall a"]);
        assert!(alerts.step(&f, 1300, false, 6, false).is_empty());
        assert!(alerts.step(&[], 1600, false, 6, false).is_empty());
        // A new episode within the hour waits for the rate limit.
        assert!(alerts.step(&f, 1900, false, 6, false).is_empty());
        assert_eq!(alerts.step(&f, 4700, false, 6, false), vec!["stall a"]);
    }

    #[test]
    fn a_loop_needs_two_checks_in_a_row() {
        let mut alerts = Alerts::default();
        let f = [finding("loop", "a", false, 2)];
        assert!(alerts.step(&f, 0, false, 6, false).is_empty());
        assert_eq!(alerts.step(&f, 300, false, 6, false), vec!["loop a"]);
    }

    #[test]
    fn quiet_hours_hold_all_but_urgent_alerts() {
        let mut alerts = Alerts::default();
        let f = [finding("stall", "a", false, 1), finding("usage-limit", "s", true, 1)];
        assert_eq!(alerts.step(&f, 0, true, 6, false), vec!["usage-limit s"]);
        // Held episodes are not sent when the quiet hours end.
        assert!(alerts.step(&f, 300, false, 6, false).is_empty());
    }

    #[test]
    fn past_the_hourly_cap_one_message_counts_the_rest() {
        let mut alerts = Alerts::default();
        let names: Vec<String> = (0..9).map(|i| i.to_string()).collect();
        let f: Vec<_> = names.iter().map(|n| finding("stall", n, false, 1)).collect();
        let sent = alerts.step(&f, 0, false, 6, false);
        assert_eq!(sent.len(), 7);
        assert_eq!(sent[6], "3 more alerts in the monitor's summary");
        assert!(alerts.step(&f, 300, false, 6, false).is_empty());
        let urgent = [finding("usage-limit", "s", true, 1)];
        assert_eq!(alerts.step(&urgent, 600, false, 6, false), vec!["usage-limit s"]);
    }

    #[test]
    fn the_first_check_sends_nothing_and_a_failed_send_is_retried() {
        let mut alerts = Alerts::default();
        let f = [finding("stall", "a", false, 1)];
        assert!(alerts.step(&f, 0, false, 6, true).is_empty());
        assert!(alerts.step(&f, 300, false, 6, false).is_empty());
        let g = [finding("loop", "b", false, 1)];
        assert_eq!(alerts.step(&g, 600, false, 6, false), vec!["loop b"]);
        alerts.unsend("loop b");
        assert_eq!(alerts.step(&g, 900, false, 6, false), vec!["loop b"]);
    }
}
