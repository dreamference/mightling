"""Collect the redistributable part of the ling-docs evaluation corpus, pinned (revision ids, commit
shas), and write a manifest with URL, sha256 and licence per file."""
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = sys.argv[1]
UA = {"User-Agent": "mightling-eval/0.1 (dgxcoder@dreamference.ai) python-urllib"}
manifest = []


def get(url, binary=False):
    for attempt in range(2):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
            return data if binary else data.decode()
        except Exception as exc:  # noqa: BLE001
            print("retry", url, exc, file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(url)


def save(rel, url, licence, source, data=None, strict=False):
    if data is None:
        try:
            data = get(url, binary=True)
        except RuntimeError:
            if strict or "rfc-editor.org/rfc/rfc" in url:
                raise
            print("SKIP", rel, url, file=sys.stderr)
            return
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(data)
    manifest.append({"path": rel, "url": url, "sha256": hashlib.sha256(data).hexdigest(),
                     "bytes": len(data), "licence": licence, "source": source})
    print(rel, len(data), flush=True)


IETF = "IETF Trust Legal Provisions (BCP 78): may be copied and distributed unmodified"
for n in [9110, 8446, 9293, 6749, 7519, 8259, 3339, 9114, 1149, 7231]:
    for url in (f"https://www.rfc-editor.org/rfc/rfc{n}.pdf", f"https://www.rfc-editor.org/rfc/pdfrfc/rfc{n}.txt.pdf"):
        try:
            save(f"pdf/rfc{n}.pdf", url, IETF, f"RFC {n}")
            break
        except RuntimeError as exc:
            print("next", exc, file=sys.stderr)
for n in [5321, 2616, 7540, 6455, 4648]:
    save(f"txt/rfc{n}.txt", f"https://www.rfc-editor.org/rfc/rfc{n}.txt", IETF, f"RFC {n}")

USGOV = "Public domain (work of the U.S. federal government, 17 U.S.C. 105)"
for rel, url in [
    ("pdf/nist-sp-800-63b.pdf", "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-63b.pdf"),
    ("pdf/nist-cswp-29-csf-2.pdf", "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf"),
    ("pdf/nist-sp-800-207.pdf", "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-207.pdf"),
    ("pdf/nist-fips-197-upd1.pdf", "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.197-upd1.pdf"),
    ("pdf/nist-sp-800-88r1.pdf", "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-88r1.pdf"),
]:
    save(rel, url, USGOV, "NIST")
for rel, url in [
    ("pdf/bls-empsit-2025-09.pdf", "https://www.bls.gov/news.release/archives/empsit_09052025.pdf"),
    ("pdf/cbo-budget-outlook-2025.pdf", "https://www.cbo.gov/system/files/2025-01/60870-Outlook-2025.pdf"),
]:
    try:
        save(rel, url, USGOV, "US government report with tables")
    except RuntimeError as exc:
        print("skip", exc, file=sys.stderr)

# Wikipedia, pinned to revision ids.
WIKI = "CC BY-SA 4.0 (Wikipedia contributors)"
html_titles = ["Atlantic_mightling", "Transport_Layer_Security", "HTTP", "Rust_(programming_language)",
               "Python_(programming_language)", "SQLite", "Unicode", "Photosynthesis", "Plate_tectonics", "Black_hole",
               "Roman_Empire", "French_Revolution", "Penicillin", "Vaccine", "Inflation", "Supply_and_demand",
               "Machine_learning", "Transformer_(deep_learning)", "Public-key_cryptography", "Bitcoin",
               "Iceland", "Volcano", "Coral_reef", "Honey_bee", "Coffee", "Chess", "Mount_Everest", "Amazon_River",
               "Electric_vehicle", "Lithium-ion_battery", "Solar_panel", "Wind_power", "General_Data_Protection_Regulation", "Copyright",
               "Open-source_software", "Linux_kernel", "Git", "Docker_(software)", "Kubernetes", "Graphics_processing_unit"]
pdf_titles = ["Mightling", "Auk", "Seabird", "Arctic_tern", "Guillemot"]
docx_titles = ["Tea", "Chocolate", "Bread", "Cheese", "Olive_oil", "Wine", "Rice", "Potato", "Tomato", "Apple",
               "Banana", "Salt", "Sugar", "Honey", "Vanilla"]


def revid(title):
    """The current revision id of `title`, following redirects (a redirect's own page is a stub)."""
    q = get("https://en.wikipedia.org/w/api.php?action=query&prop=revisions&rvprop=ids&format=json&redirects=1"
            "&titles=" + urllib.parse.quote(title))
    page = next(iter(json.loads(q)["query"]["pages"].values()))
    return page["revisions"][0]["revid"]


for t in html_titles:
    r = revid(t)
    save(f"html/{t}.html", f"https://en.wikipedia.org/api/rest_v1/page/html/{urllib.parse.quote(t)}/{r}", WIKI,
         f"Wikipedia: {t} (revision {r})")
for t in pdf_titles:
    r = revid(t)
    try:
        save(f"pdf/wikipedia-{t}.pdf", f"https://en.wikipedia.org/api/rest_v1/page/pdf/{urllib.parse.quote(t)}",
             WIKI, f"Wikipedia PDF rendering: {t} (current revision {r} at collection time)")
    except RuntimeError as exc:
        print("skip", exc, file=sys.stderr)
docx_src = []
for t in docx_titles:
    r = revid(t)
    url = f"https://en.wikipedia.org/api/rest_v1/page/html/{urllib.parse.quote(t)}/{r}"
    save(f"_docx_src/{t}.html", url, WIKI, f"Wikipedia: {t} (revision {r}), source of a generated DOCX")

# The Rust book (MIT OR Apache-2.0), pinned to a commit.
sha = json.loads(get("https://api.github.com/repos/rust-lang/book/commits/main"))["sha"]
tree = json.loads(get(f"https://api.github.com/repos/rust-lang/book/git/trees/{sha}?recursive=1"))["tree"]
chapters = sorted(x["path"] for x in tree if x["path"].startswith("src/ch") and x["path"].endswith(".md"))
for p in chapters[::2][:50]:
    save(f"md/rust-book-{os.path.basename(p)}", f"https://raw.githubusercontent.com/rust-lang/book/{sha}/{p}",
         "MIT OR Apache-2.0 (The Rust Programming Language)", f"rust-lang/book@{sha[:12]}")

# Python PEPs (public domain or CC0), pinned to a commit.
sha = json.loads(get("https://api.github.com/repos/python/peps/commits/main"))["sha"]
for n in [8, 20, 257, 484, 572, 634, 3333, 405, 517, 668]:
    p = f"peps/pep-{n:04d}.rst"
    save(f"rst/pep-{n:04d}.rst", f"https://raw.githubusercontent.com/python/peps/{sha}/{p}",
         "Public domain or CC0-1.0 (stated in each PEP)", f"python/peps@{sha[:12]}")

# arXiv papers licensed CC BY 4.0 (checked per paper through OAI-PMH by arxiv_pick.py).
for p in json.load(open(sys.argv[2]))[:15]:
    try:
        save(f"pdf/arxiv-{p['base']}.pdf", f"https://arxiv.org/pdf/{p['id']}", p["license"],
             f"arXiv {p['id']}: {p['title']}")
        time.sleep(3)
    except RuntimeError as exc:
        print("skip", exc, file=sys.stderr)

json.dump(manifest, open(os.path.join(ROOT, "manifest_fetched.json"), "w"), indent=1)
print("files:", len(manifest))
