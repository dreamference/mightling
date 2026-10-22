//! The Devices page (specs/DREAMFERENCE_MIGHTLING_ASK.md §4.3, "Settings → Devices"): paired
//! devices with a Revoke button, and a pairing code with a QR code a phone scans to open the
//! pairing page on this server, prefilled.
//!
//! Only this machine's own session may use it: a session comes from `ling web open`, whose link is
//! for loopback, so a paired device cannot mint more devices or revoke others. What the QR code
//! carries is the pairing link (qr.rs): the server's address and a code that works once, for ten
//! minutes. Served by the server itself, with no script: the forms post back to it, so the page
//! needs nothing beyond the CSP every page has.

use crate::auth::Device;
use crate::qr;

const STYLE: &str = r#"<style>
main { max-width: 560px; }
.qr { background: #ffffff; padding: 8px; border-radius: 8px; width: min(320px, 100%); margin: 0 0 0.75rem; }
.qr svg { display: block; width: 100%; height: auto; }
.code { font-size: 2rem; font-weight: 700; letter-spacing: 0.15em; font-variant-numeric: tabular-nums; margin: 0 0 0.25rem; color: var(--fg); }
.link { word-break: break-all; font-size: 0.9rem; }
ul { list-style: none; padding: 0; margin: 0 0 1.5rem; }
li { display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0; border-bottom: 1px solid var(--field); }
li span { flex: 1; min-width: 0; }
li small { display: block; color: var(--muted); }
li form button { width: auto; padding: 0.4rem 0.8rem; background: var(--field); color: var(--fg); }
details { margin: 0 0 1.5rem; }
details .qr { width: min(220px, 100%); }
section { margin: 0 0 2rem; }
h2 { font-size: 1.1rem; margin: 0 0 0.5rem; }
a { color: var(--accent); }
</style>"#;

fn escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

fn page(title: &str, body: &str) -> String {
    format!(
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n\
<title>{}</title>\n<link rel=\"stylesheet\" href=\"/pair.css\">\n{STYLE}\n</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n",
        escape(title)
    )
}

/// How long ago a time was, for people.
pub fn ago(seconds: u64, now: u64) -> String {
    let elapsed = now.saturating_sub(seconds);
    match elapsed {
        0..=59 => "just now".to_string(),
        60..=3599 => format!("{} min ago", elapsed / 60),
        3600..=86_399 => format!("{} h ago", elapsed / 3600),
        _ => format!("{} days ago", elapsed / 86_400),
    }
}

/// A pairing code just issued, and where devices reach this server.
pub struct Pairing<'a> {
    pub code: &'a str,
    pub port: u16,
    pub lan_names: &'a [String],
}

fn pairing_section(pairing: &Pairing) -> String {
    let code = pairing.code;
    let spaced = format!("{} {}", &code[..code.len().min(4)], &code[code.len().min(4)..]);
    let names = qr::ordered(pairing.lan_names);
    let Some((first, others)) = names.split_first() else {
        return format!("<section><h2>Pairing code</h2><p class=\"code\">{}</p></section>", escape(&spaced));
    };
    let label = |name: &str| {
        if qr::is_tailscale(name) { format!("{} (Tailscale)", escape(name)) } else { escape(name) }
    };
    let link = qr::pairing_link(first, pairing.port, code);
    let image = qr::svg(&link).unwrap_or_default();
    let mut html = format!(
        "<section><h2>Scan this with the other device</h2>\
<div class=\"qr\">{image}</div>\
<p class=\"code\">{}</p>\
<p>Or open <span class=\"link\">{}</span> on it and type the code. It works once, for ten minutes, for one device; the device stays paired until you revoke it here.</p>\
<p>This code is for {}.</p>",
        escape(&spaced),
        escape(&link.replace(&format!("?code={code}"), "")),
        label(first)
    );
    if !others.is_empty() {
        html.push_str("<details><summary>The other device is on another network? Other addresses of this machine</summary>");
        for name in others {
            let link = qr::pairing_link(name, pairing.port, code);
            html.push_str(&format!(
                "<p>{}</p><div class=\"qr\">{}</div>",
                label(name),
                qr::svg(&link).unwrap_or_default()
            ));
        }
        html.push_str("</details>");
    }
    html.push_str("</section>");
    html
}

/// The page for this machine's own session.
pub fn owner_page(devices: &[Device], lan_names: &[String], pairing: Option<&Pairing>, notice: Option<&str>, now: u64) -> String {
    let mut body = String::from("<h1>Devices</h1>\n<p><a href=\"/\">Back to Mightling</a></p>\n");
    if let Some(notice) = notice {
        body.push_str(&format!("<p class=\"error\">{}</p>\n", escape(notice)));
    }
    if lan_names.is_empty() {
        body.push_str(
            "<section><h2>Pair a phone or another device</h2><p>This server answers on loopback only, so no other device can reach it. \
On an advertised node, <code>ling-admin node enable</code> serves the web UI to the local network (and over Tailscale), \
paired devices only; then come back here.</p></section>\n",
        );
    } else if let Some(pairing) = pairing {
        body.push_str(&pairing_section(pairing));
    } else {
        body.push_str(
            "<section><h2>Pair a phone or another device</h2>\
<p>Shows a QR code the other device scans to open its pairing page here, with a code that works once, for ten minutes.</p>\
<form method=\"post\" action=\"/devices/pair\"><button type=\"submit\">Show a pairing code</button></form></section>\n",
        );
    }
    body.push_str("<section><h2>Paired devices</h2>\n");
    if devices.is_empty() {
        body.push_str("<p>None yet.</p>");
    } else {
        body.push_str("<ul>");
        for device in devices {
            // Every pairing may control other devices' threads (SYNC §5.1); one can be narrowed to
            // watching, and widened again.
            let (kind, scope, other, label) = match device.scope {
                crate::auth::Scope::Control => (device.kind, "can control threads", "watch", "Watch only"),
                crate::auth::Scope::Watch => (device.kind, "watches only", "control", "Allow control"),
            };
            let kind = if kind == crate::auth::DeviceKind::Sync { "sync agent, " } else { "" };
            body.push_str(&format!(
                "<li><span>{}<small>{kind}{scope}; paired {}, last used {}</small></span>\
<form method=\"post\" action=\"/devices/scope\"><input type=\"hidden\" name=\"device\" value=\"{}\"><input type=\"hidden\" name=\"scope\" value=\"{other}\"><button type=\"submit\">{label}</button></form>\
<form method=\"post\" action=\"/devices/revoke\"><input type=\"hidden\" name=\"device\" value=\"{}\"><button type=\"submit\">Revoke</button></form></li>",
                escape(&device.name),
                ago(device.created, now),
                ago(device.last_used, now),
                escape(&device.id),
                escape(&device.id)
            ));
        }
        body.push_str("</ul>");
    }
    body.push_str("</section>");
    page("Devices", &body)
}

/// What a paired device sees instead: pairing is done from the Mightling machine itself.
pub fn device_page() -> String {
    page(
        "Devices",
        "<h1>Devices</h1>\n<p>Pair another device, or revoke one, on the Mightling machine itself: open this page there \
(<code>ling web open</code>), or run <code>ling web pair</code>.</p>\n<p><a href=\"/\">Back to Mightling</a></p>",
    )
}

/// Whether a code is the pairing kind, the only kind the pairing page may show back.
pub fn is_pairing_code(code: &str) -> bool {
    code.len() == 8 && code.chars().all(|c| c.is_ascii_digit())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn device(name: &str) -> Device {
        Device {
            id: "ab12cd34".to_string(),
            name: name.to_string(),
            token_sha256: "secret-hash".to_string(),
            created: 0,
            last_used: 0,
            kind: crate::auth::DeviceKind::Browser,
            scope: crate::auth::Scope::Control,
        }
    }

    #[test]
    fn the_page_escapes_names_and_never_shows_a_token_hash() {
        let page = owner_page(&[device("<script>")], &["192.168.50.105".to_string()], None, None, 100);
        assert!(page.contains("&lt;script&gt;") && !page.contains("<script>"));
        assert!(!page.contains("secret-hash"));
        assert!(page.contains("action=\"/devices/pair\"") && page.contains("action=\"/devices/revoke\""));
    }

    #[test]
    fn a_loopback_server_offers_no_code() {
        let page = owner_page(&[], &[], None, None, 0);
        assert!(page.contains("loopback only") && !page.contains("/devices/pair"));
    }

    #[test]
    fn a_pairing_shows_the_lan_address_first_and_the_others_folded() {
        let names = vec!["172.17.0.1".to_string(), "100.101.102.103".to_string(), "192.168.50.105".to_string()];
        let pairing = Pairing { code: "01234567", port: 3100, lan_names: &names };
        let page = owner_page(&[], &names, Some(&pairing), None, 0);
        assert!(page.contains("0123 4567"));
        let first = page.find("<svg").unwrap();
        let folded = page.find("<details>").unwrap();
        assert!(first < folded);
        assert!(page.contains("This code is for 192.168.50.105."));
        assert!(page.contains("100.101.102.103 (Tailscale)"));
        assert_eq!(page.matches("<svg").count(), 3);
    }

    #[test]
    fn only_eight_digits_count_as_a_pairing_code() {
        assert!(is_pairing_code("01234567"));
        for code in ["0123456", "012345678", "0123456a", "\"><b>", ""] {
            assert!(!is_pairing_code(code), "{code}");
        }
    }
}
