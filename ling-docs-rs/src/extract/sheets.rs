//! Spreadsheets (spec §7.1, Phase 3): `.xlsx`, `.xlsm`, `.xlsb`, `.xls` and `.ods`, through
//! `calamine` (pure Rust; the cells' stored values, a formula's last result).
//!
//! Each worksheet is read like a CSV file (csv.rs, Phase 0's form): its first non-empty row is
//! the header, every other row is written `column: value, column: value`, 20 rows a unit, the
//! header row being line 1 of the sheet's first unit. Every row of the sheet's used range is a line,
//! empty ones included, so the locator is the sheet's own row numbers as the spreadsheet shows
//! them (`sheet Budget, rows 2-21`); `read` takes `sheet Budget` or `sheet Budget, rows 5-9`.
//! Dates are written `2025-03-14` (with the time when it has one), whole numbers without a
//! decimal point. Each sheet is chunked apart from the others. At most [`super::csv::MAX_ROWS`] rows are read in a file.

use std::io::Cursor;

use anyhow::{anyhow, Result};
use calamine::{Data, Reader, SheetType};

use super::csv::{MAX_ROWS, ROWS_PER_UNIT};
use super::Unit;

/// A cell as text.
fn cell(data: &Data) -> String {
    match data {
        Data::Empty => String::new(),
        Data::String(s) => s.split_whitespace().collect::<Vec<_>>().join(" "),
        Data::Float(f) if f.fract() == 0.0 && f.abs() < 1e15 => format!("{}", *f as i64),
        Data::Float(f) => format!("{f}"),
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => if *b { "TRUE" } else { "FALSE" }.to_string(),
        Data::DateTime(d) if d.is_duration() => {
            let seconds = (d.as_f64() * 86_400.0).round() as i64;
            format!(
                "{}:{:02}:{:02}",
                seconds / 3600,
                seconds / 60 % 60,
                seconds % 60
            )
        }
        Data::DateTime(d) => {
            let (y, m, day, h, min, s, _) = d.to_ymd_hms_milli();
            if (h, min, s) == (0, 0, 0) {
                format!("{y:04}-{m:02}-{day:02}")
            } else {
                format!("{y:04}-{m:02}-{day:02} {h:02}:{min:02}")
            }
        }
        Data::DateTimeIso(s) | Data::DurationIso(s) => s.clone(),
        Data::Error(e) => format!("#{e:?}"),
    }
}

/// A workbook's worksheets as located units.
pub fn sheets(bytes: &[u8]) -> Result<Vec<Unit>> {
    let mut workbook = calamine::open_workbook_auto_from_rs(Cursor::new(bytes))
        .map_err(|e| anyhow!("not a readable spreadsheet: {e}"))?;
    let names: Vec<String> = workbook
        .sheets_metadata()
        .iter()
        .filter(|s| s.typ == SheetType::WorkSheet)
        .map(|s| s.name.clone())
        .collect();
    let mut units = Vec::new();
    let mut line = 1u32;
    let mut read = 0usize;
    for (number, name) in names.into_iter().enumerate() {
        let group = number as u32 + 1;
        let Ok(range) = workbook.worksheet_range(&name) else {
            continue;
        };
        let Some((first_row, _)) = range.start() else {
            continue;
        };
        let rows: Vec<Vec<String>> = range.rows().map(|r| r.iter().map(cell).collect()).collect();
        let Some(header_at) = rows.iter().position(|r| r.iter().any(|c| !c.is_empty())) else {
            continue;
        };
        let header = &rows[header_at];
        let columns = header
            .iter()
            .filter(|h| !h.is_empty())
            .cloned()
            .collect::<Vec<_>>()
            .join(", ");
        let heading = if columns.is_empty() {
            name.clone()
        } else {
            format!("{name} > {columns}")
        };
        // Sheet rows from the header on, as the spreadsheet numbers them.
        let start = first_row + header_at as u32 + 1;
        let mut lines = vec![columns.clone()];
        let mut unit_start = start;
        let mut cut = false;
        for (i, row) in rows[header_at + 1..].iter().enumerate() {
            if read >= MAX_ROWS {
                cut = true;
                break;
            }
            read += 1;
            let fields: Vec<String> = row
                .iter()
                .enumerate()
                .filter(|(_, value)| !value.is_empty())
                .map(|(c, value)| match header.get(c).filter(|h| !h.is_empty()) {
                    Some(column) => format!("{column}: {value}"),
                    None => value.clone(),
                })
                .collect();
            lines.push(fields.join(", "));
            let sheet_row = start + 1 + i as u32;
            if lines.len() - usize::from(unit_start == start) == ROWS_PER_UNIT {
                units.push(unit(
                    &name, group, unit_start, sheet_row, &heading, &lines, &mut line,
                ));
                lines.clear();
                unit_start = sheet_row + 1;
            }
        }
        if cut {
            lines.push(format!(
                "[the rows after the first {MAX_ROWS} of this file are not indexed]"
            ));
        }
        if lines.iter().any(|l| !l.is_empty()) {
            let last = unit_start + lines.len() as u32 - 1;
            units.push(unit(
                &name, group, unit_start, last, &heading, &lines, &mut line,
            ));
        }
        if cut {
            break;
        }
    }
    Ok(units)
}

fn unit(
    sheet: &str,
    group: u32,
    first: u32,
    last: u32,
    heading: &str,
    lines: &[String],
    line: &mut u32,
) -> Unit {
    let count = lines.len() as u32;
    let unit = Unit {
        loc: format!("sheet {sheet}, rows {first}-{last}"),
        page: None,
        line_first: Some(*line),
        line_last: Some(*line + count - 1),
        heading: heading.to_string(),
        // A sheet is chunked apart from the others.
        group: Some(group),
        text: lines.join("\n"),
    };
    *line += count;
    unit
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use crate::extract::office::tests::zip_of;

    /// A small XLSX: two sheets, shared strings, a number, a date (style 1 is a date format) and
    /// an empty row in the middle.
    pub fn sample_xlsx() -> Vec<u8> {
        let workbook = r#"<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Budget" sheetId="1" r:id="rId1"/><sheet name="Staff" sheetId="2" r:id="rId2"/></sheets></workbook>"#;
        let rels = r#"<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"#;
        let strings = r#"<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Item</t></si><si><t>Cost</t></si><si><t>Due</t></si><si><t>Servo batch</t></si><si><t>Name</t></si><si><t>Ingrid Berg</t></si></sst>"#;
        let styles = r#"<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/></cellXfs></styleSheet>"#;
        let sheet1 = r#"<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
            <row r="2"><c r="B2" t="s"><v>0</v></c><c r="C2" t="s"><v>1</v></c><c r="D2" t="s"><v>2</v></c></row>
            <row r="3"><c r="B3" t="s"><v>3</v></c><c r="C3"><v>1490</v></c><c r="D3" s="1"><v>45730</v></c></row>
            <row r="5"><c r="B5" t="inlineStr"><is><t>Gripper</t></is></c><c r="C5"><v>12.5</v></c></row>
            </sheetData></worksheet>"#;
        let sheet2 = r#"<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>4</v></c></row><row r="2"><c r="A2" t="s"><v>5</v></c></row></sheetData></worksheet>"#;
        let types = r#"<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>"#;
        let root_rels = r#"<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"#;
        zip_of(&[
            ("[Content_Types].xml", types),
            ("_rels/.rels", root_rels),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", rels),
            ("xl/sharedStrings.xml", strings),
            ("xl/styles.xml", styles),
            ("xl/worksheets/sheet1.xml", sheet1),
            ("xl/worksheets/sheet2.xml", sheet2),
        ])
    }

    #[test]
    fn each_sheet_is_read_like_a_table_in_its_own_rows() {
        let units = sheets(&sample_xlsx()).unwrap();
        let got: Vec<(&str, &str)> = units
            .iter()
            .map(|u| (u.loc.as_str(), u.heading.as_str()))
            .collect();
        assert_eq!(
            got,
            [
                ("sheet Budget, rows 2-5", "Budget > Item, Cost, Due"),
                ("sheet Staff, rows 1-2", "Staff > Name")
            ],
            "{units:#?}"
        );
        let lines: Vec<&str> = units[0].text.split('\n').collect();
        assert_eq!(
            lines,
            [
                "Item, Cost, Due",
                "Item: Servo batch, Cost: 1490, Due: 2025-03-14",
                "",
                "Item: Gripper, Cost: 12.5"
            ]
        );
        assert_eq!(
            (units[1].line_first, units[1].line_last),
            (Some(5), Some(6)),
            "one line numbering across sheets"
        );
        assert!(sheets(b"PK\x03\x04 not a workbook").is_err());
    }
}
