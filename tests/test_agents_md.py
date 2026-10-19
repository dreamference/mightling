"""AGENTS.md stays small enough for `ling` to read whole, and its pointers lead somewhere."""

import re
from pathlib import Path
from typing import Final

REPO: Final[Path] = Path(__file__).resolve().parent.parent
AGENTS: Final[Path] = REPO / "AGENTS.md"

# AGENTS.md's own rule: under about 24 KB, since `ling` reads only its first 32 KB
# (`project_doc_max_bytes`); detail goes into docs/dev/.
LIMIT_BYTES: Final[int] = 24_000


def test_agents_md_stays_under_its_size():
    assert len(AGENTS.read_bytes()) < LIMIT_BYTES


def test_every_docs_dev_link_in_agents_md_exists():
    text = AGENTS.read_text(encoding="utf-8")
    targets = set(re.findall(r"\]\((docs/dev/[^)#]+)", text))
    assert targets
    assert [target for target in sorted(targets) if not (REPO / target).is_file()] == []
