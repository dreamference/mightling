//! The model's Markdown, for a messenger (MIGHTLING_CHAT §3): a small renderer to the HTML subset
//! Telegram and Matrix both accept (bold, italic, inline code, code blocks, links; headings become
//! bold), everything else escaped; and a splitter that keeps each part under a messenger's limit
//! without breaking a code block. A refused render falls back to plain text in the adapters, so
//! nothing here has to be perfect, only safe: no tag the input did not earn, no unescaped `<`.

/// Escapes text for HTML.
pub fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            _ => out.push(c),
        }
    }
    out
}

/// The info string of a fence line (```` ```rust ````), when the line is one.
fn fence(line: &str) -> Option<&str> {
    line.trim_start().strip_prefix("```").map(str::trim)
}

/// Markdown to the messenger HTML subset.
pub fn to_html(markdown: &str) -> String {
    let mut out = String::new();
    let mut code: Option<(String, Vec<&str>)> = None;
    for line in markdown.split('\n') {
        if let Some(info) = fence(line) {
            match code.take() {
                Some((language, lines)) => {
                    out.push_str(&code_block(&language, &lines));
                    out.push('\n');
                }
                None => code = Some((info.split_whitespace().next().unwrap_or_default().to_string(), Vec::new())),
            }
            continue;
        }
        if let Some((_, lines)) = code.as_mut() {
            lines.push(line);
            continue;
        }
        let trimmed = line.trim_start();
        let heading = trimmed.trim_start_matches('#');
        if trimmed.starts_with('#') && heading.starts_with(' ') {
            out.push_str("<b>");
            out.push_str(&inline(heading.trim()));
            out.push_str("</b>\n");
        } else {
            out.push_str(&inline(line));
            out.push('\n');
        }
    }
    if let Some((language, lines)) = code {
        out.push_str(&code_block(&language, &lines));
        out.push('\n');
    }
    out.truncate(out.trim_end_matches('\n').len());
    out
}

fn code_block(language: &str, lines: &[&str]) -> String {
    let body = escape(&lines.join("\n"));
    let safe = !language.is_empty() && language.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '_' | '#'));
    if safe { format!("<pre><code class=\"language-{language}\">{body}</code></pre>") } else { format!("<pre>{body}</pre>") }
}

/// One line's inline Markdown: `code`, **bold**, *italic*, [text](https://…).
fn inline(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '`' {
            if let Some(end) = find(&chars, i + 1, "`") {
                out.push_str("<code>");
                out.push_str(&escape(&chars[i + 1..end].iter().collect::<String>()));
                out.push_str("</code>");
                i = end + 1;
                continue;
            }
        }
        if c == '*' && chars.get(i + 1) == Some(&'*') {
            if let Some(end) = find(&chars, i + 2, "**") {
                if end > i + 2 {
                    out.push_str("<b>");
                    out.push_str(&inline(&chars[i + 2..end].iter().collect::<String>()));
                    out.push_str("</b>");
                    i = end + 2;
                    continue;
                }
            }
        }
        if c == '*' && chars.get(i + 1).is_some_and(|next| !next.is_whitespace() && *next != '*') {
            if let Some(end) = find(&chars, i + 1, "*") {
                if !chars[end - 1].is_whitespace() {
                    out.push_str("<i>");
                    out.push_str(&inline(&chars[i + 1..end].iter().collect::<String>()));
                    out.push_str("</i>");
                    i = end + 1;
                    continue;
                }
            }
        }
        if c == '[' {
            if let Some(close) = find(&chars, i + 1, "](") {
                if let Some(end) = find(&chars, close + 2, ")") {
                    let text: String = chars[i + 1..close].iter().collect();
                    let url: String = chars[close + 2..end].iter().collect();
                    if (url.starts_with("https://") || url.starts_with("http://")) && !url.contains(char::is_whitespace) {
                        out.push_str(&format!("<a href=\"{}\">{}</a>", escape(&url), escape(&text)));
                        i = end + 1;
                        continue;
                    }
                }
            }
        }
        out.push_str(&escape(&c.to_string()));
        i += 1;
    }
    out
}

/// The index of `needle`'s first occurrence at or after `from`.
fn find(chars: &[char], from: usize, needle: &str) -> Option<usize> {
    let needle: Vec<char> = needle.chars().collect();
    (from..chars.len()).find(|&start| chars[start..].starts_with(&needle))
}

/// Splits Markdown into parts of at most `limit` characters: at a blank line, then a line break,
/// then a space, then anywhere. A code block open at a split is closed there and reopened, with its
/// language, at the start of the next part.
pub fn split(markdown: &str, limit: usize) -> Vec<String> {
    let limit = limit.max(64);
    let mut parts = Vec::new();
    let mut rest: Vec<char> = markdown.trim_end().chars().collect();
    let mut reopen = String::new();
    while !rest.is_empty() {
        let mut part: Vec<char> = reopen.chars().collect();
        // Room for a closing fence if this part ends inside a block.
        let room = limit.saturating_sub(part.len() + 4);
        if rest.len() <= room {
            part.extend(rest.drain(..));
        } else {
            let window: String = rest[..room].iter().collect();
            let cut = window
                .rfind("\n\n")
                .map(|at| at + 1)
                .or_else(|| window.rfind('\n'))
                .or_else(|| window.rfind(' '))
                .filter(|&at| at > 0)
                .map(|bytes| window[..bytes].chars().count())
                .unwrap_or(room);
            part.extend(rest.drain(..cut));
            while rest.first().is_some_and(|c| *c == '\n' || *c == ' ') {
                rest.remove(0);
            }
        }
        let text: String = part.into_iter().collect();
        // Is a block open at the end of this part?
        let mut open: Option<String> = None;
        for line in text.split('\n') {
            if let Some(info) = fence(line) {
                open = match open {
                    Some(_) => None,
                    None => Some(info.to_string()),
                };
            }
        }
        let mut text = text.trim_end().to_string();
        match open {
            Some(language) if !rest.is_empty() => {
                text.push_str("\n```");
                reopen = format!("```{language}\n");
            }
            _ => reopen.clear(),
        }
        if !text.trim().is_empty() {
            parts.push(text);
        }
    }
    parts
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markup_the_messengers_accept_and_everything_else_escaped() {
        assert_eq!(to_html("**bold** and *it* and `a<b>`"), "<b>bold</b> and <i>it</i> and <code>a&lt;b&gt;</code>");
        assert_eq!(to_html("# Title\nx < y & z"), "<b>Title</b>\nx &lt; y &amp; z");
        assert_eq!(to_html("[docs](https://example.com/a?b=1&c=2)"), "<a href=\"https://example.com/a?b=1&amp;c=2\">docs</a>");
        assert_eq!(to_html("[x](javascript:alert(1))"), "[x](javascript:alert(1))");
        assert_eq!(to_html("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;");
        assert_eq!(to_html("2 * 3 * 4"), "2 * 3 * 4");
    }

    #[test]
    fn code_blocks_keep_their_text_and_a_safe_language() {
        assert_eq!(to_html("```rust\nfn a() -> u8 { 1 }\n```"), "<pre><code class=\"language-rust\">fn a() -&gt; u8 { 1 }</code></pre>");
        assert_eq!(to_html("```\"><b\nx\n```"), "<pre>x</pre>");
        // An unclosed block is closed.
        assert_eq!(to_html("```\n**not bold**"), "<pre>**not bold**</pre>");
    }

    #[test]
    fn short_text_is_one_part() {
        assert_eq!(split("hello\n", 4000), vec!["hello".to_string()]);
    }

    #[test]
    fn long_text_splits_at_paragraphs_and_fits() {
        let paragraph = "word ".repeat(30);
        let text = vec![paragraph.trim(); 10].join("\n\n");
        let parts = split(&text, 400);
        assert!(parts.len() > 1);
        for part in &parts {
            assert!(part.chars().count() <= 400, "{}", part.len());
            assert!(!part.starts_with('\n') && !part.ends_with('\n'));
        }
        assert_eq!(parts.join(" ").split_whitespace().count(), text.split_whitespace().count());
    }

    #[test]
    fn a_code_block_across_a_split_is_closed_and_reopened() {
        let code: String = (0..80).map(|n| format!("let x{n} = {n};\n")).collect();
        let text = format!("Here:\n\n```rust\n{code}```\nDone.");
        let parts = split(&text, 300);
        assert!(parts.len() > 2);
        for (n, part) in parts.iter().enumerate() {
            assert!(part.chars().count() <= 300);
            assert_eq!(part.matches("```").count() % 2, 0, "part {n} leaves a block open: {part}");
            if n > 0 && n + 1 < parts.len() {
                assert!(part.starts_with("```rust\n"), "part {n} does not reopen the block");
            }
        }
        assert!(parts.last().unwrap().ends_with("Done."));
    }

    #[test]
    fn a_word_longer_than_the_limit_is_cut() {
        let parts = split(&"x".repeat(1000), 100);
        assert!(parts.iter().all(|part| part.chars().count() <= 100));
        assert_eq!(parts.concat().len(), 1000);
    }
}
