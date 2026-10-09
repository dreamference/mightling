"""
The host's side of the model gate (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §18).

`ling-admin server start` puts the engine on loopback at an internal port and starts the gate, a
container of its own in the engine's image, on the public port: every client, local, in a
container or on the LAN, reaches the model through it. This class starts and stops that
container, writes the two files it reads (a SWE-bench run's record, and a pause), and asks a
running gate what it is doing. The proxy itself is `model_gate_service.py`.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from dreamference.vllm_server.model_gate_service import (
    PAUSE_FILE,
    PROBE_PATH,
    RUN_FILE,
    ModelGateService,
)

# Where the gate's state lives on the host; mounted read-only into its container.
GATE_DIR: Final[Path] = Path(os.path.expanduser("~/.local/state/dreamference/model-gate"))
CONTAINER_STATE_DIR: Final[str] = "/mightling-gate"
SERVICE_SOURCE: Final[Path] = Path(__file__).with_name("model_gate_service.py")
SERVICE_COPY: Final[str] = "model_gate_service.py"

# The engine's port is the public one plus this, on loopback only (8000 -> 18000).
ENGINE_PORT_OFFSET: Final[int] = 10000
ENGINE_BIND_ADDRESS: Final[str] = "127.0.0.1"
PUBLIC_BIND_ADDRESS: Final[str] = "0.0.0.0"

GATE_MEMORY: Final[str] = "256m"
GATE_CPUS: Final[str] = "2"
DEFAULT_PAUSE_S: Final[int] = 3600
# How long a started gate has to answer its probe before the engine serves the port itself.
START_WAIT_S: Final[float] = 10.0


class ModelGate:
    """Lifecycle and state of the gate in front of the model server."""

    # Seam the tests replace.
    sleep = staticmethod(time.sleep)

    @classmethod
    def engine_port(cls, port: int) -> int:
        """
        Args:
            port: The model server's public port.

        Returns:
            int: The engine's internal port, on loopback.
        """
        return port + ENGINE_PORT_OFFSET if port + ENGINE_PORT_OFFSET <= 65535 else port - ENGINE_PORT_OFFSET

    @classmethod
    def container_name(cls, port: int) -> str:
        """
        Args:
            port: The model server's public port.

        Returns:
            str: The gate's container.
        """
        return f"dreamference-gate-{port}"

    @classmethod
    def run_command(cls, port: int, docker_image: str) -> List[str]:
        """
        The gate's `docker run`: host network (it serves the public port as the engine did),
        restarted with the engine's policy, small, as the user, its state read-only.

        Args:
            port: The public port.
            docker_image: The engine's image (it has a Python; nothing is pulled for the gate).

        Returns:
            List[str]: The command.
        """
        return [
            "docker", "run", "-d",
            "--name", cls.container_name(port),
            "--network", "host",
            "--restart", "unless-stopped",
            f"--memory={GATE_MEMORY}", f"--memory-swap={GATE_MEMORY}",
            f"--cpus={GATE_CPUS}",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--label", "ling.model-gate=1",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{GATE_DIR}:{CONTAINER_STATE_DIR}:ro",
            "--entrypoint", "python3",
            docker_image,
            f"{CONTAINER_STATE_DIR}/{SERVICE_COPY}",
            "--listen", f"{PUBLIC_BIND_ADDRESS}:{port}",
            "--upstream", f"{ENGINE_BIND_ADDRESS}:{cls.engine_port(port)}",
            "--state", CONTAINER_STATE_DIR,
        ]

    @classmethod
    def start(cls, port: int, docker_image: str) -> bool:
        """
        Replaces the gate's container. The service is copied beside its state first, so the
        container never mounts a path inside an installation a later upgrade removes.

        Args:
            port: The public port.
            docker_image: The engine's image.

        Returns:
            bool: Whether the container started.
        """
        GATE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SERVICE_SOURCE, GATE_DIR / SERVICE_COPY)
        cls.remove(port)
        result = subprocess.run(cls.run_command(port, docker_image), stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        if result is None or result.returncode != 0:
            detail = (getattr(result, "stderr", "") or "").strip()[:300]
            print(f"⚠️  The model gate did not start{': ' + detail if detail else ''}")
            return False
        # A container that started is not a gate that answers (the port may be held, the copy
        # unreadable): one that crash-looped would leave the engine, on loopback, unreachable.
        deadline = time.time() + START_WAIT_S
        while time.time() < deadline:
            if cls.probe(f"http://127.0.0.1:{port}", timeout=1.0) is not None:
                return True
            cls.sleep(0.25)
        logs = subprocess.run(["docker", "logs", "--tail", "5", cls.container_name(port)],
                              capture_output=True, text=True)
        detail = " ".join(((getattr(logs, "stdout", "") or "") + (getattr(logs, "stderr", "") or "")).split())[-300:]
        print(f"⚠️  The model gate started but did not answer on port {port}{': ' + detail if detail else ''}")
        cls.remove(port)
        return False

    @classmethod
    def stop(cls, port: int) -> None:
        """Stops the gate's container, if any."""
        subprocess.run(["docker", "stop", cls.container_name(port)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    @classmethod
    def remove(cls, port: int) -> None:
        """Removes the gate's container, if any."""
        subprocess.run(["docker", "rm", "-f", cls.container_name(port)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    # -- what the gate is doing ----------------------------------------------------------------

    @classmethod
    def probe(cls, host: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        """
        Asks a model server's gate what it is doing.

        Args:
            host: The model server's base URL.
            timeout: Seconds to wait.

        Returns:
            Optional[Dict[str, Any]]: `ModelGateService.state()`'s answer; None when there is no
            gate in front of that server (an engine answers the path 404) or it does not answer.
        """
        try:
            with urllib.request.urlopen(f"{host.rstrip('/')}{PROBE_PATH}", timeout=timeout) as response:
                record = json.load(response)
        except (OSError, ValueError, urllib.error.URLError):
            return None
        return record if isinstance(record, dict) and record.get("gate") == "mightling" else None

    @classmethod
    def state(cls, now: Optional[float] = None) -> Dict[str, Any]:
        """
        Returns:
            Dict[str, Any]: What a gate would do now with this machine's files.
        """
        return ModelGateService.state(str(GATE_DIR), now)

    # -- the run's record ----------------------------------------------------------------------

    @classmethod
    def write_run(cls, record: Dict[str, Any]) -> None:
        """
        Replaces the run's record (whole, so the gate never reads half of one).

        Args:
            record: `run`, `id`, `label`, `networks`, `done`, `total`, `eta_s`, `heartbeat`, `pid`.
        """
        cls._write(GATE_DIR / RUN_FILE, record)

    @classmethod
    def run_record(cls) -> Optional[Dict[str, Any]]:
        """
        Returns:
            Optional[Dict[str, Any]]: The run's record as written, stale or not.
        """
        return ModelGateService.read_json(str(GATE_DIR / RUN_FILE))

    @classmethod
    def clear_run(cls, hold_id: str) -> None:
        """
        Opens the gate: removes the run's record if it is still this hold's.

        Args:
            hold_id: The id the hold wrote.
        """
        record = cls.run_record()
        if record is not None and record.get("id") != hold_id:
            return
        try:
            (GATE_DIR / RUN_FILE).unlink()
        except OSError:
            pass

    # -- pause ---------------------------------------------------------------------------------

    @classmethod
    def pause_record(cls) -> Optional[Dict[str, Any]]:
        """
        Returns:
            Optional[Dict[str, Any]]: `since` and `until` (epoch seconds) of the last pause.
        """
        return ModelGateService.read_json(str(GATE_DIR / PAUSE_FILE))

    @classmethod
    def paused_until(cls, now: Optional[float] = None) -> Optional[float]:
        """
        Returns:
            Optional[float]: When the pause in force ends; None when none is.
        """
        now = time.time() if now is None else now
        record = cls.pause_record() or {}
        until = record.get("until")
        return float(until) if isinstance(until, (int, float)) and until > now else None

    @classmethod
    def pause(cls, seconds: float, now: Optional[float] = None) -> Dict[str, Any]:
        """
        Lets every request through for a while. A pause given while one is in force extends it
        and keeps its start, so a run records one interval.

        Args:
            seconds: How long, from now.
            now: The time; defaults to the clock.

        Returns:
            Dict[str, Any]: The pause written (`since`, `until`).
        """
        now = time.time() if now is None else now
        current = cls.pause_record() or {}
        since = current.get("since") if cls.paused_until(now) is not None else None
        record = {"since": since if isinstance(since, (int, float)) else now, "until": now + seconds}
        cls._write(GATE_DIR / PAUSE_FILE, record)
        return record

    @classmethod
    def resume(cls, now: Optional[float] = None) -> bool:
        """
        Ends the pause in force (its end is kept as now, so a run records the real interval).

        Returns:
            bool: Whether a pause was in force.
        """
        now = time.time() if now is None else now
        if cls.paused_until(now) is None:
            return False
        record = dict(cls.pause_record() or {})
        record["until"] = now
        cls._write(GATE_DIR / PAUSE_FILE, record)
        return True

    # -- telling the user ----------------------------------------------------------------------

    @classmethod
    def describe(cls, host: str) -> List[str]:
        """
        Lines for `night status`, `night pause` and `swe-bench status`.

        Args:
            host: This machine's model server.

        Returns:
            List[str]: What the gate is doing, and whether one is in front of the server.
        """
        live = cls.probe(host)
        state = live if live is not None else cls.state()
        lines = []
        if state.get("state") == "closed":
            lines.append(f"Model gate: closed: {state.get('message')}")
        elif state.get("state") == "paused":
            lines.append(f"Model gate: paused until {time.strftime('%H:%M', time.localtime(state['paused_until']))}; "
                         f"run {state.get('run')} waits while others use the model "
                         "(`ling-admin night resume` ends the pause)")
        else:
            until = state.get("paused_until")
            lines.append("Model gate: open (no benchmark run holds it)"
                         + (f"; a pause is in force until {time.strftime('%H:%M', time.localtime(until))}"
                            if until else ""))
            if state.get("stale"):
                lines.append(f"   Run {state['stale']} left a record whose heartbeat stopped; it is ignored.")
        if live is None:
            lines.append(f"   No gate answers at {host}: the model server was started without one "
                         "(it comes with the next `ling-admin server start`), so a benchmark run waits "
                         "for other requests instead of refusing them.")
        return lines

    @classmethod
    def _write(cls, path: Path, record: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(record, indent=2) + "\n")
        os.chmod(staging, 0o644)
        os.replace(staging, path)
