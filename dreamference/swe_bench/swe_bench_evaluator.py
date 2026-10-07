"""
Validation and grading (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §5.3, §6, §12).

Both are calls to the upstream harness. **Validation** grades an instance twice here, with the
reference patch and with a no-op patch, and records it as usable when the first resolves and the
second does not. **Grading** hands the harness a run's predictions and records, per instance,
what it found.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_harness import HARNESS_LOG_DIR, NOOP_PATCH, SweBenchHarness
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore

VALIDATE_GOLD_RUN: Final[str] = "validate-gold"
VALIDATE_NOOP_RUN: Final[str] = "validate-noop"
NOOP_MODEL: Final[str] = "mightling-noop"


class SweBenchEvaluator:
    """Validates instances and grades runs."""

    @classmethod
    def validation_dir(cls) -> Path:
        """
        Returns:
            Path: Where the harness runs for validation.
        """
        return swe_bench_settings.CACHE_DIR / "validation"

    @classmethod
    def disk_problem(cls, settings: "swe_bench_settings.SweBenchSettings") -> Optional[str]:
        """
        Checks the disk reserve: images are about 2.2 GB each unpacked and share the one
        filesystem with the model caches.

        Args:
            settings: Benchmark settings.

        Returns:
            Optional[str]: Why nothing more may be pulled or started, or None.
        """
        from dreamference.night_shift.night_shift_host import GIB, NightShiftHost
        reserve = NightShiftHost.parse_size(settings.disk_reserve)
        swe_bench_settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(swe_bench_settings.CACHE_DIR).free
        if free < reserve:
            return (f"{free / GIB:.0f} GiB of disk is free and the reserve is {reserve / GIB:.0f} "
                    "(free some with `mling-admin swe-bench clean --images`)")
        return None

    # -- validation ----------------------------------------------------------------------------

    @classmethod
    def validate(cls, dataset: str, instance_ids: List[str],
                 settings: "swe_bench_settings.SweBenchSettings", force: bool = False) -> Dict[str, Optional[str]]:
        """
        Validates instances on this machine and records each result.

        Args:
            dataset: The dataset the instances belong to.
            instance_ids: The instances to validate.
            settings: Benchmark settings.
            force: Validate again instances that already have a result.

        Returns:
            Dict[str, Optional[str]]: Instance id to the reason it is not usable, None when it is.
        """
        rows = {row["instance_id"]: row for row in SweBenchHarness.rows(dataset)}
        validated, rejected = SweBenchImages.validated(), SweBenchImages.rejected()
        tags = SweBenchImages.tags()
        outcome: Dict[str, Optional[str]] = {}
        images: Dict[str, str] = {}
        digests: Dict[str, Optional[str]] = {}
        for instance_id in instance_ids:
            if not force and instance_id in validated:
                outcome[instance_id] = None
                continue
            if not force and instance_id in rejected:
                outcome[instance_id] = rejected[instance_id]
                continue
            if instance_id not in rows:
                outcome[instance_id] = f"not in {dataset}"
                continue
            image = SweBenchImages.image_for(instance_id, tags)
            if image is None:
                problem = f"no {swe_bench_settings.SweBenchSettings.architecture()} image"
                SweBenchImages.record_validation(instance_id, None, None, problem)
                outcome[instance_id] = problem
                continue
            disk = cls.disk_problem(settings)
            if disk and SweBenchDocker.image_digest(image) is None:
                outcome[instance_id] = f"not validated yet: {disk}"
                continue
            digest = SweBenchDocker.ensure_image(image)
            if digest is None:
                outcome[instance_id] = "not validated yet: the image could not be pulled"
                continue
            images[instance_id], digests[instance_id] = image, digest
        if not images:
            return outcome

        work = cls.validation_dir()
        to_grade = sorted(images)
        for run_id, model in ((VALIDATE_GOLD_RUN, "gold"), (VALIDATE_NOOP_RUN, NOOP_MODEL)):
            for instance_id in to_grade:  # the harness returns a cached verdict if one is on disk
                shutil.rmtree(work / HARNESS_LOG_DIR / run_id / model / instance_id, ignore_errors=True)
        dataset_file = work / "dataset.jsonl"
        SweBenchHarness.write_dataset_file(dataset_file, (rows[i] for i in to_grade), images)
        noop_file = work / "noop.jsonl"
        noop_file.write_text("".join(
            json.dumps({"instance_id": i, "model_name_or_path": NOOP_MODEL, "model_patch": NOOP_PATCH}) + "\n"
            for i in to_grade))
        for run_id, predictions in ((VALIDATE_GOLD_RUN, None), (VALIDATE_NOOP_RUN, noop_file)):
            SweBenchHarness.evaluate(work, dataset_file, run_id, to_grade, predictions,
                                     settings.eval_workers, settings.eval_timeout_s, settings.eval_memory)
        for instance_id in to_grade:
            gold = SweBenchHarness.instance_report(work, VALIDATE_GOLD_RUN, "gold", instance_id)
            noop = SweBenchHarness.instance_report(work, VALIDATE_NOOP_RUN, NOOP_MODEL, instance_id)
            problem = cls.validation_problem(gold, noop)
            SweBenchImages.record_validation(instance_id, images[instance_id], digests[instance_id], problem)
            outcome[instance_id] = problem
        return outcome

    @classmethod
    def validation_problem(cls, gold: Optional[Dict[str, Any]], noop: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        Decides from the two verdicts whether an instance is usable here.

        Args:
            gold: The harness's report for the reference patch; None if it wrote none.
            noop: Its report for the no-op patch; None if it wrote none.

        Returns:
            Optional[str]: Why the instance is not validated; None when it is.
        """
        if gold is None:
            return "the reference patch could not be graded here"
        if not gold.get("resolved"):
            return "the reference patch does not resolve it here"
        if noop is None:
            return "the no-op patch could not be graded here"
        if noop.get("resolved"):
            return "a patch that fixes nothing resolves it here"
        return None

    # -- grading -------------------------------------------------------------------------------

    @classmethod
    def grader(cls, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """
        Describes what a grading is done with; a change in any of it starts a new grading.

        Args:
            manifest: The run's manifest.

        Returns:
            Dict[str, Any]: The harness version, the dataset revision and the image source.
        """
        return {"harness": SweBenchHarness.installed_version(),
                "dataset_revision": manifest.get("dataset_revision"),
                "image_source": manifest.get("image_source")}

    @classmethod
    def grading_number(cls, store: SweBenchRunStore, grader: Dict[str, Any]) -> int:
        """
        Picks the grading to continue or start. A grading names a grader, not a set of patches:
        the number goes up when the harness, the dataset snapshot or the image source changed,
        or when an instance already graded now has a different image, because the harness caches
        verdicts by run id and instance id and would otherwise return the old one.

        Args:
            store: The run.
            grader: The current grader.

        Returns:
            int: The grading number.
        """
        numbers = store.gradings()
        if not numbers:
            return 1
        latest = store.grading(numbers[-1])
        if latest["grader"] and latest["grader"] != grader:
            return numbers[-1] + 1
        images = (store.manifest() or {}).get("images", {})
        for instance_id, result in latest["results"].items():
            image = (images.get(instance_id) or {}).get("image")
            current = SweBenchDocker.image_digest(image) if image else None
            if current and result.get("digest") and current != result["digest"]:
                return numbers[-1] + 1
        return numbers[-1]

    @classmethod
    def grade(cls, store: SweBenchRunStore, settings: "swe_bench_settings.SweBenchSettings",
              only: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        """
        Grades the run's predictions that the current grading has not graded yet.

        Args:
            store: The run.
            settings: Benchmark settings.
            only: Limit this call to these instances.

        Returns:
            Dict[str, Dict[str, Any]]: The grading's results so far, by instance id.
        """
        manifest = store.manifest() or {}
        grader = cls.grader(manifest)
        number = cls.grading_number(store, grader)
        record = store.grading(number)
        record["grader"] = grader
        images = {instance_id: entry["image"] for instance_id, entry in manifest.get("images", {}).items()}
        pending: List[Dict[str, Any]] = []
        for prediction in store.predictions():
            instance_id = prediction["instance_id"]
            if instance_id in record["results"] or (only is not None and instance_id not in only):
                continue
            if not prediction["model_patch"].strip():
                # The harness starts no container for an empty patch; it is unresolved by definition.
                record["results"][instance_id] = {"resolved": False, "empty": True}
                continue
            pending.append(prediction)
        store.write_grading(number, record)
        if not pending:
            return record["results"]

        work = store.grading_dir(number)
        rows = {row["instance_id"]: row for row in SweBenchHarness.rows(manifest.get("dataset", "verified"))}
        digests: Dict[str, Optional[str]] = {}
        gradable: List[Dict[str, Any]] = []
        for prediction in pending:
            instance_id = prediction["instance_id"]
            image = images.get(instance_id)
            disk = cls.disk_problem(settings) if image and SweBenchDocker.image_digest(image) is None else None
            if disk:
                print(f"⚠️  {instance_id} is not graded yet: {disk}")
                continue
            digest = SweBenchDocker.ensure_image(image) if image else None
            if digest is None or instance_id not in rows:
                print(f"⚠️  {instance_id} is not graded yet: its image is not available")
                continue
            digests[instance_id] = digest
            gradable.append(prediction)
        if not gradable:
            return record["results"]

        run_id = f"{store.name}-{number}"
        ids = [prediction["instance_id"] for prediction in gradable]
        dataset_file = work / "dataset.jsonl"
        SweBenchHarness.write_dataset_file(dataset_file, (rows[i] for i in ids), images)
        predictions_file = work / "to-grade.jsonl"
        predictions_file.write_text("".join(json.dumps(prediction) + "\n" for prediction in gradable))
        SweBenchHarness.evaluate(work, dataset_file, run_id, ids, predictions_file,
                                 settings.eval_workers, settings.eval_timeout_s, settings.eval_memory)
        for prediction in gradable:
            instance_id, model = prediction["instance_id"], prediction["model_name_or_path"]
            report = SweBenchHarness.instance_report(work, run_id, model, instance_id)
            if report is None:
                # No verdict: the container failed before the tests ran. Left ungraded, so the
                # next `eval` tries it again, and counted as "not graded" meanwhile.
                continue
            record["results"][instance_id] = {
                "resolved": bool(report.get("resolved")),
                "applied": bool(report.get("patch_successfully_applied")),
                "test_patch_failed": SweBenchHarness.test_patch_failed(work, run_id, model, instance_id),
                "digest": digests[instance_id],
            }
        store.write_grading(number, record)
        return record["results"]

    @classmethod
    def results(cls, store: SweBenchRunStore) -> tuple:
        """
        Returns:
            tuple: (grading number or None, its grader, its results by instance id) for the
            run's latest grading.
        """
        numbers = store.gradings()
        if not numbers:
            return None, {}, {}
        record = store.grading(numbers[-1])
        return numbers[-1], record["grader"], record["results"]
