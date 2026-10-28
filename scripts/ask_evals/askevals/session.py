"""One scenario's session, run inside the jail (spec §5.2, §9): the stand-ins up, the model's
port joined to the socket outside, then each turn through `ling exec` (the first) or
`ling exec … resume --last` (the rest), with the harness's own bound on every turn.

    python3 -I session.py SCENARIO.json OUT_DIR --home CODEX_HOME --workspace DIR
                          [--model-socket PATH] [--ling ling]

Writes into OUT_DIR: `turns.jsonl` (one record per turn: exit status, seconds, why it was cut),
`events-<n>.jsonl` (each turn's `--json` stream), `web.jsonl` (the stand-ins' log). Standard
library only, so it runs with the system Python.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Final, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from askevals.forward import serve_tcp  # noqa: E402
from askevals.standins import StandIns  # noqa: E402

# The model server's address inside the jail.
MODEL_PORT: Final[int] = 8000
# The harness's own bound (spec §5.5): a turn that prints nothing for this long is cut.
SILENCE_S: Final[float] = 180.0
# And no turn runs longer than this unless the scenario says so.
TURN_LIMIT_S: Final[float] = 600.0


def _start_forwarder(socket_path: str) -> None:
    ready = threading.Event()

    def run() -> None:
        loop = asyncio.new_event_loop()
        event = asyncio.Event()

        async def go() -> None:
            task = asyncio.ensure_future(serve_tcp(MODEL_PORT, socket_path, event))
            await event.wait()
            ready.set()
            await task

        loop.run_until_complete(go())

    threading.Thread(target=run, daemon=True).start()
    if not ready.wait(10):
        raise SystemExit("the model forwarder did not start")


def command(ling: str, workspace: Path, turn: dict, first: bool, images: list[Path]) -> list[str]:
    """The `ling exec` command line for one turn."""
    options = [
        "exec",
        "--json",
        "-s",
        "workspace-write",
        "-C",
        str(workspace),
        "--skip-git-repo-check",
    ]
    tail = [turn["text"]] if first else ["resume", "--last", turn["text"]]
    for image in images:
        tail += ["-i", str(image)]
    prefix = []
    if turn.get("faketime"):
        prefix = ["faketime", "-f", str(turn["faketime"])]
    return [*prefix, ling, *options, *tail]


def run_turn(argv: list[str], env: dict, events: Path, limit_s: float, silence_s: float) -> dict:
    """Runs one turn, cutting it on silence or at its limit; returns its record."""
    start = time.time()
    last = [start]
    with events.open("w") as out:
        process = subprocess.Popen(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def read() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                last[0] = time.time()
                out.write(line)
                out.flush()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        cut: str | None = None
        while process.poll() is None:
            now = time.time()
            if now - start > limit_s:
                cut = "limit"
            elif now - last[0] > silence_s:
                cut = "silence"
            if cut:
                process.kill()
                break
            time.sleep(0.5)
        process.wait()
        reader.join(5)
    return {"rc": process.returncode, "seconds": round(time.time() - start, 1), "cut": cut}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario")
    parser.add_argument("out")
    parser.add_argument("--home", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model-socket")
    parser.add_argument("--ling", default="ling")
    parser.add_argument("--silence-s", type=float, default=SILENCE_S)
    args = parser.parse_args(argv)

    scenario = json.loads(Path(args.scenario).read_text())
    out, home, workspace = Path(args.out), Path(args.home), Path(args.workspace)
    out.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    if args.model_socket:
        _start_forwarder(args.model_socket)
    faketime_missing = any(t.get("faketime") for t in scenario["turns"]) and not shutil.which(
        "faketime"
    )
    with StandIns(scenario.get("web", {}), out / "web.jsonl") as web:
        env = {
            **os.environ,
            **web.environment(),
            "CODEX_HOME": str(home),
            "DREAMFERENCE_MIGHTLING_PROMPT": scenario.get("prompt", "ask"),
            "DREAMFERENCE_VLLM_HOST": f"http://127.0.0.1:{MODEL_PORT}",
        }
        assets = Path(args.scenario).parent
        with (out / "turns.jsonl").open("w") as log:
            for index, turn in enumerate(scenario["turns"]):
                if turn.get("faketime") and faketime_missing:
                    record = {"turn": index, "skipped": "faketime is not installed"}
                    log.write(json.dumps(record) + "\n")
                    return 3
                images = [assets / name for name in turn.get("images", [])]
                argv_ = command(args.ling, workspace, turn, index == 0, images)
                limit = float(turn.get("limit_s", scenario.get("turn_limit_s", TURN_LIMIT_S)))
                record = run_turn(argv_, env, out / f"events-{index}.jsonl", limit, args.silence_s)
                record["turn"] = index
                log.write(json.dumps(record) + "\n")
                log.flush()
                if record["rc"] not in (0, None) and record["cut"] is None:
                    break
    return 0


if __name__ == "__main__":
    sys.exit(main())
