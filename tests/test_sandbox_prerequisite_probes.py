"""What the sandbox prerequisite reads from the machine before it decides to ask.

A profile file it cannot read does not count as attaching to bubblewrap; a closed input is "not
now", never "fix it" with sudo; the restriction is known only from the kernel's own setting; and
only a person at a terminal on both ends is asked anything.
"""

from test_sandbox_prerequisite import machine  # noqa: F401 - fixture

from dreamference.vllm_server import SandboxPrerequisite


def test_a_profile_that_cannot_be_read_is_skipped_and_the_rest_still_count(machine):  # noqa: F811
    (machine["profiles"] / "abstractions").mkdir()
    (machine["profiles"] / "local-bwrap").write_text("profile /usr/bin/bwrap {\n}\n")
    assert SandboxPrerequisite.profiles_attaching("/usr/bin/bwrap") == [
        str(machine["profiles"] / "local-bwrap")
    ]
