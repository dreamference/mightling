"""Where does an engine lose the reference's word order? Print reference 8-grams it misses, with context:
missing_grams.py <rfcNNNN> <engine> [count]. Also tallies how many missed 8-grams contain a BCP 14 keyword."""
import json
import os
import re
import sys

D = os.path.dirname(os.path.abspath(__file__))
name, eng = sys.argv[1], sys.argv[2]
k = int(sys.argv[3]) if len(sys.argv) > 3 else 4
W = re.compile(r"[a-z0-9]+")
ref = W.findall(open(f"{D}/reference/{name}.txt").read().lower())
got_text = "\n".join(json.load(open(f"{D}/results/pdf-{eng}.json"))[f"corpus/pdf/{name}.pdf"]["pages"])
got = W.findall(got_text.lower())
have = {" ".join(got[i:i + 8]) for i in range(len(got) - 7)}
miss = [i for i in range(len(ref) - 7) if " ".join(ref[i:i + 8]) not in have]
kw = {"must", "shall", "should", "may", "required", "recommended", "optional", "not"}
with_kw = sum(1 for i in miss if any(w in kw and w.upper() for w in ref[i:i + 8]))
print(f"{eng} {name}: {len(miss)} of {len(ref) - 7} reference 8-grams missing; {with_kw} contain a BCP 14 word")
shown, last = 0, -100
for i in miss:
    if i - last > 30 and shown < k:
        print("  ref:", " ".join(ref[i:i + 14]))
        shown += 1
    last = i
