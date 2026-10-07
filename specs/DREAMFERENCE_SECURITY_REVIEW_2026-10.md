# Security review, October 2026

**Status:** review of `main` at `61d00d5` (2026-10-07), the day after the repository became public,
with fixes on branch `security/review-1`. Read from an attacker's side: what a stranger on the
internet, a page in the user's browser, a machine on the same network, or a paired machine can
reach. Findings that are fixed name the change; findings that are documented or accepted say why.
Anything serious that is not yet fixed is tracked privately and is not described here.

The scope, in the order reviewed: the installers and `ling update`, the release workflow, the node
over SSH (pairing, `serve-job`, jobs), what listens on the network, the agent's untrusted-content
handling, and secrets on disk. The rename branch's migration code (`rename/mightling`) was read as
well, because it runs on every upgrade.

## Findings

| # | Severity | Area | Finding | Status |
|---|---|---|---|---|
| 1 | Medium | `install.sh`, `ling update` | A GitHub token was sent with every request, including to whatever download URL the release metadata named, and a logged-in `gh`'s token was used even for the public repository | Fixed |
| 2 | Medium | Release workflow | Every job held write permission, and actions (two of them third-party) were referenced by movable tags | Fixed |
| 3 | Low | `install.sh` | The node's Python wheel was installed without a checksum check | Fixed (from the next release) |
| 4 | Medium | Google service (port 8767) | Three state-changing POSTs took no shared secret and no origin check, so another website, or a DNS-rebinding page, could call them from the user's browser | Fixed |
| 5 | Medium | Agent, untrusted content | The `<untrusted>` wrapper around email and Drive text only defused one exact spelling of its closing tag; fetched web pages and search results carried no "this is untrusted" notice at all | Fixed |
| 6 | Low | Node discovery | A second advert claiming the remembered node's id, at another address, could be followed with only a note | Fixed |
| 7 | Low | Node pairing | The paired key's `authorized_keys` line named each forbidden capability instead of using `restrict` | Fixed |
| 8 | Medium | Releases | Releases are not signed: the checksum files come from the same release they check | Fixed on `release/signing` (from 1.5.0; [RELEASE_SIGNING](./DREAMFERENCE_RELEASE_SIGNING.md)) |
| 9 | Medium | Repository renames | Installed 1.4.x binaries look for releases under older repository names that now redirect | Documented (operational rule) |
| 10 | — | Model server (port 8000) | Answers anyone on the local network, with no key | Accepted by design |
| 11 | Low | Node discovery | The first node found on a network is trusted on first use | Accepted (trusted-LAN premise) |
| 12 | Low | Rename migration | Two files are rewritten in place rather than atomically; the project's config file in the working directory is rewritten too | Recommendation for `rename/mightling` |

### 1. The GitHub token followed download URLs (fixed)

**Scenario.** The release JSON names each asset's download URL. Both the installer and `ling
update` sent the user's token (from `GH_TOKEN`/`GITHUB_TOKEN`, or `gh auth token`, which usually
has wide scopes) along with every download. Whoever controls the release metadata (a tampered
release, or an old repository name taken over, see 9) would have received the token as well as
serving the binary.

**Fix.** A token is only sent to `https://api.github.com/` (`update.rs::sends_token`; the `fetch`
function in `install.sh` only for URLs under the API root). A logged-in `gh`'s token is used only
when `MIGHTLING_RELEASE_REPO` names another repository (a private fork); the public repository is read
anonymously. Tests: `update.rs` `the_token_goes_to_the_github_api_and_nowhere_else`;
`tests/test_release_install.py::test_the_token_is_not_sent_to_download_urls_on_another_host`, which
fails on the old `install.sh`.

### 2. Release workflow permissions and action pinning (fixed)

**Scenario.** `release.yml` granted `contents: write` to every job, and referenced
`dtolnay/rust-toolchain@stable`, `Swatinem/rust-cache@v2` and the `actions/*` steps by tag. A
moved or compromised tag would have run with a token able to publish release assets, which every
install and update then trusts.

**Fix.** `contents: read` by default, `contents: write` only for the `release` job. Every action in
`release.yml` and `docs.yml` is pinned to a commit, with its tag in a comment. The workflow is
`workflow_dispatch` only (only people with write access can run it), and its inputs reach shell
code through environment variables or as validated booleans; that part needed no change.
Workflows added on other branches (`windows.yml`, `build-clients.yml`) should follow the same rule
when they merge.

### 3. The wheel had no checksum check (fixed for new releases)

The binaries are checked against the per-target sums file; the `dreamference` wheel that a node
installs with pip was not. The release job now writes a release-wide `SHA256SUMS` covering every
file it publishes, and `install.sh` checks the wheel against it, refusing a mismatch before any
virtualenv is created. Releases made before this (1.4.x) have no such file: the installer says so
instead of skipping the check silently. Test:
`test_a_wheel_that_does_not_match_the_release_sums_is_not_installed`.

### 4. Cross-site requests to the Google service (fixed)

The service publishes on `127.0.0.1:8767`. Searching and reading mail need its shared secret, but
`/disconnect` and the two OAuth steps (`/api/google/oauth/start`, `/complete`) did not, because the
pages that call them cannot hold a secret.

**Scenario.** A page in the user's browser could disconnect a connected account. With DNS
rebinding (a hostname that resolves to 127.0.0.1), a page could also complete the OAuth flow with
its author's own Google account, putting attacker-written mail in reach of the agent: a prompt
injection channel.

**Fix.** `GmailSearchService.post_refusal`: a POST whose `Origin` is not the web chat's or the
service's own is refused (403). Programs (the launcher, `ling-admin`) send no `Origin` and are
unaffected; a browser always sends one with a cross-origin or rebinding POST. Tests:
`tests/test_google_service_origin.py`, which runs the real service on a free port. The fix reaches
a running installation when the service's copy is refreshed (`ling-admin ling configure` or
`ling-admin google start`).

### 5. Untrusted content (fixed)

Email and Drive results reach the model inside `<untrusted …>` blocks (`ling-rs/apps/src/mcp.rs`).
The text was only cleaned of the exact string `</untrusted>`: a capitalised or spaced closing tag,
one inside a list-valued field (serialised as JSON), or a forged opening tag survived, letting a
sender end the block and write text that looks like Mightling's own. `neutralize` now defuses any
`untrusted` tag in any case or spacing, in the whole block and in the id attribute. Test:
`every_spelling_of_the_untrusted_tag_is_defused`.

The web commands' prompt section said nothing about trust, although the email section does. It now
says that search results and fetched pages are third-party data, never instructions, and that no
private data goes into a query or URL. This is a change to the system prompt (`WEB_ACCESS_INSTRUCTIONS`),
so benchmark runs before and after it are not strictly comparable.

### 6. Node identity on the network (fixed)

A remembered node is found again by the id in its mDNS advert, and the address is allowed to change
(a node that moves is real). An advert's id is only a claim. With two adverts claiming the
remembered id, the launcher now uses the one at the remembered address; if neither is there, it
asks (`ling-rs/src/node.rs`), and the desktop app keeps the remembered address
(`desktop/src-tauri/src/discover.rs`). One claimant at a new address is still followed with a note,
which is the documented behaviour for a node that has moved (see 11).

### 7. `restrict` on paired keys (fixed)

`authorized_keys` lines written by `node add` now start with `restrict` (OpenSSH 7.2+), which also
turns off tunnel forwarding and anything a future OpenSSH adds; the named options stay for
readers. Lines written before keep their old options until the pairing is renewed.

### 8. Unsigned releases (fixed on `release/signing`)

The checksum files protect against corrupted or swapped downloads, not against a compromised
release: they are published by the same workflow, in the same release. Recommended next step:
GitHub artifact attestations (`actions/attest-build-provenance` in the release job, verifiable with
`gh attestation verify`), or a signing key held outside GitHub, checked by `ling update` and
`install.sh`.

Both were done on `release/signing`
([DREAMFERENCE_RELEASE_SIGNING.md](./DREAMFERENCE_RELEASE_SIGNING.md)). The release job signs
`SHA256SUMS`, which lists every file, with an Ed25519 key (OpenSSH signature format). `ling update`
and `install.sh` refuse any release of 1.5.0 or later whose signature is missing or does not verify
against the key compiled into them. Every file also gets a build-provenance attestation. 1.4.x
binaries cannot verify a signature, so the step from 1.4.x to 1.5.0 is no stronger than before
(§7 there). The key is a repository secret, so the review's other option, a key held outside
GitHub, applies only partly. Restricting the secret to a reviewed `release` environment is
recommended there.

### 9. Old repository names (operational rule)

Installed binaries look for releases at the name they were built with: 1.4.0 at
`dgxcoder/dgxcoder`, 1.4.1 at `dreamference/puffin`. GitHub redirects renamed repositories only
while nobody creates a repository at the old name, and a user account's repositories stop
redirecting if the account is renamed and its old name is claimed by someone else. Rules:

- never create a repository named `ling`, `mightling-ai` or `dgx-lunny` in the `dreamference`
  organisation, or `dgxcoder` under the `dgxcoder` account;
- never rename or delete the `dgxcoder` account;
- keep at least one release published at the current name that old binaries can update to.

### 10. The model server on port 8000 (accepted)

By design, the model server listens on every interface with no key, because the web chat and
OpenHands reach it through Docker's bridge and the local network is assumed trusted. This is
stated in `docs/privacy.md`, with the firewall rule to apply on an untrusted network. Not changed.

### 11. Trust on first use (accepted)

With no remembered node, a single node found on the network is used and remembered. On a hostile
network that is the moment of exposure; `MIGHTLING_NODE`/`vllm_host` and `ling node use` pin a node
explicitly. Consistent with the trusted-LAN premise above.

### 12. The rename migration (recommendations for `rename/mightling`)

Read on the branch, not changed here:

- `LegacyNameMigration.rewrite_authorized_keys` rewrites `~/.ssh/authorized_keys` with
  `write_text`. An interruption mid-write can truncate the file and lock the owner out of their own
  SSH keys; write a staging file, `chmod 600`, then `os.replace`, as `NodeServe.authorize` does. It
  could also add `restrict` to the lines it rewrites (7).
- `rename::migrate` rewrites `puffin_*` keys in the working directory's `dreamference.toml`, which
  can be a tracked file in someone else's repository; rewriting only user-level files, and saying
  that a project file still uses the old keys (as `stale_project_config` already does for the
  node), would surprise no one.
- The new home is created with mode 700 and copies skip the sign-in, the installation id and the
  logs; links are copied as links, never followed. No issue found there.

## Not changed and checked

- **`serve-job`:** operations are an allow-list; model keys, job ids, git paths and `--out` are
  checked against strict patterns before use; binds must resolve inside the node owner's
  `[node] bindable`; jobs run in bubblewrap with the home folder, `/run`, `/tmp` and the GPU hidden,
  and with no network at `/airgapped on`.
- **Tokens on disk:** the Google tokens, the key file and the service secret are mode 600 in a
  700 directory.
- **Secrets in the history:** a full-history secret scan before the repository went public found
  only the GNOME desktop's published OAuth client id and secret.
