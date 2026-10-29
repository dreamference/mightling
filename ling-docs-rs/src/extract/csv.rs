//! CSV and TSV (spec §7.1, Phase 2): the header and the rows as text, 20 rows a unit.
//!
//! Phase 0's form (`eval/evallib.py`, `units_csv`), which §15 measured: each data row is written
//! as `column: value, column: value` (empty values left out), so a row found alone still says what
//! its numbers are; the header is the unit's heading (embedded, not stored) and line 1 of the
//! first unit. The locator is the row range, the header being row 1, as a spreadsheet numbers
//! them. The delimiter is the one of `, ; Tab |` the first line uses most (a `.tsv` is Tab). A
//! value's line breaks become spaces, so one row is one line. At most [`MAX_ROWS`] rows are read.

use super::Unit;

/// Data rows a unit holds (Phase 0's group).
pub const ROWS_PER_UNIT: usize = 20;
/// Rows read at most: a larger export is cut, and says so in its last unit.
pub const MAX_ROWS: usize = 100_000;

/// The delimiter the first line uses most, quotes skipped.
fn delimiter(text: &str, tab: bool) -> u8 {
    if tab {
        return b'\t';
    }
    let first = text.lines().next().unwrap_or("");
    let mut counts = [(b',', 0usize), (b';', 0), (b'\t', 0), (b'|', 0)];
    let mut quoted = false;
    for c in first.bytes() {
        if c == b'"' {
            quoted = !quoted;
        } else if !quoted {
            if let Some(entry) = counts.iter_mut().find(|(d, _)| *d == c) {
                entry.1 += 1;
            }
        }
    }
    counts
        .iter()
        .max_by_key(|(d, n)| (*n, *d == b','))
        .filter(|(_, n)| *n > 0)
        .map(|(d, _)| *d)
        .unwrap_or(b',')
}

fn clean(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// A table's text as located units. `tab` for a `.tsv`.
pub fn csv(text: &str, tab: bool) -> Vec<Unit> {
    let mut reader = ::csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .delimiter(delimiter(text, tab))
        .from_reader(text.as_bytes());
    let mut records = reader.records().map_while(|r| r.ok());
    let Some(header) = records.next() else {
        return Vec::new();
    };
    let header: Vec<String> = header.iter().map(clean).collect();
    let heading = header
        .iter()
        .filter(|h| !h.is_empty())
        .cloned()
        .collect::<Vec<_>>()
        .join(", ");
    let mut units = Vec::new();
    let mut lines = vec![heading.clone()];
    let mut first_row = 1usize;
    let mut row = 1usize;
    let mut cut = false;
    // Whether `lines` starts with the header (the first unit only).
    let mut header_in = true;
    for record in records {
        if row > MAX_ROWS {
            cut = true;
            break;
        }
        row += 1;
        let fields: Vec<String> = record
            .iter()
            .enumerate()
            .filter_map(|(i, value)| {
                let value = clean(value);
                if value.is_empty() {
                    return None;
                }
                Some(match header.get(i).filter(|h| !h.is_empty()) {
                    Some(name) => format!("{name}: {value}"),
                    None => value,
                })
            })
            .collect();
        lines.push(fields.join(", "));
        if lines.len() - usize::from(header_in) == ROWS_PER_UNIT {
            units.push(unit(first_row, row, &heading, &lines));
            lines.clear();
            first_row = row + 1;
            header_in = false;
        }
    }
    if cut {
        lines.push(format!("[the rows after row {MAX_ROWS} are not indexed]"));
    }
    if !lines.is_empty() && lines.iter().any(|l| !l.is_empty()) {
        units.push(unit(
            first_row,
            first_row + lines.len() - 1,
            &heading,
            &lines,
        ));
    }
    units
}

fn unit(first: usize, last: usize, heading: &str, lines: &[String]) -> Unit {
    Unit {
        loc: format!("rows {first}-{last}"),
        page: None,
        line_first: Some(first as u32),
        line_last: Some(last as u32),
        heading: heading.to_string(),
        group: None,
        text: lines.join("\n"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rows_are_written_with_their_column_names_twenty_a_unit() {
        let mut text = String::from("Item,Location,Note\n");
        for i in 1..=45 {
            text.push_str(&format!("part {i},\"Shelf {i}\nleft\",\n"));
        }
        let units = csv(&text, false);
        let locs: Vec<&str> = units.iter().map(|u| u.loc.as_str()).collect();
        assert_eq!(locs, ["rows 1-21", "rows 22-41", "rows 42-46"]);
        assert_eq!(units[0].heading, "Item, Location, Note");
        let lines: Vec<&str> = units[0].text.lines().collect();
        assert_eq!(lines[0], "Item, Location, Note");
        assert_eq!(lines[1], "Item: part 1, Location: Shelf 1 left");
        assert_eq!(
            units[2].text.lines().last(),
            Some("Item: part 45, Location: Shelf 45 left")
        );
        assert_eq!(
            (units[1].line_first, units[1].line_last),
            (Some(22), Some(41))
        );
    }

    #[test]
    fn the_delimiter_is_found_from_the_first_line() {
        let units = csv("Name;Amount\nAcme;1,5\n", false);
        assert_eq!(units[0].text, "Name, Amount\nName: Acme, Amount: 1,5");
        let units = csv("a\tb\nx\ty\n", true);
        assert_eq!(units[0].text.lines().nth(1), Some("a: x, b: y"));
        assert!(csv("", false).is_empty());
    }
}
