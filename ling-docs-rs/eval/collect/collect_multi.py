"""Third collection pass: the multilingual part of the set. Three articles (the other-language editions of
Coffee, Volcano and Photosynthesis, which the English part also holds, so a search can confuse them) in
each of eight languages, pinned by revision id, CC BY-SA 4.0. Appends to the manifest."""
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = sys.argv[1]
UA = {"User-Agent": "mightling-eval/0.1 (dgxcoder@dreamference.ai)"}
path = os.path.join(ROOT, "manifest_fetched.json")
manifest = json.load(open(path))
have = {m["path"] for m in manifest}
WIKI = next(m["licence"] for m in manifest if m["path"].startswith("html/"))
LANGS = ["de", "fr", "es", "sv", "ru", "zh", "ja", "ar"]
os.makedirs(os.path.join(ROOT, "html-multi"), exist_ok=True)


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300).read()


for en in ["Coffee", "Volcano", "Photosynthesis"]:
    q = json.loads(get("https://en.wikipedia.org/w/api.php?action=query&prop=langlinks&lllimit=500&format=json&titles="
                       + en))
    links = {l["lang"]: l["*"] for l in next(iter(q["query"]["pages"].values()))["langlinks"]}
    for lang in LANGS:
        rel = f"html-multi/{lang}-{en}.html"
        if rel in have:
            continue
        title = links[lang].replace(" ", "_")
        api = f"https://{lang}.wikipedia.org/w/api.php?action=query&prop=revisions&rvprop=ids&format=json&titles="
        r = next(iter(json.loads(get(api + urllib.parse.quote(title)))["query"]["pages"].values()))["revisions"][0]["revid"]
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/html/{urllib.parse.quote(title)}/{r}"
        data = get(url)
        open(os.path.join(ROOT, rel), "wb").write(data)
        manifest.append({"path": rel, "url": url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                         "licence": WIKI, "source": f"Wikipedia ({lang}): {title} (revision {r})"})
        print(rel, len(data))
json.dump(manifest, open(path, "w"), indent=1)
