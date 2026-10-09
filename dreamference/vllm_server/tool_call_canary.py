"""
The tool-call check `ling-admin server start` runs once the model answers.

`ling` speaks one wire protocol to the model server: upstream Codex's Responses API, streamed
(`WireApi` has no other variant at the pin). The server turns the model's text into a call with
its tool-call parser, keeps thinking apart with its reasoning parser, and renders the tools into
the prompt with the chat template, so a wrong parser or a broken template leaves ordinary chat
working and every agent turn broken: the model writes the call as text, or inside its reasoning,
and the session sees prose. This check sends one request shaped like `ling`'s, with one trivial
tool, and reads the stream the way `ling` does. It never blocks the start: the server is up, and
the chat UI does not need tools (specs/DREAMFERENCE_MODELS.md §2.1).
"""

import json
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Final

import requests

# The whole check, from the request to the last event. The answer is a few dozen tokens with
# reasoning off; a first request after a boot can take over 10 s, which warm-up absorbs first.
TOOL_CALL_CANARY_TIMEOUT_S: Final[float] = 60.0
CONNECT_TIMEOUT_S: Final[float] = 5.0
MAX_OUTPUT_TOKENS: Final[int] = 256

TOOL_NAME: Final[str] = "get_weather"
REQUIRED_ARGUMENT: Final[str] = "city"
PROMPT: Final[str] = "What is the weather in Paris right now? Use the tool."

# Text a Qwen-family model writes for a call. Seen in the answer or the reasoning, it means the
# model made the call and the server did not read it.
CALL_MARKUP: Final[Tuple[str, ...]] = ("<tool_call>", "<function=", '"name": "get_weather"', "get_weather(")


class ToolCallCanary:
    """
    One streamed `/v1/responses` request with one tool, judged as `ling` would see it.
    """

    @classmethod
    def request_body(cls, model_id: str) -> Dict[str, Any]:
        """
        The request: the fields and tool shape `ling` sends (a captured request of a `ling exec`
        turn), with one tool and no other context.

        Args:
            model_id (str): The model id the server reports on `/v1/models`.

        Returns:
            Dict[str, Any]: The JSON body.
        """
        return {
            "model": model_id,
            "instructions": "You are a helpful assistant. Use the tools you are given.",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": PROMPT}]}],
            "tools": [{
                "type": "function",
                "name": TOOL_NAME,
                "description": "Get the current weather for a city.",
                "strict": False,
                "parameters": {
                    "type": "object",
                    "properties": {REQUIRED_ARGUMENT: {"type": "string", "description": "The city."}},
                    "required": [REQUIRED_ARGUMENT],
                    "additionalProperties": False,
                },
            }],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            # `ling`'s catalog offers one reasoning level, `none` (ling-rs/src/lib.rs).
            "reasoning": {"effort": "none", "summary": "auto"},
            "store": False,
            "stream": True,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }

    @classmethod
    def run(cls, host: str, model_id: str, model_key: Optional[str] = None,
            timeout: float = TOOL_CALL_CANARY_TIMEOUT_S,
            post: Optional[Callable[..., Any]] = None,
            clock: Callable[[], float] = time.monotonic) -> Tuple[bool, str]:
        """
        Sends the request and judges the answer.

        Args:
            host (str): The model server's base URL, without `/v1`.
            model_id (str): The served model id.
            model_key (Optional[str]): The registry key of the served model, to name its parsers.
            timeout (float): Seconds the whole check may take.
            post (Optional[Callable[..., Any]]): `requests.post`, replaceable in tests.
            clock (Callable[[], float]): The clock the limit is measured on.

        Returns:
            Tuple[bool, str]: Whether a well-formed call came back, and one line saying what came
            back or what is likely at fault.
        """
        post = post or requests.post
        started = clock()
        try:
            response = post(f"{host.rstrip('/')}/v1/responses", json=cls.request_body(model_id),
                            headers={"Accept": "text/event-stream"}, stream=True,
                            timeout=(CONNECT_TIMEOUT_S, timeout))
        except requests.RequestException as error:
            return False, f"the request failed ({type(error).__name__}); nothing could be checked"
        try:
            if response.status_code != 200:
                body = (getattr(response, "text", "") or "").strip().replace("\n", " ")[:160]
                return False, cls.refusal(response.status_code, body, model_key)
            try:
                events, finished = cls.read_events(response.iter_lines(decode_unicode=True),
                                                   started + timeout, clock)
            except requests.RequestException as error:
                return False, f"the stream broke off ({type(error).__name__})"
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if not finished:
            return False, (f"no complete answer within {timeout:.0f} s "
                           f"(the server is busy, or the stream never ended)")
        return cls.verdict(events, model_key, clock() - started)

    @classmethod
    def read_events(cls, lines: Iterable[Any], deadline: float,
                    clock: Callable[[], float] = time.monotonic) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Reads server-sent events until the response ends or the deadline passes.

        Args:
            lines (Iterable[Any]): The stream's lines (`str` or `bytes`).
            deadline (float): When to stop, on `clock`.
            clock (Callable[[], float]): The clock.

        Returns:
            Tuple[List[Dict[str, Any]], bool]: The events' JSON payloads, and whether a final event
            (`response.completed`, `response.incomplete`, `response.failed` or `error`) arrived.
        """
        events: List[Dict[str, Any]] = []
        for line in lines:
            if clock() > deadline:
                return events, False
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                return events, True
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            events.append(event)
            if event.get("type") in ("response.completed", "response.incomplete", "response.failed", "error"):
                return events, True
        return events, False

    @classmethod
    def verdict(cls, events: List[Dict[str, Any]], model_key: Optional[str],
                elapsed: float) -> Tuple[bool, str]:
        """
        Judges the events: one `function_call` item naming the tool, with a call id and arguments
        that are a JSON object carrying the required argument.

        Args:
            events (List[Dict[str, Any]]): The events, in order.
            model_key (Optional[str]): The registry key, to name the parsers.
            elapsed (float): Seconds the check took.

        Returns:
            Tuple[bool, str]: The result and its line.
        """
        failed = next((event for event in events if event.get("type") in ("response.failed", "error")), None)
        if failed is not None:
            detail = failed.get("message") or (failed.get("response") or {}).get("error") or failed
            return False, f"the server reported an error mid-stream ({str(detail)[:120]})"
        items = cls.output_items(events)
        calls = [item for item in items if item.get("type") == "function_call"]
        text = cls.text_of(items, events, "message", "response.output_text")
        reasoning = cls.text_of(items, events, "reasoning", "response.reasoning")
        parsers = cls.parsers(model_key)
        if not calls:
            if any(mark in text for mark in CALL_MARKUP):
                return False, (f"the model wrote the call as text and the server did not read it: "
                               f"the tool-call parser ({parsers['tool']}) does not match this model's call format")
            if any(mark in reasoning for mark in CALL_MARKUP):
                return False, (f"the call came inside the reasoning: the reasoning parser ({parsers['reasoning']}) "
                               f"or the chat template's thinking switch is at fault")
            said = (text or reasoning).strip().replace("\n", " ")[:80]
            return False, (f"no tool call, only prose ({said!r}): check the tool-call parser ({parsers['tool']}) "
                           f"and that the chat template renders tools{parsers['template']}")
        call = calls[0]
        if call.get("name") != TOOL_NAME:
            return False, (f"a call to {call.get('name')!r}, not {TOOL_NAME!r}: the tool-call parser "
                           f"({parsers['tool']}) misread the name")
        if not call.get("call_id"):
            return False, "the call has no call_id, which ling needs to pair its output with it"
        try:
            arguments = json.loads(call.get("arguments") or "")
        except (TypeError, ValueError):
            return False, (f"the call's arguments are not JSON ({str(call.get('arguments'))[:60]!r}): "
                           f"the tool-call parser ({parsers['tool']}) is at fault")
        if not isinstance(arguments, dict) or not isinstance(arguments.get(REQUIRED_ARGUMENT), str):
            return False, (f"the call's arguments lack {REQUIRED_ARGUMENT!r} ({json.dumps(arguments)[:60]}): "
                           f"the tool-call parser ({parsers['tool']}) or the template's tool rendering is at fault")
        return True, f"{TOOL_NAME} {json.dumps(arguments)[:60]} in {elapsed:.1f} s"

    @classmethod
    def refusal(cls, status: int, body: str, model_key: Optional[str]) -> str:
        """
        The line for a request the server refused.

        Args:
            status (int): The HTTP status.
            body (str): The start of the response body.
            model_key (Optional[str]): The registry key.

        Returns:
            str: What came back and what is likely at fault.
        """
        if status == 400:
            return (f"the server refused the request (HTTP 400: {body!r}): the chat template"
                    f"{cls.parsers(model_key)['template']} or the server's Responses support is at fault")
        if status == 404:
            return "the server has no /v1/responses (HTTP 404): ling cannot use it"
        return f"the server answered HTTP {status} ({body!r})"

    @classmethod
    def output_items(cls, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        The response's output items: the final event's list when it carries one, otherwise each
        `response.output_item.done`, which is what `ling` records.

        Args:
            events (List[Dict[str, Any]]): The events.

        Returns:
            List[Dict[str, Any]]: The items.
        """
        for event in reversed(events):
            output = (event.get("response") or {}).get("output") if event.get("type", "").startswith("response.") else None
            if output:
                return [item for item in output if isinstance(item, dict)]
        return [event["item"] for event in events
                if event.get("type") == "response.output_item.done" and isinstance(event.get("item"), dict)]

    @classmethod
    def text_of(cls, items: List[Dict[str, Any]], events: List[Dict[str, Any]], item_type: str,
                delta_prefix: str) -> str:
        """
        All text of one kind: the items' content and summaries, or the streamed deltas.

        Args:
            items (List[Dict[str, Any]]): The output items.
            events (List[Dict[str, Any]]): The events.
            item_type (str): `message` or `reasoning`.
            delta_prefix (str): The start of the matching delta events' type (reasoning deltas
                are `response.reasoning_text.delta` or `response.reasoning_summary_text.delta`).

        Returns:
            str: The text.
        """
        parts: List[str] = []
        for item in items:
            if item.get("type") != item_type:
                continue
            for part in (item.get("content") or []) + (item.get("summary") or []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        if not parts:
            parts = [event.get("delta") or "" for event in events
                     if str(event.get("type", "")).startswith(delta_prefix)
                     and str(event.get("type", "")).endswith(".delta")]
        return "".join(part for part in parts if isinstance(part, str))

    @classmethod
    def parsers(cls, model_key: Optional[str]) -> Dict[str, str]:
        """
        How the served model's registry entry names its parsers and template, for the warning.

        Args:
            model_key (Optional[str]): The registry key, or None when the served id is not in it.

        Returns:
            Dict[str, str]: `tool`, `reasoning` and `template` phrases.
        """
        overrides: Dict[str, Any] = {}
        if model_key:
            from dreamference.hardware import get_model_launch_overrides
            try:
                overrides = get_model_launch_overrides(model_key) or {}
            except Exception:
                overrides = {}
        tool = overrides.get("tool_call_parser")
        reasoning = overrides.get("reasoning_parser")
        template = ""
        if overrides.get("chat_template_patches"):
            template = (f", patched at launch from {model_key}'s chat_template_patches "
                        f"(a stale anchor or a new checkpoint revision)")
        return {
            "tool": f"--tool-call-parser {tool}" if tool else "the server's --tool-call-parser",
            "reasoning": f"--reasoning-parser {reasoning}" if reasoning else "the server's --reasoning-parser",
            "template": template,
        }
