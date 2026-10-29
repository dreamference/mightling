# Mightling 1.8.1 — release notes

**Status: STUB, not for release.** Started 2026-10-29 beside the 1.8.0 notes, to hold what 1.8.0 leaves
to the next release. Every paragraph between the two rules is a `{{TBD` placeholder until its work has
landed and passed its own test night, which keeps `scripts/release_notes.py` from publishing it.

---

**ling-serve reads images.** {{TBD: 1.8.0 starts ling-serve with `--vision off`; 1.8.1 serves image input
on ling-serve (the recipe's `"vision"` on, the facts' "Image input" on), measured: an image question's
answer and time against SGLang's.}}

**LoRA adapters on ling-serve.** {{TBD: the adapter guard (`_engine_for_adapters`) lets ling-serve serve
installed adapters; the messages that name 1.8.1 come out; measured against SGLang's adapter serving.}}

**Four conversations at the full context (paged KV).** {{TBD: only if its night passes: the recipe's
`kv_pages` (`QWEN38_LING_KV_PAGES`) on, so the launch passes `--sessions 4 --batch 4 --kv-pages` to the engine
that 1.8.0 already ships with the pool off; the pooled rate, the slowest stream and the output against the flat
slots, measured.}}

**Fair pairing on by default.** {{TBD: only if its test night passes (greedy output identical, the
slowest of four streams no slower, the pooled rate not materially lower): ling-serve gives every
decoding conversation the same share of passes; `--fair-pairing 0` or `LING_FAIR_PAIRING=0` turns it
off. If 1.8.0's image did not contain it, it is new in 1.8.1.}}

**Also:** {{TBD the merges since v1.8.0}}

## Known issues

{{TBD}}

---

## Upgrading from 1.8.0

{{TBD: `ling update`; a node restarts the server to pull the new ling-serve image; a node kept on SGLang
for images or adapters (`server_engine = "sglang"`) may remove that line.}}
