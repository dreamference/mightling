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
   - the 100-task SWE-bench Verified pair (default and refine), which replaces the 24-task estimate;
   - a re-run of the live slash-command suite on the current 23-patch build;
   - a re-run of Codex's own test suite with `/cavemode` and `/night` in the tree, and the cause
     of its 16 app-server timeouts;
   - the decode rate while an index run is active;
   - a live Night Shift stall;
   - what the server does with a session larger than its KV pool.

   Resolved since the first draft, and now reported as results: the first-run sign-in screen
   (fixed), the memory needed by `rust-analyzer scip` on the Codex workspace (measured), task-level
   accuracy on a 24-task SWE-bench Verified sample, runs of the Java and .NET indexers, and live
   Night Shift runs of a resumed task and parallel tasks (2026-10-08 update).
8. **The title carries no number** (decided 2026-10-02). It used to quote the patch series' size
   ("21 KB", then "27 KB"), which went stale each time a patch was added; the size is in the
   abstract and the evaluation table instead.

## Plain-text abstract (for the arXiv form, which does not accept LaTeX)

Terminal coding agents such as OpenAI's Codex CLI are open source, but they are built around a hosted model, a vendor account and a cloud of companion services. We describe Mightling, a coding agent whose model runs on one NVIDIA GB10 workstation (128 GB of CPU-GPU unified memory): a 27B-parameter model (Qwen3.8-27B, NVFP4) served locally through SGLang with a block-diffusion drafter. Source code, prompts and inference never leave the machine, and no vendor account is involved. Mightling is a fork of the Codex CLI (release rust-v0.158.0, 4,894 Rust source files, 1.93 million lines). Its central engineering choice is to keep the fork almost identical to upstream. The upstream tree is never edited. Each build exports it, applies a series of 23 patches totalling 42.7 KB (43 files, 169 lines added and 53 removed lines), and links in a separate crate that does the actual work of localisation: model discovery, configuration, prompt rebranding, help-text rewriting, a signed release-based updater and refusal of cloud-bound commands. We report how the series shrank from an initial 406 KB to about 9 KB, which techniques did it, and how seven later features each cost only a hook. We also report an inventory of the cloud dependencies we had to switch off, a trace-based audit that keeps them off, an enforced network switch, integration findings that are not visible from the documentation, and the host-safety layer that unified memory forced on us after model loads froze the machine. On a 24-task sample of SWE-bench Verified, run inside each task's container on this machine, the agent resolves a median of 16 tasks over six rounds; calibrated against the 175 published leaderboard submissions on the same tasks, that corresponds to about 71% on all of Verified (95% band 60-81%), and a two-session "study, then fix" mode resolved 20 in its one round (about 82%). We then report what was built on the fork, each from the same launcher crate or beside it: a terse-answer mode that halves answers to questions and leaves coding work unchanged, a two-layer code index (a tree-sitter knowledge graph plus compiler-exact SCIP data), an overnight task queue that works in git worktrees, and a client/node split that pairs workstations over SSH. We are explicit about what is estimated rather than measured.

## Where the numbers come from

Every figure in the paper was measured on the author's GB10 or read from this repository, first on
2026-09-28, again on 2026-10-01 (commit `05b1c8e`) and updated on 2026-10-08 (from `origin/main` at `6aca2f2`). Re-derive them before submission if the
code has moved:

| Figure | Source |
|---|---|
| Upstream size (4,894 `.rs` files, 1.93M lines) | exported `codex-rs/` of `rust-v0.158.0`, excluding `ling/` |
| Patch series (23 patches, 42,741 B, 43 files, +169/−53; per-patch columns of Table 1) | `codex-patches/*.patch`, each through `git apply --numstat` |
| Smallest series (about 9 KB) | status line of `specs/DREAMFERENCE_MIGHTLING_CODEX.md` |
| Initial series (406,116 B; 395,156 B in one patch) | `git ls-tree -l b03ad9b codex-patches/` |
| Launcher crates (23,706 lines, 372 tests; `cave.rs` 566 and `night.rs` 769 lines as of 2026-10-01) | `ling-rs/**/*.rs`, `#[test]` and `#[tokio::test]` counted |
| Code-index router (10,444 lines, 131 tests) | `ling-code-rs/**/*.rs`, `#[test]` counted |
| Binary sizes (315 MB, 93 MB, binary megabytes; 1.4 GB unstripped) | `~/.local/share/dreamference/mightling/bin/` |
| Python (141 modules, 39.1k lines) | `dreamference/**/*.py` |
| Python tests (996 collected) | `pytest tests/` on 2026-10-08: 905 pass, 78 skip, 13 fail only because the installed binary predates the rename |
| Live suite (77 tests: 75 pass, 2 skip; 651.3 s and 806.7 s) | source of the two timings not located in the repository or caches on 2026-10-01; the figures predate this revision, so re-run before submission. The full `pytest tests/` run of 2026-10-01 (509 pass, 2 skip) confirms 75 pass and 2 skip on the default model. The earlier 65/3/2 run is `~/.cache/dreamference/slash-tests.log` |
| Throughput (25.5 / 50.3 / 87.0; 23.8 / 49.9 / 53.1 for the removed 122B model) | registry entries in `model_matrix_registry.py` (the 122B one in git history before 2026-10-07) |
| Agent-session throughput (about 30 tok/s, 4.87 of 16 drafted tokens accepted) | ling-engine `SPEC.md`, production baseline from recorded sessions |
| SWE-bench (rounds, 71% and 82% estimates, bands, comparisons, refine timing) | `specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_COMPARISON.md`, `specs/DREAMFERENCE_MIGHTLING_REFINE.md` §2, `specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md` |
| Airgapped, egress audit | `specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md` §14, `specs/DREAMFERENCE_MIGHTLING_EGRESS.md` §10 |
| Nodes, second machine | `specs/DREAMFERENCE_MIGHTLING_NODE.md` §18; the second GB10 paired on 2026-10-08 (commits `2e81039`, `596c248`) |
| Host-safety thresholds | `psi_watchdog.py`, `vllm_server_manager.py` constants |
| Cave mode (output shares, 67:1 cost ratio, benchmark ratios, 35 of 36 checks) | `specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md` §1 |
| KV pool, compaction and session sizes, diffusion-compressor test | `specs/DREAMFERENCE_MIGHTLING_COMPACTION.md` §1 |
| Code index (recall, CLI and SQL latency, rust-analyzer time and memory, replay, indexers, query latency) | `specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md` §2 and §14.1 |
| SCIP OOM at 8 GB | `journalctl --user` scope records, 2026-09-28 23:05 and 23:07 |
| Night Shift (live run, parallelism, 34 runner tests) | `specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md` §11; `tests/test_night_shift.py` |
| Codex's own suite (20,380 tests, 20,362 passed, 16 timeouts) | `specs/DREAMFERENCE_MIGHTLING_CODEX.md`, "Results, 2026-10-01" |
| arXiv citations | checked against `export.arxiv.org` on 2026-09-28; `adeyemi2026cavewoman` on 2026-10-01 |
