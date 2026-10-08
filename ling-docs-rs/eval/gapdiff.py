"""8-grams of the reference one engine loses and another keeps, with the first engine's text there:
gapdiff.py <rfcNNNN> <engine-a> <engine-b> [count] (debugging the reading-order pass)."""
import json
import os
import re
import sys

D = os.path.dirname(os.path.abspath(__file__))
name, a, b = sys.argv[1], sys.argv[2], sys.argv[3]
W = re.compile(r"[a-z0-9]+")
ref = W.findall(open(f"{D}/reference/{name}.txt").read().lower())


def grams(eng):
    t = "\n".join(json.load(open(f"{D}/results/pdf-{eng}.json"))[f"corpus/pdf/{name}.pdf"]["pages"])
    g = W.findall(t.lower())
    return {" ".join(g[i:i + 8]) for i in range(len(g) - 7)}, t


ha, ta = grams(a)
hb, _ = grams(b)
miss = [i for i in range(len(ref) - 7) if " ".join(ref[i:i + 8]) not in ha and " ".join(ref[i:i + 8]) in hb]
print(len(miss), "lost by", a, "kept by", b)
last, shown = -100, 0
for i in miss:
    if i - last > 40 and shown < int(sys.argv[4] if len(sys.argv) > 4 else 6):
        print("REF:", " ".join(ref[i:i + 16]))
        shown += 1
        m = re.search(r"\W+".join(ref[i:i + 3]), ta.lower())
        if m:
            print("  A:", repr(ta[max(0, m.start() - 100):m.start() + 220]))
    last = i
