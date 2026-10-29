//! E-mail (spec §7.1, Phase 2): a `.eml` message or an `.mbox` mailbox becomes one unit per
//! message, plus the text of its attachments one level deep.
//!
//! Parsed by `mail-parser` (MIME, encoded words, legacy charsets; an mbox split at its `From `
//! lines, `>From` unquoted). A message's unit is its headers (From, To, Cc, Date, Subject, and
//! the attachments' names) and its text body (the HTML body as text when there is no plain
//! one), located as `msg N`, N counting from 1 in the file, with the subject as its heading. An
//! attachment of a kind the index reads (by its name's extension, else its MIME type) goes
//! through the same extractor as a file of that kind, and its units follow the message's,
//! located `msg N · name` (and its page, for a PDF); a forwarded message is read as text, its
//! own attachments not. All of a file's units share one line numbering, so `read` takes a
//! message (`msg 3`) or any range of lines; a message and its attachments are chunked together
//! and never with another message (Phase 0's rule).

use std::io::Cursor;

use mail_parser::{Address, Message, MessageParser, MimeHeaders};

use super::{extract_bytes, Readers, Unit};
use crate::discover::{sniff_bytes, Kind};

/// `Name <address>, …`
fn addresses(address: Option<&Address>) -> String {
    let Some(address) = address else {
        return String::new();
    };
    address
        .iter()
        .map(|a| match (a.name(), a.address()) {
            (Some(name), Some(addr)) => format!("{name} <{addr}>"),
            (Some(name), None) => name.to_string(),
            (None, Some(addr)) => addr.to_string(),
            (None, None) => String::new(),
        })
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(", ")
}

/// A body's text with Windows line ends, trailing spaces and long blank runs gone.
fn tidy(text: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    let mut blank = 0;
    for line in text.lines() {
        let line = line.trim_end();
        if line.is_empty() {
            blank += 1;
            if blank > 1 {
                continue;
            }
        } else {
            blank = 0;
        }
        out.push(line);
    }
    out.join("\n").trim_matches('\n').to_string()
}

/// The kind an attachment is read as: by its name, else by its MIME type. Mail inside mail is
/// not read as a mailbox (one level deep).
fn attachment_kind(name: Option<&str>, ctype: Option<(&str, Option<&str>)>) -> Option<Kind> {
    if let Some(kind) = name.and_then(Kind::of) {
        return (!matches!(kind, Kind::Eml | Kind::Mbox)).then_some(kind);
    }
    let (main, sub) = ctype?;
    Some(
        match (
            main.to_ascii_lowercase().as_str(),
            sub.unwrap_or("").to_ascii_lowercase().as_str(),
        ) {
            ("application", "pdf") => Kind::Pdf,
            ("text", "plain") => Kind::Text,
            ("text", "markdown") => Kind::Markdown,
            ("text", "html") => Kind::Html,
            ("text", "csv") => Kind::Csv,
            ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document") => {
                Kind::Docx
            }
            ("application", "vnd.oasis.opendocument.text") => Kind::Odt,
            ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet") => Kind::Xlsx,
            ("application", "vnd.ms-excel") => Kind::Xls,
            ("application", "vnd.oasis.opendocument.spreadsheet") => Kind::Ods,
            ("application", "vnd.openxmlformats-officedocument.presentationml.presentation") => {
                Kind::Pptx
            }
            ("application", "vnd.oasis.opendocument.presentation") => Kind::Odp,
            _ => return None,
        },
    )
}

/// The header block and body of a message, as one text.
fn message_text(message: &Message, attachments: &[String]) -> String {
    let mut head = Vec::new();
    for (label, value) in [
        ("From", addresses(message.from())),
        ("To", addresses(message.to())),
        ("Cc", addresses(message.cc())),
    ] {
        if !value.is_empty() {
            head.push(format!("{label}: {value}"));
        }
    }
    if let Some(date) = message.date() {
        head.push(format!("Date: {}", date.to_rfc3339()));
    }
    head.push(format!("Subject: {}", message.subject().unwrap_or("")));
    if !attachments.is_empty() {
        head.push(format!("Attachments: {}", attachments.join(", ")));
    }
    let body: Vec<String> = (0..message.text_body_count())
        .filter_map(|i| message.body_text(i))
        .map(|b| tidy(&b))
        .filter(|b| !b.is_empty())
        .collect();
    let mut text = head.join("\n");
    if !body.is_empty() {
        text.push_str("\n\n");
        text.push_str(&body.join("\n\n"));
    }
    text
}

/// Gives units consecutive lines from `line` on, in the message's group.
fn place(units: &mut [Unit], line: &mut u32, group: u32) {
    for unit in units {
        let count = unit.text.split('\n').count() as u32;
        unit.line_first = Some(*line);
        unit.line_last = Some(*line + count - 1);
        unit.page = None;
        unit.group = Some(group);
        *line += count;
    }
}

/// The units of message `n`: itself, then each readable attachment's.
fn message_units(n: u32, raw: &[u8], line: &mut u32, readers: &Readers) -> Vec<Unit> {
    let Some(message) = MessageParser::default().parse(raw) else {
        return Vec::new();
    };
    let subject = message.subject().unwrap_or("").trim().to_string();
    let heading = if subject.is_empty() {
        "(no subject)".to_string()
    } else {
        subject
    };
    let mut names = Vec::new();
    let mut parts: Vec<Unit> = Vec::new();
    for (i, part) in message.attachments().enumerate() {
        if let Some(inner) = part.message() {
            let title = inner
                .subject()
                .map(|s| format!("attached message \"{}\"", s.trim()))
                .unwrap_or_else(|| "attached message".to_string());
            names.push(title.clone());
            parts.push(Unit {
                loc: format!("msg {n} · {title}"),
                page: None,
                line_first: None,
                line_last: None,
                heading: format!("{heading} > {title}"),
                group: None,
                text: message_text(inner, &[]),
            });
            continue;
        }
        let name = part
            .attachment_name()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| format!("attachment {}", i + 1));
        names.push(name.clone());
        let ctype = part.content_type().map(|c| (c.ctype(), c.subtype()));
        let Some(kind) =
            attachment_kind(Some(&name), None).or_else(|| attachment_kind(None, ctype))
        else {
            continue;
        };
        let contents = part.contents();
        if contents.is_empty()
            || sniff_bytes(
                &contents[..contents.len().min(crate::discover::SNIFF_BYTES)],
                kind,
            )
            .is_err()
        {
            continue;
        }
        // One level deep: an attachment's own extraction never reads mail.
        let Ok(extracted) = extract_bytes(&name, kind, contents, readers, None) else {
            continue;
        };
        for unit in extracted.units {
            let at = if unit.page.is_some() {
                format!(" {}", unit.loc)
            } else {
                String::new()
            };
            let inner = if unit.heading.is_empty() {
                String::new()
            } else {
                format!(" > {}", unit.heading)
            };
            parts.push(Unit {
                loc: format!("msg {n} · {name}{at}"),
                heading: format!("{heading} > {name}{inner}"),
                ..unit
            });
        }
    }
    let mut units = vec![Unit {
        loc: format!("msg {n}"),
        page: None,
        line_first: None,
        line_last: None,
        heading,
        group: None,
        text: message_text(&message, &names),
    }];
    units.extend(parts);
    units.retain(|u| !u.text.trim().is_empty());
    place(&mut units, line, n);
    units
}

/// A `.eml` file: one message (a leading mbox `From ` line is skipped).
pub fn eml(bytes: &[u8], readers: &Readers) -> Vec<Unit> {
    let mut raw = bytes;
    // `From ` with a space is never a header (`From:` is): the envelope line of a mailbox.
    if raw.starts_with(b"From ") {
        raw = &raw[raw
            .iter()
            .position(|b| *b == b'\n')
            .map_or(raw.len(), |i| i + 1)..];
    }
    let mut line = 1;
    message_units(1, raw, &mut line, readers)
}

/// An `.mbox` file: every message, in order.
pub fn mbox(bytes: &[u8], readers: &Readers) -> Vec<Unit> {
    let mut units = Vec::new();
    let mut line = 1;
    for (i, message) in
        mail_parser::mailbox::mbox::MessageIterator::new(Cursor::new(bytes)).enumerate()
    {
        let Ok(message) = message else { break };
        units.extend(message_units(
            i as u32 + 1,
            message.contents(),
            &mut line,
            readers,
        ));
    }
    units
}

#[cfg(test)]
mod tests {
    use super::*;

    fn message(id: u32, subject: &str, body: &str) -> String {
        format!("From: Maria Lind <maria@kestrel.example>\r\nTo: ops@kestrel.example\r\nDate: Tue, 4 Mar 2025 09:30:00 +0100\r\nMessage-ID: <kr-{id}@kestrel.example>\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n")
    }

    #[test]
    fn a_mailbox_becomes_one_unit_per_message_on_shared_lines() {
        let mbox_text = format!(
            "From maria@kestrel.example Tue Mar  4 09:30:00 2025\n{}\nFrom ops@kestrel.example Wed Mar  5 10:00:00 2025\n{}",
            message(1, "Delivery date", "The container reaches Gothenburg on 14 March.\n>From the port it is two days."),
            message(2, "=?utf-8?q?R=C3=A9union?=", "Agenda attached.")
        );
        let units = mbox(mbox_text.as_bytes(), &Readers::none());
        let got: Vec<(&str, &str, Option<u32>)> = units
            .iter()
            .map(|u| (u.loc.as_str(), u.heading.as_str(), u.group))
            .collect();
        assert_eq!(
            got,
            [
                ("msg 1", "Delivery date", Some(1)),
                ("msg 2", "Réunion", Some(2))
            ]
        );
        assert!(units[0].text.starts_with("From: Maria Lind <maria@kestrel.example>\nTo: ops@kestrel.example\nDate: 2025-03-04T09:30:00+01:00\nSubject: Delivery date\n\nThe container"), "{}", units[0].text);
        assert!(
            units[0].text.contains("\nFrom the port"),
            "mbox quoting undone: {}",
            units[0].text
        );
        assert_eq!(units[0].line_first, Some(1));
        assert_eq!(units[1].line_first, Some(units[0].line_last.unwrap() + 1));
    }

    #[test]
    fn readable_attachments_follow_their_message() {
        let raw = "From: a@x.example\r\nSubject: Invoice\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nPlease find it attached.\r\n--b\r\nContent-Type: text/csv; name=lines.csv\r\nContent-Disposition: attachment; filename=lines.csv\r\n\r\nItem,Price\r\nBearing,12\r\n--b\r\nContent-Type: image/png\r\nContent-Disposition: attachment; filename=logo.png\r\nContent-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n--b\r\nContent-Type: message/rfc822\r\n\r\nFrom: c@x.example\r\nSubject: Earlier note\r\n\r\nThe bearings were late.\r\n--b--\r\n";
        let units = eml(raw.as_bytes(), &Readers::none());
        let got: Vec<&str> = units.iter().map(|u| u.loc.as_str()).collect();
        assert_eq!(
            got,
            [
                "msg 1",
                "msg 1 · lines.csv",
                "msg 1 · attached message \"Earlier note\""
            ],
            "{units:#?}"
        );
        assert!(
            units[0]
                .text
                .contains("Attachments: lines.csv, logo.png, attached message \"Earlier note\""),
            "{}",
            units[0].text
        );
        assert_eq!(units[1].text, "Item, Price\nItem: Bearing, Price: 12");
        assert_eq!(units[1].heading, "Invoice > lines.csv > Item, Price");
        assert!(units[2].text.contains("The bearings were late."));
        assert!(units.iter().all(|u| u.group == Some(1)));
        let lines: Vec<(u32, u32)> = units
            .iter()
            .map(|u| (u.line_first.unwrap(), u.line_last.unwrap()))
            .collect();
        assert!(lines.windows(2).all(|w| w[1].0 == w[0].1 + 1), "{lines:?}");
    }

    #[test]
    fn an_eml_saved_from_a_mailbox_keeps_its_headers() {
        let raw = format!(
            "From maria@kestrel.example Tue Mar  4 09:30:00 2025\n{}",
            message(7, "Lead time", "Six weeks.")
        );
        let units = eml(raw.as_bytes(), &Readers::none());
        assert_eq!(units[0].heading, "Lead time");
        assert!(units[0].text.ends_with("Six weeks."));
    }
}
