# ling-docs evaluation set (Phase 0)

The fixed set the local file index is measured against
([DREAMFERENCE_MIGHTLING_LOCAL_INDEX](../../specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md) §12, results in §15).
The documents are **not** in the repository: `manifest.json` names each one with its URL, SHA-256 and
licence, and `fetch.py` rebuilds the corpus from it. Only redistributable sources are used.

| Format | Documents | Source and licence |
|---|---|---|
| PDF | 31 | RFC 9110, 9114, 9293 (IETF Trust, BCP 78); NIST SP 800-63B, 800-207, 800-88r1, CSWP 29, FIPS 197; BLS Employment Situation, EIA Monthly Energy Review (298 pages of tables), Census P60-282 (U.S. government, public domain); 15 arXiv cs.CL papers whose record says CC BY 4.0 (three two-column); 5 Wikipedia PDF renderings (CC BY-SA 4.0) |
| HTML | 59 | Wikipedia articles pinned by revision id (CC BY-SA 4.0) |
| Markdown | 50 | Chapters of *The Rust Programming Language* at a pinned commit (MIT OR Apache-2.0) |
| reStructuredText | 10 | PEPs at a pinned commit (public domain or CC0) |
| Plain text | 5 | RFC 2616, 4648, 5321, 6455, 7540 (IETF Trust) |
| DOCX | 15 | Generated from pinned Wikipedia articles (CC BY-SA 4.0, attributed in the file) |
| Email | 1 `.mbox` (30 messages) + 10 `.eml` | Authored for this set, fictional company (CC0) |
| CSV | 5 | Authored and seeded (CC0) |
| Hostile PDFs | 10 | Synthetic (CC0): empty, truncated, garbage, bad xref, flate bomb (1 GB of spaces in 1 MB), 100,000-deep nesting, page-tree loop, 20,000 pages, 2 million text operators, AES-256 encrypted |

186 documents plus the 10 hostile files. `questions.jsonl` has 109 questions, each with the expected
document, the locator (page, line range, section, message id or row range) and an answer snippet:
a verbatim span of the source, matched after normalisation (NFKC, lower case, letters and digits
only), so a hit does not depend on how an extractor breaks lines or hyphenates. 54 questions are
*lexical* (they share the passage's distinctive words) and 55 *paraphrase* (they ask in other
words); the split is what tells keyword search and meaning-based search apart.

## Reproducing

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python fetch.py                       # corpus/, reference/, hostile/ (~150 MB)
(cd rustpdf && cargo build --release)          # the two pure-Rust PDF crates, for comparison
bash run_pdf.sh && venv/bin/python analyze_pdf.py   # every engine on every PDF, sandboxed
HF_HOME=$PWD/hf venv/bin/python download_models.py <models…>   # the only networked step after fetch
bash run_chunks.sh && bash run_embed_all.sh && bash run_sweep.sh
venv/bin/python scale.py && venv/bin/python report.py
```

Every extraction runs in `bwrap --unshare-all` (no network, home hidden) inside a
`systemd-run --user --scope` with `MemoryMax=1G`, no swap, a 30 s kill timeout and `nice`/`ionice`.
Every embedding run is pinned to four Cortex-X925 cores (`taskset -c 15-18`), four ONNX Runtime
threads on the CPU provider (CUDA hidden), `MemoryMax=4G`, no network. `collect/` holds the scripts
that chose the corpus (the arXiv licence filter reads each paper's OAI-PMH record).
