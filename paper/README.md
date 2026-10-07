# Mightling preprint

LaTeX source for the arXiv preprint describing the terminal coding agent of Mightling (by Dreamference).

```bash
make              # builds mling.pdf inside the texlive/texlive container (no local TeX needed)
make arxiv.tar.gz # source bundle for arXiv: mling.tex, references.bib and the generated mling.bbl
```

## Decisions for the author before submitting

These are deliberately left open; the draft makes a provisional choice for each.

1. **Author line.** Currently `Dreamference <dgxcoder@dreamference.ai>`. The draft took the name from
   the email address, so check the spelling, name order and whether to list the email at all.
2. **Affiliation.** Absent. Add one, or state "Independent researcher".
3. **Authorship.** Sole author. Add co-authors if anyone else contributed.
5. **Categories.** Suggested primary `cs.SE` (Software Engineering), cross-list `cs.DC`
   (Distributed, Parallel, and Cluster Computing). `cs.LG` is a possible alternative cross-list.
6. **Licence of the preprint.** arXiv asks for one at submission; CC BY 4.0 is the common choice.
7. **Repository availability.** The paper says the system is in a private repository. If it will be
   public at submission time, update the Availability section with the URL.
8. **Open measurements the paper admits to.** If they are done before submission, update
   §7 and §8 and drop the matching limitation:
   - task-level accuracy (e.g. a SWE-bench Verified subset);
   - a re-run of Codex's own test suite with `/cavemode` and `/night` in the tree, and the cause
     of its 16 app-server timeouts;
   - a run of the Java and .NET indexers, and the decode rate while an index run is active;
   - live Night Shift runs of a stall, a resumed task and parallel tasks;
   - what the server does with a session larger than its KV pool.

   Resolved since the first draft, and now reported as results: the first-run sign-in screen
   (fixed) and the memory needed by `rust-analyzer scip` on the Codex workspace (measured).
9. **The title carries no number** (decided 2026-10-02). It used to quote the patch series' size
   ("21 KB", then "27 KB"), which went stale each time a patch was added; the size is in the
   abstract and the evaluation table instead.

## Plain-text abstract (for the arXiv form, which does not accept LaTeX)

Terminal coding agents such as OpenAI's Codex CLI are open source, but they are built around a hosted model, a vendor account and a cloud of companion services. We describe Mightling, a coding agent whose model runs on one NVIDIA GB10 workstation (128 GB of CPU-GPU unified memory): a 27B-parameter model served locally through SGLang, with a 122B mixture-of-experts model on vLLM as the fallback. Source code, prompts and inference never leave the machine, and no vendor account is involved. Mightling is a fork of the Codex CLI (release rust-v0.158.0, 4,894 Rust source files, 1.93 million lines). Its central engineering choice is to keep the fork almost identical to upstream. The upstream tree is never edited. Each build exports it, applies a series of sixteen patches totalling 26.9 KB (27 files, 109 lines added and 47 removed), and links in a separate crate that does the actual work of localisation: model discovery, configuration, prompt rebranding, help-text rewriting, a release-based updater and refusal of cloud-bound commands. We report how the series shrank from an initial 406 KB and which techniques did it. We also report an inventory of the cloud dependencies we had to switch off, integration findings that are not visible from the documentation, and the host-safety layer that unified memory forced on us after model loads froze the machine. A 77-test live suite, covering every slash command, runs against the local model: 75 tests pass and 2 are skipped by design; three earlier failures exposed two test-harness bugs and one real first-run defect, since fixed. We then report three things built on the fork, each from the same launcher crate or beside it: a terse-answer mode that halves answers to questions and leaves coding work unchanged, a two-layer code index (a tree-sitter knowledge graph plus compiler-exact SCIP data) served by a router of its own, and an overnight task queue that works in git worktrees. We are explicit about what we have not yet measured, notably task-level coding accuracy.

## Where the numbers come from

Every figure in the paper was measured on the author's GB10 or read from this repository, first on
2026-09-28 and again on 2026-10-01 (commit `05b1c8e`). Re-derive them before submission if the
code has moved:

| Figure | Source |
|---|---|
| Upstream size (4,894 `.rs` files, 1.93M lines) | exported `codex-rs/` of `rust-v0.158.0`, excluding `mling/` |
| Patch series (16 patches, 26,933 B, 27 files, +109/−47; per-patch columns of Table 1) | `codex-patches/*.patch`, each through `git apply --numstat` |
| Smallest series (about 9 KB) | status line of `specs/DREAMFERENCE_MIGHTLING_CODEX.md` |
| Initial series (406,116 B; 395,156 B in one patch) | `git ls-tree -l b03ad9b codex-patches/` |
| Launcher (3,359 lines, 59 tests; `cave.rs` 566 and `night.rs` 769 lines) | `mling-rs/src/*.rs` |
| Code-index router (6,107 lines, 73 tests) | `mling-code-rs/src/`, `cargo test --locked` there |
| Binary sizes (315 MB, 93 MB, binary megabytes; 1.4 GB unstripped) | `~/.local/share/dreamference/mightling/bin/` |
| Python (87 modules, 23.2k lines) | `dreamference/**/*.py` |
| Python tests (511; 509 pass and 2 skip in 775.5 s with the server; 63 skip without it) | `pytest tests/`, 2026-10-01 |
| Live suite (77 tests: 75 pass, 2 skip; 651.3 s and 806.7 s) | source of the two timings not located in the repository or caches on 2026-10-01; the figures predate this revision, so re-run before submission. The full `pytest tests/` run of 2026-10-01 (509 pass, 2 skip) confirms 75 pass and 2 skip on the default model. The earlier 65/3/2 run is `~/.cache/dreamference/slash-tests.log` |
| Throughput (25.5 / 50.3 / 87.0 default; 23.8 / 49.9 / 53.1 fallback) | registry entries in `model_matrix_registry.py` |
| Host-safety thresholds | `psi_watchdog.py`, `vllm_server_manager.py` constants |
| Cave mode (output shares, 67:1 cost ratio, benchmark ratios, 35 of 36 checks) | `specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md` §1 |
| KV pool, compaction and session sizes, diffusion-compressor test | `specs/DREAMFERENCE_MIGHTLING_COMPACTION.md` §1 |
| Code index (recall, CLI and SQL latency, rust-analyzer time and memory, replay, indexers, query latency) | `specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md` §2 and §14.1 |
| SCIP OOM at 8 GB | `journalctl --user` scope records, 2026-09-28 23:05 and 23:07 |
| Night Shift (live run, parallelism, 34 runner tests) | `specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md` §11; `tests/test_night_shift.py` |
| Codex's own suite (20,380 tests, 20,362 passed, 16 timeouts) | `specs/DREAMFERENCE_MIGHTLING_CODEX.md`, "Results, 2026-10-01" |
| arXiv citations | checked against `export.arxiv.org` on 2026-09-28; `adeyemi2026cavewoman` on 2026-10-01 |
