"""What the tool-call canary blames when the server answers oddly or the model has no recipe."""

from dreamference.vllm_server.tool_call_canary import ToolCallCanary


def _recipe(monkeypatch, recipe):
    monkeypatch.setattr(
        "dreamference.hardware.get_model_launch_overrides", lambda model, engine=None: recipe
    )


# -- refusal -------------------------------------------------------------------------------------------


def test_a_status_other_than_400_or_404_is_reported_as_it_came(monkeypatch):
    _recipe(monkeypatch, {"tool_call_parser": "hermes"})

    line = ToolCallCanary.refusal(503, "overloaded", "model")

    assert line == "the server answered HTTP 503 ('overloaded')"
