# ling-docs evaluation set (Phase 0)

The fixed set the local file index is measured against
([DREAMFERENCE_MIGHTLING_LOCAL_INDEX](../../specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md) §12, results in §15).
The documents are **not** in the repository: `manifest.json` names each one with its URL, SHA-256 and
licence, and `fetch.py` rebuilds the corpus from it. Only redistributable sources are used.

| Format | Documents | Source and licence |
|---|---|---|
| PDF | 31 | RFC 9110, 9114, 9293 (IETF Trust, BCP 78); NIST SP 800-63B, 800-207, 800-88r1, CSWP 29, FIPS 197; BLS Employment Situation, EIA Monthly Energy Review (298 pages of tables), Census P60-282 (U.S. government, public domain); 15 arXiv cs.CL papers whose record says CC BY 4.0 (three two-column); 5 Wikipedia PDF renderings (CC BY-SA 4.0) |
| HTML | 59 | Wikipedia articles pinned by revision id (CC BY-SA 4.0) |
| HTML, other languages | 24 | The Coffee, Volcano and Photosynthesis articles in Arabic, Chinese, French, German, Japanese, Russian, Spanish and Swedish, pinned by revision id (CC BY-SA 4.0); the English editions are in the set too, as distractors |
| Markdown | 50 | Chapters of *The Rust Programming Language* at a pinned commit (MIT OR Apache-2.0) |
| reStructuredText | 10 | PEPs at a pinned commit (public domain or CC0) |
| Plain text | 5 | RFC 2616, 4648, 5321, 6455, 7540 (IETF Trust) |
| DOCX | 15 | Generated from pinned Wikipedia articles (CC BY-SA 4.0, attributed in the file) |
| Email | 1 `.mbox` (30 messages) + 10 `.eml` | Authored for this set, fictional company (CC0) |
| CSV | 5 | Authored and seeded (CC0) |
| Hostile PDFs | 10 | Synthetic (CC0): empty, truncated, garbage, bad xref, flate bomb (1 GB of spaces in 1 MB), 100,000-deep nesting, page-tree loop, 20,000 pages, 2 million text operators, AES-256 encrypted |
| OCR fixtures | 39 pages | One page each of an English prose, two-column, table and RFC page and of Wikipedia's ling article in German, Swedish, French, Russian, Ukrainian, Chinese, Japanese, Korean and Arabic (CC BY-SA 4.0, `ocr/sources.json`), each as a 200 dpi scan, a 170 dpi phone photo and a poor 100 dpi copy (`make_ocr_fixtures.py`); the source's text layer is the ground truth |

210 documents plus the 10 hostile files. `questions.jsonl` has 181 questions, each with the expected
document, the locator (page, line range, section, message id or row range) and an answer snippet:
a verbatim span of the source, matched after normalisation (NFKC, lower case, letters and digits of
any script), so a hit does not depend on how an extractor breaks lines or hyphenates. 109 are about
the English documents (54 *lexical*, sharing the passage's distinctive words, and 55 *paraphrase*,
asking in other words); 72 are about the other-language articles: 24 lexical and 24 paraphrase
questions in the document's own language, and 24 *cross-lingual* questions asked in English
(`lang` gives the document's language). The split is what tells keyword search, meaning-based search
and cross-language search apart.

## Reproducing

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python fetch.py                       # corpus/, reference/, hostile/ (~150 MB)
(cd rustpdf && cargo build --release)          # the two pure-Rust PDF crates, for comparison
bash run_pdf.sh && venv/bin/python analyze_pdf.py && venv/bin/python bcp14.py <engines…>
venv/bin/python rtl_score.py ocr/sources/wikipedia-ar.pdf          # right-to-left text
HF_HOME=$PWD/hf venv/bin/python download_models.py    # the only networked step after fetch
bash run_chunks.sh && bash run_probes.sh && bash run_embed_all.sh && bash run_sweep.sh <model…>
bash run_probes_big.sh && bash bm25_engines.sh
bash run_batch_probe.sh [<model>] && bash run_sweep_arctic.sh && bash sweep_eval.sh <model-basename>
venv/bin/python fusion_weights.py chunks/pdfium-ordered-512.json emb/pdfium-ordered-512__snowflake-arctic-embed-m-v2.0-int8
venv/bin/python retrieval_report.py && venv/bin/python scale.py
bash tess_setup.sh && venv/bin/python ocr_models.py && venv/bin/python make_ocr_fixtures.py
bash run_ocr.sh v6-tiny v6-small v6-medium v5-oracle v5-all tesseract osd && venv/bin/python ocr_report.py
venv/bin/python make_downloads.py corpus downloads && venv/bin/python filter_bench.py
venv/bin/python first_index_probe.py synthetic corpus downloads
```

Every extraction runs in `bwrap --unshare-all` (no network, home hidden) inside a
`systemd-run --user --scope` with `MemoryMax=1G`, no swap, a 30 s kill timeout and `nice`/`ionice`.
Every embedding run is pinned to four Cortex-X925 cores (`taskset -c 15-18`), four ONNX Runtime
threads on the CPU provider (CUDA hidden), `MemoryMax=4G`, no network; the OCR runs likewise on
cores 5-8 with `MemoryMax=3G`. `collect/` holds the scripts that chose the corpus (the arXiv licence
filter reads each paper's OAI-PMH record; `collect_multi.py` pins the other-language articles).
`first_index_probe.py` and `folder_profile.py` take any folder; on a person's own `~/Documents` and
`~/Downloads` only `folder_profile.py` is run, which reads metadata (no file is opened).
