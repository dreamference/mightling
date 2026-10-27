"""SGLang v0.5.21's streamed `/v1/responses` held a message's text, and the recipe's patch that streams it.

With tools in the request, the stream wrapper held every event of a message until a tool call opened
or the response ended, to tag the message `commentary` or `final_answer`, so no text reached the
client before the turn ended (the messenger live test, 2026-10-27). The patch passes the text through
and holds only the message's closing events, for the one event after them. No network and no docker:
the wrapper is a verbatim excerpt in tests/fixtures/sglang/, run over a scripted inner stream.
"""

import ast
import asyncio
import json
import pathlib
import types
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Optional

from dreamference.hardware.model_matrix_registry import (
    QWEN38_SGLANG_IMAGE,
    SGLANG_PHASE_MERGE_PATCH,
    SGLANG_SERVING_RESPONSES,
    SGLANG_SERVING_RESPONSES_SHA256,
    SGLANG_STREAM_PHASE_PATCH,
)
from dreamference.vllm_server.chat_template_patcher import ChatTemplatePatcher

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sglang"
EXCERPT = (FIXTURES / "serving_responses_v0.5.21_stream_excerpt.py.txt").read_text(encoding="utf-8")
MERGE_EXCERPT = (FIXTURES / "serving_responses_v0.5.21_excerpt.py.txt").read_text(encoding="utf-8")
TOOLS = [{"type": "function", "name": "shell"}]


def patched(text: str) -> str:
    """The text with the stream patch's substitutions applied, every anchor exactly once."""
    out, failed = ChatTemplatePatcher.apply(
        text, [tuple(pair) for pair in SGLANG_STREAM_PHASE_PATCH["substitutions"]]
    )
    assert failed == []
    return out


def wrapper(source: str) -> Any:
    """The excerpt's class, executed with stand-ins for what the module imports."""
    namespace: dict[str, Any] = {
        "Any": Any,
        "Optional": Optional,
        "AsyncIterator": AsyncIterator,
        "AsyncGenerator": AsyncGenerator,
        "ResponsesRequest": object,
        "RequestResponseMetadata": object,
        "orjson": types.SimpleNamespace(
            loads=json.loads, dumps=lambda value: json.dumps(value).encode()
        ),
    }
    exec(compile(ast.parse(source), "serving_responses_stream_excerpt", "exec"), namespace)
    return namespace["OpenAIServingResponses"]


def sse(payload: dict[str, Any]) -> str:
    """One event as the inner generator writes it."""
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n"


def message(event_type: str, phase: str = "final_answer") -> dict[str, Any]:
    """A message item event as the inner generator yields it: tagged final_answer when it opens."""
    return {"type": event_type, "item": {"type": "message", "id": "msg_1", "phase": phase}}


def text_turn(then: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The inner stream of a turn that writes three pieces of text, then `then`."""
    return [
        {"type": "response.created"},
        {"type": "response.in_progress"},
        message("response.output_item.added"),
        {"type": "response.content_part.added", "item_id": "msg_1"},
        {"type": "response.output_text.delta", "delta": "Let me "},
        {"type": "response.output_text.delta", "delta": "look at "},
        {"type": "response.output_text.delta", "delta": "the file."},
        {"type": "response.output_text.done", "text": "Let me look at the file."},
        {"type": "response.content_part.done", "item_id": "msg_1"},
        message("response.output_item.done"),
        *then,
    ]


CALL = [
    {"type": "response.output_item.added", "item": {"type": "function_call", "id": "fc_1"}},
    {"type": "response.function_call_arguments.delta", "delta": "{}"},
    {"type": "response.function_call_arguments.done", "arguments": "{}"},
    {"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc_1"}},
]
COMPLETED = [{"type": "response.completed"}]


def run(
    source: str, script: list[dict[str, Any]], tools: list[dict[str, Any]] = TOOLS
) -> list[tuple[int, dict[str, Any]]]:
    """Every event the wrapper sends, with how many inner events had been produced when it was sent."""
    serving = wrapper(source)
    instance = serving.__new__(serving)
    produced = 0

    async def inner(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        nonlocal produced
        for payload in script:
            produced += 1
            yield sse(payload)

    instance._responses_stream_generator_non_harmony = inner
    instance._response_tools_to_chat_tools = lambda request: list(request.tools)
    request = types.SimpleNamespace(tools=tools, effective_tool_choice=lambda: "auto")

    async def collect() -> list[tuple[int, dict[str, Any]]]:
        sent = []
        async for event in instance.responses_stream_generator_non_harmony(
            request, None, None, "m", None, None, require_reasoning=False
        ):
            sent.append((produced, json.loads(event.split("data: ", 1)[1])))
        return sent

    return asyncio.run(collect())


def delta_lags(sent: list[tuple[int, dict[str, Any]]], script: list[dict[str, Any]]) -> list[int]:
    """For each text delta, how many inner events had been produced past it when it was sent."""
    positions = [i + 1 for i, p in enumerate(script) if p["type"] == "response.output_text.delta"]
    sent_at = [n for n, p in sent if p["type"] == "response.output_text.delta"]
    return [at - pos for at, pos in zip(sent_at, positions, strict=True)]


def done_phase(sent: list[tuple[int, dict[str, Any]]]) -> str:
    """The phase on the message's output_item.done."""
    (phase,) = [
        p["item"]["phase"]
        for _, p in sent
        if p["type"] == "response.output_item.done" and p["item"]["type"] == "message"
    ]
    return phase


# -- the patch against the pinned file -----------------------------------------------------------


def test_the_substitution_applies_once_to_the_pinned_wrapper():
    ((anchor, replacement),) = SGLANG_STREAM_PHASE_PATCH["substitutions"]
    assert EXCERPT.count(anchor) == 1
    out = patched(EXCERPT)
    assert anchor not in out and replacement in out
    ast.parse(out)


def test_the_patch_pins_the_same_original_as_the_merge_patch_and_touches_another_part():
    for key in ("path", "sha256", "image"):
        assert SGLANG_STREAM_PHASE_PATCH[key] == SGLANG_PHASE_MERGE_PATCH[key]
    assert SGLANG_STREAM_PHASE_PATCH["path"] == SGLANG_SERVING_RESPONSES
    assert SGLANG_STREAM_PHASE_PATCH["sha256"] == SGLANG_SERVING_RESPONSES_SHA256
    assert SGLANG_STREAM_PHASE_PATCH["image"] == QWEN38_SGLANG_IMAGE
    # The two patches' anchors lie in different parts of the file: neither excerpt holds the other's.
    ((stream_anchor, _),) = SGLANG_STREAM_PHASE_PATCH["substitutions"]
    ((merge_anchor, _),) = SGLANG_PHASE_MERGE_PATCH["substitutions"]
    assert stream_anchor not in MERGE_EXCERPT and merge_anchor not in EXCERPT


# -- the stream ----------------------------------------------------------------------------------


def test_the_image_holds_the_text_until_the_tool_call_and_the_patch_streams_it():
    script = text_turn(CALL + COMPLETED)
    before = run(EXCERPT, script)
    after = run(patched(EXCERPT), script)
    # Unpatched: every delta waits for the function call's output_item.added, 8 events later at most.
    assert delta_lags(before, script) == [6, 5, 4]
    # Patched: each delta is sent before the inner stream produces another event.
    assert delta_lags(after, script) == [0, 0, 0]
    assert done_phase(before) == done_phase(after) == "commentary"


def test_an_answer_with_no_tool_call_streams_and_closes_as_final_answer():
    script = text_turn(COMPLETED)
    before = run(EXCERPT, script)
    after = run(patched(EXCERPT), script)
    assert delta_lags(before, script) == [6, 5, 4]  # held until response.completed
    assert delta_lags(after, script) == [0, 0, 0]
    assert done_phase(before) == done_phase(after) == "final_answer"


def test_only_the_closing_events_wait_and_for_one_event_only():
    script = text_turn(CALL + COMPLETED)
    after = run(patched(EXCERPT), script)
    assert len(after) == len(script)
    # How many inner events past its own each event was sent: the three closing events wait for the
    # function call's output_item.added, produced right after them; everything else goes at once.
    lags = [produced - (k + 1) for k, (produced, _) in enumerate(after)]
    closing = [7, 8, 9]
    assert [lags[k] for k in closing] == [3, 2, 1]
    assert all(lag == 0 for k, lag in enumerate(lags) if k not in closing)


def test_the_patched_stream_sends_the_images_events_in_the_images_order():
    for then in (CALL + COMPLETED, COMPLETED, CALL + text_turn(COMPLETED)[2:]):
        script = text_turn(then)
        before = [p for _, p in run(EXCERPT, script)]
        after = [p for _, p in run(patched(EXCERPT), script)]
        assert [p["type"] for p in after] == [p["type"] for p in script]
        # Same events, same content; the one difference is the phase on the message's
        # output_item.added, which the image rewrote once it knew and the patch leaves as the
        # inner generator wrote it. Codex reads the phase from the completed item.
        for b, a in zip(before, after, strict=True):
            if a["type"] == "response.output_item.added" and a["item"]["type"] == "message":
                b = {**b, "item": {**b["item"], "phase": a["item"]["phase"]}}
            assert a == b


def test_a_request_without_tools_passes_every_event_through_untouched():
    script = text_turn(COMPLETED)
    sent = run(patched(EXCERPT), script, tools=[])
    assert [p for _, p in sent] == script
    assert delta_lags(sent, script) == [0, 0, 0]


def test_a_message_followed_by_reasoning_then_a_call_closes_as_final_answer():
    # The one case the phase differs from the image's: the image waited past the reasoning for the
    # call (commentary); the patch decides on the event after the message, the reasoning item. The
    # history merge ignores the phase (SGLANG_PHASE_MERGE_PATCH), so the model's history is the same.
    reasoning = [
        {"type": "response.output_item.added", "item": {"type": "reasoning", "id": "rs_1"}},
        {"type": "response.output_item.done", "item": {"type": "reasoning", "id": "rs_1"}},
    ]
    script = text_turn(reasoning + CALL + COMPLETED)
    assert done_phase(run(EXCERPT, script)) == "commentary"
    assert done_phase(run(patched(EXCERPT), script)) == "final_answer"


def test_closing_events_left_at_the_end_of_the_stream_are_still_sent():
    # The inner generator always ends with a terminal event; should it not, nothing is lost.
    script = text_turn([])
    sent = [p for _, p in run(patched(EXCERPT), script)]
    assert [p["type"] for p in sent] == [p["type"] for p in script]
    assert done_phase([(0, p) for p in sent]) == "final_answer"
