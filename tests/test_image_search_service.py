"""Tests for the Mightling image search sidecar.

Everything here runs offline: DNS is stubbed, downloads are fakes, and the SigLIP and vision
stages are plain objects -- the funnel's collaborators are constructor-injected for exactly
this reason. The hardened downloader's SSRF assertions are the load-bearing part.
"""

import io
import json
import pathlib

import pytest
from PIL import Image

from dreamference.chat.image_search_service import (
    CANDIDATE_POOL_SIZE,
    FetchRejected,
    HardenedFetcher,
    ImageSearchService,
    ImageStore,
    SearxngClient,
    VisionRanker,
    hamming,
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
    for private in ("10.1.2.3", "172.18.0.5", "192.168.1.9", "127.0.0.1", "169.254.1.1", "0.0.0.0"):
        with pytest.raises(FetchRejected):
            HardenedFetcher.assert_public("evil.example", resolver=_resolver(private))


def test_a_public_address_is_pinned_for_the_connection():
    assert (
        HardenedFetcher.assert_public("img.example", resolver=_resolver("93.184.216.34"))
        == "93.184.216.34"
    )


def test_the_searxng_exemption_reaches_a_private_host_but_nothing_else_does():
    # The image_proxy thumbnails live on SearXNG's own container address. That single host is
    # exempt; the same address behind any other name stays refused.
    allowed = HardenedFetcher.assert_public(
        "searxng-host", allow_hosts=frozenset({"searxng-host"}), resolver=_resolver("172.18.0.9")
    )
    assert allowed == "172.18.0.9"
    with pytest.raises(FetchRejected):
        HardenedFetcher.assert_public(
            "evil.example",
            allow_hosts=frozenset({"searxng-host"}),
            resolver=_resolver("172.18.0.9"),
        )


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
            "http://img.example/a", 1024, 1.0, resolver=_resolver("93.184.216.34"), opener=opener
        )
    assert len(hops) == 4  # the original plus REDIRECT_CAP retries, then refusal


def test_a_body_over_the_cap_is_refused_rather_than_truncated():
    big = b"\xff\xd8\xff" + b"x" * 2048

    def opener(url, pinned, timeout):
        return _Response(200, body=big)

    with pytest.raises(FetchRejected, match="exceeds"):
        HardenedFetcher.fetch(
            "http://img.example/a", 1024, 1.0, resolver=_resolver("93.184.216.34"), opener=opener
        )


def test_a_payload_without_image_magic_bytes_is_refused():
    def opener(url, pinned, timeout):
        return _Response(200, body=b"<html>totally an image</html>")

    with pytest.raises(FetchRejected, match="not a known image"):
        HardenedFetcher.fetch(
            "http://img.example/a", 4096, 1.0, resolver=_resolver("93.184.216.34"), opener=opener
        )


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

    store = ImageStore(str(tmp_path))
    file_id = store.persist("http://img.example/a.jpg", _jpeg("red"), "a", 1)
    pathlib.Path(store.path(file_id)).unlink()
    assert store.lookup_source("http://img.example/a.jpg") is None
    # And the stale row is purged, so a re-persist is clean.
    assert store.persist("http://img.example/a.jpg", _jpeg("red"), "a", 1) == file_id


def test_garbage_collection_removes_expired_images_and_trims_to_the_volume_cap(tmp_path):

    store = ImageStore(str(tmp_path))
    old = store.persist("http://img.example/old.jpg", _jpeg("red"), "old", 1)
    fresh = store.persist("http://img.example/new.jpg", _jpeg("blue"), "new", 2)
    # Age the first row far past the cutoff.
    with store._lock:
        store._db.execute("UPDATE images SET created=1 WHERE file_id=?", (old,))
        store._db.commit()
    removed = store.collect_garbage(now=1000000.0)
    assert removed == 1
    assert not pathlib.Path(store.path(old)).exists()
    assert pathlib.Path(store.path(fresh)).exists()

    # The size cap trims oldest-first even when nothing has expired.
    a = store.persist("http://img.example/a.jpg", _jpeg("green"), "a", 3)
    removed = store.collect_garbage(max_bytes=1)
    assert removed >= 1
    assert not pathlib.Path(store.path(fresh)).exists()
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
    assert urls == [shared["image_url"], "http://img.example/a1.jpg", "http://img.example/b1.jpg"]
    assert len(merged) <= CANDIDATE_POOL_SIZE


def test_a_known_source_url_skips_all_network_work(tmp_path):
    cand = _candidate("known")
    per_query = {"q": [cand]}
    service, fetcher = _service(tmp_path, per_query, {})
    service.store.persist(cand["image_url"], _jpeg("red"), "cached title", 1)

    result = service.search(["q"])
    assert "](/images/" in result["response"]
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
    assert all("](/images/" in l and l.endswith(".jpg)") for l in lines)
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
    canned = {
        "results": [
            {
                "img_src": "http://img.example/full.jpg",
                "thumbnail_src": "/image_proxy?url=x",
                "title": "t",
            }
        ]
    }

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


def test_metadata_lookup_names_the_source_for_the_gallery_badges(tmp_path):
    store = ImageStore(str(tmp_path))
    file_id = store.persist("http://www.museum.example/a.jpg", _jpeg("red"), "exhibit", 1)
    row = store.lookup_id(file_id)
    assert row == {"source_url": "http://www.museum.example/a.jpg", "title": "exhibit"}
    assert store.lookup_id("0" * 16) is None


def test_rank_thumbnails_are_downscaled_before_the_vision_call(tmp_path):
    # Full-size thumbnails made every rank call a multi-thousand-token multimodal prefill on
    # the shared engine; the verdict is no better for the extra pixels.
    sizes = {}

    class _MeasuringRanker:
        def rank(self, query, thumbnails):
            sizes["bytes"] = [len(t) for t in thumbnails]
            return list(range(len(thumbnails)))

    cand = _candidate(0)
    big = _blocky_jpeg(0, size=1024)
    table = {cand["thumb_url"]: big, cand["image_url"]: big}
    service, _ = _service(tmp_path, {"q": [cand]}, table, ranker=_MeasuringRanker())
    service.search(["q"])
    assert sizes["bytes"] and all(b < len(big) / 4 for b in sizes["bytes"])


# --- Downloader refusals -------------------------------------------------------------------


def _failing_resolver(host, port):
    raise OSError("name not known")


def test_a_dns_failure_is_a_refusal_not_a_crash():
    with pytest.raises(FetchRejected, match="DNS failure"):
        HardenedFetcher.assert_public("nowhere.example", resolver=_failing_resolver)


def test_a_dns_failure_for_an_exempt_host_is_also_a_refusal():
    with pytest.raises(FetchRejected, match="DNS failure"):
        HardenedFetcher.assert_public(
            "searx", allow_hosts=frozenset({"searx"}), resolver=_failing_resolver
        )


def test_a_host_that_resolves_to_nothing_is_refused():
    with pytest.raises(FetchRejected, match="resolved to nothing"):
        HardenedFetcher.assert_public("empty.example", resolver=lambda h, p: [])


def test_an_answer_that_is_not_an_address_is_refused():
    with pytest.raises(FetchRejected, match="non-address"):
        HardenedFetcher.assert_public("odd.example", resolver=_resolver("not-an-ip"))


def test_one_private_address_among_public_ones_refuses_the_host():
    def mixed(host, port):
        return [(2, 1, 6, "", ("8.8.8.8", 0)), (2, 1, 6, "", ("10.0.0.5", 0))]

    with pytest.raises(FetchRejected, match="non-public"):
        HardenedFetcher.assert_public("rebind.example", resolver=mixed)


def test_an_ipv4_mapped_private_address_is_refused():
    with pytest.raises(FetchRejected):
        HardenedFetcher.assert_public("mapped.example", resolver=_resolver("::ffff:10.0.0.5"))


def test_a_non_http_scheme_is_refused_before_any_lookup():
    def resolver(host, port):
        raise AssertionError("must not resolve")

    for url in ("file:///etc/passwd", "ftp://img.example/a.jpg", "gopher://img.example/"):
        with pytest.raises(FetchRejected, match="scheme"):
            HardenedFetcher.fetch(url, 1024, 1.0, resolver=resolver)


def test_a_url_without_a_host_is_refused():
    with pytest.raises(FetchRejected, match="no host"):
        HardenedFetcher.fetch("http:///a.jpg", 1024, 1.0, resolver=_resolver("8.8.8.8"))


def test_a_redirect_without_a_location_is_refused():
    with pytest.raises(FetchRejected, match="Location"):
        HardenedFetcher.fetch(
            "http://img.example/a",
            1024,
            1.0,
            resolver=_resolver("8.8.8.8"),
            opener=lambda u, p, t: _Response(302),
        )


def test_an_error_status_is_refused():
    with pytest.raises(FetchRejected, match="HTTP 404"):
        HardenedFetcher.fetch(
            "http://img.example/a",
            1024,
            1.0,
            resolver=_resolver("8.8.8.8"),
            opener=lambda u, p, t: _Response(404),
        )


def test_a_relative_redirect_is_followed_on_the_same_host():
    urls = []

    def opener(url, pinned, timeout):
        urls.append(url)
        if len(urls) == 1:
            return _Response(301, headers={"Location": "/real.jpg"})
        return _Response(200, body=b"\xff\xd8\xffdata")

    body = HardenedFetcher.fetch(
        "http://img.example/a", 1024, 1.0, resolver=_resolver("8.8.8.8"), opener=opener
    )
    assert urls == ["http://img.example/a", "http://img.example/real.jpg"]
    assert body.startswith(b"\xff\xd8\xff")


def test_a_redirect_to_a_private_host_is_refused_on_the_second_hop():
    answers = {"img.example": "8.8.8.8", "internal.example": "10.1.2.3"}

    def resolver(host, port):
        return [(2, 1, 6, "", (answers[host], 0))]

    def opener(url, pinned, timeout):
        return _Response(302, headers={"Location": "http://internal.example/x"})

    with pytest.raises(FetchRejected, match="non-public"):
        HardenedFetcher.fetch("http://img.example/a", 1024, 1.0, resolver=resolver, opener=opener)


def test_the_default_opener_connects_to_the_pinned_address_and_names_the_host(monkeypatch):
    import http.client

    seen = {}

    class _Conn:
        def __init__(self, host, port, timeout=None):
            seen["connect"] = (host, port)

        def request(self, method, path, headers=None):
            seen["request"] = (method, path, headers)

        def getresponse(self):
            return "response"

    monkeypatch.setattr(http.client, "HTTPConnection", _Conn)
    out = HardenedFetcher._open("http://img.example:8080/p/a.jpg?x=1", "8.8.8.8", 3.0)
    assert out == "response"
    assert seen["connect"] == ("8.8.8.8", 8080)
    assert seen["request"][1] == "/p/a.jpg?x=1"
    assert seen["request"][2]["Host"] == "img.example"


# --- Store edge cases ----------------------------------------------------------------------


def test_a_payload_pillow_cannot_decode_is_stored_as_given(tmp_path):
    store = ImageStore(str(tmp_path))
    file_id = store.persist("http://img.example/odd", b"\xff\xd8\xffnot really", "odd", None)
    with pathlib.Path(store.path(file_id)).open("rb") as fh:
        assert fh.read() == b"\xff\xd8\xffnot really"


def test_garbage_collection_survives_a_row_whose_file_is_already_gone(tmp_path):

    store = ImageStore(str(tmp_path))
    file_id = store.persist("http://img.example/a", _jpeg("red"), "a", None)
    pathlib.Path(store.path(file_id)).unlink()
    # Under the cap with a missing file, and past the age cap with a missing file.
    assert store.collect_garbage(max_bytes=1) == 0
    assert store.collect_garbage(now=10**12) == 1
    assert store.lookup_id(file_id) is None


# --- Clients for the other containers ------------------------------------------------------


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return (
            self._payload
            if isinstance(self._payload, bytes)
            else json.dumps(self._payload).encode()
        )

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_search_results_without_an_http_image_url_are_skipped(monkeypatch):
    canned = {
        "results": [
            {"img_src": "data:image/png;base64,AAAA", "title": "inline"},
            {"img_src": "", "title": "empty"},
            {"title": "missing"},
            {"img_src": "https://img.example/ok.jpg", "title": "  ok  "},
        ]
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(canned))
    results = SearxngClient("http://searx:8080/").search_images("q")
    assert [r["image_url"] for r in results] == ["https://img.example/ok.jpg"]
    assert results[0]["title"] == "ok"
    assert results[0]["thumb_url"] == "https://img.example/ok.jpg"


def test_a_searxng_answer_that_is_not_json_raises(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(b"<html>"))
    with pytest.raises(ValueError):
        SearxngClient("http://searx:8080").search_images("q")


def test_siglip_scores_follow_cosine_similarity_in_input_order(monkeypatch):
    from dreamference.chat.image_search_service import SiglipClient

    sent = []

    def urlopen(request, timeout=None):
        body = json.loads(request.data)
        sent.append(body)
        if body["modality"] == "text":
            return _Resp({"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
        # Returned out of order: the client must sort by index.
        return _Resp(
            {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    scores = SiglipClient("http://siglip:9100/", model="m").score("cat", [b"a", b"b"])
    assert scores == pytest.approx([1.0, 0.0])
    assert sent[0]["model"] == "m"
    assert sent[1]["input"][0].startswith("data:image/jpeg;base64,")


def test_a_zero_vector_scores_zero_instead_of_dividing_by_zero(monkeypatch):
    from dreamference.chat.image_search_service import SiglipClient

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: _Resp({"data": [{"index": 0, "embedding": [0.0, 0.0]}]}),
    )
    assert SiglipClient("http://siglip:9100").score("q", [b"a"]) == [0.0]


def _chat_answer(text):
    return _Resp({"choices": [{"message": {"content": text}}]})


def test_the_vision_verdict_is_parsed_from_chatty_output(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: _chat_answer("Sure! The best are [2, 0] for you.")
    )
    assert VisionRanker("http://v/v1", "m").rank("q", [b"a", b"b", b"c"]) == [2, 0]


def test_out_of_range_and_repeated_indices_in_a_verdict_are_dropped(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _chat_answer("[1, 1, 7, 0]"))
    assert VisionRanker("http://v/v1", "m").rank("q", [b"a", b"b"]) == [1, 0]


def test_an_empty_verdict_rejects_everything(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _chat_answer("[]"))
    assert VisionRanker("http://v/v1", "m").rank("q", [b"a", b"b"]) == []


def test_an_answer_with_no_array_falls_back_to_the_given_order(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _chat_answer("none match"))
    assert VisionRanker("http://v/v1", "m").rank("q", [b"a", b"b"]) == [0, 1]


def test_a_malformed_chat_reply_falls_back_to_the_given_order(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp({"oops": 1}))
    assert VisionRanker("http://v/v1", "m").rank("q", [b"a", b"b"]) == [0, 1]


# --- Funnel stages -------------------------------------------------------------------------


def test_a_failing_search_query_does_not_stop_the_other_queries(tmp_path):
    class _Flaky:
        host = "searxng-host"

        def search_images(self, query):
            if query == "bad":
                raise OSError("down")
            return [_candidate(1)]

    service, _ = _service(tmp_path, {}, {})
    service.searxng = _Flaky()
    assert [c["image_url"] for c in service._gather(["bad", "good"])] == [
        "http://img.example/1.jpg"
    ]


def test_the_candidate_pool_is_capped(tmp_path):
    service, _ = _service(
        tmp_path, {"q": [_candidate(n) for n in range(CANDIDATE_POOL_SIZE + 10)]}, {}
    )
    assert len(service._gather(["q"])) == CANDIDATE_POOL_SIZE


def test_siglip_keeps_only_the_best_scoring_candidates(tmp_path):
    class _Scores:
        def score(self, query, thumbnails):
            return [0.1, 0.9, 0.5, 0.7]

    service, _ = _service(tmp_path, {}, {}, siglip=_Scores())
    thumbs = [({"n": n}, b"t") for n in range(4)]
    kept = service._prefilter("q", thumbs, keep=2)
    assert [c["n"] for c, _ in kept] == [1, 3]


def test_a_broken_siglip_falls_back_to_the_first_candidates(tmp_path):
    class _Broken:
        def score(self, query, thumbnails):
            raise OSError("down")

    service, _ = _service(tmp_path, {}, {}, siglip=_Broken())
    thumbs = [({"n": n}, b"t") for n in range(4)]
    assert [c["n"] for c, _ in service._prefilter("q", thumbs, keep=2)] == [0, 1]


def test_a_cached_thumbnail_that_cannot_be_read_drops_out(tmp_path):
    service, _ = _service(tmp_path, {}, {})
    cand = {"file_id": "0123456789abcdef", "thumb_url": "x", "image_url": "y", "title": "t"}
    assert service._fetch_thumbnails([cand]) == []


def test_a_cached_file_pillow_cannot_decode_is_used_as_it_is(tmp_path):
    service, _ = _service(tmp_path, {}, {})
    with pathlib.Path(service.store.path("0123456789abcdef")).open("wb") as fh:
        fh.write(b"raw bytes")
    cand = {"file_id": "0123456789abcdef", "thumb_url": "x", "image_url": "y", "title": "t"}
    assert service._fetch_thumbnails([cand])[0][1] == b"raw bytes"


def test_an_undecodable_rank_thumbnail_is_passed_through_unchanged():
    assert ImageSearchService._rank_thumb(b"junk") == b"junk"


def test_an_undecodable_thumbnail_is_kept_by_the_duplicate_collapse():
    thumbs = [({"n": 1}, b"junk"), ({"n": 2}, b"junk")]
    assert len(ImageSearchService._collapse(thumbs)) == 2


def test_a_full_download_refusal_drops_the_winner_instead_of_failing(tmp_path):
    cand = _candidate(1)
    table = {cand["thumb_url"]: _jpeg("red")}  # the full image is not in the table
    service, _ = _service(tmp_path, {"q": [cand]}, table)
    assert service._run_funnel("q", [cand], 1) == []


# --- Wiring and the HTTP front -------------------------------------------------------------


def test_the_service_is_wired_from_the_environment(monkeypatch, tmp_path):
    from dreamference.chat import image_search_service as mod

    monkeypatch.setenv(mod.DATA_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(mod.SEARXNG_URL_ENV, "http://searx.example:8080/")
    monkeypatch.setenv(mod.SIGLIP_URL_ENV, "")
    monkeypatch.setenv(mod.VISION_URL_ENV, "http://vision.example/v1")
    monkeypatch.setenv(mod.VISION_MODEL_ENV, "")
    service = mod.build_service()
    assert service.searxng.base_url == "http://searx.example:8080"
    assert service.siglip is None
    assert service.ranker is None  # a URL without a model name is not enough


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Runs `serve` on a loopback port chosen by the OS, with the real handler class."""
    import threading
    from http.server import ThreadingHTTPServer

    from dreamference.chat import image_search_service as mod

    holder = []

    class _Loopback(ThreadingHTTPServer):
        def __init__(self, address, handler):
            super().__init__(("127.0.0.1", 0), handler)
            holder.append(self)

        def serve_forever(self, poll_interval=0.02):
            # `serve` calls it with the default 0.5 s, which every teardown's shutdown waited out.
            super().serve_forever(poll_interval)

    searched = []

    class _Stub:
        def __init__(self):
            self.store = ImageStore(str(tmp_path))

        def search(self, queries, count=4):
            searched.append((queries, count))
            if queries == ["boom"]:
                raise RuntimeError("engine fell over")
            return {"markdown": "ok"}

    stub = _Stub()
    monkeypatch.setattr(mod, "build_service", lambda: stub)
    monkeypatch.setattr(mod, "ThreadingHTTPServer", _Loopback)
    monkeypatch.setattr(mod.time, "sleep", lambda s: threading.Event().wait(60))
    thread = threading.Thread(target=mod.serve, kwargs={"secret": "s3cret"}, daemon=True)
    thread.start()
    for _ in range(200):
        if holder:
            break
        threading.Event().wait(0.01)
    server = holder[0]
    yield server.server_address[1], stub, searched
    server.shutdown()
    server.server_close()


def _http(port, method, path, body=None, headers=None):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_the_service_answers_health_and_unknown_paths(served):
    port, _, _ = served
    assert _http(port, "GET", "/health")[0] == 200
    assert _http(port, "GET", "/nope")[0] == 404
    assert _http(port, "POST", "/other", body=b"{}")[0] == 404


def test_an_image_request_serves_only_well_formed_ids(served):
    port, stub, _ = served
    file_id = stub.store.persist("http://img.example/a", _jpeg("red"), "a", None)
    status, data = _http(port, "GET", f"/images/{file_id}.jpg")
    assert status == 200 and data[:2] == b"\xff\xd8"
    assert _http(port, "GET", "/images/0123456789abcdef.jpg")[0] == 404
    assert _http(port, "GET", "/images/..%2f..%2fdb.sqlite.jpg")[0] == 404
    assert _http(port, "GET", "/images/ABCDEF0123456789.jpg")[0] == 404


def test_image_metadata_names_the_source_host_without_www(served):
    port, stub, _ = served
    file_id = stub.store.persist("https://www.photos.example/a.jpg", _jpeg("red"), "A cat", None)
    status, data = _http(port, "GET", f"/images/{file_id}.json")
    assert status == 200
    assert json.loads(data)["host"] == "photos.example"
    assert _http(port, "GET", "/images/0123456789abcdef.json")[0] == 404


def test_a_search_without_the_shared_secret_is_refused(served):
    port, _, searched = served
    body = json.dumps({"queries": ["cat"]}).encode()
    assert (
        _http(port, "POST", "/search", body=body, headers={"Content-Length": str(len(body))})[0]
        == 403
    )
    assert (
        _http(
            port,
            "POST",
            "/search",
            body=body,
            headers={"X-Mightling-Image-Token": "wrong", "Content-Length": str(len(body))},
        )[0]
        == 403
    )
    assert searched == []


def test_a_search_with_the_secret_runs_with_a_clean_query_list(served):
    port, _, searched = served
    body = json.dumps({"queries": ["cat", "", 7, "dog"] + ["x"] * 10, "count": "abc"}).encode()
    status, data = _http(
        port,
        "POST",
        "/search",
        body=body,
        headers={"X-Mightling-Image-Token": "s3cret", "Content-Length": str(len(body))},
    )
    assert status == 200 and json.loads(data) == {"markdown": "ok"}
    queries, count = searched[0]
    assert queries[:2] == ["cat", "dog"] and len(queries) == 5  # capped at five
    assert count == 4  # an unparsable count falls back to the default


@pytest.mark.parametrize("length", ["abc", "-1", str(2 * 1024 * 1024)])
def test_a_search_with_a_content_length_that_cannot_be_read_is_a_bad_request(served, length):
    # A negative or huge length used to block the handler until the client hung up, and one that
    # is not a number dropped the connection with no answer.
    port, _, searched = served
    status, data = _http(
        port,
        "POST",
        "/search",
        body=b'{"queries": ["cat"]}',
        headers={"X-Mightling-Image-Token": "s3cret", "Content-Length": length},
    )
    assert status == 400 and "Content-Length" in json.loads(data)["error"]
    assert searched == []


def test_a_search_with_no_queries_is_a_bad_request(served):
    port, _, _ = served
    body = json.dumps({"queries": ["  "]}).encode()
    assert (
        _http(
            port,
            "POST",
            "/search",
            body=body,
            headers={"X-Mightling-Image-Token": "s3cret", "Content-Length": str(len(body))},
        )[0]
        == 400
    )


def test_a_search_failure_answers_500_instead_of_hanging(served):
    port, _, _ = served
    body = json.dumps({"queries": "boom"}).encode()
    status, data = _http(
        port,
        "POST",
        "/search",
        body=body,
        headers={"X-Mightling-Image-Token": "s3cret", "Content-Length": str(len(body))},
    )
    assert status == 500 and "engine fell over" in json.loads(data)["error"]
    bad = b"{not json"
    assert (
        _http(
            port,
            "POST",
            "/search",
            body=bad,
            headers={"X-Mightling-Image-Token": "s3cret", "Content-Length": str(len(bad))},
        )[0]
        == 500
    )
