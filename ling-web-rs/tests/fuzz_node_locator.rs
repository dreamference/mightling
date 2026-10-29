//! Seeded randomised tests that hold the node locator's hand-written JSON readers to a real JSON
//! parser: the node file's reader agrees with `serde_json` on every file it accepts, and the
//! NetBird status scanner never says the management is connected when NetBird does not.
//! `MIGHTLING_FUZZ_CASES` raises the number of cases per seed for a long hunt.

use ling_web::node_locator::DEFAULT_MODEL_PORT;
use ling_web::node_locator::Node;
use ling_web::node_locator::management_connected;
use serde_json::Value;
use serde_json::json;

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

    fn chance(&mut self, one_in: usize) -> bool {
        self.below(one_in) == 0
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

/// JSON's own whitespace, sometimes none.
fn space(rng: &mut Rng) -> &'static str {
    ["", "", " ", "\n", "\r\n  ", "\t"][rng.below(6)]
}

/// A string's characters, each written raw or escaped in one of the ways JSON allows.
fn json_string(rng: &mut Rng, text: &str) -> String {
    let mut out = String::from('"');
    for c in text.chars() {
        let short = match c {
            '"' => Some("\\\""),
            '\\' => Some("\\\\"),
            '\n' => Some("\\n"),
            '\r' => Some("\\r"),
            '\t' => Some("\\t"),
            '\u{8}' => Some("\\b"),
            '\u{c}' => Some("\\f"),
            '/' => Some("\\/"),
            _ => None,
        };
        let must_escape = (c as u32) < 0x20 || c == '"' || c == '\\';
        if must_escape || rng.chance(3) {
            match short {
                Some(escape) if rng.chance(2) => out.push_str(escape),
                _ => {
                    let mut units = [0u16; 2];
                    for unit in c.encode_utf16(&mut units) {
                        out.push_str(&if rng.chance(2) {
                            format!("\\u{unit:04x}")
                        } else {
                            format!("\\u{unit:04X}")
                        });
                    }
                }
            }
        } else {
            out.push(c);
        }
    }
    out.push('"');
    out
}

fn text(rng: &mut Rng) -> String {
    const PIECES: &[&str] = &[
        "a", "Z", "0", " ", "\"", "\\", "/", "\n", "\r", "\t", "\u{8}", "\u{c}", "\u{1}", "é", "€",
        "😀", "𝄞", ":", ",", "{", "}", "[", "]", "null",
    ];
    (0..rng.below(8)).map(|_| rng.pick(PIECES)).collect()
}

const KEYS: &[&str] = &[
    "node",
    "name",
    "address",
    "model_port",
    "web_port",
    "search_port",
    "version",
    "last_seen",
    "remote",
    "other",
];

/// A flat object as JSON text, with keys in any order, repeated keys, every kind of scalar and
/// always an address.
fn flat_object(rng: &mut Rng) -> String {
    let mut members: Vec<(String, String)> = Vec::new();
    for _ in 0..rng.below(10) {
        let key = rng.pick(KEYS).to_string();
        let value = match rng.below(6) {
            0 => rng.below(70_000).to_string(),
            1 => ["true", "false", "null", "-1", "0"][rng.below(5)].to_string(),
            _ => {
                let text = text(rng);
                json_string(rng, &text)
            }
        };
        members.push((key, value));
    }
    let address = format!("a{}", text(rng));
    let at = rng.below(members.len() + 1);
    members.insert(at, ("address".to_string(), json_string(rng, &address)));
    let body: Vec<String> = members
        .iter()
        .map(|(key, value)| {
            format!(
                "{}{}{}:{}{}{}",
                space(rng),
                json_string(rng, key),
                space(rng),
                space(rng),
                value,
                space(rng)
            )
        })
        .collect();
    format!("{}{{{}}}{}", space(rng), body.join(","), space(rng))
}

/// What `Node::parse` must read, from the object a real JSON parser read.
fn expected(value: &Value) -> Option<Node> {
    let object = value.as_object()?;
    if object
        .values()
        .any(|value| value.is_object() || value.is_array())
    {
        return None;
    }
    let get = |key: &str| {
        object.get(key).map(|value| match value {
            Value::String(text) => text.clone(),
            Value::Null => String::new(),
            other => other.to_string(),
        })
    };
    let port = |key: &str| {
        get(key)
            .and_then(|value| value.parse::<u16>().ok())
            .filter(|port| *port != 0)
    };
    let address = get("address")?.trim().to_string();
    if address.is_empty() {
        return None;
    }
    Some(Node {
        node: get("node").unwrap_or_default(),
        name: get("name").unwrap_or_default(),
        address,
        model_port: port("model_port").unwrap_or(DEFAULT_MODEL_PORT),
        web_port: port("web_port"),
        search_port: port("search_port"),
        version: get("version").unwrap_or_default(),
        last_seen: get("last_seen").unwrap_or_default(),
        remote: get("remote").unwrap_or_default(),
    })
}

#[test]
fn the_node_file_reader_reads_every_flat_object_as_a_json_parser_does() {
    each_case(3_000, |rng| {
        let text = flat_object(rng);
        let real: Value = serde_json::from_str(&text).expect("the generator writes JSON");
        assert_eq!(Node::parse(&text), expected(&real), "{text}");
    });
}

#[test]
fn what_the_node_file_reader_accepts_a_json_parser_reads_the_same() {
    const PIECES: &[&str] = &[
        "{",
        "}",
        "\"",
        "\\",
        ":",
        ",",
        " ",
        "\n",
        "null",
        "true",
        "8000",
        "0",
        "\\u00e9",
        "\\ud83d\\ude00",
        "\\b",
        "\\\"",
        "\"address\"",
        "\"model_port\"",
        "\"name\"",
        "\"a\"",
        "é",
        "x",
    ];
    each_case(5_000, |rng| {
        let text: String = (0..rng.below(20)).map(|_| rng.pick(PIECES)).collect();
        if let (Some(hand), Ok(real)) = (Node::parse(&text), serde_json::from_str::<Value>(&text)) {
            assert_eq!(Some(hand), expected(&real), "{text:?}");
        }
    });
}

/// A value for the parts of a status the scanner must look past: strings that look like the
/// keys it searches for, counts, nulls and small objects.
fn decoy(rng: &mut Rng, depth: usize) -> Value {
    const STRINGS: &[&str] = &[
        "",
        "}",
        "{",
        "\"management\": {\"connected\": true}",
        "connected",
        "\"connected\"",
        "true",
        "https://api.netbird.io:443",
        "x\\",
    ];
    match rng.below(if depth == 0 { 4 } else { 6 }) {
        0 => json!(rng.pick(STRINGS)),
        1 => json!(rng.below(5)),
        2 => json!(rng.chance(2)),
        3 => Value::Null,
        4 => {
            let mut object = serde_json::Map::new();
            for _ in 0..rng.below(4) {
                let key = rng.pick(&["connected", "url", "error", "fqdn", "total", "management"]);
                object.insert(key.to_string(), decoy(rng, depth - 1));
            }
            Value::Object(object)
        }
        _ => Value::Array((0..rng.below(3)).map(|_| decoy(rng, depth - 1)).collect()),
    }
}

fn decoy_string(rng: &mut Rng) -> Value {
    match decoy(rng, 0) {
        Value::String(text) => Value::String(text),
        other => Value::String(other.to_string()),
    }
}

/// `netbird status --json`'s shape: `management` and `signal` objects of url, connected and
/// error; peers with a count named `connected`.
fn status(rng: &mut Rng) -> Value {
    let service = |rng: &mut Rng| {
        let mut object = serde_json::Map::new();
        for key in ["url", "connected", "error"] {
            if rng.chance(5) {
                continue;
            }
            let value = match key {
                "connected" if rng.chance(4) => decoy(rng, 0),
                "connected" => json!(rng.chance(2)),
                _ => json!(text(rng)),
            };
            object.insert(key.to_string(), value);
        }
        Value::Object(object)
    };
    let mut top = serde_json::Map::new();
    top.insert(
        "peers".to_string(),
        json!({"total": rng.below(4), "connected": rng.below(4), "details": []}),
    );
    match rng.below(4) {
        0 => {}
        1 => {
            top.insert("management".to_string(), Value::Null);
        }
        _ => {
            top.insert("management".to_string(), service(rng));
        }
    }
    top.insert("signal".to_string(), service(rng));
    top.insert("fqdn".to_string(), json!(text(rng)));
    // Events carry metadata, a map of strings whose keys may be any word, `management` too.
    let events: Vec<Value> = (0..rng.below(3))
        .map(|_| {
            let mut metadata = serde_json::Map::new();
            for _ in 0..rng.below(3) {
                let key = rng.pick(&["management", "connected", "peer", "url"]);
                metadata.insert(key.to_string(), decoy_string(rng));
            }
            json!({"category": rng.pick(&["management", "dns", "network"]),
                   "message": decoy_string(rng), "metadata": metadata})
        })
        .collect();
    top.insert("events".to_string(), Value::Array(events));
    Value::Object(top)
}

/// JSON text for `value` with its members in a random order and random whitespace.
fn write_shuffled(rng: &mut Rng, value: &Value) -> String {
    match value {
        Value::Object(object) => {
            let mut members: Vec<(&String, &Value)> = object.iter().collect();
            for i in (1..members.len()).rev() {
                members.swap(i, rng.below(i + 1));
            }
            let body: Vec<String> = members
                .into_iter()
                .map(|(key, value)| {
                    format!(
                        "{}{}{}:{}{}",
                        space(rng),
                        // Keys as NetBird writes them, unescaped: the scanner looks for them.
                        Value::from(key.as_str()),
                        space(rng),
                        space(rng),
                        write_shuffled(rng, value)
                    )
                })
                .collect();
            format!("{{{}{}}}", body.join(","), space(rng))
        }
        Value::Array(items) => {
            let body: Vec<String> = items.iter().map(|item| write_shuffled(rng, item)).collect();
            format!("[{}]", body.join(","))
        }
        Value::String(text) => json_string(rng, text),
        other => other.to_string(),
    }
}

fn really_connected(value: &Value) -> bool {
    value
        .get("management")
        .and_then(|management| management.get("connected"))
        == Some(&Value::Bool(true))
}

/// Whether the management object holds a `}` inside a string, where the scanner, which ends
/// the object at the first `}`, may stop early and say not connected.
fn brace_in_management(value: &Value) -> bool {
    value
        .get("management")
        .and_then(Value::as_object)
        .is_some_and(|object| object.values().any(|value| value.to_string().contains('}')))
}

/// JSON text for a status in the order NetBird writes it: the top-level members in its struct
/// order, `management` and `signal` as url, connected, error. What is inside the other members
/// is shuffled.
fn write_as_netbird(rng: &mut Rng, value: &Value) -> String {
    const TOP: &[&str] = &["peers", "management", "signal", "fqdn", "events"];
    const SERVICE: &[&str] = &["url", "connected", "error"];
    let ordered = |rng: &mut Rng,
                   object: &serde_json::Map<String, Value>,
                   order: &[&str],
                   inner: &dyn Fn(&mut Rng, &str, &Value) -> String| {
        let body: Vec<String> = order
            .iter()
            .filter_map(|key| object.get(*key).map(|value| (key, value)))
            .map(|(key, value)| {
                format!(
                    "{}\"{key}\"{}:{}{}",
                    space(rng),
                    space(rng),
                    space(rng),
                    inner(rng, key, value)
                )
            })
            .collect();
        format!("{{{}{}}}", body.join(","), space(rng))
    };
    let service = |rng: &mut Rng, key: &str, value: &Value| match (key, value) {
        ("management" | "signal", Value::Object(object)) => {
            ordered(rng, object, SERVICE, &|rng, _, value| {
                write_shuffled(rng, value)
            })
        }
        _ => write_shuffled(rng, value),
    };
    let Value::Object(object) = value else {
        unreachable!("a status is an object")
    };
    ordered(rng, object, TOP, &service)
}

#[test]
fn the_management_is_read_as_netbird_writes_it() {
    each_case(3_000, |rng| {
        let value = status(rng);
        let text = write_as_netbird(rng, &value);
        let real: Value = serde_json::from_str(&text).expect("the writer writes JSON");
        assert_eq!(real, value, "{text}");
        // A `}` inside a management string ends the scanner's object early: it may then say not
        // connected, the safe side, and never says connected wrongly (checked below).
        if !brace_in_management(&real) {
            assert_eq!(
                management_connected(&text),
                really_connected(&real),
                "{text}"
            );
        }
    });
}

#[test]
fn the_management_is_never_read_as_connected_unless_netbird_says_so() {
    // In any member order, nested or not: the scanner may miss a connection, never invent one.
    each_case(3_000, |rng| {
        let value = status(rng);
        let text = write_shuffled(rng, &value);
        let real: Value = serde_json::from_str(&text).expect("the writer writes JSON");
        if management_connected(&text) {
            assert!(really_connected(&real), "read as connected: {text}");
        }
    });
}
