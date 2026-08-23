import re

with open('specs/DREAMFERENCE_IMAGE_SEARCH.md', 'r') as f:
    original = f.read()

# I will write the updated content
updated = """# Puffin Image Search Tool — Technical Specification

**Status:** Draft v13 (Moved from Onyx internal tool to standalone Dreamference Sidecar)
**Target:** Puffin (Dreamference Sidecar ecosystem)
**Estimated effort:** ~5–6 days (tool, thumbnails-first pipeline, SigLIP pre-filter + warm-up/cache, pHash dedupe, speculative caching, vision ranking, source-URL dedupe, GC)

---

## 1. Overview

Add a built-in `Image Search` tool that lets the LLM search the internet for images and display them inline in chat. Instead of modifying Onyx's internal Python codebase, this is implemented as an independent **Dreamference Sidecar container (`dream-image-search`)** and registered dynamically via Onyx's Custom Tool REST API (similar to the Gmail integration).

The tool queries the deployment's SearXNG instance (image category) with a **wide candidate pool (up to 30)**, fetches the candidates' **thumbnails in-memory**, **pre-filters them with a local SigLIP model**, **collapses visual near-duplicates with perceptual hashing**, **re-ranks the survivors with the served multimodal model (vision pass)**, and only then **downloads the ranked winners' full images to a dedicated local Docker volume**, deduplicating against previously cached images by source URL. 

Results return to the LLM as a JSON response containing Markdown image embeddings (`![alt](/puffin-images/123.jpg)`). Onyx natively renders these. A custom Nginx route injected into Onyx's web proxy serves the images directly from the sidecar.

Caching locally eliminates the two failure modes of hotlinking: cross-origin embedding blocks (CORP/hotlink protection returning 403) and link rot. Images render permanently.

---

## 2. Architecture

```
LLM custom tool call {"queries": ["puffin bird flying"]}
        │
        ▼
Onyx API Server calls POST http://dream-image-search:8768/search
        │
        ▼
[Inside dream-image-search container]
SearXNGClient.search_images (categories=images)
        │
        ▼
round-robin merge, dedupe by image_url, cap CANDIDATE_POOL_SIZE (30)
        │
        ▼
DEDUPE LOOKUP: check local SQLite DB for source_url
  → known images reuse existing file_id, skip ALL network work
        │
        ▼
THUMB STEP: parallel in-memory fetch of thumbnails for the rest
  → SSRF-guarded, 512 KB / 5 s caps → drop unfetchable candidates
        │
        ▼
SIGLIP PRE-FILTER: embed via the puffin-siglip Infinity sidecar
  (query text + thumbnail batch) → cosine scored → keep top RANK_CANDIDATES (8)
        │
        ▼
PHASH COLLAPSE: pHash all kept thumbnails, Hamming ≤ 8 → merge
        │
        ▼
RANK STEP ─┬─ SPECULATIVE PREFETCH (concurrent)
  (vision)  │   hardened full downloads of ALL 8 rank candidates
            │   start when the rank call is dispatched
  one vision API call to local vLLM: 8 thumbnails + query → strict-JSON ranking
        │
        ▼
RECONCILE: verdict ∩ prefetch
  → cancel in-flight fetches of rank-dropped candidates
        │
        ▼
PERSIST: winners → write to local Docker volume (/data)
  record phash + source_url in sidecar SQLite DB
        │
        ▼
Return Custom Tool Response (JSON):
{
  "response": "![title](/puffin-images/{file_id}.jpg)\\n...",
  "instructions": "Embed these exactly as provided."
}
        │
        ▼
Onyx receives response, streams to chat.
Browser requests /puffin-images/{file_id}.jpg
        │
        ▼
Onyx Nginx Proxy routes to http://dream-image-search:8768/images/{file_id}.jpg
```

---

## 3. Sidecar Service Design

The sidecar is a FastAPI Python application (`dream-image-search`) running on port `8768`.

### 3.1 Nginx Routing
In `onyx_runner.py` during `configure`, Dreamference injects a tiny `.conf` snippet into Onyx's Nginx proxy:
```nginx
location /puffin-images/ {
    proxy_pass http://dream-image-search:8768/images/;
}
```
This elegantly sidesteps CORS/CORP issues and avoids modifying Onyx's internal Postgres FileStore or `access.py` permissions.

### 3.2 Tool Registration
In `onyx_runner.py`'s `configure_gmail` equivalent, we dynamically register the custom tool:
```python
payload = {
    "name": "Image Search",
    "description": "Search the web for images. Use when user asks for pictures.",
    "custom_tool_url": "http://dream-image-search:8768/search",
}
# PUT to /api/admin/tool/custom
```

### 3.3 The Sidecar Storage (SQLite & File GC)
The sidecar mounts a local Docker volume `/data`. 
- `/data/images/` stores the raw JPEGs.
- `/data/db.sqlite` stores the deduplication metadata (source URL, file ID, pHash, timestamp).
- **Garbage Collection:** The FastAPI app runs a background `asyncio` task every hour that deletes files older than 7 days (or based on volume size cap). Since images are cached locally and persist for 7 days, they cover the active lifetime of a chat discussion.

---

## 4. Provider & Inference Interactions

Because the sidecar is independent, it communicates with the rest of the stack over standard HTTP:

### 4.1 SearXNG Client
Queries `http://searxng:8080/search`. Follows the same wide-funnel rules (30 candidates). Includes the same SSRF exemptions for `image_proxy` routing.

### 4.2 SigLIP Pre-Filter
Queries `http://puffin-siglip:9100/embeddings` (the Infinity sidecar). Identical logic to the original spec: 30 candidates, cosine scoring, keep top 8.

### 4.3 Vision Re-ranking
Queries the local vLLM API server (`http://api_server:8000/v1/chat/completions` or directly hitting vLLM). Passes the 8 thumbnails as base64 images in an OpenAI-compatible request to get the strict-JSON ranking array.

---

## 5. Security & Hardening

The sidecar implements the identical hardened downloader logic for fetching images from the internet:
- DNS pinning, private-IP SSRF rejection, redirect capping, and magic-byte sniffing. 
- Prevents malicious image URLs from probing the internal Docker network.

---

## 6. Testing Plan

**Unit Tests (Sidecar):**
- Mock FastAPI endpoints, SearXNG client, SigLIP scoring, and vLLM ranking.
- Hardened downloader SSRF assertions.
- SQLite GC cleanup routines.

**Integration:**
- `dream onyx configure` successfully registers the tool and injects the Nginx route.
- Assistant seamlessly uses the tool and renders `![alt](/puffin-images/...)` inline.
- Grid/Timeline natively supported via Onyx's built-in `CustomToolStart/Delta` packet rendering. 

"""

with open('specs/DREAMFERENCE_IMAGE_SEARCH.md', 'w') as f:
    f.write(updated)
