# Mightling PDF Search Tool — Technical Specification

**Status:** Draft v1: **not implemented.** Nothing in `dreamference/` implements PDF search, as of 2026-09-28. The facts below about existing infrastructure (container names, tool registration, SearXNG, Infinity) were corrected against the code on that date; the design itself is unchanged.
**Target:** Mightling (sidecar ecosystem)
**Estimated effort:** ~4-5 days (sidecar app, SearXNG integration, PDF parsing, text embedding, semantic chunking, SSRF hardened downloader)

---

## 1. Overview

Add a built-in `PDF Search` tool that lets the LLM search the internet for PDF documents, download them on the fly, extract their text, and return the most relevant excerpts. 

Built on Mightling's sidecar architecture (like the Image Search and Gmail tools), this tool would run as an independent container (`dreamference-pdf-search`; the deployment names its sidecars `dreamference-*`). It registers dynamically via Onyx's Custom Tool REST API. 

The tool queries the deployment's SearXNG instance with `filetype:pdf` filters, downloads the candidate PDFs with strict SSRF guards, extracts text using PyMuPDF, chunks the text, and runs it through the local Infinity embedding sidecar. The top-K most semantically relevant chunks are returned to the LLM as text along with source URLs and page numbers, enabling accurate, on-the-fly research without bloating the LLM's context window.

---

## 2. Architecture

```
LLM custom tool call {"queries": ["Sony Alpha a7 III user manual"]}
        │
        ▼
Onyx API Server calls POST http://dreamference-pdf-search:8769/search
        │
        ▼
[Inside dreamference-pdf-search container]
SearXNGClient.search_web (with query modifications e.g. "ext:pdf")
        │
        ▼
Filter results for URLs ending in .pdf, cap at CANDIDATE_POOL_SIZE (5)
        │
        ▼
DOWNLOAD STEP: parallel hardened download of PDFs
  → SSRF-guarded, 20 MB size cap, 10s timeout
        │
        ▼
PARSE STEP: PyMuPDF extracts text + page numbers
  → Chunk text into overlapping segments (e.g. 500 words)
        │
        ▼
EMBED STEP: a text-embedding server (see §4.2: the Infinity sidecar cannot run on GB10)
  → Embed all chunks and the search query
        │
        ▼
SCORE & SELECT: Cosine similarity
  → Select top 5-10 chunks across all downloaded PDFs
        │
        ▼
Return Custom Tool Response (JSON):
{
  "response": "Here are the relevant excerpts:\\n\\n[Source 1, Page 4]...\\n",
  "instructions": "Synthesize this information and cite the source URLs."
}
        │
        ▼
Onyx receives response, streams to chat.
```

---

## 3. Sidecar Service Design

The sidecar would run on port `8769`. The existing sidecars (Gmail, image search) are a **single stdlib-HTTP file** in a stock `python:3-slim` container, not FastAPI. Following that convention means a PDF library installed at start, the way image search installs Pillow into `/tmp`.

### 3.1 Tool Registration
Onyx's custom-tool API takes an **OpenAPI document**, not a `custom_tool_url`. Registration should mirror `enable_image_search()` / `enable_gmail_search()`:
- `openapi_definition()` in the service module, with one `pdf_search` POST operation;
- sent to `POST /admin/tool/custom`, with lookup-then-`PUT` so re-runs update rather than duplicate;
- a `custom_headers` shared secret (e.g. `X-Mightling-PDF-Token`) generated once into the data directory;
- a `--no-pdf-search` opt-out on `mling-admin chat configure`, like the other tools.

### 3.2 PDF Parsing and Chunking
The sidecar uses `pymupdf` (fitz) to extract text. 
- Fast, robust parsing that retains page numbers.
- Text is split into overlapping chunks (e.g., 500 words, 50-word overlap) using a simple sentence or word boundary chunker.
- Each chunk preserves metadata: `source_url`, `title`, and `page_number`.

### 3.3 Ephemeral Storage
Unlike Image Search, the PDFs downloaded by this tool are strictly ephemeral. They are held in memory (or a `/tmp` ramdisk) just long enough to extract the text, and then immediately discarded. No persistent storage or garbage collection is required.

---

## 4. Provider & Inference Interactions

### 4.1 SearXNG Client
Queries `http://dreamference-searxng:8080/search` (`SEARXNG_CONTAINER_URL`), which is reachable because SearXNG is joined to Onyx's network. The sidecar modifies the LLM's query by appending `" filetype:pdf"` (or passing the appropriate SearXNG category flags) to ensure the search engine returns PDF files.

### 4.2 Infinity Embeddings (Text)
The draft planned to reuse the image-search Infinity sidecar (`dreamference-siglip`, `http://dreamference-siglip:9100`) with a text model loaded alongside SigLIP. **That sidecar cannot start on GB10.** Infinity publishes amd64 images only, and GB10 is aarch64 (`DREAMFERENCE_IMAGE_SEARCH.md` §7). Options that do work here:
- a small torch/transformers or ONNX text-embedding sidecar;
- reusing `nomic-embed-text-v1.5`, which the context engine already runs on this machine.
The sidecar embeds the search query and the PDF chunks, calculating cosine similarity locally (NumPy) to surface the most relevant excerpts.

---

## 5. Security & Hardening

Because the tool fetches arbitrary PDFs from the internet and parses them locally, security is critical:
1. **SSRF Guard:** The same hardened downloader used in Image Search (DNS pinning, loopback rejection, RFC1918 block) is used to prevent the sidecar from probing the internal Docker network.
2. **Resource Limits:** 
   - `MAX_PDF_SIZE_BYTES = 20 * 1024 * 1024` (20 MB).
   - `MAX_DOWNLOAD_TIME = 10s`.
   - `MAX_PARSE_TIME = 5s`.
3. **No Execution:** PDFs are parsed purely for text extraction via PyMuPDF. Javascript execution and rendering are strictly disabled during parsing to prevent exploitation of parser vulnerabilities.

---

## 6. Testing Plan

**Unit Tests (Sidecar):**
- Mock FastAPI endpoints, SearXNG client.
- Text chunking logic (verifying overlap and page number retention).
- Cosine similarity sorting.
- Hardened downloader SSRF assertions.

**Integration:**
- `mling-admin chat configure` successfully registers the tool.
- End-to-end local test against a mock SearXNG instance returning a test PDF. 
- Verify the tool extracts text, embeds it, and returns the top chunk.
- Verify memory is freed (no PDF files left on disk).
