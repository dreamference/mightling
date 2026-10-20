//! QR codes for pairing a device (specs/DREAMFERENCE_MIGHTLING_ASK.md §4.3), drawn here: as SVG for
//! the Devices page and as text for `ling web pair` in a terminal. No QR service is ever asked; the
//! encoder is `qrcodegen`, a dependency-free crate.
//!
//! A code encodes `http://<address>:<port>/pair?code=<eight digits>`: where the device reaches this
//! server, and a pairing code that works once, for ten minutes. Nothing longer-lived goes in it:
//! never the owner token, a session or a device cookie.

use std::net::Ipv4Addr;

use qrcodegen::QrCode;
use qrcodegen::QrCodeEcc;

/// The light border every reader expects around a code, in modules.
const QUIET_ZONE: i32 = 4;

/// The link a code carries for a server on `name:port` and a pairing code.
pub fn pairing_link(name: &str, port: u16, code: &str) -> String {
    let host = if name.contains(':') && !name.starts_with('[') { format!("[{name}]") } else { name.to_string() };
    format!("http://{host}:{port}/pair?code={code}")
}

/// How likely another device reaches this machine by `name`, lower first: a home or office LAN
/// address, then a Tailscale address (100.64.0.0/10), then 172.16.0.0/12 (where container bridges
/// such as Docker's usually are), then other addresses, then names (`.local` needs mDNS on the
/// device, which phones do not all have, and does not cross Tailscale).
pub fn reachability(name: &str) -> u8 {
    match name.parse::<Ipv4Addr>() {
        Ok(ip) => {
            let [a, b, ..] = ip.octets();
            if (a == 192 && b == 168) || a == 10 {
                0
            } else if a == 100 && (64..128).contains(&b) {
                1
            } else if a == 172 && (16..32).contains(&b) {
                3
            } else {
                2
            }
        }
        Err(_) => 4,
    }
}

/// The names to offer a device, most likely to work first.
pub fn ordered(names: &[String]) -> Vec<String> {
    let mut names = names.to_vec();
    names.sort_by_key(|name| reachability(name));
    names
}

/// Whether `name` is most likely a Tailscale address, to say so beside it.
pub fn is_tailscale(name: &str) -> bool {
    reachability(name) == 1
}

fn encode(text: &str) -> Option<QrCode> {
    QrCode::encode_text(text, QrCodeEcc::Medium).ok()
}

/// The code as an SVG image, dark modules on a white ground whatever the page's theme, so every
/// camera reads it.
pub fn svg(text: &str) -> Option<String> {
    let qr = encode(text)?;
    let size = qr.size();
    let full = size + 2 * QUIET_ZONE;
    let mut path = String::new();
    for y in 0..size {
        for x in 0..size {
            if qr.get_module(x, y) {
                path.push_str(&format!("M{},{}h1v1h-1z", x + QUIET_ZONE, y + QUIET_ZONE));
            }
        }
    }
    Some(format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {full} {full}\" shape-rendering=\"crispEdges\" role=\"img\" aria-label=\"QR code\">\
<rect width=\"{full}\" height=\"{full}\" fill=\"#ffffff\"/><path d=\"{path}\" fill=\"#000000\"/></svg>"
    ))
}

/// The code as text for a terminal: two rows of modules per line in half blocks, black on white
/// set explicitly, so it reads on a dark terminal and a light one alike.
pub fn terminal(text: &str) -> Option<String> {
    let qr = encode(text)?;
    let size = qr.size();
    let dark = |x: i32, y: i32| x >= 0 && y >= 0 && x < size && y < size && qr.get_module(x, y);
    let mut out = String::new();
    let mut y = -QUIET_ZONE;
    while y < size + QUIET_ZONE {
        out.push_str("\x1b[30;47m");
        for x in -QUIET_ZONE..size + QUIET_ZONE {
            out.push(match (dark(x, y), dark(x, y + 1)) {
                (true, true) => '█',
                (true, false) => '▀',
                (false, true) => '▄',
                (false, false) => ' ',
            });
        }
        out.push_str("\x1b[0m\n");
        y += 2;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_link_names_the_pairing_page_and_the_code() {
        assert_eq!(pairing_link("192.168.50.105", 3100, "01234567"), "http://192.168.50.105:3100/pair?code=01234567");
        assert_eq!(pairing_link("fe80::1", 3100, "01234567"), "http://[fe80::1]:3100/pair?code=01234567");
    }

    #[test]
    fn lan_addresses_come_first_and_container_bridges_late() {
        let names: Vec<String> =
            ["spark.local", "172.17.0.1", "100.101.102.103", "192.168.50.105", "spark"].iter().map(|n| n.to_string()).collect();
        assert_eq!(ordered(&names), ["192.168.50.105", "100.101.102.103", "172.17.0.1", "spark.local", "spark"]);
        assert!(is_tailscale("100.101.102.103") && !is_tailscale("100.1.2.3"));
    }

    #[test]
    fn the_svg_is_a_whole_code_on_white() {
        let image = svg("http://192.168.50.105:3100/pair?code=01234567").unwrap();
        assert!(image.starts_with("<svg") && image.ends_with("</svg>"));
        assert!(image.contains("fill=\"#ffffff\"") && image.contains("M4,4h1v1h-1z"), "the finder pattern starts inside the quiet zone");
        // The same text, the same image: nothing random goes in.
        assert_eq!(image, svg("http://192.168.50.105:3100/pair?code=01234567").unwrap());
    }

    #[test]
    fn the_terminal_code_is_square_in_modules() {
        let text = "http://192.168.50.105:3100/pair?code=01234567";
        let qr = encode(text).unwrap();
        let drawn = terminal(text).unwrap();
        let lines: Vec<&str> = drawn.lines().collect();
        let width = (qr.size() + 2 * QUIET_ZONE) as usize;
        assert_eq!(lines.len(), width.div_ceil(2));
        for line in lines {
            assert_eq!(line.trim_start_matches("\x1b[30;47m").trim_end_matches("\x1b[0m").chars().count(), width);
        }
    }
}
