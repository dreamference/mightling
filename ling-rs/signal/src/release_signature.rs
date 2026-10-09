//! The OpenPGP check of the signal-cli release (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §3, §17):
//! setup downloads the tarball and its detached `.asc`, checks the tarball against the pinned
//! SHA-256, then runs `ling-signal verify-signal-cli`, which checks the signature against the
//! maintainer's key embedded here. Both must pass before anything is unpacked as root.
//!
//! The policy is this module's, not the OpenPGP library's: the embedded key must have the pinned
//! fingerprint and valid self-signatures; the `.asc` must be one armored block holding exactly one
//! signature, of a binary document, made with SHA-256 or stronger, naming the pinned fingerprint as
//! its issuer (a 64-bit key id alone is not enough), and it must verify against the primary key
//! only, so a signing subkey slipped into the key file could not stand in for it. Anything else
//! fails closed.

use std::io::BufReader;
use std::io::Read;
use std::path::Path;

use pgp::composed::Deserializable;
use pgp::composed::DetachedSignature;
use pgp::composed::SignedPublicKey;
use pgp::crypto::hash::HashAlgorithm;
use pgp::packet::SignatureType;
use pgp::types::KeyDetails;

/// The signal-cli maintainer's signing key (AsamK), primary key fingerprint. Where it came from and
/// how it was cross-checked is in the spec, §17.
pub const SIGNAL_CLI_KEY_FINGERPRINT: &str = "FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570";
/// The same key, armored, as keys.openpgp.org serves it.
pub const SIGNAL_CLI_KEY: &str = include_str!("../keys/signal-cli-AsamK.asc");

/// Hashes a release signature may use.
const ACCEPTED_HASHES: &[HashAlgorithm] = &[HashAlgorithm::Sha256, HashAlgorithm::Sha384, HashAlgorithm::Sha512, HashAlgorithm::Sha3_256, HashAlgorithm::Sha3_512];

fn normalized(fingerprint: &str) -> String {
    fingerprint.chars().filter(|c| !c.is_whitespace()).collect::<String>().to_ascii_uppercase()
}

/// The armored key, checked: its primary fingerprint is `fingerprint` and its self-signatures hold.
fn pinned_key(key: &str, fingerprint: &str) -> Result<SignedPublicKey, String> {
    let (key, _) = SignedPublicKey::from_string(key).map_err(|err| format!("the embedded signing key does not parse: {err}"))?;
    let actual = format!("{:X}", key.primary_key.fingerprint());
    if actual != normalized(fingerprint) {
        return Err(format!("the embedded signing key is {actual}, not the pinned {}", normalized(fingerprint)));
    }
    key.verify_bindings().map_err(|err| format!("the embedded signing key's self-signatures do not verify: {err}"))?;
    Ok(key)
}

/// Checks a detached, armored `signature` over `data` against `key`, whose primary fingerprint must
/// be `fingerprint`. Ok only when every rule in the module comment holds.
pub fn verify_detached(data: impl Read, signature: &str, key: &str, fingerprint: &str) -> Result<(), String> {
    let key = pinned_key(key, fingerprint)?;
    let pinned = normalized(fingerprint);
    // One armored block and nothing after it: the parser reads the first block and would ignore
    // whatever follows.
    let text = signature.trim();
    if !text.starts_with("-----BEGIN PGP SIGNATURE-----") || !text.ends_with("-----END PGP SIGNATURE-----") || text.matches("-----BEGIN PGP").count() != 1 {
        return Err("the signature file is not one armored block".to_string());
    }
    let (signatures, _) = DetachedSignature::from_string_many(signature).map_err(|err| format!("the signature does not parse: {err}"))?;
    let signatures: Vec<DetachedSignature> = signatures.collect::<Result<_, _>>().map_err(|err| format!("the signature does not parse: {err}"))?;
    let [signature] = signatures.as_slice() else {
        return Err(format!("expected one signature, found {}", signatures.len()));
    };
    let signature = &signature.signature;
    if signature.typ() != Some(SignatureType::Binary) {
        return Err(format!("the signature is of type {:?}, not a binary document's", signature.typ()));
    }
    match signature.hash_alg() {
        Some(hash) if ACCEPTED_HASHES.contains(&hash) => {}
        other => return Err(format!("the signature uses {other:?}; SHA-256 or stronger is required")),
    }
    let issuers: Vec<String> = signature.issuer_fingerprint().iter().map(|f| format!("{f:X}")).collect();
    if issuers.is_empty() {
        return Err("the signature does not name its issuer's fingerprint".to_string());
    }
    if let Some(other) = issuers.iter().find(|issuer| **issuer != pinned) {
        return Err(format!("the signature is by {other}, not by the pinned key {pinned}"));
    }
    signature.verify(&key.primary_key, data).map_err(|err| {
        // The library's message can carry an assertion dump; its last clause says what failed.
        let err = err.to_string();
        let reason = err.rsplit(": ").next().unwrap_or("").trim();
        format!("the signature does not verify, so this is not the file that was signed ({reason})")
    })
}

/// `ling-signal verify-signal-cli --file TARBALL --signature ASC`: the setup step.
pub fn verify_signal_cli(tarball: &Path, signature: &Path) -> Result<(), String> {
    let asc = std::fs::read_to_string(signature).map_err(|err| format!("cannot read {}: {err}", signature.display()))?;
    let file = std::fs::File::open(tarball).map_err(|err| format!("cannot read {}: {err}", tarball.display()))?;
    verify_detached(BufReader::new(file), &asc, SIGNAL_CLI_KEY, SIGNAL_CLI_KEY_FINGERPRINT)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SIGNER_A: &str = include_str!("../testdata/signer-a.asc");
    const SIGNER_A_FPR: &str = "C3A6C56B4808EFF762BD620FD9F301A5FC3FA043";
    const SIGNER_B: &str = include_str!("../testdata/signer-b.asc");
    const SIGNER_B_FPR: &str = "9B87F6976EB5B02E07CB612CA316B70B3908EE70";
    const PAYLOAD: &[u8] = include_bytes!("../testdata/payload.txt");
    const SIG_BY_A: &str = include_str!("../testdata/payload.txt.a.asc");
    const SIG_BY_B: &str = include_str!("../testdata/payload.txt.b.asc");
    const SIG_BY_A_AND_B: &str = include_str!("../testdata/payload.txt.ab.asc");
    const SIG_BY_A_SHA1: &str = include_str!("../testdata/payload.txt.a-sha1.asc");
    const REAL_ASC: &str = include_str!("../testdata/signal-cli-0.14.9.tar.gz.asc");

    #[test]
    fn a_good_signature_by_the_pinned_key_passes() {
        assert_eq!(verify_detached(PAYLOAD, SIG_BY_A, SIGNER_A, SIGNER_A_FPR), Ok(()));
        // The pin is compared without spaces or case.
        assert_eq!(verify_detached(PAYLOAD, SIG_BY_A, SIGNER_A, "c3a6 c56b 4808 eff7 62bd  620f d9f3 01a5 fc3f a043"), Ok(()));
    }

    #[test]
    fn a_bad_signature_fails() {
        let mut altered = PAYLOAD.to_vec();
        altered[0] ^= 1;
        let err = verify_detached(altered.as_slice(), SIG_BY_A, SIGNER_A, SIGNER_A_FPR).unwrap_err();
        assert!(err.contains("does not verify"), "{err}");
        // A corrupted signature fails too, whichever check catches it.
        let corrupted = SIG_BY_A.replacen("iQ", "iR", 1);
        assert!(verify_detached(PAYLOAD, &corrupted, SIGNER_A, SIGNER_A_FPR).is_err());
        assert!(verify_detached(PAYLOAD, "", SIGNER_A, SIGNER_A_FPR).is_err());
        assert!(verify_detached(PAYLOAD, "not a signature", SIGNER_A, SIGNER_A_FPR).is_err());
    }

    #[test]
    fn a_signature_by_another_key_fails() {
        let err = verify_detached(PAYLOAD, SIG_BY_B, SIGNER_A, SIGNER_A_FPR).unwrap_err();
        assert!(err.contains(SIGNER_B_FPR) && err.contains("not by the pinned key"), "{err}");
        // B's key file under A's pin is refused before any signature is looked at.
        let err = verify_detached(PAYLOAD, SIG_BY_B, SIGNER_B, SIGNER_A_FPR).unwrap_err();
        assert!(err.contains("not the pinned"), "{err}");
        // Two signatures, A's and B's, in one block or in two: refused, not "one of them is good".
        let err = verify_detached(PAYLOAD, SIG_BY_A_AND_B, SIGNER_A, SIGNER_A_FPR).unwrap_err();
        assert!(err.contains("expected one signature, found 2"), "{err}");
        let err = verify_detached(PAYLOAD, &format!("{SIG_BY_A}\n{SIG_BY_B}"), SIGNER_A, SIGNER_A_FPR).unwrap_err();
        assert!(err.contains("one armored block"), "{err}");
        assert!(verify_detached(PAYLOAD, &format!("{SIG_BY_A}trailing"), SIGNER_A, SIGNER_A_FPR).is_err());
    }

    #[test]
    fn a_weak_hash_fails_even_when_the_signature_is_good() {
        let err = verify_detached(PAYLOAD, SIG_BY_A_SHA1, SIGNER_A, SIGNER_A_FPR).unwrap_err();
        assert!(err.contains("SHA-256 or stronger"), "{err}");
    }

    #[test]
    fn the_embedded_key_is_the_pinned_one_and_signed_the_pinned_release() {
        let key = pinned_key(SIGNAL_CLI_KEY, SIGNAL_CLI_KEY_FINGERPRINT).expect("the embedded key");
        assert_eq!(format!("{:X}", key.primary_key.fingerprint()), SIGNAL_CLI_KEY_FINGERPRINT);
        let (signature, _) = DetachedSignature::from_string(REAL_ASC).unwrap();
        let signature = signature.signature;
        assert_eq!(signature.typ(), Some(SignatureType::Binary));
        assert_eq!(signature.hash_alg(), Some(HashAlgorithm::Sha512));
        let issuers: Vec<String> = signature.issuer_fingerprint().iter().map(|f| format!("{f:X}")).collect();
        assert_eq!(issuers, vec![SIGNAL_CLI_KEY_FINGERPRINT.to_string()]);
        // Over anything but the real tarball it does not verify.
        let err = verify_detached(&b"not signal-cli"[..], REAL_ASC, SIGNAL_CLI_KEY, SIGNAL_CLI_KEY_FINGERPRINT).unwrap_err();
        assert!(err.contains("does not verify"), "{err}");
    }

    #[test]
    fn missing_files_fail() {
        assert!(verify_signal_cli(Path::new("/nonexistent/signal-cli.tar.gz"), Path::new("/nonexistent/signal-cli.tar.gz.asc")).is_err());
    }
}
