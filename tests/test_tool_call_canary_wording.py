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


# -- parsers -------------------------------------------------------------------------------------------


def test_a_served_id_outside_the_registry_names_the_servers_own_flags(monkeypatch):
    _recipe(monkeypatch, {"tool_call_parser": "must-not-be-read"})

    assert ToolCallCanary.parsers(None) == {
        "tool": "the server's --tool-call-parser",
        "reasoning": "the server's --reasoning-parser",
        "template": "",
    }


def test_a_recipe_without_template_patches_blames_no_patch(monkeypatch):
    _recipe(monkeypatch, {"engine": "vllm", "tool_call_parser": "hermes"})

    assert ToolCallCanary.parsers("model") == {
        "tool": "--tool-call-parser hermes",
        "reasoning": "the server's --reasoning-parser",
        "template": "",
    }
