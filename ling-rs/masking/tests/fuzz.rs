//! Seeded randomised tests for what masking reads from a history: a `call_id` names a file inside
//! the outputs folder and no other id's, a placeholder stays within its bound, a segment's state
//! reads back as it was saved, and a masked output's saved copy is its own text.
//! `MIGHTLING_FUZZ_CASES` raises the number of cases per seed for a long hunt.

use std::collections::BTreeSet;
use std::collections::HashMap;
use std::path::Path;
use std::path::PathBuf;

use ling_masking::MAX_PLACEHOLDER_CHARS;
use ling_masking::Policy;
use ling_masking::State;
use ling_masking::apply_with;
use ling_masking::copy_path;
use ling_masking::output_text;
use ling_masking::placeholder;
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

/// A folder of its own under the system's temporary folder, removed when dropped.
struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("ling-masking-fuzz-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a scratch folder");
        Self(dir)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// A `call_id` made of what Codex writes and what a path trips over.
fn call_id(rng: &mut Rng) -> String {
    const PIECES: &[&str] = &[
        "call_", "call", "a", "B", "z", "0", "9", "-", "_", ".", "..", "/", "\\", "~", ":", " ", "\0", "\n", "é", "😀",
        "C:", "%2e", "*", "?", "\u{feff}", "fc_", "toolu_01",
    ];
    (0..rng.below(8)).map(|_| rng.pick(PIECES)).collect()
}

fn safe(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '-' || c == '_'
}

/// A tool's output, with the shapes `placeholder` looks for.
fn output(rng: &mut Rng) -> String {
    const PIECES: &[&str] = &[
        "Output:\n",
        "Exit code: 0\n",
        "Process exited with code 1\n",
        "Wall time: 0.1 seconds\n",
        "\n",
        "\r\n",
        "   \n",
        "line",
        "error: x",
        "é",
        "😀",
        "\t",
        "…",
        "\u{0}",
    ];
    let mut text: String = (0..rng.below(16)).map(|_| rng.pick(PIECES)).collect();
    if rng.below(3) == 0 {
        text.push_str(&"x".repeat(rng.below(1_500)));
    }
    text
}

#[test]
fn a_call_id_names_a_file_in_the_outputs_folder_and_no_other_ids() {
    let dir = Path::new("/state/masking");
    for seed in SEEDS {
        let mut rng = Rng(seed);
        let mut seen: HashMap<PathBuf, String> = HashMap::new();
        for _ in 0..cases(3_000) {
            let id = call_id(&mut rng);
            let path = copy_path(dir, &id);
            assert_eq!(path.parent(), Some(dir.join("outputs").as_path()), "{id:?}");
            let name = path.file_name().and_then(|name| name.to_str()).unwrap_or_default();
            let stem = name
                .strip_suffix(".txt")
                .unwrap_or_else(|| panic!("{id:?} gave {name:?}"));
            let changed = id.is_empty() || !id.chars().all(safe);
            if changed {
                let (mangled, hash) = stem.split_once('~').unwrap_or_else(|| panic!("{id:?} gave {stem:?}"));
                assert!(
                    mangled.chars().all(safe) && mangled.chars().count() == id.chars().count(),
                    "{id:?}"
                );
                assert!(
                    hash.len() == 16 && hash.chars().all(|c| c.is_ascii_hexdigit()),
                    "{id:?}"
                );
            } else {
                assert_eq!(stem, id, "a safe id is its own name");
            }
            if let Some(other) = seen.insert(path.clone(), id.clone()) {
                assert_eq!(other, id, "two ids share {path:?}");
            }
        }
    }
}

#[test]
fn a_placeholder_stays_within_its_bound_and_names_its_copy() {
    let path = Path::new("/state/masking/outputs/call_1.txt");
    each_case(3_000, |rng| {
        let text = output(rng);
        let shown = placeholder(&text, path);
        let header = format!(
            "[output moved out of context, {} chars: {}]",
            text.chars().count(),
            path.display()
        );
        assert!(shown.starts_with(&header), "{text:?}");
        assert!(
            shown.chars().count() <= MAX_PLACEHOLDER_CHARS,
            "{text:?} gave {shown:?}"
        );
        assert!(shown.lines().count() <= 3, "{text:?} gave {shown:?}");
    });
}

#[test]
fn a_segments_state_reads_back_as_it_was_saved() {
    let scratch = Scratch::new("state");
    each_case(300, |rng| {
        let key = call_id(rng);
        let state = State {
            masked: (0..rng.below(6)).map(|_| call_id(rng)).collect::<BTreeSet<_>>(),
            after_last_move: (rng.below(2) == 0).then(|| rng.next() >> rng.below(64)),
        };
        state.save(&scratch.0, &key).expect("the state is saved");
        assert_eq!(State::load(&scratch.0, &key), state, "{key:?}");
    });
}

/// A history in the Responses API's shape: calls, their outputs (string or content items), and
/// messages, with ids that repeat and ids that mangle to the same name.
fn history(rng: &mut Rng) -> Vec<Value> {
    let mut items = Vec::new();
    for _ in 0..rng.below(30) {
        let id = if rng.below(4) == 0 {
            format!("call{}", rng.pick(&[".1", "_1", "/1", "-1"]))
        } else {
            call_id(rng)
        };
        match rng.below(5) {
            0 => items.push(json!({"type": "function_call", "call_id": id, "name": "shell", "arguments": "{}"})),
            1 => items.push(
                json!({"type": "message", "role": "user", "content": [{"type": "input_text", "text": output(rng)}]}),
            ),
            2 => items.push(json!({
                "type": "custom_tool_call_output",
                "call_id": id,
                "output": [{"type": "input_text", "text": output(rng)}, {"type": "input_text", "text": output(rng)}],
            })),
            _ => {
                let text = format!("{}{}", output(rng), "y".repeat(rng.below(2_000)));
                items.push(json!({"type": "function_call_output", "call_id": id, "output": text}));
            }
        }
    }
    items
}

#[test]
fn a_masked_output_is_shown_as_a_placeholder_naming_a_copy_of_its_own_text() {
    let mut masked = 0;
    each_case(60, |rng| {
        let scratch = Scratch::new("apply");
        let policy = Policy {
            high: 0,
            low: 0,
            min_step: 0,
            keep_recent: rng.below(4),
            min_chars: rng.below(800),
        };
        let original = history(rng);
        let mut items = original.clone();
        apply_with(&policy, &scratch.0, &mut items, 1 + rng.next() % 1_000_000, |item| item);
        assert_eq!(items.len(), original.len());
        for (before, after) in original.iter().zip(&items) {
            if before == after {
                continue;
            }
            let (Some(Value::String(shown)), Some(id)) =
                (after.get("output"), after.get("call_id").and_then(Value::as_str))
            else {
                panic!("{before} became {after}");
            };
            let path = copy_path(&scratch.0, id);
            let mut rest = after.clone();
            rest["output"] = before["output"].clone();
            assert_eq!(&rest, before, "only the output changes");
            assert_eq!(shown, &placeholder(&output_text(before), &path));
            // A copy is written once per id: two outputs with one id share it, so only an
            // output whose text is the copy's may be shown as masked.
            let copy = std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("{path:?}: {error}"));
            let same_id: Vec<String> = original
                .iter()
                .filter(|item| item.get("call_id").and_then(Value::as_str) == Some(id))
                .map(output_text)
                .collect();
            assert!(same_id.contains(&copy), "{id:?}: the copy holds no output of that id");
            masked += 1;
        }
    });
    assert!(masked > 0, "no case masked anything");
}
