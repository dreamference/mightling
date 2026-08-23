"""
Tests for the Puffin image search sidecar.

Everything here runs offline: DNS is stubbed, downloads are fakes, and the SigLIP and vision
stages are plain objects -- the funnel's collaborators are constructor-injected for exactly
this reason. The hardened downloader's SSRF assertions are the load-bearing part.
"""

import io
import json

import pytest
from PIL import Image

from dreamference.chat.image_search_service import (
    CANDIDATE_POOL_SIZE,
    FetchRejected,
    HardenedFetcher,
    ImageSearchService,
    ImageStore,
    RANK_CANDIDATES,
    SearxngClient,
    VisionRanker,
    hamming,
    openapi_definition,
    phash64,
    sniff_image_type,
)


def _resolver(address):
    def resolve(host, port):
        return [(2, 1, 6, "", (address, 0))]
    return resolve


def _jpeg(color, size=(48, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue()


def _blocky(seed, size=64):
    # Low-frequency, visually distinct content: an 8x8 grid of pseudo-random grey blocks.
    # Solid colours are useless here -- they all hash identically (their DCT is one DC term),
    # which the collapse test exploits on purpose and every other test must avoid.
    import random

    rng = random.Random(seed)
    small = Image.new("L", (8, 8))
    small.putdata([rng.randrange(256) for _ in range(64)])
    return small.resize((size, size), Image.NEAREST).convert("RGB")


def _blocky_jpeg(seed, size=64) -> bytes:
    out = io.BytesIO()
    _blocky(seed, size).save(out, format="JPEG")
    return out.getvalue()


# --- Hardened downloader -------------------------------------------------------------------


def test_a_url_resolving_to_the_docker_network_is_refused():
    for private in ("10.1.2.3", "172.18.0.5", "192.168.1.9", "127.0.0.1",
                    "169.254.1.1", "0.0.0.0"):
        with pytest.raises(FetchRejected):
            HardenedFetcher.assert_public("evil.example", resolver=_resolver(private))


def test_a_public_address_is_pinned_for_the_connection():
    assert HardenedFetcher.assert_public(
        "img.example", resolver=_resolver("93.184.216.34")) == "93.184.216.34"


def test_the_searxng_exemption_reaches_a_private_host_but_nothing_else_does():
    # The image_proxy thumbnails live on SearXNG's own container address. That single host is
    # exempt; the same address behind any other name stays refused.
    allowed = HardenedFetcher.assert_public(
        "searxng-host", allow_hosts=frozenset({"searxng-host"}),
        resolver=_resolver("172.18.0.9"))
    assert allowed == "172.18.0.9"
    with pytest.raises(FetchRejected):
        HardenedFetcher.assert_public(
            "evil.example", allow_hosts=frozenset({"searxng-host"}),
            resolver=_resolver("172.18.0.9"))


class _Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, n=-1):
        return self._body.read(n)

    def close(self):
        pass


def test_redirect_hops_are_capped_and_each_hop_is_revalidated():
    hops = []

    def opener(url, pinned, timeout):
        hops.append(url)
        return _Response(302, headers={"Location": "http://img.example/next"})

    with pytest.raises(FetchRejected, match="redirect cap"):
        HardenedFetcher.fetch(
            "http://img.example/a", 1024, 1.0,
            resolver=_resolver("93.184.216.34"), opener=opener)
    assert len(hops) == 4  # the original plus REDIRECT_CAP retries, then refusal


def test_a_body_over_the_cap_is_refused_rather_than_truncated():
    big = b"\xff\xd8\xff" + b"x" * 2048

    def opener(url, pinned, timeout):
        return _Response(200, body=big)

    with pytest.raises(FetchRejected, match="exceeds"):
        HardenedFetcher.fetch(
            "http://img.example/a", 1024, 1.0,
            resolver=_resolver("93.184.216.34"), opener=opener)


def test_a_payload_without_image_magic_bytes_is_refused():
    def opener(url, pinned, timeout):
        return _Response(200, body=b"<html>totally an image</html>")

    with pytest.raises(FetchRejected, match="not a known image"):
        HardenedFetcher.fetch(
            "http://img.example/a", 4096, 1.0,
            resolver=_resolver("93.184.216.34"), opener=opener)


def test_magic_byte_sniffing_recognises_the_web_image_formats():
    assert sniff_image_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert sniff_image_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert sniff_image_type(b"GIF89a-rest") == "image/gif"
    assert sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPrest") == "image/webp"
    assert sniff_image_type(b"MZ\x90\x00") is None


# --- Perceptual hashing --------------------------------------------------------------------


def test_perceptual_hashes_collapse_near_duplicates_and_separate_distinct_images():
    base = _blocky(1)
    near = base.resize((60, 60))  # the same picture at a slightly different size
    distinct = _blocky(2)

    h_base, h_near, h_distinct = phash64(base), phash64(near), phash64(distinct)
    assert hamming(h_base, h_near) <= 8
    assert hamming(h_base, h_distinct) > 8


# --- Store and garbage collection ----------------------------------------------------------


def test_a_known_source_url_reuses_its_cached_file(tmp_path):
    store = ImageStore(str(tmp_path))
    file_id = store.persist("http://img.example/a.jpg", _jpeg("red"), "a red square", 1)
    row = store.lookup_source("http://img.example/a.jpg")
    assert row and row["file_id"] == file_id
    assert store.lookup_source("http://img.example/other.jpg") is None


def test_a_row_whose_file_was_collected_reads_as_absent(tmp_path):
    import os

    store = ImageStore(str(tmp_path))
    file_id = store.persist("http://img.example/a.jpg", _jpeg("red"), "a", 1)
    os.remove(store.path(file_id))
    assert store.lookup_source("http://img.example/a.jpg") is None
    # And the stale row is purged, so a re-persist is clean.
    assert store.persist("http://img.example/a.jpg", _jpeg("red"), "a", 1) == file_id


def test_garbage_collection_removes_expired_images_and_trims_to_the_volume_cap(tmp_path):
    import os

    store = ImageStore(str(tmp_path))
    old = store.persist("http://img.example/old.jpg", _jpeg("red"), "old", 1)
    fresh = store.persist("http://img.example/new.jpg", _jpeg("blue"), "new", 2)
    # Age the first row far past the cutoff.
    with store._lock:
        store._db.execute("UPDATE images SET created=1 WHERE file_id=?", (old,))
        store._db.commit()
    removed = store.collect_garbage(now=1000000.0)
    assert removed == 1
    assert not os.path.exists(store.path(old))
    assert os.path.exists(store.path(fresh))

    # The size cap trims oldest-first even when nothing has expired.
    a = store.persist("http://img.example/a.jpg", _jpeg("green"), "a", 3)
    removed = store.collect_garbage(max_bytes=1)
    assert removed >= 1
    assert not os.path.exists(store.path(fresh))
    assert store.lookup_source("http://img.example/new.jpg") is None
    del a


# --- The funnel ----------------------------------------------------------------------------


class _Fetcher:
    """Records every fetch; serves thumbnails and full images from a canned table."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def fetch(self, url, max_bytes, timeout, allow_hosts=frozenset(), **kwargs):
        self.calls.append({"url": url, "allow": set(allow_hosts)})
        body = self.table.get(url)
        if body is None:
            raise FetchRejected("canned refusal")
        return body


class _Searxng:
    host = "searxng-host"

    def __init__(self, per_query):
        self.per_query = per_query

    def search_images(self, query):
        return self.per_query.get(query, [])


class _Siglip:
    def __init__(self, ordered_urls):
        self.ordered = ordered_urls

    def score(self, query, thumbnails):
        raise AssertionError("scoring is stubbed at a higher level in these tests")


def _candidate(n):
    return {
        "image_url": f"http://img.example/{n}.jpg",
        "thumb_url": f"http://searxng-host/image_proxy?url={n}",
        "title": f"image {n}",
    }


def _service(tmp_path, per_query, table, siglip=None, ranker=None, fetcher=None):
    fetcher = fetcher or _Fetcher(table)
    service = ImageSearchService(
        store=ImageStore(str(tmp_path)),
        searxng=_Searxng(per_query),
        siglip=siglip,
        ranker=ranker,
        fetcher=fetcher,
    )
    return service, fetcher


def test_results_from_several_queries_merge_round_robin_and_dedupe_by_image_url(tmp_path):
    shared = _candidate("shared")
    per_query = {
        "a": [shared, _candidate("a1")],
        "b": [shared, _candidate("b1")],
    }
    service, _ = _service(tmp_path, per_query, {})
    merged = service._gather(["a", "b"])
    urls = [c["image_url"] for c in merged]
    assert urls == [shared["image_url"],
                    "http://img.example/a1.jpg", "http://img.example/b1.jpg"]
    assert len(merged) <= CANDIDATE_POOL_SIZE


def test_a_known_source_url_skips_all_network_work(tmp_path):
    cand = _candidate("known")
    per_query = {"q": [cand]}
    service, fetcher = _service(tmp_path, per_query, {})
    service.store.persist(cand["image_url"], _jpeg("red"), "cached title", 1)

    result = service.search(["q"])
    assert "/puffin-images/" in result["response"]
    assert "cached title" in result["response"]
    assert fetcher.calls == []


def test_the_funnel_fetches_thumbnails_persists_winners_and_answers_markdown(tmp_path):
    cands = [_candidate(i) for i in range(3)]
    table = {}
    for i, cand in enumerate(cands):
        table[cand["thumb_url"]] = _blocky_jpeg(i)
        table[cand["image_url"]] = _blocky_jpeg(i, size=128)
    service, fetcher = _service(tmp_path, {"q": cands}, table)

    result = service.search(["q"])
    lines = [l for l in result["response"].splitlines() if l.startswith("![")]
    assert 1 <= len(lines) <= 4
    assert all("/puffin-images/" in l and l.endswith(".jpg)") for l in lines)
    # Thumbnail fetches carried the SearXNG exemption; full downloads carried none.
    thumb_calls = [c for c in fetcher.calls if "image_proxy" in c["url"]]
    full_calls = [c for c in fetcher.calls if "image_proxy" not in c["url"]]
    assert thumb_calls and all(c["allow"] == {"searxng-host"} for c in thumb_calls)
    assert full_calls and all(c["allow"] == set() for c in full_calls)
    # And the winners are cached: a second search does no network work at all.
    fetcher.calls.clear()
    service.search(["q"])
    assert fetcher.calls == []


def test_visually_identical_candidates_collapse_to_one(tmp_path):
    same = _jpeg("red", size=(16, 16))
    cands = [_candidate(i) for i in range(3)]
    table = {c["thumb_url"]: same for c in cands}
    table[cands[0]["image_url"]] = _jpeg("red")
    service, _ = _service(tmp_path, {"q": cands}, table)

    result = service.search(["q"])
    lines = [l for l in result["response"].splitlines() if l.startswith("![")]
    assert len(lines) == 1


def test_an_unfetchable_candidate_drops_out_instead_of_failing_the_search(tmp_path):
    good, bad = _candidate("good"), _candidate("bad")
    table = {
        good["thumb_url"]: _jpeg("blue", size=(16, 16)),
        good["image_url"]: _jpeg("blue"),
        # bad's thumbnail and image are absent -> canned refusal
    }
    service, _ = _service(tmp_path, {"q": [bad, good]}, table)
    result = service.search(["q"])
    assert "image good" in result["response"]
    assert "image bad" not in result["response"]


def test_an_empty_search_answers_gracefully(tmp_path):
    service, _ = _service(tmp_path, {"q": []}, {})
    result = service.search(["q"])
    assert "instructions" in result and "empty" in result["instructions"]


def test_a_broken_vision_ranker_degrades_to_the_prefilter_order():
    # An unreachable endpoint must yield the identity order, never an exception.
    ranker = VisionRanker("http://127.0.0.1:1", "some-model")
    assert ranker.rank("q", [b"a", b"b", b"c"]) == [0, 1, 2]


def test_searxng_relative_thumbnails_resolve_against_the_searxng_base(monkeypatch):
    client = SearxngClient("http://searxng-host:8080")
    canned = {"results": [{
        "img_src": "http://img.example/full.jpg",
        "thumbnail_src": "/image_proxy?url=x",
        "title": "t",
    }]}

    class _Resp:
        def read(self):
            return json.dumps(canned).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    results = client.search_images("q")
    assert results[0]["thumb_url"] == "http://searxng-host:8080/image_proxy?url=x"


def test_the_openapi_document_carries_the_one_search_operation():
    doc = openapi_definition("http://dreamference-image-search:8768")
    assert doc["servers"] == [{"url": "http://dreamference-image-search:8768"}]
    post = doc["paths"]["/search"]["post"]
    assert post["operationId"] == "image_search"
    schema = post["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["queries"]
    assert RANK_CANDIDATES <= CANDIDATE_POOL_SIZE


class _FilteringRanker:
    def __init__(self, verdict):
        self.verdict = verdict

    def rank(self, query, thumbnails):
        return self.verdict


def test_the_vision_verdict_filters_out_unrelated_images_rather_than_padding(tmp_path):
    # Seen live: a person search returned one correct photo and three unrelated ones, because
    # the vision pass only ordered candidates and the result count was padded from the rest.
    # A successful verdict is a filter; what it leaves out stays out.
    cands = [_candidate(i) for i in range(3)]
    table = {}
    for i, cand in enumerate(cands):
        table[cand["thumb_url"]] = _blocky_jpeg(i)
        table[cand["image_url"]] = _blocky_jpeg(i, size=128)
    service, _ = _service(tmp_path, {"q": cands}, table, ranker=_FilteringRanker([1]))

    result = service.search(["q"])
    lines = [l for l in result["response"].splitlines() if l.startswith("![")]
    assert len(lines) == 1
    assert "image 1" in lines[0]


def test_an_all_rejected_verdict_answers_empty_rather_than_forcing_images(tmp_path):
    cands = [_candidate(0)]
    table = {
        cands[0]["thumb_url"]: _blocky_jpeg(0),
        cands[0]["image_url"]: _blocky_jpeg(0, size=128),
    }
    service, _ = _service(tmp_path, {"q": cands}, table, ranker=_FilteringRanker([]))
    result = service.search(["q"])
    assert "empty" in result["instructions"]


def test_cached_images_are_judged_again_for_each_new_query(tmp_path):
    # An image cached for one query is not thereby relevant to the next. The cached candidate
    # rides through the ranking on its local file -- no network -- and a verdict that rejects
    # it keeps it out, cache or no cache.
    cand = _candidate("cached")
    per_query = {"q": [cand]}
    service, fetcher = _service(tmp_path, per_query, {}, ranker=_FilteringRanker([]))
    service.store.persist(cand["image_url"], _blocky_jpeg(5), "old winner", 1)

    result = service.search(["q"])
    assert fetcher.calls == []  # local file stood in for the thumbnail
    assert "old winner" not in result["response"]


def test_a_requested_count_is_satisfied_when_enough_images_survive(tmp_path):
    cands = [_candidate(i) for i in range(8)]
    table = {}
    for i, cand in enumerate(cands):
        table[cand["thumb_url"]] = _blocky_jpeg(i)
        table[cand["image_url"]] = _blocky_jpeg(i, size=128)
    service, _ = _service(tmp_path, {"q": cands}, table)

    result = service.search(["q"], count=6)
    lines = [l for l in result["response"].splitlines() if l.startswith("![")]
    assert len(lines) == 6


def test_a_requested_count_larger_than_the_supply_returns_what_exists(tmp_path):
    cands = [_candidate(i) for i in range(2)]
    table = {}
    for i, cand in enumerate(cands):
        table[cand["thumb_url"]] = _blocky_jpeg(i)
        table[cand["image_url"]] = _blocky_jpeg(i, size=128)
    service, _ = _service(tmp_path, {"q": cands}, table)

    result = service.search(["q"], count=10)
    lines = [l for l in result["response"].splitlines() if l.startswith("![")]
    assert len(lines) == 2
