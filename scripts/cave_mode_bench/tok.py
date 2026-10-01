"""Token counts with the served model's own tokenizer (Qwen3.8 by default)."""
import glob
import os
from pathlib import Path

from tokenizers import Tokenizer

_PATTERN = "models--RadixArk--Qwen3.8-27B-NVFP4/snapshots/*/tokenizer.json"


def _tokenizer_path() -> str:
    explicit = os.environ.get("CAVE_TOKENIZER")
    if explicit:
        return explicit
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"
    found = sorted(glob.glob(str(hub / _PATTERN)))
    if not found:
        raise SystemExit(f"no tokenizer under {hub}; set CAVE_TOKENIZER to a tokenizer.json")
    return found[-1]


_T = Tokenizer.from_file(_tokenizer_path())


def n(text: str) -> int:
    return len(_T.encode(text, add_special_tokens=False).ids)
