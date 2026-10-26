# Signing and notarising the macOS builds

Prepared 2026-10-26 so that the macOS builds can ship signed with a **Developer ID** and **notarised** by Apple once there is an Apple Developer Program membership. The membership is to be an **organisation** enrolment of the company, not a personal account; the entity is still to be decided, so nothing is signed yet. Everything in the workflows is ready and strictly gated: until the six secrets of the `release` environment exist, every build is the unsigned preview it was. What remains is the owner's checklist below. The Ed25519 signature of `SHA256SUMS` (specs/DREAMFERENCE_RELEASE_SIGNING.md) is unchanged and independent of this.

## What the workflows do

`release.yml` passes `sign: true` to `build-clients.yml` and `mac-preview.yml`. Each then runs its build job in the `release` environment (the only place the secrets are; only `main` and `release/*` may use it), and `scripts/macos/signing_gate.sh` decides:

- **No Apple secret set:** the unsigned preview, step for step as before, with a warning in the run. A push to `clients/**` or `desktop/mac**` never asks for signing, so it never asks for the environment either.
- **Some set, not all:** the job fails, naming the missing ones (a half-configured identity must not ship an unsigned build unnoticed).
- **All six set:**
  - `build-clients.yml`, macOS legs: `import_identity.sh` puts the identity in a keychain of the job's own; `sign_binaries.sh` signs `ling`, `codex-code-mode-host`, `ling-search`, `ling-fetch` and `ling-code` with the hardened runtime and a secure timestamp, and checks each against `anchor apple generic and certificate leaf[subject.OU] = "<team>"`. That happens before the smoke and Seatbelt tests, so they run the signed binaries. Then the binaries go to the notary as one zip (a bare binary cannot carry a stapled ticket; Gatekeeper checks it online if it ever arrives quarantined), and only then `package_mightling.sh` gzips and sums them. The archives `ling update` and `install.sh` fetch are therefore the signed files.
  - `mac-preview.yml`: after Forge's `package` (whose postPackage fuse flip leaves an ad-hoc signature), `desktop/electron/mac/sign.mjs` runs `@electron/osx-sign` (pinned in `package.json`) over the whole bundle, inside out, with the entitlements of `mac/entitlements.mjs`. The hidden audit session then starts the **signed, hardened** app. Next the app is notarised (a `ditto` zip), its ticket stapled, `spctl -a -t exec` must accept it; the dmg is made from the stapled app, signed, notarised, stapled, and checked as a user's Mac would (`spctl -t open`, then the app on the mounted image). The uploaded dmg, which the release sums into `SHA256SUMS`, is that final file.
  - `release.yml`'s notes say "signed with an Apple Developer ID and notarised by Apple" instead of the Open Anyway steps (`MAC_SIGNED`, mac-preview's `signed` output).
- The keychain and the notary key are deleted at the end of the job (`remove_identity.sh`, `if: always()`).

`codesign` is given the identity's SHA-1, never its name, and `import_identity.sh` masks the certificate's name in the log: on a public repository the run logs are public, and the name is the enrolled account's.

**Entitlements, only what the hardened runtime needs** (`scripts/macos/entitlements/`):

| Code | Entitlements | Why |
|---|---|---|
| `ling`, `ling-search`, `ling-fetch`, `ling-code` | none | no JIT, no device; `ling` links no V8 (`codex-code-mode` has no V8 dependency) |
| `codex-code-mode-host` | `cs.allow-jit` | V8 with its JIT on (`V8JitMode::Enabled`, the default in `code-mode-runtime/src/v8_init.rs`); V8 maps its code space `MAP_JIT` on every Darwin build, Intel included (`platform-posix.cc` of v8 150.4.0), which the hardened runtime refuses without it. Not `allow-unsigned-executable-memory`. |
| `Mightling.app` and `Mightling Helper.app` | `cs.allow-jit`, `device.audio-input` | V8 in the main process; the microphone for dictation (`egress.ts` `grantsRequest`, audio only). `NSMicrophoneUsageDescription` is in `Info.plist` too (`forge.config.js`) |
| the Renderer, GPU and Plugin helpers | `cs.allow-jit` | V8 / Chromium's GPU process |
| frameworks, dylibs, crashpad, ShipIt | none | |

osx-sign's own defaults (camera, Bluetooth, USB, location, photos, and unsigned memory plus disabled library validation for the Plugin helper) are not used.

## The secrets

All six in **Settings → Environments → `release` → Environment secrets** (not repository secrets: a called workflow's job reads them through its own `environment`):

| Secret | Value |
|---|---|
| `APPLE_DEVELOPER_ID_P12` | the Developer ID Application certificate and its private key, as a `.p12`, base64 |
| `APPLE_DEVELOPER_ID_P12_PASSWORD` | the `.p12`'s export password |
| `APPLE_TEAM_ID` | the ten-character Team ID |
| `APPLE_API_KEY_ID` | the App Store Connect API key's Key ID |
| `APPLE_API_ISSUER_ID` | the API keys page's Issuer ID (a UUID) |
| `APPLE_API_KEY_P8` | the downloaded `AuthKey_<KeyID>.p8`, base64 |

The account's name, the team's name and the Team ID are owner data: they go into these secrets and nowhere in the repository.

## The owner's checklist

1. **Settle the legal entity.** A Developer ID certificate names the enrolled party, and so does every binary signed with it: anyone can read it (`codesign -dv`, Gatekeeper, the Finder's info on the app). **Never enrol with an individual (personal) account: it puts that person's legal name in every signature, on every release, for good.** An organisation enrolment shows the company's legal entity name exactly as registered (for example "Developer ID Application: <Entity name> (<Team ID>)"), so pick the entity before enrolling; renaming later means a new certificate.
2. **Get a D-U-N-S number** for that entity (free from Dun & Bradstreet through Apple's lookup at developer.apple.com/enroll/duns-lookup; allow a few days to two weeks). The legal name and address in the D-U-N-S record must match what is entered at enrolment.
3. **Enrol as an organisation** at developer.apple.com/programs/enroll with an Apple Account made for the company (99 USD a year). Apple verifies the entity and that the enrolling person may bind it (it may phone); wait for the approval mail. The enrolling person becomes the Account Holder, the only role that can create Developer ID certificates.
4. **Make the key and the request on the Mac mini** (`ssh mac`; macOS 12.3's own LibreSSL is enough, no Xcode tools; do not run `git` there, it pops up the tools dialog):
   ```bash
   ssh mac
   mkdir -m 700 ~/mightling-signing && cd ~/mightling-signing
   openssl genrsa -out developer-id.key 2048
   openssl req -new -key developer-id.key -out developer-id.csr -subj "/CN=Developer ID/C=US"
   ```
   Copy the request to the machine with the browser: `scp mac:mightling-signing/developer-id.csr .`
5. **Create the certificate** (Account Holder only): developer.apple.com → Certificates, IDs & Profiles → Certificates → **+** → **Developer ID Application** (G2 Sub-CA) → upload `developer-id.csr` → download `developerID_application.cer`.
6. **Make the `.p12` on the Mac mini**, with Apple's intermediate inside, so the runner can build the chain:
   ```bash
   scp developerID_application.cer mac:mightling-signing/
   ssh mac
   cd ~/mightling-signing
   curl -fsSLO https://www.apple.com/certificateauthority/DeveloperIDG2CA.cer
   openssl x509 -inform DER -in developerID_application.cer -out developer-id.pem
   openssl x509 -inform DER -in DeveloperIDG2CA.cer -out developer-id-g2.pem
   openssl x509 -in developer-id.pem -noout -subject     # OU= is the Team ID
   openssl pkcs12 -export -inkey developer-id.key -in developer-id.pem \
     -certfile developer-id-g2.pem -name "Developer ID Application" -out developer-id.p12
   ```
   It asks for an export password: that is `APPLE_DEVELOPER_ID_P12_PASSWORD`. (LibreSSL writes the 3DES `.p12` the runner's `security import` reads; with OpenSSL 3 elsewhere, add `-legacy`.)
7. **Create the API key**: appstoreconnect.apple.com → Users and Access → Integrations → App Store Connect API → Team Keys (the first time, the Account Holder requests access) → **+** → name `mightling-notary`, access **Developer** → **Download** `AuthKey_<KeyID>.p8` (offered once only). Note the Key ID and the Issuer ID shown above the list.
8. **Add the six secrets** from the GB10 (or any machine with `gh` signed in), so no value is typed on a command line:
   ```bash
   ssh mac 'base64 -i ~/mightling-signing/developer-id.p12' | gh secret set APPLE_DEVELOPER_ID_P12 --env release --repo dreamference/mightling
   base64 -w0 AuthKey_*.p8 | gh secret set APPLE_API_KEY_P8 --env release --repo dreamference/mightling
   for name in APPLE_DEVELOPER_ID_P12_PASSWORD APPLE_TEAM_ID APPLE_API_KEY_ID APPLE_API_ISSUER_ID; do
     gh secret set "$name" --env release --repo dreamference/mightling    # prompts for the value
   done
   ```
9. **Keep a backup** of `developer-id.p12` (with its password) and the `.p8` where the Ed25519 release key is kept (LastPass), then delete `~/mightling-signing` on the Mac mini and the downloaded `.p8` and `.cer`.
10. **Try it before a release** (optional, no release made): Actions → Mac preview → Run workflow on `main` with **sign** ticked. Its "Apple signing" step must say "Signing with the Developer ID", and the run must end green (it bundles the newest client artifacts; osx-sign re-signs the `ling` inside). The next release is then signed with nothing else to change.

After the first signed release: update `docs/desktop.md` (On a Mac: the Open Anyway steps and the keychain paragraph), DESKTOP_ELECTRON §10, and revisit `--use-mock-keychain` (`egress.ts`), which was there because every ad-hoc build asked for the keychain again.

**Renewals.** The certificate lasts five years; the membership one year (if it lapses, released builds keep opening, new ones cannot be signed). The API key does not expire; revoke it in App Store Connect if it leaks, and the certificate at developer.apple.com.

## Not verified yet

No signed run exists until the secrets do. From the code, not a run: that `allow-jit` alone lets `codex-code-mode-host` run a cell on Intel and Apple silicon (the V8 source says so; no CI step drives the host's protocol), that dictation works under the hardened runtime with the two microphone entries, and that osx-sign 2.7.1's per-file entitlements land as `mac/entitlements.mjs` says (`tests/test_macos_signing.py` checks the mapping; the signed run prints each one).
