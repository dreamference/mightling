# Signed releases

*Built on `release/signing` (2026-10-07), closing finding 8 of
[DREAMFERENCE_SECURITY_REVIEW_2026-10.md](./DREAMFERENCE_SECURITY_REVIEW_2026-10.md). Nothing has
been released with it yet: 1.5.0 is the first release that will be signed.*

## 1. What it protects against

Before this, a release's checksum files were published by the same job, in the same release, as
the files they checked. They caught a corrupted download. They did not catch a replaced one: anyone
who could write release assets could replace a binary and its checksum line together. That covers
a leaked token with `contents: write`, a compromised workflow run, and someone who takes over an
old repository name that 1.4.x installs still follow (finding 9).

Two things are added:

1. **A signature.** `SHA256SUMS` lists every file the release carries. The release job signs it
   with an Ed25519 key, and `ling update` and `install.sh` refuse to install from a release (1.5.0
   or later) whose signature is missing or does not verify against the public key built into them.
   Someone who can write release assets but does not hold the key cannot make a release a client
   will install.
2. **Build provenance.** Every file, `SHA256SUMS` and its signature included, gets a GitHub
   artifact attestation (`actions/attest-build-provenance`). The attestation records which
   workflow, commit and run built the file, and Sigstore's transparency log keeps a record of it.
   A person can check it with `gh attestation verify` (§6). The installers do not check
   attestations: that would need `gh` or a Sigstore client on every machine.

## 2. The chain of trust

```
release key (Ed25519, private half: the RELEASE_SIGNING_KEY secret and one offline copy)
  └─ SHA256SUMS.sig      ssh-keygen -Y sign -n mightling-release
       └─ SHA256SUMS     sha256 of every release file:
            ├─ ling-<target>.sha256sums ─┬─ ling-<target>.gz
            │                            ├─ codex-code-mode-host-<target>.gz
            │                            └─ ling-search / ling-fetch / ling-code-<target>.gz
            ├─ puffin-<target>.sha256sums (and the transition's puffin-*.gz, for 1.4.x)
            ├─ dreamference-<version>-py3-none-any.whl
            ├─ install.sh, the desktop bundles
            └─ release-key-transition.pub(.sig), during a key rotation (§5)
```

One signature covers everything, because each file is listed by a file that is signed or that a
signed file lists. Both clients check the per-target sums file against `SHA256SUMS` before
trusting any line in it. This is the step that stops someone from replacing the binaries together
with their sums file (a test does exactly that).

## 3. The format: OpenSSH signatures, and why not `openssl`

The signature is OpenSSH's file-signature format (SSHSIG; `PROTOCOL.sshsig` in OpenSSH), made with
`ssh-keygen -Y sign` and checked with `ssh-keygen -Y verify`. The key is an ordinary
`ssh-ed25519` key.

`openssl pkeyutl -verify -rawin` was the first suggestion. It works on Linux with OpenSSL 3, but
`/usr/bin/openssl` on macOS is LibreSSL, and LibreSSL's `pkeyutl` does not (to our knowledge) do
Ed25519. This was not tried on a Mac here. The installer would then need a second code path for
macOS, or a tool the user has to install (minisign, signify, gpg). Python's standard library has no
Ed25519 either.

`ssh-keygen -Y` is in OpenSSH 8.1 (2019) and later. Every current Linux distribution ships it, and
so does macOS, both as part of the base system. Windows 11 ships it as the optional OpenSSH Client
feature. That gives one tool, one format and one command on all three systems. The costs:

- **The installer needs `ssh-keygen`.** A minimal container image may not have it. install.sh then
  stops and names the package (`openssh-client`) instead of skipping the check. There is no
  opt-out: an opt-out a user can be talked into setting is the attack.
- **OpenSSH older than 8.1 cannot verify.** Ubuntu 18.04 (7.6) and the OpenSSH that shipped in
  Windows 10 (7.7) are too old. Ubuntu 20.04 and later, Debian 11 and later, and Windows 11 are
  fine.
- **The armored format has to be parsed in Rust.** `ling update` does not shell out. It reads the
  SSHSIG blob itself (`ling-rs/src/release_signature.rs`, about 200 lines) and verifies it with
  `ed25519-dalek` 2.2, which the Codex workspace already locks. A test checks the parser against a
  signature made by the real `ssh-keygen` 9.6, not only against signatures the test made itself.

Namespaces keep two kinds of signature apart. `mightling-release` signs checksum files, and
`mightling-release-key` signs a key transition (§5). A signature made for one is refused as the
other, by both verifiers.

## 4. Where each piece lives

| Piece | Where |
|---|---|
| Private key | `~/.config/dreamference/release-signing/release-ed25519.key` on the maintainer's machine (mode 0600, folder 0700), plus the repository secret `RELEASE_SIGNING_KEY`. **Keep one copy offline** (a password manager or an encrypted USB key); losing it means a rotation that clients cannot follow (§5) |
| Public key(s) | `ling-rs/release-signing.pub`, which `ling` compiles in (`include_str!`). install.sh repeats the lines in `RELEASE_KEYS`, and a test keeps the two identical |
| Signing | `.github/workflows/release.yml`, job `release`, step "Write and sign SHA256SUMS". The key is written to a 0600 temporary file only for the `ssh-keygen -Y sign` call. If the secret is missing, the release fails |
| Self-check | Same step: the job verifies its own signature against `release-signing.pub` (and a transition, if one is present) before publishing. A secret that does not match the published key stops the release, rather than shipping one no client would install |
| Attestation | Same job, `actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8` (v4.2.2), with `subject-checksums` over every file plus `SHA256SUMS` and its `.sig`. The job has `id-token: write` and `attestations: write`, and no other job does |
| Verification | `ling update` (`ling-rs/src/update.rs`, `release_signature.rs`) and `install.sh`. install.ps1 is on `windows/phase-0-1` and should use the same `ssh-keygen -Y verify` lines; Windows 11's `ssh-keygen.exe` has `-Y` |

The current key:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKIG5+J3RTa4AaoT0o2qIhVr7bGvaa+T5b84rDv31ptj mightling-release-2026-10
SHA256:6cvnF/4G2TaHAHsimf1FkDSqhCwh1nzFetXN7Ksb54g
```

**Recommended, not done:** move `RELEASE_SIGNING_KEY` from a repository secret to a `release`
environment that requires a reviewer and allows only `main`. As things stand, anyone who can push
a workflow to any branch can sign with the key.

## 5. Rotating the key

A rotation is planned in advance and done with the current key. The new key is published with a
signature by the current key, and every client accepts the new key on the strength of that
endorsement.

1. Make the new key offline: `ssh-keygen -t ed25519 -N "" -C mightling-release-<yyyy-mm> -f next.key`.
2. Endorse it with the current key:
   ```
   cp next.key.pub ling-rs/release-key-transition.pub
   ssh-keygen -Y sign -f ~/.config/dreamference/release-signing/release-ed25519.key \
       -n mightling-release-key ling-rs/release-key-transition.pub
   ```
   This writes `ling-rs/release-key-transition.pub.sig`. Commit both files.
3. Append the new key's line to `ling-rs/release-signing.pub` and to `RELEASE_KEYS` in install.sh.
   Keep the old line, because a client built now should trust both.
4. Replace the secret: `gh secret set RELEASE_SIGNING_KEY -R dreamference/mightling < next.key`.
   Move `next.key` to the key's folder and its offline copy.
5. From then on, the release job signs with the new key and attaches the two transition files.
   An older `ling`, which knows only the old key, checks that the transition is signed by the old
   key under `mightling-release-key`, then accepts `SHA256SUMS` signed by the new key. Its next
   update brings the new key in compiled.
6. Remove the old key and the transition files only once no supported install could still be
   relying on them. The files are small, so keep them for a long time. A `ling` that skipped the
   whole period has to be reinstalled with install.sh.

**What a rotation does not handle: a stolen key.** A thief can endorse a key of their own, and
every client that trusts the stolen key follows that endorsement. Revocation needs a channel the
thief does not control. Publish a release signed by the new key, with no transition, so that
clients which already updated stop trusting the old key. Announce the change outside GitHub, and
have everyone else reinstall with install.sh, fetched from the repository at a commit they trust.
For this reason the private key stays in the secret and on one machine, plus the offline copy, and
nowhere else.

## 6. Checking a release by hand

```
gh release download v1.5.0 -R dreamference/mightling -p SHA256SUMS -p SHA256SUMS.sig -p 'ling-aarch64-*'
echo 'mightling-release ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKIG5+J3RTa4AaoT0o2qIhVr7bGvaa+T5b84rDv31ptj' > allowed_signers
ssh-keygen -Y verify -f allowed_signers -I mightling-release -n mightling-release -s SHA256SUMS.sig < SHA256SUMS
sha256sum -c --ignore-missing SHA256SUMS
sha256sum -c --ignore-missing ling-aarch64-unknown-linux-gnu.sha256sums

# Provenance: built by this repository's release workflow, from which commit
gh attestation verify ling-aarch64-unknown-linux-gnu.gz -R dreamference/mightling \
    --signer-workflow dreamference/mightling/.github/workflows/release.yml
```

## 7. The cut-over: 1.4.x to 1.5.0

**1.4.x binaries do not verify signatures.** `puffin update` in 1.4.0 and 1.4.1 downloads the
transition assets (`puffin-<target>.gz`, `codex-code-mode-host-<target>.gz`,
`puffin-<target>.sha256sums`; RENAME_MIGHTLING §4) and checks them against that sums file only.
That last step is therefore exactly as strong as releases were before: TLS to GitHub, plus whoever
can write the release. Nothing shipped later can change code that is already installed.

From then on:

- The 1.5.0 `ling` that step installs has the key compiled in. Every later `ling update` verifies
  the signature, and refuses an unsigned release of 1.5.0 or later with "release vX is not signed
  … nothing was installed".
- install.sh from 1.5.0 on verifies any release that carries a signature, refuses an unsigned
  release of 1.5.0 or later, and still installs `--version 1.4.x` with a warning that the release
  predates signing.
- The transition assets are listed in the signed `SHA256SUMS`. Someone careful can check the 1.5.0
  release by hand (§6) before running `puffin update`, or afterwards reinstall with 1.5.0's
  install.sh, which verifies.

`SIGNED_SINCE = 1.5.0` is set in both clients. A release with a tag that does not parse as a
version is treated as new, so it must be signed. If 1.5.0 ships without a signature, for example
because the secret was removed, the release job fails before publishing.

## 8. Tests

- `tests/test_release_install.py` runs the real install.sh against the stand-in release server,
  with session keys made by `ssh-keygen` (the release key's private half is never used in tests).
  Cases: a signed release installs; an unsigned 1.5.0, 9.9.9 or `nightly` is refused; a missing
  `.sig` is refused; an unsigned 1.4.1 installs with the warning; `SHA256SUMS` changed after
  signing fails; a release signed by another key fails; binaries swapped together with their sums
  file fail against the signed `SHA256SUMS`; a key transition endorsed by the current key installs;
  a key endorsed only by itself, or endorsed under the checksums namespace, is refused. A further
  test keeps install.sh's `RELEASE_KEYS` equal to `ling-rs/release-signing.pub`.
- `cargo test -p ling-launcher` (in an export, never in `codex/`):
  - `release_signature::tests`: a real `ssh-keygen` signature verifies, plus tampered message,
    unknown key, wrong namespace, garbage, transitions accepted and refused, and the cut-over
    version.
  - `update::tests`: signed sums tie the target sums, a swapped target sums file is refused,
    another key is refused, rotation works, and the missing-signature decision.
- actionlint was not run: it is not installed here and nothing was installed for this. The
  workflow was checked as YAML and its `run:` blocks with `bash -n`.
