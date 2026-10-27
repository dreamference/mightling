# Mightling 1.7.2 — release notes

**Status:** draft, started 2026-10-26 on the branch `models/ling-engine-server` with the model
server's section; the rest of the release is still to be written into it ({{TBD}} below). The text
between the two rules is the GitHub release's description: the release workflow publishes it as it
stands (`scripts/release_notes.py`), and refuses it while a `{{TBD` marker is left.

**The engine.** ling-engine 8d112d0d25393bca95e3e5c9227d0135eba6f2eb (branch
`release/1.7.2-engine`, 2026-10-27): bdda3a2 plus, alone, main's tokenizer fix (15e82b6: the BPE
cache under a shared mutex; concurrent requests could crash ling-serve with SIGSEGV in
`Tokenizer::bpe`; token ids unchanged, `ling-tokenizer-threads-tests`). bdda3a2 is b8e9451, the
commit SWE-bench nights 12 and 15 tested, plus the parse-only tool-call fix of branch
`fix/toolcall-text` (the owner's rule of 2026-10-20: a later commit only if the engine's checks show
it bit-exact); its binary (sha256 598e5520…9ef41f1) is greedy-identical to night 15's on agentlong
(16 of 16 turns, every streamed event) and passes `--spec-check`. The image's binary, sha256
7c9098f1aa09fbcb8df8680450d2cda0180ba22f6d2fa34067567a769a889975, passed the unit suites,
`tests/server` and a smoke (a tool call, 2 and 4 concurrent requests) on node-b on 2026-10-27. Night 17 (2026-10-26 to 27) compares b8e9451 with SGLang carrying the
phase-merge fix.

**Before publishing:**
- **The image** `ghcr.io/dreamference/ling-serve:8d112d0d2539` (`scripts/build_ling_serve_image.sh`),
  pushed on 2026-10-27 and pinned in `QWEN38_LING_IMAGE` as
  `sha256:9428996ac21af34a816bca14ea0828ef0553576afd312e153bef4c0d3a28bc35` (an anonymous pull of
  the digest answers 200); it replaces bdda3a2's (`sha256:87807c50…`, pushed 2026-10-26).
- **Long context** (the nights ran 65,536), done on node-b on 2026-10-26 with the bdda3a2 image:
  started at 262,144 in 40 s (host memory +60 GB, 46 GB left available), both needles of a
  204,857-token prompt answered exactly, prefill ~850-900 tokens/s at that length, decode 39.6
  tokens/s there (74.9 at short context), `ling-run --spec-check` on the long prompt passed (tokens
  and state identical).
- **The drafter:** ScaleSearch if its upload is on the Hub in time, else RTN ({{TBD which}}).
- **The live test on the node:** switch to ling-serve (`server stop`, `server start --engine ling`),
  `/v1/models`, a `ling exec` task, a phone bridge, the desktop app and the egress audit, then back
  to SGLang ({{TBD date and result}}).
- **The suite**, the desktop gate, the Rust tests and `ling-admin codex build` as for 1.7.1.

---

**A second engine for the model, opt-in: ling-serve.** The default model, Qwen3.8-27B with the
DFlash2 drafter, can now also be served by `ling-serve`, the server of
[ling-engine](https://github.com/dreamference/ling-engine), written for this one model on the GB10.
SGLang stays the default. To try it:

```bash
ling-admin server stop && ling-admin server start --engine ling
```

or `server_engine = "ling"` in `~/.config/dreamference/config.toml` to keep it; remove that line (or
`server start --engine sglang`) to go back. If ling-serve fails one of `server start`'s checks,
`server start` says so and prints the way back; it never switches by itself.

It serves the same checkpoint, drafter and 262,144-token context from its own image (under 1 GB,
against SGLang's 34 GB), inside the same container limits and memory-safety checks. Its speculative
decoding is exact: the drafter changes the speed, never the output. Measured on a GB10: ready in
40 s; about 60 GB of memory in use while serving; a code answer at 74.9 tokens per second; a
204,857-token prompt with two facts hidden in it answered both exactly, with decode at 39.6 tokens
per second at that length.

Its limits, which are why it is not the default yet:

- **No images**: an image reaches the model as the text `[image]` (`ling` and the phone bridges
  know, and send text).
- **No LoRA adapters**, and no API key.
- **Two requests at once**; a third waits.
- **Changing the end of a long prompt reads it again in full**: near 200K tokens that is nearly four
  minutes (~850-900 tokens per second).

It becomes the default once it reads images, serves adapters and runs more than two requests at
once.

ling-engine (opt-in): fixed a crash under concurrent requests (tokenizer cache).

{{TBD the rest of 1.7.2}}

---

## Upgrading from 1.7.1

Nothing changes until you choose ling-serve: the node pulls its image (under 1 GB) at the first
`ling-admin server start --engine ling`, and SGLang's image stays in use by default.
