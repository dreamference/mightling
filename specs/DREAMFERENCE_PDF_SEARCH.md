# Puffin PDF Search Tool — Technical Specification

**Status:** Draft v1 (Dreamference Sidecar architecture)
**Target:** Puffin (Dreamference Sidecar ecosystem)
**Estimated effort:** ~4-5 days (sidecar app, SearXNG integration, PDF parsing, text embedding, semantic chunking, SSRF hardened downloader)

---

## 1. Overview

Add a built-in `PDF Search` tool that lets the LLM search the internet for PDF documents, download them on the fly, extract their text, and return the most relevant excerpts. 

Built on the Dreamference sidecar architecture (like the Image Search and Gmail tools), this tool runs as an independent container (`dream-pdf-search`). It registers dynamically via Onyx's Custom Tool REST API. 

The tool queries the deployment's SearXNG instance with `filetype:pdf` filters, downloads the candidate PDFs with strict SSRF guards, extracts text using PyMuPDF, chunks the text, and runs it through the local Infinity embedding sidecar. The top-K most semantically relevant chunks are returned to the LLM as text along with source URLs and page numbers, enabling accurate, on-the-fly research without bloating the LLM's context window.

---

## 2. Architecture

```
LLM custom tool call {"queries": ["Sony Alpha a7 III user manual"]}
        │
        ▼
Onyx API Server calls POST http://dream-pdf-search:8769/search
        │
        ▼
[Inside dream-pdf-search container]
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
EMBED STEP: Infinity sidecar (puffin-siglip / text embedding model)
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

The sidecar is a FastAPI Python application (`dream-pdf-search`) running on port `8769`.

### 3.1 Tool Registration
In `onyx_runner.py`'s `configure` phase, Dreamference dynamically registers the tool:
```python
payload = {
    "name": "PDF Search",
    "description": "Search the web specifically for PDF documents (manuals, research papers, reports) and read their contents.",
    "custom_tool_url": "http://dream-pdf-search:8769/search",
}
# PUT to /api/admin/tool/custom
```

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
Queries `http://searxng:8080/search`. The sidecar modifies the LLM's query by appending `" filetype:pdf"` (or passing the appropriate SearXNG category flags) to ensure the search engine returns PDF files.

### 4.2 Infinity Embeddings (Text)
Queries the existing Infinity sidecar (`http://puffin-siglip:9100/embeddings`). 
*Note: Infinity supports serving multiple models. The sidecar should be configured to load a fast text embedding model (e.g., `BAAI/bge-small-en-v1.5`) alongside SigLIP.*
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
- `dream onyx configure` successfully registers the tool.
- End-to-end local test against a mock SearXNG instance returning a test PDF. 
- Verify the tool extracts text, embeds it, and returns the top chunk.
- Verify memory is freed (no PDF files left on disk).
