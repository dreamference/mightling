"""What the vLLM server manager decides from its probes of the server, Docker and the disk.

A server that lists its models is not ready until a chat request answers too; a warm-up the
server refuses is not a warm-up; an image that cannot be pulled is never built in its place; and
the page-cache eviction counts only the files the kernel took the advice for.
"""

import json
import urllib.error

import requests

from dreamference.vllm_server.vllm_server_manager import VLLMServerManager


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class _ChatAnswer:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _probe(monkeypatch, models, chat):
    """Answers `GET /v1/models` with `models` and the chat request with `chat` (or raises it)."""
    sent = []
    monkeypatch.setattr(requests, "get", lambda url, timeout: models)

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        if isinstance(chat, Exception):
            raise chat
        return _ChatAnswer(chat)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return sent


def test_a_server_is_ready_only_when_the_chat_request_answers_too(monkeypatch):
    sent = _probe(monkeypatch, _Response(200, {"data": [{"id": "served-model"}]}), 200)
    assert VLLMServerManager().check_health(timeout=0.1) is True
    assert sent[0]["model"] == "served-model" and sent[0]["max_tokens"] == 1


def test_a_server_that_lists_its_models_but_refuses_a_chat_is_not_ready(monkeypatch):
    refused = urllib.error.HTTPError("http://x", 503, "loading", {}, None)
    _probe(monkeypatch, _Response(200, {"data": [{"id": "served-model"}]}), refused)
    assert VLLMServerManager().check_health(timeout=0.1) is False
    _probe(monkeypatch, _Response(200, {"data": [{"id": "served-model"}]}), 204)
    assert VLLMServerManager().check_health(timeout=0.1) is False


def test_a_server_whose_model_list_fails_is_not_asked_to_chat(monkeypatch):
    sent = _probe(monkeypatch, _Response(503), 200)
    assert VLLMServerManager().check_health(timeout=0.1) is False
    assert sent == []


def test_the_served_models_are_the_entries_with_an_id_and_none_on_a_failure(monkeypatch):
    listing = {"data": [{"id": "a"}, {"object": "model"}, {"id": "b"}]}
    monkeypatch.setattr(requests, "get", lambda url, timeout: _Response(200, listing))
    assert VLLMServerManager().get_models() == ["a", "b"]
    monkeypatch.setattr(requests, "get", lambda url, timeout: _Response(500, listing))
    assert VLLMServerManager().get_models() == []

    def unreachable(url, timeout):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", unreachable)
    assert VLLMServerManager().get_models() == []
