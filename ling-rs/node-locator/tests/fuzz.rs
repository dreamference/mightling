//! Seeded randomised tests for the node file's reader and the NetBird status scanner: whatever
//! the text, nothing panics, and a node written by `Node::render` reads back as itself. The
//! comparison with a real JSON parser is in `ling-web-rs/tests/fuzz_node_locator.rs`, which has
//! one. `MIGHTLING_FUZZ_CASES` raises the number of cases per seed for a long hunt.

use ling_node_locator::Node;
use ling_node_locator::management_connected;
use ling_node_locator::url_host;

const SEEDS: [u64; 4] = [1, 0x9e37_79b9_7f4a_7c15, 0xdead_beef, 20_261_029];

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }

    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }

    fn pick<'a>(&mut self, items: &[&'a str]) -> &'a str {
        items[self.below(items.len())]
    }
}

fn cases(default: usize) -> usize {
    std::env::var("MIGHTLING_FUZZ_CASES")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(default)
}

fn each_case(default: usize, mut check: impl FnMut(&mut Rng)) {
    for seed in SEEDS {
        let mut rng = Rng(seed);
        for _ in 0..cases(default) {
            check(&mut rng);
        }
    }
}

/// Text made of the pieces a JSON scanner trips over.
fn noise(rng: &mut Rng) -> String {
    const PIECES: &[&str] = &[
        "{",
        "}",
        "[",
        "]",
        "\"",
        "\\",
        ":",
        ",",
        " ",
        "\n",
        "\r\n",
        "\t",
        "\u{feff}",
        "\u{a0}",
        "null",
        "true",
        "8000",
        "-1",
        "0",
        "70000",
        "\\u",
        "\\u00e9",
        "\\ud83d",
        "\\ude00",
        "\\n",
        "\\\"",
        "\"address\"",
        "\"model_port\"",
        "\"management\"",
        "\"connected\"",
        "é",
        "😀",
        "\u{0}",
        "a",
        "x",
    ];
    (0..rng.below(24)).map(|_| rng.pick(PIECES)).collect()
}

/// A string with quotes, escapes, control characters and characters outside the BMP.
fn text(rng: &mut Rng) -> String {
    const PIECES: &[&str] = &[
        "a", "Z", "0", " ", "\"", "\\", "/", "\n", "\r", "\t", "\u{8}", "\u{c}", "\u{1}", "\u{1f}",
        "\u{7f}", "é", "ß", "€", "😀", "\u{feff}", ":", ",", "{", "}", "[", "]", "null", "\\u0041",
    ];
    (0..rng.below(8)).map(|_| rng.pick(PIECES)).collect()
}

fn port(rng: &mut Rng) -> u16 {
    1 + rng.below(65_535) as u16
}

fn node(rng: &mut Rng) -> Option<Node> {
    let address = text(rng).trim().to_string();
    if address.is_empty() {
        return None;
    }
    let remote = text(rng);
    Some(Node {
        node: text(rng),
        name: text(rng),
        address,
        model_port: port(rng),
        web_port: (rng.below(2) == 0).then(|| port(rng)),
        search_port: (rng.below(2) == 0).then(|| port(rng)),
        version: text(rng),
        last_seen: text(rng),
        remote,
    })
}

#[test]
fn no_text_makes_the_readers_panic() {
    each_case(3_000, |rng| {
        let text = noise(rng);
        let _ = Node::parse(&text);
        let _ = management_connected(&text);
        let _ = url_host(&text);
        if let Some(node) = Node::parse(&text) {
            assert!(!node.address.is_empty(), "{text:?}");
            let _ = (node.model_url(), node.web_url(), node.search_url());
        }
    });
}

#[test]
fn every_rendered_node_reads_back_as_itself() {
    each_case(3_000, |rng| {
        let Some(node) = node(rng) else { return };
        let text = node.render();
        assert_eq!(Node::parse(&text), Some(node), "{text}");
    });
}

#[test]
fn a_rendered_node_survives_windows_line_ends_and_a_byte_order_mark_is_refused_not_misread() {
    each_case(1_000, |rng| {
        let Some(node) = node(rng) else { return };
        let crlf = node.render().replace('\n', "\r\n");
        assert_eq!(Node::parse(&crlf), Some(node.clone()), "{crlf}");
        // A byte order mark is not JSON; the reader may refuse the file, never read it wrongly.
        let bom = format!("\u{feff}{}", node.render());
        assert!(Node::parse(&bom).is_none_or(|read| read == node), "{bom}");
    });
}
