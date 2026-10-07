#!/bin/bash
# The full PDF extractor benchmark (all engines, corpus + hostile set).
D="$(dirname "$(readlink -f "$0")")"
"$D/venv/bin/python" "$D/bench_pdf.py" pdfium,pymupdf,pymupdf-sort,pdftotext,pdf_oxide,pdf-extract,pypdf,pdfminer
