# Mightling preprint

LaTeX source for the arXiv preprint describing the terminal coding agent of Mightling (by Dreamference).

```bash
make              # builds ling.pdf inside the texlive/texlive container (no local TeX needed)
make arxiv.tar.gz # source bundle for arXiv: ling.tex, references.bib and the generated ling.bbl
```

## Decisions for the author before submitting

These are deliberately left open; the draft makes a provisional choice for each.

1. **Author line.** `dgxcoder <dgxcoder@dreamference.ai>` (decided 2026-10-08). The author stays
   anonymous: no personal name, initials or personal email appears in the paper, its metadata or
   this repository.
2. **Affiliation.** Absent. Add one, or state "Independent researcher".
3. **Authorship.** Sole author. Add co-authors if anyone else contributed.
4. **Categories.** Suggested primary `cs.SE` (Software Engineering), cross-list `cs.DC`
   (Distributed, Parallel, and Cluster Computing). `cs.LG` is a possible alternative cross-list.
5. **Licence of the preprint.** arXiv asks for one at submission; CC BY 4.0 is the common choice.
6. **Repository availability.** The repository is public; the Availability section names it
   (`github.com/dreamference/mightling`).
7. **Open measurements the paper admits to.** If they are done before submission, update
   §7 and §8 and drop the matching limitation:
   - a re-run of the live slash-command suite on the current 32-patch build;
   - the decode rate while an index run is active;
   - a live Night Shift stall;
   - what the server does with a session larger than its KV pool;
   - controlled head-to-head runs against Aider, OpenHands, Cline and Continue on the same GB10
     and model server, and against unmodified Codex CLI (`codex --oss`) on the same local model
     and task subset;
   - the first run of the 50-task SWE-bench Multilingual list.

   Resolved since the first draft, and now reported as results: the first-run sign-in screen
   (fixed), the memory needed by `rust-analyzer scip` on the Codex workspace (measured), task-level
   accuracy on a 24-task SWE-bench Verified sample, runs of the Java and .NET indexers, and live
   Night Shift runs of a resumed task and parallel tasks (2026-10-08 update); the 100-task
   SWE-bench pair (default and refine), the failure analysis, A/B nights 1–4, the compaction limit
   tied to the KV pool, throughput at 12 draft tokens and on replayed agent sessions (2026-10-13
   update); A/B night 5 (void: the candidate arm failed at start) and night 6 (Code Mode, 31
   against 30, with the time analysis), and the engine's first two step savings on seeded replays
   (2026-10-14 update); the cause of the 16 app-server timeouts in Codex's suite and a 21,414-test
   re-run on the patched tree, patch `0027` fixing prefix-cache invalidation during local
   compactions and the v2 turn-end compaction + 800-word summary-cap bench, the SGLang v0.5.21
   `/v1/responses` phase-merge bug (Nights 12 and 15) and its runtime patches, the three-check
   streaming loop guard, the removal of `/cavemode` on 2026-10-28, the best-of-3 trajectory chooser (`NightShiftChooser`, Nights 11 and 13), and
   `ling-serve` shipping opt-in at 262,144-token context alongside its Night 17 50-task gate run
   (2026-10-28 update).
8. **The title carries no number** (decided 2026-10-02). It used to quote the patch series' size
   ("21 KB", then "27 KB"), which went stale each time a patch was added; the size is in the
   abstract and the evaluation table instead.

## Plain-text abstract (for the arXiv form, which does not accept LaTeX)

Terminal coding agents such as the Codex CLI are open source, but they are built around a hosted model, a vendor account and a cloud of companion services. We describe Mightling, a coding agent whose model runs on one NVIDIA GB10 workstation (128 GB of CPU–GPU unified memory): a 27B-parameter hybrid Gated-DeltaNet/Transformer model (Qwen3.8-27B, NVFP4) served locally through SGLang or an opt-in model-specific C++/CUDA engine (ling-serve) with a calibrated NVFP4 DFlash2 block-diffusion drafter. Source code, prompts and inference never leave the machine, and no vendor account is involved. Mightling is a fork of the Codex CLI (release rust-v0.158.0, 4,894 Rust source files, 1.93 million lines). Its central engineering choice is to keep the fork almost identical to upstream. The upstream tree is never edited. Each build exports it, applies a series of 32 patches totalling 57.6 KB (57,571 bytes across 62 files, +255/−89 lines), and links in a separate workspace of crates (94.0k lines of Rust) that does the actual work of localisation and extension: model discovery, configuration, prompt rebranding, help-text rewriting, a signed release-based updater and refusal of cloud-bound commands. De-vendoring and localisation take 29.1 KB (16 patches, down from 406 KB before runtime rebranding); the other 16 patches (28.5 KB) are one- and two-line hooks for local features and platform sandboxing. We describe the inventory of cloud dependencies that are switched off, the trace-based egress audit that keeps them off, an enforced air-gap switch that also masks host control sockets, the protocol and caching traps between a vendor harness and a local model server, and the host-safety layer that unified memory requires when CUDA allocations bypass container cgroups. On a 100-task sample of SWE-bench Verified, run inside each task's container on this machine, a single attempt resolves 68; calibrated against 175 published leaderboard submissions on the same tasks, that corresponds to about 69% on all of Verified (95% band 64–73%). A two-session "study, then fix" mode resolves 69 of the same 100 at 2.2× the time, and five single-attempt prompt and harness changes on 44–50 tasks lie within single-run variance (three runs of the default arm on 44 tasks resolve 29, 29 and 31, with 36 solved by at least one). Multi-attempt selection clears that noise: a two-stage best-of-3 chooser (regression test filter plus pairwise diff judge) resolves 33 of those 44 tasks offline and, live, 31 of 50 against 22 of 50 for the first of its three single runs (exact paired test, p = 0.022). On one 50-task night, ling-serve resolves 33 against SGLang's 29, in 4 h 28 min instead of 7 h 32 min. We discuss threats to validity and what remains unmeasured.

## Where the numbers come from

Every figure in the paper was measured on the author's GB10 or read from this repository, first on
2026-09-28, again on 2026-10-01 (commit `05b1c8e`), updated on 2026-10-08 (from `origin/main` at `6aca2f2`),
on 2026-10-13 (from `origin/main` at `05e32b9`), on 2026-10-14 (from `origin/main` at `a24d127`),
and on 2026-10-28. Re-derive them before submission if the code has moved:

| Figure | Source |
|---|---|
| Upstream size (4,894 `.rs` files, 1.93M lines) | exported `codex-rs/` of `rust-v0.158.0`, excluding `ling/` |
| Patch series (32 patches, 57,571 B, 62 files, +255/−89; 64 KB cap; per-patch columns of Table 1) | `codex-patches/*.patch`, each through `git apply --numstat`; `docs/dev/codex-build.md` |
| Smallest series (about 9 KB) | status line of `specs/DREAMFERENCE_MIGHTLING_CODEX.md` |
| Initial series (406,116 B; 395,156 B in one patch) | `git ls-tree -l b03ad9b codex-patches/` |
| Launcher crates (175 `.rs` files, 94,023 lines, 1,174 tests on 2026-10-28) | `ling-rs/**/*.rs`, `#[test]` and `#[tokio::test]` counted |
| Code-index router (28 `.rs` files, 10,577 lines, 135 tests) | `ling-code-rs/**/*.rs`, `#[test]` counted |
| Binary sizes (315 MB, 93 MB, binary megabytes; 1.4 GB unstripped) | `~/.local/share/dreamference/mightling/bin/` |
| Python (217 modules, 71.3k lines; 1,963 test functions on 2026-10-28; 1,764 pass at 1.7.1) | `dreamference/**/*.py`, `tests/**/test_*.py`, `specs/DREAMFERENCE_RELEASE_1.7.1.md` |
| Live suite (77 tests: 75 pass, 2 skip; 651.3 s and 806.7 s) | `tests/test_mightling_slash_commands.py`; earlier 65/3/2 run in `~/.cache/dreamference/slash-tests.log` |
| Throughput (32.0 / 68.6 / 84.1 and 3.4 / 4.4 / 5.9 accepted per step at 12 draft tokens; 25.5 / 50.3 / 87.0 at 16; 23.8 / 49.9 / 53.1 for the removed 122B model) | `specs/DREAMFERENCE_INFERENCE.md`, 2026-10-09 row (`scripts/decode_speed.py --runs 3`); registry entries in `model_matrix_registry.py` |
| Agent-session throughput (48.3 against 45.1 tok/s at 12 and 16 draft tokens, 5.23 against 4.99 accepted; 47 tok/s at 105 ms per step and 33.9 tok/s per stream with two streams at 16) | ling-engine `reports/M0.md` §1 and §3–4; `specs/DREAMFERENCE_INFERENCE.md` drafter row |
| ling-engine / `ling-serve` (682 MB vs 33.7 GB container; 262,144 context, 40 s start, 74.9 tok/s short code, 39.6 tok/s decode and 850–900 tok/s prefill at 205K two-needle prompt; Night 17 33/50 in 4 h 28 m vs SGLang 29/50 in 7 h 32 m) | `docs/dev/models-and-engines.md`, `specs/DREAMFERENCE_RELEASE_1.7.2.md`, `specs/DREAMFERENCE_BACKLOG.md` §12, `specs/DREAMFERENCE_MOONSHOT_BEAT_HUSKY.md` |
| SGLang v0.5.21 `/v1/responses` bugs (57 early stops on Nights 12 and 15; nights on v0.5.21 resolved 40–52% against 62–74% on v0.5.19, a natural experiment; `SGLANG_PHASE_MERGE_PATCH` and `SGLANG_STREAM_PHASE_PATCH`) | `docs/dev/models-and-engines.md` ("SGLang traps") |
| SWE-bench (rounds, 71% and 69% estimates, bands, comparisons, refine timing, the 100-task pair) | `specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_COMPARISON.md`, `specs/DREAMFERENCE_MIGHTLING_REFINE.md` §2 and §2.1, `specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md` |
| Failure categories of the 32, A/B nights 1–2 | `specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md`, Conclusion and §9.4 |
| Nights 3–4 (26 against 29, p 0.51; 28 against 31, p 0.45), best-of-3 ceiling (29/29/31, 23 every time, 36 by some run), and chooser v1 (33/44 offline, 10/13 discordant; Night 11 18/30 vs 15/30, p 0.375; Night 13 31/50 vs 22/50, p 0.022) | `specs/research/together-ai-survey-2026-10-13.md` §3.1; `specs/DREAMFERENCE_BACKLOG.md` §2; `specs/DREAMFERENCE_MOONSHOT_BEAT_HUSKY.md` |
| Night 5 void (0 of 44, 44 agent errors, 7 s median; the default arm 29; `unknown variant function, expected freeform`) and the per-arm smoke check | `specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md`; commit `9367c42` |
| Night 6 (31 against 30, +2.3 points, McNemar p 1.000; medians 7:07 and 5:38; 6 and 1 timeouts; 2,865 `exec` calls in 44 of 44) and its time analysis | `docs/dev/code-mode-night6.md` (`cdfa321`) |
| Model gate | `specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md` §18; commit `008c90c` |
| Airgapped, host control socket masking (`0019`), egress audit | `docs/dev/airgapped.md`, `specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md` §14, `specs/DREAMFERENCE_MIGHTLING_EGRESS.md` §10 |
| Nodes, second machine, remote access (NetBird) and adapters | `specs/DREAMFERENCE_MIGHTLING_NODE.md` §18; `docs/dev/remote-access.md`; `docs/dev/adapters.md` |
| Host-safety thresholds, unified-memory cgroup blind spot (`ling-serve` 60 GB vs 0.4 GB cgroup), `safegit` | `psi_watchdog.py`, `vllm_server_manager.py`, `docs/dev/host-safety.md` |
| Loop guard (72,000-token `<parameter>` loop, 3-check streaming detector, 0 false positives on 203,009 texts / 77 MB, 1,037/1,037 synthetic loops) | `docs/dev/loop-guard.md` |
| Cave mode evaluation and removal (removed 2026-10-28, `0017` and `cave.rs` deleted) | `specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md` §1 (before its removal in `ef443182`), `specs/DREAMFERENCE_RELEASE_1.7.1.md`, `docs/dev/codex-build.md` (`ef443182`) |
| KV pool, compaction (`0027` prefix-cache fix: 0% → 100% median cached, 110–166 s → 61–98 s), turn-end compaction + 800-word summary cap v2 bench (mid-turn 2.0–2.5 → 0.0; Click 1,761 → 1,277 s; Night Shift 2,537 → 1,462 s), drop-first (`0026`) 4,369 s timeout | `specs/DREAMFERENCE_MIGHTLING_COMPACTION.md` §1, §11, §12; `docs/dev/turn-end-compaction/2026-10-14.md` |
| Compactions on benchmark runs (320 tasks: 75/78/57/38% by compaction count; 17% of agent time; 98 s median; 18 paired tasks 50 against 40%; 1% repeated commands) | `specs/research/compaction-survey-2026-10-13.md` |
| Code Mode and apply_patch on SGLang (custom tools dropped; 11 turns, no call; 0020 +217 B) | commits `b1b9296` and `dc7bd9d`; `docs/dev/codex-build.md` |
| Stream-idle timeout (300 s → 900 s), tool-call check, NO_PROXY | commit `a7188c3`; `specs/DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md` 1.10 |
| Node resolution by id, Wi-Fi power saving (~80% of mDNS queries lost) | commits `d746115`, `7275cdf`, `9fb0b1e`; `specs/DREAMFERENCE_MIGHTLING_NODE.md` §6.4 and §13.2 |
| Releases (signed since 1.5.1; automatic since 2026-10-12) | `specs/DREAMFERENCE_RELEASE_SIGNING.md`; `specs/DREAMFERENCE_RELEASE_1.7.0.md`–`1.7.2.md` |
| Messengers (off by default; Signal live test 2026-10-12; Matrix and Telegram) and Web/Desktop (`ling web`, `ling app`, Onyx retired) | `docs/dev/messengers.md`, `docs/dev/desktop.md`, `specs/DREAMFERENCE_RELEASE_1.7.0.md` |
| Code index (recall, CLI and SQL latency, rust-analyzer time and memory, replay, indexers, query latency) and `ling-docs` | `specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md` §2 and §14.1; `docs/dev/local-file-index.md` |
| SCIP OOM at 8 GB | `journalctl --user` scope records, 2026-09-28 23:05 and 23:07 |
| Night Shift (live run, parallelism, 34 runner tests, `best_of = 3` default) | `specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md` §11; `docs/dev/night-shift.md`; `tests/test_night_shift.py` |
| Codex's own suite (20,380 tests on 2026-10-01; 16 timeouts explained 2026-10-02 as `0013` analytics events; 21,414 tests on 2026-10-27: 21,413 pass, 234 skip) | `specs/DREAMFERENCE_MIGHTLING_CODEX.md` ("Results, 2026-10-01", "Results, 2026-10-02" and "Results, 2026-10-27") |
| arXiv citations | checked against `export.arxiv.org` on 2026-09-28; `adeyemi2026cavewoman` on 2026-10-01 |
