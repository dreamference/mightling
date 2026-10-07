"""Fetch the embedding candidates and the chunking tokenizers into the scratch folder (the only networked
step after fetch.py): download_models.py [<model> …] (default: every candidate). Prints each model's
on-disk ONNX size."""
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
os.environ["CUDA_VISIBLE_DEVICES"] = ""
sys.path.insert(0, D)
from fastembed import TextEmbedding  # noqa: E402
from tokenizers import Tokenizer  # noqa: E402

from candidates import CANDIDATES, register  # noqa: E402

Tokenizer.from_pretrained("intfloat/multilingual-e5-small")  # chunking (XLM-R family)
Tokenizer.from_pretrained("BAAI/bge-small-en-v1.5")  # the English runs' chunking
for m in sys.argv[1:] or list(CANDIDATES):
    register(m)
    e = TextEmbedding(m, cache_dir=f"{D}/models", providers=["CPUExecutionProvider"], lazy_load=True)
    path = e.model._model_dir if hasattr(e.model, "_model_dir") else None
    size = 0
    if path:
        for dp, _, fs in os.walk(path):
            size += sum(os.path.getsize(os.path.join(dp, f)) for f in fs if ".onnx" in f)
    print("ok", m, f"{size / 2**20:.0f} MB", flush=True)
