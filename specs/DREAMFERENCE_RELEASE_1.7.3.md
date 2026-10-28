# Mightling 1.7.3 — release notes

**Status:** prepared 2026-10-28 on the branch `release/1.7.3`: main at `7f58ec44` plus the version set
to 1.7.3 and these notes, to be cut through the release workflow from that branch on 2026-10-29
around 11:00 UTC, after night 18. Built from the merges in `v1.7.2..main`. The text between the two
rules is the GitHub release's description: the release workflow publishes it as it stands
(`scripts/release_notes.py`).

**Version.** Set to 1.7.3 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo`,
`desktop/electron/package.json` and its `package-lock.json`, the delegation plugin's manifest and
marketplace entry and `ling delegate mcp`'s `serverInfo` (`ling-rs/delegate/src/mcp.rs`), the same
files as 1.7.2.

**The engine.** SGLang stays the default and ling-serve stays opt-in. Variant A (this branch):
the engine pin is unchanged, ling-engine 8d112d0 and the image of 1.7.2. Variant B (when night 18 is
clean): ling-engine pinned exactly at `72c2a6a` with a clean rebuild of its image, applied on top of
this branch by the pin commit (`QWEN38_LING_ENGINE_COMMIT`, `QWEN38_LING_IMAGE`, the engine test, the
developer notes, and the engine paragraph below). Exactly one variant is cut.

**Checked before the cut:** see the release dry run recorded in the cut's log; `windows.yml` green on
the tree; no benchmark entry for 1.7.3 (an entry is optional; the Benchmarks page shows no number for
a release without one).

---

**Install and update.** `install.sh` reads the release API's answer whether GitHub sends it compact
or pretty-printed (an answer on one line was not understood), and when GitHub's rate limit
refuses it, the installer now says so instead of failing without a reason.

**Remote access.** The relay now has its own public certificate under its own name, so a phone that
reaches the node through the relay needs nothing of ours installed. The node's daily renewal renews
the control plane's certificate and the relay's, and alerts you if either fails. Android's app no
longer needs Mightling's CA to manage the node. A machine at another site joins your private network
with one line (`ling-admin remote code --join`, with `--ttl` up to 24 hours and a single-use key), and
`ling-admin node provision` works by the machine's network name, with no local-network discovery.
`ling-admin node power-cycle` drives a smart plug for a node that has hung.

**Model downloads for a new node.** `ling-admin node provision --model-source hf` has the node
download the model's public weights itself and check every file by SHA-256; only what the Hub does
not hold is sent from your machine over the network. The default, `copy`, is unchanged.

**Telegram's Mini App.** The Mini App gets a phone layout: Telegram's back button closes the top
layer, the page respects the phone's safe area, and the narrow view lists a thread's files. A phone
Work thread can be given Full Access to its repository, only when the node's owner turned that on
with a typed yes and the page's user types `FULL ACCESS <repo>`; it is never available from a
messenger bridge or at `/airgapped on`, and every repository turn now carries an explicit sandbox
from your own settings. The node also posts notices about a repository to Telegram (one topic per
repository, fixed wording and an Open button), and text typed in a repository thread is refused in
the shared hub, so a repository's content does not reach Telegram or Discord.

**Safer git on your machine.** Where Mightling runs git in a checkout the agent can write (the web
server, the launcher, the delegation plugin, the monitor, `ling-code` and the desktop app), git no
longer runs a program that the checkout's own configuration names (an external diff, a filter, a
hook or a pager). The same holds for the git that Night Shift, node jobs, the context engine and the
SWE-bench harness run. Repository hooks run again, as you choose. Your checkouts behave as before:
git still reads your system git configuration, so on Windows line endings (`core.autocrlf`) and Git
LFS work as installed, and no environment variable can point it at another configuration file or add
settings to it.

Full Access given to a phone Work thread now ends within five seconds of the node turning it off (or
of `/airgapped on`), not at the thread's next turn.

**Cave mode is removed for now** (`/cavemode` and the terse-answer levels); it can come back later.

**Night Shift fills its own queue,** off until you turn it on: `[night] autofill = true` (or
`ling-admin night autofill` by hand) queues up to five bounded tasks a night from failing CI tests,
the daily QA findings and the open items of the specs, each as an ordinary task on its own branch.
Nothing is merged or pushed. A morning-review checklist is in the developer notes.

**The delegation plugin** gains `lookup` (an exact answer from the code index, no model), `task` (a
bounded edit with one test, handed to the local model, with a ledger of how it did), and `run_test`,
`git_log` and `diff_stat`; the plugin now runs `ling-delegate mcp`, which is linked into
`~/.local/bin`.

**Benchmarks.** The website has a Benchmarks page: only numbers tied to a release, with how each was
measured. 1.7.3 carries no entry. The website is now in 20 languages, with a dark and light switch.

**Also:** on a Mac, the code-mode helper is found beside the `ling` binary and `ling web start` and
`stop` use a per-user launchd agent; the delegation plugin's test and lint gates run inside the
sandbox; the Mini App refuses a replayed launch; the desktop app's icons show the Mightling mark at every size, including the tray's;
the Apple silicon client can build on a self-hosted Mac; SWE-bench Multilingual runs on arm64.

ling-engine (opt-in): unchanged from 1.7.2 (image
`ghcr.io/dreamference/ling-serve@sha256:7bff4e37e731893a6248b0632897a2740e1842e0b19b28a68f0eee22d670caf8`).

## Known issues

The two Windows limits of 1.7.2 stand: https from inside `ling`'s sandbox (use `ling-fetch`; Python,
Node and git bring their own TLS), and compatibility mode (`ling --compat claude`) not yet available
on Windows.

---

## Upgrading from 1.7.2

`ling update` on a client or a node (it updates the desktop app too when the one-line installer put
it there). On a node, `ling web start` restarts the web server; if a phone bridge is on,
`ling chat start`. The desktop app otherwise: on Linux the release's `.deb`, on a Mac the new dmg,
on Windows the new installer. The model server needs no restart.
