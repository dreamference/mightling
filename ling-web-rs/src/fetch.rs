//! `ling-fetch`: retrieve a web page and return its readable text.

use std::io::Read;
use std::sync::LazyLock;

use encoding_rs::Encoding;
use regex::bytes::Regex;
use serde::Serialize;

use crate::html_text::html_to_text;
use crate::share_links::{self, Walked};
use crate::take_chars;

/// Hard ceiling on what a single fetch pulls down, before any text extraction. A model cannot use
/// more than this anyway, and without it one link to a large binary stalls the whole session.
pub const MAX_DOWNLOAD_BYTES: u64 = 5 * 1024 * 1024;

/// What `ling-fetch` returns unless asked for more: a page's worth, not a book's.
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

/// The error the agent sees for a failed request.
pub fn request_error(error: ureq::Error) -> String {
    match error {
        ureq::Error::Status(status, response) => format!(
            "request failed: HTTP {status} {} for url: {}",
            response.status_text(),
            response.get_url()
        ),
        error => format!("request failed: {error}"),
    }
}

/// Retrieves a page and returns its readable text, or the error the agent should see.
///
/// A Google share link is resolved to the search or place it opens, without requesting Google's
/// page (`share_links`); where its redirects lead anywhere else, that page is read as any other.
pub fn fetch(url: &str, max_chars: i64) -> Result<FetchedPage, String> {
    require_http_url(url)?;
    let proxy = crate::proxy_for(url, |name| std::env::var(name).ok())?;
    // Redirects are followed by the walk, which refuses a `Location` ureq would panic on.
    let classify: fn(&str) -> Option<share_links::Target> = if share_links::is_share_link(url) {
        share_links::classify
    } else {
        |_: &str| None
    };
    let response = match share_links::walk(&crate::agent_with_redirects(proxy, 0), url, classify)? {
        Walked::Target {
            final_url,
            status,
            target,
        } => {
            return Ok(share_links::page(url, final_url, status, &target));
        }
        Walked::Response(response) => response,
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
        .take(MAX_DOWNLOAD_BYTES + 1)
        .read_to_end(&mut body)
        .map_err(|error| format!("request failed: {error}"))?;
    // A page longer than the cap is read up to it, and says that it was cut.
    let cut = body.len() as u64 > MAX_DOWNLOAD_BYTES;
    body.truncate(MAX_DOWNLOAD_BYTES as usize);
    if is_binary(&body, &content_type) {
        return Err(format!("not text: {content_type}, {} bytes", body.len()));
    }
    let mut page = page(url, final_url, status, content_type, &body, max_chars);
    page.truncated |= cut;
    Ok(page)
}

/// What `ling-fetch --download` saved (one JSON line on stdout).
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Downloaded {
    pub final_url: String,
    pub status: u16,
    pub content_type: String,
    pub bytes: u64,
}

/// The default and the highest `--max-bytes` of a download.
pub const DOWNLOAD_DEFAULT_BYTES: u64 = 64 * 1024 * 1024;
pub const DOWNLOAD_CEILING_BYTES: u64 = 256 * 1024 * 1024;

/// Downloads a PDF into `dest` for `ling-docs`' `pdf_search` (specs/DREAMFERENCE_PDF_SEARCH.md
/// §3.3.1): the same request as a fetch (agent, proxy rules, redirects, timeouts), the body kept
/// only when it is a PDF and within `max_bytes`. `ling-docs` opens no socket; this is its download.
pub fn download_pdf(
    url: &str,
    dest: &std::path::Path,
    max_bytes: u64,
) -> Result<Downloaded, String> {
    require_http_url(url)?;
    let proxy = crate::proxy_for(url, |name| std::env::var(name).ok())?;
    let response = match share_links::walk_with(
        &crate::agent_with_redirects(proxy, 0),
        url,
        &[("Accept", "application/pdf,*/*;q=0.8")],
        |_| None,
    )? {
        Walked::Response(response) => response,
        Walked::Target { .. } => unreachable!("nothing is classified as a target"),
    };
    let final_url = response.get_url().to_string();
    let status = response.status();
    let content_type = response
        .header("Content-Type")
        .unwrap_or_default()
        .to_string();
    let bytes = save_pdf(
        response.into_reader(),
        dest,
        max_bytes.clamp(1, DOWNLOAD_CEILING_BYTES),
        &content_type,
    )?;
    Ok(Downloaded {
        final_url,
        status,
        content_type,
        bytes,
    })
}

/// Streams `body` into `dest` (created new, mode 600 on Unix) if its first KiB holds `%PDF-`,
/// stopping at `max_bytes`. Nothing is left behind on a refusal.
pub fn save_pdf(
    body: impl Read,
    dest: &std::path::Path,
    max_bytes: u64,
    content_type: &str,
) -> Result<u64, String> {
    let mut body = body.take(max_bytes + 1);
    let mut head = Vec::with_capacity(1024);
    (&mut body)
        .take(1024)
        .read_to_end(&mut head)
        .map_err(|e| format!("request failed: {e}"))?;
    if !head.windows(5).any(|w| w == b"%PDF-") {
        let named = if content_type.is_empty() {
            "no type".to_string()
        } else {
            content_type.to_string()
        };
        return Err(format!(
            "not a PDF (no %PDF- header; the server sent {named})"
        ));
    }
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(dest)
        .map_err(|e| format!("cannot write {}: {e}", dest.display()))?;
    let result = (|| -> Result<u64, String> {
        use std::io::Write;
        file.write_all(&head).map_err(|e| e.to_string())?;
        let copied =
            std::io::copy(&mut body, &mut file).map_err(|e| format!("request failed: {e}"))?;
        let total = head.len() as u64 + copied;
        if total > max_bytes {
            return Err(format!(
                "larger than {} MiB: not downloaded",
                max_bytes >> 20
            ));
        }
        Ok(total)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(dest);
    }
    result
}

/// Whether a body is not text: it holds a NUL byte, which no text does except UTF-16, which says
/// so with a byte-order mark or its `Content-Type`. Printed, it would be thousands of replacement
/// characters.
fn is_binary(body: &[u8], content_type: &str) -> bool {
    body.contains(&0)
        && !body.starts_with(&[0xff, 0xfe])
        && !body.starts_with(&[0xfe, 0xff])
        && !content_type.to_ascii_lowercase().contains("utf-16")
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
    // A header charset no encoding knows (`charset=`, a typo) leaves the meta in charge.
    let known = |label: String| Encoding::for_label(label.as_bytes());
    let encoding = declared
        .and_then(known)
        .or_else(|| {
            // A `<meta>` that could be read in ASCII is not UTF-16, so, as the HTML standard
            // says, it means UTF-8 (`output_encoding`).
            if is_html {
                sniffed().and_then(known).map(Encoding::output_encoding)
            } else {
                None
            }
        })
        .unwrap_or(encoding_rs::UTF_8);
    let (text, _, _) = encoding.decode(body);
    text.into_owned()
}

/// The text `ling-fetch` prints for a page.
pub fn render_text(page: &FetchedPage, max_chars: i64) -> String {
    let mut out = String::new();
    if !page.title.is_empty() {
        out.push_str(&format!("# {}\n\n", page.title));
    }
    out.push_str(&page.text);
    out.push('\n');
    if page.truncated {
        // The limit `page` cut at, not the one asked for.
        let limit = max_chars.clamp(1, MAX_CHARS_CEILING);
        if page.text.chars().count() < limit as usize {
            // Fewer characters than the limit: the page itself was cut at the size cap.
            out.push_str(&format!(
                "\n[truncated: the page is larger than {MAX_DOWNLOAD_BYTES} bytes]\n"
            ));
        } else {
            out.push_str(&format!("\n[truncated at {limit} chars]\n"));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_download_is_kept_only_when_it_is_a_pdf_within_the_cap() {
        let dir = std::env::temp_dir().join(format!("ling-fetch-save-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let pdf = [b"%PDF-1.7\n".as_slice(), &vec![b'x'; 5000]].concat();
        assert_eq!(
            save_pdf(
                pdf.as_slice(),
                &dir.join("a.pdf"),
                1 << 20,
                "application/octet-stream"
            ),
            Ok(pdf.len() as u64)
        );
        assert_eq!(std::fs::read(dir.join("a.pdf")).unwrap(), pdf);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(dir.join("a.pdf"))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
        let html = save_pdf(
            b"<html>sorry</html>".as_slice(),
            &dir.join("b.pdf"),
            1 << 20,
            "text/html",
        )
        .unwrap_err();
        assert!(
            html.contains("not a PDF") && html.contains("text/html"),
            "{html}"
        );
        assert!(!dir.join("b.pdf").exists());
        let big =
            save_pdf(pdf.as_slice(), &dir.join("c.pdf"), 2048, "application/pdf").unwrap_err();
        assert!(big.contains("larger than"), "{big}");
        assert!(!dir.join("c.pdf").exists(), "a partial file is removed");
        assert!(
            save_pdf(pdf.as_slice(), &dir.join("a.pdf"), 1 << 20, "").is_err(),
            "never overwrites"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

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
    fn text_before_a_colon_that_cannot_be_a_scheme_is_no_scheme() {
        // As `urlparse`: a scheme is a letter, then letters, digits, `+`, `-` or `.`.
        for url in ["example.com/a:b", "1http://example.com"] {
            assert_eq!(
                require_http_url(url).unwrap_err(),
                "Only http and https URLs are supported, got no scheme",
                "{url}"
            );
        }
    }

    #[test]
    fn a_url_without_an_authority_has_no_host() {
        for url in ["http:example.com", "https://?q=1", "http://#top"] {
            assert_eq!(
                require_http_url(url).unwrap_err(),
                "URL has no host",
                "{url}"
            );
        }
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
    fn a_header_charset_no_encoding_knows_leaves_the_meta_in_charge() {
        let with_meta = [b"<meta charset=\"windows-1252\">caf".as_slice(), &[0xe9]].concat();
        for header in ["text/html; charset=", "text/html; charset=bogus-8"] {
            assert!(
                decode(&with_meta, header, true).ends_with("café"),
                "{header}"
            );
        }
    }

    #[test]
    fn a_meta_naming_utf16_is_read_as_utf8() {
        // An ASCII `<meta>` cannot be UTF-16, so the HTML standard reads such a page as UTF-8.
        let page = "<meta charset=\"utf-16\"><p>café</p>";
        assert_eq!(decode(page.as_bytes(), "text/html", true), page);
    }

    #[test]
    fn a_byte_order_mark_overrides_the_header_and_the_meta() {
        let utf16 = [0xff, 0xfe, b'h', 0, 0xe9, 0];
        assert_eq!(decode(&utf16, "text/html; charset=ISO-8859-1", true), "hé");
        let utf8 = [
            b"\xef\xbb\xbf<meta charset=\"latin1\">".as_slice(),
            "café".as_bytes(),
        ]
        .concat();
        assert!(decode(&utf8, "text/plain; charset=latin1", true).ends_with("café"));
    }

    #[test]
    fn an_unknown_or_late_charset_falls_back_to_utf8() {
        assert_eq!(
            decode(
                "café".as_bytes(),
                "text/html; charset=no-such-charset",
                true
            ),
            "café"
        );
        let late = [
            " ".repeat(1024).as_bytes(),
            b"<meta charset=\"windows-1252\">caf",
            &[0xe9],
        ]
        .concat();
        assert!(decode(&late, "text/html", true).ends_with("caf\u{fffd}"));
        let not_html = [b"<meta charset=\"windows-1252\">caf".as_slice(), &[0xe9]].concat();
        assert!(decode(&not_html, "text/plain", false).ends_with("caf\u{fffd}"));
    }

    #[test]
    fn a_pdf_header_after_the_first_kib_is_not_a_pdf() {
        let dir = std::env::temp_dir().join(format!("ling-fetch-late-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let late = [vec![b' '; 1024].as_slice(), b"%PDF-1.7\n"].concat();
        let error = save_pdf(late.as_slice(), &dir.join("a.pdf"), 1 << 20, "").unwrap_err();
        assert!(error.contains("no type"), "{error}");
        assert!(!dir.join("a.pdf").exists());
        let at_the_end_of_the_kib = [vec![b' '; 1019].as_slice(), b"%PDF-"].concat();
        assert_eq!(
            save_pdf(
                at_the_end_of_the_kib.as_slice(),
                &dir.join("b.pdf"),
                1 << 20,
                ""
            ),
            Ok(1024)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_download_of_exactly_the_cap_is_kept() {
        let dir = std::env::temp_dir().join(format!("ling-fetch-cap-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let pdf = [b"%PDF-1.7\n".as_slice(), &vec![b'x'; 3000]].concat();
        let cap = pdf.len() as u64;
        assert_eq!(
            save_pdf(pdf.as_slice(), &dir.join("a.pdf"), cap, ""),
            Ok(cap)
        );
        assert!(save_pdf(pdf.as_slice(), &dir.join("b.pdf"), cap - 1, "").is_err());
        assert!(!dir.join("b.pdf").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn a_download_never_writes_through_a_symlink() {
        let dir = std::env::temp_dir().join(format!("ling-fetch-link-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("victim"), b"keep").unwrap();
        std::os::unix::fs::symlink(dir.join("victim"), dir.join("a.pdf")).unwrap();
        std::os::unix::fs::symlink(dir.join("absent"), dir.join("b.pdf")).unwrap();
        let pdf = b"%PDF-1.7\nbody".as_slice();
        assert!(save_pdf(pdf, &dir.join("a.pdf"), 1 << 20, "").is_err());
        assert!(save_pdf(pdf, &dir.join("b.pdf"), 1 << 20, "").is_err());
        assert_eq!(std::fs::read(dir.join("victim")).unwrap(), b"keep");
        assert!(
            !dir.join("absent").exists(),
            "a dangling link is not followed"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_body_that_breaks_off_leaves_nothing_behind() {
        struct BreaksOff(Vec<u8>);
        impl Read for BreaksOff {
            fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
                if self.0.is_empty() {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::ConnectionReset,
                        "reset",
                    ));
                }
                let n = buf.len().min(self.0.len());
                buf[..n].copy_from_slice(&self.0[..n]);
                self.0.drain(..n);
                Ok(n)
            }
        }
        let dir = std::env::temp_dir().join(format!("ling-fetch-reset-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let body = BreaksOff([b"%PDF-1.7\n".as_slice(), &vec![b'x'; 4096]].concat());
        let error = save_pdf(body, &dir.join("a.pdf"), 1 << 20, "").unwrap_err();
        assert!(error.contains("request failed"), "{error}");
        assert!(!dir.join("a.pdf").exists());
        let _ = std::fs::remove_dir_all(&dir);
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

    #[test]
    fn the_truncation_line_names_the_limit_the_text_was_cut_at() {
        let body = "x".repeat(MAX_CHARS_CEILING as usize + 10);
        for (asked, kept) in [(0, 1), (-5, 1), (MAX_CHARS_CEILING + 1, MAX_CHARS_CEILING)] {
            let page = page(
                "u",
                "u".into(),
                200,
                "text/plain".into(),
                body.as_bytes(),
                asked,
            );
            assert_eq!(page.text.chars().count() as i64, kept);
            assert!(
                render_text(&page, asked).ends_with(&format!("[truncated at {kept} chars]\n")),
                "asked {asked}"
            );
        }
    }
}
