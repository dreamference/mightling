"""The pinned digests of the community images: a pull whose digest differs is refused.

Every docker command is mocked; the pin file is a temporary one.
"""

import json
import pathlib
import subprocess

import pytest

from dreamference.swe_bench import swe_bench_image_pins
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_image_pins import SweBenchImagePins
from dreamference.swe_bench.swe_bench_images import SweBenchImages

IMAGE = "greynewell/swe-bench-arm64:a-b-1"
GOOD = "sha256:" + "a" * 64
BAD = "sha256:" + "b" * 64


@pytest.fixture
def pins(tmp_path, monkeypatch):
    monkeypatch.setattr(swe_bench_image_pins, "PINS_DIR", tmp_path)
    SweBenchImagePins.write({IMAGE: GOOD})
    return tmp_path


def docker(monkeypatch, digests, calls):
    """`image inspect` answers with the next digest; everything else succeeds."""
    queue = list(digests)

    def run(args, timeout=None, input_text=None):
        calls.append(args)
        if args[:2] == ["image", "inspect"]:
            if not queue or queue[0] is None:
                queue and queue.pop(0)
                return subprocess.CompletedProcess(args, 1, "", "")
            return subprocess.CompletedProcess(args, 0, f'["r@{queue.pop(0)}"] sha256:id\n', "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(lambda cls, *a, **k: run(*a, **k)))


def test_a_pulled_image_with_the_pinned_digest_is_accepted(pins, monkeypatch):
    calls = []
    docker(monkeypatch, [None, GOOD], calls)
    assert SweBenchDocker.ensure_image(IMAGE) == GOOD
    assert ["rmi", IMAGE] not in calls


def test_a_pulled_image_with_another_digest_is_refused_and_removed(pins, monkeypatch, capsys):
    calls = []
    docker(monkeypatch, [None, BAD], calls)
    assert SweBenchDocker.ensure_image(IMAGE) is None
    assert ["rmi", IMAGE] in calls
    assert "pin-images" in capsys.readouterr().out


def test_a_local_image_with_another_digest_is_refused_but_kept(pins, monkeypatch):
    calls = []
    docker(monkeypatch, [BAD], calls)
    assert SweBenchDocker.ensure_image(IMAGE) is None
    assert ["rmi", IMAGE] not in calls


def test_an_image_without_a_pin_is_accepted(pins, monkeypatch):
    calls = []
    docker(monkeypatch, [BAD], calls)
    assert SweBenchDocker.ensure_image("mightling/swe-bench-built-arm64:x") == BAD


def test_pin_images_accepts_a_changed_image_on_request(pins, monkeypatch):
    monkeypatch.setattr(
        SweBenchImages, "local_images", classmethod(lambda cls: [{"image": IMAGE, "size": "1GB"}])
    )
    docker(monkeypatch, [BAD, BAD, BAD], [])
    assert SweBenchImagePins.review([], False) == 1
    assert SweBenchImagePins.load() == {IMAGE: GOOD}
    assert SweBenchImagePins.review([IMAGE], False) == 0
    assert SweBenchImagePins.load() == {IMAGE: BAD}


def test_pin_images_pins_new_images_only_with_new(pins, monkeypatch):
    other = "greynewell/swe-bench-arm64:c-d-2"
    monkeypatch.setattr(
        SweBenchImages, "local_images", classmethod(lambda cls: [{"image": other, "size": ""}])
    )
    docker(monkeypatch, [BAD, BAD], [])
    SweBenchImagePins.review([], False)
    assert other not in SweBenchImagePins.load()
    SweBenchImagePins.review([], True)
    assert SweBenchImagePins.load()[other] == BAD


def test_the_committed_pins_are_well_formed():
    from dreamference.swe_bench.swe_bench_settings import COMMUNITY_IMAGE_REPO

    data = json.loads((swe_bench_image_pins.PINS_DIR / "arm64.json").read_text())["images"]
    assert data and all(
        k.startswith(COMMUNITY_IMAGE_REPO + ":") and v.startswith("sha256:") and len(v) == 71
        for k, v in data.items()
    )


def test_the_pins_ship_in_the_package():
    assert "image_digests/*" in pathlib.Path("setup.py").read_text()
