//! The catalogue budget, with Codex's own arithmetic (spec §7; `codex-rs/ext/skills/src/render.rs`
//! in the pinned source).
//!
//! Every session the model is shown one line per skill, `- <name>: <description> (file: <path>)`.
//! Their total is capped at 2% of the context window, counted as bytes / 4. Over the cap Codex
//! truncates descriptions, and when even the bare lines do not fit it removes **every**
//! description, after which no skill can be chosen by what it says it does. The launcher counts
//! the same way so it can stop short of that.

/// `SKILL_METADATA_CONTEXT_WINDOW_PERCENT` in the pinned source.
pub const CONTEXT_WINDOW_PERCENT: u64 = 2;
/// `APPROX_BYTES_PER_TOKEN`.
pub const BYTES_PER_TOKEN: usize = 4;
/// `MAX_CATALOG_SKILL_DESCRIPTION_CHARS`: longer descriptions are cut to this, ending in `...`.
pub const MAX_DESCRIPTION_CHARS: usize = 1_024;
/// The share of the budget at which the launcher says so at start.
pub const WARN_PERCENT: usize = 80;

/// The budget in tokens for a model advertising `context_window` tokens.
pub fn limit(context_window: u64) -> usize {
    usize::try_from(context_window.saturating_mul(CONTEXT_WINDOW_PERCENT) / 100).unwrap_or(usize::MAX).max(1)
}

/// What one skill's catalogue line costs, in tokens.
pub fn line_cost(name: &str, description: &str, skill_md: &str) -> usize {
    let description = truncated(description);
    let line = if description.is_empty() {
        format!("- {name}: (file: {skill_md})\n")
    } else {
        format!("- {name}: {description} (file: {skill_md})\n")
    };
    line.len().saturating_add(BYTES_PER_TOKEN - 1) / BYTES_PER_TOKEN
}

fn truncated(description: &str) -> String {
    if description.chars().count() <= MAX_DESCRIPTION_CHARS {
        return description.to_string();
    }
    let kept: String = description.chars().take(MAX_DESCRIPTION_CHARS - 3).collect();
    kept + "..."
}

/// `5242` as `5,242`.
pub fn grouped(number: usize) -> String {
    let digits = number.to_string();
    let mut out = String::new();
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index) % 3 == 0 {
            out.push(',');
        }
        out.push(digit);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_budget_is_two_percent_of_the_window() {
        // The figures of spec §2.1: 262,144 tokens advertised, 5,242 for the catalogue.
        assert_eq!(limit(262_144), 5_242);
        assert_eq!(limit(32_768), 655);
        assert_eq!(limit(10), 1);
    }

    #[test]
    fn a_line_costs_its_bytes_over_four_rounded_up() {
        // "- pdf: Read PDFs (file: /s/pdf/SKILL.md)\n" is 41 bytes.
        assert_eq!(line_cost("pdf", "Read PDFs", "/s/pdf/SKILL.md"), 11);
        // Bytes, not characters: a two-byte letter counts twice.
        assert_eq!(line_cost("pdf", "Read PDFß", "/s/pdf/SKILL.md"), 11);
        assert_eq!(line_cost("pdf", "Read PDFßßßß", "/s/pdf/SKILL.md"), 12);
        assert_eq!(line_cost("pdf", "", "/s/pdf/SKILL.md"), 8);
    }

    #[test]
    fn a_long_description_is_counted_as_codex_cuts_it() {
        let long = "x".repeat(5_000);
        let capped = "x".repeat(1_021) + "...";
        assert_eq!(line_cost("a", &long, "/p"), line_cost("a", &capped, "/p"));
    }

    #[test]
    fn numbers_are_grouped_in_threes() {
        assert_eq!(grouped(0), "0");
        assert_eq!(grouped(620), "620");
        assert_eq!(grouped(5_242), "5,242");
        assert_eq!(grouped(1_234_567), "1,234,567");
    }
}
