"""T0 for the instruction-following sets (specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §10.3, §10.5).

    python setup_cache.py ifeval [--cache DIR]      IFEval's scorer and data, IFBench, NLTK's punkt
    python setup_cache.py polyglot [--no-build]     Aider Polyglot's exercises and its arm64 image

Everything goes into the cache (`~/.cache/dreamference/industry/` by default), never into the
repository: the scorers' code is fetched at pinned commits, the data at pinned revisions, and a
virtualenv of its own holds their dependencies at pinned versions. `manifest.json` records each
source's URL, revision and licence, which every run copies into its result (the owner's D4: data
is used for internal measurement only, never redistributed or committed). After this step the
scorers run with no network.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Final

DEFAULT_CACHE: Final[Path] = Path.home() / ".cache" / "dreamference" / "industry"

# IFEval: google-research's folder at the last commit that touched it (checked 2026-10-21).
IFEVAL_COMMIT: Final[str] = "e6890f85757dd84e27ca6df2dd30651dafad28e0"
IFEVAL_RAW: Final[str] = (
    "https://raw.githubusercontent.com/google-research/google-research/"
    f"{IFEVAL_COMMIT}/instruction_following_eval/"
)
IFEVAL_FILES: Final[tuple[str, ...]] = (
    "__init__.py",
    "evaluation_main.py",
    "evaluation_lib.py",
    "instructions.py",
    "instructions_registry.py",
    "instructions_util.py",
    "data/input_data.jsonl",
)
# IFBench: the repository at a pinned commit, as a tarball (its test set and run_eval.py).
IFBENCH_COMMIT: Final[str] = "1c40f0c10d9b5c5c2f10a175a28007ebb64f7f4d"
IFBENCH_TARBALL: Final[str] = f"https://codeload.github.com/allenai/IFBench/tar.gz/{IFBENCH_COMMIT}"
# The scorers' dependencies. IFEval's requirements.txt names four, unpinned; IFBench's
# pyproject.toml names them and more, and its uv.lock pins every one, so the versions come from
# that lock at the pinned commit (`pins_from_lock`).
SCORER_PACKAGES: Final[tuple[str, ...]] = (
    "absl-py",
    "langdetect",
    "nltk",
    "immutabledict",
    "emoji",
    "syllapy",
    "httpx",
    "tqdm",
    "pydantic",
    "pydantic-settings",
)
# Aider Polyglot: the exercises at the last commit (checked 2026-10-21), and the image's tag.
POLYGLOT_COMMIT: Final[str] = "7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f"
POLYGLOT_TARBALL: Final[str] = (
    f"https://codeload.github.com/Aider-AI/polyglot-benchmark/tar.gz/{POLYGLOT_COMMIT}"
)
POLYGLOT_IMAGE: Final[str] = "mightling-polyglot:latest"
HERE: Final[Path] = Path(__file__).resolve().parent
MANIFEST: Final[dict] = {
    "ifeval": {
        "source": IFEVAL_RAW,
        "revision": IFEVAL_COMMIT,
        "licence": "Apache-2.0 (code, google-research; data, Hugging Face google/IFEval card)",
    },
    "ifbench": {
        "source": IFBENCH_TARBALL,
        "revision": IFBENCH_COMMIT,
        "licence": "Apache-2.0 (code, allenai/IFBench); test set card names no licence",
    },
    "nltk_punkt": {
        "source": "nltk.download('punkt', 'punkt_tab')",
        "licence": "Apache-2.0 (NLTK data)",
    },
    "polyglot": {
        "source": POLYGLOT_TARBALL,
        "revision": POLYGLOT_COMMIT,
        "licence": "MIT (the Exercism tracks cpp, go, java, javascript, python, rust); the polyglot-benchmark "
        "repository has no licence file and attributes the exercises to Exercism",
    },
}


def fetch(url: str, path: Path) -> Path:
    """Downloads `url` to `path` (a setup step: the runs themselves never reach the network)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as answer:
        path.write_bytes(answer.read())
    return path


def pins_from_lock(lock_text: str, names: tuple[str, ...] = SCORER_PACKAGES) -> list[str]:
    """`name==version` for each of `names`, read from a uv.lock; a name missing from it is an error."""
    import tomllib  # noqa: PLC0415

    versions = {
        p["name"]: p["version"]
        for p in tomllib.loads(lock_text).get("package", [])
        if "version" in p
    }
    missing = [name for name in names if name not in versions]
    if missing:
        raise SystemExit(f"uv.lock pins none of {missing}")
    return [f"{name}=={versions[name]}" for name in names]


def venv_python(cache: Path) -> Path:
    return cache / "venv" / "bin" / "python"


def setup_ifeval(cache: Path, run=subprocess.run, download=fetch) -> dict:
    """Fetches IFEval and IFBench, builds the scorers' virtualenv, downloads punkt."""
    root = cache / "ifeval" / "instruction_following_eval"
    for name in IFEVAL_FILES:
        download(IFEVAL_RAW + name, root / name)
    tarball = download(IFBENCH_TARBALL, cache / "ifbench.tar.gz")
    with tarfile.open(tarball) as archive:
        archive.extractall(cache, filter="data")
    unpacked = cache / f"IFBench-{IFBENCH_COMMIT}"
    target = cache / "ifbench"
    if unpacked.exists():
        if target.exists():
            subprocess.run(["rm", "-rf", str(target)], check=True)
        unpacked.rename(target)
    tarball.unlink(missing_ok=True)
    if not venv_python(cache).exists():
        run([sys.executable, "-m", "venv", str(cache / "venv")], check=True)
    pins = pins_from_lock((target / "uv.lock").read_text())
    run([str(venv_python(cache)), "-m", "pip", "install", "--quiet", *pins], check=True)
    nltk_data = cache / "nltk_data"
    run(
        [
            str(venv_python(cache)),
            "-c",
            f"import nltk; nltk.download('punkt', {str(nltk_data)!r}); nltk.download('punkt_tab', {str(nltk_data)!r})",
        ],
        check=True,
    )
    for key in ("ifeval", "ifbench", "nltk_punkt"):
        _manifest(cache, key)
    manifest = json.loads((cache / "manifest.json").read_text())
    manifest["packages"] = pins
    (cache / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _manifest(cache: Path, key: str) -> None:
    path = cache / "manifest.json"
    current = json.loads(path.read_text()) if path.exists() else {}
    current[key] = MANIFEST[key]
    path.write_text(json.dumps(current, indent=2) + "\n")


def setup_polyglot(cache: Path, build: bool = True, run=subprocess.run, download=fetch) -> Path:
    """Fetches the exercises into the cache and builds the image from a staged context."""
    tarball = download(POLYGLOT_TARBALL, cache / "polyglot.tar.gz")
    with tarfile.open(tarball) as archive:
        archive.extractall(cache, filter="data")
    target = cache / "polyglot-benchmark"
    unpacked = cache / f"polyglot-benchmark-{POLYGLOT_COMMIT}"
    if unpacked.exists():
        if target.exists():
            subprocess.run(["rm", "-rf", str(target)], check=True)
        unpacked.rename(target)
    tarball.unlink(missing_ok=True)
    _manifest(cache, "polyglot")
    if build:
        context = cache / "polyglot-context"
        if context.exists():
            subprocess.run(["rm", "-rf", str(context)], check=True)
        context.mkdir(parents=True)
        for name in ("Dockerfile", "test-js.sh", "test-cpp.sh"):
            (context / name).write_bytes((HERE / "polyglot" / name).read_bytes())
            (context / name).chmod(0o755)
        subprocess.run(["cp", "-a", str(target), str(context / "polyglot-benchmark")], check=True)
        run(["docker", "build", "-t", POLYGLOT_IMAGE, str(context)], check=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["ifeval", "polyglot"])
    parser.add_argument(
        "--no-build", action="store_true", help="polyglot: fetch the exercises only"
    )
    parser.add_argument(
        "--cache", default=os.environ.get("DREAMFERENCE_INDUSTRY_CACHE", str(DEFAULT_CACHE))
    )
    args = parser.parse_args(argv)
    if args.what == "polyglot":
        target = setup_polyglot(Path(args.cache), build=not args.no_build)
        print(
            f"✅ Aider Polyglot's exercises in {target}"
            + ("" if args.no_build else f", image {POLYGLOT_IMAGE}")
        )
        return 0
    manifest = setup_ifeval(Path(args.cache))
    print(
        f"✅ IFEval and IFBench ready in {args.cache} ({len(manifest['packages'])} packages pinned)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
