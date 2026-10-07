"""
Mightling Image Search Service.

Implements ``specs/DREAMFERENCE_IMAGE_SEARCH.md``: a standalone sidecar (the
``dreamference-image-search`` container) that lets the assistant search the web for images and
embed them inline. One ``POST /search`` call runs the whole funnel -- SearXNG's image category
with a wide candidate pool, in-memory thumbnail fetches behind a hardened SSRF guard, a SigLIP
pre-filter over the thumbnails, perceptual-hash collapse of visual near-duplicates, a vision
re-rank by the served multimodal model with the full downloads already prefetching
speculatively, and finally persistence of the winners into a local store that the deployment's
nginx serves back at ``/puffin-images/{file_id}.jpg``. Caching locally is the point: hotlinked
images die of CORP blocks and link rot; cached ones render forever.

**Deviation from the spec, stated loudly:** the spec names FastAPI. This service is standard
library plus Pillow instead, for the same reason the Gmail sidecar is stdlib-only -- it runs in
a stock ``python:3-slim`` container from a single staged file, with nothing to install at boot
beyond Pillow itself, and an install-at-boot dependency stack is exactly what an air-gapped
target cannot afford. The spec's architecture (endpoints, funnel, hourly GC) is implemented
whole; only the web framework differs. Pillow is import-guarded: without it the funnel still
runs, minus decoding-dependent stages (pHash collapse, JPEG re-encode).

Like the Gmail service, this module runs in two worlds: imported by Dreamference on the host
(for registration metadata and tests) and executed as ``__main__`` inside the container. It must
therefore stay a single self-contained file.
"""

import base64
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import socket
import sqlite3
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Final, List, Optional, Tuple

try:  # Pillow is the one third-party need; every use degrades without it.
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only in a bare container
    Image = None

SERVICE_PORT: Final[int] = 8768

# The funnel's shape, straight from the spec: a wide pool in, eight ranked, a few embedded.
CANDIDATE_POOL_SIZE: Final[int] = 30
RANK_CANDIDATES: Final[int] = 8
RESULT_COUNT: Final[int] = 4
MAX_RESULT_COUNT: Final[int] = 10
PHASH_HAMMING_MAX: Final[int] = 8

# Thumbnails are fetched in memory and deliberately small; full images are bounded too, because
# the downloader talks to the open internet and a cap is the difference between a slow site and
# a memory exhaustion.
THUMB_MAX_BYTES: Final[int] = 512 * 1024
THUMB_TIMEOUT_SECONDS: Final[float] = 5.0
FULL_MAX_BYTES: Final[int] = 15 * 1024 * 1024
FULL_TIMEOUT_SECONDS: Final[float] = 20.0
REDIRECT_CAP: Final[int] = 3

GC_MAX_AGE_SECONDS: Final[int] = 7 * 24 * 3600
GC_INTERVAL_SECONDS: Final[int] = 3600
GC_MAX_VOLUME_BYTES: Final[int] = 2 * 1024 * 1024 * 1024

# The one SSRF exemption the funnel needs: SearXNG returns `thumbnail_src` as its own relative
# `/image_proxy?url=...` route, which resolves to a private container address. Thumbnails may
# therefore fetch from the SearXNG host alone; full-size downloads get no exemptions at all.
AUTH_HEADER: Final[str] = "X-Mightling-Image-Token"

SEARXNG_URL_ENV: Final[str] = "MIGHTLING_SEARXNG_URL"
SIGLIP_URL_ENV: Final[str] = "MIGHTLING_SIGLIP_URL"
VISION_URL_ENV: Final[str] = "MIGHTLING_VISION_URL"
VISION_MODEL_ENV: Final[str] = "MIGHTLING_VISION_MODEL"
DATA_DIR_ENV: Final[str] = "MIGHTLING_DATA_DIR"
SECRET_ENV: Final[str] = "MIGHTLING_IMAGE_SECRET"

# Magic bytes accepted from the network. Sniffing the payload rather than trusting Content-Type
# is what keeps an attacker-controlled URL from handing the pipeline an HTML page or worse.
_MAGIC: Final[Tuple[Tuple[bytes, str], ...]] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class FetchRejected(Exception):
    """A download refused by the hardened fetcher, with the reason in the message."""


def sniff_image_type(payload: bytes) -> Optional[str]:
    """
    Identifies an image payload by its magic bytes.

    Args:
        payload (bytes): The first bytes of a downloaded body.

    Returns:
        Optional[str]: The MIME type, or None if the bytes are not a known image format.
    """
    for magic, mime in _MAGIC:
        if payload.startswith(magic):
            return mime
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


class HardenedFetcher:
    """
    Downloads a URL from the open internet without letting it probe the Docker network.

    The defenses are the spec's list: DNS resolution up front with every address checked
    against the private ranges (pinning -- the checked address is the one connected to, so a
    rebinding DNS answer cannot swap targets between check and connect), a redirect cap with
    every hop re-validated, a streamed size cap, and magic-byte sniffing of the result.
    """

    @classmethod
    def assert_public(
        cls,
        host: str,
        allow_hosts: frozenset = frozenset(),
        resolver: Any = socket.getaddrinfo,
    ) -> str:
        """
        Resolves a hostname and rejects it unless every address is publicly routable.

        Args:
            host (str): Hostname or address literal from the URL.
            allow_hosts (frozenset): Hostnames exempt from the private-address check.
            resolver (Any): DNS resolution function, injectable for tests.

        Returns:
            str: The single pinned IP address to connect to.
        """
        if host in allow_hosts:
            try:
                infos = resolver(host, None)
            except OSError as exc:
                raise FetchRejected(f"DNS failure for {host}: {exc}")
            return infos[0][4][0]
        try:
            infos = resolver(host, None)
        except OSError as exc:
            raise FetchRejected(f"DNS failure for {host}: {exc}")
        if not infos:
            raise FetchRejected(f"{host} resolved to nothing")
        pinned = None
        for info in infos:
            address = info[4][0]
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                raise FetchRejected(f"{host} resolved to non-address {address!r}")
            if (
                parsed.is_private or parsed.is_loopback or parsed.is_link_local
                or parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified
            ):
                raise FetchRejected(f"{host} resolves to non-public address {address}")
            pinned = pinned or address
        return pinned

    @classmethod
    def fetch(
        cls,
        url: str,
        max_bytes: int,
        timeout: float,
        allow_hosts: frozenset = frozenset(),
        resolver: Any = socket.getaddrinfo,
        opener: Any = None,
    ) -> bytes:
        """
        Downloads a URL under the full set of guards.

        Args:
            url (str): The http(s) URL to fetch.
            max_bytes (int): Hard cap on the body size.
            timeout (float): Per-request timeout in seconds.
            allow_hosts (frozenset): Hostnames exempt from the private-address check.
            resolver (Any): DNS resolution function, injectable for tests.
            opener (Any): Callable ``(url, pinned_ip, timeout) -> response`` for tests; the
                default opens a real pinned-IP connection.

        Returns:
            bytes: The body, verified to start with a known image format's magic bytes.
        """
        current = url
        for _ in range(REDIRECT_CAP + 1):
            parsed = urllib.parse.urlparse(current)
            if parsed.scheme not in ("http", "https"):
                raise FetchRejected(f"scheme {parsed.scheme!r} refused")
            host = parsed.hostname
            if not host:
                raise FetchRejected("URL carries no host")
            pinned = cls.assert_public(host, allow_hosts=allow_hosts, resolver=resolver)
            response = (opener or cls._open)(current, pinned, timeout)
            status = getattr(response, "status", 200)
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise FetchRejected("redirect without Location")
                current = urllib.parse.urljoin(current, location)
                continue
            if status != 200:
                response.close()
                raise FetchRejected(f"HTTP {status}")
            body = cls._read_capped(response, max_bytes)
            response.close()
            if sniff_image_type(body) is None:
                raise FetchRejected("payload is not a known image format")
            return body
        raise FetchRejected("redirect cap exceeded")

    @classmethod
    def _open(cls, url: str, pinned_ip: str, timeout: float):
        """
        Opens a connection to the pinned address while presenting the original hostname.

        Args:
            url (str): The URL being fetched.
            pinned_ip (str): The address `assert_public` validated.
            timeout (float): Connect/read timeout in seconds.

        Returns:
            http.client.HTTPResponse: The live response object.
        """
        import http.client

        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        if parsed.scheme == "https":
            raw = socket.create_connection((pinned_ip, port), timeout=timeout)
            context = ssl.create_default_context()
            sock = context.wrap_socket(raw, server_hostname=host)
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
            conn.sock = sock
        else:
            conn = http.client.HTTPConnection(pinned_ip, port, timeout=timeout)
        conn.request(
            "GET", path,
            headers={"Host": host, "User-Agent": "puffin-image-search/1.0", "Accept": "image/*"},
        )
        return conn.getresponse()

    @classmethod
    def _read_capped(cls, response: Any, max_bytes: int) -> bytes:
        """
        Streams a response body up to a hard size cap.

        Args:
            response (Any): An object with a file-like ``read``.
            max_bytes (int): The cap; a body that exceeds it is refused, not truncated.

        Returns:
            bytes: The complete body.
        """
        chunks = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = response.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        if len(body) > max_bytes:
            raise FetchRejected(f"body exceeds {max_bytes} bytes")
        if not body:
            raise FetchRejected("empty body")
        return body


def phash64(image: Any) -> int:
    """
    Computes a 64-bit perceptual hash of an image.

    The classic pHash recipe: 32x32 grayscale, 2D DCT-II, the top-left 8x8 of coefficients
    minus the DC term, thresholded at their median. Implemented directly so the container
    needs no numpy or imagehash -- 32x32 is small enough that plain loops are instant.

    Args:
        image (Any): A PIL image.

    Returns:
        int: The 64-bit hash.
    """
    size = 32
    gray = image.convert("L").resize((size, size))
    pixels = list(gray.getdata())
    rows = [pixels[i * size:(i + 1) * size] for i in range(size)]

    cos = [[math.cos((2 * x + 1) * u * math.pi / (2 * size)) for x in range(size)]
           for u in range(8)]
    row_dct = [[sum(rows[y][x] * cos[u][x] for x in range(size)) for u in range(8)]
               for y in range(size)]
    coeffs = [[sum(row_dct[y][u] * cos[v][y] for y in range(size)) for u in range(8)]
              for v in range(8)]

    flat = [coeffs[v][u] for v in range(8) for u in range(8)][1:]
    ordered = sorted(flat)
    median = ordered[len(ordered) // 2]
    bits = 0
    for value in flat:
        bits = (bits << 1) | (1 if value > median else 0)
    return bits


def hamming(a: int, b: int) -> int:
    """
    Counts differing bits between two 64-bit hashes.

    Args:
        a (int): First hash.
        b (int): Second hash.

    Returns:
        int: The Hamming distance.
    """
    return bin(a ^ b).count("1")


class ImageStore:
    """
    The sidecar's persistence: image files on disk, dedupe metadata in SQLite.

    The database lives beside the images so the whole store is one directory, which is what
    lets the runner mount a single user-owned path instead of a root-owned named volume --
    the same permission trap the torch.compile cache hit, avoided the same way.
    """

    def __init__(self, data_dir: str):
        """
        Opens (creating if needed) the store under a directory.

        Args:
            data_dir (str): Directory for ``images/`` and ``db.sqlite``.
        """
        self.data_dir = data_dir
        self.images_dir = os.path.join(data_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(os.path.join(data_dir, "db.sqlite"), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS images ("
            "file_id TEXT PRIMARY KEY, source_url TEXT UNIQUE, phash INTEGER,"
            "title TEXT, created REAL)"
        )
        self._db.commit()

    def path(self, file_id: str) -> str:
        """
        Maps a file id to its on-disk path.

        Args:
            file_id (str): The stored image's identifier.

        Returns:
            str: Absolute path of the JPEG.
        """
        return os.path.join(self.images_dir, f"{file_id}.jpg")

    def lookup_source(self, source_url: str) -> Optional[Dict[str, Any]]:
        """
        Finds a previously cached image by its source URL.

        Args:
            source_url (str): The original image URL.

        Returns:
            Optional[Dict[str, Any]]: The row, or None. A row whose file has been garbage
                collected is treated as absent and purged.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT file_id, phash, title FROM images WHERE source_url=?", (source_url,)
            ).fetchone()
        if not row:
            return None
        if not os.path.exists(self.path(row[0])):
            with self._lock:
                self._db.execute("DELETE FROM images WHERE file_id=?", (row[0],))
                self._db.commit()
            return None
        return {"file_id": row[0], "phash": row[1], "title": row[2]}

    def lookup_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Finds a cached image's metadata by its file id.

        Args:
            file_id (str): The stored image's identifier.

        Returns:
            Optional[Dict[str, Any]]: ``source_url`` and ``title``, or None.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT source_url, title FROM images WHERE file_id=?", (file_id,)
            ).fetchone()
        return {"source_url": row[0], "title": row[1]} if row else None

    def persist(self, source_url: str, payload: bytes, title: str,
                phash: Optional[int]) -> str:
        """
        Writes an image to disk and records its metadata.

        Args:
            source_url (str): Where the bytes came from.
            payload (bytes): The image body. Re-encoded to JPEG when Pillow is present, which
                also normalises the format and strips any non-image payload.
            title (str): Human title for the markdown alt text.
            phash (Optional[int]): Perceptual hash, if one was computed.

        Returns:
            str: The new file id.
        """
        file_id = hashlib.sha256(source_url.encode()).hexdigest()[:16]
        if Image is not None:
            try:
                decoded = Image.open(io.BytesIO(payload)).convert("RGB")
                out = io.BytesIO()
                decoded.save(out, format="JPEG", quality=90)
                payload = out.getvalue()
            except Exception:
                pass
        with open(self.path(file_id), "wb") as fh:
            fh.write(payload)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO images (file_id, source_url, phash, title, created)"
                " VALUES (?,?,?,?,?)",
                (file_id, source_url, phash, title, time.time()),
            )
            self._db.commit()
        return file_id

    def collect_garbage(self, now: Optional[float] = None,
                        max_age: int = GC_MAX_AGE_SECONDS,
                        max_bytes: int = GC_MAX_VOLUME_BYTES) -> int:
        """
        Deletes expired images, then trims oldest-first to the volume cap.

        Args:
            now (Optional[float]): The clock, injectable for tests.
            max_age (int): Age past which an image is deleted.
            max_bytes (int): Total size cap for the store.

        Returns:
            int: Number of images removed.
        """
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            rows = self._db.execute(
                "SELECT file_id, created FROM images ORDER BY created ASC").fetchall()
        survivors = []
        for file_id, created in rows:
            if now - created > max_age:
                self._delete(file_id)
                removed += 1
            else:
                survivors.append(file_id)
        total = 0
        sizes = []
        for file_id in survivors:
            try:
                size = os.path.getsize(self.path(file_id))
            except OSError:
                size = 0
            sizes.append((file_id, size))
            total += size
        for file_id, size in sizes:  # oldest first, from the ordered query above
            if total <= max_bytes:
                break
            self._delete(file_id)
            total -= size
            removed += 1
        return removed

    def _delete(self, file_id: str) -> None:
        """
        Removes one image and its row.

        Args:
            file_id (str): The stored image's identifier.
        """
        try:
            os.remove(self.path(file_id))
        except OSError:
            pass
        with self._lock:
            self._db.execute("DELETE FROM images WHERE file_id=?", (file_id,))
            self._db.commit()


class SearxngClient:
    """Queries the deployment's SearXNG for image results."""

    def __init__(self, base_url: str):
        """
        Args:
            base_url (str): SearXNG base URL, e.g. ``http://dreamference-searxng:8080``.
        """
        self.base_url = base_url.rstrip("/")
        self.host = urllib.parse.urlparse(self.base_url).hostname or ""

    def search_images(self, query: str) -> List[Dict[str, str]]:
        """
        Runs one image-category search.

        Args:
            query (str): The search text.

        Returns:
            List[Dict[str, str]]: Candidates with ``image_url``, ``thumb_url``, ``title``.
                ``thumb_url`` is resolved against the SearXNG base because results routinely
                carry it as SearXNG's own relative ``/image_proxy`` route.
        """
        params = urllib.parse.urlencode(
            {"q": query, "categories": "images", "format": "json"})
        request = urllib.request.Request(f"{self.base_url}/search?{params}")
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode())
        candidates = []
        for item in data.get("results", []):
            image_url = item.get("img_src") or ""
            if not image_url.startswith("http"):
                continue
            thumb = item.get("thumbnail_src") or image_url
            candidates.append({
                "image_url": image_url,
                "thumb_url": urllib.parse.urljoin(self.base_url + "/", thumb),
                "title": (item.get("title") or "image").strip(),
            })
        return candidates


class SiglipClient:
    """Scores thumbnails against the query through the Infinity embeddings sidecar."""

    def __init__(self, base_url: str, model: str = ""):
        """
        Args:
            base_url (str): The Infinity server, e.g. ``http://dreamference-siglip:9100``.
            model (str): Model id to name in requests; Infinity defaults to its only model.
        """
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _embed(self, inputs: List[str], modality: str) -> List[List[float]]:
        """
        Posts one embeddings request.

        Args:
            inputs (List[str]): Texts, or data-URI images.
            modality (str): ``"text"`` or ``"image"``.

        Returns:
            List[List[float]]: One vector per input, in order.
        """
        payload: Dict[str, Any] = {"input": inputs, "modality": modality}
        if self.model:
            payload["model"] = self.model
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode())
        rows = sorted(data.get("data", []), key=lambda r: r.get("index", 0))
        return [row["embedding"] for row in rows]

    def score(self, query: str, thumbnails: List[bytes]) -> List[float]:
        """
        Cosine-scores thumbnails against a query.

        Args:
            query (str): The search text.
            thumbnails (List[bytes]): Image payloads.

        Returns:
            List[float]: One score per thumbnail, in order.
        """
        text_vec = self._embed([query], "text")[0]
        uris = [
            "data:image/jpeg;base64," + base64.b64encode(t).decode() for t in thumbnails
        ]
        image_vecs = self._embed(uris, "image")

        def cosine(a: List[float], b: List[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
            return dot / norm if norm else 0.0

        return [cosine(text_vec, vec) for vec in image_vecs]


class VisionRanker:
    """Re-ranks the shortlist with the served multimodal model."""

    def __init__(self, base_url: str, model: str):
        """
        Args:
            base_url (str): OpenAI-compatible base URL including ``/v1``.
            model (str): Served model id.
        """
        self.base_url = base_url.rstrip("/")
        self.model = model

    def rank(self, query: str, thumbnails: List[bytes]) -> List[int]:
        """
        Asks the model to order thumbnails by relevance.

        Args:
            query (str): The search text.
            thumbnails (List[bytes]): The shortlisted thumbnail payloads.

        Returns:
            List[int]: Indices into ``thumbnails``, best first. Falls back to the given order
                if the model's answer is not the strict JSON asked for -- ranking is an
                improvement, never a gate.
        """
        content: List[Dict[str, Any]] = [{
            "type": "text",
            "text": (
                f"You are filtering image search results for the query {query!r}. "
                f"Of these {len(thumbnails)} images, which actually show what the query asks "
                "for? Answer with ONLY a JSON array of the matching image numbers, best "
                "first, e.g. [2,0]. Numbers are 0-based. EXCLUDE any image that does not "
                "clearly match -- an empty array [] is a valid answer. Never pad the array "
                "with unrelated images."
            ),
        }]
        for thumb in thumbnails:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(thumb).decode()},
            })
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 200,
                "temperature": 0,
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        fallback = list(range(len(thumbnails)))
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode())
            text = data["choices"][0]["message"]["content"]
            match = re.search(r"\[[\d,\s]*\]", text)
            order = json.loads(match.group(0)) if match else []
        except Exception:
            return fallback
        if match is None:
            return fallback
        # A successful verdict is a *filter*: indices the model left out are rejected, not
        # backfilled -- padding with unrelated images is exactly the failure this exists to
        # stop. Only a transport or parse failure falls back to the unfiltered order.
        seen = [i for i in order if isinstance(i, int) and 0 <= i < len(thumbnails)]
        return list(dict.fromkeys(seen))


class ImageSearchService:
    """
    The funnel, wired from collaborators so every stage is testable offline.

    Each stage degrades rather than gates: no SigLIP means the first eight candidates go to
    ranking, no vision model means SigLIP's order stands, and a candidate whose thumbnail or
    full image cannot be fetched simply drops out.
    """

    def __init__(self, store: ImageStore, searxng: SearxngClient,
                 siglip: Optional[SiglipClient], ranker: Optional[VisionRanker],
                 fetcher: Any = HardenedFetcher):
        """
        Args:
            store (ImageStore): Persistence for winners.
            searxng (SearxngClient): Candidate source.
            siglip (Optional[SiglipClient]): Pre-filter; skipped when None or failing.
            ranker (Optional[VisionRanker]): Vision re-rank; skipped when None or failing.
            fetcher (Any): The hardened downloader; injectable for tests.
        """
        self.store = store
        self.searxng = searxng
        self.siglip = siglip
        self.ranker = ranker
        self.fetcher = fetcher
        self._pool = ThreadPoolExecutor(max_workers=8)

    def search(self, queries: List[str], count: int = RESULT_COUNT) -> Dict[str, str]:
        """
        Runs the full funnel for a set of queries.

        Args:
            queries (List[str]): One or more search texts; results are merged round-robin.
            count (int): How many images the user asked for; clamped to
                ``1..MAX_RESULT_COUNT`` and satisfied unless fewer relevant images exist.

        Returns:
            Dict[str, str]: The custom-tool response: markdown embeds plus instructions.
        """
        count = max(1, min(int(count or RESULT_COUNT), MAX_RESULT_COUNT))
        candidates = self._gather(queries)
        query_text = "; ".join(queries)

        # Dedupe lookup first: a known source URL reuses its cached file and skips every
        # network stage for that candidate -- but it does NOT skip judgment. An image cached
        # for one query is not thereby relevant to the next, so cached candidates ride
        # through the ranking like the rest, with their local file standing in for the
        # thumbnail. Seen live: a person search re-served three unrelated cached images
        # because the cache was treated as pre-approved.
        entries = []
        for cand in candidates:
            row = self.store.lookup_source(cand["image_url"])
            if row:
                cand = dict(cand, file_id=row["file_id"],
                            title=row["title"] or cand["title"])
            entries.append(cand)

        winners = self._run_funnel(query_text, entries, count)

        if not winners:
            return {
                "response": "No embeddable images were found for this search.",
                "instructions": "Tell the user the image search came up empty.",
            }
        lines = [f"![{w['title']}](/puffin-images/{w['file_id']}.jpg)" for w in winners]
        markdown = "\n\n".join(lines)
        # The instructions restate the embeds because models given only a pointer ("embed the
        # above") have answered `Done` and shown nothing -- the images render only if the model
        # copies these lines into its reply, so the tool says so twice and shows them twice.
        return {
            "response": markdown,
            "instructions": (
                "Write your answer to the user now, and include the following Markdown "
                "image lines in it VERBATIM -- copying them into the reply is what makes "
                "the images visible to the user. Do not merely acknowledge them, do not "
                "describe them instead, and never answer with just 'Done'.\n\n" + markdown
            ),
        }

    def _gather(self, queries: List[str]) -> List[Dict[str, str]]:
        """
        Merges per-query result lists round-robin and dedupes by image URL.

        Args:
            queries (List[str]): The search texts.

        Returns:
            List[Dict[str, str]]: At most ``CANDIDATE_POOL_SIZE`` candidates.
        """
        per_query = []
        for query in queries:
            try:
                per_query.append(self.searxng.search_images(query))
            except Exception:
                per_query.append([])
        merged, seen = [], set()
        for rank in range(max((len(r) for r in per_query), default=0)):
            for results in per_query:
                if rank < len(results):
                    url = results[rank]["image_url"]
                    if url not in seen:
                        seen.add(url)
                        merged.append(results[rank])
                if len(merged) >= CANDIDATE_POOL_SIZE:
                    return merged
        return merged

    def _run_funnel(self, query: str, candidates: List[Dict[str, str]],
                    need: int) -> List[Dict[str, str]]:
        """
        Thumbnails, SigLIP, pHash collapse, speculative prefetch, vision rank, persist.

        Args:
            query (str): Combined query text for scoring and ranking.
            candidates (List[Dict[str, str]]): Fresh candidates, funnel order.
            need (int): How many winners to return.

        Returns:
            List[Dict[str, str]]: Winners with ``file_id`` and ``title``.
        """
        thumbs = self._fetch_thumbnails(candidates)
        if not thumbs:
            return []
        rank_pool = max(RANK_CANDIDATES, min(need + 2, CANDIDATE_POOL_SIZE))
        kept = self._prefilter(query, thumbs, rank_pool)
        kept = self._collapse(kept)
        kept = kept[:rank_pool]

        # Speculative prefetch: every fresh rank candidate's full download starts before the
        # vision verdict, so the verdict reconciles against bytes already in flight. Cached
        # candidates have their bytes on disk already and prefetch nothing.
        futures = {
            id(c): self._pool.submit(self._fetch_full, c["image_url"])
            for c, _ in kept if "file_id" not in c
        }
        order = list(range(len(kept)))
        # Even a single candidate goes to the vision pass: it filters as well as orders, and
        # one wrong image confidently embedded is the exact complaint that added filtering.
        # The thumbnails are downscaled first: at full size (up to 512 KB each, eight of them)
        # one rank call is a multi-thousand-token multimodal prefill on the shared engine, and
        # the verdict is no better for the extra pixels. 256px matches what the cached-thumb
        # path already feeds the ranking.
        if self.ranker is not None and kept:
            try:
                order = self.ranker.rank(query, [self._rank_thumb(t) for _, t in kept])
            except Exception:
                pass

        winners: List[Dict[str, str]] = []
        chosen = set()
        for index in order:
            if len(winners) >= need:
                break
            cand, thumb = kept[index]
            chosen.add(id(cand))
            if "file_id" in cand:
                winners.append({"file_id": cand["file_id"], "title": cand["title"]})
                continue
            payload = futures[id(cand)].result()
            if payload is None:
                continue
            phash = None
            if Image is not None:
                try:
                    phash = phash64(Image.open(io.BytesIO(thumb)))
                except Exception:
                    phash = None
            file_id = self.store.persist(cand["image_url"], payload, cand["title"], phash)
            winners.append({"file_id": file_id, "title": cand["title"]})
        for key, future in futures.items():
            if key not in chosen:
                future.cancel()
        return winners

    def _fetch_thumbnails(self, candidates: List[Dict[str, str]]
                          ) -> List[Tuple[Dict[str, str], bytes]]:
        """
        Fetches candidate thumbnails in parallel, dropping the unfetchable.

        Args:
            candidates (List[Dict[str, str]]): The candidate pool.

        Returns:
            List[Tuple[Dict[str, str], bytes]]: Candidates paired with thumbnail bytes.
        """
        allow = frozenset({self.searxng.host}) if self.searxng.host else frozenset()

        def grab(cand: Dict[str, str]) -> Optional[bytes]:
            if "file_id" in cand:
                try:
                    with open(self.store.path(cand["file_id"]), "rb") as fh:
                        payload = fh.read()
                except OSError:
                    return None
                if Image is not None:
                    try:
                        small = Image.open(io.BytesIO(payload)).convert("RGB")
                        small.thumbnail((256, 256))
                        out = io.BytesIO()
                        small.save(out, format="JPEG", quality=85)
                        return out.getvalue()
                    except Exception:
                        return payload
                return payload
            try:
                return self.fetcher.fetch(
                    cand["thumb_url"], THUMB_MAX_BYTES, THUMB_TIMEOUT_SECONDS,
                    allow_hosts=allow,
                )
            except Exception:
                return None

        fetched = list(self._pool.map(grab, candidates))
        return [(c, t) for c, t in zip(candidates, fetched) if t]

    @staticmethod
    def _rank_thumb(payload: bytes) -> bytes:
        """
        Downscales a thumbnail for the vision rank call.

        Args:
            payload (bytes): The fetched thumbnail.

        Returns:
            bytes: A 256px JPEG when Pillow can produce one, the original bytes otherwise.
        """
        if Image is None:
            return payload
        try:
            small = Image.open(io.BytesIO(payload)).convert("RGB")
            small.thumbnail((256, 256))
            out = io.BytesIO()
            small.save(out, format="JPEG", quality=80)
            return out.getvalue()
        except Exception:
            return payload

    def _fetch_full(self, url: str) -> Optional[bytes]:
        """
        Fetches a full-size image with no SSRF exemptions at all.

        Args:
            url (str): The original image URL.

        Returns:
            Optional[bytes]: The body, or None on any refusal or failure.
        """
        try:
            return self.fetcher.fetch(url, FULL_MAX_BYTES, FULL_TIMEOUT_SECONDS)
        except Exception:
            return None

    def _prefilter(self, query: str, thumbs: List[Tuple[Dict[str, str], bytes]],
                   keep: int = RANK_CANDIDATES) -> List[Tuple[Dict[str, str], bytes]]:
        """
        Keeps the SigLIP top candidates, or the first few when SigLIP is unavailable.

        Args:
            query (str): The search text.
            thumbs (List[Tuple[Dict[str, str], bytes]]): Candidates with thumbnails.
            keep (int): Size of the shortlist; grows with a user-requested count.

        Returns:
            List[Tuple[Dict[str, str], bytes]]: At most ``keep`` best candidates.
        """
        if self.siglip is None or len(thumbs) <= keep:
            return thumbs[:keep]
        try:
            scores = self.siglip.score(query, [t for _, t in thumbs])
        except Exception:
            return thumbs[:keep]
        ranked = sorted(zip(scores, range(len(thumbs))), key=lambda p: -p[0])
        return [thumbs[i] for _, i in ranked[:keep]]

    @staticmethod
    def _collapse(thumbs: List[Tuple[Dict[str, str], bytes]]
                  ) -> List[Tuple[Dict[str, str], bytes]]:
        """
        Merges visual near-duplicates by perceptual hash.

        Args:
            thumbs (List[Tuple[Dict[str, str], bytes]]): Candidates with thumbnails.

        Returns:
            List[Tuple[Dict[str, str], bytes]]: With near-duplicates (Hamming distance at
                most ``PHASH_HAMMING_MAX``) collapsed to their first representative. Without
                Pillow, returned unchanged.
        """
        if Image is None:
            return thumbs
        kept: List[Tuple[Dict[str, str], bytes]] = []
        hashes: List[int] = []
        for cand, thumb in thumbs:
            try:
                candidate_hash = phash64(Image.open(io.BytesIO(thumb)))
            except Exception:
                kept.append((cand, thumb))
                hashes.append(-1)
                continue
            if any(h >= 0 and hamming(h, candidate_hash) <= PHASH_HAMMING_MAX
                   for h in hashes):
                continue
            kept.append((cand, thumb))
            hashes.append(candidate_hash)
        return kept


def openapi_definition(base_url: str) -> Dict[str, Any]:
    """
    Builds the OpenAPI document Onyx's custom-tool API consumes.

    One operation only: searching. Serving the images is nginx's job, not the model's.

    Args:
        base_url (str): The sidecar's base URL as Onyx's API server reaches it.

    Returns:
        Dict[str, Any]: An OpenAPI 3 document.
    """
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Image Search",
            "version": "1.0.0",
            "description": "Searches the web for images and returns Markdown embeds.",
        },
        "servers": [{"url": base_url}],
        "paths": {
            "/search": {
                "post": {
                    "operationId": "image_search",
                    "summary": (
                        "Search the web for images and display them inline in the chat. Use "
                        "whenever the user asks for a picture, photo, or image of anything. "
                        "Returns Markdown image embeds; place them in the reply verbatim."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "queries": {
                                    "type": "array", "items": {"type": "string"},
                                    "description": "One or more image search queries.",
                                },
                                "count": {
                                    "type": "integer",
                                    "description": (
                                        "How many images the user asked for (e.g. 'give me "
                                        "10 images of X' -> 10). Omit when the user named no "
                                        "number; the default is 4, the maximum 10."
                                    ),
                                },
                            },
                            "required": ["queries"],
                        }}},
                    },
                    "responses": {"200": {
                        "description": "Markdown image embeds.",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }},
                }
            }
        },
    }


def build_service() -> ImageSearchService:
    """
    Wires the service from environment configuration.

    Returns:
        ImageSearchService: Ready to serve.
    """
    data_dir = os.environ.get(DATA_DIR_ENV, "/config/data")
    searxng = SearxngClient(os.environ.get(SEARXNG_URL_ENV, "http://dreamference-searxng:8080"))
    siglip_url = os.environ.get(SIGLIP_URL_ENV, "")
    vision_url = os.environ.get(VISION_URL_ENV, "")
    vision_model = os.environ.get(VISION_MODEL_ENV, "")
    return ImageSearchService(
        store=ImageStore(data_dir),
        searxng=searxng,
        siglip=SiglipClient(siglip_url) if siglip_url else None,
        ranker=VisionRanker(vision_url, vision_model) if vision_url and vision_model else None,
    )


def serve(port: int = SERVICE_PORT, secret: Optional[str] = None) -> None:
    """
    Runs the HTTP service until killed.

    Args:
        port (int): Port to listen on.
        secret (Optional[str]): Required value of the shared-secret header on /search; taken
            from the environment when not given. Image GETs carry no secret -- the browser is
            the caller there, and the ids are unguessable.
    """
    expected = secret or os.environ.get(SECRET_ENV, "")
    service = build_service()

    def gc_loop() -> None:
        while True:
            time.sleep(GC_INTERVAL_SECONDS)
            try:
                service.store.collect_garbage()
            except Exception:
                pass

    threading.Thread(target=gc_loop, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: Dict[str, Any]) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/health":
                self._reply(200, {"status": "ok"})
                return
            meta = re.fullmatch(r"/images/([0-9a-f]{16})\.json", parsed.path)
            if meta:
                row = service.store.lookup_id(meta.group(1))
                if not row:
                    self._reply(404, {"error": "unknown image"})
                    return
                host = urllib.parse.urlparse(row["source_url"]).hostname or ""
                if host.startswith("www."):
                    host = host[4:]
                self._reply(200, {"source_url": row["source_url"],
                                  "title": row["title"] or "", "host": host})
                return
            match = re.fullmatch(r"/images/([0-9a-f]{16})\.jpg", parsed.path)
            if match:
                path = service.store.path(match.group(1))
                try:
                    with open(path, "rb") as fh:
                        payload = fh.read()
                except OSError:
                    self._reply(404, {"error": "unknown image"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "public, max-age=604800, immutable")
                self.end_headers()
                self.wfile.write(payload)
                return
            self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - name fixed by http.server
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/search":
                self._reply(404, {"error": "not found"})
                return
            if expected and self.headers.get(AUTH_HEADER, "") != expected:
                self._reply(403, {"error": "bad token"})
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode() or "{}")
                queries = body.get("queries") or []
                if isinstance(queries, str):
                    queries = [queries]
                queries = [q for q in queries if isinstance(q, str) and q.strip()][:5]
                if not queries:
                    self._reply(400, {"error": "queries required"})
                    return
                try:
                    count = int(body.get("count") or RESULT_COUNT)
                except (TypeError, ValueError):
                    count = RESULT_COUNT
                self._reply(200, service.search(queries, count=count))
            except Exception as exc:  # a tool call must answer, not hang the assistant
                self._reply(500, {"error": str(exc)[:200]})

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
