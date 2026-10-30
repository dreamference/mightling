"""Tests of `SweBenchImages`: the cached tag list, the images listed on disk and which of them are removed."""

import json
import subprocess
import time

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_images import TAG_LIST_MAX_AGE_S, SweBenchImages

BUILT = f"{swe_bench_settings.BUILT_IMAGE_REPO}:a-1"
COMMUNITY = f"{swe_bench_settings.COMMUNITY_IMAGE_REPO}:b-2"


def write_cache(tags, age):
    path = SweBenchImages.tags_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": time.time() - age, "tags": tags}) + "\n")
    return path


def fetching(monkeypatch, result):
    calls = []

    def fetch(cls):
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(SweBenchImages, "fetch_tags", classmethod(fetch))
    return calls


def fake_docker(monkeypatch, stdout="", returncodes=None):
    argvs = []

    def run(cls, argv, **kwargs):
        argvs.append(argv)
        code = (returncodes or {}).get(argv[-1], 0)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(run))
    return argvs


def test_a_stale_list_is_returned_silently_when_the_registry_cannot_be_reached(monkeypatch, capsys):
    path = write_cache(["old-1"], TAG_LIST_MAX_AGE_S + 60)
    before = path.read_text()
    calls = fetching(monkeypatch, OSError("offline"))
    assert SweBenchImages.tags() == ["old-1"]
    assert calls == [1]
    assert capsys.readouterr().out == ""
    assert path.read_text() == before


def test_without_a_cached_list_a_failed_fetch_warns_and_returns_nothing(monkeypatch, capsys):
    fetching(monkeypatch, OSError("offline"))
    assert SweBenchImages.tags() == []
    assert "Could not list" in capsys.readouterr().out
    assert not SweBenchImages.tags_path().exists()


def test_a_fresh_list_is_returned_without_fetching(monkeypatch):
    write_cache(["cached-1"], 60)
    calls = fetching(monkeypatch, ["new-1"])
    assert SweBenchImages.tags() == ["cached-1"]
    assert calls == []


def test_a_fresh_list_is_fetched_again_and_rewritten_when_a_refresh_is_asked_for(monkeypatch):
    write_cache(["cached-1"], 60)
    calls = fetching(monkeypatch, ["z-1", "new-1"])
    assert SweBenchImages.tags(refresh=True) == ["z-1", "new-1"]
    assert calls == [1]
    assert json.loads(SweBenchImages.tags_path().read_text())["tags"] == ["new-1", "z-1"]


def test_a_stale_list_is_replaced_by_the_fetched_one(monkeypatch):
    write_cache(["old-1"], TAG_LIST_MAX_AGE_S + 60)
    fetching(monkeypatch, ["new-1"])
    assert SweBenchImages.tags() == ["new-1"]
    assert json.loads(SweBenchImages.tags_path().read_text())["tags"] == ["new-1"]


def test_a_blank_line_in_the_listed_images_is_skipped(monkeypatch):
    fake_docker(monkeypatch, stdout=f"{COMMUNITY}\t1.2GB\n\n\t3GB\n")
    assert SweBenchImages.local_images() == [
        {"image": COMMUNITY, "size": "1.2GB"},
    ]


def test_an_image_built_here_is_never_removed(monkeypatch):
    argvs = fake_docker(monkeypatch)
    assert SweBenchImages.remove([BUILT]) == 0
    assert argvs == []


def test_only_the_images_docker_removed_are_counted(monkeypatch):
    other = f"{swe_bench_settings.COMMUNITY_IMAGE_REPO}:c-3"
    argvs = fake_docker(monkeypatch, returncodes={COMMUNITY: 1})
    assert SweBenchImages.remove([COMMUNITY, BUILT, other]) == 1
    assert argvs == [["rmi", COMMUNITY], ["rmi", other]]
