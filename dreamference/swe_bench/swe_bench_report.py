"""
The report of a run, and the comparison of two (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §6.3, §8).

Every figure is printed with what it is not: a number from this command compares configurations
on this machine and is not a leaderboard score.
"""

import math
import statistics
from typing import Any, Dict, Final, List, Optional

from dreamference.swe_bench.swe_bench_evaluator import SweBenchEvaluator
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore

CAVEATS: Final[str] = (
    "Not comparable with published SWE-bench scores (spec §8): the benchmark is contaminated; "
    "these are third-party arm64 images and only the instances that validate here, not the "
    "official x86_64 set; the agent has no network and the model is a quantised build; and one "
    "run is one sample."
)

# Manifest fields `--against` lists when they differ between two runs.
COMPARED_FIELDS: Final[tuple] = (
    "model_name_or_path", "served_model", "model_alias", "puffin_version", "runtime_hash",
    "cave_mode", "prompt", "prompt_sha256", "airgapped", "code_index", "masking", "task_context", "task_timeout_s", "task_memory", "nudges",
    "parallelism", "harness", "repository_commit",
)
# What a manifest written before a field existed ran with.
MISSING_FIELDS: Final[dict] = {"code_index": "off", "prompt": "default", "masking": "off"}


class SweBenchReport:
    """Renders reports."""

    @classmethod
    def summary(cls, store: SweBenchRunStore) -> Optional[Dict[str, Any]]:
        """
        Counts a run.

        Args:
            store: The run.

        Returns:
            Optional[Dict[str, Any]]: The counts and per-instance verdicts; None when the run
            does not exist.
        """
        manifest = store.manifest()
        if manifest is None:
            return None
        number, grader, results = SweBenchEvaluator.results(store)
        states = store.states()
        instances: List[str] = manifest.get("instances", [])
        finished = set(store.finished())
        statuses: Dict[str, int] = {}
        for instance_id in instances:
            if instance_id in finished:
                status = (states.get(instance_id) or {}).get("status", "?")
                statuses[status] = statuses.get(status, 0) + 1
        resolved = [i for i in instances if (results.get(i) or {}).get("resolved")]
        graded = [i for i in instances if i in results]
        walls = [state["wall_s"] for i, state in states.items() if i in finished and "wall_s" in state]
        stats = {i: store.log_stats(i) for i in instances if i in finished}
        index_seconds = [float((state.get("index") or {}).get("seconds") or 0)
                         for i, state in states.items() if i in finished and state.get("index")]
        per_repo: Dict[str, List[int]] = {}
        for instance_id in instances:
            counts = per_repo.setdefault(instance_id.rsplit("-", 1)[0].replace("__", "/"), [0, 0])
            counts[1] += 1
            counts[0] += 1 if instance_id in resolved else 0
        return {
            "manifest": manifest, "grading": number, "grader": grader, "results": results,
            "validated": len(instances), "excluded": len(manifest.get("excluded", {})),
            "finished": len(finished & set(instances)), "graded": len(graded),
            "resolved": len(resolved), "resolved_ids": resolved, "statuses": statuses,
            "test_patch_failed": sum(1 for i in graded if results[i].get("test_patch_failed")),
            "walls": walls, "per_repo": per_repo, "states": states, "stats": stats,
            "code_index": manifest.get("code_index", "off"), "index_seconds": index_seconds,
            "tokens": {key: sum(entry[key] for entry in stats.values())
                       for key in ("input_tokens", "cached_input_tokens", "output_tokens")},
            "commands": sum(entry["commands"] for entry in stats.values()),
            "puffin_code_calls": sum(entry["puffin_code_calls"] for entry in stats.values()),
            "puffin_code_users": sum(1 for entry in stats.values() if entry["puffin_code_calls"]),
        }

    @classmethod
    def render(cls, store: SweBenchRunStore) -> Optional[str]:
        """
        Renders a run's report.

        Args:
            store: The run.

        Returns:
            Optional[str]: The report text; None when the run does not exist.
        """
        summary = cls.summary(store)
        if summary is None:
            return None
        manifest = summary["manifest"]
        validated, selected = summary["validated"], summary["validated"] + summary["excluded"]
        rate = f"{100 * summary['resolved'] / validated:.1f}%" if validated else "n/a"
        statuses = summary["statuses"]
        lines = [
            f"{manifest.get('model_name_or_path')}       run {store.name}",
            f"{manifest.get('dataset')}, {manifest.get('architecture')}, images: {manifest.get('image_source')}",
            "",
            f"Resolved            {summary['resolved']} / {validated} validated   {rate}",
            f"Not evaluable       {summary['excluded']} of {selected} selected "
            f"({manifest.get('dataset_rows') or '?'} in the dataset): no {manifest.get('architecture')} image, "
            "or the instance does not validate here",
            f"Empty patch         {statuses.get('empty', 0)}     Stalled {statuses.get('stalled', 0)}     "
            f"Timeout {statuses.get('timeout', 0)}     Agent error {statuses.get('error', 0)}",
            f"Patch broke test_patch application     {summary['test_patch_failed']}",
        ]
        not_run = validated - summary["finished"]
        not_graded = summary["finished"] - summary["graded"]
        if not_run or not_graded:
            lines.append(f"INCOMPLETE          {not_run} not run yet, {not_graded} run but not graded yet: "
                         "they count as unresolved above")
        if summary["walls"]:
            lines.append(f"Median wall time    {cls.duration(statistics.median(summary['walls']))} per instance, "
                         f"up to {manifest.get('parallelism')} at once; "
                         f"{cls.duration(sum(summary['walls']))} of agent time in all")
        tokens = summary["tokens"]
        lines.append(f"Tokens              {tokens['input_tokens']:,} in ({tokens['cached_input_tokens']:,} cached), "
                     f"{tokens['output_tokens']:,} out; {summary['commands']:,} commands")
        lines.append(cls.code_index_line(summary))
        if summary["grading"] is not None:
            grader = summary["grader"]
            lines.append(f"Grading {summary['grading']}: harness {grader.get('harness')}, dataset revision "
                         f"{str(grader.get('dataset_revision'))[:12]}")
        else:
            lines.append("Not graded yet: run `ling-admin swe-bench eval " + store.name + "`")
        lines += ["", "Per repository (resolved / validated):"]
        for repo, (resolved, total) in sorted(summary["per_repo"].items()):
            lines.append(f"  {repo:<28} {resolved} / {total}")
        lines += ["", f"Prompt {manifest.get('prompt', 'default')}; cave mode {manifest.get('cave_mode')}; "
                      f"{manifest.get('task_context')} tokens per task; "
                      f"timeout {int(manifest.get('task_timeout_s', 0)) // 60} min; nudges {manifest.get('nudges')}.",
                  "", CAVEATS]
        return "\n".join(lines) + "\n"

    @classmethod
    def code_index_line(cls, summary: Dict[str, Any]) -> str:
        """
        Says whether the agent had `ling-code`, what the indexes cost and whether it used them.

        Args:
            summary: A run's summary.

        Returns:
            str: One line of the report.
        """
        if summary["code_index"] == "off":
            return "Code index          off: the agent had no ling-code and navigated with grep and find"
        seconds = summary["index_seconds"]
        built = (f"indexes took {cls.duration(sum(seconds))} in all, median {cls.duration(statistics.median(seconds))}, "
                 "outside the agent's time") if seconds else "no index time recorded"
        return (f"Code index          {summary['code_index']}: {built}; the agent called ling-code "
                f"{summary['puffin_code_calls']} time(s), in {summary['puffin_code_users']} of "
                f"{summary['finished']} instance(s)")

    @classmethod
    def write(cls, store: SweBenchRunStore) -> Optional[str]:
        """
        Renders a run's report and writes it to `report.md` in the run directory.

        Args:
            store: The run.

        Returns:
            Optional[str]: The report text; None when the run does not exist.
        """
        text = cls.render(store)
        if text is not None:
            (store.directory / "report.md").write_text(text)
        return text

    @classmethod
    def against(cls, store: SweBenchRunStore, other: SweBenchRunStore) -> str:
        """
        Compares two runs instance by instance.

        Args:
            store: The run being reported.
            other: The run it is compared with.

        Returns:
            str: The comparison, or the reason the two cannot be compared.
        """
        ours, theirs = cls.summary(store), cls.summary(other)
        if ours is None or theirs is None:
            return f"No such run: {store.name if ours is None else other.name}\n"
        a, b = ours["manifest"], theirs["manifest"]
        if a.get("dataset") != b.get("dataset") or a.get("dataset_revision") != b.get("dataset_revision"):
            return (f"Not compared: {store.name} and {other.name} use different datasets "
                    f"({a.get('dataset')}@{str(a.get('dataset_revision'))[:8]} against "
                    f"{b.get('dataset')}@{str(b.get('dataset_revision'))[:8]}).\n")
        if sorted(a.get("instances", [])) != sorted(b.get("instances", [])):
            return (f"Not compared: {store.name} and {other.name} cover different instances "
                    f"({len(a.get('instances', []))} against {len(b.get('instances', []))}). "
                    "A difference between them would say nothing about the configurations.\n")
        both = [i for i in a["instances"] if i in ours["results"] and i in theirs["results"]]
        only_ours = sorted(i for i in both if ours["results"][i].get("resolved") and not theirs["results"][i].get("resolved"))
        only_theirs = sorted(i for i in both if theirs["results"][i].get("resolved") and not ours["results"][i].get("resolved"))
        lines = [f"{store.name} against {other.name}: {len(both)} instance(s) graded in both"]
        for field in COMPARED_FIELDS:
            # Runs made before the code-index arm or named prompts existed have no such field.
            first, second = (m.get(field, MISSING_FIELDS.get(field)) for m in (a, b))
            if first != second:
                lines.append(f"  differs: {field}: {first} | {second}")
        lines.append(f"Resolved only by {store.name} ({len(only_ours)}): {', '.join(only_ours) or 'none'}")
        lines.append(f"Resolved only by {other.name} ({len(only_theirs)}): {', '.join(only_theirs) or 'none'}")
        if both:
            low, high = cls.paired_interval(len(only_ours), len(only_theirs), len(both))
            difference = (len(only_ours) - len(only_theirs)) / len(both)
            p_value = cls.mcnemar(len(only_ours), len(only_theirs))
            lines.append(f"Difference in resolved rate: {100 * difference:+.1f} points "
                         f"(95% interval {100 * low:+.1f} to {100 * high:+.1f}; McNemar exact p = {p_value:.3f})")
            if low <= 0 <= high:
                lines.append("No measurable difference.")
        lines += cls.arms(store.name, ours, other.name, theirs, both)
        lines += ["", CAVEATS]
        return "\n".join(lines) + "\n"

    @classmethod
    def arms(cls, name: str, ours: Dict[str, Any], other: str, theirs: Dict[str, Any],
             both: List[str]) -> List[str]:
        """
        Sets two runs side by side on the instances both graded: the rates, the cost, the code
        index, and one row per instance.

        Args:
            name: The first run's name.
            ours: Its summary.
            other: The second run's name.
            theirs: Its summary.
            both: The instances graded in both.

        Returns:
            List[str]: The lines of the table.
        """
        if not both:
            return []
        lines = ["", f"Side by side, on the {len(both)} instance(s) graded in both "
                     "(one run per arm is one sample of each):",
                 f"{'':<22}{name:>18}{other:>18}"]

        def row(label: str, first: Any, second: Any) -> str:
            return f"{label:<22}{str(first):>18}{str(second):>18}"

        def column(summary: Dict[str, Any]) -> Dict[str, Any]:
            stats = [summary["stats"].get(i, {}) for i in both]
            walls = [summary["states"].get(i, {}).get("wall_s") for i in both]
            walls = [wall for wall in walls if wall is not None]
            index = [float((summary["states"].get(i, {}).get("index") or {}).get("seconds") or 0) for i in both]
            resolved = sum(1 for i in both if summary["results"][i].get("resolved"))
            return {
                "code index": summary["code_index"],
                "resolved": f"{resolved} ({100 * resolved / len(both):.1f}%)",
                "median wall": cls.duration(statistics.median(walls)) if walls else "n/a",
                "agent time": cls.duration(sum(walls)),
                "input tokens": f"{sum(s.get('input_tokens', 0) for s in stats):,}",
                "output tokens": f"{sum(s.get('output_tokens', 0) for s in stats):,}",
                "commands": f"{sum(s.get('commands', 0) for s in stats):,}",
                "ling-code calls": sum(s.get("puffin_code_calls", 0) for s in stats),
                "instances using it": sum(1 for s in stats if s.get("puffin_code_calls")),
                "index time": cls.duration(sum(index)) if summary["code_index"] != "off" else "none",
            }

        first, second = column(ours), column(theirs)
        lines += [row(label, first[label], second[label]) for label in first]
        outcome = {(True, True): "both", (True, False): f"only {name}", (False, True): f"only {other}",
                   (False, False): "neither"}
        counts: Dict[str, int] = {}
        table = []
        for instance_id in both:
            a, b = ours["results"][instance_id], theirs["results"][instance_id]
            verdict = outcome[(bool(a.get("resolved")), bool(b.get("resolved")))]
            counts[verdict] = counts.get(verdict, 0) + 1
            cells = []
            for summary in (ours, theirs):
                state = summary["states"].get(instance_id, {})
                calls = summary["stats"].get(instance_id, {}).get("puffin_code_calls", 0)
                cells.append(f"{state.get('status', '?')} {state.get('wall_s', '?')} s"
                             + (f", {calls} ling-code" if summary["code_index"] != "off" else ""))
            table.append(f"  {instance_id:<34} {verdict:<16} {cells[0]:<28} {cells[1]}")
        lines.append("Resolved in " + ", ".join(f"{label}: {counts.get(label, 0)}"
                                                 for label in ("both", f"only {name}", f"only {other}", "neither")))
        for summary, run in ((ours, name), (theirs, other)):
            if summary["code_index"] != "off":
                users = sum(1 for i in both if summary["stats"].get(i, {}).get("puffin_code_calls"))
                if users == 0:
                    lines.append(f"In {run} the agent never called ling-code: this comparison says nothing about the index.")
                else:
                    lines.append(f"In {run} the agent called ling-code in {users} of {len(both)} instances; "
                                 "the others ran as if there were no index.")
        lines += ["", f"  {'instance':<34} {'resolved in':<16} {name:<28} {other}"] + table
        return lines

    @classmethod
    def mcnemar(cls, only_first: int, only_second: int) -> float:
        """
        The exact (binomial) McNemar test on the discordant pairs.

        Args:
            only_first: Instances only the first run resolved.
            only_second: Instances only the second run resolved.

        Returns:
            float: The two-sided p-value; 1.0 when there are no discordant pairs.
        """
        n = only_first + only_second
        if n == 0:
            return 1.0
        k = min(only_first, only_second)
        tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
        return min(1.0, 2 * tail)

    @classmethod
    def paired_interval(cls, only_first: int, only_second: int, pairs: int) -> tuple:
        """
        A 95% Wald interval for the difference of two paired proportions.

        Args:
            only_first: Instances only the first run resolved.
            only_second: Instances only the second run resolved.
            pairs: Instances both runs attempted.

        Returns:
            tuple: (low, high), as fractions.
        """
        difference = (only_first - only_second) / pairs
        variance = (only_first + only_second - (only_first - only_second) ** 2 / pairs) / pairs ** 2
        half = 1.96 * math.sqrt(max(variance, 0.0))
        return difference - half, difference + half

    @classmethod
    def duration(cls, seconds: float) -> str:
        """
        Args:
            seconds: A duration.

        Returns:
            str: `6 min 40 s`, `17 h 10 min`.
        """
        seconds = int(seconds)
        if seconds >= 3600:
            return f"{seconds // 3600} h {seconds % 3600 // 60} min"
        if seconds >= 60:
            return f"{seconds // 60} min {seconds % 60} s"
        return f"{seconds} s"
