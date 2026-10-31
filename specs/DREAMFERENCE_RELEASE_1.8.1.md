# Mightling 1.8.1 — release notes

**Status: STUB, not for release.** Started 2026-10-29 beside the 1.8.0 notes, to hold what 1.8.0 leaves
to the next release. Every paragraph between the two rules is a `{{TBD` placeholder until its work has
landed and passed its own test night, which keeps `scripts/release_notes.py` from publishing it.

## Plan (not part of the notes)

Target 2026-11-02, after 1.8.0 (~11-01). Ragon 1.9.0 is cut the day after 1.8.1 if 1.8.1 needs no hotfix in its
first day. The checklist that makes the cut take minutes is [release-1.8.1.md](../docs/dev/release-1.8.1.md).

**What 1.8.1 adds on top of 1.8.0**

- **Image input on ling-serve.** The recipe's `"vision"` goes from `False` to `True`; the launch passes
  `--vision on` and the launcher declares image input. Memory-checked with four sessions before it merged.
- **Two per-launch switches, off in the recipe:** `"paging"` (the KV page pool: `--kv-pages`, or `--no-kv-pages`
  and the engine's own flat sessions) and `"fair_pairing"` (`--fair-pairing 1|0`). A night arm overrides either from
  the environment of the launch (`DREAMFERENCE_LING_PAGING=0|1`, `DREAMFERENCE_LING_FAIR_PAIRING=0|1`) without a
  new image; the flags are always given explicitly, so `docker inspect` and `server status` name the arm. They act
  only on an engine whose recipe lists the features `paging-switch` and `fair-pairing` (the candidate image's
  commit has both).
- **LoRA adapters on ling-serve: 1.8.1 if ready by the cut, otherwise 1.8.2.** It never holds 1.8.1: if it is not
  merged and tested by 2026-11-02 the paragraph below is deleted and the work goes to 1.8.2.

**What the 2026-10-31 night decides.** The night runs every arm on one candidate image (image input on, paging and
fair pairing switched per arm), on all three machines. Two defaults come out of it:

1. *Paging default.* Paging on only if its arm shows greedy output identical to flat and no loss on the SWE arm; the
   paged-vs-flat speed read is information, not a gate. Otherwise `"paging"` stays `False`.
2. *Fair pairing default.* On only if the fair gate exits 0 at the paging default that ships and the SWE fair arm
   shows no loss (greedy output identical, the slowest of four streams no slower, the pooled rate not materially
   lower). Otherwise `"fair_pairing"` stays `False` and the paragraph below says it is available, off.

The verdict is written into `reports/` by the bar-night agent; the cut flips only the recipe lines it names.


---

**ling-serve reads images.** 1.8.0 started ling-serve with image input off; 1.8.1 turns it on
(`QWEN38_LING_RECIPE["vision"]` is True, so the launch passes `--vision on`). `/model_info` says the
model understands images, so `ling`, the desktop app and the phone bridges send them again, and
`ling-admin main-model inspect` shows "Image input: On". `server start` names `--engine sglang` for
images only while a recipe turns the tower off. The tower's scratch at the largest image (1,100 MB) and
its embedding cache (512 MB) are in ling-serve's plan: with 1.8.0's flat sessions the plan grows by about
1.6 GB, with the page pool the pool is that much smaller; host safety checks the same plan. {{TBD:
measured on the pinned engine: the plan with `--vision on` and off, the
engine's image checks through the page pool and the prefix KV store, one `ling exec` with an image, and
an image question's answer and time against SGLang's.}}

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

**Signal tables.** A bold cell (or other Markdown) in a table row is now read, so it no longer reaches the phone
with its asterisks.

**Short memory cuts sessions.** `ling-serve start` counts the engine's host reserve and, when four sessions do not
fit, starts with three, two or one and prints a warning.

**Also:** {{TBD the other merges since v1.8.0}}

## Known issues

{{TBD}}

---

## Upgrading from 1.8.0

{{TBD: `ling update`; a node restarts the server to pull the new ling-serve image; a node kept on SGLang
for images or adapters (`server_engine = "sglang"`) may remove that line.}}
