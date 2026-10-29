"""The shapes of the JSON bodies `ling-admin model benchmark` sends to the model server."""

from collections.abc import Mapping, Sequence
from typing import Required, TypedDict

JsonValue = str | int | float | bool | Sequence["JsonValue"] | Mapping[str, "JsonValue"] | None


class SamplePrompt(TypedDict, total=False):
    """One benchmark prompt: its text, an optional system message, extra request fields."""

    prompt: Required[str]
    system: str
    kwargs: Mapping[str, JsonValue]
