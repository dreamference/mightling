# SGLang wrong-answer probe: v0.5.19 against v0.5.21 (2026-10-14)

Why: production's pinned image is SGLang v0.5.19 (`QWEN38_SGLANG_RECIPE`, `lmsysorg/sglang@sha256:d6e7288627be…`). The SGLang survey (`specs/research/sglang-team-survey-2026-10-13.md` §2.9) found two correctness issues against it: the DFlash checkpoint-crossing bug (upstream #37817/#37818, accepted drafts crossing a GDN checkpoint skip the state save, and a later prefix-cache hit pairs new KV with a stale recurrent state), fixed in v0.5.20, and the open concurrency corruption (#36548, #35150: 111 of 304 greedy ordering answers wrong at concurrency 8 on a DGX Spark with this checkpoint, 0 of 100 serial). The owner's rule (2026-10-14): probe both images for wrong answers at 2 and 8 streams on this machine; if the newer image is no worse, move the pin in 1.6.2; if it is also wrong at 2 streams, keep v0.5.19 and report.

## Method

One stdlib script (kept with the raw results in `~/.cache/dreamference/sglang-probe-2026-10-14/`), the same requests to both images, through the gate at `http://localhost:8000`, greedy (`temperature 0`), non-streaming, `seed` = the copy's index, the checkpoint's own chat template as production patches it (reasoning on, the template's default effort).

1. **Ordering** (issue #36548's own probe). Eight five-runner ordering puzzles with one solution each (four adjacent "before/after" constraints and one more, shuffled; uniqueness checked by brute force), sent as copies that cycle through the eight: 48 copies at concurrency 1 (the baseline), 200 at concurrency 2, 304 at concurrency 8 (the issue's count; 8 is also production's `--max-running-requests`). The answer is the last line of the reply; a *wrong* answer is a clean completion (`finish_reason: stop`) whose last line is five names in the wrong order; anything else (truncation, no five-name line) is counted apart as *unparsed*.
2. **Checkpoint** (the shape of #37818). A ~4.5K-token prefix of 170 numbered records (owner, colour, weight), a first turn that asks for 20 of them as `record N: owner, colour, weight kg` lines (a long greedy answer with accepted drafts: ~600 tokens), then a second turn on the same conversation (the first answer as the assistant message, so the radix cache hits over the prompt and the generated tokens) asking one more record's owner and colour. 32 conversations at concurrency 2. Scored: the second turn's line must name the right owner and colour; the first turn's 20 lines are scored too (owner and weight).

Image swap: `ling-admin server stop`, then `ling-admin server start --docker-image lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469` (the `v0.5.21` tag's manifest-list digest, arm64 image `1f176b3a…`, SGLang `0.5.21`); the rest of the launch line is the registry recipe unchanged (every production flag, `--num-continuous-decode-steps` included, is still accepted by v0.5.21's `launch_server --help`). Production was brought back with the pinned image afterwards. Never two model servers at once on this machine.

## Numbers

All answers right on both images, nothing truncated or unparseable; the accepted-draft length and the throughput are the server's own (`/metrics`, the probe's wall clock).

| Part | Streams | Requests | v0.5.19 wrong / unparsed | v0.5.21 wrong / unparsed | v0.5.19 tok/s | v0.5.21 tok/s |
|---|---|---|---|---|---|---|
| Ordering (baseline) | 1 | 48 | 0 / 0 | 0 / 0 | 63.7 | 67.6 |
| Ordering | 2 | 200 | 0 / 0 | 0 / 0 | 112.7 | 121.8 |
| Ordering | 8 | 304 | 0 / 0 | 0 / 0 | 269.1 | 297.7 |
| Checkpoint, second turn | 2 | 32 conversations | 0 / 0 | 0 / 0 | | |
| Checkpoint, first-turn lines | 2 | 640 lines | 0 wrong | 0 wrong | | |

tok/s is aggregate completion tokens over the run's wall clock (all streams together, prefill included). Mean accepted draft length during the ordering runs: ~7.5 of 12 on v0.5.19, ~7.1 on v0.5.21. Whole probe: 25 min per image; v0.5.21's cold start (model load, torch.compile and graph capture on a new image) 7 min to `/v1/models`, and `server start`'s NVFP4 and tool-call canaries passed on it.

**What the probe did not show.** Issue #36548's 111 of 304 wrong at concurrency 8 did not reproduce here on v0.5.19 (0 of 304 with production's flags: NVFP4 drafter, 12 drafts, BF16 KV and mamba state, `extra_buffer`, decode CUDA graphs to batch 8). So the probe cannot rank the two images by that bug; it establishes that neither image gives a wrong answer on this machine at 1, 2 or 8 streams, and that the two-turn checkpoint shape is clean on both.

**Found on the way.** v0.5.21 rejects `--cuda-graph-max-bs` as ambiguous: the flag was split into `--cuda-graph-max-bs-decode` and `--cuda-graph-max-bs-prefill`. The first start on the new image restart-looped on that error (`server start`'s own wait saw only the container's exit); the recipe now says `--cuda-graph-max-bs-decode 8`, the same setting (prefill graphs are off, `--disable-prefill-cuda-graph`). Every other flag of the production launch line is accepted by v0.5.21 by its exact name, `--num-continuous-decode-steps` included (dead code upstream since #37551, kept until the owner drops it).

## Decision

By the owner's rule of 2026-10-14 (no worse → move the pin), **the pin moves to v0.5.21 in 1.6.2**: `QWEN38_SGLANG_RECIPE['docker_image'] = lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469`, the same answers, a few percent faster, and the upstream checkpoint-crossing fix on board. The candidate recipe (Minima, night 2) inherits it, as intended. The move changes one flag name and nothing else in the launch line.

Production on this machine was brought back on v0.5.19 after the probe and stays there until its next `server start` with 1.6.2's code; a node that updates to 1.6.2 pulls the new image (33.7 GB) at its next `server start`.
