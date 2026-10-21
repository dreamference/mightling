"""T0 and T3: Terminal-Bench 2.1 with `ling`, offline, on arm64 (specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §10.1).

    python tbench.py setup              Harbor in its own virtualenv; the tasks at a pinned commit
    python tbench.py build [--only a,b] each task's image for this machine, with the verifier pre-baked
    python tbench.py prepare            the offline task set: local images and network policies
    python tbench.py subset             draws the fixed 30-task subset, once, from the tasks that built
    python tbench.py run [--subset] [--include a,b] [--parallel 3] [--hold-gate LABEL]

Why each step (all checked 2026-10-20/21):
- **build.** Every published image is amd64-only and this machine has no x86 emulation, so each
  task is built here from its `environment/Dockerfile` (multi-arch bases). Every verifier installs
  curl, uv 0.9.5, Python 3.13 and pinned pytest from the internet when it grades; a second layer
  does that once at build time, so grading needs no network.
- **prepare.** The owner's D7: offline. Each task's copy names its local image and replaces
  `allow_internet = true` with Harbor's network policy: the agent's phase may reach only the
  model server's address (`allowlist`), the verifier nothing (`no-network`). A task that needs the
  internet to be solved fails offline; such tasks are listed in `needs-internet.txt` and scored
  apart.
- **run.** `harbor run -p <offline tasks> -a ling_agent:LingAgent`, the agent of `tbench/`, with
  SWE-bench's runtime copy of `ling` and the model at the Docker bridge's address.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Final

HERE: Final[Path] = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ask_evals"))

from setup_cache import DEFAULT_CACHE, fetch  # noqa: E402

TB_COMMIT: Final[str] = "7131e4375048a0e408a8fb404b5f499d726b695b"
TB_TARBALL: Final[str] = (
    f"https://codeload.github.com/harbor-framework/terminal-bench-2-1/tar.gz/{TB_COMMIT}"
)
HARBOR_VERSION: Final[str] = "0.24.0"
IMAGE_PREFIX: Final[str] = "mightling-tb21"
SUBSET_SIZE: Final[int] = 30
SUBSET_SEED: Final[int] = 20261021
# Every Docker network's addresses: the agent's phase may reach the model server there and nowhere else.
DOCKER_RANGE: Final[str] = "172.16.0.0/12"
VERIFIER_LAYER: Final[str] = """FROM {base}
USER root
RUN (apt-get update && apt-get install -y curl) || true
RUN curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh
RUN /root/.local/bin/uv python install 3.13 \\
 && /root/.local/bin/uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 pytest --version
# The verifier's `uvx` then resolves from this cache alone, with no index to ask.
ENV UV_OFFLINE=1
"""
MANIFEST: Final[dict] = {
    "source": TB_TARBALL,
    "revision": TB_COMMIT,
    "licence": "Apache-2.0 (harbor-framework/terminal-bench-2-1; harbor itself Apache-2.0)",
    "harbor": HARBOR_VERSION,
}


def paths(cache: Path) -> dict[str, Path]:
    root = cache / "tbench"
    return {
        "root": root,
        "tasks": root / "terminal-bench-2-1" / "tasks",
        "offline": root / "offline-tasks",
        "venv": root / "harbor-venv",
        "built": root / "built.json",
        "subset": root / "subset-30.txt",
        "internet": root / "needs-internet.txt",
    }


def task_names(tasks: Path) -> list[str]:
    return sorted(p.name for p in tasks.iterdir() if (p / "task.toml").exists())


def setup(cache: Path, run=subprocess.run, download=fetch) -> None:
    p = paths(cache)
    p["root"].mkdir(parents=True, exist_ok=True)
    tarball = download(TB_TARBALL, p["root"] / "tb21.tar.gz")
    with tarfile.open(tarball) as archive:
        archive.extractall(p["root"], filter="data")
    unpacked = p["root"] / f"terminal-bench-2-1-{TB_COMMIT}"
    target = p["root"] / "terminal-bench-2-1"
    if unpacked.exists():
        shutil.rmtree(target, ignore_errors=True)
        unpacked.rename(target)
    tarball.unlink(missing_ok=True)
    if not (p["venv"] / "bin" / "python").exists():
        run([sys.executable, "-m", "venv", str(p["venv"])], check=True)
    run(
        [
            str(p["venv"] / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--quiet",
            f"harbor=={HARBOR_VERSION}",
        ],
        check=True,
    )
    (p["root"] / "manifest.json").write_text(json.dumps(MANIFEST, indent=2) + "\n")


def docker_image_of(toml_text: str) -> str | None:
    match = re.search(r'^docker_image\s*=\s*"([^"]+)"', toml_text, re.M)
    return match.group(1) if match else None


def build(cache: Path, only: list[str] | None, run=subprocess.run) -> dict:
    """Builds each task's image and its verifier layer; returns {task: "ok" | why it failed}."""
    p = paths(cache)
    results = json.loads(p["built"].read_text()) if p["built"].exists() else {}
    for name in only or task_names(p["tasks"]):
        task = p["tasks"] / name
        env_tag, offline_tag = f"{IMAGE_PREFIX}/{name}:env", f"{IMAGE_PREFIX}/{name}:offline"
        first = run(
            ["docker", "build", "-t", env_tag, str(task / "environment")],
            capture_output=True,
            text=True,
            check=False,
        )
        if first.returncode != 0:
            results[name] = "environment: " + (first.stderr or first.stdout or "")[-300:]
        else:
            layer = p["root"] / "layers" / name
            layer.mkdir(parents=True, exist_ok=True)
            (layer / "Dockerfile").write_text(VERIFIER_LAYER.format(base=env_tag))
            second = run(
                ["docker", "build", "-t", offline_tag, str(layer)],
                capture_output=True,
                text=True,
                check=False,
            )
            results[name] = (
                "ok"
                if second.returncode == 0
                else "verifier layer: " + (second.stderr or "")[-300:]
            )
        p["built"].write_text(json.dumps(results, indent=2) + "\n")
    return results


def offline_toml(text: str, image: str, model_address: str) -> str:
    """A task's `task.toml` for D7: the local image, the model alone reachable, the verifier offline."""
    if docker_image_of(text):
        text = re.sub(r'^docker_image\s*=\s*"[^"]+"', f'docker_image = "{image}"', text, flags=re.M)
    else:
        text = re.sub(
            r"^\[environment\]\s*$",
            f'[environment]\ndocker_image = "{image}"',
            text,
            count=1,
            flags=re.M,
        )
    text = re.sub(r"^allow_internet\s*=.*\n", "", text, flags=re.M)
    policy = f'network_mode = "allowlist"\nallowed_hosts = ["{model_address}"]'
    text = re.sub(r"^\[environment\]\s*$", "[environment]\n" + policy, text, count=1, flags=re.M)
    if re.search(r"^\[verifier\]\s*$", text, re.M):
        text = re.sub(
            r"^\[verifier\]\s*$",
            '[verifier]\nnetwork_mode = "no-network"',
            text,
            count=1,
            flags=re.M,
        )
    else:
        text += '\n[verifier]\nnetwork_mode = "no-network"\n'
    return text


def prepare(cache: Path, model_address: str) -> list[str]:
    """Copies every task that built into the offline set, its `task.toml` rewritten."""
    p = paths(cache)
    built = json.loads(p["built"].read_text()) if p["built"].exists() else {}
    shutil.rmtree(p["offline"], ignore_errors=True)
    ready = []
    for name in task_names(p["tasks"]):
        if built.get(name) != "ok":
            continue
        target = p["offline"] / name
        shutil.copytree(p["tasks"] / name, target)
        toml = target / "task.toml"
        toml.write_text(
            offline_toml(toml.read_text(), f"{IMAGE_PREFIX}/{name}:offline", model_address)
        )
        ready.append(name)
    return ready


def draw_subset(cache: Path) -> list[str]:
    """The fixed 30-task subset, drawn once from the tasks that built and kept from then on."""
    p = paths(cache)
    if p["subset"].exists():
        return p["subset"].read_text().split()
    built = json.loads(p["built"].read_text())
    ok = sorted(name for name, state in built.items() if state == "ok")
    chosen = sorted(random.Random(SUBSET_SEED).sample(ok, min(SUBSET_SIZE, len(ok))))
    p["subset"].write_text("\n".join(chosen) + "\n")
    return chosen


def reward_of(result: dict) -> float | None:
    """A trial's reward from Harbor's `result.json` (its layout is read defensively)."""
    verifier = result.get("verifier_result") or {}
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    if isinstance(rewards, dict) and rewards:
        value = rewards.get("reward", next(iter(rewards.values())))
        return float(value) if isinstance(value, (int, float)) else None
    for key in ("reward", "score"):
        if isinstance(result.get(key), (int, float)):
            return float(result[key])
    return None


def summarise(job_dir: Path, needs_internet: set[str]) -> dict:
    """Resolved tasks, all and apart from the ones that need the internet (D7)."""
    trials = []
    for path in sorted(job_dir.rglob("result.json")):
        result = json.loads(path.read_text())
        name = result.get("task_name") or path.parent.name.split("__")[0]
        reward = reward_of(result)
        if reward is not None:
            trials.append({"task": name, "reward": reward})

    def rate(rows: list[dict]) -> dict:
        return {
            "tasks": len(rows),
            "resolved": sum(1 for r in rows if r["reward"] >= 1.0),
            "rate": round(100 * sum(1 for r in rows if r["reward"] >= 1.0) / max(1, len(rows)), 1),
        }

    offline = [t for t in trials if t["task"] not in needs_internet]
    return {
        "all": rate(trials),
        "offline_solvable": rate(offline),
        "needs_internet": rate([t for t in trials if t["task"] in needs_internet]),
        "trials": trials,
    }


def bridge_address(run=subprocess.run) -> str:
    """The host as containers reach it: the default Docker bridge's gateway."""
    done = run(
        [
            "docker",
            "network",
            "inspect",
            "bridge",
            "--format",
            "{{(index .IPAM.Config 0).Gateway}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() or "172.17.0.1"


def harbor_command(
    cache: Path, args: argparse.Namespace, names: list[str], model_address: str
) -> list[str]:
    p = paths(cache)
    command = [
        str(p["venv"] / "bin" / "harbor"),
        "run",
        "-p",
        str(p["offline"]),
        "-a",
        "ling_agent:LingAgent",
        "-m",
        "mightling/local",
        "-n",
        str(args.parallel),
        "-o",
        str(args.runs),
        "--ae",
        f"MIGHTLING_MODEL_HOST=http://{model_address}:8000",
        "--ae",
        f"LING_RUNTIME_DIR={args.runtime}",
    ]
    for name in names:
        command += ["-i", name]
    return command


def main(argv: list[str] | None = None, run=subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["setup", "build", "prepare", "subset", "run"])
    parser.add_argument(
        "--cache", default=os.environ.get("DREAMFERENCE_INDUSTRY_CACHE", str(DEFAULT_CACHE))
    )
    parser.add_argument("--only", help="build: comma-separated task names")
    parser.add_argument("--subset", action="store_true", help="run: the fixed 30-task subset")
    parser.add_argument("--include", help="run: comma-separated task names")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument(
        "--runs", default=str(DEFAULT_CACHE / "runs" / time.strftime("tbench-%Y%m%d-%H%M%S"))
    )
    parser.add_argument(
        "--runtime", help="SWE-bench's runtime copy of ling (default: its cache folder)"
    )
    parser.add_argument("--hold-gate", metavar="LABEL")
    args = parser.parse_args(argv)
    cache = Path(args.cache)
    p = paths(cache)
    if args.step == "setup":
        setup(cache, run=run)
        print(
            f"✅ Terminal-Bench 2.1 at {TB_COMMIT[:8]} and Harbor {HARBOR_VERSION} in {p['root']}"
        )
        return 0
    if args.step == "build":
        results = build(cache, args.only.split(",") if args.only else None, run=run)
        failed = {k: v for k, v in results.items() if v != "ok"}
        print(f"✅ {len(results) - len(failed)} built, ❌ {len(failed)} failed (see {p['built']})")
        return 0
    if args.step == "prepare":
        ready = prepare(cache, bridge_address(run))
        print(f"✅ {len(ready)} tasks ready offline in {p['offline']}")
        return 0
    if args.step == "subset":
        print("\n".join(draw_subset(cache)))
        return 0
    if not args.runtime:
        from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime  # noqa: PLC0415

        args.runtime = str(SweBenchRuntime.directory())
    names = draw_subset(cache) if args.subset else (args.include.split(",") if args.include else [])
    address = bridge_address(run)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    hold = None
    if args.hold_gate:
        from askevals.gate import GateHold  # noqa: PLC0415

        hold = GateHold(
            args.hold_gate,
            total=len(names) or len(task_names(p["offline"])),
            networks=[DOCKER_RANGE],
        ).__enter__()
    try:
        env = {**os.environ, "PYTHONPATH": str(HERE / "tbench")}
        run(harbor_command(cache, args, names, address), env=env, check=False)
    finally:
        if hold:
            hold.__exit__()
    needs = set(p["internet"].read_text().split()) if p["internet"].exists() else set()
    result = summarise(Path(args.runs), needs)
    result["manifest"] = MANIFEST
    Path(args.runs).mkdir(parents=True, exist_ok=True)
    (Path(args.runs) / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("all", "offline_solvable", "needs_internet")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
