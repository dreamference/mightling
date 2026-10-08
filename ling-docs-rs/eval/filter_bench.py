"""What does skipping unsupported files without opening them save? Runs each discovery strategy over
Documents (the corpus) + Downloads (make_downloads.py) in its own memory-capped scope, with the folders'
pages evicted from the page cache first (posix_fadvise DONTNEED, no root needed), and reports wall time,
CPU time, bytes read and peak RSS. filter_bench.py -> results/filter.json

Strategies:
  ext        walk + lstat + extension allow-list; opens nothing
  ext+sniff  ext, then the first 8 KiB of each candidate (magic number / NUL sniff)
  dedupe     ext+sniff, then SHA-256 of each surviving candidate (duplicates are indexed once)
  hash-all   SHA-256 of every file, streamed (an indexer that hashes before it filters)
  slurp-all  read every file whole into memory (an indexer that loads, then decides)"""
import hashlib
import json
import os
import resource
import subprocess
import sys
import time

D = os.path.dirname(os.path.abspath(__file__))
ROOTS = [f"{D}/corpus", f"{D}/downloads"]
SUPPORTED = {".pdf", ".md", ".txt", ".rst", ".org", ".tex", ".html", ".htm", ".docx", ".odt", ".eml", ".mbox", ".csv"}


def walk():
    for root in ROOTS:
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if not d.startswith((".", "_"))]
            for f in fns:
                if not f.startswith(".") and f != "manifest_fetched.json":
                    yield os.path.join(dp, f)


def sniff_ok(path, head):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return b"%PDF-" in head[:1024]
    if ext in (".docx", ".odt"):
        return head[:4] == b"PK\x03\x04"
    return b"\x00" not in head  # text formats: the context engine's NUL sniff


def run(strategy):
    files = cand = kept = dups = 0
    seen = set()
    for p in walk():
        files += 1
        if strategy in ("hash-all", "slurp-all"):
            if strategy == "hash-all":
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    while chunk := f.read(1 << 20):
                        h.update(chunk)
            else:
                data = open(p, "rb").read()
                del data
            continue
        os.lstat(p)
        if os.path.splitext(p)[1].lower() not in SUPPORTED:
            continue
        cand += 1
        if strategy == "ext":
            kept += 1
            continue
        with open(p, "rb") as f:
            head = f.read(8192)
        if not sniff_ok(p, head):
            continue
        kept += 1
        if strategy == "dedupe":
            h = hashlib.sha256()
            with open(p, "rb") as f:
                while chunk := f.read(1 << 20):
                    h.update(chunk)
            d = h.digest()
            dups += d in seen
            seen.add(d)
    io = dict(line.split(": ") for line in open("/proc/self/io").read().splitlines())
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return {"files": files, "candidates": cand, "kept": kept, "duplicates": dups,
            "read_mb": round(int(io["rchar"]) / 2**20, 1), "cpu_s": round(ru.ru_utime + ru.ru_stime, 2),
            "peak_rss_mb": round(ru.ru_maxrss / 1024, 1)}


def evict():
    for p in walk():
        fd = os.open(p, os.O_RDONLY)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        os.close(fd)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--one":
        t = time.perf_counter()
        r = run(sys.argv[2])
        r["wall_s"] = round(time.perf_counter() - t, 2)
        print(json.dumps(r))
        sys.exit(0)
    total = sum(os.path.getsize(p) for p in walk())
    results = {"folders_files": sum(1 for _ in walk()), "folders_gib": round(total / 2**30, 2)}
    for strategy in ("ext", "ext+sniff", "dedupe", "hash-all", "slurp-all"):
        evict()
        t = time.perf_counter()
        r = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "-p", "MemoryMax=1G", "-p",
                            "MemorySwapMax=0", "--", "nice", "-n", "10", "ionice", "-c", "3", sys.executable,
                            __file__, "--one", strategy], capture_output=True, text=True)
        wall = round(time.perf_counter() - t, 2)
        if r.returncode == 0:
            results[strategy] = json.loads(r.stdout.strip().splitlines()[-1])
        else:
            results[strategy] = {"failed": f"exit {r.returncode} after {wall} s"
                                 + (" (OOM-killed at the 1 GB cap; check the user journal)" if r.returncode in (-9, -15) else "")}
        print(strategy, results[strategy], flush=True)
    json.dump(results, open(f"{D}/results/filter.json", "w"), indent=1)
