"""Run every PDF engine on every PDF (corpus + hostile set) in bwrap with no network, a memory cap and a
timeout, as ling-docs would. Writes results/pdf-<engine>.json: {file: {ok, pages|error, seconds, maxrss_kb}}."""
import glob
import json
import os
import subprocess
import sys

D = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(D, "venv/bin/python")
ENGINES = sys.argv[1].split(",")
FILES = sorted(glob.glob(f"{D}/corpus/pdf/*.pdf")) + sorted(glob.glob(f"{D}/hostile/*.pdf"))
os.makedirs(f"{D}/results", exist_ok=True)
HOME = os.path.expanduser("~")


def run(engine, path, cap="1G", timeout=30):
    cmd = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={cap}", "-p", "MemorySwapMax=0",
           "--", "nice", "-n", "15", "ionice", "-c", "3", "timeout", "-s", "KILL", str(timeout),
           "bwrap", "--die-with-parent", "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
           "--tmpfs", HOME, "--tmpfs", "/var/tmp", "--setenv", "HOME", "/var/tmp",
           PY, os.path.join(D, "worker.py"), engine, path]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode == 0 and r.stdout.strip():
        try:
            return json.loads(r.stdout.decode().strip().splitlines()[-1])
        except json.JSONDecodeError:
            pass
    reason = {137: "killed (timeout or memory cap)", 124: "timeout"}.get(r.returncode, f"exit {r.returncode}")
    return {"engine": engine, "ok": False, "error": reason + ": " + r.stderr.decode(errors="replace")[-200:],
            "seconds": None, "maxrss_kb": None}


for engine in ENGINES:
    out = {}
    for f in FILES:
        rel = os.path.relpath(f, D)
        out[rel] = run(engine, f)
        status = "ok" if out[rel]["ok"] else "FAIL " + out[rel]["error"][:60]
        print(engine, rel, status, out[rel].get("seconds"), flush=True)
    json.dump(out, open(f"{D}/results/pdf-{engine}.json", "w"))
