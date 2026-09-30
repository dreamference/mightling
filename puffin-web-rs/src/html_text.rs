//! HTML to readable text, the way `WebTools._html_to_text` does it with BeautifulSoup.
//!
//! The Python version drops every non-content element, then joins all remaining text nodes with a
//! newline and collapses the blank runs that leaves. This does the same over html5ever's tree. The
//! two parsers recover from broken markup differently (html5ever follows the HTML standard,
//! `html.parser` does not), so the text of malformed pages can differ in where a line breaks, not
//! in what the words are.

use std::sync::LazyLock;

use ego_tree::iter::Edge;
use regex::Regex;
use scraper::Html;
use scraper::Node;

/// Everything that carries no prose. Dropped with its whole subtree, so the result reads like the
/// page rather than like its source.
pub const NON_CONTENT_TAGS: [&str; 8] = [
    "script", "style", "noscript", "svg", "canvas", "template", "iframe", "form",
];

static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[ \t]+").unwrap());
static BLANK_RUNS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\n\s*\n\s*").unwrap());

/// A page reduced to its title and text.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct PageText {
    pub title: String,
    pub text: String,
}

/// Extracts the title and the readable text of an HTML document.
pub fn html_to_text(html: &str) -> PageText {
    let document = Html::parse_document(html);
    let mut pieces: Vec<&str> = Vec::new();
    let mut title: Option<String> = None;
    let mut in_title = false;
    // The node whose subtree is being skipped. Iterative, because a recursive walk overflows the
    // stack on a page nested a few thousand elements deep.
    let mut skipping = None;

    for edge in document.tree.root().traverse() {
        match edge {
            Edge::Open(node) => {
                if skipping.is_some() {
                    continue;
                }
                match node.value() {
                    Node::Element(element) if NON_CONTENT_TAGS.contains(&element.name()) => {
                        skipping = Some(node.id());
                    }
                    Node::Element(element) if element.name() == "title" && title.is_none() => {
                        in_title = true;
                        title = Some(String::new());
                    }
                    Node::Text(text) => {
                        if in_title {
                            // `get_text(strip=True)`: each piece stripped, joined with nothing.
                            if let Some(title) = title.as_mut() {
                                title.push_str(text.trim());
                            }
                        }
                        pieces.push(text);
                    }
                    _ => {}
                }
            }
            Edge::Close(node) => {
                if skipping == Some(node.id()) {
                    skipping = None;
                } else if in_title
                    && matches!(node.value(), Node::Element(element) if element.name() == "title")
                {
                    in_title = false;
                }
            }
        }
    }

    let text = pieces.join("\n");
    let text = SPACES.replace_all(&text, " ");
    let text = BLANK_RUNS.replace_all(&text, "\n\n");
    PageText {
        title: title.unwrap_or_default(),
        text: text.trim().to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn drops_non_content_and_keeps_the_title() {
        let page = html_to_text(
            "<html><head><title> The  Title </title><style>p{}</style>\
             <script>var x = 1;</script></head><body><h1>Heading</h1>\
             <p>First &amp; second</p><form><input value=x>Form text</form>\
             <svg><title>icon</title><text>vector</text></svg><p>Last</p></body></html>",
        );
        assert_eq!(page.title, "The  Title");
        assert!(page.text.contains("Heading"));
        assert!(page.text.contains("First & second"));
        for dropped in ["var x", "p{}", "Form text", "icon", "vector"] {
            assert!(
                !page.text.contains(dropped),
                "{dropped:?} leaked into {:?}",
                page.text
            );
        }
        assert!(page.text.ends_with("Last"));
    }

    #[test]
    fn collapses_spaces_and_blank_runs_but_keeps_paragraph_breaks() {
        let page = html_to_text("<p>a \t  b</p>\n\n   \n<p>c</p><p>d</p>");
        assert_eq!(page.text, "a b\n\nc\nd");
    }

    #[test]
    fn a_title_split_by_markup_is_joined_without_separators() {
        let page = html_to_text("<title>A &amp; B</title><p>x</p>");
        assert_eq!(page.title, "A & B");
    }

    #[test]
    fn a_page_without_a_title_has_an_empty_one() {
        assert_eq!(html_to_text("<p>only text</p>").title, "");
    }

    #[test]
    fn deep_nesting_does_not_overflow_the_stack() {
        let html = "<div>".repeat(20_000) + "deep" + &"</div>".repeat(20_000);
        assert_eq!(html_to_text(&html).text, "deep");
    }

    #[test]
    fn comments_and_the_doctype_are_not_text() {
        let page = html_to_text("<!DOCTYPE html><!-- note --><p>body</p>");
        assert_eq!(page.text, "body");
    }
}
