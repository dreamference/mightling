"""Several candidate passages from one document: passages_one.py <corpus> <rel> [count] (question authoring)."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib  # noqa: E402

root, rel = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 3
paras = []
for u in evallib.extract(root, rel):
    if "Einzelnachweise" in u["loc"] or "Literatur" in u["loc"]:
        continue
    for line in u["text"].split("\n"):
        if 150 <= len(line) <= 600 and "http" not in line and "ISBN" not in line:
            paras.append((u["loc"], line))
for loc, text in random.Random(5).sample(paras, min(n, len(paras))):
    print(f"### {rel} | {loc}\n{text[:330]}\n")
