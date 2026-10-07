"""Fetch the embedding models and the chunking tokenizer into the scratch folder (the only networked step)."""
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
os.environ["CUDA_VISIBLE_DEVICES"] = ""
from fastembed import TextEmbedding  # noqa: E402
from tokenizers import Tokenizer  # noqa: E402

Tokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
for m in sys.argv[1:]:
    TextEmbedding(m, cache_dir=f"{D}/models", providers=["CPUExecutionProvider"], lazy_load=True)
    print("ok", m, flush=True)
