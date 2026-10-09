"""The tool-call check of `ling-admin server start` (specs/DREAMFERENCE_MODELS.md §2.1).

No test reaches a server: every request goes to a recorded stream handed in as `post`.
"""

import inspect
import json

import requests

from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
from dreamference.vllm_server.tool_call_canary import TOOL_NAME, ToolCallCanary

SERVED = "RadixArk/Qwen3.8-27B-NVFP4"


class Response:
    def __init__(self, events=(), status=200, text="", lines=None):
        self.status_code = status
        self.text = text
        self.lines = lines if lines is not None else [
            f"event: {event['type']}\ndata: {json.dumps(event)}" for event in events]
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for chunk in self.lines:
            yield from chunk.split("\n")
            yield ""

    def close(self):
        self.closed = True


def call_item(name=TOOL_NAME, arguments='{"city": "Paris"}', call_id="call_1"):
    return {"type": "function_call", "name": name, "arguments": arguments, "call_id": call_id, "id": "fc_1"}


def message_item(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def stream(*items, reasoning=""):
    events = [{"type": "response.created", "response": {"output": []}}]
    if reasoning:
        events.append({"type": "response.reasoning_text.delta", "delta": reasoning})
    events += [{"type": "response.output_item.done", "item": item} for item in items]
    events.append({"type": "response.completed", "response": {"output": list(items)}})
    return events


def check(response, sent=None, **kwargs):
    def post(url, **request):
        if sent is not None:
            sent.append((url, request))
        if isinstance(response, Exception):
            raise response
        return response
    return ToolCallCanary.run("http://127.0.0.1:8000/", SERVED, DEFAULT_MODEL_ALIAS, post=post, **kwargs)


def test_the_request_is_shaped_like_lings_own():
    sent = []
    check(Response(stream(call_item())), sent)
    ((url, request),) = sent
    assert url == "http://127.0.0.1:8000/v1/responses" and request["stream"] is True
    body = request["json"]
    assert body["model"] == SERVED and body["stream"] is True and body["tool_choice"] == "auto"
    assert body["reasoning"]["effort"] == "none" and body["store"] is False
    (tool,) = body["tools"]
    # The flat Responses shape ling sends, not chat completions' {"function": {...}}.
    assert tool["type"] == "function" and tool["name"] == TOOL_NAME and "function" not in tool
    assert tool["parameters"]["required"] == ["city"]
    connect, read = request["timeout"]
    assert connect <= 5 and read <= 60


def test_a_well_formed_call_passes():
    response = Response(stream(call_item()))
    passed, line = check(response)
    assert passed and line.startswith('get_weather {"city": "Paris"}') and response.closed


def test_a_call_written_as_text_names_the_tool_call_parser():
    passed, line = check(Response(stream(message_item(
        '<tool_call>\n<function=get_weather>\n<parameter=city>\nParis\n</parameter>\n</function>\n</tool_call>'))))
    assert not passed and "--tool-call-parser qwen3_coder" in line and "as text" in line


def test_a_call_inside_the_reasoning_names_the_reasoning_parser():
    passed, line = check(Response(stream(message_item("Let me check."),
                                         reasoning="<tool_call>\n<function=get_weather>")))
    assert not passed and "--reasoning-parser qwen3" in line


def test_prose_without_a_call_names_the_parser_and_the_patched_template():
    passed, line = check(Response(stream(message_item("It is probably sunny in Paris."))))
    assert not passed and "--tool-call-parser qwen3_coder" in line
    assert f"{DEFAULT_MODEL_ALIAS}'s chat_template_patches" in line


def test_malformed_calls_fail():
    for item, words in ((call_item(arguments="city=Paris"), "not JSON"),
                        (call_item(arguments='{"town": "Paris"}'), "lack 'city'"),
                        (call_item(name="weather"), "misread the name"),
                        (call_item(call_id=""), "no call_id")):
        passed, line = check(Response(stream(item)))
        assert not passed and words in line, line


def test_a_refusal_names_the_template():
    passed, line = check(Response(status=400, text="Invalid reasoning effort"))
    assert not passed and "HTTP 400" in line and "chat template" in line and "chat_template_patches" in line
    passed, line = check(Response(status=404))
    assert not passed and "no /v1/responses" in line


def test_the_time_limit_is_strict_and_nothing_raises():
    ticks = iter(range(0, 1000, 30))
    endless = Response(lines=['data: {"type": "response.output_text.delta", "delta": "."}'] * 100)
    passed, line = check(endless, clock=lambda: next(ticks), timeout=60)
    assert not passed and "within 60 s" in line
    passed, line = check(requests.ConnectionError("refused"))
    assert not passed and "ConnectionError" in line
    passed, line = check(Response(lines=['data: {"type": "error", "message": "out of memory"}']))
    assert not passed and "out of memory" in line


class Manager:
    host = "http://127.0.0.1:8000"

    def __init__(self, models):
        self.models = models

    def get_models(self):
        return self.models


def test_server_start_warns_and_carries_on(monkeypatch, capsys):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
    asked = []

    def run(cls, host, model_id, model_key=None, **_):
        asked.append((host, model_id, model_key))
        return False, "the model wrote the call as text"

    monkeypatch.setattr(ToolCallCanary, "run", classmethod(run))
    assert DreamferenceCLIController._run_tool_call_canary(Manager([SERVED]), "some-other-alias") is False
    # The served id is the server's, looked up in the registry; never the requested alias.
    assert asked == [("http://127.0.0.1:8000", SERVED, DEFAULT_MODEL_ALIAS)]
    out = capsys.readouterr().out
    assert "⚠️  Tool-call check failed: the model wrote the call as text." in out
    assert DreamferenceCLIController._run_tool_call_canary(Manager([]), None) is None


def test_server_start_runs_it_for_every_model_after_the_warm_up():
    from dreamference.cli import dreamference_cli_controller as controller
    source = inspect.getsource(controller)
    check_at = source.index("cls._run_tool_call_canary(vllm_mgr, args.model)")
    assert source.index("vllm_mgr.warm_up()") < source.index('if "nvfp4" in args.model.lower():') < check_at
    line = source[source.rindex("\n", 0, check_at) + 1:check_at]
    nvfp4 = source[source.rindex("\n", 0, source.index('if "nvfp4" in args.model.lower():')) + 1:]
    assert len(line) == len(nvfp4) - len(nvfp4.lstrip())  # same depth as the NVFP4 test, not inside it
