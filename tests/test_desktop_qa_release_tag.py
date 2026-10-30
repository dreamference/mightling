"""The desktop QA reads the latest release tag from the /releases/latest redirect; with no
published release that redirect points at /releases and the install.sh leg must skip."""

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "qa" / "desktop_linux_qa.sh"


def tag(url: str) -> str:
    # The script is piped to the desktop on standard input, so the function is cut out of it.
    func = re.search(r"^release_tag\(\) \{.*?^\}", SCRIPT.read_text(), re.S | re.M).group(0)
    out = subprocess.run(
        ["bash", "-c", func + '\nrelease_tag "$1"', "_", url],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_tag_from_release_redirect():
    assert tag("https://github.com/dreamference/mightling/releases/tag/v1.8.0") == "1.8.0"


def test_no_release_gives_empty_tag():
    assert tag("https://github.com/dreamference/mightling/releases") == ""
    assert tag("") == ""


def test_install_leg_skips_without_release():
    text = SCRIPT.read_text()
    assert 'skip "no published release' in text
    assert 'release_tag "$(curl' in text
