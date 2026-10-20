//! Presentations (spec §7.1, Phase 3): `.pptx` and `.odp` become one unit per slide, located
//! `slide N` in the order the presentation shows them, with the slide's title as its heading.
//!
//! The same streamed ZIP and XML reading as Word and OpenDocument text (office.rs, the same
//! inflation cap): every paragraph of every text frame, a table row as one paragraph, then the
//! speaker notes after a `Notes:` line. A slide is a chunk of its own (and more when long), as
//! a message is, so a passage's heading is its own slide's title. Slide numbers, dates, footers and headers are left out.
//! All slides share one line numbering, so `read` takes `slide 4` or any range of lines.

use anyhow::{bail, Result};
use quick_xml::events::Event;

use super::office::{attr, local, open, read_xml, text_of, Archive, Collector};
use super::{Para, Unit};

/// Placeholders whose text is the slide's furniture, not its content.
const FURNITURE: &[&str] = &["sldNum", "dt", "ftr", "hdr", "page-number", "date-time", "footer", "header"];

/// A PresentationML part's paragraphs (a slide or its notes): the title's paragraphs at level 1.
fn pptx_part(archive: &mut Archive, name: &str) -> Result<Vec<Para>> {
    let mut out = Collector::default();
    // Whether the shape being read is the title, or furniture to leave out.
    let mut title = false;
    let mut furniture = false;
    let mut in_t = false;
    read_xml(archive, name, &mut |event| {
        match &event {
            Event::Start(e) => {
                if out.skip > 0 {
                    out.skip += 1;
                    return Ok(());
                }
                match local(e).as_str() {
                    "sp" | "graphicFrame" => {
                        title = false;
                        furniture = false;
                    }
                    "txBody" if furniture => out.skip = 1,
                    "p" => out.start_para(title.then_some(1)),
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
                    "ph" => {
                        let kind = attr(e, "type").unwrap_or_default();
                        title = matches!(kind.as_str(), "title" | "ctrTitle");
                        furniture = FURNITURE.contains(&kind.as_str());
                    }
                    "br" | "tab" => out.text(" "),
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
                    "sp" | "graphicFrame" => {
                        title = false;
                        furniture = false;
                    }
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
    })?;
    Ok(out.paras)
}

/// `Id → Target` of a relationships part, targets resolved against `base` (a folder).
fn relationships(archive: &mut Archive, name: &str, base: &str) -> Result<Vec<(String, String, String)>> {
    let mut out = Vec::new();
    read_xml(archive, name, &mut |event| {
        if let Event::Empty(e) | Event::Start(e) = &event {
            if local(e) == "Relationship" {
                if let (Some(id), Some(target)) = (attr(e, "Id"), attr(e, "Target")) {
                    out.push((id, resolve(base, &target), attr(e, "Type").unwrap_or_default()));
                }
            }
        }
        Ok(())
    })?;
    Ok(out)
}

/// `ppt/slides` + `../notesSlides/notesSlide1.xml` → `ppt/notesSlides/notesSlide1.xml`.
fn resolve(base: &str, target: &str) -> String {
    if let Some(absolute) = target.strip_prefix('/') {
        return absolute.to_string();
    }
    let mut parts: Vec<&str> = base.split('/').filter(|p| !p.is_empty()).collect();
    for piece in target.split('/') {
        match piece {
            ".." => {
                parts.pop();
            }
            "." | "" => {}
            other => parts.push(other),
        }
    }
    parts.join("/")
}

/// One slide's unit from its paragraphs and notes.
fn slide_unit(n: usize, paras: Vec<Para>, notes: Vec<Para>, line: &mut u32) -> Option<Unit> {
    let heading = paras.iter().find(|p| p.level.is_some()).map(|p| p.text.clone()).unwrap_or_default();
    let mut lines: Vec<String> = paras.into_iter().map(|p| p.text).collect();
    if !notes.is_empty() {
        lines.push("Notes:".into());
        lines.extend(notes.into_iter().map(|p| p.text));
    }
    if lines.is_empty() {
        return None;
    }
    let count = lines.len() as u32;
    let unit = Unit { loc: format!("slide {n}"), page: None, line_first: Some(*line), line_last: Some(*line + count - 1), heading, group: Some(n as u32), text: lines.join("\n") };
    *line += count;
    Some(unit)
}

/// A `.pptx`'s slides as located units.
pub fn pptx(bytes: &[u8]) -> Result<Vec<Unit>> {
    let mut archive = open(bytes)?;
    // The order the presentation shows: `sldIdLst` in presentation.xml, through its relationships.
    let mut order: Vec<String> = Vec::new();
    let found = read_xml(&mut archive, "ppt/presentation.xml", &mut |event| {
        if let Event::Empty(e) | Event::Start(e) = &event {
            if local(e) == "sldId" {
                // `r:id`, the relationship: an `id` with a namespace prefix (the plain `id` is a number).
                let relationship = e.attributes().flatten().find(|a| a.key.local_name().as_ref() == "id" && a.key.prefix().is_some());
                if let Some(id) = relationship.and_then(|a| a.normalized_value(quick_xml::XmlVersion::Implicit1_0).ok().map(|v| v.into_owned())) {
                    order.push(id);
                }
            }
        }
        Ok(())
    })?;
    if !found {
        bail!("not a PowerPoint presentation (no ppt/presentation.xml)");
    }
    let rels = relationships(&mut archive, "ppt/_rels/presentation.xml.rels", "ppt")?;
    let mut slides: Vec<String> = order.iter().filter_map(|id| rels.iter().find(|(rid, ..)| rid == id).map(|(_, target, _)| target.clone())).collect();
    if slides.is_empty() {
        // No list: the slide parts by number.
        let mut named: Vec<(u32, String)> = archive.file_names().flatten().filter_map(|n| n.strip_prefix("ppt/slides/slide")?.strip_suffix(".xml")?.parse().ok().map(|k| (k, n.to_string()))).collect();
        named.sort();
        slides = named.into_iter().map(|(_, n)| n).collect();
    }
    let mut units = Vec::new();
    let mut line = 1;
    for (i, slide) in slides.iter().enumerate() {
        let paras = pptx_part(&mut archive, slide)?;
        let (dir, file) = slide.rsplit_once('/').unwrap_or(("", slide));
        let notes = match relationships(&mut archive, &format!("{dir}/_rels/{file}.rels"), dir)?.into_iter().find(|(_, _, kind)| kind.ends_with("/notesSlide")) {
            Some((_, target, _)) => pptx_part(&mut archive, &target)?.into_iter().map(|p| Para { level: None, ..p }).collect(),
            None => Vec::new(),
        };
        units.extend(slide_unit(i + 1, paras, notes, &mut line));
    }
    Ok(units)
}

/// An `.odp`'s pages as located units.
pub fn odp(bytes: &[u8]) -> Result<Vec<Unit>> {
    let mut archive = open(bytes)?;
    // Per page: its paragraphs and its notes.
    let mut pages: Vec<(Vec<Para>, Vec<Para>)> = Vec::new();
    let mut out = Collector::default();
    let mut notes = Collector::default();
    let mut in_page = false;
    let mut in_notes = 0usize;
    let mut title = false;
    let found = read_xml(&mut archive, "content.xml", &mut |event| {
        // A page starts and ends outside either collector.
        match &event {
            Event::Start(e) if local(e) == "page" && out.skip == 0 => {
                in_page = true;
                out = Collector::default();
                notes = Collector::default();
                return Ok(());
            }
            Event::End(e) if e.local_name().as_ref() == "page" && out.skip == 0 && in_notes == 0 => {
                in_page = false;
                pages.push((std::mem::take(&mut out.paras), std::mem::take(&mut notes.paras)));
                return Ok(());
            }
            _ => {}
        }
        let target = if in_notes > 0 { &mut notes } else { &mut out };
        match &event {
            Event::Start(e) => {
                if target.skip > 0 {
                    target.skip += 1;
                    if in_notes > 0 {
                        in_notes += 1;
                    }
                    return Ok(());
                }
                if in_notes > 0 {
                    in_notes += 1;
                }
                match local(e).as_str() {
                    "notes" if in_page => in_notes = 1,
                    "frame" => {
                        let class = attr(e, "class").unwrap_or_default();
                        title = class == "title";
                        if FURNITURE.contains(&class.as_str()) {
                            target.skip = 1;
                        }
                    }
                    "annotation" | "note-citation" => target.skip = 1,
                    "p" | "h" if in_page => target.start_para((title && in_notes == 0).then_some(1)),
                    "table" if in_page => target.start_table(),
                    "table-cell" if in_page => target.start_cell(),
                    _ => {}
                }
            }
            Event::Empty(e) => {
                if target.skip > 0 || !in_page {
                    return Ok(());
                }
                match local(e).as_str() {
                    "s" => {
                        let n = attr(e, "c").and_then(|v| v.parse::<usize>().ok()).unwrap_or(1).min(64);
                        target.text(&" ".repeat(n));
                    }
                    "tab" | "line-break" => target.text(" "),
                    "table-cell" => {
                        target.start_cell();
                        target.end_cell();
                    }
                    _ => {}
                }
            }
            Event::End(e) => {
                if in_notes > 0 {
                    in_notes -= 1;
                }
                if target.skip > 0 {
                    target.skip -= 1;
                    return Ok(());
                }
                match e.local_name().as_ref() {
                    "frame" => title = false,
                    "p" | "h" if in_page => target.end_para(),
                    "table-cell" if in_page => target.end_cell(),
                    "table-row" if in_page => target.end_row(),
                    "table" if in_page => target.end_table(),
                    _ => {}
                }
            }
            other => {
                if in_page {
                    if let Some(text) = text_of(other) {
                        target.text(&text);
                    }
                }
            }
        }
        Ok(())
    })?;
    if !found {
        bail!("not an OpenDocument presentation (no content.xml)");
    }
    let mut line = 1;
    Ok(pages.into_iter().enumerate().filter_map(|(i, (paras, notes))| slide_unit(i + 1, paras, notes, &mut line)).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extract::office::tests::zip_of;

    const NS: &str = r#"xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships""#;

    fn slide(title: &str, body: &str) -> String {
        format!(
            r#"<p:sld {NS}><p:cSld><p:spTree>
            <p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp>
            <p:sp><p:nvSpPr><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>{body}</a:t></a:r><a:br/><a:r><a:t>more</a:t></a:r></a:p></p:txBody></p:sp>
            <p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>7</a:t></a:r></a:p></p:txBody></p:sp>
            </p:spTree></p:cSld></p:sld>"#
        )
    }

    #[test]
    fn a_pptx_is_read_slide_by_slide_in_the_order_shown_with_notes() {
        let presentation = format!(r#"<p:presentation {NS}><p:sldIdLst><p:sldId id="257" r:id="rId9"/><p:sldId id="256" r:id="rId8"/></p:sldIdLst></p:presentation>"#);
        let rels = r#"<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId8" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/><Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/></Relationships>"#;
        let slide_rels = r#"<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>"#;
        let notes = format!(r#"<p:notes {NS}><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Say the date twice.</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>"#);
        let table = format!(r#"<p:sld {NS}><p:cSld><p:spTree><p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>Q1</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>12%</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>"#);
        let bytes = zip_of(&[
            ("ppt/presentation.xml", &presentation),
            ("ppt/_rels/presentation.xml.rels", rels),
            ("ppt/slides/slide1.xml", &slide("Launch plan", "Ship on 1 May")),
            ("ppt/slides/_rels/slide1.xml.rels", slide_rels),
            ("ppt/notesSlides/notesSlide1.xml", &notes),
            ("ppt/slides/slide2.xml", &table),
        ]);
        let units = pptx(&bytes).unwrap();
        let got: Vec<(&str, &str, &str)> = units.iter().map(|u| (u.loc.as_str(), u.heading.as_str(), u.text.as_str())).collect();
        assert_eq!(got, [("slide 1", "", "Q1 | 12%"), ("slide 2", "Launch plan", "Launch plan\nShip on 1 May more\nNotes:\nSay the date twice.")]);
        assert_eq!((units[1].line_first, units[1].line_last), (Some(2), Some(5)));
        assert_eq!(resolve("ppt/slides", "../notesSlides/n1.xml"), "ppt/notesSlides/n1.xml");
    }

    #[test]
    fn an_odp_is_read_page_by_page_with_notes() {
        let content = r#"<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"><office:body><office:presentation>
            <draw:page draw:name="p1"><draw:frame presentation:class="title"><draw:text-box><text:p>Budget 2026</text:p></draw:text-box></draw:frame>
            <draw:frame presentation:class="outline"><draw:text-box><text:list><text:list-item><text:p>Travel down 8%</text:p></text:list-item></text:list></draw:text-box></draw:frame>
            <draw:frame presentation:class="page-number"><draw:text-box><text:p>1</text:p></draw:text-box></draw:frame>
            <presentation:notes><draw:frame presentation:class="notes"><draw:text-box><text:p>Mention the freeze.</text:p></draw:text-box></draw:frame></presentation:notes></draw:page>
            <draw:page draw:name="p2"><draw:frame><draw:text-box><text:p>Questions?</text:p></draw:text-box></draw:frame></draw:page>
            </office:presentation></office:body></office:document-content>"#;
        let units = odp(&zip_of(&[("mimetype", "application/vnd.oasis.opendocument.presentation"), ("content.xml", content)])).unwrap();
        let got: Vec<(&str, &str, &str)> = units.iter().map(|u| (u.loc.as_str(), u.heading.as_str(), u.text.as_str())).collect();
        assert_eq!(got, [("slide 1", "Budget 2026", "Budget 2026\nTravel down 8%\nNotes:\nMention the freeze."), ("slide 2", "", "Questions?")]);
    }
}
