"""Show one page as two engines extracted it: compare_page.py <file-rel> <page> <engine> <engine> [chars]."""
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
rel, page, engines = sys.argv[1], int(sys.argv[2]), sys.argv[3:5]
n = int(sys.argv[5]) if len(sys.argv) > 5 else 900
for e in engines:
    r = json.load(open(f"{D}/results/pdf-{e}.json"))["corpus/" + rel]
    print(f"===== {e} =====")
    print(r["pages"][page - 1][:n])
