# Puffin Image Search Tool — Technical Specification

**Status:** Draft v12 (sidecar implementation switched to Infinity — off-the-shelf embedding server replaces the custom FastAPI app; zero custom serving code)
**Target:** Puffin (Onyx fork), backend + minimal frontend
**Estimated effort:** ~5–6 days (tool, thumbnails-first pipeline, SigLIP pre-filter + warm-up/cache, pHash dedupe, speculative caching, vision ranking, thumb persistence, source-URL dedupe, DNS pinning, GC, grid renderer)
**Verified against:** `onyx-dot-app/onyx` main branch, 2026-08-23

---

## 1. Overview

Add a built-in `ImageSearchTool` that lets the LLM search the internet for images and display them inline in chat. The tool queries the deployment's SearXNG instance (image category) with a **wide candidate pool (up to 30)**, fetches the candidates' **thumbnails in-memory**, **pre-filters them with a local SigLIP model**, **collapses visual near-duplicates with perceptual hashing**, **re-ranks the survivors with the served multimodal model (vision pass)**, and only then **downloads the ranked winners' full images into Onyx's FileStore**, deduplicating against previously cached images by source URL. Results return to the LLM with same-origin URLs (`/api/chat/file/{file_id}`). The LLM embeds selected images as markdown (`![alt](url)`), which the existing chat renderer displays without modification.

Caching locally eliminates the two failure modes of hotlinking: cross-origin embedding blocks (CORP/hotlink protection returning 403) and link rot. Images render from Puffin's own origin, permanently.

The thumbnails-first inversion is the headline change of v7: full-image downloads move **off the pre-rank critical path** and shrink to winners only. v9 removes the last avoidable serialization: full downloads of the 8 rank candidates start **the moment the rank call is dispatched** (speculative prefetch, §6.6.2), so the critical path is `max(30 thumb fetches) + siglip + max(rank, full downloads)` — rank and downloads overlap instead of adding. v9 also persists the ≤512px rank thumbnails so the timeline grid stops fetching multi-MB originals to paint 96px thumbs (§6.6.7), and moves the SigLIP model out of the API process entirely — served by an off-the-shelf **Infinity** container (§6.3) that warms at start and batches dynamically, with a thin scoring client in the tool.

### Goals
- LLM-invocable image search, **always enabled on every assistant** — no per-assistant toggle to forget; the LLM decides per-request whether to call it.
- Inline image display in assistant answers.
- **Images cached locally and served same-origin** — immune to hotlink blocking and rot; chats stay intact forever. Only ranked winners are stored.
- **Wide funnel, cheap filter**: 24–30 candidates scored locally by SigLIP in milliseconds, so "the right image wasn't even in the candidates" failures die without raising vision-LLM cost.
- **Visually distinct results**: pHash collapses the same image re-hosted/resized 3–4 times, so rank slots and embed slots go to different images.
- **Every result set is vision re-ranked** — the served vLLM model is multimodal, so the tool always looks at the candidates before the answer model does; irrelevant thumbnails-of-something-else never reach the answer.
- **Latency engineered end-to-end**: speculative prefetch overlaps rank and downloads; SigLIP is warm before the first call and cached across calls; the grid renders from ~30–60 KB thumbs, not 8 MB originals.
- Fully local: SearXNG + SigLIP sidecar + served vLLM — every model in this architecture is served, none loaded into the API process; no API keys, no per-query cost, no external search dependency.
- Shipped in Puffin's Docker images — no per-deployment MCP setup.

### Non-goals (v1)

- **Alternative search providers.** SearXNG is the only supported image-search backend — no Serper/Brave clients, no provider designation column, no admin toggle. Puffin's stock deployment runs SearXNG for web search already; image search rides the same instance. Adding a provider later means implementing `ImageSearchProvider` on its client plus a resolution rule — nothing in the pipeline assumes SearXNG beyond the client.

Everything else is in scope; remaining open ideas are in §13.

---

## 2. Background: verified codebase facts

| Fact | Location |
|---|---|
| Tool base class + `ToolResponse(rich_response, llm_facing_response)` | `backend/onyx/tools/interface.py`, `backend/onyx/tools/models.py` |
| `WebSearchTool` reference implementation (358 lines): provider loading from DB, parallel queries, round-robin merge, packet emission, `ToolCallException` error contract | `backend/onyx/tools/tool_implementations/web_search/web_search_tool.py` |
| Search provider clients (Serper, SearXNG, Brave, Exa, Google PSE, Tavily) | `backend/onyx/tools/tool_implementations/web_search/clients/` |
| Provider factory + active-provider DB lookup | `.../web_search/providers.py`, `backend/onyx/db/web_search.py` |
| Built-in tool registry: `BUILT_IN_TOOL_TYPES` union + `BUILT_IN_TOOL_MAP` keyed by class name (= `in_code_tool_id`) | `backend/onyx/tools/built_in_tools.py` |
| Built-in tools are seeded into the `tool` table by Alembic migration (insert-or-update by `in_code_tool_id`) | `backend/alembic/versions/d09fc20a3c66_seed_builtin_tools.py` |
| Chat markdown renderer overrides `a`, `p`, `code` only — default `img` rendering is active, so `![alt](url)` displays inline | `web/src/app/app/message/messageComponents/markdownUtils.tsx` |
| Tool icons mapped by `in_code_tool_id` in `getIconForAction`, unknown tools fall back to `SvgCpu` | `web/src/app/app/services/actionUtils.ts` |
| Streaming packets: `SearchToolStart`, `SearchToolQueriesDelta`, `SearchToolDocumentsDelta`, `CustomToolStart/Delta` | `backend/onyx/server/query_and_chat/streaming_models.py` |
| `save_file_from_url(url)` downloads into FileStore (used by `ImageGenerationTool`); `save_files()` parallelizes; **no SSRF/size/content-type guards** — plain `requests.get` | `backend/onyx/file_store/utils.py:242` |
| `build_frontend_file_url(file_id)` → `/api/chat/file/{file_id}` — how image gen rewrites URLs for the frontend | `backend/onyx/file_store/utils.py:390` |
| `GET /api/chat/file/{file_id}` serves FileStore files same-origin, behind auth, with `Cache-Control: private, max-age=31536000, immutable` + ETag | `backend/onyx/server/query_and_chat/chat_backend.py:1142` |
| Access check `user_can_access_chat_file` has an explicit allow branch for `FileRecord` origin `CHAT_IMAGE_GEN` | `backend/onyx/access/access.py:221` |
| FileStore backends: Postgres (default), S3, GCS, Azure Blob | `backend/onyx/file_store/` |
| Chat-session deletion deletes `ChatMessage.files` blobs inline (skipping `user_file_id` entries) — the hook point for GC | `backend/onyx/db/chat.py:211` |
| `FileRecord.file_metadata` is JSONB — indexable for source-URL dedupe | `backend/onyx/db/models.py:4819` |
| Packet pattern: `BaseObj` subclasses with `Literal` type discriminators + `StreamingType` enum | `backend/onyx/server/query_and_chat/streaming_models.py` |
| `get_default_llm_with_vision()` — returns an image-capable LLM (designated vision provider first, else first vision-capable provider), or `None` | `backend/onyx/llm/factory.py:234` |
| OpenAI-style multimodal content parts (`ImageContentPart` with `image_url` + `detail`) — base64 data URLs work through LiteLLM to vLLM | `backend/onyx/llm/models.py` |

---

## 3. Architecture

```
LLM tool call {"queries": ["puffin bird flying"]}
        │
        ▼
ImageSearchTool.run()
        │  parallel per query
        ▼
SearXNGClient.search_images (categories=images)
        │  list[ImageSearchResult]
        ▼
round-robin merge, dedupe by image_url, cap CANDIDATE_POOL_SIZE (30)
        │
        ▼
DEDUPE LOOKUP: indexed match on file_metadata.source_url
  → known images reuse existing file_id, skip ALL network work;
    their rank thumbnails come from stored FileStore bytes
        │
        ▼
THUMB STEP: parallel in-memory fetch of thumbnails for the rest
  (thumbnail_src; fallback to image_url only when thumbnail_src absent)
  → SSRF-guarded, 512 KB / 5 s caps → drop unfetchable candidates
        │
        ▼
SIGLIP PRE-FILTER: embed via the puffin-siglip Infinity sidecar
  (OpenAI-style /embeddings: query text + thumbnail batch),
  cosine scored client-side → keep top RANK_CANDIDATES (8)
  → fallback on sidecar error/timeout: provider order, logged ERROR
        │
        ▼
PHASH COLLAPSE: pHash all kept thumbnails, Hamming ≤ 8 → merge
  near-duplicates (keep highest SigLIP score), backfill from
  next-scored candidates to refill to 8 distinct images
        │
        ▼
RANK STEP ─┬─ SPECULATIVE PREFETCH (concurrent, §6.6.2)
  (vision)  │   hardened full downloads of ALL 8 rank candidates
            │   start when the rank call is dispatched, SigLIP order
  one vision call: 8 thumbnails + query → strict-JSON ranking
  → drop judged-irrelevant, reorder best-first, cap RANK_MAX_RESULTS (6);
    ranked leftovers kept as RESERVES
  → fallback chain: vision rank → SigLIP order → provider order
        │
        ▼
RECONCILE: verdict ∩ prefetch
  → winners' downloads usually already complete (verdict arrives last)
  → cancel in-flight fetches of rank-dropped candidates (chunk-boundary abort)
  → failed winner → promote next reserve (its download is already running)
  → stored-but-dropped files: not attached to the message → nightly sweep reclaims
        │
        ▼
PERSIST: winners → FileStore (origin CHAT_IMAGE_SEARCH),
  phash + source_url in metadata; PLUS the ≤512px rank thumbnail stored
  as a paired blob (§6.6.7) → thumb_url for the grid
  → rewrite image_url = /api/chat/file/{file_id}
        │
        ├── emit packets (queries + start early; results post-cache, with thumb_url)
        │
        └── ToolResponse
              llm_facing_response: JSON results (same-origin URLs) + embedding instructions
              rich_response: ImageSearchDocsResponse
        │
        ▼
LLM answer embeds `![title](/api/chat/file/{id})` → react-markdown renders <img>
  → browser fetches same-origin, authed, immutable-cached
```

Four design decisions:
1. **Display happens via markdown in the final answer**, not via a custom packet renderer. Keeps v1 frontend work minimal (§7 grid renderer aside).
2. **Cache-before-respond**: winners are downloaded *before* the LLM sees them, so the LLM only ever receives URLs that are guaranteed to render. The alternative (rewrite after generation) requires parsing/patching the streamed answer — strictly worse.
3. **Rank-before-download (thumbnails-first)**: ranking runs on provider thumbnails, and full images are fetched only for ranked winners. §6.5's vision call downscales its inputs to ≤512px anyway, so ranking on ~thumbnail-resolution inputs changes nothing about rank quality — it only removes 8 full downloads (worst ~10s) from the critical path and stops the FileStore from accumulating discarded images.
4. **Wide funnel, local pre-filter**: rank quality is capped by the candidate pool. 30 candidates scored by SigLIP in milliseconds → top 8 to the vision LLM. Same vision cost as an 8-candidate pipeline, ~3× the effective pool.
5. **Speculate on the rank verdict**: winners are a subset of the 8 rank candidates, and SigLIP order correlates strongly with the verdict — so downloading all 8 while the rank runs wastes at most 2 downloads and removes the download leg from the critical path entirely. Worst-case wasted bytes (8 fetched, 6 kept) are still below what v6 downloaded *every* time.
6. **Two assets per winner**: full image for the answer/lightbox, the already-computed ≤512px rank JPEG (~30–60 KB) for the timeline grid. The grid painting 96px thumbs from 8 MB originals was the single largest frontend-bandwidth waste in the design; storing the thumbnail costs one small blob we already hold in memory.

---

## 4. Data models

`backend/onyx/tools/tool_implementations/image_search/models.py`

```python
from abc import abstractmethod
from collections.abc import Sequence
from pydantic import BaseModel, field_validator
from onyx.utils.url import normalize_url

CANDIDATE_POOL_SIZE = 30        # merged candidates across all queries (thumb budget)
MAX_IMAGES_PER_QUERY = 10       # per-query request size to the provider
RANK_CANDIDATES = 8             # survivors of SigLIP pre-filter → vision rank input
                                # = speculative-prefetch set (§6.6.2): all rank
                                # candidates download while the rank call runs
RANK_MAX_RESULTS = 6            # final winners after vision rank


class ImageSearchResult(BaseModel):
    title: str
    image_url: str        # provider's direct image URL (download source)
    cached_url: str | None = None   # /api/chat/file/{file_id} after caching — what gets embedded
    file_id: str | None = None      # FileStore id after caching
    thumbnail_url: str | None = None
    source_page_url: str | None = None  # page the image was found on (attribution)
    source_domain: str | None = None
    width: int | None = None
    height: int | None = None
    siglip_score: float | None = None   # set by §6.3 pre-filter
    phash: str | None = None            # hex pHash of the thumbnail, set by §6.4
    thumb_file_id: str | None = None    # FileStore id of the persisted rank thumb (§6.6.7)
    thumb_url: str | None = None        # /api/chat/file/{thumb_file_id} — grid asset

    @field_validator("image_url")
    @classmethod
    def normalize_image_url(cls, v: str) -> str:
        return normalize_url(v)


class ImageSearchProvider:
    @abstractmethod
    def search_images(self, query: str) -> Sequence[ImageSearchResult]: ...

    @abstractmethod
    def test_connection(self) -> dict[str, str]: ...
```

Thumbnail bytes are **runtime-only** — held in a `dict[str, bytes]` keyed by `image_url` inside `run()`, never on the Pydantic model and never persisted. Only winners' full images reach the FileStore.

Result filtering rules (applied in the tool, mirroring `filter_web_search_results_with_no_title_or_snippet`):
- Drop results with empty `image_url`.
- Drop non-http(s) schemes (`data:`, `javascript:` — injection/abuse guard).
- Drop obvious non-image extensions only if the provider is known to return mixed content (SearXNG occasionally does); otherwise trust provider.
- Dedupe on `image_url`.

---

## 5. Provider clients

### 5.1 SearXNG client (Puffin default)

Extend `clients/searxng_client.py`. `SearXNGClient` gains `search_images` and implements `ImageSearchProvider` alongside `WebSearchProvider`:

```python
@retry_builder(tries=3, delay=1, backoff=2)
def search_images(self, query: str) -> list[ImageSearchResult]:
    payload = {"q": query, "format": "json", "categories": "images"}
    response = requests.post(f"{self._searxng_base_url}/search", data=payload)
    response.raise_for_status()
    results = response.json().get("results", [])[:MAX_IMAGES_PER_QUERY]
    return [
        ImageSearchResult(
            title=r.get("title") or "",
            image_url=r.get("img_src") or "",
            thumbnail_url=r.get("thumbnail_src"),
            source_page_url=r.get("url"),
            source_domain=r.get("parsed_url", [None, None])[1]
                          if r.get("parsed_url") else None,
        )
        for r in results
        if r.get("img_src")
    ]
```

Notes:
- SearXNG image results use `img_src` / `thumbnail_src` keys (differs from web results' `url`/`content`).
- `thumbnail_src` is the input to the thumbnails-first pipeline (§6.2) — SearXNG proxies/serves small previews, so 30 thumbnails total ~100 KB and fetch in <1s.
- Requires `format: json` enabled in the SearXNG instance settings (`search.formats: [html, json]`) — same requirement as existing web search, so no new deployment constraint.
- Image engines (google images, bing images, duckduckgo images) must be enabled in the SearXNG config; document in Puffin deploy docs.
- Depending on the engine, results may include a `resolution` string (e.g. `"1600x1067"`); parse into `width`/`height` when present, leave `None` otherwise. Don't rely on it — winners get authoritative dimensions from decoded bytes at cache time (§6.6).

### 5.2 Provider resolution (SearXNG only)

No new DB column, no designation, no admin UI. The tool resolves the **active web search provider** (existing `db/web_search.py` lookup) and requires it to be SearXNG:

1. Active provider is SearXNG → use its `SearXNGClient` (which now implements `ImageSearchProvider`).
2. Anything else, or no provider configured → constructor raises (deployment bug: ERROR log + graceful LLM-facing failure, §10).

In Puffin's stock deployment SearXNG is the active web provider, so this works with zero config. The `search_images` method lives on the client behind the `ImageSearchProvider` interface (§4), so a future provider is an additive change, not a refactor.

`is_available()` remains unconditionally True — the tool is always enabled (§8.2); provider problems surface loudly at call time, never as a hidden tool.

---

## 6. The tool

`backend/onyx/tools/tool_implementations/image_search/image_search_tool.py`

Skeleton mirrors `WebSearchTool` exactly. Key deltas only:

```python
class ImageSearchTool(Tool[WebSearchToolOverrideKwargs]):
    NAME = "image_search"
    DISPLAY_NAME = "Image Search"
    DESCRIPTION = "Search the web for images to show the user."

    def __init__(self, tool_id: int, emitter: Emitter) -> None:
        # like WebSearchTool.__init__ (active-provider lookup), plus:
        provider_type = WebSearchProviderType(provider_model.provider_type)
        if provider_type != WebSearchProviderType.SEARXNG:
            logger.error(
                "ImageSearchTool misconfigured: active web search provider "
                "is '%s', expected SearXNG — check deployment config",
                provider_type.value,
            )
            raise RuntimeError("Image search requires SearXNG.")

    @override
    @classmethod
    def is_available(cls, db_session: Session) -> bool:
        return True  # always enabled; provider validated at call time (§5.2)

    def tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Search the internet for images. Use when the user asks to "
                    "see, show, or find pictures/photos/images of something. "
                    "Returns image URLs with titles and source pages. After "
                    "receiving results, display the best 1-4 images inline by "
                    "embedding them as markdown: ![title](image_url). Never "
                    "invent image URLs — only embed URLs returned by this tool."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "1-3 short, visual, descriptive queries "
                                "(e.g. 'atlantic puffin flying'). "
                                "Printable characters only."
                            ),
                        },
                    },
                    "required": ["queries"],
                },
            },
        }
```

### 6.1 `run()` behavior

Same shape as `WebSearchTool.run()`:
1. Validate `queries` present → `ToolCallException` with `llm_facing_message` otherwise (copy the existing message pattern).
2. `_normalize_queries_input` + `_sanitize_query` — **reuse, don't copy**: move both helpers from `web_search_tool.py` into `web_search/utils.py` (or `tools/tool_implementations/utils.py`) and import from both tools.
3. Emit `ImageSearchToolStart` + `ImageSearchToolQueriesDelta(queries=queries)` immediately (see §7).
4. Run queries in parallel via `run_functions_tuples_in_parallel` with per-query error capture (`_safe_execute_single_search` pattern).
5. Round-robin merge, dedupe on `image_url`, cap `CANDIDATE_POOL_SIZE` (30).
6. Partial failure → log + continue; total failure → `ToolCallException` with failed-query details; success-but-empty → `ToolCallException` telling the LLM not to retry (mirror existing messages).
7. **Dedupe lookup** (§6.6.5): partition candidates into already-cached (reuse `file_id`; rank thumbnail generated from stored FileStore bytes — zero network) and new.
8. **Thumbnail acquisition** (§6.2): parallel in-memory fetch of the new candidates' thumbnails; drop unfetchable candidates.
9. **SigLIP pre-filter** (§6.3): score all thumbnails vs the query; keep top `RANK_CANDIDATES`.
10. **pHash collapse** (§6.4): merge visual near-duplicates among the kept set; backfill to `RANK_CANDIDATES` from next-scored candidates.
11. **Vision re-rank + speculative prefetch, concurrently** (§6.5, §6.6.2): dispatch the rank call and, in the same instant, start hardened full downloads of all `RANK_CANDIDATES` in SigLIP order (dedupe hits excluded — already stored). Rank verdict: drop irrelevant, reorder; winners capped at `RANK_MAX_RESULTS`, ranked leftovers kept as **reserves**.
12. **Reconcile** (§6.6.2): keep winners' completed downloads, cancel in-flight fetches of rank-dropped candidates, promote reserves for failed winners (their downloads are already in flight). Stored files for dropped candidates are not attached to the message — the nightly sweep reclaims them.
13. **Thumb persistence** (§6.6.7): store each winner's ≤512px rank JPEG as a paired FileStore blob; set `thumb_url`.
14. Emit `ImageSearchToolResultsDelta` (cached winners only, with `thumb_url`) + `ImageSearchToolEnd`, build `llm_facing_response` (§6.7), return `ToolResponse`.

### 6.2 Thumbnail acquisition (in-memory, pre-rank)

`backend/onyx/tools/tool_implementations/image_search/thumbnails.py`

```python
THUMB_MAX_BYTES = 512 * 1024        # 512 KB per thumbnail
THUMB_TIMEOUT_SECONDS = 5

def fetch_thumbnail(result: ImageSearchResult) -> bytes | None:
    """Fetch result.thumbnail_url (or image_url iff thumbnail_url is absent)
    into memory. Returns raw bytes, or None (caller drops the candidate)."""
```

1. **Source**: `thumbnail_url` when present. When absent (some engines omit it), fall back to `image_url` **under the same 512 KB cap** — a big original simply fails the cap and the candidate is dropped; the pool is wide (30), so attrition is cheap. Never fetch full-size originals at this stage.
2. **SearXNG image_proxy is the common case, and it collides with the SSRF guard — handle it explicitly.** SearXNG frequently serves `thumbnail_src` through its own proxy (`/image_proxy?url=...`), i.e. a URL pointing at the SearXNG instance itself — which in Puffin's stock deployment is a Docker service on an RFC1918 address over plain HTTP, and depending on the engine may even be a *relative* path. A naive application of §6.6.1's guard therefore blocks **every** thumbnail in the default config and silently degrades the pipeline to the size-capped `image_url` fallback. Rules:
   - Resolve relative `thumbnail_src` values against the configured SearXNG base URL before validation.
   - Exempt exactly one origin from the private-IP/scheme rejection: the **configured SearXNG base** (`scheme + host + port` exact match against deployment config — no suffix/subdomain matching, no general allowlist). The exemption applies to the thumbnail fetcher only, never to full-image downloads, and redirects **leaving** the SearXNG origin re-enter full validation on the next hop.
   - Rationale: fetching from the deployment's own SearXNG is not SSRF — it's the same trust boundary as the search query itself.
3. **Hardening** (all non-exempt URLs): same SSRF core as the full downloader (§6.6.1) — scheme check, per-hop private-IP rejection, DNS pinning, redirect cap. Extract the validation/pinning logic into a shared helper in `download.py`; the thumbnail fetcher differs only in caps, the SearXNG-origin exemption, and writing to memory instead of the FileStore.
4. **Validation**: content-type prefix `image/` or magic-byte sniff (shared helper, §6.6.1). Reject SVG.
5. **Parallelism**: all fetches via `run_functions_tuples_in_parallel`; 30 thumbs ≈ 100 KB total, wall time <1s typical, worst ≈ one 5s timeout.
6. Failure → drop the candidate silently (debug log). If **all** thumbnails fail → `ToolCallException` ("Found images but none could be retrieved; tell the user and do not retry").
7. Dedupe-hit candidates (§6.6.5) never enter this step — their rank thumbnail is produced by downscaling stored FileStore bytes.

### 6.3 SigLIP pre-filter (served sidecar)

Rank quality is capped by the candidate pool, and 8 is small. SigLIP widens the funnel to 30 for free. It runs as a **dedicated served endpoint** — mirroring how everything else GPU-shaped in this architecture is served (vLLM, SearXNG): one shared model instance, one shared cache, zero footprint in the API process. This dissolves the entire class of in-process problems v10 had to legislate around: no per-worker × 400 MB residency, no warm-up hooks in someone else's process lifecycle, no "which process executes tools" gate, no per-worker cache dilution.

#### 6.3.1 The `puffin-siglip` service — Infinity, not custom code

The sidecar is **[Infinity](https://github.com/michaelfeil/infinity)** (`michaelfeil/infinity`), a purpose-built REST/gRPC embedding server for text *and vision* embedding models, CLIP/SigLIP included — torch-backed with FlashAttention where available, fast tokenization, and **dynamic batching** out of the box. v11 spec'd a custom FastAPI app; Infinity replaces it entirely. **We write zero serving code**: no `siglip_server/` package, no `/score` endpoint, no hand-rolled batching, no server test suite to maintain — the sidecar becomes pure deployment config, and concurrent tool calls get coalesced into batched forward passes for free.

- **Run**: official Infinity Docker image, command `infinity_emb v2 --model-id google/siglip-base-patch16-224 --port 9100`. Weights baked at image build (a build stage runs the model download into `HF_HOME` inside the layer) — the no-runtime-HuggingFace-egress constraint holds.
- **API**: Infinity's OpenAI-compatible `POST /embeddings` — one call with the sanitized query as text input, one with the thumbnail batch as base64 image input (Infinity's vision modality). Returns embedding vectors; **cosine scoring moves into the client** (§6.3.2) — a numpy dot product over ≤31 vectors, trivially cheap. `GET /health` is the compose healthcheck; the service reports ready only after the model is loaded.
- **Device**: Infinity auto-selects (CUDA → fp16, else CPU fp32 — ~0.5–1s for 30 thumbs on CPU, inside the vision-rank window either way); `--device cpu` forces CPU if GPU isolation from vLLM is preferred. Resolved device appears in Infinity's startup logs — that log line is the deploy guide's verification step.
- **Caching trade, stated honestly**: v11's server-side shared cache (and its `misses` protocol) is dropped — Infinity doesn't do key-addressed embedding caching, and building a wrapper around it to add one would resurrect exactly the custom code this switch deletes. What replaces it: (a) Infinity's throughput makes the uncached path cheap enough that the cache was optimizing a non-problem — 30 thumbs embed in tens of ms on GPU; (b) a small **per-worker client-side LRU** of image embeddings keyed by normalized `image_url` (`SIGLIP_IMAGE_CACHE_SIZE = 2048`) still catches repeat queries within a worker, ~15 lines in the client; (c) the source-URL dedupe (§6.6.5) already removes repeat *downloads*, which was the expensive part. Deployment-wide cache sharing moves to §13 (external store), now genuinely optional rather than load-bearing.
- **Trust boundary**: internal Docker network only, never published on a host port — same unauthenticated-internal model as SearXNG. Document that exposing it is a deployment error. REST in v1; Infinity's gRPC exists if the hop ever matters (it won't at 31 inputs).

#### 6.3.2 The client

`backend/onyx/tools/tool_implementations/image_search/siglip_client.py`

1. **Embed, then score locally**: `score(query, thumbs) -> dict[key, float]` — up to two `POST /embeddings` calls to `SIGLIP_SERVER_URL` (env, default `http://puffin-siglip:9100`): the query text, and the image batch (base64 data-URIs of the ≤512px thumbs; LRU-cached embeddings skipped from the payload). L2-normalize, cosine via numpy, set `siglip_score` on each result. `SIGLIP_REQUEST_TIMEOUT_SECONDS = 5`, one retry on connection error. Payload ~100–300 KB total, sub-ms on the compose network; Infinity's dynamic batching absorbs concurrent tool calls server-side.
2. **Select**: sort descending by score, keep top `RANK_CANDIDATES` (8). No absolute score threshold in v1 — the vision rank (§6.5) is the relevance gate; SigLIP only orders the funnel.
3. **Fallback**: sidecar unreachable, non-200, timeout, or malformed response → log ERROR (`"SigLIP sidecar unavailable at <url>"`), keep the first `RANK_CANDIDATES` in provider order. The pre-filter must never make the tool less reliable than not having it — identical semantics to every other fallback in this spec, and the tool never blocks on the sidecar beyond the 5s timeout (a sidecar still booting is just an unreachable sidecar).

### 6.4 pHash near-duplicate collapse

`backend/onyx/tools/tool_implementations/image_search/phash.py`

Engines return the same image resized/re-hosted 3–4 times; exact-URL dedupe (§6.6.5) can't see that. One hashing pass fixes it:

```python
PHASH_HAMMING_THRESHOLD = 8

def collapse_near_duplicates(
    kept: list[ImageSearchResult],
    pool: list[ImageSearchResult],      # remaining SigLIP-ordered candidates
    thumbs: dict[str, bytes],
) -> list[ImageSearchResult]:
    """pHash each kept thumbnail (imagehash.phash). Two results with
    Hamming distance <= PHASH_HAMMING_THRESHOLD are near-duplicates:
    keep the higher siglip_score, drop the other. Backfill from `pool`
    (hashing + comparing each backfill candidate too) until len == 8
    or the pool is exhausted. Stores each hash on result.phash."""
```

1. **Library**: `imagehash` (+ Pillow, already a dependency). Hashing 8–30 thumbnails is sub-millisecond-per-image.
2. **Order of operations**: runs *after* SigLIP selection so backfill candidates are the next-best-scored, and *before* the vision rank so all 8 rank slots hold visually distinct images.
3. **Tie-break**: among near-duplicates prefer higher `siglip_score`; on equal score prefer larger provider-reported dimensions, then earlier provider order.
4. **Persistence**: winners' `phash` values are written into `file_metadata` at cache time (§6.6.1) — enables future cross-conversation *visual* dedupe (exact-hash match is a cheap indexed win; Hamming-distance search across the DB stays future work, §13).
5. Unhashable thumbnail (decode failure) → treat as unique (no collapse), debug log.

### 6.5 Vision re-ranking (always on, on thumbnails)

The Puffin deployment serves a **multimodal** model on the GB10 via vLLM — the same model that writes the chat answer. Ranking is therefore not an optional enhancement: **every image-search call ends with a vision ranking pass** before results go back to the answer flow.

Why: search engines rank images by surrounding page text, not visual content. Without looking, "SKALE logo" returns conference crowd shots and unrelated banners ranked above the actual logo. One local vision call eliminates that failure class. SigLIP (§6.3) narrows the funnel by *similarity*; the vision LLM is the *judgment* pass — it reads the user's actual request, catches text-in-image, style, and compositional mismatches SigLIP can't.

#### 6.5.1 Mechanics

`backend/onyx/tools/tool_implementations/image_search/ranking.py`

```python
RANK_IMAGE_MAX_DIM = 512        # px, longest side
RANK_IMAGE_JPEG_QUALITY = 80
RANK_TIMEOUT_SECONDS = 20
# RANK_MAX_RESULTS = 6 (models.py) — final winner cap

def rank_images(
    query: str, candidates: list[ImageSearchResult], thumbs: dict[str, bytes]
) -> tuple[list[ImageSearchResult], list[ImageSearchResult]]:
    """Return (winners, reserves): candidates filtered + reordered by visual
    relevance to query. Falls back to SigLIP order (then provider order) on
    any failure (logged at ERROR)."""
```

1. **LLM**: `get_default_llm_with_vision()` (`llm/factory.py:234`). In Puffin this resolves to the served vLLM multimodal model. If it returns `None`, that is a deployment bug (same philosophy as §5.2): log at ERROR (`"Vision ranking unavailable: no image-capable LLM configured"`) and fall back to SigLIP order — never skip silently, never fail the tool.
2. **Input images**: the in-memory thumbnails from §6.2 (dedupe hits: downscaled stored bytes). Normalize with Pillow to ≤`RANK_IMAGE_MAX_DIM` longest side, re-encode JPEG q80, base64 data-URL. Provider thumbnails are already ~thumbnail resolution, so this is what the model would have seen under the v6 download-first pipeline anyway — **rank quality is unchanged by the inversion**; only the network cost moved.
3. **One call, all candidates**: single user message = the ranking instruction + the sanitized search queries + numbered `ImageContentPart`s (`detail: "low"`), OpenAI-style content parts from `llm/models.py` (verified to pass through LiteLLM → vLLM). **Honest input contract**: the tool receives only `queries` — the answer model's reformulation — not the user's message; the rank judges relevance to the queries, and the prompt says so. (If Onyx's tool kwargs later expose the user turn, plumbing it through is a one-line improvement — noted in §13.)
4. **Prompt contract** (strict JSON, no prose):

```
You are ranking candidate images for relevance to an image search.
Search queries: "<sanitized queries, joined>"
Images are numbered 1..N in order.
Respond with ONLY this JSON: {"ranked": [<image numbers, most relevant first,
omitting any image that is irrelevant, low-quality, or a duplicate>]}
```

5. **Apply**: keep returned indices in returned order; first `RANK_MAX_RESULTS` are **winners**, the remainder are **reserves** (used by §6.6.2 to replace winners whose full download fails — their downloads are already in flight via the speculative prefetch). The rank call is dispatched *after* the prefetch tasks are started (§6.6.2) so the two overlap fully. Validation: parse JSON (strip code fences), drop out-of-range/duplicate indices. Empty `ranked` is a *valid* verdict — treat as "nothing relevant found" and surface the §6.1 empty-results `ToolCallException` path.
6. **Failure = fallback, loudly**: timeout, malformed JSON after one re-ask, or LLM error → log ERROR with the raw output, return SigLIP order (winners = top 6, reserves = rest); if SigLIP also failed, provider order. Ranking must never make the tool less reliable than not ranking.

#### 6.5.2 Cost

One extra local vision call per image search: ~1–3s on the GB10 at 8×512px inputs, zero marginal cost. With the speculative prefetch (§6.6.2) the winner downloads run *inside* this window, so the rank call's latency is largely absorbed: the post-SigLIP path costs `max(rank, downloads)` ≈ the rank call alone in the typical case.

### 6.6 Speculative winner caching (full images, hardened downloader)

Full-image downloads run **concurrently with the vision rank**, covering all rank candidates; the verdict then selects which stored files become winners. Reuses the `ImageGenerationTool` persistence path (`FileStore` + `/api/chat/file/{file_id}`), with a hardened downloader replacing the naive `save_file_from_url`.

#### 6.6.1 Hardened downloader

`backend/onyx/tools/tool_implementations/image_search/download.py`

The existing `save_file_from_url()` (`file_store/utils.py:242`) is a bare `requests.get` — no scheme check, no SSRF guard, no size cap, no content-type validation, and it hardcodes `file_type="image/png;base64"`. Do **not** reuse it. New function:

```python
MAX_IMAGE_BYTES = 8 * 1024 * 1024      # 8 MB
DOWNLOAD_TIMEOUT_SECONDS = 10
ALLOWED_CONTENT_TYPE_PREFIX = "image/"
MAX_REDIRECTS = 3

def download_and_store_image(result: ImageSearchResult) -> ImageSearchResult | None:
    """Download result.image_url (full size) into the FileStore. Returns the
    result with file_id + cached_url + width/height set, or None if the image
    is unfetchable/invalid (caller promotes a reserve)."""
```

Requirements:
1. **Scheme**: `https` only (`http` allowed via `IMAGE_SEARCH_ALLOW_HTTP=true` env for LAN test setups).
2. **SSRF guard with DNS pinning**: resolve hostname; reject loopback, RFC1918 private, link-local (incl. `169.254.169.254` metadata), and ULA/unique-local IPv6 ranges — `ipaddress` stdlib against all resolved A/AAAA records. Then **connect to the validated IP directly** so the guard and the connection use the same resolution (no resolve/connect TOCTOU): mount a custom `HTTPAdapter` whose connection pool targets the pinned IP while urllib3's `server_hostname`/`assert_hostname` keep SNI and certificate validation on the original hostname, and send the original `Host` header. Follow redirects manually (cap `MAX_REDIRECTS`), re-running resolve-validate-pin on **every** hop — redirect-to-internal is the classic bypass. **Shared**: the resolve/validate/pin core is exported and reused by the thumbnail fetcher (§6.2) with its own caps and the SearXNG-origin exemption (§6.2 point 2) — the exemption is a parameter of the shared helper, and full-image downloads always call it with the exemption disabled.
3. **Streaming download** with `stream=True`; abort once `MAX_IMAGE_BYTES` is exceeded (check both `Content-Length` upfront and actual bytes read — servers lie). The chunk loop also checks a per-task `threading.Event` **cancel flag** between chunks — this is how the reconcile step (§6.6.2) aborts in-flight fetches of rank-dropped candidates; HTTP requests can't be forcibly killed mid-stream, but a chunk-boundary check bounds waste to one chunk.
4. **Content-type validation**: response `Content-Type` must start with `image/`; additionally sniff magic bytes (`imghdr`-style check for jpeg/png/gif/webp/avif) since some hosts serve images with `application/octet-stream`. Magic bytes win over header. Reject SVG (`image/svg+xml`) — scriptable content, and the file endpoint serves with the stored MIME type; not worth the sanitization effort in v1.
5. **Store**: `file_store.save_file(content=BytesIO(data), display_name=result.title or "SearchImage", file_origin=FileOrigin.CHAT_IMAGE_SEARCH, file_type=<sniffed mime>, file_metadata={"source_url": result.image_url, "source_page": result.source_page_url, "phash": result.phash})`. The metadata preserves provenance for attribution and dedupe (source-URL now, visual-hash later). Decode once with Pillow to populate authoritative `width`/`height` on the result (feeds §6.7 `dimensions`).
6. On any failure: log at debug, return `None`. **Unfetchable by the server ≈ unembeddable by the browser** — dropping and promoting a reserve is the feature, not a compromise.

#### 6.6.2 Orchestration in `run()` — speculative prefetch + reconcile

```python
# 1) SPECULATE: before dispatching the rank call, start downloads for ALL
#    rank candidates (SigLIP order); dedupe hits already carry file_id — skip
prefetch = {
    r.image_url: submit(download_and_store_image, r, cancel_flags[r.image_url])
    for r in rank_candidates if r.file_id is None
}
winners, reserves = rank_images(query, rank_candidates, thumbs)  # runs concurrently

# 2) RECONCILE the verdict against the prefetch:
#    - winners: await their futures (usually already done — verdict arrives last)
#    - rank-dropped candidates: set cancel_flags → chunk-boundary abort
#    - failed winner → promote next reserve; its download is already in flight,
#      so promotion costs await-time, not a fresh fetch
#    - completed downloads for dropped candidates: NOT attached to the message
#      → unreferenced → reclaimed by the nightly sweep (§6.6.6); until then they
#      serve as dedupe hits for repeat queries — harmless either way
for r in final_winners:
    r.cached_url = build_frontend_file_url(r.file_id)  # "/api/chat/file/{id}"
```

- The prefetch tasks start **before** the rank call is dispatched, then the rank runs — post-SigLIP wall time is `max(rank, slowest winner download)`, not the sum. Typical: the ~1–3s rank window fully covers the 1–3s downloads; the verdict finds its winners already stored.
- Bounded waste: at most `RANK_CANDIDATES − RANK_MAX_RESULTS` = 2 downloads per call are discarded (fewer when the verdict drops candidates early enough for cancellation to bite). Still below v6's every-call download volume.
- If **all** winners (and reserves) fail to download → `ToolCallException` ("Found images but none could be retrieved; tell the user and do not retry").
- If ≥1 succeeds → proceed with the cached subset.
- The LLM response (§6.7) contains **only** `cached_url`s. Original URLs never reach the LLM, which structurally prevents it from embedding a hotlink.

#### 6.6.3 `FileOrigin` and access control (2 one-line changes)

- Add `CHAT_IMAGE_SEARCH` to the `FileOrigin` enum (`onyx/configs/constants.py`, wherever `CHAT_IMAGE_GEN` lives).
- `backend/onyx/access/access.py` (`user_can_access_chat_file`): extend the existing `CHAT_IMAGE_GEN` origin allow-branch to also accept `CHAT_IMAGE_SEARCH`. Without this, the file endpoint 404s for everyone but breaks silently — this is the easiest change to miss. Semantics match image gen exactly: any authenticated user with basic access can fetch by id (ids are UUIDs, unguessable; same trust model Onyx already accepted for generated images).

#### 6.6.4 Storage backend

Postgres FileStore is the default backend — cached images land in the DB. Fine at Puffin's scale — winners-only storage (≤6 per call instead of 8 candidates) plus dedupe (§6.6.5) and GC (§6.6.6) keep growth bounded; if it ever matters, S3/MinIO is a config switch, no code.

#### 6.6.5 Cross-conversation dedupe

Same source URL → stored once, ever.

1. **Index**: Alembic migration adding a functional index on the file table: `CREATE INDEX ix_file_record_source_url ON file_record ((file_metadata->>'source_url')) WHERE file_metadata->>'source_url' IS NOT NULL` (the `file_metadata` column is JSONB — verified, `db/models.py:4819`).
2. **Lookup**: `partition_by_existing_source_url(results)` — one query fetching existing `(source_url, file_id)` pairs for the batch, restricted to `file_origin = CHAT_IMAGE_SEARCH`. Runs **first**, on the full candidate pool (§6.1 step 7): hits get `file_id`/`cached_url` set and skip *all* network work — their rank thumbnails come from stored bytes; misses continue through the thumbnail pipeline.
3. **Effect**: repeat and follow-up queries ("show me more puffins") collapse to search → lookup → siglip → rank — near-instant, zero downloads. Sharing files across users is consistent with the existing access model: any authed user can read `CHAT_IMAGE_SEARCH` files by id (§6.6.3), same trust boundary as before.
4. **Staleness**: a source URL's content can change over time; v1 accepts serving the first-cached version (images embedded in old chats *should* stay stable anyway). No TTL.

#### 6.6.6 Garbage collection

Chat-session deletion already deletes files listed in `ChatMessage.files` (`db/chat.py:211`, `delete_messages_and_files_from_chat_session`). Dedupe breaks that model: a shared file referenced by two chats must survive the deletion of one. Design:

1. **Attach file descriptors**: the tool attaches all returned winners' `file_id`s **and `thumb_file_id`s** (§6.6.7) to the assistant message's `files` (as `ImageGenerationTool` does) — this records references for both assets. Speculatively stored files for rank-dropped candidates are deliberately *not* attached.
2. **Exclude shared-origin files from inline deletion**: one origin check in `delete_messages_and_files_from_chat_session` — skip `file_origin = CHAT_IMAGE_SEARCH` (mirrors the existing `user_file_id` skip at `db/chat.py:228`). Message rows still delete; only the blob is spared.
3. **Orphan sweep with a grace window**: nightly Celery beat task (register alongside Onyx's existing periodic tasks): delete `CHAT_IMAGE_SEARCH` file records whose `file_id` appears in **no** `ChatMessage.files` entry **and whose `created_at` is older than `IMAGE_SWEEP_GRACE_HOURS = 24`**. The grace window is load-bearing, not hygiene: v9's speculation made "unreferenced" a *normal transient state* — a winner's file exists from tool-return until the assistant message (with its file descriptors) commits, a window that spans the entire answer generation (tens of seconds to minutes on the GB10). Without the grace window, a sweep firing in that gap deletes a winner's blob and the streaming answer embeds a URL that 404s — violating the "no broken images by construction" invariant. 24h also covers the crash case (tool returned, message never persisted): such files are reclaimed on the *next* nightly run instead of leaking forever. Refcounting without a refcount column — the message table *is* the reference registry. Batch in chunks of 500; log deleted count.
4. **Invariant**: a cached image lives exactly as long as at least one chat message references it. This covers full/thumb pairs (both ids are message-referenced together) and reclaims speculative leftovers (never referenced → swept on the next nightly run).

#### 6.6.7 Rank-thumbnail persistence (grid asset)

The §7.2 grid paints ~96px thumbs; serving them from `cached_url` means fetching up to 8 MB per image — worst case ~48 MB to render a strip, brutal on mobile chat reload. The perfect asset already exists in memory: the ≤512px JPEG q80 produced for the rank call (§6.5.1).

1. **Store**: for each final winner, save the rank JPEG as a second FileStore blob — `file_origin=FileOrigin.CHAT_IMAGE_SEARCH`, `file_type="image/jpeg"`, `display_name=<title> + " (thumb)"`, `file_metadata={"full_file_id": <winner file_id>, "kind": "thumb"}`. ~30–60 KB each; the full image's `file_metadata` gains `"thumb_file_id"` so dedupe hits (§6.6.5) recover the pair in the same lookup.
2. **Serve**: `thumb_url = build_frontend_file_url(thumb_file_id)` — same authed endpoint, same immutable caching, ~100× less transfer for the timeline.
3. **Fallback**: a dedupe-hit full image stored before v9 has no thumb — generate and store one lazily at hit time from the stored bytes (they're local; downscale is ms-scale). If that fails, the packet falls back to `cached_url` — degraded bandwidth, never a broken grid.
4. **Answer unchanged**: the LLM still receives and embeds only full-image `cached_url`s (§6.7) — thumbs are a timeline/packet concern exclusively.
5. **Exclusion**: thumb records carry no `source_url` in metadata, so they are invisible to the dedupe lookup by construction.

### 6.7 LLM-facing response format

This is the part that makes images actually display. Results arrive **already ranked best-first and pre-filtered for relevance** (§6.3–§6.5); JSON payload plus an inline instruction footer — note all URLs are same-origin cached URLs:

```json
{
  "results": [
    {
      "index": 1,
      "title": "Atlantic puffin in flight",
      "image_url": "/api/chat/file/3f9c2e10-8a4b-4e2a-9c1d-7b6e5f4a3d21",
      "source_page": "https://en.wikipedia.org/wiki/Atlantic_puffin",
      "source_domain": "en.wikipedia.org",
      "dimensions": "1600x1067"
    }
  ],
  "instructions": "Results are ranked most-relevant first. Embed 1-4 images inline, preferring the earliest, using exactly: ![<title>](<image_url>). Below each image, add a one-line source attribution linking to source_page. Do not fabricate or modify URLs."
}
```

Rules encoded in the instruction string:
- **1–4 images max** — prevents wall-of-images answers.
- **Exact URL copy** — hallucinated/modified URLs are the top failure mode.
- **Attribution line** — link the source page; polite and useful when hotlinks die.
- Prefer larger images (`dimensions`, authoritative — decoded from cached bytes, §6.6.1) when equally relevant.

`rich_response`: `ImageSearchDocsResponse(BaseModel)` carrying the ranked results list — the same data the §7 grid renderer consumes via packets, kept on the response for persistence/replay. Do **not** reuse `SearchDocsResponse` — image results aren't citeable documents, and `image_search` must **not** be added to `CITEABLE_TOOLS_NAMES`.

---

## 7. Streaming packets & thumbnail-grid renderer (timeline UX)

Dedicated packets plus a grid renderer — the timeline shows *all* ranked winners as thumbnails while the answer embeds the chosen few.

### 7.1 Backend packets

`backend/onyx/server/query_and_chat/streaming_models.py`, following the existing `BaseObj` + `Literal` type-discriminator pattern (add matching `StreamingType` enum values):

```python
class ImageSearchToolStart(BaseObj):
    type: Literal["image_search_tool_start"] = ...

class ImageSearchToolQueriesDelta(BaseObj):
    type: Literal["image_search_tool_queries_delta"] = ...
    queries: list[str]

class ImageSearchToolResultsDelta(BaseObj):
    type: Literal["image_search_tool_results_delta"] = ...
    results: list[ImageSearchPacketResult]   # cached_url, thumb_url, title, source_page_url, source_domain

class ImageSearchToolEnd(BaseObj):
    type: Literal["image_search_tool_end"] = ...
```

Emission order in `run()`: start → queries delta (immediately, so the timeline shows activity during the thumbnail/rank pipeline) → results delta (post-cache, so the grid shows only stored winners, best-first) → end. `results` carries **cached** same-origin URLs only.

### 7.2 Frontend grid renderer

New renderer in `web/src/app/app/message/messageComponents/timeline/renderers/` (alongside the existing search renderer), registered for the four packet types:

- Collapsed row while running: spinner + "Image Search: <queries>".
- On results: horizontal thumbnail strip (up to `RANK_MAX_RESULTS`), each thumb ~96px, lazy-loaded from **`thumb_url`** (falling back to `cached_url` when absent, §6.6.7), click → opens source page in new tab, hover → title + domain. The strip costs ~200–400 KB total instead of tens of MB.
- Degradation: unknown-packet fallback must not break older clients during rolling deploys — ship the backend packet emission and the frontend renderer in the same release, and verify the frontend's unknown-type handling ignores (not crashes on) unrecognized packets.

### 7.3 Relationship to the answer

The grid shows the ranked winner set (transparency: "what did the tool find"); the answer's markdown embeds the model's final selection (§6.7). Both consume the same cached URLs, so there is no second fetch and no divergence between timeline and answer.

---

## 8. Registration & seeding

### 8.1 Code registry

`backend/onyx/tools/built_in_tools.py`:
- Add `ImageSearchTool` to `BUILT_IN_TOOL_TYPES` union.
- Add `ImageSearchTool.__name__: ImageSearchTool` to `BUILT_IN_TOOL_MAP`.
- Do **not** add to `STOPPING_TOOLS_NAMES` (answer generation must continue after the tool so the LLM can embed) or `CITEABLE_TOOLS_NAMES`.

### 8.2 DB seed migration

New Alembic migration modeled on `d3fd499c829c_add_file_reader_tool.py` — the better precedent, because it both inserts the tool row **and attaches it to assistants**, which is what makes "always enabled" real:

```python
{
    "name": "ImageSearchTool",
    "display_name": "Image Search",
    "description": (
        "The Image Search Action allows the assistant to search the internet "
        "for images and display them inline in its answers."
    ),
    "in_code_tool_id": "ImageSearchTool",
    "enabled": True,
}
```

Then, in the same migration (patterned on the file-reader migration's `persona__tool` insert with `ON CONFLICT DO NOTHING`):

```sql
-- Attach to every existing assistant, not just the default (id=0)
INSERT INTO persona__tool (persona_id, tool_id)
SELECT p.id, :tool_id FROM persona p
ON CONFLICT DO NOTHING;
```

**New assistants**: the migration covers existing rows only. To make the tool default-on for assistants created later, pre-select `ImageSearchTool` in the assistant editor's default tool set (`web/src/views/AgentEditorPage.tsx`) — a small fork edit, counted in §8.3. Admins can still untick it per assistant in the editor; "always enabled" means on-by-default everywhere, not admin-proof.

Fork-maintenance note: keep this migration's revision ID at the tip of Puffin's own migration chain and rebase it when syncing upstream — upstream will keep appending migrations and Alembic needs a linear (or explicitly merged) history.

### 8.3 Frontend registration (3 small edits)

- `web/src/app/app/services/actionUtils.ts`: add `isImageSearchTool` check (by `in_code_tool_id === "ImageSearchTool"`) → return `SvgImage` (or a dedicated icon) in `getIconForAction`. Without this it silently falls back to `SvgCpu` — functional, but off-brand.
- Assistant editor tool list picks the tool up automatically from the tools API.
- `web/src/views/AgentEditorPage.tsx`: include `ImageSearchTool` in the default-selected tools for newly created assistants (§8.2).

---

## 9. Assistant prompting (deployment config, not code)

Add to the Puffin default assistant system prompt:

> When the user asks to see, show, or find images of something, use the `image_search` tool and embed the best results as markdown images per the tool's instructions. Do not describe images you have not embedded.

Belt-and-suspenders with the tool description; small local models (your GB10 vLLM stack) need the reinforcement more than frontier models do.

---

## 10. Failure modes & mitigations

| Failure | Behavior |
|---|---|
| Active web search provider is not SearXNG | **Deployment bug, not a supported state** — the tool stays enabled (it is never hidden); the constructor raises at call time, logging at ERROR: `"ImageSearchTool misconfigured: active web search provider is '<type>', expected SearXNG — check deployment config"`. The LLM receives a graceful failure and tells the user image search is unavailable. Loud in logs, graceful to users, impossible to miss. |
| SearXNG JSON format disabled / image engines off | Provider error → `ToolCallException` with actionable message; document config in deploy guide. |
| All queries fail | `ToolCallException`; LLM tells user image search failed. |
| Zero results | Explicit "do not search again" LLM message (mirror web search). |
| Thumbnail unfetchable / oversized / non-image | Candidate dropped pre-rank (§6.2). Pool of 30 absorbs attrition; no user-visible effect. |
| All thumbnails fail | `ToolCallException`; LLM tells the user images were found but couldn't be retrieved. |
| `thumbnail_src` absent | Fallback fetch of `image_url` under the 512 KB thumb cap; oversized originals drop the candidate (§6.2). |
| SigLIP load/inference failure | ERROR log; pre-filter falls back to provider order (first 8). Tool still works (§6.3). |
| pHash decode failure on a thumbnail | Treated as unique — no collapse for that image, debug log (§6.4). |
| Hotlink blocked (403/CORP), dead URL, oversized, non-image content **on a winner** | Caught at cache time (§6.6.1) — winner dropped, next **reserve** promoted (already thumb-vetted and vision-ranked). No broken images in chat by construction. |
| All winner + reserve downloads fail | `ToolCallException`; LLM tells the user images were found but couldn't be retrieved. |
| Malicious redirect to internal network (SSRF) | Hardened fetch path shared by thumbnail and full downloads: scheme check, per-hop private-IP rejection incl. cloud metadata, redirect cap, **DNS-pinned connections** (guarded IP = connected IP, no TOCTOU) (§6.2, §6.6.1). |
| No vision-capable LLM configured | Deployment bug (served model is multimodal by design). ERROR log + fallback to SigLIP order; tool still works. |
| Rank call fails (timeout / malformed JSON after one re-ask) | ERROR log with raw output; fallback to SigLIP order, then provider order. Ranking never reduces reliability. |
| Rank verdict: nothing relevant | Valid outcome — empty-results path, LLM told not to retry. |
| Slow image host stalls the tool | 5s per-thumbnail / 10s per-winner timeouts; all fetches parallel and overlapped with the rank call, so worst case ≈ one timeout, not N. |
| Speculative download of a rank-dropped candidate completes | Bounded waste (≤2 per call); file not attached to the message → nightly sweep reclaims; meanwhile it can serve dedupe hits. Cancellation aborts in-flight fetches at the next chunk boundary. |
| SigLIP sidecar down / unreachable / still booting | Client timeout (5s) or connection error → ERROR log + provider-order fallback (§6.3.2). Tool never blocks on the sidecar; compose healthcheck (Infinity `/health`) + restart policy handle recovery. |
| SigLIP sidecar slow under load | Bounded by the 5s client timeout → fallback for that call; sidecar remains up for the next. |
| Legacy full image (pre-v9) has no stored thumb | Lazy thumb generation from stored bytes at dedupe-hit time; on failure the packet falls back to `cached_url` (§6.6.7). Never a broken grid. |
| Thumb blob stored but full-image store fails (partial pair) | Thumbs are stored only after the winner's full image is confirmed stored (§6.1 step 13 ordering); a thumb orphaned by a later message-write failure is unreferenced → swept. |
| LLM hallucinates or edits URLs | LLM only ever receives `/api/chat/file/{uuid}` URLs; a fabricated/edited UUID 404s at the authed file endpoint. Hotlink embedding is structurally impossible (original URLs withheld). |
| Deleting one chat breaks a deduped image in another chat | Prevented by design: `CHAT_IMAGE_SEARCH` files are excluded from inline message-deletion cleanup; only the orphan sweep deletes, and only at zero references (§6.6.6). |
| Sweep races an in-flight turn (file stored, message not yet committed) | Eliminated by the 24h `created_at` grace window (§6.6.6): a file is only sweepable long after any turn that could reference it has committed. Covers winners awaiting message persist, concurrent dedupe reuse, and speculative leftovers alike. |
| Crash between tool return and message persist | Winner files left unreferenced with no message; reclaimed by the sweep after the grace window expires. Bounded leak of ≤ one call's files for ≤ ~48h. |
| Stale cached image (source URL content changed) | Accepted: first-cached version served forever; embedded history should be stable anyway (§6.6.5). |
| DB bloat (Postgres FileStore default) | Winners-only storage: ≤48 MB worst case per tool call (6 × 8 MB), typically ~3 MB. Dedupe + GC bound growth; S3/MinIO backend is a config switch if needed. No eviction in v1 (§6.6.4). |
| SigLIP memory footprint | One sidecar instance total: ~400 MB (GPU fp16) / ~800 MB (CPU fp32), independent of `WEB_CONCURRENCY` (§6.3.1). Non-issue anywhere. |
| Sidecar host has no GPU | Supported, not a failure: Infinity's CPU path (~0.5–1s for 30 thumbs), identical semantics; resolved device stated in Infinity's startup logs (§6.3.1). |
| Infinity API/schema drift on image upgrade | Pin the Infinity image tag in compose; the client integration test (§11) runs against the pinned image, so an upgrade that breaks `/embeddings` vision input fails CI, not production. |
| Sidecar accidentally exposed on a host port | Deployment error by definition — internal network only, documented (§6.3.1). No auth in v1; same trust model as SearXNG. |
| Unsafe imagery | Inherit SearXNG's `safesearch` instance setting — set `safesearch: 1` in Puffin's default SearXNG config. No Puffin-side filtering in v1. |

---

## 11. Testing plan

**Unit** (`backend/tests/unit/onyx/tools/tool_implementations/`):
- SearXNG image response parsing → `ImageSearchResult` (fixture JSON, incl. `resolution` present/absent, missing `img_src`).
- Filtering: empty `image_url`, `data:` scheme, dedupe, pool cap (30).
- Round-robin merge across 2 queries.
- `is_available()` returns True unconditionally; constructor raises + ERROR-logs on non-SearXNG provider.
- `run()` error paths: missing param, all-fail, empty results, all-thumbnails-fail, all-winner-downloads-fail (mock provider).
- **Thumbnail fetcher**: 512 KB cap abort, timeout, `thumbnail_src`-absent fallback to `image_url` under thumb cap, SSRF rejection via shared core, in-memory-only (no FileStore writes).
- **SearXNG-origin exemption**: exact scheme+host+port match only (reject `evil-searxng.example`, suffix tricks, other ports); relative `thumbnail_src` resolved against configured base; redirect leaving the SearXNG origin re-enters full validation; exemption never active in the full-image downloader.
- **SigLIP**: scoring orders by cosine similarity (mock model), top-8 selection, fallback to provider order on load failure (ERROR logged), lazy singleton loads once.
- **pHash**: near-duplicate collapse at Hamming ≤ 8 (fixture: same image at 2 sizes + 1 distinct), higher-`siglip_score` survivor wins, backfill refills to 8 and hash-checks backfills, decode failure → treated unique, `phash` populated on results.
- **Downloader**: SSRF rejection (loopback, RFC1918, link-local, metadata IP, redirect-to-private via mocked resolver), size-cap abort (lying `Content-Length`), content-type + magic-byte validation, SVG rejection, timeout, `cached_url`/`file_id` population, metadata provenance fields incl. `phash`, width/height populated from decoded bytes.
- **Reserve promotion**: failed winner replaced by next reserve (reserve's in-flight future awaited, not re-fetched); reserves exhausted → partial results; all fail → `ToolCallException`.
- **Speculative prefetch**: downloads start before the rank call dispatch (mock ordering assertion); verdict∩prefetch reconcile keeps winners, cancels dropped (cancel flag observed at chunk boundary; waste ≤ one chunk); dropped candidates' stored files not attached to the message; dedupe-hit candidates excluded from prefetch.
- **SigLIP client** (mock Infinity `/embeddings`): request shape (text call + base64-image batch call), L2-normalize + cosine correctness against fixture vectors, score mapping onto results, 5s timeout → provider-order fallback, connection error → one retry then fallback, non-200/malformed JSON → fallback, client-side LRU hit excludes cached images from the payload (payload-size assertion) and cache bounded at configured size. No server unit tests — Infinity is upstream software; we test our client and the integration boundary.
- **Thumb persistence**: winner gains `thumb_file_id`/`thumb_url`; thumb metadata carries `full_file_id` + `kind`, no `source_url`; full metadata gains `thumb_file_id`; lazy thumb generation for legacy dedupe hits; packet falls back to `cached_url` when thumb missing; thumb stored only after full-image store succeeds.
- Access: `user_can_access_chat_file` allows `CHAT_IMAGE_SEARCH` origin.
- **Ranking**: input = thumbnails dict (no downloads triggered), JSON parse (clean / code-fenced / garbage), out-of-range + duplicate index dropping, empty-ranked → empty-results path, winners/reserves split at 6, fallback chain (vision→SigLIP→provider) on timeout and on `get_default_llm_with_vision() is None`, normalize output ≤ 512px (mock LLM).
- **DNS pinning**: connection targets the validated IP; SNI/cert validation against original hostname; re-pin per redirect hop (mocked resolver + adapter inspection); shared core used by both fetchers.
- **Dedupe**: lookup runs pre-thumbnail on full pool; hit skips thumb fetch AND download, reuses `file_id`, rank thumb from stored bytes; miss proceeds; hit restricted to `CHAT_IMAGE_SEARCH` origin; batch lookup is one query.
- **GC**: inline deletion skips `CHAT_IMAGE_SEARCH`; orphan sweep deletes zero-reference files only **and only past the 24h grace window** (young unreferenced file survives; aged one deleted); multi-chat shared file survives single-chat deletion; full/thumb pair both message-referenced and both survive/sweep together; unattached speculative leftovers swept after grace.
- **Packets**: emission order start→queries→results→end; results contain cached winner URLs only, `thumb_url` populated (or fallback).

**Integration:**
- Against a live local SearXNG container (already in the dev stack) **with `image_proxy` enabled**: one query returns ≥1 result; thumbnails fetch successfully through the proxy exemption (this is the assertion that would have caught the guard/proxy conflict); a proxied `thumbnail_src` pointing at the container's private IP succeeds, an identical URL on any other host is rejected.
- `puffin-siglip` (pinned Infinity image, baked SigLIP weights): `/health` passes; text + 30-image `/embeddings` round-trip and client-side cosine end-to-end < 500 ms on dev hardware; this test doubles as the schema-drift guard for Infinity upgrades.

**Manual E2E (GB10):**
1. Enable tool on Puffin assistant, SearXNG provider active.
2. "Show me pictures of atlantic puffins" → expect tool call, 1–4 inline images, attribution links; confirm image `src` is `/api/chat/file/...`, not an external host. Check logs: ≤8 full downloads (rank candidates, speculative), started before the rank verdict lands; ≥1 cancellation when the verdict drops candidates.
3. "What does the SKALE logo look like?" → single relevant image; verify SigLIP scores + rank verdict in tool logs demoted non-logo results.
4. Query known to return the same image re-hosted (e.g. a famous photo) → grid shows distinct images, pHash collapse visible in logs.
5. Kill SearXNG → graceful failure message, no stack trace to user.
6. Reload the chat a day later → images still render (served from FileStore, not the internet).
7. Open the chat as a second user (shared session) → images render (access branch works).
8. Verify markdown images render on mobile web; timeline shows the thumbnail grid with ranked winners.
9. Repeat the same query → confirm near-instant response (dedupe hits in logs; zero thumb fetches, zero downloads).
10. Delete one of two chats sharing a deduped image → image still renders in the surviving chat; run the sweep → file survives; delete the second chat + sweep → file gone.
11. Compare wall time in logs: post-SigLIP segment ≈ rank duration alone (downloads absorbed); cold first call shows no SigLIP load penalty (warm-up completed at startup).
12. Open a long image-heavy chat on mobile → network panel shows the grid loading ~30–60 KB thumbs, not full images; total timeline transfer < 1 MB.
13. Second "puffins" query on the same worker → client LRU hits in API logs, image payload near-empty; scoring segment dominated by one text embed + cosine.
14. `docker stop puffin-siglip` → next image search still succeeds (provider-order funnel, ERROR in API logs); `docker start` → next call scores again with no API-server restart.

---

## 12. Implementation checklist

**Search & providers**
- [ ] `image_search/models.py` — `ImageSearchResult` (+ `siglip_score`, `phash`), `ImageSearchProvider`, constants
- [ ] `searxng_client.py` — `search_images` (+ implements `ImageSearchProvider`)

**Tool core**
- [ ] Move `_sanitize_query` / `_normalize_queries_input` to shared utils
- [ ] `image_search/download.py` — hardened downloader with DNS pinning + FileStore persistence; SSRF core exported for reuse
- [ ] `image_search/thumbnails.py` — in-memory thumbnail fetcher (shared SSRF core with SearXNG-origin exemption, relative-URL resolution, 512 KB/5s caps)
- [ ] `puffin-siglip` image: build stage baking SigLIP weights into a **pinned** Infinity image (`infinity_emb v2 --model-id google/siglip-base-patch16-224 --port 9100`)
- [ ] Compose stack: `puffin-siglip` service, internal network only, Infinity `/health` healthcheck, restart policy, optional GPU reservation / `--device cpu` override
- [ ] `image_search/siglip_client.py` — embeddings client (text + image-batch `/embeddings`, numpy cosine, per-worker image-embedding LRU, 5s timeout, one retry, fallback)
- [ ] Orphan sweep: 24h `created_at` grace window
- [ ] `image_search/phash.py` — near-duplicate collapse + backfill (`imagehash`)
- [ ] `image_search/ranking.py` — vision re-ranking on thumbnails (one-call rank, strict-JSON parse, winners+reserves, fallback chain)
- [ ] `image_search/image_search_tool.py` — tool class (dedupe lookup → thumbs → siglip → phash → **rank ∥ speculative prefetch** → reconcile/cancel → thumb persistence; winner + thumb descriptors attached to message; dropped files unattached)
- [ ] `download.py` — per-task cancel flag checked at chunk boundaries
- [ ] Thumb persistence: paired blob store, `thumb_file_id` cross-links, lazy generation for legacy dedupe hits
- [ ] `FileOrigin.CHAT_IMAGE_SEARCH` enum value
- [ ] `access.py` — extend `CHAT_IMAGE_GEN` allow-branch to `CHAT_IMAGE_SEARCH`
- [ ] `built_in_tools.py` — registry entries
- [ ] Dependencies: `imagehash` (API image); `transformers`/`torch` move to the sidecar image only — the API image gains **no** ML dependencies

**Dedupe & GC**
- [ ] Functional index on `file_metadata->>'source_url'` (migration)
- [ ] `partition_by_existing_source_url` batch lookup (runs pre-thumbnail, full pool)
- [ ] Origin skip in `delete_messages_and_files_from_chat_session`
- [ ] Nightly Celery orphan-sweep task

**Timeline UX**
- [ ] Streaming packet classes + `StreamingType` values
- [ ] Thumbnail-grid renderer + packet registration
- [ ] `actionUtils.ts` — icon mapping
- [ ] `AgentEditorPage.tsx` — default-on for new assistants

**Seeding, docs, tests**
- [ ] Alembic seed migration (tool row + `persona__tool` attach to all existing assistants)
- [ ] Default assistant prompt addition (deployment config)
- [ ] Unit tests (§11) + SearXNG + SigLIP integration tests
- [ ] Deploy-guide note: SearXNG `formats: [json]`, image engines enabled, `safesearch: 1`, `image_proxy` behavior + exemption, `puffin-siglip` Infinity service (internal-only, pinned tag, ~400–800 MB, device-verification step = Infinity's startup log)
- [ ] Manual E2E on GB10 (§11)

## 13. Future work

Remaining open ideas, none blocking:

1. **Cross-conversation visual dedupe** — `phash` is already stored per winner (§6.4/§6.6.1); an exact-hash index is a cheap first step, Hamming-distance search across the DB (BK-tree or a pg extension) the full version. Collapses the same image cached under different source URLs.
2. **SigLIP absolute-score threshold** — drop candidates below a floor before the vision rank; needs calibration data from production logs first.
3. **TTL / re-validation for stale cached images** if first-cached-forever ever becomes a problem.
4. **Plumb the user's message into the rank prompt** when tool kwargs expose it — the rank currently judges against the sanitized queries (§6.5.1), which is honest but loses intent the answer model didn't encode into its reformulation.
5. **Shared embedding store** — back the per-worker client LRUs with a small external store (e.g. Redis, already in the stack) keyed by normalized URL, restoring deployment-wide cache warmth without wrapping Infinity in custom serving code. (Batching is no longer future work — Infinity does it dynamically.)