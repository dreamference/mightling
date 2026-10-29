# The index benchmark, `scripts/bench_index.py`

A CPU benchmark of the code index (`ling-code`) and the local file index (`ling-docs`) on fixed corpora. It changes no product code and touches no real state: `HOME`, `CODEX_HOME`, the code index's state and cache directories and the file index's collection file are all in a temp folder. Only the pinned tools, the embedding model and the PDFium libraries under the install directory are read.

- `ling-code`: this repository's tree at `HEAD` (`git archive`, committed into a scratch repository) is indexed with `ling-code index --wait`; then 50 queries (`def`, `refs`, `callers` in turn, names drawn from the tree's own definitions with a fixed seed) run for 5 rounds.
- `ling-docs`: 2,000 generated files (1,000 text, 700 markdown, 300 PDF; fixed seed) are indexed with `ling-docs index` (`MIGHTLING_DOCS_TEST_HOST=direct`, the workers as plain children, the measurement mode); then 50 `search` queries run for 5 rounds.

```bash
# build both crates in release mode first (cargo build --release --locked, own CARGO_TARGET_DIR)
systemd-run --user --scope --slice=mightling-batch.slice -p MemoryMax=6G nice -n 10 \
  python3 scripts/bench_index.py --code-bin T/code/release/ling-code \
  --docs-bin T/docs/release/ling-docs --json-out run1.json --md-out run1.md
python3 scripts/bench_index.py --summary run1.json run2.json run3.json   # the spread
```

Things to know:

- Peak RSS is the largest sum over the command's process tree plus the processes of `mightling-index-*` units: the code index runs its indexers as transient systemd units, outside the command's tree, so `/usr/bin/time -v` alone shows 30 MB for it. Both numbers are in the JSON.
- A query's latency is the whole CLI invocation. For `ling-docs search` it includes loading the embedder.
- `ling-docs add` starts a background index run that holds the host-wide lock, so the script writes `docs.toml` itself.
- Run it on a quiet machine: with other work running (load average above 12) the same corpus varied by a factor of 3 between runs.
- `perf` is not usable here (`kernel.perf_event_paranoid` is 4, no sudo), so the script attributes CPU seconds per process name instead of per function.
