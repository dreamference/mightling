//! Usage limits (check C3): the kind and the reset time of a `rate_limit` API error.
//!
//! Claude Code writes the limit as text: "You've hit your session limit · resets 1am (Europe/Lisbon)",
//! "... weekly limit · resets Oct 14, 11am (...)", and for a monthly spend limit "... · your weekly
//! limit resets 11am (...)", where the time is the weekly limit's, not the spend limit's. The time
//! is converted to UTC only when its zone is this machine's own zone; there is no time-zone database
//! in the build, so another zone keeps the time as written and says the UTC time is unknown.

use chrono::Datelike;
use chrono::NaiveDate;
use chrono::NaiveDateTime;
use chrono::NaiveTime;
use chrono::TimeZone;

/// Converts a wall-clock time in one named zone to epoch seconds.
pub struct Zone {
    /// The zone's name as the messages write it (`Europe/Lisbon`).
    pub name: String,
    /// Local wall-clock time to epoch seconds, `None` for a time that does not exist there.
    pub to_utc: Box<dyn Fn(NaiveDateTime) -> Option<i64>>,
    /// Epoch seconds to local wall-clock time.
    pub to_local: Box<dyn Fn(i64) -> NaiveDateTime>,
}

impl Zone {
    /// This machine's zone: `TZ` when it names one, else `/etc/timezone`, else the target of
    /// `/etc/localtime`; converted with the C library's rules through chrono's `Local`.
    pub fn system() -> Zone {
        Zone {
            name: system_zone_name().unwrap_or_default(),
            to_utc: Box::new(|naive| chrono::Local.from_local_datetime(&naive).earliest().map(|t| t.timestamp())),
            to_local: Box::new(|ts| chrono::Local.timestamp_opt(ts, 0).single().map(|t| t.naive_local()).unwrap_or_default()),
        }
    }

    /// A fixed offset under a name, for tests.
    pub fn fixed(name: &str, offset_seconds: i32) -> Zone {
        let offset = chrono::FixedOffset::east_opt(offset_seconds).expect("a valid offset");
        Zone {
            name: name.to_string(),
            to_utc: Box::new(move |naive| offset.from_local_datetime(&naive).earliest().map(|t| t.timestamp())),
            to_local: Box::new(move |ts| offset.timestamp_opt(ts, 0).single().map(|t| t.naive_local()).unwrap_or_default()),
        }
    }
}

fn system_zone_name() -> Option<String> {
    if let Ok(tz) = std::env::var("TZ") {
        let tz = tz.trim_start_matches(':');
        if tz.contains('/') {
            return Some(tz.to_string());
        }
    }
    // `/etc/localtime` is what the C library (and chrono's `Local`) converts with; `/etc/timezone`
    // can be stale after a change of zone, so it is read only when the link is missing.
    if let Ok(target) = std::fs::read_link("/etc/localtime") {
        let target = target.to_string_lossy().into_owned();
        if let Some((_, name)) = target.split_once("zoneinfo/") {
            return Some(name.to_string());
        }
    }
    let text = std::fs::read_to_string("/etc/timezone").ok()?;
    let text = text.trim();
    (!text.is_empty()).then(|| text.to_string())
}

/// One usage limit, as parsed from its message.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Limit {
    /// `session`, `weekly`, `monthly spend` or `unknown`.
    pub kind: String,
    /// The reset time in epoch seconds, when it is known in UTC.
    pub reset: Option<i64>,
    /// The reset as written (`1am (Europe/Lisbon)`), empty when the message gives none.
    pub reset_text: String,
    /// For a spend limit, the limit whose reset the message names (`weekly`).
    pub reset_of: Option<String>,
    /// The UTC day the limit was seen, which tells apart two limits whose reset is known only as
    /// written ("1am" today and "1am" next week).
    #[serde(default)]
    pub seen_day: String,
}

impl Limit {
    /// The key alerts are deduplicated by: one alert per limit and reset.
    pub fn key(&self) -> String {
        match self.reset {
            Some(reset) => format!("{}@{reset}", self.kind),
            None => format!("{}@{}@{}", self.kind, self.reset_text, self.seen_day),
        }
    }
}

const MONTHS: [&str; 12] = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/// `11am`, `1:30pm`, `11 am`.
fn parse_clock(text: &str) -> Option<NaiveTime> {
    let text = text.trim().to_ascii_lowercase().replace(' ', "");
    let (digits, pm) = if let Some(rest) = text.strip_suffix("am") {
        (rest.to_string(), false)
    } else if let Some(rest) = text.strip_suffix("pm") {
        (rest.to_string(), true)
    } else {
        return None;
    };
    let (hour, minute) = match digits.split_once(':') {
        Some((h, m)) => (h.parse::<u32>().ok()?, m.parse::<u32>().ok()?),
        None => (digits.parse::<u32>().ok()?, 0),
    };
    if !(1..=12).contains(&hour) {
        return None;
    }
    let hour = (hour % 12) + if pm { 12 } else { 0 };
    NaiveTime::from_hms_opt(hour, minute, 0)
}

/// Parses a usage-limit message seen at `seen` (epoch seconds).
pub fn parse(text: &str, seen: i64, zone: &Zone) -> Limit {
    let seen_day = chrono::Utc.timestamp_opt(seen, 0).single().map(|t| t.format("%Y-%m-%d").to_string()).unwrap_or_default();
    let lower = text.to_ascii_lowercase();
    let kind = if lower.contains("spend limit") {
        "monthly spend"
    } else if lower.contains("weekly limit") && !lower.contains("your weekly limit resets") {
        "weekly"
    } else if lower.contains("session limit") {
        "session"
    } else if lower.contains("weekly limit") {
        "weekly"
    } else {
        "unknown"
    };
    let reset_of = (kind == "monthly spend" && lower.contains("weekly limit resets")).then(|| "weekly".to_string());
    let Some(after) = lower.find("resets ").map(|at| &text[at + "resets ".len()..]) else {
        return Limit { kind: kind.to_string(), reset: None, reset_text: String::new(), reset_of, seen_day };
    };
    let reset_text = after.lines().next().unwrap_or_default().trim().to_string();
    let (when, zone_name) = match reset_text.split_once('(') {
        Some((when, rest)) => (when.trim(), rest.trim_end_matches(')').trim()),
        None => (reset_text.as_str(), ""),
    };
    // An optional date ("Oct 14, 11am"), then the clock time.
    let (date, clock) = match when.split_once(',') {
        Some((date, clock)) => (Some(date.trim()), clock.trim()),
        None => (None, when),
    };
    let reset = (zone_name == zone.name && !zone.name.is_empty())
        .then(|| {
            let clock = parse_clock(clock)?;
            let local_seen = (zone.to_local)(seen);
            let naive = match date {
                Some(date) => {
                    let (month, day) = date.split_once(' ')?;
                    let month = MONTHS.iter().position(|m| month.to_ascii_lowercase().starts_with(m))? as u32 + 1;
                    let day = day.trim().parse::<u32>().ok()?;
                    let mut candidate = NaiveDate::from_ymd_opt(local_seen.year(), month, day)?.and_time(clock);
                    if candidate < local_seen - chrono::Duration::days(180) {
                        candidate = NaiveDate::from_ymd_opt(local_seen.year() + 1, month, day)?.and_time(clock);
                    }
                    candidate
                }
                None => {
                    let today = local_seen.date().and_time(clock);
                    if today <= local_seen { today + chrono::Duration::days(1) } else { today }
                }
            };
            (zone.to_utc)(naive)
        })
        .flatten();
    Limit { kind: kind.to_string(), reset, reset_text, reset_of, seen_day }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ts(text: &str) -> i64 {
        chrono::DateTime::parse_from_rfc3339(text).unwrap().timestamp()
    }

    #[test]
    fn a_session_limit_resets_at_the_next_occurrence_of_its_time() {
        let zone = Zone::fixed("Europe/Lisbon", 0);
        let limit = parse("You've hit your session limit · resets 1am (Europe/Lisbon)", ts("2026-10-27T00:17:27Z"), &zone);
        assert_eq!(limit.kind, "session");
        assert_eq!(limit.reset, Some(ts("2026-10-27T01:00:00Z")));
        // Seen after 1am: tomorrow's.
        let limit = parse("You've hit your session limit · resets 1am (Europe/Lisbon)", ts("2026-10-27T03:00:00Z"), &zone);
        assert_eq!(limit.reset, Some(ts("2026-10-28T01:00:00Z")));
    }

    #[test]
    fn a_dated_weekly_limit_and_a_summer_offset() {
        let zone = Zone::fixed("Europe/Lisbon", 3600);
        let limit = parse("You've hit your weekly limit · resets Oct 14, 11am (Europe/Lisbon)", ts("2026-10-12T20:00:00Z"), &zone);
        assert_eq!(limit.kind, "weekly");
        assert_eq!(limit.reset, Some(ts("2026-10-14T10:00:00Z")));
        let limit = parse("You've hit your weekly limit · resets 2:30pm (Europe/Lisbon)", ts("2026-10-12T08:00:00Z"), &zone);
        assert_eq!(limit.reset, Some(ts("2026-10-12T13:30:00Z")));
    }

    #[test]
    fn a_spend_limit_names_the_weekly_reset_it_quotes() {
        let zone = Zone::fixed("Europe/Lisbon", 3600);
        let text = "You've hit your monthly spend limit · raise it at https://example.invalid/usage · your weekly limit resets 11am (Europe/Lisbon)";
        let limit = parse(text, ts("2026-10-20T12:25:12Z"), &zone);
        assert_eq!((limit.kind.as_str(), limit.reset_of.as_deref()), ("monthly spend", Some("weekly")));
        assert_eq!(limit.reset, Some(ts("2026-10-21T10:00:00Z")));
    }

    #[test]
    fn another_zone_keeps_the_time_as_written() {
        let zone = Zone::fixed("Europe/Lisbon", 0);
        let limit = parse("You've hit your session limit · resets 5pm (America/New_York)", ts("2026-10-27T00:00:00Z"), &zone);
        assert_eq!((limit.reset, limit.reset_text.as_str()), (None, "5pm (America/New_York)"));
        let none = parse("You've hit your monthly spend limit", 0, &zone);
        assert_eq!((none.reset, none.reset_text.as_str(), none.key().as_str()), (None, "", "monthly spend@@1970-01-01"));
    }
}
