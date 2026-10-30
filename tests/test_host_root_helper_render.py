"""`HostRootHelper.render` refuses to write a root helper it could not fill completely."""

import pytest

from dreamference.vllm_server import host_root_helper
from dreamference.vllm_server.host_root_helper import PROFILE_END, HostRootHelper
from dreamference.vllm_server.sandbox_prerequisite import SandboxPrerequisite


def test_a_profile_that_would_end_the_helpers_heredoc_early_is_refused(monkeypatch):
    # Whatever followed the marker would run in the root helper as shell, not land in the profile.
    monkeypatch.setattr(
        SandboxPrerequisite,
        "profile_text",
        classmethod(lambda cls, bwrap: f"profile bwrap {{\n}}\n{PROFILE_END}\nrm -rf /\n"),
    )

    with pytest.raises(ValueError, match="was not filled"):
        HostRootHelper.render()


def test_a_placeholder_left_in_the_template_is_refused(monkeypatch):
    monkeypatch.setattr(
        host_root_helper, "TEMPLATE", host_root_helper.TEMPLATE + "\n@UNKNOWN_KEY@\n"
    )

    with pytest.raises(ValueError, match="was not filled"):
        HostRootHelper.render()


def test_the_helper_as_shipped_is_filled():
    text = HostRootHelper.render()

    assert text.startswith("#!/bin/sh")
    assert text.count(PROFILE_END) >= 2
