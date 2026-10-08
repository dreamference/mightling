"""One candidate passage per non-English document (200-500 characters of running text), with its locator."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib  # noqa: E402

root = sys.argv[1]
rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
for f in sorted(os.listdir(f"{root}/html-multi")):
    units = evallib.extract(root, f"html-multi/{f}")
    paras = []
    for u in units:
        for line in u["text"].split("\n"):
            if 200 <= len(line) <= 600 and sum(c.isalpha() for c in line) > 0.7 * len(line.replace(" ", "")) \
                    and "Lua" not in line and "ISBN" not in line:
                paras.append((u["loc"], line))
    loc, text = rng.choice(paras[2:min(len(paras), 40)] or paras)
    print(f"### html-multi/{f} | {loc}\n{text[:420]}\n")
