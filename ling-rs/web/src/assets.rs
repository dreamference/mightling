//! What `ling web` serves from its own binary: the Mightling UI build (embedded by `build.rs`
//! from `LING_WEB_UI_DIST`), the browser's bridge script, and the few pages shown before signing in.

include!(concat!(env!("OUT_DIR"), "/ui_assets.rs"));

pub const BRIDGE_JS: &str = include_str!("../static/bridge.js");
pub const PAIR_CSS: &str = include_str!("../static/pair.css");
pub const LOCKED_HTML: &str = include_str!("../static/locked.html");
pub const LOGIN_FAILED_HTML: &str = include_str!("../static/login-failed.html");
const PAIR_HTML: &str = include_str!("../static/pair.html");
const PLACEHOLDER_HTML: &str = include_str!("../static/placeholder.html");

/// The tag that gives the page `window.electronBridge`, placed before the UI's own scripts.
pub const BRIDGE_TAG: &str = r#"<script src="/bridge.js"></script>"#;

pub fn ui_asset(path: &str) -> Option<&'static [u8]> {
    UI_ASSETS.iter().find(|(name, _)| *name == path).map(|(_, bytes)| *bytes)
}

/// The UI's `index.html` with the bridge script first in `<head>`, or the placeholder when this
/// binary was built without the UI.
pub fn index_html() -> String {
    match ui_asset("index.html") {
        Some(bytes) => with_bridge(&String::from_utf8_lossy(bytes)),
        None => PLACEHOLDER_HTML.to_string(),
    }
}

/// Puts the bridge tag right after `<head>`, so it runs before any module script.
pub fn with_bridge(page: &str) -> String {
    let lower = page.to_ascii_lowercase();
    match lower.find("<head") {
        Some(start) => {
            let end = lower[start..].find('>').map_or(page.len(), |offset| start + offset + 1);
            format!("{}\n    {BRIDGE_TAG}{}", &page[..end], &page[end..])
        }
        None => format!("{BRIDGE_TAG}\n{page}"),
    }
}

/// The pairing page, with a message above the form when there is one.
pub fn pair_html(message: Option<&str>) -> String {
    pair_html_with(message, None)
}

/// The pairing page with the code field filled in, as the Devices page's QR code links to it.
pub fn pair_html_with(message: Option<&str>, code: Option<&str>) -> String {
    let message = message.map(|text| format!("<p class=\"error\">{}</p>", escape(text))).unwrap_or_default();
    PAIR_HTML.replace("<!--MESSAGE-->", &message).replace("<!--CODE-->", &escape(code.unwrap_or_default()))
}

fn escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

pub fn content_type(path: &str) -> &'static str {
    match path.rsplit_once('.').map(|(_, extension)| extension.to_ascii_lowercase()).as_deref() {
        Some("js" | "mjs") => "text/javascript; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("html") => "text/html; charset=utf-8",
        Some("json" | "map") => "application/json",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("webp") => "image/webp",
        Some("ico") => "image/x-icon",
        Some("woff2") => "font/woff2",
        Some("woff") => "font/woff",
        Some("ttf") => "font/ttf",
        Some("wasm") => "application/wasm",
        Some("txt") => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_bridge_goes_first_in_head() {
        let page = "<!doctype html>\n<html><HEAD lang=x>\n<script type=\"module\" src=\"./assets/a.js\"></script></head></html>";
        let served = with_bridge(page);
        let bridge = served.find(BRIDGE_TAG).unwrap();
        assert!(bridge < served.find("./assets/a.js").unwrap());
        assert!(served.starts_with("<!doctype html>\n<html><HEAD lang=x>"));
    }

    #[test]
    fn the_pair_page_escapes_its_message() {
        assert!(pair_html(Some("<b>")).contains("&lt;b&gt;"));
        assert!(!pair_html(None).contains("<!--MESSAGE-->"));
        assert!(!pair_html(None).contains("<!--CODE-->"));
        assert!(pair_html_with(None, Some("01234567")).contains("value=\"01234567\""));
    }
}
