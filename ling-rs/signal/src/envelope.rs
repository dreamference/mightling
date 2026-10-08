//! What signal-cli's `receive` notification carries, reduced to what the bridge acts on
//! (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §5.1).

use serde_json::Value;

/// An attachment as signal-cli reports it; the file is in signal-cli's attachment folder.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Attachment {
    pub id: String,
    pub content_type: String,
    pub filename: Option<String>,
    pub size: Option<u64>,
}

impl Attachment {
    pub fn is_image(&self) -> bool {
        self.content_type.starts_with("image/")
    }
}

/// What an envelope is, for the bridge.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Body {
    /// A direct message to the bridge's account (dedicated mode).
    Direct { text: String, attachments: Vec<Attachment> },
    /// A message the account's owner sent to their own Note to Self from another device (linked mode).
    NoteToSelf { text: String, attachments: Vec<Attachment> },
    /// A message to a group: never acted on.
    Group,
    /// Anything else: receipts, typing, calls, stories, reactions, other sync messages.
    Other,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Envelope {
    pub source_uuid: Option<String>,
    pub source_number: Option<String>,
    pub source_device: Option<u64>,
    /// The sender's timestamp, milliseconds since the epoch; also the message's id in Signal.
    pub timestamp: u64,
    pub body: Body,
}

fn attachments(message: &Value) -> Vec<Attachment> {
    message
        .get("attachments")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .filter_map(|a| {
                    Some(Attachment {
                        id: a.get("id")?.as_str()?.to_string(),
                        content_type: a.get("contentType").and_then(Value::as_str).unwrap_or("application/octet-stream").to_string(),
                        filename: a.get("filename").and_then(Value::as_str).map(str::to_string),
                        size: a.get("size").and_then(Value::as_u64),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

fn is_group(message: &Value) -> bool {
    message.get("groupInfo").is_some_and(|g| !g.is_null()) || message.get("groupV2").is_some_and(|g| !g.is_null())
}

/// Reads one `receive` notification's `params`. `own_uuid` is the bridge's account, needed to tell
/// a Note to Self message (sent to oneself) from the owner's other sync messages.
pub fn parse(params: &Value, own_uuid: Option<&str>) -> Option<Envelope> {
    let envelope = params.get("envelope")?;
    let timestamp = envelope.get("timestamp").and_then(Value::as_u64)?;
    let source_uuid = envelope.get("sourceUuid").and_then(Value::as_str).map(str::to_string);
    let source_number = envelope.get("sourceNumber").and_then(Value::as_str).map(str::to_string);
    let source_device = envelope.get("sourceDevice").and_then(Value::as_u64);
    let body = if let Some(data) = envelope.get("dataMessage").filter(|d| !d.is_null()) {
        if is_group(data) {
            Body::Group
        } else if data.get("reaction").is_some_and(|r| !r.is_null()) || data.get("remoteDelete").is_some_and(|r| !r.is_null()) {
            Body::Other
        } else {
            let text = data.get("message").and_then(Value::as_str).unwrap_or_default().to_string();
            let attachments = attachments(data);
            if text.is_empty() && attachments.is_empty() { Body::Other } else { Body::Direct { text, attachments } }
        }
    } else if let Some(sent) = envelope.pointer("/syncMessage/sentMessage").filter(|s| !s.is_null()) {
        let to_self = own_uuid.is_some() && sent.get("destinationUuid").and_then(Value::as_str) == own_uuid;
        if is_group(sent) {
            Body::Group
        } else if to_self {
            let text = sent.get("message").and_then(Value::as_str).unwrap_or_default().to_string();
            let attachments = attachments(sent);
            if text.is_empty() && attachments.is_empty() { Body::Other } else { Body::NoteToSelf { text, attachments } }
        } else {
            Body::Other
        }
    } else {
        Body::Other
    };
    Some(Envelope { source_uuid, source_number, source_device, timestamp, body })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_direct_message_with_an_attachment() {
        let params = json!({"envelope": {
            "source": "+15550001", "sourceNumber": "+15550001", "sourceUuid": "aci-owner", "sourceDevice": 1,
            "timestamp": 1_760_000_000_000u64,
            "dataMessage": {"timestamp": 1_760_000_000_000u64, "message": "hello", "expiresInSeconds": 0,
                "attachments": [{"contentType": "image/jpeg", "filename": "a.jpg", "id": "abc.jpg", "size": 12}]}
        }, "account": "+15559999"});
        let e = parse(&params, Some("aci-bridge")).unwrap();
        assert_eq!(e.source_uuid.as_deref(), Some("aci-owner"));
        assert_eq!(e.timestamp, 1_760_000_000_000);
        let Body::Direct { text, attachments } = e.body else { panic!("{:?}", e.body) };
        assert_eq!(text, "hello");
        assert_eq!(attachments.len(), 1);
        assert!(attachments[0].is_image());
    }

    #[test]
    fn groups_receipts_and_reactions_are_not_messages() {
        let group = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "dataMessage": {"message": "hi", "groupInfo": {"groupId": "g"}}}});
        assert_eq!(parse(&group, None).unwrap().body, Body::Group);
        let receipt = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "receiptMessage": {"isRead": true}}});
        assert_eq!(parse(&receipt, None).unwrap().body, Body::Other);
        let reaction = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "dataMessage": {"reaction": {"emoji": "👍"}}}});
        assert_eq!(parse(&reaction, None).unwrap().body, Body::Other);
        let typing = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "typingMessage": {"action": "STARTED"}}});
        assert_eq!(parse(&typing, None).unwrap().body, Body::Other);
        assert!(parse(&json!({"nothing": 1}), None).is_none());
    }

    #[test]
    fn a_note_to_self_is_told_from_the_owners_other_sync_messages() {
        let to_self = json!({"envelope": {"sourceUuid": "me", "sourceDevice": 1, "timestamp": 5,
            "syncMessage": {"sentMessage": {"destinationUuid": "me", "message": "q?", "timestamp": 5}}}});
        assert_eq!(parse(&to_self, Some("me")).unwrap().body, Body::NoteToSelf { text: "q?".to_string(), attachments: vec![] });
        let to_friend = json!({"envelope": {"sourceUuid": "me", "timestamp": 6,
            "syncMessage": {"sentMessage": {"destinationUuid": "friend", "message": "private", "timestamp": 6}}}});
        assert_eq!(parse(&to_friend, Some("me")).unwrap().body, Body::Other);
    }
}
