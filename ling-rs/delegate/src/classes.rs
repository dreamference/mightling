//! Task classes (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §6.1).
//!
//! Phase P1 builds the two model-free classes: `W1` (`watch`, turned on by the owner on 2026-10-27)
//! and `L1` (`lookup`, exact answers from the code index). Every model-backed class is off and
//! unbuilt here.

use serde::Serialize;

/// Default value of `DREAMFERENCE_DELEGATE_ALLOW_CLASSES` in Phase P1.
pub const DEFAULT_ALLOW_CLASSES: &str = "W1,L1";

/// Environment variable overriding enabled task classes.
pub const ALLOW_CLASSES_ENV: &str = "DREAMFERENCE_DELEGATE_ALLOW_CLASSES";

/// Lifecycle status of a delegation class.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ClassStatus {
    /// Built and on (`W1`).
    Active,
    /// Not built in P1.
    Off,
}

impl ClassStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Off => "off",
        }
    }
}

/// One row of the delegation class table (`ling delegate classes`).
#[derive(Debug, Clone, Serialize)]
pub struct ClassInfo {
    pub code: &'static str,
    pub name: &'static str,
    pub tool: &'static str,
    pub uses_model: bool,
    pub status: ClassStatus,
    pub note: &'static str,
}

/// The class table of `specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §6.1.
pub const CLASSES: &[ClassInfo] = &[
    ClassInfo {
        code: "W1",
        name: "Waiting and monitoring",
        tool: "watch",
        uses_model: false,
        status: ClassStatus::Active,
        note: "no model; the plugin observes the condition",
    },
    ClassInfo {
        code: "L1",
        name: "Lookups with quotes",
        tool: "lookup",
        uses_model: false,
        status: ClassStatus::Active,
        note: "no model; ling-code's index, quotes read back from the files",
    },
    ClassInfo {
        code: "L2",
        name: "Multi-step questions with quotes",
        tool: "ask",
        uses_model: true,
        status: ClassStatus::Off,
        note: "not built in P1",
    },
    ClassInfo {
        code: "L3",
        name: "Log and rollout summaries with quotes",
        tool: "ask",
        uses_model: true,
        status: ClassStatus::Off,
        note: "not built in P1",
    },
    ClassInfo {
        code: "T1",
        name: "Lint, typing and format fixes",
        tool: "task",
        uses_model: true,
        status: ClassStatus::Off,
        note: "not built in P1",
    },
    ClassInfo {
        code: "T2",
        name: "Test-only CI fixes",
        tool: "task",
        uses_model: true,
        status: ClassStatus::Off,
        note: "not built in P1",
    },
    ClassInfo {
        code: "T3",
        name: "Mechanical refactors with tests",
        tool: "task",
        uses_model: true,
        status: ClassStatus::Off,
        note: "not built in P1",
    },
    ClassInfo {
        code: "T4",
        name: "Docs updates checked against the code",
        tool: "task",
        uses_model: true,
        status: ClassStatus::Off,
        note: "not built in P1",
    },
    ClassInfo {
        code: "T5",
        name: "Small UI items with e2e tests",
        tool: "task",
        uses_model: true,
        status: ClassStatus::Off,
        note: "not built in P1",
    },
    ClassInfo {
        code: "N1",
        name: "Best-of-N for T1-T5",
        tool: "night",
        uses_model: true,
        status: ClassStatus::Off,
        note: "not built in P1",
    },
];

/// Returns true if class `W1` (`watch`) is allowed by the given `allow_classes` setting.
/// An empty or unset value enables both model-free classes.
pub fn is_w1_enabled(allow_classes_raw: Option<&str>) -> bool {
    is_class_enabled(allow_classes_raw, &["W1", "watch"])
}

/// Returns true if class `L1` (`lookup`) is allowed by the given `allow_classes` setting.
pub fn is_l1_enabled(allow_classes_raw: Option<&str>) -> bool {
    is_class_enabled(allow_classes_raw, &["L1", "lookup"])
}

fn is_class_enabled(allow_classes_raw: Option<&str>, names: &[&str]) -> bool {
    let raw = allow_classes_raw.unwrap_or("").trim();
    if raw.is_empty() {
        return true;
    }
    if raw.eq_ignore_ascii_case("none") || raw.eq_ignore_ascii_case("off") {
        return false;
    }
    raw.split(|c: char| c == ',' || c.is_ascii_whitespace())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .any(|token| {
            names.iter().any(|n| token.eq_ignore_ascii_case(n)) || token.eq_ignore_ascii_case("all")
        })
}

/// Formats the human-readable table for `ling delegate classes`.
pub fn format_table() -> String {
    let mut out = String::new();
    out.push_str("CLASS  TOOL    MODEL  STATUS        NOTE\n");
    for c in CLASSES {
        let model = if c.uses_model { "yes" } else { "no" };
        out.push_str(&format!(
            "{:<5}  {:<6}  {:<5}  {:<12}  {}\n",
            c.code,
            c.tool,
            model,
            c.status.as_str(),
            c.note
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_model_free_classes_are_active_in_p1() {
        let active: Vec<&str> = CLASSES
            .iter()
            .filter(|c| c.status == ClassStatus::Active)
            .map(|c| c.code)
            .collect();
        assert_eq!(active, vec!["W1", "L1"]);
        assert!(CLASSES.iter().filter(|c| c.status == ClassStatus::Active).all(|c| !c.uses_model));
        let w1 = CLASSES.iter().find(|c| c.code == "W1").unwrap();
        assert_eq!(w1.tool, "watch");
        assert!(!w1.uses_model);
    }

    #[test]
    fn allow_classes_defaults_to_w1_and_respects_none() {
        assert!(is_w1_enabled(None));
        assert!(is_w1_enabled(Some("")));
        assert!(is_w1_enabled(Some("W1")));
        assert!(is_w1_enabled(Some("L1, W1")));
        assert!(is_w1_enabled(Some("watch")));
        assert!(!is_w1_enabled(Some("none")));
        assert!(!is_w1_enabled(Some("off")));
        assert!(!is_w1_enabled(Some("L1,T2")));
        assert!(is_l1_enabled(None));
        assert!(is_l1_enabled(Some("L1,T2")));
        assert!(is_l1_enabled(Some("lookup")));
        assert!(!is_l1_enabled(Some("W1")));
        assert!(!is_l1_enabled(Some("none")));
    }
}
