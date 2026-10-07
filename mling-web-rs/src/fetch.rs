//! `mling-fetch`: retrieve a web page and return its readable text.

use std::io::Read;
use std::sync::LazyLock;

use encoding_rs::Encoding;
use regex::bytes::Regex;
use serde::Serialize;

use crate::agent;
use crate::html_text::html_to_text;
use crate::take_chars;

/// Hard ceiling on what a single fetch pulls down, before any text extraction. A model cannot use
/// more than this anyway, and without it one link to a large binary stalls the whole session.
pub const MAX_DOWNLOAD_BYTES: u64 = 5 * 1024 * 1024;

/// What `mling-fetch` returns unless asked for more: a page's worth, not a book's.
pub const DEFAULT_MAX_CHARS: i64 = 8_000;

/// The most a caller can ask for.
pub const MAX_CHARS_CEILING: i64 = 100_000;

/// A fetched page, in the field order `WebTools.fetch` returns.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct FetchedPage {
    pub url: String,
    pub final_url: String,
    pub status: u16,
    pub content_type: String,
    pub title: String,
    pub text: String,
    pub truncated: bool,
}

/// Checks that `url` is an ordinary web address, with Python's `urlparse` wording.
///
/// Anything but http and https is refused, so a `file://` argument cannot turn a web tool into a
/// local-file reader.
pub fn require_http_url(url: &str) -> Result<(), String> {
    let scheme = url
        .split_once(':')
        .map(|(scheme, _)| scheme)
        .filter(|scheme| {
            scheme.starts_with(|c: char| c.is_ascii_alphabetic())
                && scheme
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c))
        })
        .unwrap_or_default()
        .to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        let named = if scheme.is_empty() {
            "no"
        } else {
            scheme.as_str()
        };
        return Err(format!(
            "Only http and https URLs are supported, got {named} scheme"
        ));
    }
    // The authority as `urlparse` finds it: the `url` crate would read `http:///path` as host
    // `path`, where Python (and any reader of the URL) sees no host at all.
    let rest = &url[scheme.len() + 1..];
    let netloc = rest
        .strip_prefix("//")
        .map(|rest| rest.split(['/', '?', '#']).next().unwrap_or_default())
        .unwrap_or_default();
    if netloc.is_empty() {
        return Err("URL has no host".to_string());
    }
    Ok(())
}

/// Retrieves a page and returns its readable text, or the error the agent should see.
pub fn fetch(url: &str, max_chars: i64) -> Result<FetchedPage, String> {
    require_http_url(url)?;
    let proxy = crate::proxy_for(url, |name| std::env::var(name).ok());
    let response = match agent(proxy).get(url).set("Accept-Language", "en").call() {
        Ok(response) => response,
        Err(ureq::Error::Status(status, response)) => {
            return Err(format!(
                "request failed: HTTP {status} {} for url: {}",
                response.status_text(),
                response.get_url()
            ));
        }
        Err(error) => return Err(format!("request failed: {error}")),
    };
    let final_url = response.get_url().to_string();
    let status = response.status();
    let content_type = response
        .header("Content-Type")
        .unwrap_or_default()
        .to_string();
    let mut body = Vec::new();
    // Streamed through the cap, so it holds while the body arrives rather than after it has
    // already been buffered.
    response
        .into_reader()
        .take(MAX_DOWNLOAD_BYTES)
        .read_to_end(&mut body)
        .map_err(|error| format!("request failed: {error}"))?;
    Ok(page(url, final_url, status, content_type, &body, max_chars))
}

/// Builds the page from a response's parts; separate from the request so it can be tested alone.
pub fn page(
    url: &str,
    final_url: String,
    status: u16,
    content_type: String,
    body: &[u8],
    max_chars: i64,
) -> FetchedPage {
    let is_html = content_type.to_lowercase().contains("html");
    let decoded = decode(body, &content_type, is_html);
    let (title, text) = if is_html {
        let page = html_to_text(&decoded);
        (page.title, page.text)
    } else {
        // Plain text, JSON, source files: handed back as they are rather than run through a markup
        // parser.
        (String::new(), decoded)
    };
    let limit = max_chars.clamp(1, MAX_CHARS_CEILING) as usize;
    let kept = take_chars(&text, limit);
    FetchedPage {
        url: url.to_string(),
        final_url,
        status,
        content_type,
        title,
        truncated: kept.len() < text.len(),
        text: kept.to_string(),
    }
}

static META_CHARSET: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?i)<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9_:.+-]+)"#).unwrap()
});

/// Decodes a body by the charset its `Content-Type` names, else (for HTML) the one its `<meta>`
/// declares in the first 1024 bytes, else UTF-8. A byte-order mark overrides all three.
///
/// This differs from the Python version on purpose: `requests` decodes any `text/*` response that
/// names no charset as ISO-8859-1, which turns every such UTF-8 page into mojibake, and never
/// reads the `<meta>` declaration.
pub fn decode(body: &[u8], content_type: &str, is_html: bool) -> String {
    let declared = content_type.split(';').skip(1).find_map(|parameter| {
        let (name, value) = parameter.split_once('=')?;
        (name.trim().eq_ignore_ascii_case("charset")).then(|| {
            value
                .trim()
                .trim_matches(|c| c == '"' || c == '\'')
                .to_string()
        })
    });
    let sniffed = || {
        let head = &body[..body.len().min(1024)];
        let label = META_CHARSET.captures(head)?.get(1)?.as_bytes().to_vec();
        String::from_utf8(label).ok()
    };
    let encoding = declared
        .or_else(|| if is_html { sniffed() } else { None })
        .and_then(|label| Encoding::for_label(label.as_bytes()))
        .unwrap_or(encoding_rs::UTF_8);
    let (text, _, _) = encoding.decode(body);
    text.into_owned()
}

/// The text `mling-fetch` prints for a page.
pub fn render_text(page: &FetchedPage, max_chars: i64) -> String {
    let mut out = String::new();
    if !page.title.is_empty() {
        out.push_str(&format!("# {}\n\n", page.title));
    }
    out.push_str(&page.text);
    out.push('\n');
    if page.truncated {
        out.push_str(&format!("\n[truncated at {max_chars} chars]\n"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_http_and_https_are_accepted() {
        assert!(require_http_url("https://example.com/a?b").is_ok());
        assert!(require_http_url("HTTP://example.com").is_ok());
        assert_eq!(
            require_http_url("file:///etc/passwd").unwrap_err(),
            "Only http and https URLs are supported, got file scheme"
        );
        assert_eq!(
            require_http_url("example.com").unwrap_err(),
            "Only http and https URLs are supported, got no scheme"
        );
        assert_eq!(
            require_http_url("http:///path").unwrap_err(),
            "URL has no host"
        );
    }

    #[test]
    fn html_is_reduced_to_text_and_other_types_are_kept_as_they_are() {
        let html = page(
            "u",
            "f".into(),
            200,
            "text/html; charset=utf-8".into(),
            b"<title>T</title><p>Body</p>",
            100,
        );
        assert_eq!((html.title.as_str(), html.text.as_str()), ("T", "T\nBody"));
        let json = page(
            "u",
            "f".into(),
            200,
            "application/json".into(),
            b"{\"a\": 1}",
            100,
        );
        assert_eq!(
            (json.title.as_str(), json.text.as_str()),
            ("", "{\"a\": 1}")
        );
    }

    #[test]
    fn text_is_cut_at_a_character_count_and_marked() {
        let long = page(
            "u",
            "f".into(),
            200,
            "text/plain".into(),
            "é".repeat(10).as_bytes(),
            4,
        );
        assert_eq!(long.text, "éééé");
        assert!(long.truncated);
        let short = page("u", "f".into(), 200, "text/plain".into(), b"abc", 4);
        assert!(!short.truncated);
        let clamped = page("u", "f".into(), 200, "text/plain".into(), b"abc", -1);
        assert_eq!(clamped.text, "a");
    }

    #[test]
    fn the_charset_comes_from_the_header_then_the_meta_then_utf8() {
        let latin1 = [0x63, 0x61, 0x66, 0xe9];
        assert_eq!(
            decode(&latin1, "text/html; charset=ISO-8859-1", true),
            "café"
        );
        let with_meta = [b"<meta charset=\"windows-1252\">caf".as_slice(), &[0xe9]].concat();
        assert!(decode(&with_meta, "text/html", true).ends_with("café"));
        assert_eq!(decode("café".as_bytes(), "text/html", true), "café");
        assert_eq!(decode("café".as_bytes(), "text/plain", false), "café");
    }

    #[test]
    fn rendering_matches_the_python_command() {
        let page = FetchedPage {
            url: "u".into(),
            final_url: "f".into(),
            status: 200,
            content_type: "text/html".into(),
            title: "T".into(),
            text: "Body".into(),
            truncated: true,
        };
        assert_eq!(
            render_text(&page, 4),
            "# T\n\nBody\n\n[truncated at 4 chars]\n"
        );
    }
}
