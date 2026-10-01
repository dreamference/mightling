# Puffin preprint

LaTeX source for the arXiv preprint describing the terminal coding agent of Puffin (by Dreamference).

```bash
make              # builds puffin.pdf inside the texlive/texlive container (no local TeX needed)
make arxiv.tar.gz # source bundle for arXiv: puffin.tex, references.bib and the generated puffin.bbl
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
   - the fix for the first-run sign-in screen;
   - the memory needed by `rust-analyzer scip` on the Codex workspace.

## Plain-text abstract (for the arXiv form, which does not accept LaTeX)

Terminal coding agents such as OpenAI's Codex CLI are open source, but they are built around a hosted model, a vendor account and a cloud of companion services. We describe Puffin, a coding agent whose model runs on one NVIDIA GB10 workstation (128 GB of CPU-GPU unified memory): a 122B-parameter mixture-of-experts model served locally through vLLM. Source code, prompts and inference never leave the machine, and no vendor account is involved. Puffin is a fork of the Codex CLI (release rust-v0.158.0, 4,894 Rust source files, 1.93 million lines). Its central engineering choice is to keep the fork almost identical to upstream. The upstream tree is never edited. Each build exports it, applies a series of ten patches totalling 15.4 KB (14 files, 46 lines added and 42 removed), and links in a separate crate that does the actual work of localisation: model discovery, configuration, prompt rebranding, help-text rewriting, a release-based updater and refusal of cloud-bound commands. We report how the series shrank from an initial 406 KB and which techniques did it. We also report an inventory of the cloud dependencies we had to switch off, integration findings that are not visible from the documentation, and the host-safety layer that unified memory forced on us after model loads froze the machine. A 70-test live suite, covering every slash command, runs against the local model: 65 tests pass, 2 are skipped by design, and the 3 failures expose two test-harness bugs and one real first-run defect. We close with the design of a two-layer code index (a tree-sitter knowledge graph plus compiler-exact SCIP data) and are explicit about what we have not yet measured, notably task-level coding accuracy.

## Where the numbers come from

Every figure in the paper was measured on the author's GB10 or read from this repository on
2026-09-28. Re-derive them before submission if the code has moved:

| Figure | Source |
|---|---|
| Upstream size (4,894 `.rs` files, 1.93M lines) | exported `codex-rs/` of `rust-v0.158.0`, excluding `puffin/` |
| Patch series (15 patches, 24,800 B; 14 patches and 21,807 B before `0017-cave-mode`) | `codex-patches/*.patch` |
| Initial series (406,116 B; 395,156 B in one patch) | `git ls-tree -l b03ad9b codex-patches/` |
| Launcher (1,403 lines, 24 tests) | `puffin-rs/src/*.rs` |
| Binary sizes (315 MB, 93 MB; 1.4 GB unstripped) | `~/.local/share/dreamference/puffin/bin/` |
| Python tests (427; 364 pass, 63 skip without server) | `pytest tests/` |
| Live suite (70 tests: 65/3/2, 731.6 s) | `~/.cache/dreamference/slash-tests.log` |
| Throughput (23.8 / 49.9 / 53.1 tok/s) | comment above `DEFAULT_MODEL_ALIAS` in `model_matrix_registry.py` |
| Host-safety thresholds | `psi_watchdog.py`, `vllm_server_manager.py` constants |
| SCIP OOM at 8 GB | `journalctl --user` scope records, 2026-09-28 23:05 and 23:07 |
| arXiv citations | checked against `export.arxiv.org` on 2026-09-28 |
