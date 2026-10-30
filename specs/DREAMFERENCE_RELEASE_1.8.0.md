# Mightling 1.8.0 — release notes

**Status: DRAFT, not for release.** Started 2026-10-28 on the branch `docs/1.8.0`, ahead of the cut. The
description between the two rules still holds `{{TBD` placeholders for every figure, which is what keeps
`scripts/release_notes.py` from publishing it: remove each placeholder with a measured number (or the
sentence that needs it), then change this status. Nothing here is true of a release until the owner's
bar of 2026-10-28 has passed (docs/dev/release-1.8.0.md): the combined rate of four conversations,
identical output paged against flat, and a night with no loss on the new default. The switch itself
(`QWEN38_DEFAULT_ENGINE = ENGINE_LING` in `dreamference/hardware/model_matrix_registry.py`, ling-serve
started with `--vision off`) and the engine pin (`scripts/pin_ling_serve_image.py`) are on the branch
`prep/1.8.0`; the cut still needs the version set as for 1.7.2.

**Still to fill in before the cut:** the measured figures below (the bar night, on the pinned image);
the share-scope and HTTP 503 statements, confirmed against the pinned engine; the upgrade note's sizes; the known issues the
bar night finds. Decided (2026-10-29): image input and LoRA adapters stay on SGLang until 1.8.1
(`--engine sglang` for both); ling-serve reads no images and serves no adapters in 1.8.0.

---

**ling-serve is the default model server.** The default model, Qwen3.8-27B with the DFlash2 drafter,
is now served by `ling-serve`, the server of
[ling-engine](https://github.com/dreamference/ling-engine), written for this one model on the GB10.
It was opt-in from 1.7.2. SGLang, which served the model until now, is one flag away:

```bash
ling-admin server stop && ling-admin server start --engine sglang
```

or `server_engine = "sglang"` in `~/.config/dreamference/config.toml` to keep it. Speculative decoding
is exact on both: the drafter changes the speed, never the output. If ling-serve fails one of
`server start`'s checks, `server start` says so and prints the way back; it never switches by itself.

**Four conversations at once.** ling-serve runs four conversations in parallel, each with a
196,608-token context (two until now; a third waited). Paged memory, which lets four conversations
have the model's full 262,144 tokens, comes in a later 1.8.x. Measured on our
machine, not a leaderboard: {{TBD combined rate of four conversations, tokens per second}}, against
{{TBD SGLang's pooled rate on the same four conversations}} on SGLang; one conversation decodes at
{{TBD}} tokens per second. Output is identical to one stream at a time, checked
bit for bit.

**Memory is sized when the server starts.** ling-serve plans its footprint before it allocates
anything; `ling-admin server start` runs the
same plan first, counting the 16 GB the engine keeps for the host. If four conversations do not fit, it starts
with three, two or one, whichever is the most that does, and prints a warning saying so; if even one does not
fit, it stops and says by how much. While serving, a
request that arrives when the host is nearly out of memory is answered with HTTP 503 and the reason,
instead of risking the machine. About {{TBD}} GB of memory in use while serving four conversations.
`ling-admin status` shows the engine, its sessions and batch, and whether the KV cache is paged (it is not in 1.8.0).

**Conversations that begin alike are read once.** The system prompt, the tool list and a repository's
instructions open every agent conversation. ling-serve now reuses what it has already read for such a
beginning, which saves reading time and never changes an answer. Sharing is private by scope: `ling`
sends an opaque hash of the owner and the repository in `X-Ling-Share-Scope`, and a beginning is reused
only by requests with the same scope. Two repositories, or two owners on one machine, never share, even
for identical text, and the server never sees a name or a path. A request without a scope shares
nothing across conversations. Measured on our machine, not a leaderboard: a new conversation's first
answer after a shared beginning arrives in {{TBD}} s, against {{TBD}} s when it is read again.

**Fairer turns.** A long prompt being read no longer stalls the others: conversations take turns, a
short follow-up runs ahead of a long first read, and while others decode the long read goes in small
chunks. A conversation used in the last 30 seconds is not evicted for a new one.

**Fair pairing, included and off.** ling-serve can give every decoding conversation the same share of
passes (`--fair-pairing 1`). It ships switched off in 1.8.0, and Mightling does not turn it on, so the
scheduling is the bar night's; it is meant to be on by default from 1.8.1, once its own test night
passes.

**A smaller image.** ling-serve's Docker image is under 1 GB, against SGLang's 34 GB; a node pulls it
at the first `ling-admin server start` after the upgrade.

**Everything from 1.7.3 and 1.7.4.** Both came out after 1.7.2 and 1.8.0 contains them. 1.7.3 fixed
`install.sh` reading the release API's answer, and it pinned ling-engine `72c2a6a`; 1.8.0's engine
pin (`4853a93`) replaces that one. The security fixes from 1.7.4, by area:

- **`ling web` and paired devices.** A device paired with the `watch` scope can only read and follow
  threads. A revoked device stays revoked, two pairings at once cannot race past the one-time code,
  and an Ask turn cannot choose its own sandbox.
- **The bridge and sync.** `ling web`'s bridge refuses parameters that are not an object. A sync host
  answers approvals only for requests that are waiting. The Signal and Telegram bridges and sync read
  the air gap at every message.
- **The desktop app.** Open never runs a file the agent wrote. The air-gap gate fails closed, and
  preview tabs follow the air gap. The office preview opens no stray sockets, and the sync hub is
  pinned to the one you chose.
- **The air gap.** Every profile without a sandbox is refused at the air gap. A node job cannot loosen
  its node's air gap. The setting's reader fails closed on a malformed file (found by fuzzing).
- **Installers and updates.** `install.ps1` checks the release signature as `install.sh` does. The
  installers and `ling update` require the latest release to be signed, and the search services'
  base image is pinned by digest.
- **The Hugging Face token** is no longer on the model server's `docker` command line or any other
  command's arguments. Config never saves a value taken from the environment, and a config file keeps
  its mode when it is written.
- **A repository's own config.** A repository's `dreamference.toml` names only a local model server
  and cannot choose the adapter or the prompt. A project's `.codex/config.toml` cannot reopen the
  vendor channels. Skill downloads and worktree trust are closed to a hostile repository.
- **Nodes.** The `authorized_keys` line, pairing, provisioning, power-cycle, fetch, the model copy's
  paths and sizes, and the LAN copy's host key are all checked more strictly.
- **Night Shift and Signal setup.** Night Shift reports carry no terminal escape sequences. Signal
  setup unpacks in a private scratch folder and takes the pairing code on standard input.
- **The chat sidecar** refuses the tailnet address range and saves Gmail tokens safely. A model server
  the memory watchdog killed is not restarted by the restart policy.
- **The egress audit** counts every address of a batched send, even one another line interrupted.
- **Remote access and the forge.** The mirror stops when air-gapped, and the CAA record is swapped
  without a gap.
- **`ling-code` and `ling-docs`.** Nine fixes each from a correctness review. `ling-docs` refuses a
  symlinked folder that points at a secret location.
- **Releases.** The Mac preview bundles only this repository's client artifacts, and CI actions are
  pinned.

Also from 1.7.4: `ling-search` and `ling-fetch` honour `MIGHTLING_NODE` as `ling` does. The desktop
app lets you pick a node when several answer. Every `ling-admin` command refuses unknown arguments,
and Night Shift copes with paths that have spaces, clock changes and a hung index.

**Also:** {{TBD the merges since 1.7.4 that are not about the engine}}

## Known issues

- **No image input on ling-serve.** ling-serve starts with `--vision off` in 1.8.0, and an image arrives
  as the text `[image]`; `ling`, the desktop app and the phone bridges learn this from the server and
  send text. For images, `ling-admin server start --engine sglang`. ling-serve reads images from 1.8.1.
- **No LoRA adapters on ling-serve.** While an adapter is installed, `server start` serves the model
  with SGLang and says so (an explicit `--engine ling` is refused); ling-serve serves adapters from
  1.8.1.
- **No `--api-key` on ling-serve**: `server start --api-key` without `--engine sglang` is refused and
  names the flag.

{{TBD from the bar night; carry over any 1.7.2 limit that remains.}}

---

## Upgrading from 1.7.2

`ling update` on a client or a node. On a node, `ling-admin server stop && ling-admin server start`
then restarts the server with ling-serve and pulls its image (under 1 GB; the 34 GB SGLang image stays
on disk until you remove it, and is used again with `--engine sglang`). A node that set
`server_engine = "ling"` is unchanged; one that set `"sglang"` stays on SGLang. Then `ling web start`
(it restarts the server) and, if a phone bridge is on, `ling chat start` (or `ling signal setup
--refresh` for Signal). A node with LoRA adapters installed stays on SGLang by itself until 1.8.1;
`server_engine = "sglang"` keeps it there afterwards too.
