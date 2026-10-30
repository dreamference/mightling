//! The corners of the TOML reading that decide whether text is TOML at all. Text this reads as TOML
//! takes the key where a TOML parser finds it; text it refuses falls back to the first plain
//! `mightling_airgapped = <value>` line. `after` puts such a line inside a multi-line string ahead
//! of the real key, so the two readings disagree: `on` when the text before is TOML, `off` when it
//! is not.

use ling_airgapped::*;

fn after(prefix: &str) -> String {
    format!(
        "{prefix}\nnote = \"\"\"\nmightling_airgapped = \"off\"\n\"\"\"\nmightling_airgapped = \"on\"\n"
    )
}

fn read(prefix: &str) -> Option<String> {
    toml_value(&after(prefix))
}

#[test]
fn text_toml_accepts_lends_the_key_its_place() {
    // Up to two quotes after a closing `'''` or `"""` are the string's content.
    assert_eq!(read("x = '''a''''").as_deref(), Some("on"));
    assert_eq!(read("x = \"\"\"a\"\"\"\"").as_deref(), Some("on"));
}

#[test]
fn text_toml_refuses_is_read_line_by_line() {
    for prefix in [
        // A date followed by a space and no time.
        "d = 1979-05-27 x",
        // A key with no value.
        "x =",
        // A literal string the line end cuts short, then a blank line.
        "x = 'abc\n",
        // A `\u` escape whose digits are not all hexadecimal.
        "x = \"\\u+041\"",
        // A backslash and spaces that end no line, in a multi-line string.
        "x = \"\"\"a\\ b\"\"\"",
        // A backslash ending a line, in a single-line string.
        "x = \"a\\\nb\"",
        // An escape TOML does not have.
        "x = \"\\q\"",
    ] {
        assert_eq!(read(prefix).as_deref(), Some("off"), "{prefix:?}");
    }
}

#[test]
fn a_string_value_is_its_content_exactly() {
    // A line end right after the opening quotes is dropped, a Windows one too.
    assert_eq!(
        toml_value("mightling_airgapped = \"\"\"\r\non\"\"\"\r\n").as_deref(),
        Some("on")
    );
    // A fourth closing quote is content, so the value is not a level.
    assert_eq!(
        toml_value("mightling_airgapped = \"\"\"on\"\"\"\"\n").as_deref(),
        Some("on\"")
    );
    // A `#` inside a string is content, not a comment.
    assert_eq!(
        toml_value("mightling_airgapped = \"on # x\"\n").as_deref(),
        Some("on # x")
    );
}

#[test]
fn text_not_toml_finds_an_indented_key() {
    assert_eq!(
        toml_value("x = @\n  mightling_airgapped = on\n").as_deref(),
        Some("on")
    );
}

#[test]
fn a_blank_tier_is_unset_not_invalid() {
    // A blank environment variable is not a tier; a blank session value is not a level (AIRGAPPED
    // §6.5), so it counts as `on` and is named.
    let path = std::path::PathBuf::from("config.toml");
    let configs = [(path.clone(), "mightling_airgapped = \"on\"\n".to_string())];
    let resolved = resolve_from(None, Some("  "), &configs);
    assert_eq!(
        (resolved.level, resolved.source, resolved.invalid),
        (Level::On, Source::ConfigFile(path), Vec::<String>::new())
    );
    let resolved = resolve_from(Some("  "), Some("off"), &[]);
    assert_eq!(
        (resolved.level, resolved.source, resolved.invalid.len()),
        (Level::On, Source::Session, 1)
    );
}
