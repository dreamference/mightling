# Puffin Image Search Tool — Technical Specification

**Status:** v14 — Implemented (updated from the source as built; v13 was the pre-implementation draft). Constants re-checked against `image_search_service.py` on 2026-09-28.
**Target:** Puffin (sidecar ecosystem)
**Source:** `dreamference/chat/image_search_service.py` (the sidecar), `OnyxRunner.enable_image_search` and siblings in `dreamference/chat/onyx_runner.py` (provisioning, registration, nginx), `GALLERY_SCRIPT`/`GALLERY_CSS` and `IMAGE_TOOL_STEP_SCRIPT` in the UI patch modules (presentation). Tests: `tests/test_image_search_service.py`, plus registration and nginx tests in `tests/test_onyx_runner.py`.

---

## 1. Overview

A built-in `Image Search` tool lets the LLM search the internet for images and display them inline in chat. It is implemented as an independent **Puffin sidecar container (`dreamference-image-search`, port 8768)** — the `dream-*` names of the v13 draft predate the deployment-wide rename to `dreamference-*` — and registered via Onyx's Custom Tool REST API, mirroring the Gmail integration.

One `POST /search` call runs the whole funnel: SearXNG's image category with a **wide candidate pool (30)**, **in-memory thumbnail fetches** behind a hardened SSRF guard, a **SigLIP pre-filter**, **perceptual-hash collapse** of visual near-duplicates, a **vision rank-and-filter pass** by the served multimodal model (with the shortlist's full downloads already **prefetching speculatively**), then persistence of the winners into a local store that nginx serves back at `/puffin-images/{file_id}.jpg`. Caching locally is the point: hotlinked images die of CORP blocks and link rot; cached ones render permanently.

**Deviation from the v13 draft, adopted deliberately:** the sidecar is **standard library + Pillow, not FastAPI**. It follows the Gmail sidecar exactly — a single staged file in a stock `python:3-slim` container, nothing to install at boot beyond Pillow itself (pip-installed into `/tmp` on start, skipped when present, degraded gracefully when offline). The draft's architecture — endpoints, funnel, hourly GC — is implemented whole; only the web framework differs.

---

## 2. The funnel, as implemented

```
LLM custom tool call {"queries": ["puffin bird flying"], "count": 6}   (count optional, 1–10, default 4)
        │
        ▼
POST http://dreamference-image-search:8768/search   (X-Puffin-Image-Token shared-secret header)
        │
        ▼
SearXNG image search (http://dreamference-searxng:8080, format=json)
round-robin merge across queries, dedupe by image URL, cap 30
        │
        ▼
DEDUPE LOOKUP (SQLite, by source URL): a known image reuses its cached file and skips all
network work — but it does NOT skip judgment. Cached candidates ride through the ranking with
their local file standing in for the thumbnail: an image cached for one query is not thereby
relevant to the next. (v13 treated the cache as pre-approved; a live person-search re-served
three unrelated cached images, and that behaviour is now a regression test.)
        │
        ▼
THUMB STEP: parallel in-memory fetches, 512 KB / 5 s caps, unfetchable candidates drop out
        │
        ▼
SIGLIP PRE-FILTER (http://dreamference-siglip:9100, Infinity): cosine-scored, keep the top
max(8, count+2). DEGRADES to first-N when unavailable — which on GB10 it is: Infinity
publishes amd64 images only and GB10 is aarch64 (see §7).
        │
        ▼
PHASH COLLAPSE: hand-rolled 64-bit DCT pHash (Pillow + stdlib, no numpy/imagehash),
Hamming ≤ 8 merges to the first representative
        │
        ▼
RANK-AND-FILTER ─┬─ SPECULATIVE PREFETCH (concurrent)
   (vision)      │   hardened full downloads of every FRESH shortlist candidate begin when
                 │   the rank call is dispatched; cached candidates prefetch nothing
  one standard /v1 vision call to the served vLLM model: shortlist thumbnails + query →
  strict-JSON array of the MATCHING indices, best first. A successful verdict is a FILTER:
  indices it leaves out stay out (an empty array is a valid answer), and only a transport or
  parse failure falls back to the unfiltered order. Even a single candidate is judged — one
  wrong image confidently embedded is the exact complaint that added filtering.
        │
        ▼
RECONCILE: winners = verdict ∩ successful downloads, up to `count`; in-flight fetches of
rank-dropped candidates are cancelled
        │
        ▼
PERSIST: winners re-encoded to JPEG (normalises format, strips non-image payload) into the
store; phash + source URL + title recorded in SQLite
        │
        ▼
{"response": "![title](/puffin-images/{file_id}.jpg)\n\n…",
 "instructions": "…include these lines VERBATIM… <the same markdown again>"}
```

The `instructions` field restates the Markdown embeds and forbids answering with a bare
acknowledgement, because a model given only a pointer ("embed the above") has answered `Done`
and shown nothing. The Puffin persona prompt and the tool's OpenAPI summary reinforce the same
contract from their side.

---

## 3. Sidecar service design

Single self-contained file, staged to `~/.config/dreamference/image-search/service.py` and
bind-mounted at `/config` — it runs on the host for imports/tests and as `__main__` in the
container, like the Gmail service.

### 3.1 Storage — user-owned bind mount, not a named volume

The container runs `--user uid:gid`, and Docker creates named volumes root-owned — the exact
permission trap the torch.compile cache documented. The store is therefore a directory under
the same bind mount: `/config/data/images/*.jpg` plus `/config/data/db.sqlite` (source URL,
file id, pHash, title, timestamp). **GC:** a background thread deletes images older than 7 days
hourly and then trims oldest-first to a 2 GB cap; a DB row whose file was collected reads as
absent and is purged.

### 3.2 Nginx routing — host-side template, deferred resolution

`OnyxRunner._inject_image_route()` rewrites the **host-side**
`~/.config/onyx/data/nginx/app.conf.template` (marker-based, cut-at-marker idempotent, so the
edit survives container recreates), inserting after the `client_max_body_size` line:

```nginx
location /puffin-images/ {
    resolver 127.0.0.11 valid=10s;
    set $puffin_img http://dreamference-image-search:8768;
    rewrite ^/puffin-images/(.*)$ /images/$1 break;
    proxy_pass $puffin_img;
}
```

Two hard-won constraints:
- **The deferred form is load-bearing.** A literal `proxy_pass` hostname is resolved at config
  load; with the sidecar absent, nginx refuses to start *at all* and the whole UI dies.
  Verified live: with this form and the sidecar stopped, nginx stays healthy and only
  `/puffin-images/` answers 502. (The `rewrite` exists because a variable `proxy_pass` does not
  append the location remainder.) `$puffin_img` and `$1` survive the entrypoint's `envsubst`
  because its variable whitelist names neither.
- **Never restart nginx when the template is unchanged.** `configure` talks to Onyx *through*
  this proxy; the first implementation restarted it unconditionally and every later step died
  with ECONNRESET. On change, nginx is restarted (found by compose service label, never by
  name) and polled back to health before returning.

### 3.3 Tool registration

The v13 draft's `{"custom_tool_url": …}` sketch does not match Onyx's API. Registration mirrors
Gmail: an **OpenAPI document** (`openapi_definition()` in the service module, one
`image_search` POST operation with `queries` and optional `count`) sent to
`POST /admin/tool/custom`, with lookup-then-`PUT` so re-runs update rather than duplicate, and
a `custom_headers` shared secret (`X-Puffin-Image-Token`, generated once into the data
directory). `puffin-admin puffin configure` (alias `onyx`) runs it; `--no-image-search` skips it. Image `GET`s carry no
secret — the browser is the caller and the ids are unguessable.

---

## 4. Provider & inference interactions

- **SearXNG** — `SEARXNG_CONTAINER_URL` (the deployment's own instance, JSON API). Relative
  `thumbnail_src` values are resolved against the SearXNG base.
- **SigLIP** — `dreamference-siglip`, an Infinity server (`michaelf34/infinity`, CPU,
  `google/siglip-base-patch16-224`, weights in a named volume), provisioned best-effort by
  `_start_siglip()`. See §7 for the arm64 gap.
- **Vision** — the served vLLM model, reached at the runner's existing loopback→bridge-gateway
  rewrite (`resolve_container_vllm_url`) with the registry-resolved model id; both passed as
  environment, never recomputed in the sidecar.

---

## 5. Security & hardening

The hardened downloader implements, and the tests assert:
- **DNS pinning with private-address rejection** — every resolved address is checked
  (private, loopback, link-local, multicast, reserved, unspecified) and the checked address is
  the one connected to, so a rebinding answer cannot swap targets between check and connect.
- **Redirect capping** (3 hops), every hop re-validated.
- **Streamed size caps** — 512 KB thumbnails, 15 MB full images; over-cap bodies are refused,
  not truncated.
- **Magic-byte sniffing** (JPEG/PNG/GIF/WebP) — Content-Type is never trusted.
- **Exactly one SSRF exemption:** SearXNG's own host, because image results routinely carry
  `thumbnail_src` as SearXNG's private `/image_proxy` route. Full-size downloads get no
  exemptions at all. The same private address behind any other hostname stays refused
  (regression-tested).

---

## 6. Presentation

- **Gallery** (`GALLERY_SCRIPT` + `GALLERY_CSS`): the tool's images inside one assistant
  message are regrouped into a clickable mosaic — hero image left, tiles right, keyed per
  count; five or more switch to a three-column tile grid. Clicking opens a lightbox
  (backdrop/Escape closes, arrows and arrow keys navigate). React's nodes are never moved —
  moving them breaks reconciliation — the originals are hidden in place and mirrored, and the
  sweep rebuilds when React re-renders.
- **Step-viewer JSON** (`IMAGE_TOOL_STEP_SCRIPT`): Onyx renders every custom tool's raw result
  as a "Response" block in the reasoning timeline. For image_search that JSON duplicates what
  the user already sees as images, so it is hidden — for this tool only; other tools' Response
  blocks stay inspectable. Text-anchored by necessity (the block carries no test id), hence a
  script rather than CSS.

---

## 7. Known platform gap — SigLIP on aarch64

Infinity publishes amd64 images only; GB10 is aarch64, so `dreamference-siglip` cannot start
there. The start is best-effort, the warning names the reason, and the funnel degrades to its
first-N candidates — the vision rank-and-filter still runs and is the stronger judgment. On an
amd64 deployment the provisioning works as written. If the pre-filter becomes load-bearing on
GB10, the follow-up is a small torch/transformers SigLIP sidecar in the Gmail/image-search
mould (aarch64 wheels exist for both).

---

## 8. Testing

**Unit (offline, `tests/test_image_search_service.py`):** SSRF assertions (private ranges,
the single SearXNG exemption, redirect cap, size cap, magic bytes), pHash collapse/separation,
store dedupe and GC (age + size cap), the funnel with injected fakes (round-robin merge,
cached-skip-network-but-not-judgment, vision verdict as filter, empty verdict, requested
count satisfied and gracefully under-satisfied, unfetchable candidates dropping out), and the
OpenAPI document. The funnel's collaborators are constructor-injected for exactly this.

**Runner (`tests/test_onyx_runner.py`):** registration payload (OpenAPI document + secret
header, create-then-update), nginx route idempotence and deferred form, configure opt-out. An
autouse fixture stubs the provisioning internals so the suite never starts containers.

**Live E2E (performed):** `puffin-admin puffin configure` (alias `onyx`) registers the tool; a real `/search` returns
ranked cached embeds; the image serves through `localhost:3000/puffin-images/…` (200,
`image/jpeg`); nginx survives the sidecar being stopped; a `count: 6` request returns six.
