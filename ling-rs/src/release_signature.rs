//! Release signatures: `ling update` refuses a release whose checksums are not signed by
//! Mightling's release key (specs/DREAMFERENCE_RELEASE_SIGNING.md).
//!
//! The release job writes `SHA256SUMS` (a checksum for every file the release carries, the
//! per-target `.sha256sums` files included) and signs it with `ssh-keygen -Y sign -n
//! mightling-release`, an Ed25519 signature in OpenSSH's own file-signature format (SSHSIG,
//! `PROTOCOL.sshsig` in OpenSSH). That format is what `ssh-keygen -Y verify` checks in
//! install.sh on Linux and macOS and in install.ps1 on Windows, so one signature serves every
//! installer, and this module verifies the same bytes here with `ed25519-dalek`, without
//! shelling out.
//!
//! The trust root is `release-signing.pub` beside this crate, compiled in. A signature by a key
//! that is not in it is accepted only through a key transition: the release carries the new key
//! (`release-key-transition.pub`) and that file signed by a key that is trusted, under its own
//! namespace, so a signature over checksums can never be mistaken for one over a key.

use anyhow::{bail, Context};
use base64::Engine;
use ed25519_dalek::{Signature, VerifyingKey};
use sha2::{Digest, Sha256, Sha512};

/// The keys this build trusts: one OpenSSH public key per line, `#` comments allowed.
pub const TRUSTED_KEYS: &str = include_str!("../release-signing.pub");
/// `ssh-keygen -Y sign -n` for `SHA256SUMS`.
pub const RELEASE_NAMESPACE: &str = "mightling-release";
/// `ssh-keygen -Y sign -n` for a key transition. A different namespace, so a checksums signature
/// can never pass as a key endorsement or the other way round.
pub const KEY_NAMESPACE: &str = "mightling-release-key";
/// The first release that is signed. Older releases cannot be signed after the fact; nothing
/// newer installs without a valid signature.
pub const SIGNED_SINCE: &str = "1.5.0";
pub const SUMS_ASSET: &str = "SHA256SUMS";
pub const SIGNATURE_ASSET: &str = "SHA256SUMS.sig";
pub const TRANSITION_KEY_ASSET: &str = "release-key-transition.pub";
pub const TRANSITION_SIGNATURE_ASSET: &str = "release-key-transition.pub.sig";

const MAGIC: &[u8] = b"SSHSIG";
const KEY_TYPE: &[u8] = b"ssh-ed25519";

/// Whether a release of this version must be signed. A version that does not parse is treated
/// as new: a tag nobody can read is no reason to skip the check.
pub fn signing_required(version: &str) -> bool {
    let since = semver::Version::parse(SIGNED_SINCE).expect("SIGNED_SINCE is a version");
    match semver::Version::parse(version.trim_start_matches('v')) {
        Ok(version) => version >= since,
        Err(_) => true,
    }
}

/// The Ed25519 keys of an OpenSSH public-key file: one key per non-empty, non-comment line.
pub fn parse_keys(text: &str) -> anyhow::Result<Vec<[u8; 32]>> {
    let keys = text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(parse_public_key)
        .collect::<anyhow::Result<Vec<_>>>()?;
    if keys.is_empty() {
        bail!("no release signing key in the file");
    }
    Ok(keys)
}

/// The key of one `ssh-ed25519 AAAA… comment` line.
pub fn parse_public_key(line: &str) -> anyhow::Result<[u8; 32]> {
    let mut fields = line.split_whitespace();
    let (Some(kind), Some(blob)) = (fields.next(), fields.next()) else {
        bail!("not an OpenSSH public key line: {line:?}");
    };
    if kind.as_bytes() != KEY_TYPE {
        bail!("release keys are ssh-ed25519, not {kind}");
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(blob)
        .context("the public key is not valid base64")?;
    let mut reader = Reader(&bytes);
    let key = wire_public_key(&mut reader)?;
    reader.done()?;
    Ok(key)
}

/// OpenSSH's fingerprint of a key, `SHA256:…`, as `ssh-keygen -l` prints it.
pub fn fingerprint(key: &[u8; 32]) -> String {
    let blob = wire(&[KEY_TYPE, key]);
    let digest = Sha256::digest(&blob);
    format!("SHA256:{}", base64::engine::general_purpose::STANDARD_NO_PAD.encode(digest))
}

/// A parsed SSHSIG signature.
#[derive(Debug)]
pub struct SshSig {
    pub key: [u8; 32],
    pub namespace: String,
    reserved: Vec<u8>,
    hash_algorithm: String,
    signature: [u8; 64],
}

/// Reads an armored `-----BEGIN SSH SIGNATURE-----` block.
pub fn parse_sshsig(armored: &str) -> anyhow::Result<SshSig> {
    let mut inside = false;
    let mut body = String::new();
    for line in armored.lines().map(str::trim) {
        match line {
            "-----BEGIN SSH SIGNATURE-----" => inside = true,
            "-----END SSH SIGNATURE-----" if inside => {
                return decode_sshsig(&body);
            }
            _ if inside => body.push_str(line),
            _ => {}
        }
    }
    bail!("not an SSH signature (no BEGIN/END SSH SIGNATURE block)")
}

fn decode_sshsig(body: &str) -> anyhow::Result<SshSig> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(body)
        .context("the signature is not valid base64")?;
    let mut reader = Reader(&bytes);
    if reader.take(MAGIC.len())? != MAGIC {
        bail!("not an SSH signature (bad preamble)");
    }
    if reader.u32()? != 1 {
        bail!("unsupported SSH signature version");
    }
    let key = {
        let blob = reader.string()?;
        let mut inner = Reader(blob);
        let key = wire_public_key(&mut inner)?;
        inner.done()?;
        key
    };
    let namespace = String::from_utf8(reader.string()?.to_vec()).context("bad namespace")?;
    let reserved = reader.string()?.to_vec();
    let hash_algorithm =
        String::from_utf8(reader.string()?.to_vec()).context("bad hash algorithm")?;
    let signature = {
        let blob = reader.string()?;
        let mut inner = Reader(blob);
        if inner.string()? != KEY_TYPE {
            bail!("release signatures are ssh-ed25519");
        }
        let signature: [u8; 64] = inner
            .string()?
            .try_into()
            .map_err(|_| anyhow::anyhow!("an Ed25519 signature is 64 bytes"))?;
        inner.done()?;
        signature
    };
    reader.done()?;
    Ok(SshSig { key, namespace, reserved, hash_algorithm, signature })
}

/// Checks that `signature` signs `message` under `namespace` with the key it names. Whether that
/// key is trusted is the caller's question.
pub fn check(message: &[u8], signature: &SshSig, namespace: &str) -> anyhow::Result<()> {
    if signature.namespace != namespace {
        bail!(
            "the signature is for {:?}, not {namespace:?}",
            signature.namespace
        );
    }
    let digest = match signature.hash_algorithm.as_str() {
        "sha512" => Sha512::digest(message).to_vec(),
        "sha256" => Sha256::digest(message).to_vec(),
        other => bail!("unsupported signature hash {other}"),
    };
    let signed = [
        MAGIC.to_vec(),
        wire(&[
            signature.namespace.as_bytes(),
            &signature.reserved,
            signature.hash_algorithm.as_bytes(),
            &digest,
        ]),
    ]
    .concat();
    VerifyingKey::from_bytes(&signature.key)
        .context("the signing key is not a valid Ed25519 key")?
        .verify_strict(&signed, &Signature::from_bytes(&signature.signature))
        .map_err(|_| anyhow::anyhow!("the signature does not match"))
}

/// Verifies a release's `SHA256SUMS` against its `SHA256SUMS.sig` with the `trusted` keys, or with
/// a new key the release's key transition endorses (`transition` is the transition's key file
/// and its signature, when the release carries them). Returns the fingerprint of the key that
/// signed.
pub fn verify_release(
    sums: &[u8],
    signature: &str,
    transition: Option<(&str, &str)>,
    trusted: &[[u8; 32]],
) -> anyhow::Result<String> {
    let signature = parse_sshsig(signature).context("SHA256SUMS.sig")?;
    if !trusted.contains(&signature.key) {
        let Some((key_file, key_signature)) = transition else {
            bail!(
                "SHA256SUMS is signed by {}, a key this ling does not trust, and the release \
                 carries no key transition",
                fingerprint(&signature.key)
            );
        };
        let endorsed = parse_keys(key_file).context(TRANSITION_KEY_ASSET)?;
        if !endorsed.contains(&signature.key) {
            bail!(
                "SHA256SUMS is signed by {}, which the release's key transition does not name",
                fingerprint(&signature.key)
            );
        }
        let endorsement = parse_sshsig(key_signature).context(TRANSITION_SIGNATURE_ASSET)?;
        if !trusted.contains(&endorsement.key) {
            bail!(
                "the release's key transition is signed by {}, a key this ling does not trust",
                fingerprint(&endorsement.key)
            );
        }
        check(key_file.as_bytes(), &endorsement, KEY_NAMESPACE)
            .context("the release's key transition")?;
    }
    check(sums, &signature, RELEASE_NAMESPACE).context("SHA256SUMS.sig")?;
    Ok(fingerprint(&signature.key))
}

fn wire_public_key(reader: &mut Reader) -> anyhow::Result<[u8; 32]> {
    if reader.string()? != KEY_TYPE {
        bail!("release keys are ssh-ed25519");
    }
    reader
        .string()?
        .try_into()
        .map_err(|_| anyhow::anyhow!("an Ed25519 public key is 32 bytes"))
}

/// SSH wire strings: each part prefixed by its length as a big-endian u32.
fn wire(parts: &[&[u8]]) -> Vec<u8> {
    let mut out = Vec::new();
    for part in parts {
        out.extend_from_slice(&(part.len() as u32).to_be_bytes());
        out.extend_from_slice(part);
    }
    out
}

struct Reader<'a>(&'a [u8]);

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> anyhow::Result<&'a [u8]> {
        if self.0.len() < n {
            bail!("the signature data ends early");
        }
        let (head, tail) = self.0.split_at(n);
        self.0 = tail;
        Ok(head)
    }

    fn u32(&mut self) -> anyhow::Result<u32> {
        Ok(u32::from_be_bytes(self.take(4)?.try_into().expect("four bytes")))
    }

    fn string(&mut self) -> anyhow::Result<&'a [u8]> {
        let len = self.u32()? as usize;
        self.take(len)
    }

    fn done(&self) -> anyhow::Result<()> {
        if self.0.is_empty() {
            Ok(())
        } else {
            bail!("unexpected bytes after the signature data")
        }
    }
}

/// Signing as the release job does, for tests here and in `update.rs`.
#[cfg(test)]
pub(crate) mod testing {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    /// Signs like `ssh-keygen -Y sign` does, for keys made in the test.
    pub(crate) fn sign(message: &[u8], key: &SigningKey, namespace: &str) -> String {
        let digest = Sha512::digest(message);
        let signed = [
            MAGIC.to_vec(),
            wire(&[namespace.as_bytes(), b"", b"sha512", &digest]),
        ]
        .concat();
        let signature = key.sign(&signed).to_bytes();
        let public = wire(&[KEY_TYPE, key.verifying_key().as_bytes()]);
        let blob = [
            MAGIC.to_vec(),
            1u32.to_be_bytes().to_vec(),
            wire(&[
                &public,
                namespace.as_bytes(),
                b"",
                b"sha512",
                &wire(&[KEY_TYPE, &signature]),
            ]),
        ]
        .concat();
        let encoded = base64::engine::general_purpose::STANDARD.encode(blob);
        format!("-----BEGIN SSH SIGNATURE-----\n{encoded}\n-----END SSH SIGNATURE-----\n")
    }

    pub(crate) fn key_line(key: &SigningKey) -> String {
        let blob = wire(&[KEY_TYPE, key.verifying_key().as_bytes()]);
        format!(
            "ssh-ed25519 {} test\n",
            base64::engine::general_purpose::STANDARD.encode(blob)
        )
    }
}

#[cfg(test)]
mod tests {
    use super::testing::{key_line, sign};
    use super::*;
    use ed25519_dalek::SigningKey;

    /// Made by the real `ssh-keygen -Y sign -n mightling-release` (OpenSSH 9.6) with a
    /// throwaway key, so the parser is checked against OpenSSH's own bytes.
    const FIXTURE_KEY: &str =
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP3mMP3X27Lendm7h+POOk3HZD4MOcIf5OWMcgvONVzp fixture";
    const FIXTURE_MESSAGE: &str = "abc123  ling-aarch64-unknown-linux-gnu.sha256sums\n";
    const FIXTURE_SIGNATURE: &str = "-----BEGIN SSH SIGNATURE-----
U1NIU0lHAAAAAQAAADMAAAALc3NoLWVkMjU1MTkAAAAg/eYw/dfbst6d2buH4846TcdkPg
w5wh/k5YxyC841XOkAAAARbWlnaHRsaW5nLXJlbGVhc2UAAAAAAAAABnNoYTUxMgAAAFMA
AAALc3NoLWVkMjU1MTkAAABAqQ7LXSE5TKMuqdDb3niY9lt3JTX4nLyll77rRTB2pbKZWX
sG2g6Bvl+r4PfEhlo5bmmdYGdJlbB7o0RHtXByAA==
-----END SSH SIGNATURE-----
";

    #[test]
    fn the_compiled_in_key_file_parses() {
        let keys = parse_keys(TRUSTED_KEYS).unwrap();
        assert_eq!(keys.len(), 1);
        assert!(fingerprint(&keys[0]).starts_with("SHA256:"));
    }

    #[test]
    fn a_signature_made_by_ssh_keygen_verifies() {
        let trusted = parse_keys(FIXTURE_KEY).unwrap();
        let signer =
            verify_release(FIXTURE_MESSAGE.as_bytes(), FIXTURE_SIGNATURE, None, &trusted).unwrap();
        assert_eq!(signer, fingerprint(&trusted[0]));
    }

    #[test]
    fn a_tampered_checksum_file_is_refused() {
        let trusted = parse_keys(FIXTURE_KEY).unwrap();
        let tampered = FIXTURE_MESSAGE.replace("abc123", "abc124");
        let error = verify_release(tampered.as_bytes(), FIXTURE_SIGNATURE, None, &trusted)
            .unwrap_err();
        assert!(format!("{error:#}").contains("does not match"));
    }

    #[test]
    fn a_signature_by_an_unknown_key_is_refused() {
        let attacker = SigningKey::from_bytes(&[9; 32]);
        let signature = sign(FIXTURE_MESSAGE.as_bytes(), &attacker, RELEASE_NAMESPACE);
        let trusted = parse_keys(FIXTURE_KEY).unwrap();
        let error =
            verify_release(FIXTURE_MESSAGE.as_bytes(), &signature, None, &trusted).unwrap_err();
        assert!(format!("{error:#}").contains("does not trust"));
    }

    #[test]
    fn a_signature_under_another_namespace_is_refused() {
        let key = SigningKey::from_bytes(&[1; 32]);
        let trusted = [key.verifying_key().to_bytes()];
        let signature = sign(FIXTURE_MESSAGE.as_bytes(), &key, KEY_NAMESPACE);
        let error =
            verify_release(FIXTURE_MESSAGE.as_bytes(), &signature, None, &trusted).unwrap_err();
        assert!(format!("{error:#}").contains("not \"mightling-release\""));
    }

    #[test]
    fn garbage_and_a_missing_block_are_refused() {
        let trusted = parse_keys(FIXTURE_KEY).unwrap();
        for bad in ["", "hello", "-----BEGIN SSH SIGNATURE-----\n!!!\n-----END SSH SIGNATURE-----"] {
            assert!(verify_release(b"x", bad, None, &trusted).is_err(), "{bad:?}");
        }
        assert!(parse_public_key("ssh-rsa AAAAB3NzaC1yc2E x").is_err());
        assert!(parse_keys("# only a comment\n").is_err());
    }

    #[test]
    fn a_new_key_is_trusted_through_a_transition_signed_by_a_trusted_key() {
        let old = SigningKey::from_bytes(&[1; 32]);
        let new = SigningKey::from_bytes(&[2; 32]);
        let trusted = [old.verifying_key().to_bytes()];
        let new_line = key_line(&new);
        let endorsement = sign(new_line.as_bytes(), &old, KEY_NAMESPACE);
        let sums = b"0123  ling-x.gz\n";
        let signature = sign(sums, &new, RELEASE_NAMESPACE);
        let signer =
            verify_release(sums, &signature, Some((&new_line, &endorsement)), &trusted).unwrap();
        assert_eq!(signer, fingerprint(&new.verifying_key().to_bytes()));
    }

    #[test]
    fn a_transition_that_is_not_signed_by_a_trusted_key_is_refused() {
        let old = SigningKey::from_bytes(&[1; 32]);
        let attacker = SigningKey::from_bytes(&[9; 32]);
        let trusted = [old.verifying_key().to_bytes()];
        let line = key_line(&attacker);
        let sums = b"0123  ling-x.gz\n";
        let signature = sign(sums, &attacker, RELEASE_NAMESPACE);
        // Endorsed by itself, then endorsed by the old key but for the checksums namespace.
        let self_endorsed = sign(line.as_bytes(), &attacker, KEY_NAMESPACE);
        assert!(verify_release(sums, &signature, Some((&line, &self_endorsed)), &trusted).is_err());
        let wrong_namespace = sign(line.as_bytes(), &old, RELEASE_NAMESPACE);
        assert!(
            verify_release(sums, &signature, Some((&line, &wrong_namespace)), &trusted).is_err()
        );
    }

    #[test]
    fn a_transition_that_names_another_key_does_not_help() {
        let old = SigningKey::from_bytes(&[1; 32]);
        let new = SigningKey::from_bytes(&[2; 32]);
        let attacker = SigningKey::from_bytes(&[9; 32]);
        let trusted = [old.verifying_key().to_bytes()];
        let new_line = key_line(&new);
        let endorsement = sign(new_line.as_bytes(), &old, KEY_NAMESPACE);
        let sums = b"0123  ling-x.gz\n";
        let signature = sign(sums, &attacker, RELEASE_NAMESPACE);
        let error = verify_release(sums, &signature, Some((&new_line, &endorsement)), &trusted)
            .unwrap_err();
        assert!(format!("{error:#}").contains("does not name"));
    }

    #[test]
    fn releases_from_the_cut_over_on_must_be_signed() {
        assert!(!signing_required("1.4.1"));
        assert!(!signing_required("v1.4.0"));
        assert!(signing_required("1.5.0"));
        assert!(signing_required("v1.6.2"));
        assert!(signing_required("not-a-version"));
    }
}
