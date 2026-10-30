//! The process-wide switches, in a process of their own: once set they stay set, and the crate's
//! unit tests run without them.

use ling_masking::DropPolicy;
use ling_masking::Policy;
use serde_json::Value;
use serde_json::json;

/// A task message and `calls` calls named `<prefix>_<n>`, each with an output of `lines` lines.
fn history(prefix: &str, calls: usize, lines: usize) -> Vec<Value> {
    let mut out = vec![json!({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix it"}]})];
    for n in 0..calls {
        let id = format!("{prefix}_{n:03}");
        let cmd = json!({"cmd": format!("cat file{n}.py")}).to_string();
        out.push(json!({"type": "function_call", "name": "exec_command", "arguments": cmd, "call_id": id}));
        let body: Vec<String> = (0..lines).map(|line| format!("line {line} of {id} {}", "x".repeat(40))).collect();
        out.push(json!({"type": "function_call_output", "call_id": id, "output": format!("Process exited with code 0\nOutput:\n{}", body.join("\n"))}));
    }
    out
}

fn starting_with(values: &[Value], prefix: &str) -> usize {
    values.iter().filter(|value| ling_masking::output_text(value).starts_with(prefix)).count()
}

#[test]
fn the_switches_turn_dropping_and_masking_on_for_the_process() {
    let home = std::env::temp_dir().join(format!("ling-masking-policies-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&home);

    // Dropping on, masking still off: a resumed session's request is shown its drops.
    assert!(ling_masking::set_drop_policy(DropPolicy::for_limit(44_000), &home));
    assert!(!ling_masking::set_drop_policy(DropPolicy::for_limit(1), &home), "the first call wins");
    assert_eq!(ling_masking::drop_policy(), Some(&DropPolicy::for_limit(44_000)));
    let mut live = history("call", 60, 20);
    assert!(ling_masking::drop_first(&mut live, 44_000, |v| v));
    assert_eq!(starting_with(&live, "[output of "), 50);
    let mut resumed = history("call", 60, 20);
    ling_masking::dropping::show_dropped(&mut resumed, |v| v);
    assert_eq!(resumed, live);
    let mut requested = history("call", 60, 20);
    ling_masking::apply(&mut requested, 44_000, |v| v);
    assert_eq!(requested, live, "the request shows the drops and masks nothing");

    // Masking on: a request past the high mark masks the oldest outputs.
    assert!(ling_masking::set_policy(Policy::for_limit(44_000), &home));
    assert!(!ling_masking::set_policy(Policy::for_limit(1), &home), "the first call wins");
    assert_eq!(ling_masking::policy(), Some(&Policy::for_limit(44_000)));
    let mut items = history("other", 40, 60);
    ling_masking::apply(&mut items, 40_000, |v| v);
    assert!(starting_with(&items, "[output moved out of context") > 0);
    assert!(home.join("masking").join("outputs").is_dir());
    let _ = std::fs::remove_dir_all(&home);
}
