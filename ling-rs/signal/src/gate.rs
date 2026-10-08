//! Who may talk to Mightling (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §4.1 step 4, §5.1, §5.3).
//!
//! One owner, recorded by account id (ACI) and identity-key fingerprint. Everyone else is dropped
//! without a reply, and so is the owner's account once its identity key changes, until the owner
//! trusts the new key on the node. Pure logic: the caller supplies the owner's current fingerprint
//! from signal-cli's trust store and the time.

use std::collections::VecDeque;

use crate::envelope::Attachment;
use crate::envelope::Body;
use crate::envelope::Envelope;

/// Messages older than this are not acted on; the owner is told they arrived late.
pub const STALE_MS: u64 = 24 * 60 * 60 * 1000;
/// How many handled timestamps are remembered, to drop a message Signal delivers twice.
pub const SEEN_LIMIT: usize = 1000;
/// How long a pairing code from `ling signal setup` is good for.
pub const BINDING_MS: u64 = 10 * 60 * 1000;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Owner {
    pub aci: String,
    pub fingerprint: String,
}

/// The account mode (§4).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Mode {
    /// The bridge has its own number; the owner sends it direct messages.
    Dedicated,
    /// The bridge is a linked device of the owner's account; the owner writes to Note to Self.
    /// `own_device` is the bridge's device id, whose own replies come back as sync messages.
    Linked { own_device: u64 },
}

/// A message from the owner, ready to act on.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OwnerMessage {
    pub text: String,
    pub attachments: Vec<Attachment>,
    pub timestamp: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// From the owner: act on it.
    Accept(OwnerMessage),
    /// The pairing code arrived: this is the owner now.
    Bound(Owner),
    /// From the owner, but older than a day.
    Late { hours: u64 },
    /// The owner's identity key differs from the recorded one. Nothing is sent to it.
    IdentityChanged,
    /// A stranger, a group, a duplicate, or not a message at all.
    Ignored,
}

/// A pending pairing: the code and when it expires.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Binding {
    pub code: String,
    pub expires_ms: u64,
}

#[derive(Clone, Debug, Default)]
pub struct Gate {
    pub owner: Option<Owner>,
    pub binding: Option<Binding>,
    seen: VecDeque<u64>,
    /// Messages dropped because they were not the owner's (counted, never stored).
    pub ignored: u64,
    /// Messages dropped because the owner's identity key changed.
    pub identity_refusals: u64,
}

impl Gate {
    pub fn new(owner: Option<Owner>) -> Gate {
        Gate { owner, ..Gate::default() }
    }

    /// The remembered timestamps, oldest first, for the state file.
    pub fn seen(&self) -> Vec<u64> {
        self.seen.iter().copied().collect()
    }

    pub fn restore_seen(&mut self, seen: &[u64]) {
        self.seen = seen.iter().rev().take(SEEN_LIMIT).rev().copied().collect();
    }

    fn remember(&mut self, timestamp: u64) -> bool {
        if self.seen.contains(&timestamp) {
            return false;
        }
        self.seen.push_back(timestamp);
        while self.seen.len() > SEEN_LIMIT {
            self.seen.pop_front();
        }
        true
    }

    /// Decides what to do with one envelope. `fingerprint` is the sender's current identity-key
    /// fingerprint as signal-cli knows it (None when unknown).
    pub fn check(&mut self, envelope: &Envelope, mode: &Mode, fingerprint: Option<&str>, now_ms: u64) -> Verdict {
        let (text, attachments) = match (&envelope.body, mode) {
            (Body::Direct { text, attachments }, Mode::Dedicated) => (text, attachments),
            (Body::NoteToSelf { text, attachments }, Mode::Linked { own_device }) => {
                // The bridge's own replies come back to it as sync messages: never act on them.
                if envelope.source_device == Some(*own_device) {
                    return Verdict::Ignored;
                }
                (text, attachments)
            }
            _ => {
                if matches!(envelope.body, Body::Direct { .. } | Body::Group) {
                    self.ignored += 1;
                }
                return Verdict::Ignored;
            }
        };
        let Some(sender) = envelope.source_uuid.as_deref() else {
            self.ignored += 1;
            return Verdict::Ignored;
        };
        let Some(owner) = self.owner.clone() else {
            // No owner yet: only the pairing code, within its window, from anyone with a key.
            if let Some(binding) = &self.binding
                && now_ms <= binding.expires_ms
                && text.trim() == binding.code
                && let Some(fingerprint) = fingerprint
            {
                let owner = Owner { aci: sender.to_string(), fingerprint: fingerprint.to_string() };
                self.owner = Some(owner.clone());
                self.binding = None;
                self.remember(envelope.timestamp);
                return Verdict::Bound(owner);
            }
            self.ignored += 1;
            return Verdict::Ignored;
        };
        if sender != owner.aci {
            self.ignored += 1;
            return Verdict::Ignored;
        }
        if fingerprint != Some(owner.fingerprint.as_str()) {
            self.identity_refusals += 1;
            return Verdict::IdentityChanged;
        }
        if !self.remember(envelope.timestamp) {
            return Verdict::Ignored;
        }
        if now_ms > envelope.timestamp + STALE_MS {
            return Verdict::Late { hours: (now_ms - envelope.timestamp) / 3_600_000 };
        }
        Verdict::Accept(OwnerMessage { text: text.clone(), attachments: attachments.clone(), timestamp: envelope.timestamp })
    }
}

/// A fresh six-digit pairing code.
pub fn pairing_code() -> String {
    use rand::Rng;
    format!("{:06}", rand::rng().random_range(0..1_000_000u32))
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: u64 = 1_760_000_000_000;

    fn direct(from: &str, text: &str, timestamp: u64) -> Envelope {
        Envelope {
            source_uuid: Some(from.to_string()),
            source_number: Some("+15550001".to_string()),
            source_device: Some(1),
            timestamp,
            body: Body::Direct { text: text.to_string(), attachments: vec![] },
        }
    }

    fn owned() -> Gate {
        Gate::new(Some(Owner { aci: "owner".to_string(), fingerprint: "fp1".to_string() }))
    }

    #[test]
    fn the_owner_is_accepted_and_strangers_are_counted_not_answered() {
        let mut gate = owned();
        assert!(matches!(gate.check(&direct("owner", "hi", NOW), &Mode::Dedicated, Some("fp1"), NOW), Verdict::Accept(m) if m.text == "hi"));
        assert_eq!(gate.check(&direct("stranger", "hi", NOW + 1), &Mode::Dedicated, Some("fpX"), NOW), Verdict::Ignored);
        assert_eq!(gate.ignored, 1);
    }

    #[test]
    fn a_number_alone_is_not_the_owner() {
        let mut gate = owned();
        // Same phone number, different account: dropped.
        let mut envelope = direct("someone-else", "hi", NOW);
        envelope.source_number = Some("+15550001".to_string());
        assert_eq!(gate.check(&envelope, &Mode::Dedicated, Some("fp1"), NOW), Verdict::Ignored);
        envelope.source_uuid = None;
        assert_eq!(gate.check(&envelope, &Mode::Dedicated, Some("fp1"), NOW), Verdict::Ignored);
    }

    #[test]
    fn a_changed_identity_key_stops_the_owner() {
        let mut gate = owned();
        assert_eq!(gate.check(&direct("owner", "hi", NOW), &Mode::Dedicated, Some("fp2"), NOW), Verdict::IdentityChanged);
        assert_eq!(gate.check(&direct("owner", "hi", NOW + 1), &Mode::Dedicated, None, NOW), Verdict::IdentityChanged);
        assert_eq!(gate.identity_refusals, 2);
    }

    #[test]
    fn duplicates_are_dropped_and_late_messages_reported() {
        let mut gate = owned();
        assert!(matches!(gate.check(&direct("owner", "a", NOW), &Mode::Dedicated, Some("fp1"), NOW), Verdict::Accept(_)));
        assert_eq!(gate.check(&direct("owner", "a", NOW), &Mode::Dedicated, Some("fp1"), NOW), Verdict::Ignored);
        let old = NOW - 30 * 3_600_000;
        assert_eq!(gate.check(&direct("owner", "old", old), &Mode::Dedicated, Some("fp1"), NOW), Verdict::Late { hours: 30 });
    }

    #[test]
    fn the_seen_list_is_bounded_and_restorable() {
        let mut gate = owned();
        for t in 0..(SEEN_LIMIT as u64 + 10) {
            gate.check(&direct("owner", "x", NOW + t), &Mode::Dedicated, Some("fp1"), NOW + t);
        }
        assert_eq!(gate.seen().len(), SEEN_LIMIT);
        let mut restored = owned();
        restored.restore_seen(&gate.seen());
        assert_eq!(restored.check(&direct("owner", "x", NOW + 500), &Mode::Dedicated, Some("fp1"), NOW + 600), Verdict::Ignored);
    }

    #[test]
    fn the_pairing_code_binds_the_first_sender_within_its_window() {
        let mut gate = Gate::new(None);
        gate.binding = Some(Binding { code: "123456".to_string(), expires_ms: NOW + BINDING_MS });
        assert_eq!(gate.check(&direct("a", "hello", NOW), &Mode::Dedicated, Some("fa"), NOW), Verdict::Ignored);
        assert_eq!(gate.check(&direct("b", "123456", NOW + 1), &Mode::Dedicated, None, NOW), Verdict::Ignored, "no key, no binding");
        let bound = gate.check(&direct("b", " 123456 ", NOW + 2), &Mode::Dedicated, Some("fb"), NOW);
        assert_eq!(bound, Verdict::Bound(Owner { aci: "b".to_string(), fingerprint: "fb".to_string() }));
        assert_eq!(gate.check(&direct("c", "123456", NOW + 3), &Mode::Dedicated, Some("fc"), NOW), Verdict::Ignored);
        assert!(matches!(gate.check(&direct("b", "now a question", NOW + 4), &Mode::Dedicated, Some("fb"), NOW), Verdict::Accept(_)));
    }

    #[test]
    fn an_expired_code_binds_no_one() {
        let mut gate = Gate::new(None);
        gate.binding = Some(Binding { code: "654321".to_string(), expires_ms: NOW });
        assert_eq!(gate.check(&direct("a", "654321", NOW), &Mode::Dedicated, Some("fa"), NOW + 1), Verdict::Ignored);
        assert!(gate.owner.is_none());
    }

    #[test]
    fn groups_and_the_wrong_mode_are_dropped() {
        let mut gate = owned();
        let mut group = direct("owner", "hi", NOW);
        group.body = Body::Group;
        assert_eq!(gate.check(&group, &Mode::Dedicated, Some("fp1"), NOW), Verdict::Ignored);
        // A direct message to a linked bridge is someone writing to the owner: never acted on.
        assert_eq!(gate.check(&direct("owner", "hi", NOW + 1), &Mode::Linked { own_device: 3 }, Some("fp1"), NOW), Verdict::Ignored);
    }

    #[test]
    fn in_linked_mode_only_note_to_self_from_another_device_counts() {
        let mut gate = owned();
        let mut note = direct("owner", "q", NOW);
        note.body = Body::NoteToSelf { text: "q".to_string(), attachments: vec![] };
        assert!(matches!(gate.check(&note, &Mode::Linked { own_device: 3 }, Some("fp1"), NOW), Verdict::Accept(_)));
        let mut echo = note.clone();
        echo.timestamp = NOW + 1;
        echo.source_device = Some(3);
        assert_eq!(gate.check(&echo, &Mode::Linked { own_device: 3 }, Some("fp1"), NOW), Verdict::Ignored);
    }

    #[test]
    fn pairing_codes_have_six_digits() {
        for _ in 0..50 {
            let code = pairing_code();
            assert_eq!(code.len(), 6);
            assert!(code.chars().all(|c| c.is_ascii_digit()));
        }
    }
}
