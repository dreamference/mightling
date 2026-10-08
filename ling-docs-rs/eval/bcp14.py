"""How often does an engine keep an RFC's BCP 14 keyword (MUST, SHOULD NOT, …) in its sentence?
For each keyword in the RFC's .txt, the trigram (word before, keyword, word after) must occur in the
extraction (whitespace normalised). Writes results/bcp14.json."""
import json
import os
import re
import sys

D = os.path.dirname(os.path.abspath(__file__))
KW = re.compile(r"(\w+) (MUST NOT|SHOULD NOT|SHALL NOT|MUST|SHOULD|SHALL|REQUIRED|RECOMMENDED|OPTIONAL|MAY) (\w+)")
out = {}
for eng in sys.argv[1:]:
    res = json.load(open(f"{D}/results/pdf-{eng}.json"))
    kept_all = total_all = 0
    for n in (9110, 9114, 9293):
        ref = re.sub(r"\s+", " ", open(f"{D}/reference/rfc{n}.txt").read())
        got = re.sub(r"\s+", " ", "\n".join(res[f"corpus/pdf/rfc{n}.pdf"]["pages"]))
        tri = [m.group(0) for m in KW.finditer(ref)]
        kept_all += sum(1 for t in tri if t in got)
        total_all += len(tri)
    out[eng] = f"{kept_all}/{total_all}"
    print(eng, out[eng], f"{kept_all / total_all:.2f}")
json.dump(out, open(f"{D}/results/bcp14.json", "w"), indent=1)
