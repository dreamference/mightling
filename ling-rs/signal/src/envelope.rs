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

/// The id of every attachment a `receive` notification names, wherever it sits (the message's own,
/// a quote's thumbnail, a link preview's image): each is a file signal-cli has downloaded into its
/// attachment folder, which the bridge deletes unless the owner sent it to be read (§19.1).
pub fn attachment_ids(params: &Value) -> Vec<String> {
    fn walk(value: &Value, ids: &mut Vec<String>) {
        match value {
            Value::Object(object) => {
                if let (Some(Value::String(id)), Some(Value::String(_))) = (object.get("id"), object.get("contentType")) {
                    ids.push(id.clone());
                }
                object.values().for_each(|v| walk(v, ids));
            }
            Value::Array(items) => items.iter().for_each(|v| walk(v, ids)),
            _ => {}
        }
    }
    let mut ids = Vec::new();
    walk(params, &mut ids);
    ids.sort();
    ids.dedup();
    ids
}

fn is_group(message: &Value) -> bool {
    message.get("groupInfo").is_some_and(|g| !g.is_null()) || message.get("groupV2").is_some_and(|g| !g.is_null())
}

/// The account signal-cli runs, as far as it is known: in linked mode, the owner's own account.
#[derive(Clone, Copy, Debug, Default)]
pub struct OwnAccount<'a> {
    pub uuid: Option<&'a str>,
    pub number: Option<&'a str>,
}

impl OwnAccount<'_> {
    /// Whether a sync message's destination is this account itself: Note to Self.
    fn is_destination(&self, sent: &Value) -> bool {
        let uuid = sent.get("destinationUuid").and_then(Value::as_str);
        let number = sent.get("destinationNumber").and_then(Value::as_str);
        (self.uuid.is_some() && uuid == self.uuid) || (self.number.is_some() && number == self.number)
    }
}

/// Reads one `receive` notification's `params`. `own` is the account signal-cli runs, needed to tell
/// a Note to Self message (sent to oneself) from the owner's messages to anyone else. signal-cli
/// writes a sync message's destination as `destinationNumber`/`destinationUuid` and the message's
/// own fields (`message`, `attachments`, `groupInfo`) beside them (its `JsonSyncDataMessage`).
pub fn parse(params: &Value, own: OwnAccount) -> Option<Envelope> {
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
        let to_self = own.is_destination(sent);
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

/// When Signal's server took the message from the sender and when it handed it to this device
/// (signal-cli's `serverReceivedTimestamp` and `serverDeliveredTimestamp`, milliseconds since the
/// epoch, on the server's clock), for the bridge's timing lines; either may be missing.
pub fn server_times(params: &Value) -> (Option<u64>, Option<u64>) {
    let envelope = params.get("envelope");
    let field = |name: &str| envelope.and_then(|e| e.get(name)).and_then(Value::as_u64).filter(|&ms| ms > 0);
    (field("serverReceivedTimestamp"), field("serverDeliveredTimestamp"))
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
        let e = parse(&params, OwnAccount { uuid: Some("aci-bridge"), number: None }).unwrap();
        assert_eq!(e.source_uuid.as_deref(), Some("aci-owner"));
        assert_eq!(e.timestamp, 1_760_000_000_000);
        let Body::Direct { text, attachments } = e.body else { panic!("{:?}", e.body) };
        assert_eq!(text, "hello");
        assert_eq!(attachments.len(), 1);
        assert!(attachments[0].is_image());
    }

    #[test]
    fn the_servers_times_are_read_when_signal_cli_gives_them() {
        let params = json!({"envelope": {"sourceUuid": "me", "timestamp": 5, "serverReceivedTimestamp": 7, "serverDeliveredTimestamp": 9,
            "syncMessage": {"sentMessage": {"destinationUuid": "me", "message": "q?", "timestamp": 5}}}});
        assert_eq!(server_times(&params), (Some(7), Some(9)));
        assert_eq!(server_times(&json!({"envelope": {"timestamp": 5, "serverDeliveredTimestamp": 0}})), (None, None));
    }

    #[test]
    fn every_downloaded_attachment_is_named_wherever_it_sits() {
        let params = json!({"envelope": {"sourceUuid": "friend", "timestamp": 3, "dataMessage": {
            "message": "look", "attachments": [{"contentType": "image/jpeg", "id": "a.jpg"}],
            "quote": {"id": 1, "attachments": [{"contentType": "image/png", "thumbnail": {"contentType": "image/png", "id": "t.png"}}]},
            "previews": [{"url": "https://example.com", "image": {"contentType": "image/webp", "id": "p.webp"}}]
        }}});
        assert_eq!(attachment_ids(&params), ["a.jpg", "p.webp", "t.png"]);
        assert!(attachment_ids(&json!({"envelope": {"timestamp": 1, "dataMessage": {"message": "hi"}}})).is_empty());
    }

    #[test]
    fn groups_receipts_and_reactions_are_not_messages() {
        let group = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "dataMessage": {"message": "hi", "groupInfo": {"groupId": "g"}}}});
        assert_eq!(parse(&group, OwnAccount::default()).unwrap().body, Body::Group);
        let receipt = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "receiptMessage": {"isRead": true}}});
        assert_eq!(parse(&receipt, OwnAccount::default()).unwrap().body, Body::Other);
        let reaction = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "dataMessage": {"reaction": {"emoji": "👍"}}}});
        assert_eq!(parse(&reaction, OwnAccount::default()).unwrap().body, Body::Other);
        let typing = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "typingMessage": {"action": "STARTED"}}});
        assert_eq!(parse(&typing, OwnAccount::default()).unwrap().body, Body::Other);
        assert!(parse(&json!({"nothing": 1}), OwnAccount::default()).is_none());
    }

    #[test]
    fn a_note_to_self_is_told_from_the_owners_other_sync_messages() {
        let to_self = json!({"envelope": {"sourceUuid": "me", "sourceDevice": 1, "timestamp": 5,
            "syncMessage": {"sentMessage": {"destinationUuid": "me", "message": "q?", "timestamp": 5}}}});
        let me = OwnAccount { uuid: Some("me"), number: Some("+15550000") };
        assert_eq!(parse(&to_self, me).unwrap().body, Body::NoteToSelf { text: "q?".to_string(), attachments: vec![] });
        // The number alone is enough when the account id is not known yet.
        let by_number = json!({"envelope": {"sourceUuid": "me", "sourceDevice": 1, "timestamp": 7,
            "syncMessage": {"sentMessage": {"destinationNumber": "+15550000", "message": "q2", "timestamp": 7}}}});
        assert!(matches!(parse(&by_number, OwnAccount { uuid: None, number: Some("+15550000") }).unwrap().body, Body::NoteToSelf { .. }));
        let to_friend = json!({"envelope": {"sourceUuid": "me", "timestamp": 6,
            "syncMessage": {"sentMessage": {"destinationUuid": "friend", "destinationNumber": "+15551111", "message": "private", "timestamp": 6}}}});
        assert_eq!(parse(&to_friend, me).unwrap().body, Body::Other);
        // A message to a group the owner is in, sent from the phone: not Note to Self.
        let to_group = json!({"envelope": {"sourceUuid": "me", "timestamp": 8,
            "syncMessage": {"sentMessage": {"message": "hi all", "groupInfo": {"groupId": "g"}, "timestamp": 8}}}});
        assert_eq!(parse(&to_group, me).unwrap().body, Body::Group);
        // Someone else's message to the owner reaches a linked device as a data message: Direct,
        // which the gate never acts on in linked mode.
        let incoming = json!({"envelope": {"sourceUuid": "friend", "timestamp": 9, "dataMessage": {"message": "hey"}}});
        assert!(matches!(parse(&incoming, me).unwrap().body, Body::Direct { .. }));
    }

    #[test]
    fn a_half_of_the_account_that_is_not_known_matches_no_destination() {
        // The account id is not known yet and the message to a friend names them by number only:
        // the two missing ids are not a match.
        let by_number = json!({"envelope": {"sourceUuid": "me", "timestamp": 6,
            "syncMessage": {"sentMessage": {"destinationNumber": "+15551111", "message": "private", "timestamp": 6}}}});
        assert_eq!(parse(&by_number, OwnAccount { uuid: None, number: Some("+15550000") }).unwrap().body, Body::Other);
        // The number is not known and the message names the friend by id only.
        let by_uuid = json!({"envelope": {"sourceUuid": "me", "timestamp": 7,
            "syncMessage": {"sentMessage": {"destinationUuid": "friend", "message": "private", "timestamp": 7}}}});
        assert_eq!(parse(&by_uuid, OwnAccount { uuid: Some("me"), number: None }).unwrap().body, Body::Other);
    }

    #[test]
    fn an_empty_note_to_self_is_not_a_question() {
        // A sticker or an edit synced from the phone: no text and no attachment.
        let empty = json!({"envelope": {"sourceUuid": "me", "sourceDevice": 1, "timestamp": 5,
            "syncMessage": {"sentMessage": {"destinationUuid": "me", "timestamp": 5}}}});
        assert_eq!(parse(&empty, OwnAccount { uuid: Some("me"), number: None }).unwrap().body, Body::Other);
    }

    #[test]
    fn a_v2_group_is_a_group_and_a_null_group_is_none() {
        let me = OwnAccount { uuid: Some("me"), number: None };
        let v2 = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "dataMessage": {"message": "hi", "groupV2": {"id": "g"}}}});
        assert_eq!(parse(&v2, me).unwrap().body, Body::Group);
        let v2_synced = json!({"envelope": {"sourceUuid": "me", "timestamp": 2,
            "syncMessage": {"sentMessage": {"destinationUuid": "me", "message": "hi all", "groupV2": {"id": "g"}, "timestamp": 2}}}});
        assert_eq!(parse(&v2_synced, me).unwrap().body, Body::Group);
        let no_group = json!({"envelope": {"sourceUuid": "x", "timestamp": 3, "dataMessage": {"message": "hi", "groupInfo": null, "groupV2": null}}});
        assert!(matches!(parse(&no_group, me).unwrap().body, Body::Direct { .. }));
    }

    #[test]
    fn a_reaction_or_a_deletion_is_never_a_message_even_with_text_beside_it() {
        let me = OwnAccount { uuid: Some("me"), number: None };
        let reaction = json!({"envelope": {"sourceUuid": "x", "timestamp": 1, "dataMessage": {"message": "rm -rf", "reaction": {"emoji": "👍"}}}});
        assert_eq!(parse(&reaction, me).unwrap().body, Body::Other);
        let deletion = json!({"envelope": {"sourceUuid": "x", "timestamp": 2, "dataMessage": {"message": "rm -rf", "remoteDelete": {"timestamp": 1}}}});
        assert_eq!(parse(&deletion, me).unwrap().body, Body::Other);
    }
}
