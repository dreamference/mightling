"""The loop guard's half in the model gate: what `ling` asks of a request it cannot write itself.

See docs/dev/loop-guard.md.

Codex builds the `/v1/responses` body and offers the launcher no way to change it; what an
extension can add is `client_metadata`. The launcher (`ling-rs/src/loop_guard.rs`) names there a
cap on the response's length (`ling_max_output_tokens`) and a sampling arm (`ling_sampling`), and
this module, run by the gate on every `/v1/responses` body, turns them into the request's own
fields, only where the body sets none: a client's explicit value always wins. A body that asks
for nothing is passed on byte for byte, so SWE-bench and Night Shift, whose `ling exec` asks for
nothing unless an arm says so, see no change at all.

The cap is the only length bound SGLang honours on `/v1/responses` (it has no per-request thinking
budget there), and the one thing that ends a loop the server never streams: a loop inside a tool
call's argument, which SGLang's tool parser holds until the argument closes.

Like `model_gate_adapters.py`, this file is copied beside the service into the gate's container
and imported there without the package, so it uses the standard library only.
"""

import json
from typing import Final

# What the gate's probe answers as `loop_guard`: the rules below, version 1.
VERSION: Final[int] = 1

# The one request whose body is read: the only endpoint `ling` speaks.
GUARDED_METHOD: Final[str] = "POST"
GUARDED_PATH: Final[str] = "/v1/responses"

SURFACE_KEY: Final[str] = "ling_surface"
BUDGET_KEY: Final[str] = "ling_max_output_tokens"
SAMPLING_KEY: Final[str] = "ling_sampling"

# Qwen3.8-27B's card, "Instruct (or non-thinking) mode", verified 2026-10-20: every `ling` surface
# runs the model with reasoning effort `none`, yet the server's defaults (the checkpoint's
# generation_config.json: temperature 1.0, top_p 0.95, top_k 20) are the card's thinking-mode values,
# and presence_penalty is 0. The card names presence_penalty (0 to 2) as the lever against endless
# repetition. An arm only, by the owner's decision of 2026-10-20: no default changes.
SAMPLING_ARMS: Final[dict[str, dict[str, float]]] = {
    "nonthinking": {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repetition_penalty": 1.0,
    },
}

# A cap below this is a mistake, not a choice: the answer could not say anything.
MIN_BUDGET: Final[int] = 64


class ModelGateGuard:
    """The rules, as classmethods: the gate's service and the tests share them."""

    @classmethod
    def applies(cls, method: str, path: str) -> bool:
        """Whether the gate reads this request's body for the guard.

        Args:
            method: The request's method.
            path: Its path, without a query.

        Returns:
            bool: True for `POST /v1/responses`.
        """
        return method == GUARDED_METHOD and path.split("?", 1)[0] == GUARDED_PATH

    @classmethod
    def rewrite(cls, body: bytes) -> tuple[bytes, str | None]:
        """Applies what the request's `client_metadata` asks.

        Args:
            body: The request's JSON body.

        Returns:
            tuple[bytes, str | None]: The body to send (the same bytes when nothing applied) and a
            line for the gate's log, or None.
        """
        try:
            record = json.loads(body)
        except ValueError:
            return body, None
        if not isinstance(record, dict):
            return body, None
        metadata = record.get("client_metadata")
        if not isinstance(metadata, dict):
            return body, None
        applied = []
        budget = cls.budget(metadata.get(BUDGET_KEY))
        if budget is not None and record.get("max_output_tokens") is None:
            record["max_output_tokens"] = budget
            applied.append(f"max_output_tokens={budget}")
        arm = SAMPLING_ARMS.get(str(metadata.get(SAMPLING_KEY) or "").strip().lower())
        if arm:
            filled = [key for key, value in arm.items() if record.get(key) is None]
            for key in filled:
                record[key] = arm[key]
            if filled:
                applied.append(f"sampling={metadata.get(SAMPLING_KEY)} ({', '.join(filled)})")
        if not applied:
            return body, None
        surface = str(metadata.get(SURFACE_KEY) or "?")
        return json.dumps(record).encode("utf-8"), f"loop guard ({surface}): {'; '.join(applied)}"

    @classmethod
    def budget(cls, value: object) -> int | None:
        """Reads a cap from the metadata, where every value is a string.

        Args:
            value: The metadata's value.

        Returns:
            int | None: The cap, or None for a missing, malformed or absurdly small one.
        """
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return number if number >= MIN_BUDGET else None
