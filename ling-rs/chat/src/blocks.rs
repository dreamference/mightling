//! The model's Markdown as Telegram rich-message blocks (specs/DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md
//! §3.2; Bot API 10.1–10.3, `InputRichMessage.blocks`): paragraphs, headings, lists, code, tables,
//! quotes and dividers, with bold, italic, code, strikethrough and links inside them.
//!
//! The bridge builds blocks itself and never passes the model's text on as `markdown` or `html`:
//! Telegram's rich Markdown accepts HTML tags, buttons (`<tg-button>`) among them, and the model's
//! text carries what it read on web pages and in mail. Here every string is plain text in a block,
//! so raw HTML shows as the characters it is; the only element with an address is a link, and only
//! for `http`, `https` and `mailto`. No button, login link or Mini App element can come from here.

use serde_json::Value;
use serde_json::json;

/// What one rich message may hold (Telegram: 32,768 UTF-8 characters and 500 blocks), with room.
pub const MESSAGE_CHARS: usize = 30_000;
pub const MESSAGE_BLOCKS: usize = 480;

/// The draft's placeholder before the first word (`InputRichBlockThinking`, drafts only).
pub fn thinking() -> Value {
    json!({ "type": "thinking", "text": "Thinking…" })
}

/// Markdown to blocks.
pub fn to_blocks(markdown: &str) -> Vec<Value> {
    let lines: Vec<&str> = markdown.lines().collect();
    parse_blocks(&lines)
}

fn fence(line: &str) -> Option<&str> {
    line.trim_start().strip_prefix("```").map(str::trim)
}

fn heading(line: &str) -> Option<(usize, &str)> {
    let trimmed = line.trim_start();
    let level = trimmed.chars().take_while(|c| *c == '#').count();
    let rest = &trimmed[level..];
    ((1..=6).contains(&level) && (rest.starts_with(' ') || rest.is_empty())).then(|| (level, rest.trim().trim_end_matches('#').trim_end()))
}

fn is_divider(line: &str) -> bool {
    let compact: String = line.chars().filter(|c| !c.is_whitespace()).collect();
    compact.len() >= 3 && ["-", "*", "_"].iter().any(|mark| compact.chars().all(|c| c.to_string() == *mark))
}

/// A list item's marker: (indent, ordered number, the text after the marker).
fn list_item(line: &str) -> Option<(usize, Option<u64>, &str)> {
    let indent = line.len() - line.trim_start().len();
    let rest = line.trim_start();
    for bullet in ["- ", "* ", "+ "] {
        if let Some(text) = rest.strip_prefix(bullet) {
            return Some((indent, None, text));
        }
    }
    let digits = rest.chars().take_while(char::is_ascii_digit).count();
    if digits > 0 && digits <= 9 {
        let after = &rest[digits..];
        if let Some(text) = after.strip_prefix(". ").or_else(|| after.strip_prefix(") ")) {
            return Some((indent, rest[..digits].parse().ok(), text));
        }
    }
    None
}

fn is_table_row(line: &str) -> bool {
    line.trim_start().starts_with('|')
}

fn is_table_separator(line: &str) -> bool {
    let cells = table_cells(line);
    !cells.is_empty() && cells.iter().all(|cell| !cell.is_empty() && cell.chars().all(|c| matches!(c, '-' | ':' | ' ')) && cell.contains('-'))
}

fn table_cells(line: &str) -> Vec<String> {
    let trimmed = line.trim();
    let inner = trimmed.strip_prefix('|').unwrap_or(trimmed);
    let inner = inner.strip_suffix('|').unwrap_or(inner);
    inner.split('|').map(|cell| cell.trim().to_string()).collect()
}

fn starts_block(lines: &[&str], i: usize) -> bool {
    let line = lines[i];
    fence(line).is_some()
        || heading(line).is_some()
        || is_divider(line)
        || line.trim_start().starts_with('>')
        || list_item(line).is_some()
        || (is_table_row(line) && lines.get(i + 1).is_some_and(|next| is_table_separator(next)))
}

fn parse_blocks(lines: &[&str]) -> Vec<Value> {
    let mut blocks = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        let line = lines[i];
        if line.trim().is_empty() {
            i += 1;
            continue;
        }
        if let Some(info) = fence(line) {
            let mut code = Vec::new();
            i += 1;
            while i < lines.len() && fence(lines[i]).is_none() {
                code.push(lines[i]);
                i += 1;
            }
            i += 1;
            let mut block = json!({ "type": "pre", "text": code.join("\n") });
            if let Some(language) = info.split_whitespace().next().filter(|l| l.chars().all(|c| c.is_ascii_alphanumeric() || "+-#._".contains(c))) {
                block["language"] = json!(language);
            }
            blocks.push(block);
            continue;
        }
        if let Some((level, text)) = heading(line) {
            blocks.push(json!({ "type": "heading", "text": inline(text), "size": level }));
            i += 1;
            continue;
        }
        if is_divider(line) {
            blocks.push(json!({ "type": "divider" }));
            i += 1;
            continue;
        }
        if is_table_row(line) && lines.get(i + 1).is_some_and(|next| is_table_separator(next)) {
            let header = table_cells(line);
            let width = header.len();
            let mut rows = vec![header.iter().map(|cell| table_cell(cell, true)).collect::<Vec<_>>()];
            i += 2;
            while i < lines.len() && is_table_row(lines[i]) {
                let mut cells: Vec<Value> = table_cells(lines[i]).iter().take(width).map(|cell| table_cell(cell, false)).collect();
                while cells.len() < width {
                    cells.push(table_cell("", false));
                }
                rows.push(cells);
                i += 1;
            }
            blocks.push(json!({ "type": "table", "cells": rows, "is_bordered": true, "is_striped": true }));
            continue;
        }
        if line.trim_start().starts_with('>') {
            let mut quoted = Vec::new();
            while i < lines.len() && lines[i].trim_start().starts_with('>') {
                let rest = lines[i].trim_start()[1..].to_string();
                quoted.push(rest.strip_prefix(' ').map(str::to_string).unwrap_or(rest));
                i += 1;
            }
            let quoted: Vec<&str> = quoted.iter().map(String::as_str).collect();
            blocks.push(json!({ "type": "blockquote", "blocks": parse_blocks(&quoted) }));
            continue;
        }
        if let Some((indent, first_number, _)) = list_item(line) {
            let ordered = first_number.is_some();
            let mut items = Vec::new();
            while i < lines.len() {
                let Some((item_indent, number, text)) = list_item(lines[i]) else { break };
                if item_indent != indent || number.is_some() != ordered {
                    break;
                }
                // The item's own text, then every line indented under it (a nested list, a
                // continued paragraph) until the next item at this level or a blank line before
                // something that is not indented.
                let mut body = vec![text.to_string()];
                i += 1;
                while i < lines.len() {
                    let next = lines[i];
                    let next_indent = next.len() - next.trim_start().len();
                    if next.trim().is_empty() {
                        let continues = lines.get(i + 1).is_some_and(|after| after.len() - after.trim_start().len() > indent && !after.trim().is_empty());
                        if !continues {
                            break;
                        }
                        body.push(String::new());
                        i += 1;
                        continue;
                    }
                    if next_indent <= indent {
                        break;
                    }
                    body.push(next.chars().skip((indent + 2).min(next_indent)).collect());
                    i += 1;
                }
                let body: Vec<&str> = body.iter().map(String::as_str).collect();
                let mut item = json!({ "blocks": parse_blocks(&body) });
                if let Some(number) = number {
                    item["value"] = json!(number);
                    item["type"] = json!("1");
                }
                items.push(item);
            }
            blocks.push(json!({ "type": "list", "items": items }));
            continue;
        }
        let mut paragraph = vec![line.trim()];
        i += 1;
        while i < lines.len() && !lines[i].trim().is_empty() && !starts_block(lines, i) {
            paragraph.push(lines[i].trim());
            i += 1;
        }
        blocks.push(json!({ "type": "paragraph", "text": inline(&paragraph.join("\n")) }));
    }
    blocks
}

fn table_cell(text: &str, header: bool) -> Value {
    let mut cell = json!({ "text": inline(text), "align": "left", "valign": "top" });
    if header {
        cell["is_header"] = json!(true);
    }
    cell
}

/// Inline Markdown as `RichText`: a plain string when there is no styling.
pub fn inline(text: &str) -> Value {
    let parts = parse_inline(text);
    match parts.as_slice() {
        [] => json!(""),
        [Value::String(_)] => parts.into_iter().next().unwrap_or_default(),
        _ => Value::Array(parts),
    }
}

fn styled(kind: &str, inner: &str) -> Value {
    json!({ "type": kind, "text": inline(inner) })
}

fn safe_url(url: &str) -> bool {
    let lower = url.trim().to_ascii_lowercase();
    (lower.starts_with("https://") || lower.starts_with("http://") || lower.starts_with("mailto:")) && !url.chars().any(char::is_whitespace)
}

fn parse_inline(text: &str) -> Vec<Value> {
    let mut parts: Vec<Value> = Vec::new();
    let mut plain = String::new();
    let flush = |plain: &mut String, parts: &mut Vec<Value>| {
        if !plain.is_empty() {
            parts.push(Value::String(std::mem::take(plain)));
        }
    };
    let mut rest = text;
    while let Some(c) = rest.chars().next() {
        let prev_alnum = plain.chars().last().is_some_and(char::is_alphanumeric);
        // `code`: literal inside.
        if c == '`'
            && let Some(end) = rest[1..].find('`')
            && end > 0
        {
            flush(&mut plain, &mut parts);
            parts.push(json!({ "type": "code", "text": &rest[1..1 + end] }));
            rest = &rest[end + 2..];
            continue;
        }
        let mut matched = false;
        for (mark, kind) in [("**", "bold"), ("__", "bold"), ("~~", "strikethrough")] {
            if mark == "__" && prev_alnum {
                continue;
            }
            if let Some(after) = rest.strip_prefix(mark)
                && let Some(end) = after.find(mark)
                && end > 0
                && !after.starts_with(' ')
            {
                flush(&mut plain, &mut parts);
                parts.push(styled(kind, &after[..end]));
                rest = &after[end + mark.len()..];
                matched = true;
                break;
            }
        }
        if matched {
            continue;
        }
        if (c == '*' || (c == '_' && !prev_alnum))
            && let Some(after) = rest.get(1..)
            && !after.starts_with(' ')
            && !after.starts_with(c)
            && let Some(end) = after.char_indices().find(|&(n, ch)| ch == c && n > 0 && !after[n + 1..].starts_with(c) && (c == '*' || !after[n + 1..].starts_with(char::is_alphanumeric))).map(|(n, _)| n)
        {
            flush(&mut plain, &mut parts);
            parts.push(styled("italic", &after[..end]));
            rest = &after[end + 1..];
            continue;
        }
        if c == '['
            && let Some(close) = rest.find("](")
            && let Some(end) = rest[close + 2..].find(')')
        {
            let label = &rest[1..close];
            let url = &rest[close + 2..close + 2 + end];
            if !label.is_empty() && !label.contains('\n') {
                if safe_url(url) {
                    flush(&mut plain, &mut parts);
                    parts.push(json!({ "type": "url", "text": inline(label), "url": url.trim() }));
                } else {
                    plain.push_str(&format!("{label} ({url})"));
                }
                rest = &rest[close + 3 + end..];
                continue;
            }
        }
        plain.push(c);
        rest = &rest[c.len_utf8()..];
    }
    flush(&mut plain, &mut parts);
    parts
}

/// The characters of text a block carries.
fn chars_of(value: &Value) -> usize {
    match value {
        Value::String(text) => text.chars().count(),
        Value::Array(items) => items.iter().map(chars_of).sum(),
        Value::Object(object) => object.iter().filter(|(key, _)| !matches!(key.as_str(), "type" | "url" | "language" | "align" | "valign")).map(|(_, v)| chars_of(v)).sum(),
        _ => 0,
    }
}

/// A block too long for one message, cut into pieces at line breaks (code and paragraphs; any
/// other block is kept whole and travels alone).
fn cut(block: Value, limit: usize) -> Vec<Value> {
    let kind = block.get("type").and_then(Value::as_str).unwrap_or_default().to_string();
    let Some(text) = block.get("text").and_then(Value::as_str).map(str::to_string) else { return vec![block] };
    if chars_of(&block) <= limit || !matches!(kind.as_str(), "pre" | "paragraph") {
        return vec![block];
    }
    let mut pieces = Vec::new();
    let mut current = String::new();
    for line in text.split('\n') {
        let line: String = line.chars().take(limit).collect();
        if !current.is_empty() && current.chars().count() + line.chars().count() + 1 > limit {
            pieces.push(std::mem::take(&mut current));
        }
        if !current.is_empty() {
            current.push('\n');
        }
        current.push_str(&line);
    }
    pieces.push(current);
    pieces
        .into_iter()
        .map(|piece| {
            let mut part = block.clone();
            part["text"] = json!(piece);
            part
        })
        .collect()
}

/// Blocks into messages, each within Telegram's limits, split only between blocks.
pub fn split(blocks: Vec<Value>, max_chars: usize, max_blocks: usize) -> Vec<Vec<Value>> {
    let mut messages: Vec<Vec<Value>> = Vec::new();
    let mut current: Vec<Value> = Vec::new();
    let mut size = 0;
    for block in blocks.into_iter().flat_map(|block| cut(block, max_chars)) {
        let chars = chars_of(&block);
        if !current.is_empty() && (size + chars > max_chars || current.len() >= max_blocks) {
            messages.push(std::mem::take(&mut current));
            size = 0;
        }
        size += chars;
        current.push(block);
    }
    if !current.is_empty() {
        messages.push(current);
    }
    messages
}

/// Blocks as plain text, for a part Telegram refused as rich blocks: the words and the links' addresses.
pub fn plain_text(blocks: &[Value]) -> String {
    blocks.iter().map(block_text).filter(|text| !text.is_empty()).collect::<Vec<_>>().join("\n\n")
}

fn block_text(block: &Value) -> String {
    if let Some(blocks) = block.get("blocks").and_then(Value::as_array) {
        return plain_text(blocks);
    }
    if let Some(items) = block.get("items").and_then(Value::as_array) {
        return items.iter().map(|item| format!("- {}", block_text(item).replace('\n', "\n  "))).collect::<Vec<_>>().join("\n");
    }
    if let Some(rows) = block.get("cells").and_then(Value::as_array) {
        let row = |row: &Value| row.as_array().map(|cells| cells.iter().map(block_text).collect::<Vec<_>>().join(" | ")).unwrap_or_default();
        return rows.iter().map(row).collect::<Vec<_>>().join("\n");
    }
    inline_text(block.get("text").unwrap_or(&Value::Null))
}

fn inline_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts.iter().map(inline_text).collect(),
        Value::Object(part) => {
            let text = inline_text(part.get("text").unwrap_or(&Value::Null));
            match part.get("url").and_then(Value::as_str) {
                Some(url) if url != text => format!("{text} ({url})"),
                _ => text,
            }
        }
        _ => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paragraphs_headings_code_and_dividers() {
        let blocks = to_blocks("# Two purifiers\n\nThe **Levoit** is *quieter*.\nIt has `HEPA 13`.\n\n---\n\n```rust\nfn main() {}\n```\n");
        assert_eq!(blocks[0], json!({ "type": "heading", "text": "Two purifiers", "size": 1 }));
        assert_eq!(
            blocks[1],
            json!({ "type": "paragraph", "text": ["The ", { "type": "bold", "text": "Levoit" }, " is ", { "type": "italic", "text": "quieter" }, ".\nIt has ", { "type": "code", "text": "HEPA 13" }, "."] })
        );
        assert_eq!(blocks[2], json!({ "type": "divider" }));
        assert_eq!(blocks[3], json!({ "type": "pre", "text": "fn main() {}", "language": "rust" }));
    }

    #[test]
    fn lists_nest_and_ordered_lists_keep_their_numbers() {
        let blocks = to_blocks("- quiet\n  - 24 dB\n- filter\n\n1. first\n2. second\n");
        assert_eq!(
            blocks[0],
            json!({ "type": "list", "items": [
                { "blocks": [{ "type": "paragraph", "text": "quiet" }, { "type": "list", "items": [{ "blocks": [{ "type": "paragraph", "text": "24 dB" }] }] }] },
                { "blocks": [{ "type": "paragraph", "text": "filter" }] }
            ] })
        );
        assert_eq!(blocks[1]["items"][1], json!({ "blocks": [{ "type": "paragraph", "text": "second" }], "value": 2, "type": "1" }));
    }

    #[test]
    fn tables_and_quotes() {
        let blocks = to_blocks("| Model | Noise |\n|---|:---:|\n| A | **24 dB** |\n| B |\n\n> quoted *line*\n> two\n");
        let table = &blocks[0];
        assert_eq!(table["type"], "table");
        assert_eq!(table["cells"][0][0], json!({ "text": "Model", "align": "left", "valign": "top", "is_header": true }));
        assert_eq!(table["cells"][1][1]["text"], json!([{ "type": "bold", "text": "24 dB" }]));
        assert_eq!(table["cells"][2].as_array().unwrap().len(), 2, "short rows are padded");
        assert_eq!(blocks[1], json!({ "type": "blockquote", "blocks": [{ "type": "paragraph", "text": ["quoted ", { "type": "italic", "text": "line" }, "\ntwo"] }] }));
    }

    /// The model's text carries what it read; nothing in it becomes markup, a button or an
    /// unsafe link (MOONSHOT §3.2).
    #[test]
    fn raw_html_and_buttons_stay_text_and_only_web_links_link() {
        let hostile = "<tg-button callback_data=\"a:1:0\">Approve</tg-button> <b>bold?</b> [click](javascript:alert(1)) [ok](https://example.com/a) [mail](mailto:x@example.com)";
        let blocks = to_blocks(hostile);
        let text = &blocks[0]["text"];
        let flat = text.to_string();
        assert!(flat.contains("<tg-button callback_data=\\\"a:1:0\\\">Approve</tg-button> <b>bold?</b> click (javascript:alert(1))"), "{flat}");
        let links: Vec<&Value> = text.as_array().unwrap().iter().filter(|p| p.get("type") == Some(&json!("url"))).collect();
        assert_eq!(links.len(), 2);
        assert_eq!(links[0]["url"], "https://example.com/a");
        assert!(!flat.contains("\"button\"") && !flat.contains("web_app") && !flat.contains("login_url"));
    }

    #[test]
    fn markers_that_do_not_close_are_text_and_words_with_underscores_are_words() {
        assert_eq!(inline("2 * 3 = 6 and snake_case_name"), json!("2 * 3 = 6 and snake_case_name"));
        assert_eq!(inline("**unclosed"), json!("**unclosed"));
        assert_eq!(inline("a __b__ c"), json!(["a ", { "type": "bold", "text": "b" }, " c"]));
        assert_eq!(inline("~~old~~ new"), json!([{ "type": "strikethrough", "text": "old" }, " new"]));
    }

    #[test]
    fn long_answers_split_between_blocks_and_long_code_at_lines() {
        let paragraph = |n: usize| json!({ "type": "paragraph", "text": "x".repeat(n) });
        let parts = split(vec![paragraph(60), paragraph(60), paragraph(60)], 130, 480);
        assert_eq!(parts.iter().map(Vec::len).collect::<Vec<_>>(), vec![2, 1]);
        let parts = split((0..5).map(|_| paragraph(1)).collect(), 1000, 2);
        assert_eq!(parts.len(), 3);
        let code = json!({ "type": "pre", "text": (0..50).map(|n| format!("line {n:02}")).collect::<Vec<_>>().join("\n"), "language": "text" });
        let parts = split(vec![code], 100, 480);
        assert!(parts.len() > 1 && parts.iter().flatten().all(|b| b["language"] == "text" && chars_of(b) <= 100), "{parts:?}");
        assert!(split(vec![], 10, 10).is_empty());
    }
}
