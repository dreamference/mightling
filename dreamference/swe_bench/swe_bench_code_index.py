"""
The code index as an arm of the benchmark: `swe-bench run --code-index universal`
(specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §13).

Without it the agent in a container navigates with `grep` and `find` only: the runtime carries
`ling` and nothing of `ling-code`. With it, each instance's repository is indexed **on the
host** before the agent starts, and the index is mounted read-only into the container:

- the repository is copied out of the instance image (`docker create` + `docker cp /testbed`),
  so the index is of exactly the tree the agent gets;
- `ling-code index --wait` builds it there, under its own admission against the host's memory
  budget and inside `mightling-index.slice`, like any other index run on this machine;
- queries only read (code-index spec: queries run inside the sandbox, indexing outside), so the
  container needs only the `ling-code` binary and four environment variables that tell it
  where the index is. A file the agent edits is then answered by text search, as on the host.

Two arms index. `universal` builds the codebase-memory graph alone (the exact indexers are made
"not installed"). `exact` builds the SCIP stores alone (scip-python, scip-typescript; the graph
is off through `layers = exact`) and the agent's `ling-code` answers from them, falling back
to text search where they have nothing. The exact layer was measured on one sympy instance on
2026-10-02: 17 minutes, 1.6-1.7 GiB per directory, and the `sympy/` package itself died of Node's
heap limit, which was Node's own default; scip-python's heap is now sized to its run's memory
cap, and the arm raises that cap. Every instance is a different commit, so nothing is shared
between instances, but each store is cached by repository and commit and a re-run is free.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime

# The arms `--code-index` accepts.
ARMS: Final[tuple] = ("off", "universal", "exact")

# The memory cap of one static exact run in the `exact` arm, MiB; ling-code gives Node three
# quarters of it as heap. Four times the default, for sympy's package (see the module docstring).
EXACT_RUN_CEILING_MB: Final[int] = 16 << 10

# Index attempts in the `exact` arm: a run the model kept busy past ling-code's wait is deferred,
# not failed, and the next attempt picks it up.
EXACT_ATTEMPTS: Final[int] = 3
EXACT_RETRY_PAUSE_S: Final[int] = 60

# Names a `ling-code` to use in place of the installed one (`host_binary`).
MIGHTLING_CODE_OVERRIDE_ENV: Final[str] = "DREAMFERENCE_SWE_BENCH_MIGHTLING_CODE"

# The MCP server's name in Codex's configuration, and the variables Codex must pass it; both as
# the launcher has them (`ling-rs/src/code_index.rs`, `MCP_SERVER` and `FORWARDED_ENV`).
MCP_SERVER: Final[str] = "ling_code"
MCP_FORWARDED_ENV: Final[List[str]] = [
    "MIGHTLING_CODE_ROOT", "MIGHTLING_CODE_STATE_DIR", "MIGHTLING_CODE_GRAPH_DB", "MIGHTLING_CODE_PROJECT",
    "MIGHTLING_CODE_TOOLS_DIR", "MIGHTLING_CODE_INDEXERS_DIR", "MIGHTLING_CODE_LAYERS", "CODEX_HOME", "DREAMFERENCE_CONFIG_PATH",
    "DREAMFERENCE_VLLM_HOST"]

# Where the relocated `ling-code` and the instance's index are mounted in the container.
CODE_MOUNT: Final[str] = "/opt/ling-code"
INDEX_MOUNT: Final[str] = "/mightling-index"

INDEX_TIMEOUT_S: Final[int] = 30 * 60
RECORD_NAME: Final[str] = "index.json"
STAMP_NAME: Final[str] = "source-hash"


class SweBenchCodeIndex:
    """Builds per-instance indexes on the host and describes how a container uses one."""

    # Seam: tests replace it so nothing in the suite runs the real `ling-code` or `patchelf`.
    execute: Callable[..., subprocess.CompletedProcess] = staticmethod(
        lambda command, **kwargs: subprocess.run(command, capture_output=True, text=True,
                                                 stdin=subprocess.DEVNULL, **kwargs))

    @classmethod
    def host_binary(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: The installed `ling-code` (beside `ling`), or None if absent; or
            the one `DREAMFERENCE_SWE_BENCH_MIGHTLING_CODE` names, to measure a build of it that
            is not installed (the arm then differs from the plain one in `ling-code` alone).
        """
        override = os.environ.get(MIGHTLING_CODE_OVERRIDE_ENV)
        if override:
            return override if os.path.exists(override) else None
        ling = SweBenchRuntime.installed_mightling()
        if not ling:
            return None
        path = os.path.join(os.path.dirname(os.path.realpath(ling)), "ling-code")
        return path if os.path.exists(path) else None

    # -- the binary for the container ----------------------------------------------------------

    @classmethod
    def runtime_dir(cls) -> Path:
        """
        Returns:
            Path: The relocated `ling-code`, kept apart from `ling`'s runtime so that
            runtime's hash, which a run's manifest pins, does not change when this one appears.
            A build named by `DREAMFERENCE_SWE_BENCH_MIGHTLING_CODE` gets a directory of its own,
            so preparing it never replaces the one a running arm has mounted.
        """
        name = "runtime-code-override" if os.environ.get(MIGHTLING_CODE_OVERRIDE_ENV) else "runtime-code"
        return swe_bench_settings.CACHE_DIR / name

    @classmethod
    def ensure_runtime(cls, patchelf: str) -> Optional[str]:
        """
        Builds the relocated `ling-code` unless the one on disk was made from the installed
        binary. Same treatment as `ling`: the instance images have an older glibc.

        Args:
            patchelf: The `patchelf` executable.

        Returns:
            Optional[str]: The SHA-256 of the installed `ling-code`, or None on failure.
        """
        import hashlib
        binary = cls.host_binary()
        if binary is None:
            print("❌ ling-code is not installed: run `ling-admin codex build` first.")
            return None
        wanted = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        target = cls.runtime_dir()
        try:
            if (target / STAMP_NAME).read_text().strip() == wanted:
                return wanted
        except OSError:
            pass
        staging = target.with_name(f".runtime-code.{os.getpid()}.tmp")
        shutil.rmtree(staging, ignore_errors=True)
        (staging / "bin").mkdir(parents=True)
        (staging / "lib").mkdir()
        try:
            loader, libraries = SweBenchRuntime.host_libraries(binary)
            for library in [loader, *libraries]:
                shutil.copy(os.path.realpath(library), staging / "lib" / os.path.basename(library))
            copy = staging / "bin" / "ling-code"
            shutil.copy(binary, copy)
            patched = cls.execute([patchelf, "--set-interpreter", f"{CODE_MOUNT}/lib/{os.path.basename(loader)}",
                                   "--set-rpath", f"{CODE_MOUNT}/lib", str(copy)])
            if patched.returncode != 0:
                raise ValueError(patched.stderr.strip()[-300:])
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            shutil.rmtree(staging, ignore_errors=True)
            print(f"❌ Could not build ling-code for the instance images: {error}")
            return None
        (staging / STAMP_NAME).write_text(wanted + "\n")
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
        return wanted

    # -- one instance's index ------------------------------------------------------------------

    @classmethod
    def index_dir(cls, row: Dict[str, Any], arm: str = "universal") -> Path:
        """
        Args:
            row: The instance's dataset row.
            arm: `universal` or `exact`: each has a cache of its own.

        Returns:
            Path: Where its index is cached, keyed by arm, repository and base commit.
        """
        repo = str(row["repo"]).replace("/", "__")
        return swe_bench_settings.CACHE_DIR / ("index-exact" if arm == "exact" else "index") \
            / f"{repo}@{str(row['base_commit'])[:16]}"

    @classmethod
    def record(cls, row: Dict[str, Any], arm: str = "universal") -> Optional[Dict[str, Any]]:
        """
        Args:
            row: The instance's dataset row.
            arm: `universal` or `exact`.

        Returns:
            Optional[Dict[str, Any]]: The cached index's record (`seconds`, `layers`, and for
            `universal` `project` and `graph_db`, for `exact` `stores`, `failed` and `peak_mb`;
            `tool_hash`), or None when no usable index is cached.
        """
        directory = cls.index_dir(row, arm)
        try:
            record = json.loads((directory / RECORD_NAME).read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        if arm == "exact":
            return record if record.get("layers") == ["exact"] else None
        if not (directory / "cbm" / str(record.get("graph_db"))).is_file():
            return None
        return record

    @classmethod
    def copy_testbed(cls, row: Dict[str, Any], image: str, checkout: Path) -> bool:
        """
        Copies the repository exactly as the agent will get it out of a container that never runs.

        Args:
            row: The instance's dataset row.
            image: The instance image, already present locally.
            checkout: Where the copy goes.

        Returns:
            bool: Whether it was copied (the reason is printed when not).
        """
        created = SweBenchDocker.run(["create", image], timeout=300)
        container = created.stdout.strip()
        if created.returncode != 0 or not container:
            print(f"⚠️  {row['instance_id']}: could not create a container to copy /testbed from")
            return False
        copied = SweBenchDocker.run(["cp", "-q", f"{container}:/testbed", str(checkout)], timeout=900)
        SweBenchDocker.run(["rm", "-f", container], timeout=120)
        if copied.returncode != 0:
            print(f"⚠️  {row['instance_id']}: could not copy /testbed out of {image}")
            return False
        return True

    @classmethod
    def ensure(cls, row: Dict[str, Any], image: str, arm: str = "universal") -> Optional[Dict[str, Any]]:
        """
        Builds the instance's index on the host unless one is cached.

        Args:
            row: The instance's dataset row.
            image: The instance image, already present locally.
            arm: `universal` or `exact`.

        Returns:
            Optional[Dict[str, Any]]: The index's record, with `cached` saying whether it was
            reused; None when it could not be built (the reason is printed). An `exact` index
            whose indexers failed still has a record, naming them: the agent then has the text
            search alone, and the run says so rather than leaving the instance out.
        """
        cached = cls.record(row, arm)
        if cached is not None:
            return dict(cached, cached=True)
        binary = cls.host_binary()
        if binary is None:
            print("❌ ling-code is not installed: run `ling-admin codex build` first.")
            return None
        if arm == "exact":
            return cls.ensure_exact(row, image, binary)
        directory = cls.index_dir(row)
        shutil.rmtree(directory, ignore_errors=True)
        for sub in ("state", "cbm", "no-indexers"):
            (directory / sub).mkdir(parents=True)
        checkout = directory / "testbed"
        started = time.time()
        if not cls.copy_testbed(row, image, checkout):
            return None
        environment = dict(
            os.environ,
            MIGHTLING_CODE_STATE_DIR=str(directory / "state"),
            CBM_CACHE_DIR=str(directory / "cbm"),
            # An empty indexers directory makes the exact (SCIP) indexers "not installed", which
            # is how this arm stays the universal layer only.
            MIGHTLING_CODE_INDEXERS_DIR=str(directory / "no-indexers"),
        )
        try:
            indexed = cls.execute([binary, "index", "--wait"], cwd=str(checkout), env=environment,
                                  timeout=INDEX_TIMEOUT_S)
            output, code = (indexed.stdout or "") + (indexed.stderr or ""), indexed.returncode
        except subprocess.TimeoutExpired:
            output, code = "timed out", 124
        (directory / "index.log").write_text(output)
        graphs = sorted(path.name for path in (directory / "cbm").glob("*.db") if path.name != "_config.db")
        shutil.rmtree(checkout, ignore_errors=True)  # queries read the container's own /testbed
        if code != 0 or not graphs:
            print(f"⚠️  {row['instance_id']}: ling-code index failed ({code}); see {directory / 'index.log'}")
            return None
        import hashlib
        record = {
            "instance_id": row["instance_id"], "repo": row["repo"], "base_commit": row["base_commit"],
            "layers": ["universal"], "graph_db": graphs[0], "project": graphs[0][:-len(".db")],
            "seconds": round(time.time() - started, 1),
            "bytes": sum(path.stat().st_size for path in directory.rglob("*") if path.is_file()),
            "tool_hash": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        }
        (directory / RECORD_NAME).write_text(json.dumps(record, indent=2) + "\n")
        return dict(record, cached=False)

    @classmethod
    def ensure_exact(cls, row: Dict[str, Any], image: str, binary: str) -> Optional[Dict[str, Any]]:
        """
        Builds the SCIP stores of one instance on the host, with the graph off.

        `ling-code index --wait` runs each static indexer in turn under the machine's admission
        rules; a run the model kept busy past its wait is deferred, and the next attempt picks it
        up. The record names every store built and every indexer that did not finish.

        Args:
            row: The instance's dataset row.
            image: The instance image, already present locally.
            binary: The `ling-code` that indexes.

        Returns:
            Optional[Dict[str, Any]]: The record; None only when /testbed could not be copied.
        """
        import hashlib
        directory = cls.index_dir(row, "exact")
        shutil.rmtree(directory, ignore_errors=True)
        for sub in ("state", "cbm"):
            (directory / sub).mkdir(parents=True)
        settings = directory / "settings.toml"
        settings.write_text(f'mightling_code_layers = "exact"\ncode_index_small_ceiling_mb = {EXACT_RUN_CEILING_MB}\n')
        checkout = directory / "testbed"
        started = time.time()
        if not cls.copy_testbed(row, image, checkout):
            return None
        environment = dict(
            os.environ,
            MIGHTLING_CODE_STATE_DIR=str(directory / "state"),
            CBM_CACHE_DIR=str(directory / "cbm"),
            MIGHTLING_CODE_LAYERS="exact",
            DREAMFERENCE_CONFIG_PATH=str(settings),
        )
        logs: List[str] = []
        runs: Dict[str, Any] = {}
        for attempt in range(1, EXACT_ATTEMPTS + 1):
            try:
                indexed = cls.execute([binary, "index", "--wait"], cwd=str(checkout), env=environment,
                                      timeout=INDEX_TIMEOUT_S)
                logs.append((indexed.stdout or "") + (indexed.stderr or ""))
            except subprocess.TimeoutExpired:
                logs.append("timed out")
            try:
                runs = json.loads((directory / "state" / "scip" / "manifest.json").read_text()).get("runs", {})
            except (OSError, ValueError):
                runs = {}
            if not any(str(entry.get("status", "")).startswith("deferred") for entry in runs.values()):
                break
            if attempt < EXACT_ATTEMPTS:
                cls.sleep(EXACT_RETRY_PAUSE_S)
        (directory / "index.log").write_text("\n".join(f"--- attempt {i + 1}\n{log}" for i, log in enumerate(logs)))
        shutil.rmtree(checkout, ignore_errors=True)  # queries read the container's own /testbed
        stores = sorted(key for key, entry in runs.items()
                        if entry.get("store") and (directory / "state" / "scip" / str(entry["store"])).is_file())
        failed = {key: str(entry.get("status")) for key, entry in runs.items() if key not in stores}
        record = {
            "instance_id": row["instance_id"], "repo": row["repo"], "base_commit": row["base_commit"],
            "layers": ["exact"], "stores": stores, "failed": failed,
            "peak_mb": max((int(entry.get("peak_rss_mb") or 0) for entry in runs.values()), default=0),
            "seconds": round(time.time() - started, 1),
            "bytes": sum(path.stat().st_size for path in directory.rglob("*") if path.is_file()),
            "tool_hash": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        }
        if not stores:
            print(f"⚠️  {row['instance_id']}: no SCIP store was built; the agent will have the text search alone "
                  f"(see {directory / 'index.log'})")
        elif failed:
            print(f"⚠️  {row['instance_id']}: {len(failed)} indexer run(s) did not finish: {', '.join(sorted(failed))}")
        (directory / RECORD_NAME).write_text(json.dumps(record, indent=2) + "\n")
        return dict(record, cached=False)

    # Seam: tests replace it so a retry does not wait.
    sleep: Callable[[float], None] = staticmethod(time.sleep)

    @classmethod
    def container_arguments(cls, row: Dict[str, Any], record: Dict[str, Any], arm: str = "universal") -> Dict[str, Any]:
        """
        Says how a container is given the index.

        Args:
            row: The instance's dataset row.
            record: The index's record.
            arm: `universal` or `exact`.

        Returns:
            Dict[str, Any]: `mounts` (`docker run -v` values, both read-only), `env` (what
            `ling-code` and the launcher read) and `path` (the directory to put on `PATH`).
        """
        if arm == "exact":
            return {
                "mounts": [f"{cls.runtime_dir()}:{CODE_MOUNT}:ro", f"{cls.index_dir(row, 'exact')}:{INDEX_MOUNT}:ro"],
                "env": {
                    "MIGHTLING_CODE_BIN": f"{CODE_MOUNT}/bin/ling-code",
                    "MIGHTLING_CODE_STATE_DIR": f"{INDEX_MOUNT}/state",
                    # No graph: every answer comes from the SCIP stores or the text search.
                    "MIGHTLING_CODE_LAYERS": "exact",
                },
                "path": f"{CODE_MOUNT}/bin",
                "config": cls.mcp_overrides(),
            }
        return {
            "mounts": [f"{cls.runtime_dir()}:{CODE_MOUNT}:ro", f"{cls.index_dir(row)}:{INDEX_MOUNT}:ro"],
            "env": {
                # The launcher appends `ling-code prompt-block` to the prompt when this names a file.
                "MIGHTLING_CODE_BIN": f"{CODE_MOUNT}/bin/ling-code",
                "MIGHTLING_CODE_STATE_DIR": f"{INDEX_MOUNT}/state",
                "MIGHTLING_CODE_GRAPH_DB": f"{INDEX_MOUNT}/cbm/{record['graph_db']}",
                # The graph names its project after the host path it was indexed at.
                "MIGHTLING_CODE_PROJECT": str(record["project"]),
            },
            "path": f"{CODE_MOUNT}/bin",
            "config": cls.mcp_overrides(),
        }

    @classmethod
    def mcp_overrides(cls) -> List[str]:
        """
        The `-c` overrides that declare `ling-code mcp` to Codex as a **required** server.

        The launcher declares it without `required`, and Codex then gives an optional server a
        short grace period before the first request and leaves its tools out if it is not up: in
        the first arm run with the tools (2026-10-03), with three containers starting at once,
        the model's first `code_search` came back "unsupported call: code_search" and it went
        back to grep for the rest of the task. A required server is waited for. The launcher
        adds its own declaration only when none is given, so this one is the whole declaration:
        the same command, arguments and forwarded variables (`ling-rs/src/code_index.rs`).

        Returns:
            List[str]: The `key=value` overrides, each to follow a `-c`.
        """
        key = f"mcp_servers.{MCP_SERVER}"
        forwarded = ", ".join(json.dumps(name) for name in MCP_FORWARDED_ENV)
        return [f"{key}.command={json.dumps(f'{CODE_MOUNT}/bin/ling-code')}",
                f'{key}.args=["mcp"]',
                f"{key}.env_vars=[{forwarded}]",
                f"{key}.required=true",
                f"{key}.startup_timeout_sec=120"]

    @classmethod
    def cached(cls) -> List[str]:
        """
        Returns:
            List[str]: The names of the cached indexes.
        """
        root = swe_bench_settings.CACHE_DIR / "index"
        return sorted(entry.name for entry in root.iterdir()) if root.is_dir() else []
