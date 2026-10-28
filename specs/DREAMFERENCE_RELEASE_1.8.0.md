# Mightling 1.8.0 — release notes

**Status: DRAFT, not for release.** Started 2026-10-28 on the branch `docs/1.8.0`, ahead of the cut. The
description between the two rules still holds `{{TBD` placeholders for every figure, which is what keeps
`scripts/release_notes.py` from publishing it: remove each placeholder with a measured number (or the
sentence that needs it), then change this status. Nothing here is true of a release until the owner's
bar of 2026-10-28 has passed (docs/dev/release-1.8.0.md): the combined rate of four conversations,
identical output paged against flat, and a night with no loss on the new default. The cut also needs
`QWEN38_DEFAULT_ENGINE = ENGINE_LING` in `dreamference/hardware/model_matrix_registry.py`, the engine
pin moved (`scripts/pin_ling_serve_image.py`), and the version set as for 1.7.2.

**Still to fill in before the cut:** the engine commit and image digest; the measured figures below;
the share-scope and HTTP 503 statements, confirmed against the pinned engine;
whether images and adapters are served (the `--engine sglang` paragraphs say what to keep if not); the
upgrade note's sizes; the known issues the bar night finds.

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

**Four conversations at once at the full context.** ling-serve runs four conversations in parallel,
each with the model's 262,144-token context (two until now; a third waited). Their contexts live in
pages taken from one pool, so four of them fit where four full-size blocks would not. Measured on our
machine, not a leaderboard: {{TBD combined rate of four conversations, tokens per second}}, against
{{TBD SGLang's pooled rate on the same four conversations}} on SGLang; one conversation decodes at
{{TBD}} tokens per second. Output is identical to the flat layout and to one stream at a time, checked
bit for bit.

**Memory is sized when the server starts.** ling-serve plans its footprint before it allocates
anything and sizes the page pool from the memory free at that moment; `ling-admin server start` runs the
same plan first and, if the host would be left short, stops and says by how much. While serving, a
request that arrives when the host is nearly out of memory is answered with HTTP 503 and the reason,
instead of risking the machine. About {{TBD}} GB of memory in use while serving four conversations.
`ling-admin status` shows the engine, its sessions and batch, and whether the KV cache is paged.

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

**A smaller image.** ling-serve's Docker image is under 1 GB, against SGLang's 34 GB; a node pulls it
at the first `ling-admin server start` after the upgrade.

**Also:** {{TBD the merges since v1.7.2 that are not about the engine}}

## Known issues

{{TBD from the bar night. If images or adapters are not served by ling-serve: "ling-serve does not read
images / serve LoRA adapters; `--engine sglang` does." Carry over any 1.7.2 limit that remains.}}

---

## Upgrading from 1.7.2

`ling update` on a client or a node. On a node, `ling-admin server stop && ling-admin server start`
then restarts the server with ling-serve and pulls its image (under 1 GB; the 34 GB SGLang image stays
on disk until you remove it, and is used again with `--engine sglang`). A node that set
`server_engine = "ling"` is unchanged; one that set `"sglang"` stays on SGLang. Then `ling web start`
(it restarts the server) and, if a phone bridge is on, `ling chat start` (or `ling signal setup
--refresh` for Signal). A node that serves LoRA adapters should set `server_engine = "sglang"` before
the restart.
