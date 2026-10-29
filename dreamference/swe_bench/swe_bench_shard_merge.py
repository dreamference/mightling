"""`ling-admin swe-bench merge <name> <run>...`: a list's shards put back together as one run.

The runs of one list's shards (`run --shard I/K`, one per machine) become one run, which `report`,
`report --against` and every script that reads runs read like any other.

The merged run is a directory of its own beside the shards': their manifests made one (the instances,
images and exclusions joined, each shard named under `shards`), their instance states, agent logs and
predictions copied, each instance's scratch (where its rollouts are) linked, their model-gate records
joined, and each grading series' latest grading of every shard written as the merged run's grading 1,
with each verdict's test counts read into it, so nothing it reports depends on a shard's grading
directory. The shards are never changed, and a merged run only reports: `run` refuses it.

Shards are merged only when they measured the same thing: every manifest key but the ones that say
where and when a shard ran must agree, no instance may be in two shards, and the gradings of one
series must have the same grader. A shard made on another machine is copied into this machine's runs
directory first.
"""

import json
import shutil
from collections import Counter
from datetime import datetime
from typing import Any, Final

from dreamference.swe_bench.swe_bench_evaluator import VARIANTS, SweBenchEvaluator
from dreamference.swe_bench.swe_bench_gate_hold import GATE_RECORD, SweBenchGateHold
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.swe_bench.swe_bench_runner import RUN_NAME

# What may differ between shards: where and when each ran, and what it covered.
PER_SHARD_KEYS: Final[frozenset] = frozenset(
    {
        "name",
        "started",
        "instances",
        "images",
        "excluded",
        "stripped_issues",
        "parallelism",
        "runtime_hash",
        "shard",
    }
)
# The per-instance tables, joined.
JOINED_KEYS: Final[tuple] = ("images", "excluded", "stripped_issues")
# The test counts a verdict carries once read (`SweBenchEvaluator.test_counts`).
COUNT_KEYS: Final[tuple] = ("f2p", "f2p_failed", "p2p", "p2p_failed")
# A merge is of two runs or more.
MIN_RUNS: Final[int] = 2


class SweBenchShardMerge:
    """Merges the runs of a list's shards into one run."""

    @classmethod
    def merge(cls, name: str, runs: list[str]) -> int:
        """Merges the shards' runs into one new run named `name`.

        Args:
            name: The merged run's name (new).
            runs: The shards' runs.

        Returns:
            int: 0 when the merged run was written, 1 when the shards cannot be merged.
        """
        stores = [SweBenchRunStore(run) for run in runs]
        found = [store.manifest() for store in stores]
        problem = cls.problem(name, runs, found)
        if problem:
            print(f"❌ Not merged: {problem}")  # noqa: T201 — swe-bench merge's console output
            return 1
        manifests = [manifest or {} for manifest in found]
        gradings = cls.gradings(stores)
        if isinstance(gradings, str):
            print(f"❌ Not merged: {gradings}")  # noqa: T201 — swe-bench merge's console output
            return 1
        for note in cls.notes(stores, manifests):
            print(f"⚠️  {note}")  # noqa: T201 — swe-bench merge's console output
        target = SweBenchRunStore(name)
        target.write_manifest(cls.manifest(name, stores, manifests))
        for store in stores:
            cls.copy_files(store, target)
        target.predictions_path.write_text(
            "".join(
                json.dumps(prediction) + "\n"
                for store in stores
                for prediction in store.predictions()
            )
        )
        cls.join_gate_records(stores, target)
        for variant, record in gradings.items():
            target.write_grading(1, record, variant)
        instances = sum(len(manifest.get("instances", [])) for manifest in manifests)
        print(  # noqa: T201 — swe-bench merge's console output
            f"✅ {name}: {len(stores)} shard(s), {instances} instance(s), {len(target.predictions())} prediction(s); "
            f"`ling-admin swe-bench report {name}` prints the report."
        )
        return 0

    @classmethod
    def gradings(cls, stores: list[SweBenchRunStore]) -> dict[str | None, dict[str, Any]] | str:
        """Every grading series the shards have, each joined (`series`).

        Args:
            stores: The shards.

        Returns:
            dict[str | None, dict[str, Any]] | str: The joined record by series (None for the plain
            one), or a string saying why a series cannot be joined.
        """
        gradings: dict[str | None, dict[str, Any]] = {}
        for variant in (None, *VARIANTS):
            graded = cls.series(stores, variant)
            if isinstance(graded, str):
                return graded
            if graded is not None:
                gradings[variant] = graded
        return gradings

    @classmethod
    def join_gate_records(cls, stores: list[SweBenchRunStore], target: SweBenchRunStore) -> None:
        """Writes the shards' model gate sessions and pauses, each named after its shard, into the merged run."""
        gate: dict[str, list[dict[str, Any]]] = {"sessions": [], "pauses": []}
        for store in stores:
            record = SweBenchGateHold.read(store)
            for kind, entries in gate.items():
                entries += [dict(entry, run=store.name) for entry in record[kind]]
        if gate["sessions"] or gate["pauses"]:
            (target.directory / GATE_RECORD).write_text(json.dumps(gate, indent=2) + "\n")

    @classmethod
    def problem(
        cls, name: str, runs: list[str], manifests: list[dict[str, Any] | None]
    ) -> str | None:
        """Why the runs cannot be merged under `name`.

        Args:
            name: The merged run's name.
            runs: The shards' runs.
            manifests: Their manifests (None for a run that does not exist).

        Returns:
            str | None: Why they cannot be merged under that name, or None.
        """
        if not RUN_NAME.fullmatch(name):
            return f"{name!r} is not a run's name (letters, digits, '.', '_' and '-')."
        if SweBenchRunStore(name).manifest() is not None or name in runs:
            return f"a run named {name} exists already."
        if len(set(runs)) != len(runs) or len(runs) < MIN_RUNS:
            return "name two or more different runs."
        missing = [run for run, manifest in zip(runs, manifests, strict=True) if manifest is None]
        if missing:
            return (
                f"no such run: {', '.join(missing)} (a shard made elsewhere is copied into this machine's "
                "runs first)."
            )
        return cls.mismatch(runs, [manifest or {} for manifest in manifests])

    @classmethod
    def mismatch(cls, runs: list[str], manifests: list[dict[str, Any]]) -> str | None:
        """Why existing runs are not the shards of one configuration and one list.

        Args:
            runs: The shards' runs.
            manifests: Their manifests.

        Returns:
            str | None: The reason, or None.
        """
        merged = [
            run
            for run, manifest in zip(runs, manifests, strict=True)
            if manifest.get("merged_from")
        ]
        if merged:
            return f"{', '.join(merged)} is a merge already; merge the shards themselves."
        first = manifests[0]
        for run, manifest in zip(runs[1:], manifests[1:], strict=True):
            differ = sorted(
                key
                for key in set(first) | set(manifest)
                if key not in PER_SHARD_KEYS and first.get(key) != manifest.get(key)
            )
            if differ:
                return (
                    f"{run} measured something other than {runs[0]}: {', '.join(differ[:5])} differ "
                    "(a merged run measures one configuration)."
                )
        reason = cls.shard_mismatch([manifest.get("shard") for manifest in manifests])
        if reason:
            return reason
        counts = Counter(i for manifest in manifests for i in manifest.get("instances", []))
        twice = sorted(i for i, n in counts.items() if n > 1)
        if twice:
            return f"{len(twice)} instance(s) are in more than one run, e.g. {twice[0]}."
        return None

    @classmethod
    def shard_mismatch(cls, shards: list[dict[str, Any] | None]) -> str | None:
        """Why the runs' shard records do not make shards of one list.

        Args:
            shards: Each run's `shard` record, or None for a run made without `--shard`.

        Returns:
            str | None: The reason, or None.
        """
        present = [shard for shard in shards if shard]
        if not present:
            return None
        if len(present) != len(shards):
            return "some runs are shards (--shard) and some are not."
        if len({(shard.get("of"), shard.get("list_sha256")) for shard in present}) > 1:
            return "the runs are shards of different lists or of different counts."
        indexes = [shard.get("index") for shard in present]
        if len(set(indexes)) != len(indexes):
            return "two runs are the same shard."
        return None

    @classmethod
    def series(
        cls, stores: list[SweBenchRunStore], variant: str | None
    ) -> dict[str, Any] | str | None:
        """The merged grading of one series: each shard's latest grading in it, joined.

        Args:
            stores: The shards.
            variant: The grading series; None for the plain one.

        Returns:
            dict[str, Any] | str | None: The merged record (`{"grader", "results"}`), None when no shard has the series,
            or a string saying why the gradings cannot be joined.
        """
        record: dict[str, Any] | None = None
        for store in stores:
            number, grader, results = SweBenchEvaluator.results(store, variant)
            if number is None:
                continue
            if record is None:
                record = {"grader": grader, "results": {}}
            elif grader != record["grader"]:
                return (
                    f"{store.name}'s {variant or 'plain'} grading was made with another grader "
                    f"({json.dumps(grader, sort_keys=True)}); grade the shards alike first."
                )
            models = {
                p["instance_id"]: p.get("model_name_or_path", "") for p in store.predictions()
            }
            for instance_id, result in results.items():
                record["results"][instance_id] = cls.verdict(
                    store, number, variant, result, instance_id, str(models.get(instance_id) or "")
                )
        return record

    @classmethod
    def verdict(
        cls,
        store: SweBenchRunStore,
        number: int,
        variant: str | None,
        result: dict[str, Any],
        instance_id: str,
        model: str,
    ) -> dict[str, Any]:
        """One verdict as the merged run keeps it.

        Its test counts are read in from the shard's harness logs when the record lacks them, and a
        reused plain verdict points at the merged run's plain grading, which is number 1.

        Returns:
            dict[str, Any]: The verdict.
        """
        merged = dict(result)
        if not all(key in merged for key in COUNT_KEYS):
            counts = SweBenchEvaluator.logged_test_counts(
                store, number, variant, result, instance_id, model
            )
            if counts is not None:
                merged.update(counts)
        if str(merged.get("reused") or "").startswith("plain grading "):
            merged["reused"] = "plain grading 1"
        return merged

    @classmethod
    def earliest(cls, times: list[Any]) -> str:
        """The first of the shards' start times, by the instant, not the text.

        Args:
            times: Their `started` values: ISO times, each with its machine's offset, or None.

        Returns:
            str: The earliest as it was written; empty when none can be read.
        """
        read: list[tuple[datetime, str]] = []
        for text in times:
            try:
                read.append((datetime.fromisoformat(str(text)).astimezone(), str(text)))
            except ValueError:
                continue
        return min(read)[1] if read else ""

    @classmethod
    def manifest(
        cls, name: str, stores: list[SweBenchRunStore], manifests: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """The merged run's manifest.

        Returns:
            dict[str, Any]: The first shard's manifest, with the instances and the per-instance
            tables joined, the largest parallelism, and every shard named.
        """
        merged = dict(manifests[0])
        merged["name"] = name
        merged["started"] = cls.earliest([manifest.get("started") for manifest in manifests])
        merged["instances"] = sorted(
            i for manifest in manifests for i in manifest.get("instances", [])
        )
        for key in JOINED_KEYS:
            if any(key in manifest for manifest in manifests):
                merged[key] = {
                    k: v for manifest in manifests for k, v in (manifest.get(key) or {}).items()
                }
        merged["parallelism"] = max(int(manifest.get("parallelism") or 0) for manifest in manifests)
        hashes = {manifest.get("runtime_hash") for manifest in manifests}
        merged["runtime_hash"] = hashes.pop() if len(hashes) == 1 else "several (see shards)"
        merged.pop("shard", None)
        merged["merged_from"] = [store.name for store in stores]
        merged["shards"] = [
            {
                "run": store.name,
                "shard": manifest.get("shard"),
                "started": manifest.get("started"),
                "instances": len(manifest.get("instances", [])),
                "parallelism": manifest.get("parallelism"),
                "runtime_hash": manifest.get("runtime_hash"),
            }
            for store, manifest in zip(stores, manifests, strict=True)
        ]
        return merged

    @classmethod
    def copy_files(cls, store: SweBenchRunStore, target: SweBenchRunStore) -> None:
        """Copies a shard's instance states and agent logs, and links each instance's scratch."""
        for sub in ("instances", "logs"):
            source = store.directory / sub
            files = (
                sorted(path for path in source.iterdir() if path.is_file())
                if source.is_dir()
                else []
            )
            for path in files:
                shutil.copy2(path, target.directory / sub / path.name)
        scratch = store.directory / "scratch"
        if scratch.is_dir():
            (target.directory / "scratch").mkdir(exist_ok=True)
            for path in sorted(scratch.iterdir()):
                link = target.directory / "scratch" / path.name
                if path.is_dir() and not link.exists():
                    link.symlink_to(path.resolve(), target_is_directory=True)

    @classmethod
    def notes(cls, stores: list[SweBenchRunStore], manifests: list[dict[str, Any]]) -> list[str]:
        """What the merged run lacks.

        Returns:
            list[str]: Shards of the list not named, and instances not run yet.
        """
        notes = []
        shards = [manifest.get("shard") or {} for manifest in manifests]
        if all(shards):
            count = shards[0].get("of")
            absent = sorted(
                set(range(1, int(count or 0) + 1)) - {shard.get("index") for shard in shards}
            )
            if absent:
                notes.append(
                    f"Shard(s) {', '.join(map(str, absent))} of {count} are not in the merge: "
                    "it covers part of the list."
                )
        for store, manifest in zip(stores, manifests, strict=True):
            left = len(set(manifest.get("instances", [])) - set(store.finished()))
            if left:
                notes.append(f"{store.name}: {left} instance(s) not run yet.")
        return notes
