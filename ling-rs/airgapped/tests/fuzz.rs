//! Seeded randomised tests for the air-gap level's resolution: the TOML reader finds the
//! top-level key exactly where a TOML parser does, whatever comes before it, and the resolver
//! keeps its order of tiers, the strictest configuration file winning. The TOML texts are built
//! with their answer known, so no TOML parser is needed here. `MIGHTLING_FUZZ_CASES` raises the
//! number of cases per seed for a long hunt.

use std::path::PathBuf;

use ling_airgapped::{Level, Source, config_value, resolve_from, resolve_guarded, toml_value};

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

/// String content with what a line reader trips over: header lines, a decoy key, comments,
/// quotes, backslashes and characters outside ASCII.
fn content(rng: &mut Rng) -> String {
    const PIECES: &[&str] = &[
        "a",
        " ",
        "\t",
        "\n",
        "#",
        "[x]",
        "[[x]]",
        "]",
        "=",
        "{",
        "mightling_airgapped = \"off\"",
        "mightling_airgapped = 'off'",
        "\"",
        "'",
        "\\",
        "\\n",
        "é",
        "😀",
        "\u{1}",
        "\u{7f}",
    ];
    (0..rng.below(6)).map(|_| rng.pick(PIECES)).collect()
}

/// A basic string's escape for `c`, or `c` itself, chosen at random where both are allowed.
fn basic_char(rng: &mut Rng, c: char, out: &mut String) {
    match c {
        '"' => out.push_str("\\\""),
        '\\' => out.push_str("\\\\"),
        '\n' => out.push_str("\\n"),
        '\t' if rng.chance(2) => out.push_str("\\t"),
        c if (c < ' ' && c != '\t') || c == '\u{7f}' || rng.chance(6) => {
            if (c as u32) < 0x1_0000 && rng.chance(2) {
                out.push_str(&format!("\\u{:04x}", c as u32));
            } else {
                out.push_str(&format!("\\U{:08X}", c as u32));
            }
        }
        c => out.push(c),
    }
}

/// `text` as a TOML string of one of the four kinds a TOML parser reads back as `text`.
fn toml_string(rng: &mut Rng, text: &str) -> String {
    let control = text
        .chars()
        .any(|c| (c < ' ' && c != '\t' && c != '\n') || c == '\u{7f}');
    match rng.below(4) {
        0 if !control && !text.contains(['\'', '\n']) => format!("'{text}'"),
        1 if !control && !text.contains('\'') => {
            // A line end right after the opening quotes is dropped, so one is added when the
            // text starts with its own.
            let open = if text.starts_with('\n') || rng.chance(2) {
                "\n"
            } else {
                ""
            };
            format!("'''{open}{text}'''")
        }
        2 => {
            let mut out = String::from("\"\"\"");
            if text.starts_with('\n') || rng.chance(2) {
                out.push('\n');
            }
            let mut chars = text.chars().peekable();
            while let Some(c) = chars.next() {
                match c {
                    '\n' => out.push('\n'),
                    c => basic_char(rng, c, &mut out),
                }
                // A backslash ending a line is dropped with the whitespace after it, so it goes
                // only where no whitespace of the text follows.
                if !chars.peek().is_some_and(|c| [' ', '\t', '\n'].contains(c)) && rng.chance(8) {
                    out.push_str(rng.pick(&["\\\n", "\\  \n\t  ", "\\\n\n  "]));
                }
            }
            out.push_str("\"\"\"");
            out
        }
        _ => {
            let mut out = String::from("\"");
            for c in text.chars() {
                basic_char(rng, c, &mut out);
            }
            out.push('"');
            out
        }
    }
}

/// A line break inside an array, with or without a comment.
fn gap(rng: &mut Rng) -> &'static str {
    rng.pick(&["", " ", "\n", "\n  ", " # a ] [x]\n  ", "\r\n"])
}

/// Any value a configuration file may hold before the key, nested to `depth`.
fn value(rng: &mut Rng, depth: usize) -> String {
    match rng.below(if depth == 0 { 4 } else { 6 }) {
        0 => rng
            .pick(&[
                "1",
                "-7",
                "0x1f",
                "1_000",
                "3.5",
                "-1e3",
                "inf",
                "nan",
                "true",
                "false",
                "1979-05-27",
                "07:32:00",
                "1979-05-27T07:32:00Z",
                "1979-05-27 07:32:00",
                "1979-05-27 07:32:00.5+01:00",
            ])
            .to_string(),
        1 | 2 => {
            let text = content(rng);
            toml_string(rng, &text)
        }
        3 if depth == 0 => {
            let text = content(rng);
            toml_string(rng, &text)
        }
        3 | 4 => {
            let items: Vec<String> = (0..rng.below(4))
                .map(|_| format!("{}{}", gap(rng), value(rng, depth - 1)))
                .collect();
            let comma = if !items.is_empty() && rng.chance(2) {
                ","
            } else {
                ""
            };
            format!("[{}{comma}{}]", items.join(","), gap(rng))
        }
        _ => {
            let mut used = Vec::new();
            let members: Vec<String> = (0..rng.below(3))
                .filter_map(|_| {
                    let key = key(rng);
                    fresh(&mut used, &key).then(|| format!("{key} = {}", value(rng, depth - 1)))
                })
                .collect();
            format!("{{ {} }}", members.join(", "))
        }
    }
}

/// A key other than the air-gap one at the top level: bare, quoted, dotted, or the air-gap name
/// under another.
fn key(rng: &mut Rng) -> String {
    rng.pick(&[
        "model",
        "x-y",
        "_1",
        "\"a b\"",
        "'lit'",
        "\"mightling_airgapped \"",
        "mightling_airgapped_other",
        "night.mightling_airgapped",
        "night . \"mightling_airgapped\"",
        "\"m\\u006fdel_2\"",
    ])
    .to_string()
}

/// Whether `key` is new among `used`, noting it: a key is set once, and a dotted one makes its
/// first part a table.
fn fresh(used: &mut Vec<String>, key: &str) -> bool {
    let first = key.split('.').next().unwrap_or("").trim().to_string();
    if used.iter().any(|seen| seen == key || *seen == first) {
        return false;
    }
    used.push(key.to_string());
    used.push(first);
    true
}

/// Lines a file may hold between statements.
fn filler(rng: &mut Rng) -> &'static str {
    rng.pick(&[
        "",
        "\n",
        "# mightling_airgapped = \"off\"\n",
        "# [x]\n",
        "   \t\n",
        "\r\n",
    ])
}

/// Statements with distinct keys none of which is the air-gap one.
fn statements(rng: &mut Rng, used: &mut Vec<String>, out: &mut String) {
    for _ in 0..rng.below(5) {
        let key = key(rng);
        if !fresh(used, &key) {
            continue;
        }
        out.push_str(filler(rng));
        let space = rng.pick(&[" ", "", "\t"]);
        let value = value(rng, 2);
        out.push_str(&format!("{key}{space}={space}{value}"));
        out.push_str(rng.pick(&["\n", " # mightling_airgapped = \"off\"\n", "\r\n", "  \n"]));
    }
}

/// A TOML text and the value of its top-level air-gap key, as a TOML parser reads it (a
/// boolean as its level).
fn config(rng: &mut Rng) -> (String, Option<String>) {
    let mut text = String::new();
    if rng.chance(8) {
        text.push('\u{feff}');
    }
    let mut used = Vec::new();
    statements(rng, &mut used, &mut text);
    let mut expected = None;
    if !rng.chance(4) {
        let name = rng.pick(&[
            "mightling_airgapped",
            "\"mightling_airgapped\"",
            "'mightling_airgapped'",
            "\"\\u006dightling_airgapped\"",
        ]);
        let space = rng.pick(&[" ", "", "\t"]);
        let (written, read) = match rng.below(8) {
            0 => {
                let flag = rng.chance(2);
                (
                    flag.to_string(),
                    if flag { "on" } else { "off" }.to_string(),
                )
            }
            _ => {
                let level = rng
                    .pick(&["on", "off", "ON", " on ", "On\n", "nope", "", "duckduckgo"])
                    .to_string();
                (toml_string(rng, &level), level)
            }
        };
        text.push_str(filler(rng));
        text.push_str(&format!("{name}{space}={space}{written}"));
        text.push_str(rng.pick(&["\n", " # was \"off\"\n", "\r\n"]));
        expected = Some(read);
        if rng.chance(2) {
            statements(rng, &mut used, &mut text);
        }
    }
    if rng.chance(2) {
        text.push_str(rng.pick(&["[later]\n", "[[x]]\n", "  [ later ]\n"]));
        text.push_str("mightling_airgapped = \"off\"\n");
        statements(rng, &mut Vec::new(), &mut text);
    }
    if text.ends_with('\n') && !text.ends_with("\r\n") && rng.chance(4) {
        text.pop();
    }
    (text, expected)
}

#[test]
fn the_key_is_read_where_a_toml_parser_reads_it() {
    each_case(3_000, |rng| {
        let (text, expected) = config(rng);
        assert_eq!(toml_value(&text), expected, "{text:?}");
    });
}

#[test]
fn no_text_makes_the_toml_reader_panic() {
    const PIECES: &[&str] = &[
        "mightling_airgapped",
        "\"mightling_airgapped\"",
        " = ",
        "=",
        "\"",
        "\"\"\"",
        "'",
        "'''",
        "\\",
        "\\u",
        "\\U0001F600",
        "\\ud800",
        "[",
        "]",
        "{",
        "}",
        ",",
        ".",
        "#",
        "\n",
        "\r",
        " ",
        "on",
        "1979-05-27 ",
        "é",
        "\u{feff}",
    ];
    each_case(5_000, |rng| {
        let text: String = (0..rng.below(16)).map(|_| rng.pick(PIECES)).collect();
        let _ = toml_value(&text);
    });
}

/// A tier's raw value: valid in any case and spacing, or not a level.
fn tier(rng: &mut Rng) -> Option<String> {
    (!rng.chance(3)).then(|| {
        rng.pick(&["on", "off", "ON", " Off ", "nope", "", "duckduckgo", "true"])
            .to_string()
    })
}

fn configs(rng: &mut Rng) -> Vec<(PathBuf, String)> {
    (0..rng.below(4))
        .map(|i| {
            let text = match tier(rng) {
                Some(value) => format!("model = \"x\"\nmightling_airgapped = \"{value}\"\n"),
                None => "[night]\nmightling_airgapped = \"on\"\n".to_string(),
            };
            (PathBuf::from(format!("c{i}.toml")), text)
        })
        .collect()
}

#[test]
fn the_first_tier_wins_among_files_the_strictest_and_an_invalid_value_is_on() {
    each_case(3_000, |rng| {
        let (session, environment, configs) = (tier(rng), tier(rng), configs(rng));
        let resolved = resolve_from(session.as_deref(), environment.as_deref(), &configs);

        // What cannot be read counts as on (AIRGAPPED §6.5), named once, in the order read.
        let mut invalid = Vec::new();
        let mut level = |value: &str| {
            Level::parse(value).unwrap_or_else(|| {
                invalid.push(value.trim().to_string());
                Level::On
            })
        };
        let expected = if let Some(value) = session.as_deref() {
            (level(value), Source::Session)
        } else if let Some(value) = environment
            .as_deref()
            .filter(|value| !value.trim().is_empty())
        {
            (level(value), Source::Environment)
        } else {
            let mut strictest = (Level::Off, Source::Default);
            for (path, text) in &configs {
                let Some(value) = config_value(text).expect("the fuzzed files are TOML") else {
                    continue;
                };
                let found = level(&value);
                if strictest.1 == Source::Default || found > strictest.0 {
                    strictest = (found, Source::ConfigFile(path.clone()));
                }
            }
            strictest
        };
        let context = format!("{session:?} {environment:?} {configs:?}");
        assert_eq!((resolved.level, resolved.source), expected, "{context}");
        let named: Vec<&str> = resolved
            .invalid
            .iter()
            .filter_map(|note| note.strip_prefix('"')?.split_once("\" from "))
            .map(|(value, _)| value)
            .collect();
        assert_eq!(named, invalid, "{context}");
    });
}

#[test]
fn a_seal_holds_on_and_an_exposed_session_never_loosens_the_files() {
    each_case(3_000, |rng| {
        let (session, environment, configs) = (tier(rng), tier(rng), configs(rng));
        let (sealed, exposed) = (rng.chance(4), rng.chance(2));
        let guarded = resolve_guarded(
            session.as_deref(),
            environment.as_deref(),
            &configs,
            sealed,
            exposed,
        );
        let plain = resolve_from(session.as_deref(), environment.as_deref(), &configs);
        let configured = resolve_from(None, environment.as_deref(), &configs);
        let context = format!("{session:?} {environment:?} {configs:?} {sealed} {exposed}");
        assert!(
            guarded.level >= plain.level,
            "the guard loosened: {context}"
        );
        if sealed {
            assert_eq!(
                (guarded.level, guarded.source),
                (Level::On, Source::Sealed),
                "{context}"
            );
        } else if exposed {
            assert!(guarded.level >= configured.level, "{context}");
        } else {
            assert_eq!(guarded, plain, "{context}");
        }
    });
}
