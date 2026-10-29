//! Seeded randomised tests for `SKILL.md` frontmatter: the block a writer closed is the block that
//! is split off, no text makes the reader panic, and when the YAML does not parse, the line-by-line
//! repair reads `name`, `description` and `disable-model-invocation` as YAML reads those same lines.
//! `MIGHTLING_FUZZ_CASES` raises the number of cases per seed for a long hunt.

use ling_skills::frontmatter::parse;
use ling_skills::frontmatter::split;
use serde_yaml::Value;

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

/// Text made of what a frontmatter reader trips over.
fn noise(rng: &mut Rng) -> String {
    const PIECES: &[&str] = &[
        "---",
        "--- ",
        "\n",
        "\r\n",
        "\u{feff}",
        " ",
        "\t",
        ":",
        ": ",
        "name",
        "description",
        "metadata",
        "disable-model-invocation",
        "true",
        "- ",
        "[",
        "]",
        "{",
        "}",
        "'",
        "\"",
        "#",
        "|",
        ">",
        "&a",
        "*a",
        "!",
        "é",
        "😀",
        "\u{0}",
        "x",
    ];
    (0..rng.below(30)).map(|_| rng.pick(PIECES)).collect()
}

/// Words a skill's name or description is made of, YAML's special characters among them.
fn words(rng: &mut Rng) -> String {
    const PIECES: &[&str] = &[
        "Read", "PDF", "files", " ", "  ", "when", ":", ": ", " #", "#", "'", "\"", "\\", "é", "😀", "-", "- ", "[x]",
        "{y}", "&", "*", "!", "%", "@", "`", "|", ">", "true", "1.5", "null", "~", ",", "?",
    ];
    (0..1 + rng.below(8)).map(|_| rng.pick(PIECES)).collect()
}

/// A value written the ways authors write one: plain, double-quoted, single-quoted, with a comment.
fn written(rng: &mut Rng, text: &str) -> String {
    match rng.below(5) {
        0 => format!("\"{}\"", text.replace('\\', "\\\\").replace('"', "\\\"")),
        1 => format!("'{}'", text.replace('\'', "''")),
        2 => format!("{text} # {}", words(rng)),
        _ => text.to_string(),
    }
}

/// What YAML reads from `key: value` on its own line, as the reader turns a scalar into text; `None`
/// when that line is not YAML or its value is not a scalar.
fn yaml_reads(key: &str, value: &str) -> Option<Option<String>> {
    let root: Value = serde_yaml::from_str(&format!("{key}: {value}\n")).ok()?;
    match root.get(key)? {
        Value::String(text) => Some(Some(text.trim().to_string()).filter(|text| !text.is_empty())),
        Value::Number(number) => Some(Some(number.to_string())),
        Value::Bool(flag) => Some(Some(flag.to_string())),
        Value::Null => Some(None),
        _ => None,
    }
}

#[test]
fn the_block_a_writer_closed_is_the_block_split_off() {
    each_case(3_000, |rng| {
        let newline = rng.pick(&["\n", "\r\n"]);
        let lines: Vec<String> = (0..rng.below(6))
            .map(|_| format!("{}{newline}", noise(rng).replace(['\n', '\r'], "")))
            .filter(|line| line.trim_end() != "---")
            .collect();
        let yaml = lines.concat();
        let body = noise(rng);
        let bom = rng.pick(&["", "\u{feff}"]);
        let closing = rng.pick(&["---", "---  ", "---\t"]);
        let text = format!("{bom}---{newline}{yaml}{closing}{newline}{body}");
        assert_eq!(split(&text), Some((yaml.as_str(), body.as_str())), "{text:?}");
    });
}

#[test]
fn no_text_makes_the_reader_panic() {
    each_case(3_000, |rng| {
        let text = format!("{}{}", rng.pick(&["", "---\n", "\u{feff}---\r\n"]), noise(rng));
        if let Ok(parsed) = parse(&text) {
            assert!(!parsed.description.trim().is_empty(), "{text:?}");
        }
    });
}

#[test]
fn the_repair_reads_each_line_as_yaml_reads_it() {
    const FLAGS: &[&str] = &[
        "true",
        "True",
        "TRUE",
        "false",
        "\"true\"",
        "'true'",
        "true # manual",
        "true  ",
        "yes",
        "on",
        "1",
    ];
    let mut repaired = 0;
    each_case(3_000, |rng| {
        let name = rng.pick(&["pdf", "mcp-builder", "notes", "a1"]).to_string();
        let name = written(rng, &name);
        let words = words(rng);
        let description = written(rng, &words);
        let flag = rng.pick(FLAGS);
        let mut lines = vec![format!("name: {name}"), format!("description: {description}")];
        if rng.below(2) == 0 {
            lines.push(format!("disable-model-invocation: {flag}"));
        }
        if rng.below(2) == 0 {
            lines.push("license: Apache-2.0".to_string());
        }
        // What makes the whole block refuse YAML: an unquoted colon or a stray list item.
        let broken = rng.pick(&["compatibility: needs: a shell", "- stray", "metadata: {"]);
        lines.insert(rng.below(lines.len() + 1), broken.to_string());
        let text = format!("---\n{}\n---\nBody\n", lines.join("\n"));
        if serde_yaml::from_str::<Value>(split(&text).map_or("", |(yaml, _)| yaml)).is_ok() {
            return;
        }
        repaired += 1;
        // A line that is not YAML on its own, or reads as nothing, is taken verbatim.
        let Some(Some(read_description)) = yaml_reads("description", &description) else {
            return;
        };
        let parsed = parse(&text).unwrap_or_else(|error| panic!("{text:?}: {error}"));
        assert_eq!(parsed.description, read_description, "{text:?}");
        if let Some(Some(read_name)) = yaml_reads("name", &name) {
            assert_eq!(parsed.name, Some(read_name), "{text:?}");
        }
        let manual = lines.iter().any(|line| line.starts_with("disable-model-invocation"))
            && yaml_reads("disable-model-invocation", flag).flatten().as_deref() == Some("true");
        assert!(
            !manual || parsed.manual_only,
            "{text:?}: its author kept it from the model"
        );
    });
    assert!(repaired > 0, "no case reached the repair");
}
