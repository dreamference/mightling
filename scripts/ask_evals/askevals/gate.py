"""The suite's hold on the model gate for its night slot (spec §6, §9.5).

While the hold lasts, the gate refuses every request but read-only probes, so a phone question
gets the bridge's "busy with a benchmark" answer instead of competing with the suite. The suite
itself reaches the engine on its own loopback port (`127.0.0.1:18000` behind the gate on 8000,
SWE_BENCH §18.1), so the record names a network no client is in (TEST-NET-1, RFC 5737). The
record is the one a SWE-bench run writes (`ModelGate.write_run`), refreshed every 15 s; a hold
whose process dies goes stale after the gate's `STALE_S` and the gate opens by itself.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Any, Final

HEARTBEAT_S: Final[float] = 15.0
NO_CLIENT_NETWORK: Final[str] = "192.0.2.0/24"


class GateHold:
    """Closes the gate for a `with` block; `gate` is the `ModelGate` class (a seam for tests)."""

    def __init__(self, label: str, total: int, gate: Any = None) -> None:
        if gate is None:
            from dreamference.vllm_server.model_gate import ModelGate  # noqa: PLC0415

            gate = ModelGate
        self.gate = gate
        self.label = label
        self.total = total
        self.done = 0
        self.id = secrets.token_hex(8)
        self.started = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="ask-evals-gate", daemon=True)

    def record(self) -> dict:
        elapsed = time.time() - self.started
        eta = int(elapsed / self.done * (self.total - self.done)) if self.done else None
        return {
            "run": "ask-evals",
            "id": self.id,
            "label": self.label,
            "networks": [NO_CLIENT_NETWORK],
            "heartbeat": time.time(),
            "pid": os.getpid(),
            "started": self.started,
            "done": self.done,
            "total": self.total,
            "eta_s": eta,
        }

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self.gate.write_run(self.record())
            except Exception:  # noqa: BLE001 - a heartbeat never takes the suite down
                pass

    def advance(self) -> None:
        self.done += 1

    def __enter__(self) -> GateHold:
        self.gate.write_run(self.record())
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(HEARTBEAT_S + 5)
        self.gate.clear_run(self.id)
