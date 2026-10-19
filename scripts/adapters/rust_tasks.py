#!/usr/bin/env python3
"""The Rust task pool: training candidates from public task sets, minus the evaluation set.

    python rust_tasks.py build --rebench <parquet> --smith <parquet> --multilingual <parquet> --out <dir>

Sources (Hugging Face datasets, revisions in the pipeline doc):

- `nebius/SWE-rebench-V2` (CC-BY-4.0): real merged pull requests with fail-to-pass tests; the Rust
  rows carry a per-task licence, an install recipe (`install_config`: Rust version, install
  commands, `test_cmd`, `parse_log_cargo`) and an LLM audit of the task (`meta.llm_metadata`).
  Kept: a permissive licence, no audit flag (B1-B6: ambiguous issue, tests checking more than
  the issue asks, ...), at most `--per-repo` tasks per repository.
- `SWE-bench/SWE-smith-rs`: synthetic bugs in 39 repositories, one environment per repository.
  Kept: repositories with a permissive licence (MPL/GPL ones are left out), at most `--per-repo`.

Always removed: every repository of the evaluation set, the Rust subset of
`SWE-bench/SWE-bench_Multilingual` (43 tasks in tokio, axum, bat, ruff, nushell, coreutils and
ripgrep). Removing the repositories, not only the 43 tasks, keeps their fixes out of training
through later snapshots of the same code. `select_trajectories.py overlap` then checks the pool
against the lists our Python nights score on.

Writes `eval-multilingual-rust.jsonl` (the held-out set, whole rows), `train-candidates.jsonl`
(SWE-bench-format rows plus `source`, `license`, `install_config` or `image_name`) and a summary.
Needs `pyarrow` (the SWE-bench harness's virtualenv has it). Nothing runs at import.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

EVAL_REPOS: Set[str] = {"tokio-rs/tokio", "tokio-rs/axum", "sharkdp/bat", "astral-sh/ruff", "nushell/nushell",
                        "uutils/coreutils", "burntsushi/ripgrep"}
PERMISSIVE: Set[str] = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "Unlicense", "ISC", "Zlib", "0BSD",
                        "CC0-1.0", "BSL-1.0"}

# SWE-smith-rs repositories and their licences (checked against SWE-rebench-V2's licence column where
# the repository appears there; the rest from the repositories' LICENSE files, to re-check before
# publishing). `None` = left out.
SMITH_LICENCES: Dict[str, Optional[str]] = {
    "parcel-bundler/lightningcss": "MPL-2.0", "chronotope/chrono": "MIT OR Apache-2.0",
    "burntsushi/ripgrep": "Unlicense OR MIT", "rust-lang/rustfmt": "Apache-2.0", "image-rs/image": "MIT OR Apache-2.0",
    "rust-lang/rust-clippy": "MIT OR Apache-2.0", "clap-rs/clap": "Apache-2.0", "serde-rs/json": "MIT OR Apache-2.0",
    "rust-itertools/itertools": "Apache-2.0", "rayon-rs/rayon": "Apache-2.0", "servo/html5ever": "MIT OR Apache-2.0",
    "mozilla/sccache": "Apache-2.0", "sharkdp/pastel": "MIT OR Apache-2.0", "rust-lang/mdbook": "MPL-2.0",
    "hyperium/hyper": "MIT", "orium/rpds": "MPL-2.0", "quinn-rs/quinn": "Apache-2.0",
    "burntsushi/rust-csv": "Unlicense OR MIT", "pemistahl/grex": "Apache-2.0",
    "marshallpierce/rust-base64": "MIT OR Apache-2.0", "o2sh/onefetch": "MIT", "bootandy/dust": "Apache-2.0",
    "dtolnay/semver": "MIT OR Apache-2.0", "anordal/shellharden": "MPL-2.0", "uuid-rs/uuid": "MIT OR Apache-2.0",
    "byron/dua-cli": "MIT", "serenity-rs/serenity": "ISC", "console-rs/indicatif": "MIT",
    "sharkdp/hexyl": "MIT OR Apache-2.0", "canop/broot": "MIT", "seanmonstar/reqwest": "Apache-2.0",
    "tealdeer-rs/tealdeer": "MIT OR Apache-2.0", "hatoo/oha": "MIT", "yoav-lavi/melody": "MIT",
    "http-rs/tide": "MIT OR Apache-2.0", "dtolnay/cxx": "Apache-2.0", "svenstaro/miniserve": "MIT",
    "orf/gping": "MIT", "seanmonstar/warp": "MIT",
}


def permissive(licence: Optional[str]) -> bool:
    """True when every alternative of an SPDX `A OR B` expression... at least one is permissive."""
    if not licence:
        return False
    return any(part.strip() in PERMISSIVE for part in licence.split(" OR "))


def read_parquet(path: Path, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    import pyarrow.parquet as pq

    return pq.read_table(str(path), columns=columns).to_pylist()


def as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            return list(json.loads(value))
        except json.JSONDecodeError:
            import ast

            return list(ast.literal_eval(value))
    return list(value or [])


def capped(rows: List[Dict[str, Any]], per_repo: int, seed: int) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    by_repo: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)
    out: List[Dict[str, Any]] = []
    for repo in sorted(by_repo):
        group = sorted(by_repo[repo], key=lambda r: r["instance_id"])
        rng.shuffle(group)
        out.extend(group[:per_repo])
    return out


def rebench_rows(path: Path, per_repo: int, seed: int) -> List[Dict[str, Any]]:
    rows = [r for r in read_parquet(path) if r["language"] == "rust"]
    kept = []
    for r in rows:
        flags = ((r.get("meta") or {}).get("llm_metadata") or {}).get("detected_issues") or {}
        if r["repo"].lower() in EVAL_REPOS or not permissive(r.get("license")) or any(flags.values()):
            continue
        kept.append({
            "instance_id": r["instance_id"], "repo": r["repo"], "base_commit": r["base_commit"],
            "created_at": str(r["created_at"]), "problem_statement": r["problem_statement"],
            "patch": r["patch"], "test_patch": r["test_patch"],
            "FAIL_TO_PASS": as_list(r["FAIL_TO_PASS"]), "PASS_TO_PASS": as_list(r["PASS_TO_PASS"]),
            "source": "nebius/SWE-rebench-V2", "license": r["license"], "image_name": r["image_name"],
            "install_config": r["install_config"],
            "difficulty": ((r.get("meta") or {}).get("llm_metadata") or {}).get("difficulty"),
        })
    return capped(kept, per_repo, seed)


def smith_rows(path: Path, per_repo: int, seed: int) -> List[Dict[str, Any]]:
    kept = []
    for r in read_parquet(path):
        # `swesmith/BurntSushi__ripgrep.3b7fd442` -> burntsushi/ripgrep at commit 3b7fd442
        owner_repo, _, commit = r["repo"].split("/", 1)[1].rpartition(".")
        upstream = owner_repo.replace("__", "/", 1).lower()
        licence = SMITH_LICENCES.get(upstream)
        if upstream in EVAL_REPOS or not permissive(licence):
            continue
        kept.append({
            "instance_id": r["instance_id"], "repo": upstream, "base_commit": commit,
            "problem_statement": r["problem_statement"], "patch": r["patch"],
            "FAIL_TO_PASS": as_list(r["FAIL_TO_PASS"]), "PASS_TO_PASS": as_list(r["PASS_TO_PASS"]),
            "source": "SWE-bench/SWE-smith-rs", "license": licence, "image_name": r["image_name"],
            "smith_repo": r["repo"],
        })
    return capped(kept, per_repo, seed)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--rebench", type=Path, required=True)
    build.add_argument("--smith", type=Path, required=True)
    build.add_argument("--multilingual", type=Path, required=True)
    build.add_argument("--per-repo", type=int, default=25)
    build.add_argument("--smith-per-repo", type=int, default=60)
    build.add_argument("--seed", type=int, default=20261019)
    build.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    held_out = [r for r in read_parquet(args.multilingual) if r["repo"].lower() in EVAL_REPOS]
    with open(args.out / "eval-multilingual-rust.jsonl", "w") as handle:
        for row in held_out:
            handle.write(json.dumps({k: (v if not hasattr(v, "isoformat") else str(v)) for k, v in row.items()}) + "\n")
    rebench = rebench_rows(args.rebench, args.per_repo, args.seed)
    smith = smith_rows(args.smith, args.smith_per_repo, args.seed)
    pool = rebench + smith
    held_ids = {r["instance_id"] for r in held_out}
    pool = [r for r in pool if r["instance_id"] not in held_ids]
    with open(args.out / "train-candidates.jsonl", "w") as handle:
        for row in pool:
            handle.write(json.dumps(row, default=str) + "\n")
    summary = {
        "eval_multilingual_rust": len(held_out),
        "eval_repos": dict(collections.Counter(r["repo"] for r in held_out)),
        "rebench": len(rebench), "rebench_repos": len({r["repo"] for r in rebench}),
        "smith": len(smith), "smith_repos": len({r["repo"] for r in smith}),
        "pool": len(pool),
        "licences": dict(collections.Counter(r["license"] for r in pool).most_common()),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
