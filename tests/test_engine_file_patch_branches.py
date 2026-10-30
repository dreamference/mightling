"""Where the engine's file patches stop short of patching, and what they leave behind."""

import subprocess

import pytest

from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.vllm_server.chat_template_patcher import ChatTemplatePatcher
from dreamference.vllm_server.sglang_runtime_patcher import SGLangRuntimePatcher

IMAGE = "registry.example/sglang:1"
PATCH = {"path": "/sgl/srt/server.py", "sha256": "0" * 64, "substitutions": [["a", "b"]]}


@pytest.fixture
def docker(monkeypatch, tmp_path):
    """`_docker` answers from `codes` (command word to exit code) and records each command."""
    seen = []
    codes = {"create": 0, "cp": 0, "rm": 0}

    def run(command):
        seen.append(command)
        return subprocess.CompletedProcess(command, codes[command[1]], stdout="c0ffee\n")

    monkeypatch.setattr(
        SGLangRuntimePatcher, "_docker", classmethod(lambda cls, command: run(command))
    )
    monkeypatch.setattr(SGLangRuntimePatcher, "host_dir", classmethod(lambda cls: tmp_path))
    return seen, codes


# -- SGLangRuntimePatcher.prepare ------------------------------------------------------------------------


def test_a_patched_file_already_written_is_not_copied_out_again(docker, tmp_path):
    seen, _codes = docker
    (tmp_path / SGLangRuntimePatcher.file_name(PATCH)).write_text("patched")

    assert SGLangRuntimePatcher.prepare(IMAGE, [PATCH]) is None
    assert seen == []


def test_a_file_docker_cannot_copy_out_stops_the_launch(docker):
    _seen, codes = docker
    codes["cp"] = 1

    assert (
        SGLangRuntimePatcher.prepare(IMAGE, [PATCH])
        == f"could not copy /sgl/srt/server.py out of {IMAGE}"
    )


# -- SGLangRuntimePatcher.extract ------------------------------------------------------------------------


def test_an_image_docker_cannot_create_a_container_from_gives_nothing_and_leaves_nothing(docker):
    seen, codes = docker
    codes["create"] = 125

    assert SGLangRuntimePatcher.extract(IMAGE, "/sgl/srt/server.py") is None
    assert seen == [["docker", "create", IMAGE]]


def test_a_failed_copy_still_removes_the_container_it_made(docker):
    seen, codes = docker
    codes["cp"] = 1

    assert SGLangRuntimePatcher.extract(IMAGE, "/sgl/srt/server.py") is None
    assert seen[-1] == ["docker", "rm", "-f", "c0ffee"]


# -- ChatTemplatePatcher.prepare -------------------------------------------------------------------------


def test_a_snapshot_without_a_chat_template_cannot_be_patched(monkeypatch, tmp_path):
    monkeypatch.setattr(ModelDownloader, "get_hf_cache_dir", classmethod(lambda cls: tmp_path))
    (tmp_path / "models--org--model" / "snapshots" / "abc123").mkdir(parents=True)

    assert ChatTemplatePatcher.prepare("org/model", "abc123", [["a", "b"]]) is None
