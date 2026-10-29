//! HTML (spec §7.1, Phase 2): a saved page becomes paragraphs, then sections by heading
//! ([`super::sections`]).
//!
//! Parsed by html5ever through `scraper`, as `ling-fetch` reads pages (`ling-web-rs/src/html_text.rs`),
//! and with the same subtrees dropped as carrying no prose ([`NON_CONTENT_TAGS`], checked against
//! ling-fetch's list by a test), except `form`: a page built by ASP.NET or a shop holds its whole
//! body in one. A readability pass on top: the page's `<head>`, navigation (`nav`, `footer`,
//! `role="navigation"`, hidden elements) and Wikipedia's navigation boxes, edit links and
//! reference markers are left out. Every block element ends a paragraph; a table row is one
//! paragraph, its cells joined by ` | `; `h1`–`h6` start sections and give the heading path.
//! The charset is the byte-order mark's, else `<meta charset>`'s, else UTF-8, else Windows-1252.

use ego_tree::iter::Edge;
use scraper::{Html, Node};

use super::{sections, Para, Unit};

/// ling-fetch's subtrees without prose (`ling-web-rs/src/html_text.rs`), less `form`.
pub const NON_CONTENT_TAGS: [&str; 7] = [
    "script", "style", "noscript", "svg", "canvas", "template", "iframe",
];

/// Also left out here: the head, controls, embedded objects, and the page's navigation.
const DROPPED_TAGS: &[&str] = &[
    "head", "select", "textarea", "button", "object", "embed", "nav", "footer", "math",
];

/// Classes of navigation and apparatus (Wikipedia's chiefly): navigation boxes, edit links,
/// reference lists and markers, category and print footers.
const DROPPED_CLASSES: &[&str] = &[
    "navbox",
    "vertical-navbox",
    "mw-editsection",
    "reflist",
    "mw-references-wrap",
    "references",
    "reference",
    "printfooter",
    "catlinks",
    "mw-jump-link",
    "navigation-not-searchable",
];

/// Elements that end a paragraph where they open and where they close.
const BLOCK_TAGS: &[&str] = &[
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "br",
    "caption",
    "center",
    "dd",
    "details",
    "dialog",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "form",
    "header",
    "hgroup",
    "hr",
    "legend",
    "li",
    "main",
    "menu",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tbody",
    "thead",
    "tfoot",
    "ul",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
];

/// Decodes a page: BOM, then `<meta charset>` in the first 4 KiB, then UTF-8, then Windows-1252.
pub fn decode(bytes: &[u8]) -> String {
    if let Some((encoding, bom)) = encoding_rs::Encoding::for_bom(bytes) {
        return encoding
            .decode_without_bom_handling(&bytes[bom..])
            .0
            .into_owned();
    }
    let head = String::from_utf8_lossy(&bytes[..bytes.len().min(4096)]).to_ascii_lowercase();
    if let Some(at) = head.find("charset=") {
        let label: String = head[at + 8..]
            .trim_start_matches(['"', '\'', ' '])
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | ':'))
            .collect();
        if let Some(encoding) = encoding_rs::Encoding::for_label(label.as_bytes()) {
            // A page that says UTF-16 in ASCII is not UTF-16.
            if encoding != encoding_rs::UTF_16LE && encoding != encoding_rs::UTF_16BE {
                return encoding.decode_without_bom_handling(bytes).0.into_owned();
            }
        }
    }
    match std::str::from_utf8(bytes) {
        Ok(text) => text.to_string(),
        Err(_) => encoding_rs::WINDOWS_1252
            .decode_without_bom_handling(bytes)
            .0
            .into_owned(),
    }
}

fn dropped(element: &scraper::node::Element) -> bool {
    let name = element.name();
    if NON_CONTENT_TAGS.contains(&name) || DROPPED_TAGS.contains(&name) {
        return true;
    }
    if element.attr("hidden").is_some()
        || element.attr("aria-hidden") == Some("true")
        || element.attr("role") == Some("navigation")
    {
        return true;
    }
    element.classes().any(|c| DROPPED_CLASSES.contains(&c))
}

/// The walk's state: the paragraph being read, and the open tables' rows.
#[derive(Default)]
struct Walk {
    paras: Vec<Para>,
    line: String,
    /// The level of the heading being read.
    level: Option<usize>,
    /// Open tables: the current row's cells, and the current cell.
    tables: Vec<(Vec<String>, Option<String>)>,
}

impl Walk {
    fn in_cell(&self) -> bool {
        self.tables.last().is_some_and(|t| t.1.is_some())
    }

    fn text(&mut self, text: &str) {
        match self.tables.last_mut() {
            Some((_, Some(cell))) => cell.push_str(text),
            _ => self.line.push_str(text),
        }
    }

    /// Ends the paragraph (inside a cell: a space).
    fn flush(&mut self) {
        if self.in_cell() {
            self.text(" ");
            return;
        }
        let text = self.line.split_whitespace().collect::<Vec<_>>().join(" ");
        self.line.clear();
        if !text.is_empty() {
            self.paras.push(Para {
                text,
                level: self.level,
            });
        }
    }

    fn end_row(&mut self) {
        let Some(table) = self.tables.last_mut() else {
            return;
        };
        if let Some(cell) = table.1.take() {
            table.0.push(cell);
        }
        let cells: Vec<String> = std::mem::take(&mut table.0)
            .into_iter()
            .map(|c| c.split_whitespace().collect::<Vec<_>>().join(" "))
            .collect();
        if cells.iter().all(|c| c.is_empty()) {
            return;
        }
        let row = cells.join(" | ");
        let depth = self.tables.len();
        if depth > 1 && self.tables[depth - 2].1.is_some() {
            if let Some(cell) = self.tables[depth - 2].1.as_mut() {
                cell.push(' ');
                cell.push_str(&row);
            }
            return;
        }
        self.flush();
        self.paras.push(Para {
            text: row,
            level: None,
        });
    }
}

/// A page's text as located units.
pub fn html(bytes: &[u8]) -> Vec<Unit> {
    let document = Html::parse_document(&decode(bytes));
    let mut walk = Walk::default();
    let mut skipping = None;
    let mut pre = 0usize;
    for edge in document.tree.root().traverse() {
        match edge {
            Edge::Open(node) => {
                if skipping.is_some() {
                    continue;
                }
                match node.value() {
                    Node::Element(element) => {
                        if dropped(element) {
                            skipping = Some(node.id());
                            continue;
                        }
                        let name = element.name();
                        match name {
                            "table" => {
                                walk.flush();
                                walk.tables.push((Vec::new(), None));
                                continue;
                            }
                            "td" | "th" if !walk.tables.is_empty() => {
                                let table = walk.tables.last_mut().unwrap();
                                if let Some(cell) = table.1.take() {
                                    table.0.push(cell);
                                }
                                table.1 = Some(String::new());
                                continue;
                            }
                            "tr" => continue,
                            "pre" => pre += 1,
                            _ => {}
                        }
                        if BLOCK_TAGS.contains(&name) {
                            walk.flush();
                        }
                        if let Some(level) = heading_level(name) {
                            if !walk.in_cell() {
                                walk.level = Some(level);
                            }
                        }
                    }
                    Node::Text(text) => {
                        if pre > 0 {
                            let mut lines = text.split('\n');
                            if let Some(first) = lines.next() {
                                walk.text(first);
                            }
                            for line in lines {
                                walk.flush();
                                walk.text(line);
                            }
                        } else {
                            walk.text(text);
                        }
                    }
                    _ => {}
                }
            }
            Edge::Close(node) => {
                if skipping == Some(node.id()) {
                    skipping = None;
                    continue;
                }
                if skipping.is_some() {
                    continue;
                }
                let Node::Element(element) = node.value() else {
                    continue;
                };
                let name = element.name();
                match name {
                    "table" => {
                        walk.end_row();
                        walk.tables.pop();
                        continue;
                    }
                    "td" | "th" => {
                        if let Some(table) = walk.tables.last_mut() {
                            if let Some(cell) = table.1.take() {
                                table.0.push(cell);
                            }
                        }
                        continue;
                    }
                    "tr" => {
                        walk.end_row();
                        continue;
                    }
                    "pre" => pre = pre.saturating_sub(1),
                    _ => {}
                }
                if BLOCK_TAGS.contains(&name) {
                    walk.flush();
                }
                if heading_level(name).is_some() {
                    walk.level = None;
                }
            }
        }
    }
    walk.flush();
    sections(walk.paras, "para")
}

fn heading_level(name: &str) -> Option<usize> {
    match name {
        "h1" => Some(1),
        "h2" => Some(2),
        "h3" => Some(3),
        "h4" => Some(4),
        "h5" => Some(5),
        "h6" => Some(6),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_page_becomes_sections_without_its_navigation() {
        let page = r#"<!DOCTYPE html><html><head><title>Coffee - Wikipedia</title><style>p{}</style></head>
            <body><nav><a href="/">Main page</a></nav><div class="mw-jump-link">Jump to content</div>
            <h1>Coffee</h1><div>Coffee is a beverage<sup class="reference">[1]</sup> brewed from <b>roasted</b> beans.<p>It is popular.</p>After the paragraph.</div>
            <h2>History<span class="mw-editsection">[edit]</span></h2><ul><li>Ethiopia</li><li>Yemen</li></ul>
            <h3>Trade</h3><table><caption>Exports</caption><tr><th>Country</th><th>Tonnes</th></tr><tr><td>Brazil</td><td>2 <i>million</i></td></tr></table>
            <pre>line one
line two</pre><form><p>Inside a form</p><input value="x"><button>Send</button></form>
            <script>var x = 1;</script><div hidden>secret</div><footer>Privacy policy</footer>
            <table class="navbox"><tr><td>Tea · Cocoa</td></tr></table></body></html>"#;
        let units = html(page.as_bytes());
        let got: Vec<(&str, &str)> = units
            .iter()
            .map(|u| (u.loc.as_str(), u.heading.as_str()))
            .collect();
        assert_eq!(
            got,
            [
                ("para 1-4", "Coffee"),
                ("para 5-7", "Coffee > History"),
                ("para 8-14", "Coffee > History > Trade")
            ],
            "{units:#?}"
        );
        let lines: Vec<&str> = units.iter().flat_map(|u| u.text.lines()).collect();
        assert_eq!(
            lines,
            [
                "Coffee",
                "Coffee is a beverage brewed from roasted beans.",
                "It is popular.",
                "After the paragraph.",
                "History",
                "Ethiopia",
                "Yemen",
                "Trade",
                "Exports",
                "Country | Tonnes",
                "Brazil | 2 million",
                "line one",
                "line two",
                "Inside a form"
            ],
        );
        let all = lines.join("\n");
        for gone in [
            "Main page",
            "Jump to content",
            "[1]",
            "[edit]",
            "var x",
            "secret",
            "Privacy",
            "Tea",
            "Send",
            "Wikipedia",
        ] {
            assert!(!all.contains(gone), "{gone:?} in {all:?}");
        }
    }

    #[test]
    fn the_charset_comes_from_the_page() {
        assert_eq!(
            decode(b"<meta charset=\"iso-8859-1\"><p>caf\xe9</p>"),
            "<meta charset=\"iso-8859-1\"><p>café</p>"
        );
        assert_eq!(decode("<p>café</p>".as_bytes()), "<p>café</p>");
        assert_eq!(decode(b"<p>caf\xe9</p>"), "<p>café</p>");
        let utf16: Vec<u8> = [0xFF, 0xFE]
            .into_iter()
            .chain("<p>ok</p>".encode_utf16().flat_map(|u| u.to_le_bytes()))
            .collect();
        assert_eq!(decode(&utf16), "<p>ok</p>");
    }

    #[test]
    fn the_dropped_tags_are_ling_fetchs_but_form() {
        // The same list as ling-fetch (spec §7.1: the same code), checked where the checkout has it.
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../ling-web-rs/src/html_text.rs");
        let Ok(source) = std::fs::read_to_string(&path) else {
            return;
        };
        let start = source
            .find("NON_CONTENT_TAGS: [&str; 8] = [")
            .expect("ling-fetch's list");
        let list = &source[start..start + source[start..].find("];").unwrap()];
        let theirs: Vec<&str> = list.split('"').skip(1).step_by(2).collect();
        let mut ours: Vec<&str> = NON_CONTENT_TAGS.to_vec();
        ours.push("form");
        assert_eq!(theirs, ours);
    }
}
