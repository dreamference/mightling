"""The night machines' instance image cache (SWE_BENCH spec §30): what stays, what goes, and in what order."""

import pytest

from dreamference.swe_bench import (
    SweBenchImageCache,
    SweBenchImages,
    SweBenchSettings,
    swe_bench_settings,
)

GIB = 1024**3
REPO = swe_bench_settings.COMMUNITY_IMAGE_REPO


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """A stand-in disk: images present and their sizes, free bytes that grow as images go."""
    state = {"free": 100 * GIB, "present": {}, "removed": []}

    def remove(cls, images):
        gone = [image for image in images if image in state["present"]]
        for image in gone:
            state["free"] += state["present"].pop(image)
            state["removed"].append(image)
        return len(gone)

    monkeypatch.setattr(SweBenchImages, "remove", classmethod(remove))
    monkeypatch.setattr(
        SweBenchImages,
        "local_images",
        classmethod(lambda cls: [{"image": i, "size": "3GB"} for i in state["present"]]),
    )
    monkeypatch.setattr(SweBenchImageCache, "free_bytes", classmethod(lambda cls: state["free"]))
    lists = tmp_path / "list.txt"
    lists.write_text("# a list\nacme__widget-1\nacme__widget-2\n\n")
    return state, lists


def settings(lists, floor="", reserve="100G"):
    table = {"disk_reserve": reserve, "image_cache_floor": floor}
    if lists is not None:
        table["image_cache_lists"] = [str(lists)]
    return SweBenchSettings(table)


def test_the_images_of_a_cached_list_stay_and_the_others_go(disk):
    state, lists = disk
    one, other = f"{REPO}:acme-widget-1", f"{REPO}:acme-other-9"
    state["present"] = {one: 3 * GIB, other: 3 * GIB}
    state["free"] = 500 * GIB
    removed = SweBenchImageCache.release([one, other], settings(lists))
    assert removed == [other] and one in state["present"]
    assert one in SweBenchImageCache.last_used()


def test_without_a_cached_list_every_released_image_goes_as_before(disk):
    state, _ = disk
    one = f"{REPO}:acme-widget-1"
    state["present"] = {one: 3 * GIB}
    state["free"] = 500 * GIB
    assert SweBenchImageCache.release([one], settings(None)) == [one]


def test_below_the_floor_the_least_recently_used_cached_images_go_first(disk):
    state, lists = disk
    old, new, unknown = f"{REPO}:acme-widget-1", f"{REPO}:acme-widget-2", f"{REPO}:acme-widget-3"
    state["present"] = {old: 3 * GIB, new: 3 * GIB, unknown: 3 * GIB}
    swe_bench_settings.IMAGE_CACHE_RECORD.parent.mkdir(parents=True, exist_ok=True)
    swe_bench_settings.IMAGE_CACHE_RECORD.write_text(f'{{"{old}": 100, "{new}": 200}}\n')
    state["free"] = 119 * GIB
    # The floor (the 100G reserve plus 20G headroom) needs 1 GiB: the image with no record goes first.
    removed = SweBenchImageCache.evict(settings(lists))
    assert removed == [unknown] and old in state["present"] and new in state["present"]
    state["free"] = 118 * GIB
    assert SweBenchImageCache.evict(settings(lists)) == [old]


def test_a_released_cached_image_goes_only_when_nothing_older_can(disk):
    state, lists = disk
    one = f"{REPO}:acme-widget-1"
    state["present"] = {one: 3 * GIB}
    state["free"] = 110 * GIB
    assert SweBenchImageCache.release([one], settings(lists)) == [one]


def test_the_floor_is_the_one_set_or_the_reserve_and_the_headroom(disk):
    _, lists = disk
    assert SweBenchImageCache.floor_bytes(settings(lists, floor="175G")) == 175 * GIB
    assert SweBenchImageCache.floor_bytes(settings(lists, reserve="150G")) == 170 * GIB


def test_a_night_can_ask_for_its_own_floor(disk):
    state, lists = disk
    one = f"{REPO}:acme-widget-1"
    state["present"] = {one: 3 * GIB}
    state["free"] = 160 * GIB
    assert SweBenchImageCache.evict(settings(lists), floor=165 * GIB) == [one]


def test_an_unreadable_list_keeps_nothing_and_says_so(disk, tmp_path, capsys):
    assert SweBenchImageCache.cached_images(settings(tmp_path / "missing.txt")) == set()
    assert "cannot read the task list" in capsys.readouterr().out
