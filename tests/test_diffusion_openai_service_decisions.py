"""What the diffusion sidecar's OpenAI service refuses, and how its sampler and runner decide.

A path it does not serve is a 404 even while the model loads; a body that is not a JSON object is
a 400 that never reaches the model; a generation that fails is a 500 naming the error; a
temperature it cannot read is greedy decoding. The sampler fills whole blocks when no
end-of-sequence comes and samples when asked to, and the runner hands the tokenizer's own mask and
end-of-sequence ids to the sampler.
"""

import http.client
import json
import types

import pytest
import requests
import torch
from test_diffusion_server import _serve, _StubRunner

from dreamference.vllm_server.diffusion_openai_service import (
    block_diffusion_sample,
)


class _Recorder(_StubRunner):
    def __init__(self, ready=True, error=None, failure=None):
        super().__init__(ready=ready, error=error)
        self.failure = failure
        self.calls = []

    def generate(self, prompt, max_new_tokens, temperature):
        self.calls.append(("generate", prompt, temperature))
        if self.failure:
            raise self.failure
        return f"gen:{prompt}"

    def chat(self, messages, max_new_tokens, temperature):
        self.calls.append(("chat", messages, temperature))
        return "chat"


@pytest.fixture
def served():
    servers = []

    def serve(runner):
        server, base = _serve(runner)
        servers.append(server)
        return base

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def test_a_path_the_service_does_not_serve_is_a_404_even_while_loading(served):
    runner = _Recorder(ready=False)
    base = served(runner)
    resp = requests.get(f"{base}/v1/engines", timeout=5)
    assert resp.status_code == 404 and "/v1/engines" in resp.json()["error"]["message"]
    resp = requests.post(f"{base}/v1/embeddings", json={"input": "x"}, timeout=5)
    assert resp.status_code == 404
    assert runner.calls == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{not json", "invalid JSON body"),
        (b"[1, 2]", "the JSON body must be an object"),
        (b'"text"', "the JSON body must be an object"),
    ],
)
def test_a_body_that_is_not_a_json_object_is_a_400_that_never_reaches_the_model(
    served, body, message
):
    runner = _Recorder()
    base = served(runner)
    resp = requests.post(f"{base}/v1/completions", data=body, timeout=5)
    assert resp.status_code == 400 and resp.json()["error"]["message"] == message
    assert runner.calls == []


def test_an_unreadable_content_length_is_a_400(served):
    runner = _Recorder()
    host, port = served(runner).rsplit("//", 1)[1].rsplit(":", 1)
    conn = http.client.HTTPConnection(host.strip("[]"), int(port), timeout=5)
    conn.putrequest("POST", "/v1/completions")
    conn.putheader("Content-Length", "many")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400 and json.loads(resp.read())["error"]["message"] == "invalid JSON body"
    conn.close()
    assert runner.calls == []


def test_a_generation_that_fails_is_a_500_naming_the_error(served):
    runner = _Recorder(failure=RuntimeError("CUDA error: out of memory"))
    base = served(runner)
    resp = requests.post(f"{base}/v1/completions", json={"prompt": "x"}, timeout=5)
    assert resp.status_code == 500
    assert resp.json()["error"]["message"] == "RuntimeError: CUDA error: out of memory"


@pytest.mark.parametrize("temperature", ["hot", None, [0.7]])
def test_a_temperature_that_cannot_be_read_is_greedy_decoding(served, temperature):
    runner = _Recorder()
    base = served(runner)
    payload = {"prompt": "x", "temperature": temperature}
    assert requests.post(f"{base}/v1/completions", json=payload, timeout=5).status_code == 200
    assert runner.calls == [("generate", "x", 0.0)]


@pytest.mark.parametrize(("prompt", "seen"), [(["first", "second"], "first"), ([], "")])
def test_a_prompt_list_is_answered_for_its_first_prompt_only(served, prompt, seen):
    runner = _Recorder()
    base = served(runner)
    resp = requests.post(f"{base}/v1/completions", json={"prompt": prompt}, timeout=5)
    assert resp.json()["choices"][0]["text"] == f"gen:{seen}"
    assert runner.calls == [("generate", seen, 0.0)]


def test_a_chat_without_messages_reaches_the_model_as_an_empty_conversation(served):
    runner = _Recorder()
    base = served(runner)
    resp = requests.post(f"{base}/v1/chat/completions", json={"temperature": 0.5}, timeout=5)
    assert resp.status_code == 200 and resp.json()["object"] == "chat.completion"
    assert runner.calls == [("chat", [], 0.5)]


def _toy(target, vocab=100, gap=1.0):
    """A model whose prediction for each generated position is `target[position - 2]`."""

    class Toy(torch.nn.Module):
        dtype = torch.float32

        def forward(self, input_ids, attention_mask):
            n = input_ids.shape[1]
            logits = torch.zeros(1, n, vocab)
            for position in range(2, n):
                logits[0, position, target[(position - 2) % len(target)]] = gap
            return types.SimpleNamespace(logits=logits)

    return Toy()


def test_with_no_end_of_sequence_every_block_is_filled_and_the_cap_still_holds():
    model = _toy([5, 6, 7, 8])
    prompt = torch.tensor([[3, 4]])
    tokens = block_diffusion_sample(
        model, prompt, mask_id=99, eos_id=None, max_new_tokens=6, block_size=4, steps=6
    )
    assert tokens == [5, 6, 7, 8, 5, 6], "two whole blocks, cut to the six asked for"
    tokens = block_diffusion_sample(
        model, prompt, mask_id=99, eos_id=9, max_new_tokens=8, block_size=4, steps=8
    )
    assert tokens == [5, 6, 7, 8, 5, 6, 7, 8], "an end-of-sequence id never produced cuts nothing"


def test_a_temperature_above_zero_samples_from_the_models_distribution():
    torch.manual_seed(0)
    tokens = block_diffusion_sample(
        _toy([5, 6, 7, 9, 1, 1, 1, 1], gap=1000.0),
        torch.tensor([[3, 4]]),
        mask_id=99,
        eos_id=9,
        max_new_tokens=8,
        block_size=4,
        steps=8,
        temperature=0.7,
    )
    assert tokens == [5, 6, 7]
