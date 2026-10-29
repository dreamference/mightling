//! Word and OpenDocument text (spec §7.1, Phase 2): a `.docx` or `.odt` becomes paragraphs, then
//! sections by heading ([`super::sections`]).
//!
//! Both are ZIP archives of XML, read as a stream (`zip`, `quick-xml`): the text of every
//! paragraph in document order, a table row as one paragraph (its cells joined by ` | `), the
//! paragraphs of text boxes, list items and notes as paragraphs of their own, footnotes and
//! endnotes at the end. A heading is told by its outline level, never by its style's display
//! name, which Word translates (`Überschrift 1`): DOCX gives the level in `styles.xml`
//! (`w:outlineLvl`, inherited through `w:basedOn`; built-in heading styles keep the English
//! internal name `heading N` there too), ODT on the `text:h` element. Deleted text of tracked
//! changes, comments, field codes and a table of contents are left out.
//!
//! An archive is attack surface like a PDF: each XML entry is inflated through a counter and
//! refused past [`MAX_ENTRY_BYTES`] (a deflate bomb ends as `failed`, not at the scope's memory
//! cap), and only stored and deflated entries are read at all.

use std::collections::HashMap;
use std::io::{BufReader, Cursor, Read};

use anyhow::{anyhow, bail, Context, Result};
use quick_xml::events::{BytesStart, Event};
use quick_xml::Reader;

use super::{sections, Para, Unit};

/// The most one XML entry may inflate to. A long novel's `document.xml` is a few tens of MB.
pub const MAX_ENTRY_BYTES: u64 = 256 << 20;

/// A reader that fails once more than `left` bytes have come through it.
pub(super) struct Capped<R> {
    inner: R,
    left: u64,
    name: String,
}

impl<R: Read> Read for Capped<R> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let n = self.inner.read(buf)?;
        if n as u64 > self.left {
            return Err(std::io::Error::other(format!(
                "{} inflates past {} MB (a ZIP bomb?)",
                self.name,
                MAX_ENTRY_BYTES >> 20
            )));
        }
        self.left -= n as u64;
        Ok(n)
    }
}

pub(super) type Archive<'a> = zip::ZipArchive<Cursor<&'a [u8]>>;

pub(super) fn open(bytes: &[u8]) -> Result<Archive<'_>> {
    zip::ZipArchive::new(Cursor::new(bytes)).map_err(|e| anyhow!("not a readable ZIP archive: {e}"))
}

/// Streams one entry's XML events to `each`; a missing entry is `Ok(false)`.
pub(super) fn read_xml(
    archive: &mut Archive,
    name: &str,
    each: &mut dyn FnMut(Event) -> Result<()>,
) -> Result<bool> {
    let entry = match archive.by_name(name) {
        Ok(entry) => entry,
        Err(zip::result::ZipError::FileNotFound) => return Ok(false),
        Err(e) => bail!("cannot read {name}: {e}"),
    };
    if entry.encrypted() {
        bail!("password-protected");
    }
    let capped = Capped {
        inner: entry,
        left: MAX_ENTRY_BYTES,
        name: name.to_string(),
    };
    let mut reader = Reader::from_reader(BufReader::new(capped));
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();
    loop {
        let event = reader
            .read_event_into(&mut buf)
            .map_err(|e| anyhow!("{name}: {e}"))?;
        if matches!(event, Event::Eof) {
            break;
        }
        each(event)?;
        buf.clear();
    }
    Ok(true)
}

/// An element's name without its namespace prefix.
pub(super) fn local(e: &BytesStart) -> String {
    e.local_name().as_ref().to_string()
}

/// The value of attribute `name` (local name, any prefix).
pub(super) fn attr(e: &BytesStart, name: &str) -> Option<String> {
    e.attributes()
        .flatten()
        .find(|a| a.key.local_name().as_ref() == name)
        .and_then(|a| {
            a.normalized_value(quick_xml::XmlVersion::Implicit1_0)
                .ok()
                .map(|v| v.into_owned())
        })
}

/// The text of an entity or character reference (`&amp;`, `&#233;`).
fn reference(name: &str) -> Option<char> {
    if let Some(code) = name.strip_prefix('#') {
        let n = match code.strip_prefix('x').or_else(|| code.strip_prefix('X')) {
            Some(hex) => u32::from_str_radix(hex, 16).ok()?,
            None => code.parse().ok()?,
        };
        return char::from_u32(n);
    }
    Some(match name {
        "amp" => '&',
        "lt" => '<',
        "gt" => '>',
        "quot" => '"',
        "apos" => '\'',
        _ => return None,
    })
}

/// Text events as a string; references resolved.
pub(super) fn text_of(event: &Event) -> Option<String> {
    match event {
        Event::Text(t) => Some(t.xml10_content().into_owned()),
        Event::CData(t) => Some(t.xml10_content().into_owned()),
        Event::GeneralRef(r) => reference(r.as_ref()).map(String::from),
        _ => None,
    }
}

/// Collects paragraphs from a stream of open, text and close events, whatever the format:
/// paragraphs nest (a text box inside a paragraph, a note's body), tables nest, and a cell's
/// paragraphs are joined into the cell.
#[derive(Default)]
pub(super) struct Collector {
    pub(super) paras: Vec<Para>,
    /// Open paragraphs, innermost last: text, heading level, and whether it is dropped.
    open: Vec<(String, Option<usize>, bool)>,
    /// Open tables: the cells of the current row, and the current cell when one is open.
    tables: Vec<(Vec<String>, Option<String>)>,
    /// Depth of an ignored subtree, when inside one.
    pub(super) skip: usize,
}

impl Collector {
    pub(super) fn text(&mut self, text: &str) {
        if self.skip > 0 {
            return;
        }
        if let Some((buf, _, _)) = self.open.last_mut() {
            buf.push_str(text);
        }
    }

    pub(super) fn start_para(&mut self, level: Option<usize>) {
        if self.skip == 0 {
            self.open.push((String::new(), level, false));
        }
    }

    pub(super) fn set_level(&mut self, level: Option<usize>) {
        if let (0, Some(open)) = (self.skip, self.open.last_mut()) {
            if level.is_some() {
                open.1 = level;
            }
        }
    }

    /// Leaves the current paragraph out (a table of contents' entry).
    pub(super) fn drop_para(&mut self) {
        if let (0, Some(open)) = (self.skip, self.open.last_mut()) {
            open.2 = true;
        }
    }

    pub(super) fn end_para(&mut self) {
        if self.skip > 0 {
            return;
        }
        let Some((text, level, dropped)) = self.open.pop() else {
            return;
        };
        let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
        if text.is_empty() || dropped {
            return;
        }
        if let Some((_, Some(cell))) = self.tables.last_mut() {
            if !cell.is_empty() {
                cell.push(' ');
            }
            cell.push_str(&text);
            return;
        }
        // A paragraph inside a paragraph (a text box) closes first and comes first.
        self.paras.push(Para { text, level });
    }

    pub(super) fn start_table(&mut self) {
        if self.skip == 0 {
            self.tables.push((Vec::new(), None));
        }
    }

    pub(super) fn start_cell(&mut self) {
        if let (0, Some(table)) = (self.skip, self.tables.last_mut()) {
            table.1 = Some(String::new());
        }
    }

    pub(super) fn end_cell(&mut self) {
        if let (0, Some(table)) = (self.skip, self.tables.last_mut()) {
            let cell = table.1.take().unwrap_or_default();
            table.0.push(cell);
        }
    }

    pub(super) fn end_row(&mut self) {
        if self.skip > 0 {
            return;
        }
        let Some(table) = self.tables.last_mut() else {
            return;
        };
        let cells: Vec<String> = std::mem::take(&mut table.0)
            .into_iter()
            .map(|c| c.trim().to_string())
            .collect();
        if cells.iter().all(|c| c.is_empty()) {
            return;
        }
        let row = cells.join(" | ");
        // A table inside a cell: its rows are the outer cell's text.
        let depth = self.tables.len();
        if depth > 1 {
            if let Some(cell) = self.tables[depth - 2].1.as_mut() {
                if !cell.is_empty() {
                    cell.push(' ');
                }
                cell.push_str(&row);
                return;
            }
        }
        self.paras.push(Para {
            text: row,
            level: None,
        });
    }

    pub(super) fn end_table(&mut self) {
        if self.skip == 0 {
            self.tables.pop();
        }
    }
}

/// A DOCX's styles: id → (outline level, based on).
// a one-off row tuple.
#[allow(clippy::type_complexity)]
fn docx_styles(archive: &mut Archive) -> Result<HashMap<String, (Option<usize>, Option<String>)>> {
    let mut styles: HashMap<String, (Option<usize>, Option<String>)> = HashMap::new();
    let mut current: Option<String> = None;
    let mut in_ppr = false;
    read_xml(archive, "word/styles.xml", &mut |event| {
        match &event {
            Event::Start(e) | Event::Empty(e) => {
                let empty = matches!(event, Event::Empty(_));
                match local(e).as_str() {
                    "style" if !empty => {
                        current = attr(e, "styleId");
                        if let Some(id) = &current {
                            styles.entry(id.clone()).or_default();
                        }
                    }
                    "name" => {
                        // Built-in heading styles keep their English internal name.
                        if let (Some(id), Some(name)) = (&current, attr(e, "val")) {
                            let lower = name.to_ascii_lowercase();
                            let level = match lower.strip_prefix("heading ") {
                                Some(n) => n
                                    .trim()
                                    .parse::<usize>()
                                    .ok()
                                    .filter(|n| (1..=9).contains(n)),
                                None if lower == "title" => Some(1),
                                None => None,
                            };
                            if let (Some(level), Some(style)) = (level, styles.get_mut(id)) {
                                style.0.get_or_insert(level);
                            }
                        }
                    }
                    "basedOn" => {
                        if let (Some(id), Some(base)) = (&current, attr(e, "val")) {
                            if let Some(style) = styles.get_mut(id) {
                                style.1 = Some(base);
                            }
                        }
                    }
                    "pPr" if !empty => in_ppr = true,
                    "outlineLvl" if in_ppr => {
                        if let (Some(id), Some(level)) = (
                            &current,
                            attr(e, "val").and_then(|v| v.parse::<usize>().ok()),
                        ) {
                            if let Some(style) = styles.get_mut(id) {
                                // 9 is body text.
                                style.0 = (level < 9).then_some(level + 1);
                            }
                        }
                    }
                    _ => {}
                }
            }
            Event::End(e) => match e.local_name().as_ref() {
                "style" => current = None,
                "pPr" => in_ppr = false,
                _ => {}
            },
            _ => {}
        }
        Ok(())
    })?;
    Ok(styles)
}

/// A style's heading level, through its `basedOn` chain.
fn style_level(
    styles: &HashMap<String, (Option<usize>, Option<String>)>,
    id: &str,
) -> Option<usize> {
    let mut id = id.to_string();
    for _ in 0..16 {
        let (level, base) = styles.get(&id)?;
        if level.is_some() {
            return *level;
        }
        id = base.clone()?;
    }
    None
}

/// The paragraphs of one WordprocessingML part (`document.xml`, `footnotes.xml`, …).
fn docx_part(
    archive: &mut Archive,
    name: &str,
    styles: &HashMap<String, (Option<usize>, Option<String>)>,
    out: &mut Collector,
) -> Result<bool> {
    // Skipped with their subtree: deleted text, field codes, the fallback copy of a drawing
    // (its text box is read once, from the choice), and a footnote separator.
    const SKIP: &[&str] = &[
        "del",
        "moveFrom",
        "instrText",
        "delInstrText",
        "Fallback",
        "comment",
    ];
    let mut in_t = false;
    read_xml(archive, name, &mut |event| {
        match &event {
            Event::Start(e) => {
                let tag = local(e);
                if out.skip > 0 {
                    out.skip += 1;
                    return Ok(());
                }
                match tag.as_str() {
                    t if SKIP.contains(&t) => out.skip = 1,
                    "footnote" | "endnote" if attr(e, "type").is_some_and(|t| t != "normal") => {
                        out.skip = 1
                    }
                    "p" => out.start_para(None),
                    "t" => in_t = true,
                    "tbl" => out.start_table(),
                    "tc" => out.start_cell(),
                    _ => {}
                }
            }
            Event::Empty(e) => {
                if out.skip > 0 {
                    return Ok(());
                }
                match local(e).as_str() {
                    "pStyle" => {
                        let id = attr(e, "val").unwrap_or_default();
                        if is_toc_style(&id) {
                            out.drop_para();
                        }
                        out.set_level(style_level(styles, &id));
                    }
                    "outlineLvl" => out.set_level(
                        attr(e, "val")
                            .and_then(|v| v.parse::<usize>().ok())
                            .filter(|l| *l < 9)
                            .map(|l| l + 1),
                    ),
                    "tab" | "br" | "cr" => out.text(" "),
                    "noBreakHyphen" => out.text("-"),
                    _ => {}
                }
            }
            Event::End(e) => {
                if out.skip > 0 {
                    out.skip -= 1;
                    return Ok(());
                }
                match e.local_name().as_ref() {
                    "p" => out.end_para(),
                    "t" => in_t = false,
                    "tc" => out.end_cell(),
                    "tr" => out.end_row(),
                    "tbl" => out.end_table(),
                    _ => {}
                }
            }
            other => {
                if in_t {
                    if let Some(text) = text_of(other) {
                        out.text(&text);
                    }
                }
            }
        }
        Ok(())
    })
}

/// A table of contents in a DOCX is a field whose entries use the TOC styles (`TOC1`, …, and
/// `TOCHeading`); their text repeats the headings, so they are dropped.
fn is_toc_style(id: &str) -> bool {
    id.to_ascii_lowercase().starts_with("toc")
}

/// A `.docx`'s text as located units.
pub fn docx(bytes: &[u8]) -> Result<Vec<Unit>> {
    let mut archive = open(bytes)?;
    let styles = docx_styles(&mut archive)?;
    let mut out = Collector::default();
    if !docx_part(&mut archive, "word/document.xml", &styles, &mut out)? {
        bail!("not a Word document (no word/document.xml)");
    }
    let body = out.paras.len();
    for part in ["word/footnotes.xml", "word/endnotes.xml"] {
        docx_part(&mut archive, part, &styles, &mut out).with_context(|| part.to_string())?;
    }
    let mut paras = out.paras;
    if paras.len() > body {
        paras.insert(
            body,
            Para {
                text: "Notes".into(),
                level: Some(1),
            },
        );
    }
    Ok(sections(paras, "para"))
}

/// The paragraphs of an ODT's `content.xml`.
fn odt_content(archive: &mut Archive, out: &mut Collector) -> Result<bool> {
    // Skipped with their subtree: comments, the deleted text of tracked changes, a note's
    // number, a table of contents (it repeats the headings), the sequence declarations.
    const SKIP: &[&str] = &[
        "annotation",
        "tracked-changes",
        "note-citation",
        "table-of-content",
        "sequence-decls",
        "illustration-index",
        "alphabetical-index",
        "bibliography",
    ];
    let mut in_body = false;
    read_xml(archive, "content.xml", &mut |event| {
        match &event {
            Event::Start(e) => {
                let tag = local(e);
                if out.skip > 0 {
                    out.skip += 1;
                    return Ok(());
                }
                match tag.as_str() {
                    "body" => in_body = true,
                    t if SKIP.contains(&t) => out.skip = 1,
                    "p" if in_body => out.start_para(None),
                    "h" if in_body => out.start_para(Some(
                        attr(e, "outline-level")
                            .and_then(|v| v.parse::<usize>().ok())
                            .unwrap_or(1)
                            .clamp(1, 9),
                    )),
                    "table" if in_body => out.start_table(),
                    "table-cell" if in_body => out.start_cell(),
                    _ => {}
                }
            }
            Event::Empty(e) => {
                if out.skip > 0 || !in_body {
                    return Ok(());
                }
                match local(e).as_str() {
                    "s" => {
                        let n = attr(e, "c")
                            .and_then(|v| v.parse::<usize>().ok())
                            .unwrap_or(1)
                            .min(64);
                        out.text(&" ".repeat(n));
                    }
                    "tab" | "line-break" => out.text(" "),
                    // An empty cell still takes its place in the row.
                    "table-cell" => {
                        out.start_cell();
                        out.end_cell();
                    }
                    _ => {}
                }
            }
            Event::End(e) => {
                if out.skip > 0 {
                    out.skip -= 1;
                    return Ok(());
                }
                match e.local_name().as_ref() {
                    "body" => in_body = false,
                    "p" | "h" if in_body => out.end_para(),
                    "table-cell" if in_body => out.end_cell(),
                    "table-row" if in_body => out.end_row(),
                    "table" if in_body => out.end_table(),
                    _ => {}
                }
            }
            other => {
                if in_body {
                    if let Some(text) = text_of(other) {
                        out.text(&text);
                    }
                }
            }
        }
        Ok(())
    })
}

/// An `.odt`'s text as located units.
pub fn odt(bytes: &[u8]) -> Result<Vec<Unit>> {
    let mut archive = open(bytes)?;
    let mut out = Collector::default();
    if !odt_content(&mut archive, &mut out)? {
        bail!("not an OpenDocument text (no content.xml)");
    }
    Ok(sections(out.paras, "para"))
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use std::io::Write;

    /// A ZIP archive of the given entries, deflated.
    pub fn zip_of(entries: &[(&str, &str)]) -> Vec<u8> {
        let mut out = Cursor::new(Vec::new());
        {
            let mut writer = zip::ZipWriter::new(&mut out);
            let options = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Deflated);
            for (name, body) in entries {
                writer.start_file(*name, options).unwrap();
                writer.write_all(body.as_bytes()).unwrap();
            }
            writer.finish().unwrap();
        }
        out.into_inner()
    }

    const W: &str = r#"xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006""#;

    /// A small DOCX: a localized heading style (German id, English internal name), a style based
    /// on it, a table, a text box with its fallback copy, tracked changes and a footnote.
    pub fn sample_docx() -> Vec<u8> {
        let styles = format!(
            r#"<?xml version="1.0"?><w:styles {W}>
            <w:style w:type="paragraph" w:styleId="berschrift1"><w:name w:val="heading 1"/></w:style>
            <w:style w:type="paragraph" w:styleId="Custom2"><w:name w:val="Mein Abschnitt"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>
            <w:style w:type="paragraph" w:styleId="Derived"><w:name w:val="Derived"/><w:basedOn w:val="Custom2"/></w:style>
            <w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
            </w:styles>"#
        );
        let document = format!(
            r#"<?xml version="1.0"?><w:document {W}><w:body>
            <w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr><w:r><w:t>Notice 1</w:t></w:r></w:p>
            <w:p><w:r><w:t>Before any heading.</w:t></w:r></w:p>
            <w:p><w:pPr><w:pStyle w:val="berschrift1"/></w:pPr><w:r><w:t>Notice</w:t></w:r></w:p>
            <w:p><w:r><w:t xml:space="preserve">The notice period is </w:t></w:r><w:r><w:t>ninety days &amp; more</w:t></w:r><w:del><w:r><w:delText>sixty</w:delText></w:r></w:del></w:p>
            <w:p><w:pPr><w:pStyle w:val="Derived"/></w:pPr><w:r><w:t>Fees</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Price</w:t></w:r></w:p></w:tc></w:tr>
            <w:tr><w:tc><w:p><w:r><w:t>Setup</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>100</w:t></w:r></w:p><w:p><w:r><w:t>EUR</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
            <w:p><w:r><mc:AlternateContent><mc:Choice><w:drawing><w:txbxContent><w:p><w:r><w:t>Boxed text</w:t></w:r></w:p></w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent><w:p><w:r><w:t>Boxed text</w:t></w:r></w:p></w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent></w:r><w:r><w:t>After the box</w:t></w:r><w:r><w:fldChar/><w:instrText>PAGE</w:instrText></w:r></w:p>
            </w:body></w:document>"#
        );
        let footnotes = format!(
            r#"<?xml version="1.0"?><w:footnotes {W}><w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:t>----</w:t></w:r></w:p></w:footnote>
            <w:footnote w:id="1"><w:p><w:r><w:t>See the annex.</w:t></w:r></w:p></w:footnote></w:footnotes>"#
        );
        zip_of(&[
            ("[Content_Types].xml", "<Types/>"),
            ("word/document.xml", &document),
            ("word/styles.xml", &styles),
            ("word/footnotes.xml", &footnotes),
        ])
    }

    #[test]
    fn a_docx_becomes_sections_by_outline_level_with_tables_and_notes() {
        let units = docx(&sample_docx()).unwrap();
        let got: Vec<(&str, &str)> = units
            .iter()
            .map(|u| (u.loc.as_str(), u.heading.as_str()))
            .collect();
        assert_eq!(
            got,
            [
                ("para 1-1", ""),
                ("para 2-3", "Notice"),
                ("para 4-8", "Notice > Fees"),
                ("para 9-10", "Notes")
            ],
            "{units:#?}"
        );
        let text: Vec<&str> = units.iter().flat_map(|u| u.text.lines()).collect();
        assert_eq!(
            text[2], "The notice period is ninety days & more",
            "deleted text is not read"
        );
        assert_eq!(
            &text[4..8],
            [
                "Item | Price",
                "Setup | 100 EUR",
                "Boxed text",
                "After the box"
            ],
            "{text:?}"
        );
        assert_eq!(text[9], "See the annex.");
        assert!(!text.iter().any(|l| l.contains("----")
            || l.contains("PAGE")
            || l.contains("sixty")
            || l.contains("Notice 1")));
        assert_eq!(
            (units[1].line_first, units[1].line_last),
            (Some(2), Some(3))
        );
    }

    #[test]
    fn an_odt_becomes_sections_with_lists_tables_and_spaces() {
        let content = r#"<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
            <office:body><office:text><text:sequence-decls><text:sequence-decl text:name="Table"/></text:sequence-decls>
            <text:table-of-content><text:index-body><text:p>Delivery .... 1</text:p></text:index-body></text:table-of-content>
            <text:h text:outline-level="1">Delivery</text:h>
            <text:p>Goods arrive<text:s text:c="2"/>within <text:span>ten</text:span> days.<text:note><text:note-citation>1</text:note-citation><text:note-body><text:p>Working days.</text:p></text:note-body></text:note></text:p>
            <text:list><text:list-item><text:p>First item</text:p></text:list-item></text:list>
            <text:h text:outline-level="2">Rates</text:h>
            <table:table><table:table-row><table:table-cell><text:p>Zone</text:p></table:table-cell><table:table-cell/><table:table-cell><text:p>Rate</text:p></table:table-cell></table:table-row></table:table>
            <office:annotation><text:p>A reviewer's comment</text:p></office:annotation>
            </office:text></office:body></office:document-content>"#;
        let bytes = zip_of(&[
            ("mimetype", "application/vnd.oasis.opendocument.text"),
            ("content.xml", content),
        ]);
        let units = odt(&bytes).unwrap();
        let got: Vec<(&str, &str)> = units
            .iter()
            .map(|u| (u.loc.as_str(), u.heading.as_str()))
            .collect();
        assert_eq!(
            got,
            [("para 1-4", "Delivery"), ("para 5-6", "Delivery > Rates")],
            "{units:#?}"
        );
        let text: Vec<&str> = units.iter().flat_map(|u| u.text.lines()).collect();
        assert_eq!(
            text,
            [
                "Delivery",
                "Working days.",
                "Goods arrive within ten days.",
                "First item",
                "Rates",
                "Zone |  | Rate"
            ]
        );
    }

    #[test]
    fn a_deflate_bomb_and_a_wrong_archive_fail_with_a_reason() {
        // 300 MB of one letter deflates to a few hundred KB.
        let mut out = Cursor::new(Vec::new());
        {
            let mut writer = zip::ZipWriter::new(&mut out);
            let options = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Deflated);
            writer.start_file("content.xml", options).unwrap();
            let block = vec![b'a'; 1 << 20];
            writer
                .write_all(b"<office:document-content><office:body><office:text><text:p>")
                .unwrap();
            for _ in 0..300 {
                writer.write_all(&block).unwrap();
            }
            writer.finish().unwrap();
        }
        let bomb = out.into_inner();
        assert!(bomb.len() < 4 << 20, "{}", bomb.len());
        let error = format!("{:#}", odt(&bomb).unwrap_err());
        assert!(error.contains("ZIP bomb"), "{error}");
        let error = format!(
            "{:#}",
            docx(&zip_of(&[("content.xml", "<x/>")])).unwrap_err()
        );
        assert!(error.contains("not a Word document"), "{error}");
        assert!(docx(b"PK\x03\x04 broken").is_err());
    }
}
