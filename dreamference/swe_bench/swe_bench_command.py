"""`ling-admin swe-bench {setup,build-images,smoke,run,eval,report,status,clean,pin-images,shard,merge}`
(specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §3).

The parser and the dispatch live here so the CLI controller only registers and calls them.
"""

import argparse
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from dreamference.swe_bench import swe_bench_settings, swe_bench_shards
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_evaluator import DROP_TEST_HUNKS, SweBenchEvaluator
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness
from dreamference.swe_bench.swe_bench_hooks import HOOK_SETS
from dreamference.swe_bench.swe_bench_image_builder import SweBenchImageBuilder
from dreamference.swe_bench.swe_bench_image_cache import SweBenchImageCache
from dreamference.swe_bench.swe_bench_image_pins import SweBenchImagePins
from dreamference.swe_bench.swe_bench_images import SweBenchImages
from dreamference.swe_bench.swe_bench_instance_run import REFINE_VERSIONS, TASK_RULES
from dreamference.swe_bench.swe_bench_report import SweBenchReport
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.swe_bench.swe_bench_runner import SweBenchRunner
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime
from dreamference.swe_bench.swe_bench_shard_merge import SweBenchShardMerge
from dreamference.swe_bench.swe_bench_shards import SweBenchShards

# The commands on one list split across machines (`dispatch_lists`): `shard` first, then `merge`.
LIST_COMMANDS: Final[tuple[str, str]] = ("shard", "merge")

# Builds the images of a dataset nobody publishes arm64 images for.
BUILD_COMMAND: Final[str] = "build-images"
PIN_COMMAND: Final[str] = "pin-images"

USAGE: Final[str] = (
    "usage: ling-admin swe-bench {setup,build-images,smoke,run,eval,report,status,clean,pin-images,shard,merge}"
)


class SweBenchCommand:
    """The `swe-bench` subcommands."""

    @classmethod
    def add_pin_parser(cls, add: Callable[..., argparse.ArgumentParser]) -> None:
        """Adds `swe-bench pin-images`.

        Args:
            add: The `swe-bench` subparsers' `add_parser`.
        """
        pin = add("pin-images", help="Compare local community images with their pinned digests")
        pin.add_argument(
            "--accept",
            nargs="+",
            metavar="IMAGE",
            default=[],
            help="Pin the local digest of these images",
        )
        pin.add_argument(
            "--new", action="store_true", help="Pin every local image that has no pin yet"
        )

    @classmethod
    def add_output_arms(cls, run: argparse.ArgumentParser) -> None:
        """Adds the arms on what becomes of the model's output to `swe-bench run`.

        Drop-first compaction (compaction spec §13) and the loop guard's three (docs/dev/loop-guard.md),
        and the compatibility mode (MIGHTLING_LING_CLAUDE §11), which changes how the session reads
        and is asked.

        Args:
            run: The `run` subcommand's parser.
        """
        run.add_argument(
            "--compat",
            choices=("claude", "off"),
            default=None,
            metavar="MODE",
            help="The compatibility mode for every session (claude: ling working the way Claude Code "
            "does); the launcher's own setting when absent, which is off in a container",
        )
        run.add_argument(
            "--drop-old-outputs",
            action="store_true",
            help="When a session is about to compact, first drop the output of its older tool calls "
            "(the calls stay, the last 10 outputs and short ones are kept) and summarise only if "
            "that is not enough; sets the launcher's switch in every session (compaction spec §13)",
        )
        run.add_argument(
            "--loop-guard",
            action="store_true",
            help="Switch on ling's loop detector in every session: a response that repeats itself is "
            "cut where the loop began and the model is told once to stop and answer "
            "(DREAMFERENCE_MIGHTLING_LOOP_GUARD; docs/dev/loop-guard.md). Off by default",
        )
        run.add_argument(
            "--output-budget",
            type=int,
            default=None,
            metavar="TOKENS",
            help="Cap every response at TOKENS (max_output_tokens, set by the model gate from ling's "
            "request metadata); a capped response is followed by one nudge to answer. "
            "Needs a gate started by this version (`ling-admin server start`). Default: no cap",
        )
        run.add_argument(
            "--sampling",
            choices=("default", "nonthinking"),
            default=None,
            metavar="ARM",
            help="nonthinking: the model card's non-thinking sampling (temperature 0.7, top_p 0.8, "
            "top_k 20, presence_penalty 1.5), filled in by the model gate; default: the server's "
            "own (the checkpoint's thinking-mode values). Needs a gate started by this version",
        )
        cls.add_launcher_switches(run)

    @classmethod
    def add_chunk_arguments(
        cls, run: argparse.ArgumentParser, evaluate: argparse.ArgumentParser
    ) -> None:
        """Adds `--remove-images` and `--image-chunk` to `run` and `eval`, and `--shard` to `run`.

        Args:
            run: `swe-bench run`'s parser.
            evaluate: `swe-bench eval`'s parser.
        """
        run.add_argument(
            "--remove-images",
            action="store_true",
            help="With --eval: work one repository at a time, in chunks of at most --image-chunk instances, "
            "and remove a chunk's images once it is graded",
        )
        evaluate.add_argument(
            "--remove-images",
            action="store_true",
            help="Grade one repository at a time, in chunks of at most --image-chunk instances, and "
            "remove the images this grading pulled once their chunk is graded",
        )
        run.add_argument(
            "--image-chunk",
            type=int,
            default=None,
            metavar="N",
            help="With --eval --remove-images: the most instances of one repository whose images are on "
            "disk at once (default [swe_bench] image_chunk, 25; 0: the whole repository)",
        )
        run.add_argument(
            "--shard",
            default=None,
            metavar="I/K",
            help="A new run covers only the I-th of K shards of the selected instances, balanced by "
            "difficulty and repository and the same on every machine, so K machines share one list; "
            "`swe-bench merge` puts the K runs back together",
        )
        evaluate.add_argument(
            "--image-chunk",
            type=int,
            default=None,
            metavar="N",
            help="With --remove-images: the most instances of one repository graded at once "
            "(default [swe_bench] image_chunk, 25; 0: the whole repository)",
        )

    @classmethod
    def add_build_parser(cls, add: Callable[..., argparse.ArgumentParser]) -> None:
        """Adds `swe-bench build-images`.

        Args:
            add: The `swe-bench` subparsers' `add_parser`.
        """
        build = add(
            BUILD_COMMAND,
            help="Build the arm64 instance images of a dataset nobody publishes them for "
            "(multilingual), from its task repository at the pinned commit",
        )
        build.add_argument(
            "--dataset",
            default="multilingual",
            help="A dataset with a task repository (default multilingual)",
        )
        build.add_argument("--instances", default=None, help="Comma-separated instance ids")
        build.add_argument("--subset", default=None, help="A file of instance ids, one per line")
        build.add_argument(
            "--languages",
            default=None,
            help="Comma-separated languages to take: c, cpp, rust, go, java, js, ruby, php",
        )
        build.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only the first N selected instances, sorted by id",
        )
        build.add_argument(
            "--force", action="store_true", help="Build again an image already there"
        )
        build.add_argument(
            "--validate",
            action="store_true",
            help="Then validate each built image: the reference patch must resolve, a no-op patch must not",
        )

    @classmethod
    def add_list_parsers(cls, add: Callable[..., argparse.ArgumentParser]) -> None:
        """Adds `swe-bench shard` and `swe-bench merge`, the commands on a list split across machines.

        Args:
            add: The `swe-bench` subparsers' `add_parser`.
        """
        cls.add_build_parser(add)
        cls.add_pin_parser(add)
        shard = add(
            "shard", help="Split a task list into K balanced shards, written as lists of their own"
        )
        shard.add_argument("list", help="The task list, one instance id per line")
        shard.add_argument("--shards", type=int, required=True, metavar="K", help="How many shards")
        shard.add_argument(
            "--out-dir",
            default=None,
            help="Where to write <list>-<I>of<K>.txt (default: beside the list)",
        )
        shard.add_argument(
            "--dataset", default="verified", help="The dataset the ids are from (default verified)"
        )
        shard.add_argument(
            "--balance-by",
            action="append",
            default=[],
            metavar="OTHER",
            help="Also spread the tasks of this other list evenly over the shards (repeatable), e.g. "
            "the fresh ones; the shards are then not those run --shard makes, and say so",
        )

        merge = add(
            "merge",
            help="Put the runs of one list's shards together as one run, which report reads like any other",
        )
        merge.add_argument("name", help="The merged run's name")
        merge.add_argument(
            "runs",
            nargs="+",
            help="The shards' runs (copy a run made on another machine into this machine's runs directory first)",
        )

    @classmethod
    def add_parser(cls, subparsers: Any) -> None:
        """Registers `swe-bench` and its subcommands.

        Args:
            subparsers: The top-level subparsers of `ling-admin`.
        """
        parser = subparsers.add_parser(
            "swe-bench",
            help="Run ling over SWE-bench instances on this machine and grade the patches",
        )
        commands = parser.add_subparsers(dest="swe_bench_command")

        setup = commands.add_parser(
            "setup",
            help="Install the harness, download the dataset, build the ling runtime for the instance images",
        )
        setup.add_argument(
            "--dataset",
            default="verified",
            help="verified (default), lite, full, or a HuggingFace id",
        )
        setup.add_argument(
            "--validate",
            action="store_true",
            help="Also check which instances grade correctly here (pulls their images)",
        )
        setup.add_argument(
            "--instances", default=None, help="With --validate: comma-separated instance ids"
        )
        setup.add_argument(
            "--limit",
            type=int,
            default=None,
            help="With --validate: only the first N instances, sorted by id",
        )
        setup.add_argument(
            "--force",
            action="store_true",
            help="With --validate: check again instances that already have a result",
        )

        smoke = commands.add_parser(
            "smoke",
            help="Prove the whole pipeline on five instances; run refuses until this has passed",
        )
        smoke.add_argument(
            "--idle-minutes",
            type=float,
            default=None,
            help="Minutes the model must have been idle first (default 10)",
        )
        smoke.add_argument(
            "--ignore-open-sessions",
            action="store_true",
            help="Do not wait for open ling sessions to close (for testing)",
        )

        run = commands.add_parser(
            "run", help="The agent phase: one ling exec per instance, producing predictions.jsonl"
        )
        run.add_argument(
            "--dataset",
            default="verified",
            help="verified (default), lite, full, or a HuggingFace id",
        )
        run.add_argument("--instances", default=None, help="Comma-separated instance ids")
        run.add_argument("--subset", default=None, help="A file of instance ids, one per line")
        run.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only the first N selected instances, sorted by id",
        )
        run.add_argument(
            "--name",
            default=None,
            help="The run's name (letters, digits, ., _ and -); an existing run of that name is resumed",
        )
        run.add_argument(
            "--eval", action="store_true", help="Grade the predictions when the agent phase ends"
        )
        run.add_argument(
            "--code-index",
            default="off",
            choices=["off", "universal", "exact"],
            help="universal: index each instance's repository on the host and give the agent ling-code (default off); "
            "exact: the same with the SCIP stores alone and no graph",
        )
        run.add_argument(
            "--prompt",
            default=None,
            help="The system prompt the agent starts with: default, offline (default without the web and email blocks), high-swe, or a custom one in $CODEX_HOME/system-prompts (default: the configured one)",
        )
        run.add_argument(
            "--mask",
            default="off",
            choices=["off", "on"],
            help="on: mask old tool outputs in the agent's requests (context budget spec §4.1; default off)",
        )
        run.add_argument(
            "--strip-names",
            action="store_true",
            help="Take the files, modules, functions and classes the reference fix touches out of each issue's text before the agent sees it",
        )
        run.add_argument(
            "--refine",
            action="store_true",
            help="Two steps per instance: a session that studies the issue and writes a refined description "
            "without changing the repository, then a fresh session that fixes it",
        )
        run.add_argument(
            "--refine-version",
            default=None,
            choices=list(REFINE_VERSIONS),
            help="With --refine: which texts the two steps get. v1 (the default) is the measured one; "
            "v2 stops protecting what the issue contradicts and lists those tests with their new "
            "values, says what the issue changes, names one option where "
            "the issue leaves a choice open, wants checks the bug fails, and checks every claim "
            "against the repository (refine spec §10)",
        )
        run.add_argument(
            "--task-rules",
            default=None,
            help="Rules added to the task prompt, comma-separated, of: "
            + ", ".join(TASK_RULES)
            + " (default none). tests: never change an existing test, keep your own scripts in "
            "/tmp, and compare failing tests by name with and without the change. tests-v2: the "
            "same, except that a test the change fails is weighed against the issue, which "
            "decides whether the test or the change is wrong. issue-v1: work out exactly what "
            "the issue asks for before editing, follow the pattern of the sibling code that "
            "does the same thing, and run the issue's example after the last edit. Rules "
            "stack (e.g. tests-v2,issue-v1); the prompt has them in the order listed here, "
            "whatever order they are given in",
        )
        run.add_argument(
            "--hooks",
            default=None,
            help="Rules enforced in the agent's session by Codex hooks, comma-separated, of: "
            + ", ".join(HOOK_SETS)
            + " (default none). issue-v1: "
            + HOOK_SETS["issue-v1"]
            + ". Independent of --task-rules, which only asks",
        )
        run.add_argument("--until", default=None, help="HH:MM after which no new instance starts")
        run.add_argument(
            "--idle-minutes",
            type=float,
            default=None,
            help="Minutes the model must have been idle first (default 10)",
        )
        run.add_argument(
            "--ignore-open-sessions",
            action="store_true",
            help="Do not wait for open ling sessions to close (for testing)",
        )
        run.add_argument(
            "--label",
            default=None,
            help='What the model gate\'s refusal calls this run, e.g. "night 1" (default: SWE-bench run <name>)',
        )
        run.add_argument(
            "--restart-after-compactions",
            type=int,
            default=0,
            metavar="N",
            help="Once the fix session's N-th compaction is recorded, stop it and start one fresh session "
            "with the task and the stopped session's diff as a file it may apply or discard; "
            "the tree is reset first, the time limit is not (default 0: never)",
        )
        run.add_argument(
            "--turn-end-compaction",
            default=None,
            metavar="SETTING",
            help="Compaction at the end of a turn in every session, through Codex's "
            "model_post_turn_compact_threshold_percent on the exec command line: on (the launcher's "
            "rule for the run's limit), off (pinned at 0) or a percentage of the usable window (1-99); "
            "absent, the key is not set and exec never compacts at a turn's end (spec §16, night 10)",
        )
        run.add_argument(
            "--apply-patch",
            choices=("off", "freeform"),
            default=None,
            metavar="FORM",
            help="How the agent is offered Codex's apply_patch tool: freeform (a custom tool, which "
            "only ling-engine serves) or off; the launcher's own choice when absent (spec §22). "
            "The pinned Codex has no function form, and ling refuses one",
        )
        run.add_argument(
            "--exec-tool",
            choices=("function", "freeform"),
            default=None,
            metavar="FORM",
            help="How the agent is offered Code Mode's exec tool: function (translated, which SGLang "
            "serves) or freeform (Codex's custom tool, which only ling-engine serves and SGLang "
            "drops); the launcher's own choice when absent: function on SGLang and vLLM (spec §23)",
        )
        cls.add_output_arms(run)
        run.add_argument(
            "--review-turn",
            action="store_true",
            help="After the agent stops with a changed tree, resume its session once more to re-read the issue, "
            "read its diff, run the tests of the modules it changed and fix what does not hold, "
            "within the task's time limit; the patch is collected after that turn (default off)",
        )

        evaluate = commands.add_parser(
            "eval",
            help="The grading phase: the upstream harness applies each patch and runs the tests",
        )
        evaluate.add_argument("run", nargs="?", default=None, help="The run (default: the latest)")
        evaluate.add_argument(
            "--drop-test-hunks",
            action="store_true",
            help="Grade the same predictions again with every test file left out of each patch, "
            "as a separate grading (eval-drop-test-hunks/); no agent runs",
        )

        cls.add_chunk_arguments(run, evaluate)

        report = commands.add_parser(
            "report", help="Print the resolved rate and what it was measured with"
        )
        report.add_argument("run", nargs="?", default=None, help="The run (default: the latest)")
        report.add_argument(
            "--against", default=None, help="Compare with this run, instance by instance"
        )
        report.add_argument(
            "--drop-test-hunks",
            action="store_true",
            help="Report the grading with test files dropped (eval --drop-test-hunks); with --against "
            "the other run's plain grading is the comparison, which may be the same run's",
        )

        commands.add_parser("status", help="Runs, their progress, images and disk")

        cls.add_list_parsers(commands.add_parser)

        clean = commands.add_parser(
            "clean",
            help="Remove a run's containers and scratch; with --images, the instance images",
        )
        clean.add_argument(
            "run", nargs="?", default=None, help="The run (default: every run's containers)"
        )
        clean.add_argument("--images", action="store_true", help="Also remove the instance images")

    @classmethod
    def add_launcher_switches(cls, run: argparse.ArgumentParser) -> None:
        """Adds the guards, the arms that only switch on a hook of the launcher in every session.

        `--drop-old-outputs`, also a launcher switch, is among `add_output_arms`, which calls this.

        Args:
            run: The `run` subcommand's parser.
        """
        run.add_argument(
            "--test-guard",
            action="store_true",
            help="The launcher's test guard in every session: a change that weakens an "
            "existing test is pointed out, and the first stop while it stands is "
            "held once (spec §29)",
        )
        run.add_argument(
            "--finish-check",
            action="store_true",
            help="The launcher's finish check in every session: the first stop after a "
            "change no test has run on, mid-work, or calling failures pre-existing "
            "unchecked is held once (spec §29)",
        )

    @classmethod
    def dispatch(cls, args: argparse.Namespace) -> int:
        """Runs the subcommand the arguments name.

        Args:
            args: Parsed arguments.

        Returns:
            int: The exit code.
        """
        command = getattr(args, "swe_bench_command", None)
        if command == "setup":
            return cls.setup(
                args.dataset, args.validate, cls._ids(args.instances), args.limit, args.force
            )
        if command == "smoke":
            return cls.smoke(args.idle_minutes, args.ignore_open_sessions)
        if command == "run":
            if args.refine_version and not args.refine:
                print("❌ --refine-version chooses the texts of --refine: give both.")
                return 2
            return SweBenchRunner.run(
                dataset=args.dataset,
                instances=cls._ids(args.instances),
                limit=args.limit,
                subset=args.subset,
                name=args.name,
                evaluate=args.eval,
                until=args.until,
                idle_minutes=args.idle_minutes,
                ignore_sessions=args.ignore_open_sessions,
                keep_images=not args.remove_images,
                code_index=args.code_index,
                prompt=args.prompt,
                mask=args.mask,
                strip_names=args.strip_names,
                refine=args.refine,
                task_rules=cls._ids(args.task_rules),
                label=args.label,
                review_turn=args.review_turn,
                refine_version=args.refine_version or "v1",
                turn_end_compaction=args.turn_end_compaction,
                hooks=cls._ids(args.hooks),
                restart_after_compactions=args.restart_after_compactions,
                apply_patch=args.apply_patch,
                exec_tool=args.exec_tool,
                compat=args.compat,
                drop_old_outputs=args.drop_old_outputs,
                loop_guard=args.loop_guard,
                output_budget=args.output_budget,
                sampling=args.sampling,
                shard=args.shard,
                image_chunk=args.image_chunk,
                test_guard=args.test_guard,
                finish_check=args.finish_check,
            )
        if command == "eval":
            return cls.evaluate(
                args.run,
                DROP_TEST_HUNKS if args.drop_test_hunks else None,
                args.remove_images,
                args.image_chunk,
            )
        if command in (*LIST_COMMANDS, BUILD_COMMAND, PIN_COMMAND):
            return cls.dispatch_lists(args)
        if command == "report":
            return cls.report(
                args.run, args.against, DROP_TEST_HUNKS if args.drop_test_hunks else None
            )
        if command == "status":
            return cls.status()
        if command == "clean":
            return cls.clean(args.run, args.images)
        print(USAGE)
        return 2

    @classmethod
    def dispatch_lists(cls, args: argparse.Namespace) -> int:
        """Runs `swe-bench shard`, `swe-bench merge` or `swe-bench build-images`.

        Args:
            args: The parsed command line, its `swe_bench_command` one of `LIST_COMMANDS`.

        Returns:
            int: The command's exit status.
        """
        if args.swe_bench_command == BUILD_COMMAND:
            return cls.build_images(args)
        if args.swe_bench_command == PIN_COMMAND:
            return SweBenchImagePins.review(args.accept, args.new)
        if args.swe_bench_command == LIST_COMMANDS[0]:
            return cls.shard(
                Path(args.list),
                args.shards,
                Path(args.out_dir) if args.out_dir else None,
                args.dataset,
                [Path(other) for other in args.balance_by],
            )
        return SweBenchShardMerge.merge(args.name, args.runs)

    @classmethod
    def _ids(cls, text: str | None) -> list[str] | None:
        return [part.strip() for part in text.split(",") if part.strip()] if text else None

    # -- setup ---------------------------------------------------------------------------------

    @classmethod
    def setup(
        cls,
        dataset: str = "verified",
        validate: bool = False,
        instances: list[str] | None = None,
        limit: int | None = None,
        force: bool = False,
    ) -> int:
        """Prepares everything a run needs except the instance images: the harness's virtualenv,
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
        if (
            SweBenchDocker.run(["info", "--format", "{{.ServerVersion}}"], timeout=60).returncode
            != 0
        ):
            print("❌ Docker is not answering.")
            return 1
        print(
            f"🔧 Harness: swebench {swe_bench_settings.HARNESS_VERSION} in {SweBenchHarness.venv_dir()}"
        )
        if not SweBenchHarness.install():
            return 1
        meta = SweBenchHarness.download_dataset(dataset)
        if meta is None:
            return 1
        print(
            f"✅ Dataset {meta.get('dataset')}: {meta.get('rows')} instances, revision {str(meta.get('revision'))[:12]}"
        )
        ids = [row["instance_id"] for row in SweBenchHarness.rows(dataset)]
        tags = SweBenchImages.tags(refresh=True)
        built = SweBenchImages.built_tags()
        with_image = [i for i in ids if SweBenchImages.image_for(i, tags, built)]
        print(
            f"✅ {architecture} images: {len(with_image)} of {len(ids)} instances "
            f"({SweBenchRunner.image_source(dataset)}); the rest are not evaluable here"
            + (
                " until built (swe-bench build-images)"
                if swe_bench_settings.SweBenchSettings.task_repo(dataset)
                else ""
            )
        )
        mightling_bin = SweBenchRuntime.installed_mightling()
        if not mightling_bin:
            print("❌ ling is not built: run `ling-admin codex build` first.")
            return 1
        runtime = SweBenchRuntime.ensure(mightling_bin, SweBenchHarness.tool("patchelf"))
        if runtime is None:
            return 1
        print(
            f"✅ ling runtime for the instance images: {SweBenchRuntime.directory()} ({runtime[:12]})"
        )
        disk = SweBenchEvaluator.disk_problem(settings)
        if disk:
            print(f"⚠️  {disk}")
        if validate:
            wanted = instances or with_image
            wanted = wanted[:limit] if limit else wanted
            print(
                f"🔎 Validating {len(wanted)} instance(s): reference patch must resolve, a no-op patch must not..."
            )
            outcome = SweBenchEvaluator.validate(dataset, wanted, settings, force=force)
            good = [i for i, problem in outcome.items() if problem is None]
            for instance_id, problem in sorted(outcome.items()):
                if problem:
                    print(f"   {instance_id}: {problem}")
            print(
                f"✅ {len(good)} of {len(wanted)} validated; list: {SweBenchImages.validated_path()}"
            )
        return 0

    # -- build-images --------------------------------------------------------------------------

    @classmethod
    def build_images(cls, args: argparse.Namespace) -> int:
        """Builds a dataset's instance images here, then validates them when asked.

        Args:
            args: The parsed `build-images` command line.

        Returns:
            int: 0 when every selected image was built (and, with `--validate`, judged).
        """
        dataset = args.dataset
        if swe_bench_settings.SweBenchSettings.task_repo(dataset) is None:
            print(f"❌ {dataset} has no task repository: its images are pulled (swe-bench setup).")  # noqa: T201 — CLI output
            return 2
        if not SweBenchHarness.install() or SweBenchHarness.download_dataset(dataset) is None:
            return 1
        ids = cls.build_selection(dataset, args)
        if ids is None:
            return 2
        settings = swe_bench_settings.SweBenchSettings()
        print(  # noqa: T201 — CLI output
            f"🔨 Building {len(ids)} image(s) in {swe_bench_settings.BUILDER_NAME} "
            f"(memory {settings.build_memory}, cores {settings.build_cpus or 'all'})..."
        )
        outcome = SweBenchImageBuilder.build(dataset, ids, settings, force=args.force)
        built = [i for i, problem in outcome.items() if problem is None]
        print(f"✅ {len(built)} of {len(ids)} built; record: {SweBenchImageBuilder.record_path()}")  # noqa: T201 — CLI output
        if args.validate and built:
            judged = SweBenchEvaluator.validate(dataset, built, settings, force=args.force)
            good = [i for i, problem in judged.items() if problem is None]
            for instance_id, problem in sorted(judged.items()):
                if problem:
                    print(f"   {instance_id}: {problem}")  # noqa: T201 — CLI output
            print(  # noqa: T201 — CLI output
                f"✅ {len(good)} of {len(built)} validated; list: {SweBenchImages.validated_path()}"
            )
        return 0 if len(built) == len(ids) else 1

    @classmethod
    def build_selection(cls, dataset: str, args: argparse.Namespace) -> list[str] | None:
        """The instances `build-images` was asked for, sorted by id.

        Args:
            dataset: The dataset (its snapshot is downloaded).
            args: The parsed `build-images` command line (`instances`, `subset`, `languages`, `limit`).

        Returns:
            list[str] | None: The ids; None when one named is not in the dataset.
        """
        rows = SweBenchHarness.rows(dataset)
        wanted = set(cls._ids(args.instances) or [])
        if args.subset:
            lines = Path(args.subset).read_text().splitlines()
            wanted |= {line.strip() for line in lines if line.strip() and not line.startswith("#")}
        unknown = sorted(wanted - {row["instance_id"] for row in rows})
        if unknown:
            print(f"❌ Not in {dataset}: {', '.join(unknown[:5])}")  # noqa: T201 — CLI output
            return None
        languages = set(cls._ids(args.languages) or [])
        ids = sorted(
            row["instance_id"]
            for row in rows
            if (not wanted or row["instance_id"] in wanted)
            and (not languages or SweBenchImageBuilder.language(row) in languages)
        )
        return ids[: args.limit] if args.limit else ids

    # -- smoke ---------------------------------------------------------------------------------

    @classmethod
    def smoke(cls, idle_minutes: float | None = None, ignore_sessions: bool = False) -> int:
        """Proves the pipeline end to end (§7.1): the five smoke instances must resolve with their
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
        if (
            not SweBenchHarness.rows("verified")
            or SweBenchHarness.installed_version() != swe_bench_settings.HARNESS_VERSION
        ):
            print("❌ Run `ling-admin swe-bench setup` first.")
            return 1
        print(
            f"🔎 Smoke 1/2: grading {len(ids)} instances with their reference patch and with a no-op patch..."
        )
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
        code = SweBenchRunner.run(
            dataset="verified",
            instances=[ids[0]],
            name=name,
            evaluate=True,
            require_smoke=False,
            settings=settings,
            idle_minutes=idle_minutes,
            ignore_sessions=ignore_sessions,
        )
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
        print(
            f"✅ The agent produced a prediction ({state.get('status')}, {state.get('wall_s')} s) "
            f"and it was graded: {verdict}."
        )
        SweBenchRunner.smoke_path().write_text(
            json.dumps(
                {
                    "passed": True,
                    "harness": swe_bench_settings.HARNESS_VERSION,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "run": name,
                    "agent_resolved": bool(results[ids[0]].get("resolved")),
                    "runtime_hash": SweBenchRuntime.current_hash(),
                },
                indent=2,
            )
            + "\n"
        )
        print("✅ Smoke passed; `ling-admin swe-bench run` is unlocked.")
        return 0

    # -- eval, report, status, clean -----------------------------------------------------------

    @classmethod
    def _store(cls, name: str | None) -> SweBenchRunStore | None:
        name = name or SweBenchRunStore.latest()
        if not name or SweBenchRunStore(name).manifest() is None:
            print(
                f"❌ No such run: {name or '(none yet)'}. `ling-admin swe-bench status` lists them."
            )
            return None
        return SweBenchRunStore(name)

    @classmethod
    def evaluate(
        cls,
        name: str | None,
        variant: str | None = None,
        remove_images: bool = False,
        image_chunk: int | None = None,
    ) -> int:
        """Grades a run's predictions that are not graded yet.

        Args:
            name: The run; None for the latest.
            variant: A grading series other than the plain one (`drop-test-hunks`), or None.
            remove_images: Grade one repository at a time, in chunks of at most `image_chunk`
                instances, and remove the images the grading pulled once their chunk is graded.
            image_chunk: The most instances of one repository graded at once; None takes
                `[swe_bench] image_chunk`, 0 the whole repository.

        Returns:
            int: 0 when the harness ran.
        """
        if image_chunk is not None and image_chunk < 0:
            print(  # noqa: T201 — CLI output
                f"❌ --image-chunk {image_chunk}: a number of instances, 0 for a whole repository."
            )
            return 1
        store = cls._store(name)
        if store is None:
            return 1
        settings = swe_bench_settings.SweBenchSettings()
        from dreamference.night_shift.night_shift_host import GIB, NightShiftHost

        available = NightShiftHost.mem_available_bytes()
        needed = settings.eval_workers * NightShiftHost.parse_size(settings.eval_memory) + 8 * GIB
        if available < needed:
            print(
                f"⚠️  Not graded: {available / GIB:.0f} GiB of memory is available and {settings.eval_workers} "
                f"workers of {settings.eval_memory} plus the 8 GiB reserve need {needed / GIB:.0f}."
            )
            return 1
        if remove_images:
            # One repository at a time, in chunks, as `run --eval --remove-images` does: an image this
            # grading pulled goes once its chunk is graded, so the disk holds one chunk's.
            chunk = settings.image_chunk if image_chunk is None else image_chunk
            manifest = store.manifest() or {}
            rows = {
                row["instance_id"]: row
                for row in SweBenchHarness.rows(manifest.get("dataset", "verified"))
            }
            ids = [
                prediction["instance_id"]
                for prediction in store.predictions()
                if prediction["instance_id"] in rows
            ]
            results = {}
            for group in SweBenchRunner.image_chunks(ids, rows, chunk):
                images = [
                    manifest["images"][i]["image"] for i in group if i in manifest.get("images", {})
                ]
                absent = [image for image in images if SweBenchDocker.image_digest(image) is None]
                results = SweBenchEvaluator.grade(store, settings, only=group, variant=variant)
                SweBenchImageCache.release(
                    [image for image in absent if SweBenchDocker.image_digest(image)], settings
                )
        else:
            results = SweBenchEvaluator.grade(store, settings, variant=variant)
        resolved = sum(1 for result in results.values() if result.get("resolved"))
        if variant == DROP_TEST_HUNKS:
            changed = sum(1 for result in results.values() if result.get("dropped"))
            print(
                f"✅ {store.name}, test files dropped: {len(results)} graded, {resolved} resolved; "
                f"{changed} patch(es) lost test files. `ling-admin swe-bench report {store.name} --drop-test-hunks "
                f"--against {store.name}` compares it with the plain grading."
            )
            return 0
        print(
            f"✅ {store.name}: {len(results)} graded, {resolved} resolved. "
            f"`ling-admin swe-bench report {store.name}` prints the report."
        )
        return 0

    @classmethod
    def shard(
        cls,
        source: Path,
        count: int,
        out_dir: Path | None = None,
        dataset: str = "verified",
        balance_by: list[Path] | None = None,
    ) -> int:
        """Writes a task list's shards as lists of their own (`SweBenchShards.write`).

        They are the same tasks `run --subset <list> --shard I/K` covers on each machine, unless
        `balance_by` names lists whose tasks are spread evenly too.

        Args:
            source: The task list.
            count: How many shards.
            out_dir: Where to write them; None for the list's own folder.
            dataset: The dataset the ids are from, for their repositories.
            balance_by: Other lists whose tasks each shard holds as evenly as the split can.

        Returns:
            int: 0 when the shards were written.
        """
        if SweBenchShards.parse(f"1/{count}") is None:
            print(f"❌ --shards {count}: from 1 to {swe_bench_shards.MAX_SHARDS}.")  # noqa: T201 — CLI output
            return 1

        def read(path: Path) -> list[str] | None:
            try:
                return [
                    line.strip()
                    for line in path.read_text().splitlines()
                    if line.strip() and not line.startswith("#")
                ]
            except OSError as error:
                print(f"❌ {path}: {error}")  # noqa: T201 — CLI output
                return None

        ids = read(source)
        others = {path.name: read(path) for path in balance_by or []}
        if ids is None or any(other is None for other in others.values()):
            return 1
        empty = [name for name, other in others.items() if not set(other or []) & set(ids)]
        if empty:
            print(f"❌ --balance-by {', '.join(empty)}: no task of {source.name} is in it.")  # noqa: T201 — CLI output
            return 1
        rows = {row["instance_id"]: row for row in SweBenchHarness.rows(dataset)}
        unknown = sorted(set(ids) - set(rows))
        if not rows or unknown:
            print(  # noqa: T201 — CLI output
                f"❌ Not in {dataset}: "
                f"{', '.join(unknown[:5]) or 'the dataset is not downloaded (swe-bench setup)'}."
            )
            return 1
        repos = {instance_id: str(rows[instance_id].get("repo", "")) for instance_id in ids}
        balanced = {name: other for name, other in others.items() if other is not None}
        for path in SweBenchShards.write(
            source, ids, repos, count, out_dir or source.parent, balanced or None
        ):
            shard = [
                line for line in path.read_text().splitlines() if line and not line.startswith("#")
            ]
            print(f"✅ {path}: {len(shard)} tasks, {SweBenchShards.mix(shard, repos)}")  # noqa: T201 — CLI output
        return 0

    @classmethod
    def report(cls, name: str | None, against: str | None, variant: str | None = None) -> int:
        """Prints a run's report, or its comparison with another run.

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
        """Prints the runs, their progress, the validated list, images and disk.

        Returns:
            int: 0.
        """
        version = SweBenchHarness.installed_version()
        print(
            f"Harness: {'swebench ' + version if version else 'not installed (ling-admin swe-bench setup)'}"
        )
        print(
            f"Smoke: {'passed' if SweBenchRunner.smoke_passed() else 'not passed with this harness version'}"
        )
        print(
            f"Validated here: {len(SweBenchImages.validated())} instance(s); "
            f"rejected: {len(SweBenchImages.rejected())}"
        )
        for name in SweBenchRunStore.runs():
            summary = SweBenchReport.summary(SweBenchRunStore(name), details=False) or {}
            print(
                f"  {name}: {summary.get('finished', 0)} of {summary.get('validated', 0)} run, "
                f"{summary.get('graded', 0)} graded, {summary.get('resolved', 0)} resolved"
            )
        images = SweBenchImages.local_images()
        print(f"Images: {len(images)} instance image(s) present")
        settings = swe_bench_settings.SweBenchSettings()
        if settings.image_cache_lists:
            cached = SweBenchImageCache.cached_images(settings)
            kept = sum(1 for entry in images if entry["image"] in cached)
            print(  # noqa: T201 — CLI output
                f"Image cache: {kept} of the {len(cached)} image(s) of {', '.join(settings.image_cache_lists)} "
                f"present; floor {SweBenchImageCache.floor_bytes(settings) / 1024**3:.0f} GiB free"
            )
        swe_bench_settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(swe_bench_settings.CACHE_DIR).free
        print(
            f"Disk: {free / 1024**3:.0f} GiB free; reserve {swe_bench_settings.SweBenchSettings().disk_reserve}"
        )
        from dreamference.config import DreamferenceConfig
        from dreamference.vllm_server.model_gate import ModelGate

        for line in ModelGate.describe(DreamferenceConfig().vllm_host):
            print(line)
        return 0

    @classmethod
    def clean(cls, name: str | None, images: bool) -> int:
        """Removes agent containers and scratch directories, and with `images` the instance images.

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
        for run in [name] if name else SweBenchRunStore.runs():
            shutil.rmtree(SweBenchRunStore(run).directory / "scratch", ignore_errors=True)
        print(
            f"🧹 Removed {len(containers)} container(s) and the scratch of {name or 'every run'}."
        )
        if images:
            present = [entry["image"] for entry in SweBenchImages.local_images()]
            removed = SweBenchImages.remove(present)
            print(f"🧹 Removed {removed} of {len(present)} instance image(s).")
        return 0
