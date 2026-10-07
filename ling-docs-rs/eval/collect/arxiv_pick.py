"""Pick recent arXiv cs.CL papers licensed CC BY 4.0 (licence checked per paper via OAI-PMH)."""
import json
import re
import sys
import time
import urllib.request

OUT = sys.argv[1]
UA = {"User-Agent": "mightling-eval/0.1 (dgxcoder@dreamference.ai)"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode()


feed = get("http://export.arxiv.org/api/query?search_query=cat:cs.CL&sortBy=submittedDate"
           "&sortOrder=descending&start=200&max_results=80")
ids = re.findall(r"<id>http://arxiv.org/abs/([^<]+)</id>", feed)
picked = []
for full in ids:
    base = re.sub(r"v\d+$", "", full)
    time.sleep(3)
    try:
        rec = get(f"http://export.arxiv.org/oai2?verb=GetRecord&identifier=oai:arXiv.org:{base}"
                  "&metadataPrefix=arXiv")
    except Exception as exc:  # noqa: BLE001
        print("err", base, exc, file=sys.stderr)
        continue
    lic = re.search(r"<license>([^<]+)</license>", rec)
    title = re.search(r"<title>([^<]+)</title>", rec)
    lic = lic.group(1) if lic else ""
    if "creativecommons.org/licenses/by/4.0" in lic:
        picked.append({"id": full, "base": base, "license": lic,
                       "title": re.sub(r"\s+", " ", title.group(1)) if title else ""})
        print(len(picked), base, picked[-1]["title"][:70], flush=True)
    if len(picked) >= 18:
        break
json.dump(picked, open(OUT, "w"), indent=1)
