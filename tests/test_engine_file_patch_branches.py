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
