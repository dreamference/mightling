"""
A SWE-bench run's hold on the model gate (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §18).

While a run holds it, the gate in front of the model server refuses every request that does not
come from the run's internal network. The hold writes the run's record for the gate (the
network's subnet, progress and ETA for the refusal's text) and keeps its heartbeat fresh from a
thread of its own; it removes the record when the run ends, and a run that dies without doing so
stops refreshing it, so the gate opens on its own `STALE_S` later.

It also records, in the run's directory (`gate.json`), whether a gate was in force and every
interval a pause (`ling-admin night pause`) let other requests through, so the report can say
which instances shared the model.
"""

import json
import os
import secrets
import statistics
import threading
import time
from typing import Any, Dict, List, Optional

from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.vllm_server.model_gate import ModelGate

HEARTBEAT_S = 15.0
GATE_RECORD = "gate.json"


class SweBenchGateHold:
    """Closes the gate for one run, for as long as the `with` block lasts."""

    # Seam the tests replace.
    gate = ModelGate

    def __init__(self, store: SweBenchRunStore, vllm_host: str, subnet: Optional[str],
                 label: Optional[str] = None) -> None:
        """
        Args:
            store: The run.
            vllm_host: This machine's model server, where the gate answers.
            subnet: The run's internal network; None leaves the gate open (nothing could tell
                the run's requests from the others).
            label: What the refusal calls the run (e.g. `night 1`); defaults to its name.
        """
        self.store = store
        self.vllm_host = vllm_host
        self.subnet = subnet
        self.label = label
        self.id = secrets.token_hex(8)
        self.total: Optional[int] = None
        self.instances: List[str] = []
        self.parallel = 1
        self.started = time.time()
        self.enforced = False
        self.ever_enforced = False
        self.live: Optional[Dict[str, Any]] = None
        self._pauses: Dict[float, float] = {}
        self._earlier: Dict[str, Any] = {"sessions": [], "pauses": []}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # -- the `with` block ----------------------------------------------------------------------

    def __enter__(self) -> "SweBenchGateHold":
        self.started = time.time()
        self._earlier = self.read(self.store)
        self.tick()
        self._thread = threading.Thread(target=self._beat, name="swe-gate-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=HEARTBEAT_S + 5)
        with self._lock:
            if self.subnet:
                self.gate.clear_run(self.id)
            self._persist(time.time(), final=True)

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self.tick()
            except Exception:  # A heartbeat must never take the run down; the next one tries again.
                pass

    # -- what the run tells the gate -----------------------------------------------------------

    def update(self, instances: List[str], parallel: int) -> None:
        """
        Gives the hold the run's instances and parallelism, once the manifest is known.

        Args:
            instances: The instances the run covers (excluded ones left out).
            parallel: How many run at once on this machine.
        """
        self.instances = list(instances)
        self.total = len(self.instances)
        self.parallel = max(1, int(parallel))
        self.tick()

    def progress(self) -> Dict[str, Any]:
        """
        Returns:
            Dict[str, Any]: `done`, `total` and `eta_s` (None until an instance has finished):
            the instances left times the median instance's time, over the parallelism.
        """
        if self.total is None:
            return {"done": None, "total": None, "eta_s": None}
        wanted = set(self.instances)
        finished = [i for i in self.store.finished() if i in wanted]
        states = self.store.states()
        walls = [states[i]["wall_s"] for i in finished if isinstance(states.get(i, {}).get("wall_s"), (int, float))]
        left = max(0, self.total - len(finished))
        eta = left * statistics.median(walls) / self.parallel if walls else None
        return {"done": len(finished), "total": self.total, "eta_s": None if eta is None else int(eta)}

    def tick(self) -> None:
        """Refreshes the run's record, asks the gate what it does, and notes a pause."""
        with self._lock:
            now = time.time()
            if self.subnet:
                record = {"run": self.store.name, "id": self.id, "label": self.label,
                          "networks": [self.subnet], "heartbeat": now, "pid": os.getpid(),
                          "started": self.started, **self.progress()}
                self.gate.write_run(record)
            self.live = self.gate.probe(self.vllm_host)
            self.enforced = bool(self.live and self.live.get("id") == self.id
                                 and self.live.get("state") in ("closed", "paused"))
            self.ever_enforced = self.ever_enforced or self.enforced
            pause = self.gate.pause_record() or {}
            since, until = pause.get("since"), pause.get("until")
            if isinstance(since, (int, float)) and isinstance(until, (int, float)) \
                    and until > self.started and since <= now:
                self._pauses[float(since)] = float(until)
            self._persist(now)

    def priority(self) -> bool:
        """
        Returns:
            bool: Whether the gate is refusing everyone but this run right now (in force and not
            paused): the run then neither waits for open sessions nor for other requests.
        """
        live = self.live or {}
        return self.enforced and live.get("state") == "closed"

    def paused(self) -> bool:
        """
        Returns:
            bool: Whether a pause is letting other requests through right now.
        """
        return self.enforced and (self.live or {}).get("state") == "paused"

    # -- the run's record of it ----------------------------------------------------------------

    @classmethod
    def read(cls, store: SweBenchRunStore) -> Dict[str, Any]:
        """
        Args:
            store: A run.

        Returns:
            Dict[str, Any]: Its `gate.json`: `sessions` (each `start`, `end`, `gate`) and
            `pauses` (each `start`, `end`), in epoch seconds; empty lists for a run without one.
        """
        try:
            record = json.loads((store.directory / GATE_RECORD).read_text())
        except (OSError, ValueError):
            record = {}
        if not isinstance(record, dict):
            record = {}
        return {"sessions": list(record.get("sessions") or []), "pauses": list(record.get("pauses") or [])}

    def _persist(self, now: float, final: bool = False) -> None:
        if not self.store.manifest_path.is_file():
            return  # A run that never started (admission refused it) gets no directory from this.
        if self.subnet is None:
            gate = "open: the run's network has no subnet to tell its requests apart"
        else:
            gate = "in force" if self.ever_enforced else "absent"
        session = {"start": self.started, "end": now, "gate": gate, "ended": final}
        pauses = [{"start": max(since, self.started), "end": min(until, now)}
                  for since, until in sorted(self._pauses.items()) if min(until, now) > max(since, self.started)]
        record = {"sessions": self._earlier["sessions"] + [session],
                  "pauses": self._earlier["pauses"] + pauses}
        path = self.store.directory / GATE_RECORD
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{GATE_RECORD}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(record, indent=2) + "\n")
        os.replace(staging, path)
