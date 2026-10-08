"""Rebuild the ling-docs evaluation corpus from manifest.json: download every fetched document, check its
SHA-256, then write the generated documents (generate.py) and the hostile PDFs (make_hostile.py).

    python fetch.py            # into ./corpus, ./reference and ./hostile next to this file

A checksum mismatch is reported, not fatal: Wikipedia's PDF renderings are made on request from the
current revision and cannot be pinned, so those five files drift; everything else is pinned (RFC and
government URLs are fixed, Wikipedia HTML by revision id, GitHub files by commit, arXiv by version)."""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

D = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "mightling-eval/0.1 (dgxcoder@dreamference.ai)"}


def get(url):
    for attempt in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300).read()
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                raise
            print("retry", url, exc, file=sys.stderr)
            time.sleep(3)


def main():
    manifest = json.load(open(f"{D}/manifest.json"))
    drift = 0
    for e in manifest["fetched"]:
        dest = os.path.join(D, e["path"])
        if os.path.exists(dest) and hashlib.sha256(open(dest, "rb").read()).hexdigest() == e["sha256"]:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        data = get(e["url"])
        open(dest, "wb").write(data)
        if hashlib.sha256(data).hexdigest() != e["sha256"]:
            drift += 1
            print("checksum differs (content changed upstream):", e["path"], file=sys.stderr)
    subprocess.run([sys.executable, f"{D}/generate.py", f"{D}/corpus"], check=True)
    subprocess.run([sys.executable, f"{D}/make_hostile.py", f"{D}/hostile", f"{D}/corpus/pdf/nist-sp-800-207.pdf"],
                   check=True)
    for e in manifest["generated"]:
        p = os.path.join(D, e["path"])
        if "sha256" in e and hashlib.sha256(open(p, "rb").read()).hexdigest() != e["sha256"]:
            drift += 1
            print("generated file differs:", e["path"], file=sys.stderr)
    print(f"corpus ready; {drift} file(s) differ from the recorded checksums")


if __name__ == "__main__":
    main()
