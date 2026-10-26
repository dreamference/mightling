# Mightling 1.7.2 — release notes

**Status:** draft, started 2026-10-26 on the branch `models/ling-engine-server` with the model
server's section; the rest of the release is still to be written into it ({{TBD}} below). The text
between the two rules is the GitHub release's description: the release workflow publishes it as it
stands (`scripts/release_notes.py`), and refuses it while a `{{TBD` marker is left.

**The engine.** ling-engine at 79c38c7648d06a5657ff488195059348cfcd5f4a, the commit SWE-bench
nights 12 and 15 tested, or a later commit whose changes the engine's checks show bit-exact (the
owner's decision of 2026-10-20). The planned one adds the tool-call parse fix f5ab951 (branch
`fix/toolcall-text`, parse only): {{TBD the final commit and the binary's sha256}}. Night 17
(2026-10-26 to 27) compares it with SGLang carrying the phase-merge fix.

**Before publishing:**
- **The image** built from the final commit (`scripts/build_ling_serve_image.sh`), pushed to
  `ghcr.io/dreamference/ling-serve`, made public, and its digest pinned in `QWEN38_LING_IMAGE` with
  the commit in `QWEN38_LING_ENGINE_COMMIT` ({{TBD digest}}).
- **Long context** (the nights ran 65,536): ling-serve starts at 262,144 within the container's
  cap, a ~205K-token needle question answers correctly, decode speed at that length is recorded,
  and `ling-run --spec-check` passes on the long prompt ({{TBD result}}).
- **The drafter:** ScaleSearch if its upload is on the Hub in time, else RTN ({{TBD which}}).
- **The live test on the node:** `server stop`, `server start` with ling-serve, `/v1/models`, a
  `ling exec` task, a phone bridge, the desktop app and the egress audit ({{TBD date and result}}).
- **The suite**, the desktop gate, the Rust tests and `ling-admin codex build` as for 1.7.1.

---

**The model now runs on ling-engine.** The default model, Qwen3.8-27B with the DFlash2 drafter, is
served by `ling-serve`, the server of [ling-engine](https://github.com/dreamference/ling-engine),
written for this one model on the GB10. Its speculative decoding is exact, so the drafter changes
the speed and never the output, and it checks the drafter's guesses as a tree rather than a single
line. It was tested against SGLang on SWE-bench before it became the default. The image is small
(under 1 GB against SGLang's 34 GB) and runs inside the same container limits and memory-safety
checks as before.

What changes with it: two requests run at once and more wait, and it does not read images, serve
LoRA adapters or take an API key. The context window stays 262,144 tokens.
**SGLang is one flag away** for any of those, with the same checkpoint and drafter:

```bash
ling-admin server stop && ling-admin server start --engine sglang
```

or `server_engine = "sglang"` in `~/.config/dreamference/config.toml` to keep it. If ling-serve
fails one of `server start`'s checks, `server start` says so and prints that command; it never
switches by itself.

{{TBD the rest of 1.7.2}}

## Upgrading from 1.7.1

The node pulls the new image (under 1 GB) at its next `ling-admin server start`; SGLang's image
stays on disk for the fallback.
