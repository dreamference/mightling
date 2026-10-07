"""Second collection pass: the table-heavy U.S. government reports and 20 more Wikipedia articles
(distractors, so the set reaches about 200 documents). Appends to the manifest; skips what is present."""
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = sys.argv[1]
UA = {"User-Agent": "mightling-eval/0.1 (dgxcoder@dreamference.ai)"}
USGOV = "Public domain (work of the U.S. federal government, 17 U.S.C. 105)"
path = os.path.join(ROOT, "manifest_fetched.json")
manifest = json.load(open(path))
have = {m["path"] for m in manifest}
WIKI = next(m["licence"] for m in manifest if m["path"].startswith("html/"))


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300).read()


def add(rel, url, licence, source, data):
    open(os.path.join(ROOT, rel), "wb").write(data)
    manifest.append({"path": rel, "url": url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                     "licence": licence, "source": source})
    print(rel, len(data))


for rel, url, src in [
    ("pdf/eia-monthly-energy-review-2025-09.pdf", "https://www.eia.gov/totalenergy/data/monthly/archive/00352509.pdf",
     "EIA Monthly Energy Review, September 2025 (tables)"),
    ("pdf/census-p60-282.pdf", "https://www2.census.gov/library/publications/2024/demo/p60-282.pdf",
     "U.S. Census Bureau P60-282 (tables)"),
]:
    if rel not in have:
        add(rel, url, USGOV, src, get(url))

for t in ["Arctic", "Glacier", "Tide", "Lighthouse", "Fjord", "Salmon", "Whale", "Seal_(animal)", "Penguin",
          "Albatross", "Database", "Compiler", "Operating_system", "Cryptographic_hash_function", "Email",
          "Spreadsheet", "PDF", "Markdown", "Search_engine", "Information_retrieval"]:
    rel = f"html/{t}.html"
    if rel in have:
        continue
    q = get("https://en.wikipedia.org/w/api.php?action=query&prop=revisions&rvprop=ids&format=json&titles="
            + urllib.parse.quote(t))
    page = next(iter(json.loads(q)["query"]["pages"].values()))
    r = page["revisions"][0]["revid"]
    url = f"https://en.wikipedia.org/api/rest_v1/page/html/{urllib.parse.quote(t)}/{r}"
    data = get(url)
    if b"This page is a redirect" in data[:200000] or b"mw:PageProp/redirect" in data[:20000]:
        print("redirect, skipped:", t)
        continue
    add(rel, url, WIKI, f"Wikipedia: {t} (revision {r})", data)
json.dump(manifest, open(path, "w"), indent=1)
