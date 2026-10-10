"""
`ling-admin swe-bench {setup,smoke,run,eval,report,status,clean}`
(specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §3).

The parser and the dispatch live here so the CLI controller only registers and calls them.
"""

import argparse
import json
import shutil
import time
from typing import Any, List, Optional

from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_evaluator import DROP_TEST_HUNKS, SweBenchEvaluator
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_report import SweBenchReport
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.swe_bench.swe_bench_hooks import HOOK_SETS
from dreamference.swe_bench.swe_bench_instance_run import REFINE_VERSIONS, TASK_RULES
from dreamference.swe_bench.swe_bench_runner import SweBenchRunner
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime


class SweBenchCommand:
    """The `swe-bench` subcommands."""

    @classmethod
    def add_parser(cls, subparsers: Any) -> None:
        """
        Registers `swe-bench` and its subcommands.

        Args:
            subparsers: The top-level subparsers of `ling-admin`.
        """
        parser = subparsers.add_parser(
            "swe-bench", help="Run ling over SWE-bench instances on this machine and grade the patches")
        commands = parser.add_subparsers(dest="swe_bench_command")

        setup = commands.add_parser("setup", help="Install the harness, download the dataset, build the ling runtime for the instance images")
        setup.add_argument("--dataset", default="verified", help="verified (default), lite, full, or a HuggingFace id")
        setup.add_argument("--validate", action="store_true", help="Also check which instances grade correctly here (pulls their images)")
        setup.add_argument("--instances", default=None, help="With --validate: comma-separated instance ids")
        setup.add_argument("--limit", type=int, default=None, help="With --validate: only the first N instances, sorted by id")
        setup.add_argument("--force", action="store_true", help="With --validate: check again instances that already have a result")

        smoke = commands.add_parser("smoke", help="Prove the whole pipeline on five instances; run refuses until this has passed")
        smoke.add_argument("--idle-minutes", type=float, default=None, help="Minutes the model must have been idle first (default 10)")
        smoke.add_argument("--ignore-open-sessions", action="store_true", help="Do not wait for open ling sessions to close (for testing)")

        run = commands.add_parser("run", help="The agent phase: one ling exec per instance, producing predictions.jsonl")
        run.add_argument("--dataset", default="verified", help="verified (default), lite, full, or a HuggingFace id")
        run.add_argument("--instances", default=None, help="Comma-separated instance ids")
        run.add_argument("--subset", default=None, help="A file of instance ids, one per line")
        run.add_argument("--limit", type=int, default=None, help="Only the first N selected instances, sorted by id")
        run.add_argument("--name", default=None, help="The run's name (letters, digits, ., _ and -); an existing run of that name is resumed")
        run.add_argument("--eval", action="store_true", help="Grade the predictions when the agent phase ends")
        run.add_argument("--remove-images", action="store_true", help="With --eval: work one repository at a time and remove its images once it is graded")
        run.add_argument("--code-index", default="off", choices=["off", "universal", "exact"],
                         help="universal: index each instance's repository on the host and give the agent ling-code (default off); "
                              "exact: the same with the SCIP stores alone and no graph")
        run.add_argument("--prompt", default=None,
                         help="The system prompt the agent starts with: default, offline (default without the web and email blocks), high-swe, or a custom one in $CODEX_HOME/system-prompts (default: the configured one)")
        run.add_argument("--mask", default="off", choices=["off", "on"],
                         help="on: mask old tool outputs in the agent's requests (context budget spec §4.1; default off)")
        run.add_argument("--strip-names", action="store_true",
                         help="Take the files, modules, functions and classes the reference fix touches out of each issue's text before the agent sees it")
        run.add_argument("--refine", action="store_true",
                         help="Two steps per instance: a session that studies the issue and writes a refined description "
                              "without changing the repository, then a fresh session that fixes it")
        run.add_argument("--refine-version", default=None, choices=list(REFINE_VERSIONS),
                         help="With --refine: which texts the two steps get. v1 (the default) is the measured one; "
                              "v2 stops protecting what the issue contradicts and lists those tests with their new "
                              "values, says what the issue changes, names one option where "
                              "the issue leaves a choice open, wants checks the bug fails, and checks every claim "
                              "against the repository (refine spec §10)")
        run.add_argument("--task-rules", default=None,
                         help="Rules added to the task prompt, comma-separated, of: " + ", ".join(TASK_RULES)
                              + " (default none). tests: never change an existing test, keep your own scripts in "
                                "/tmp, and compare failing tests by name with and without the change. tests-v2: the "
                                "same, except that a test the change fails is weighed against the issue, which "
                                "decides whether the test or the change is wrong. issue-v1: work out exactly what "
                                "the issue asks for before editing, follow the pattern of the sibling code that "
                                "does the same thing, and run the issue's example after the last edit. Rules "
                                "stack (e.g. tests-v2,issue-v1); the prompt has them in the order listed here, "
                                "whatever order they are given in")
        run.add_argument("--hooks", default=None,
                         help="Rules enforced in the agent's session by Codex hooks, comma-separated, of: "
                              + ", ".join(HOOK_SETS) + " (default none). issue-v1: "
                              + HOOK_SETS["issue-v1"] + ". Independent of --task-rules, which only asks")
        run.add_argument("--until", default=None, help="HH:MM after which no new instance starts")
        run.add_argument("--idle-minutes", type=float, default=None, help="Minutes the model must have been idle first (default 10)")
        run.add_argument("--ignore-open-sessions", action="store_true", help="Do not wait for open ling sessions to close (for testing)")
        run.add_argument("--label", default=None,
                         help="What the model gate's refusal calls this run, e.g. \"night 1\" (default: SWE-bench run <name>)")
        run.add_argument("--review-turn", action="store_true",
                         help="After the agent stops with a changed tree, resume its session once more to re-read the issue, "
                              "read its diff, run the tests of the modules it changed and fix what does not hold, "
                              "within the task's time limit; the patch is collected after that turn (default off)")

        evaluate =commands.add_parser("eval", help="The grading phase: the upstream harness applies each patch and runs the tests")
        evaluate.add_argument("run", nargs="?", default=None, help="The run (default: the latest)")
        evaluate.add_argument("--drop-test-hunks", action="store_true",
                              help="Grade the same predictions again with every test file left out of each patch, "
                                   "as a separate grading (eval-drop-test-hunks/); no agent runs")
        evaluate.add_argument("--remove-images", action="store_true",
                              help="Grade one repository at a time and remove the images this grading pulled once "
                                   "their repository is graded")

        report = commands.add_parser("report", help="Print the resolved rate and what it was measured with")
        report.add_argument("run", nargs="?", default=None, help="The run (default: the latest)")
        report.add_argument("--against", default=None, help="Compare with this run, instance by instance")
        report.add_argument("--drop-test-hunks", action="store_true",
                            help="Report the grading with test files dropped (eval --drop-test-hunks); with --against "
                                 "the other run's plain grading is the comparison, which may be the same run's")

        commands.add_parser("status", help="Runs, their progress, images and disk")

        clean = commands.add_parser("clean", help="Remove a run's containers and scratch; with --images, the instance images")
        clean.add_argument("run", nargs="?", default=None, help="The run (default: every run's containers)")
        clean.add_argument("--images", action="store_true", help="Also remove the instance images")

    @classmethod
    def dispatch(cls, args: argparse.Namespace) -> int:
        """
        Runs the subcommand the arguments name.

        Args:
            args: Parsed arguments.

        Returns:
            int: The exit code.
        """
        command = getattr(args, "swe_bench_command", None)
        if command == "setup":
            return cls.setup(args.dataset, args.validate, cls._ids(args.instances), args.limit, args.force)
        if command == "smoke":
            return cls.smoke(args.idle_minutes, args.ignore_open_sessions)
        if command == "run":
            if args.refine_version and not args.refine:
                print("❌ --refine-version chooses the texts of --refine: give both.")
                return 2
            return SweBenchRunner.run(
                dataset=args.dataset, instances=cls._ids(args.instances), limit=args.limit,
                subset=args.subset, name=args.name, evaluate=args.eval, until=args.until,
                idle_minutes=args.idle_minutes, ignore_sessions=args.ignore_open_sessions,
                keep_images=not args.remove_images, code_index=args.code_index, prompt=args.prompt,
                mask=args.mask, strip_names=args.strip_names, refine=args.refine,
                task_rules=cls._ids(args.task_rules), label=args.label, review_turn=args.review_turn,
                refine_version=args.refine_version or "v1",
                hooks=cls._ids(args.hooks))
        if command == "eval":
            return cls.evaluate(args.run, DROP_TEST_HUNKS if args.drop_test_hunks else None, args.remove_images)
        if command == "report":
            return cls.report(args.run, args.against, DROP_TEST_HUNKS if args.drop_test_hunks else None)
        if command == "status":
            return cls.status()
        if command == "clean":
            return cls.clean(args.run, args.images)
        print("usage: ling-admin swe-bench {setup,smoke,run,eval,report,status,clean}")
        return 2

    @classmethod
    def _ids(cls, text: Optional[str]) -> Optional[List[str]]:
        return [part.strip() for part in text.split(",") if part.strip()] if text else None

    # -- setup ---------------------------------------------------------------------------------

    @classmethod
    def setup(cls, dataset: str = "verified", validate: bool = False,
              instances: Optional[List[str]] = None, limit: Optional[int] = None,
              force: bool = False) -> int:
        """
        Prepares everything a run needs except the instance images: the harness's virtualenv,
        the dataset snapshot, the list of arm64 images and the relocated `ling`.

        Args:
            dataset: The dataset to download.
            validate: Also validate instances (this pulls their images).
            instances: Instances to validate; default all with an image.
            limit: Validate only the first N.
            force: Validate again instances that already have a result.

        Returns:
            int: 0 when the machine is ready.
        """
        architecture = swe_bench_settings.SweBenchSettings.architecture()
        settings = swe_bench_settings.SweBenchSettings()
        if SweBenchDocker.run(["info", "--format", "{{.ServerVersion}}"], timeout=60).returncode != 0:
            print("❌ Docker is not answering.")
            return 1
        print(f"🔧 Harness: swebench {swe_bench_settings.HARNESS_VERSION} in {SweBenchHarness.venv_dir()}")
        if not SweBenchHarness.install():
            return 1
        meta = SweBenchHarness.download_dataset(dataset)
        if meta is None:
            return 1
        print(f"✅ Dataset {meta.get('dataset')}: {meta.get('rows')} instances, revision {str(meta.get('revision'))[:12]}")
        ids = [row["instance_id"] for row in SweBenchHarness.rows(dataset)]
        tags = SweBenchImages.tags(refresh=True)
        with_image = [i for i in ids if SweBenchImages.image_for(i, tags)]
        print(f"✅ {architecture} images: {len(with_image)} of {len(ids)} instances "
              f"({swe_bench_settings.COMMUNITY_IMAGE_REPO}, third-party); the rest are not evaluable here")
        mightling_bin = SweBenchRuntime.installed_mightling()
        if not mightling_bin:
            print("❌ ling is not built: run `ling-admin codex build` first.")
            return 1
        runtime = SweBenchRuntime.ensure(mightling_bin, SweBenchHarness.tool("patchelf"))
        if runtime is None:
            return 1
        print(f"✅ ling runtime for the instance images: {SweBenchRuntime.directory()} ({runtime[:12]})")
        disk = SweBenchEvaluator.disk_problem(settings)
        if disk:
            print(f"⚠️  {disk}")
        if validate:
            wanted = instances or with_image
            wanted = wanted[:limit] if limit else wanted
            print(f"🔎 Validating {len(wanted)} instance(s): reference patch must resolve, a no-op patch must not...")
            outcome = SweBenchEvaluator.validate(dataset, wanted, settings, force=force)
            good = [i for i, problem in outcome.items() if problem is None]
            for instance_id, problem in sorted(outcome.items()):
                if problem:
                    print(f"   {instance_id}: {problem}")
            print(f"✅ {len(good)} of {len(wanted)} validated; list: {SweBenchImages.validated_path()}")
        return 0

    # -- smoke ---------------------------------------------------------------------------------

    @classmethod
    def smoke(cls, idle_minutes: Optional[float] = None, ignore_sessions: bool = False) -> int:
        """
        Proves the pipeline end to end (§7.1): the five smoke instances must resolve with their
        reference patches and must not with a no-op patch, and one of them must go through the
        agent phase and be graded.

        Args:
            idle_minutes: Minutes the model must have been idle before the agent step.
            ignore_sessions: Do not wait for open `ling` sessions.

        Returns:
            int: 0 when the smoke passed and was recorded.
        """
        settings = swe_bench_settings.SweBenchSettings()
        ids = list(swe_bench_settings.SMOKE_INSTANCES)
        if not SweBenchHarness.rows("verified") or SweBenchHarness.installed_version() != swe_bench_settings.HARNESS_VERSION:
            print("❌ Run `ling-admin swe-bench setup` first.")
            return 1
        print(f"🔎 Smoke 1/2: grading {len(ids)} instances with their reference patch and with a no-op patch...")
        outcome = SweBenchEvaluator.validate("verified", ids, settings, force=True)
        failed = {i: problem for i, problem in outcome.items() if problem}
        for instance_id, problem in failed.items():
            print(f"   ❌ {instance_id}: {problem}")
        if failed:
            print("❌ Smoke failed: the grader cannot be trusted on this machine as it is.")
            return 1
        print(f"✅ All {len(ids)} resolve with the reference patch and none with the no-op patch.")

        name = f"smoke-{time.strftime('%Y%m%d-%H%M%S')}"
        print(f"🔎 Smoke 2/2: the agent on {ids[0]} (run {name})...")
        code = SweBenchRunner.run(dataset="verified", instances=[ids[0]], name=name, evaluate=True,
                                  require_smoke=False, settings=settings,
                                  idle_minutes=idle_minutes, ignore_sessions=ignore_sessions)
        store = SweBenchRunStore(name)
        state = store.state(ids[0]) or {}
        _, _, results = SweBenchEvaluator.results(store)
        problems = []
        if code != 0:
            problems.append("the run did not start (see above)")
        if ids[0] not in store.finished():
            problems.append("no prediction was written")
        if not store.log_path(ids[0]).exists() or store.log_path(ids[0]).stat().st_size == 0:
            problems.append("no agent log was written")
        if state.get("status") == "error":
            problems.append(f"the agent run failed: {'; '.join(state.get('notes', []))[:300]}")
        if ids[0] not in results:
            problems.append("the prediction was not graded")
        if problems:
            print("❌ Smoke failed: " + "; ".join(problems) + ".")
            return 1
        verdict = "resolved" if results[ids[0]].get("resolved") else "not resolved"
        print(f"✅ The agent produced a prediction ({state.get('status')}, {state.get('wall_s')} s) "
              f"and it was graded: {verdict}.")
        SweBenchRunner.smoke_path().write_text(json.dumps({
            "passed": True, "harness": swe_bench_settings.HARNESS_VERSION,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run": name,
            "agent_resolved": bool(results[ids[0]].get("resolved")),
            "runtime_hash": SweBenchRuntime.current_hash(),
        }, indent=2) + "\n")
        print("✅ Smoke passed; `ling-admin swe-bench run` is unlocked.")
        return 0

    # -- eval, report, status, clean -----------------------------------------------------------

    @classmethod
    def _store(cls, name: Optional[str]) -> Optional[SweBenchRunStore]:
        name = name or SweBenchRunStore.latest()
        if not name or SweBenchRunStore(name).manifest() is None:
            print(f"❌ No such run: {name or '(none yet)'}. `ling-admin swe-bench status` lists them.")
            return None
        return SweBenchRunStore(name)

    @classmethod
    def evaluate(cls, name: Optional[str], variant: Optional[str] = None, remove_images: bool = False) -> int:
        """
        Grades a run's predictions that are not graded yet.

        Args:
            name: The run; None for the latest.
            variant: A grading series other than the plain one (`drop-test-hunks`), or None.
            remove_images: Grade one repository at a time and remove the images the grading
                pulled once their repository is graded.

        Returns:
            int: 0 when the harness ran.
        """
        store = cls._store(name)
        if store is None:
            return 1
        settings = swe_bench_settings.SweBenchSettings()
        from dreamference.night_shift.night_shift_host import GIB, NightShiftHost
        available = NightShiftHost.mem_available_bytes()
        needed = settings.eval_workers * NightShiftHost.parse_size(settings.eval_memory) + 8 * GIB
        if available < needed:
            print(f"⚠️  Not graded: {available / GIB:.0f} GiB of memory is available and {settings.eval_workers} "
                  f"workers of {settings.eval_memory} plus the 8 GiB reserve need {needed / GIB:.0f}.")
            return 1
        if remove_images:
            # One repository at a time, as `run --eval --remove-images` does: an image this grading
            # pulled goes once its repository is graded, so the disk holds one repository's.
            manifest = store.manifest() or {}
            rows = {row["instance_id"]: row for row in SweBenchHarness.rows(manifest.get("dataset", "verified"))}
            ids = [prediction["instance_id"] for prediction in store.predictions() if prediction["instance_id"] in rows]
            results = {}
            for group in SweBenchRunner.by_repository(ids, rows).values():
                images = [manifest["images"][i]["image"] for i in group if i in manifest.get("images", {})]
                absent = [image for image in images if SweBenchDocker.image_digest(image) is None]
                results = SweBenchEvaluator.grade(store, settings, only=group, variant=variant)
                SweBenchImages.remove([image for image in absent if SweBenchDocker.image_digest(image)])
        else:
            results = SweBenchEvaluator.grade(store, settings, variant=variant)
        resolved = sum(1 for result in results.values() if result.get("resolved"))
        if variant == DROP_TEST_HUNKS:
            changed = sum(1 for result in results.values() if result.get("dropped"))
            print(f"✅ {store.name}, test files dropped: {len(results)} graded, {resolved} resolved; "
                  f"{changed} patch(es) lost test files. `ling-admin swe-bench report {store.name} --drop-test-hunks "
                  f"--against {store.name}` compares it with the plain grading.")
            return 0
        print(f"✅ {store.name}: {len(results)} graded, {resolved} resolved. "
              f"`ling-admin swe-bench report {store.name}` prints the report.")
        return 0

    @classmethod
    def report(cls, name: Optional[str], against: Optional[str], variant: Optional[str] = None) -> int:
        """
        Prints a run's report, or its comparison with another run.

        Args:
            name: The run; None for the latest.
            against: The run to compare with, if any.
            variant: The grading series of the run's verdicts (`drop-test-hunks`); None for the
                plain one.

        Returns:
            int: 0 when a report was printed.
        """
        store = cls._store(name)
        if store is None:
            return 1
        if against:
            print(SweBenchReport.against(store, SweBenchRunStore(against), variant), end="")
            return 0
        print(SweBenchReport.write(store, variant), end="")
        return 0

    @classmethod
    def status(cls) -> int:
        """
        Prints the runs, their progress, the validated list, images and disk.

        Returns:
            int: 0.
        """
        version = SweBenchHarness.installed_version()
        print(f"Harness: {'swebench ' + version if version else 'not installed (ling-admin swe-bench setup)'}")
        print(f"Smoke: {'passed' if SweBenchRunner.smoke_passed() else 'not passed with this harness version'}")
        print(f"Validated here: {len(SweBenchImages.validated())} instance(s); "
              f"rejected: {len(SweBenchImages.rejected())}")
        for name in SweBenchRunStore.runs():
            summary = SweBenchReport.summary(SweBenchRunStore(name)) or {}
            print(f"  {name}: {summary.get('finished', 0)} of {summary.get('validated', 0)} run, "
                  f"{summary.get('graded', 0)} graded, {summary.get('resolved', 0)} resolved")
        images = SweBenchImages.local_images()
        print(f"Images: {len(images)} instance image(s) present")
        swe_bench_settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(swe_bench_settings.CACHE_DIR).free
        print(f"Disk: {free / 1024 ** 3:.0f} GiB free; reserve {swe_bench_settings.SweBenchSettings().disk_reserve}")
        from dreamference.config import DreamferenceConfig
        from dreamference.vllm_server.model_gate import ModelGate
        for line in ModelGate.describe(DreamferenceConfig().vllm_host):
            print(line)
        return 0

    @classmethod
    def clean(cls, name: Optional[str], images: bool) -> int:
        """
        Removes agent containers and scratch directories, and with `images` the instance images.

        Args:
            name: The run; None for every run.
            images: Also remove the instance images.

        Returns:
            int: 0.
        """
        label = f"ling.swe-bench.run={name}" if name else "ling.swe-bench.run"
        listed = SweBenchDocker.run(["ps", "-aq", "--filter", f"label={label}"], timeout=60)
        containers = listed.stdout.split()
        if containers:
            SweBenchDocker.run(["rm", "-f", *containers], timeout=300)
        for run in ([name] if name else SweBenchRunStore.runs()):
            shutil.rmtree(SweBenchRunStore(run).directory / "scratch", ignore_errors=True)
        print(f"🧹 Removed {len(containers)} container(s) and the scratch of {name or 'every run'}.")
        if images:
            present = [entry["image"] for entry in SweBenchImages.local_images()]
            removed = SweBenchImages.remove(present)
            print(f"🧹 Removed {removed} of {len(present)} instance image(s).")
        return 0
