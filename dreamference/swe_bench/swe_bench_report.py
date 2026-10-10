"""
The report of a run, and the comparison of two (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §6.3, §8).

Every figure is printed with what it is not: a number from this command compares configurations
on this machine and is not a leaderboard score.
"""

import math
import statistics
from typing import Any, Dict, Final, List, Optional

from dreamference.swe_bench.swe_bench_evaluator import DROP_TEST_HUNKS, SweBenchEvaluator
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
    "cave_mode", "prompt", "prompt_sha256", "airgapped", "code_index", "masking", "issue_text", "refine", "refine_version", "task_rules", "task_context", "task_timeout_s", "task_memory", "nudges",
    "parallelism", "harness", "repository_commit", "review_turn", "hooks", "apply_patch",
)
# What a manifest written before a field existed ran with.
MISSING_FIELDS: Final[dict] = {"code_index": "off", "prompt": "default", "masking": "off", "issue_text": "verbatim", "refine": False,
                               "refine_version": "v1",
                               "task_rules": [], "review_turn": False, "hooks": [], "apply_patch": "auto"}


class SweBenchReport:
    """Renders reports."""

    @classmethod
    def summary(cls, store: SweBenchRunStore, variant: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Counts a run.

        Args:
            store: The run.
            variant: The grading series the verdicts come from; None for the plain one
                (`drop-test-hunks`: the same predictions with their test files left out).

        Returns:
            Optional[Dict[str, Any]]: The counts and per-instance verdicts; None when the run
            does not exist.
        """
        manifest = store.manifest()
        if manifest is None:
            return None
        number, grader, results = SweBenchEvaluator.results(store, variant)
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
            "manifest": manifest, "grading": number, "grader": grader, "results": results, "variant": variant,
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
            "refine": cls.refine_summary(store, manifest, states, finished & set(instances)),
            "nudges_fired": cls.nudge_counts(states, finished & set(instances)),
            "review": cls.review_summary(store, manifest, states, finished & set(instances)),
            "apply_patch": cls.apply_patch_summary(manifest, states, finished & set(instances)),
            "hooks": cls.hooks_summary(manifest, states, finished & set(instances)),
            "dropped": {i: results[i]["dropped"] for i in graded if results[i].get("dropped")},
        }

    @classmethod
    def nudge_counts(cls, states: Dict[str, Any], finished: Any) -> Dict[str, int]:
        """
        Counts the nudges that fired, by kind: `stall` (the tree unchanged and the last message
        announcing work) and `completion` (the tree changed and the turn ended mid-work).

        Args:
            states: The instances' states.
            finished: The instances that have a prediction.

        Returns:
            Dict[str, int]: Kind to the number of times it fired. A state written before the
            kinds were recorded counts its nudges as `stall`, the only kind there was.
        """
        counts: Dict[str, int] = {}
        for instance_id in finished:
            state = states.get(instance_id) or {}
            kinds = state.get("nudge_kinds")
            if kinds is None:
                kinds = ["stall"] * int(state.get("nudges") or 0)
            for kind in kinds:
                counts[kind] = counts.get(kind, 0) + 1
        return counts

    @classmethod
    def refine_summary(cls, store: SweBenchRunStore, manifest: Dict[str, Any], states: Dict[str, Any],
                       finished: Any) -> Optional[Dict[str, Any]]:
        """
        The refine arm's two steps, counted apart: their time, their tokens, and how often the
        first step changed the tree it was told not to change.

        Args:
            store: The run.
            manifest: Its manifest.
            states: Its instances' states.
            finished: The instances that have a prediction.

        Returns:
            Optional[Dict[str, Any]]: None for a run without the refine arm.
        """
        if not manifest.get("refine"):
            return None
        records = {i: (states.get(i) or {}).get("refine") for i in finished}
        records = {i: record for i, record in records.items() if record}
        first = {key: 0 for key in ("input_tokens", "cached_input_tokens", "output_tokens")}
        for instance_id, record in records.items():
            stats = store.log_stats(instance_id, 0, record.get("log_offset"))
            for key in first:
                first[key] += stats[key]
        return {
            "version": manifest.get("refine_version") or "v1",
            "instances": len(records),
            "refine_s": [record.get("refine_s", 0) for record in records.values()],
            "fix_s": [record["fix_s"] for record in records.values() if "fix_s" in record],
            "edited": sum(1 for record in records.values() if record.get("refine_edited")),
            "empty": sum(1 for record in records.values() if not record.get("refined_bytes")),
            "timeouts": sum(1 for record in records.values() if record.get("refine_exec") == "timeout"),
            "refine_tokens": first,
        }

    @classmethod
    def review_summary(cls, store: SweBenchRunStore, manifest: Dict[str, Any], states: Dict[str, Any],
                       finished: Any) -> Optional[Dict[str, Any]]:
        """
        What the review turn did (spec §19): where it ran and why not elsewhere, how often it
        changed the patch and by how many lines, its time limits reached, its time and tokens.

        Args:
            store: The run.
            manifest: Its manifest.
            states: Its instances' states.
            finished: The instances that have a prediction.

        Returns:
            Optional[Dict[str, Any]]: None for a run without the review turn.
        """
        if not manifest.get("review_turn"):
            return None
        records = {i: (states.get(i) or {}).get("review") for i in finished}
        ran = {i: record for i, record in records.items() if record and "skipped" not in record}
        skipped: Dict[str, int] = {}
        for record in records.values():
            if record and "skipped" in record:
                skipped[record["skipped"]] = skipped.get(record["skipped"], 0) + 1
        tokens = {key: 0 for key in ("input_tokens", "cached_input_tokens", "output_tokens")}
        for instance_id, record in ran.items():
            counted = record.get("tokens")
            if counted is None:
                counted = store.log_stats(instance_id, int(record.get("log_offset") or 0))
            for key in tokens:
                tokens[key] += int(counted.get(key) or 0)
        changed = sorted(i for i, record in ran.items() if record.get("changed"))
        return {
            "instances": len(records), "ran": len(ran), "skipped": skipped,
            "fresh": sum(1 for record in ran.values() if not record.get("resumed")),
            "changed": changed,
            "added": sum(int(record.get("added") or 0) for record in ran.values()),
            "removed": sum(int(record.get("removed") or 0) for record in ran.values()),
            "timeouts": sorted(i for i, record in ran.items() if record.get("exec") == "timeout"),
            "seconds": [int(record.get("seconds") or 0) for record in ran.values()],
            "tokens": tokens,
        }

    @classmethod
    def apply_patch_summary(cls, manifest: Dict[str, Any], states: Dict[str, Any], finished: Any) -> Dict[str, Any]:
        """
        The apply_patch arm's measures (spec §22): the form the run asked for and, over the
        finished instances with a record, how many called the tool at all and how many calls.

        Returns:
            Dict[str, Any]: `form`, `instances`, `recorded`, `users` (ids that called it), `calls`.
        """
        form = str(manifest.get("apply_patch") or "auto")
        records = {i: (states.get(i) or {}).get("apply_patch") for i in finished}
        recorded = {i: r for i, r in records.items() if isinstance(r, dict)}
        users = sorted(i for i, r in recorded.items() if int(r.get("calls") or 0))
        return {"form": form, "instances": len(records), "recorded": len(recorded), "users": users,
                "calls": sum(int(r.get("calls") or 0) for r in recorded.values())}

    @classmethod
    def apply_patch_line(cls, summary: Dict[str, Any]) -> str:
        """One line: the apply_patch form and how much the agent used the tool."""
        head = f"Apply patch         {summary['form']}"
        if summary["form"] == "auto":
            head += " (the launcher's choice: freeform on ling-engine, none on SGLang)"
        if not summary["recorded"]:
            return head + ": no finished instance recorded its calls" if summary["instances"] else head
        return (f"{head}: called in {len(summary['users'])} of {summary['recorded']} finished instance(s), "
                f"{summary['calls']} call(s)" + (": " + ", ".join(summary["users"]) if summary["users"] else ""))

    @classmethod
    def review_line(cls, review: Optional[Dict[str, Any]]) -> str:
        """
        Says whether the review turn was on and, when it was, what it did.

        Args:
            review: The summary's `review` entry; None when the run had no review turn.

        Returns:
            str: One line of the report.
        """
        if review is None:
            return "Review turn         off: the patch was collected when the agent stopped"
        if not review["ran"]:
            return (f"Review turn         on: it ran in none of {review['instances']} finished instance(s)"
                    + "".join(f"; {reason}: {count}" for reason, count in sorted(review["skipped"].items())))
        tokens = review["tokens"]
        skipped = ", ".join(f"{reason} {count}" for reason, count in sorted(review["skipped"].items()))
        return (f"Review turn         on: ran in {review['ran']} of {review['instances']}"
                + (f" (not run: {skipped})" if skipped else "")
                + (f", {review['fresh']} as a fresh session" if review["fresh"] else "")
                + f"; changed the patch in {len(review['changed'])} (+{review['added']} -{review['removed']} lines)"
                + f", reached the time limit in {len(review['timeouts'])}"
                + f"; median {cls.duration(statistics.median(review['seconds']))}, "
                  f"{cls.duration(sum(review['seconds']))} in all; {tokens['input_tokens']:,} tokens in, "
                  f"{tokens['output_tokens']:,} out")

    @classmethod
    def hooks_summary(cls, manifest: Dict[str, Any], states: Dict[str, Any], finished: Any) -> Optional[Dict[str, Any]]:
        """
        What the hooks did (spec §20): where they ran at all, what the edit hold and the stop
        hold held, and whether the agent then did what it was told.

        Args:
            manifest: The run's manifest.
            states: Its instances' states.
            finished: The instances that have a prediction.

        Returns:
            Optional[Dict[str, Any]]: None for a run without hooks.
        """
        if not manifest.get("hooks"):
            return None
        records = {i: (states.get(i) or {}).get("hooks") or {} for i in sorted(finished)}

        def held(key: str) -> Dict[str, Any]:
            holds = {i: r[key] for i, r in records.items() if r.get(key)}
            return {"fired": sorted(holds), "complied": sum(1 for h in holds.values() if h.get("complied") is True),
                    "not": sum(1 for h in holds.values() if h.get("complied") is False),
                    "undecided": sum(1 for h in holds.values() if h.get("complied") is None)}

        first = [r["first_edit"] for r in records.values() if r.get("first_edit") and r["first_edit"].get("of")]
        measured = [r.get("example_run_after_last_edit") for r in records.values()
                    if r.get("example_run_after_last_edit") is not None]
        return {
            "sets": manifest["hooks"], "instances": len(records),
            "never_ran": sorted(i for i, r in records.items() if not r.get("ran")),
            "with_targets": sum(1 for r in records.values() if r.get("targets")),
            "first_edit_read": sum(f["read"] for f in first), "first_edit_of": sum(f["of"] for f in first),
            "first_edit_all": sum(1 for f in first if f["read"] == f["of"]), "first_edits": len(first),
            "edit_hold": held("edit_hold"), "stop_hold": held("stop_hold"),
            "with_examples": sum(1 for r in records.values() if r.get("examples")),
            "example_after_edit": sum(1 for value in measured if value), "example_measured": len(measured),
            "errors": sorted(i for i, r in records.items() if r.get("errors")),
        }

    @classmethod
    def hooks_lines(cls, hooks: Optional[Dict[str, Any]]) -> List[str]:
        """
        Says whether hooks were on and, when they were, what they held and whether the agent
        complied.

        Args:
            hooks: The summary's `hooks` entry; None when the run had none.

        Returns:
            List[str]: Lines of the report.
        """
        if hooks is None:
            return ["Hooks               off: no rule was enforced in the session"]
        ran = hooks["instances"] - len(hooks["never_ran"])
        lines = [f"Hooks               {', '.join(hooks['sets'])}: ran in {ran} of {hooks['instances']} finished instance(s)"]
        if hooks["never_ran"]:
            lines.append(f"  never ran in {len(hooks['never_ran'])} ({', '.join(hooks['never_ran'][:10])}"
                         + (", ..." if len(hooks["never_ran"]) > 10 else "")
                         + "): those ran as without hooks (an untrusted hook is skipped in silence)")
        edit, stop = hooks["edit_hold"], hooks["stop_hold"]
        lines.append(f"  edit hold         held the first edit in {len(edit['fired'])}: then read what it named "
                     f"{edit['complied']}, did not {edit['not']}, edited no more {edit['undecided']}; "
                     f"{hooks['with_targets']} issue(s) named something to read, "
                     f"{hooks['first_edit_all']} of {hooks['first_edits']} first edits came after reading all of it "
                     f"({hooks['first_edit_read']} of {hooks['first_edit_of']} items)")
        lines.append(f"  stop hold         held the first stop in {len(stop['fired'])}: then ran the example "
                     f"{stop['complied']}, did not {stop['not']}, stopped no more {stop['undecided']}; "
                     f"{hooks['with_examples']} issue(s) showed an example, run after the last edit at the "
                     f"last stop in {hooks['example_after_edit']} of {hooks['example_measured']}")
        if hooks["errors"]:
            lines.append(f"  gate errors in {len(hooks['errors'])}: {', '.join(hooks['errors'][:10])} (see state.hooks.errors)")
        return lines

    @classmethod
    def render(cls, store: SweBenchRunStore, variant: Optional[str] = None) -> Optional[str]:
        """
        Renders a run's report.

        Args:
            store: The run.
            variant: The grading series to report; None for the plain one.

        Returns:
            Optional[str]: The report text; None when the run does not exist.
        """
        summary = cls.summary(store, variant)
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
            f"Nudges              stall {summary['nudges_fired'].get('stall', 0)}     "
            f"completion {summary['nudges_fired'].get('completion', 0)}",
        ]
        if manifest.get("task_rules"):
            lines.append(f"Task rules          {', '.join(manifest['task_rules'])} (lines added to the task prompt)")
        lines.append(cls.review_line(summary["review"]))
        lines.append(cls.apply_patch_line(summary["apply_patch"]))
        lines += cls.hooks_lines(summary["hooks"])
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
        lines += cls.gate_lines(store, summary["states"], manifest.get("instances", []))
        if manifest.get("issue_text") == "names stripped":
            stripped = manifest.get("stripped_issues") or {}
            changed = sum(1 for entry in stripped.values() if entry.get("replaced"))
            lines.append(f"Issue text          names stripped: the files, modules, functions and classes the reference "
                         f"fix touches were taken out of {changed} of {len(stripped)} issues; the rest named none of them")
        if summary["refine"] is not None:
            lines.append(cls.refine_line(summary["refine"]))
        if summary["grading"] is not None:
            grader = summary["grader"]
            lines.append(f"Grading {summary['grading']}: harness {grader.get('harness')}, dataset revision "
                         f"{str(grader.get('dataset_revision'))[:12]}"
                         + (f", test files reset {grader['eval_reset']}" if grader.get("eval_reset") else ""))
            if variant == DROP_TEST_HUNKS:
                emptied = sum(1 for result in summary["results"].values() if result.get("empty") and result.get("dropped"))
                lines.append(f"Test files dropped  regrade of the same predictions with every test file left out of each "
                             f"patch: {len(summary['dropped'])} patch(es) changed, {emptied} left empty")
        else:
            flag = f" --{variant}" if variant else ""
            lines.append(f"Not graded yet: run `ling-admin swe-bench eval{flag} " + store.name + "`")
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
        arm = summary["code_index"]
        if arm == "exact":
            indexes = [state.get("index") or {} for state in summary["states"].values() if state.get("index")]
            peak = max((int(index.get("peak_mb") or 0) for index in indexes), default=0)
            partial = sum(1 for index in indexes if index.get("failed"))
            bare = sum(1 for index in indexes if not index.get("stores"))
            arm = (f"exact (SCIP stores only, no graph; peak {peak} MiB per indexer run; "
                   f"{partial} instance(s) with an indexer that did not finish, {bare} with no store at all)")
        return (f"Code index          {arm}: {built}; the agent called ling-code "
                f"{summary['puffin_code_calls']} time(s), in {summary['puffin_code_users']} of "
                f"{summary['finished']} instance(s)")

    @classmethod
    def refine_line(cls, refine: Dict[str, Any]) -> str:
        """
        Says what the refine arm's first step cost and did.

        Args:
            refine: The summary's `refine` entry.

        Returns:
            str: One line of the report.
        """
        state = f"on ({refine.get('version', 'v1')})"
        if not refine["instances"]:
            return f"Refine first        {state}: no instance has finished its first step yet"
        tokens = refine["refine_tokens"]
        fix = (f", median {cls.duration(statistics.median(refine['fix_s']))} fixing"
               if refine["fix_s"] else "")
        return (f"Refine first        {state}: median {cls.duration(statistics.median(refine['refine_s']))} studying"
                f"{fix}; the first step took {cls.duration(sum(refine['refine_s']))} and "
                f"{tokens['input_tokens']:,} tokens in, {tokens['output_tokens']:,} out; it wrote nothing in "
                f"{refine['empty']}, timed out in {refine['timeouts']} and changed the tree in {refine['edited']} "
                f"of {refine['instances']} (discarded before fixing)")

    @classmethod
    def write(cls, store: SweBenchRunStore, variant: Optional[str] = None) -> Optional[str]:
        """
        Renders a run's report and writes it to `report.md` in the run directory
        (`report-<variant>.md` for another grading series).

        Args:
            store: The run.
            variant: The grading series to report; None for the plain one.

        Returns:
            Optional[str]: The report text; None when the run does not exist.
        """
        text = cls.render(store, variant)
        if text is not None:
            (store.directory / (f"report-{variant}.md" if variant else "report.md")).write_text(text)
        return text

    @classmethod
    def against(cls, store: SweBenchRunStore, other: SweBenchRunStore, variant: Optional[str] = None) -> str:
        """
        Compares two runs instance by instance.

        Args:
            store: The run being reported.
            other: The run it is compared with (it may be the same run).
            variant: The grading series of `store`'s verdicts; `other`'s are always the plain
                grading's, so `report X --drop-test-hunks --against X` says what dropping the
                test files changes.

        Returns:
            str: The comparison, or the reason the two cannot be compared.
        """
        ours, theirs = cls.summary(store, variant), cls.summary(other)
        if ours is None or theirs is None:
            return f"No such run: {store.name if ours is None else other.name}\n"
        name = f"{store.name} [{variant}]" if variant else store.name
        a, b = ours["manifest"], theirs["manifest"]
        if a.get("dataset") != b.get("dataset") or a.get("dataset_revision") != b.get("dataset_revision"):
            return (f"Not compared: {name} and {other.name} use different datasets "
                    f"({a.get('dataset')}@{str(a.get('dataset_revision'))[:8]} against "
                    f"{b.get('dataset')}@{str(b.get('dataset_revision'))[:8]}).\n")
        if sorted(a.get("instances", [])) != sorted(b.get("instances", [])):
            return (f"Not compared: {name} and {other.name} cover different instances "
                    f"({len(a.get('instances', []))} against {len(b.get('instances', []))}). "
                    "A difference between them would say nothing about the configurations.\n")
        both = [i for i in a["instances"] if i in ours["results"] and i in theirs["results"]]
        only_ours = sorted(i for i in both if ours["results"][i].get("resolved") and not theirs["results"][i].get("resolved"))
        only_theirs = sorted(i for i in both if theirs["results"][i].get("resolved") and not ours["results"][i].get("resolved"))
        lines = [f"{name} against {other.name}: {len(both)} instance(s) graded in both"]
        for field in COMPARED_FIELDS:
            # Runs made before the code-index arm or named prompts existed have no such field.
            first, second = (m.get(field, MISSING_FIELDS.get(field)) for m in (a, b))
            if first != second:
                lines.append(f"  differs: {field}: {first} | {second}")
        # Timings are only comparable where the model was the run's alone (§18).
        gates = [cls.gate_lines(run, summary["states"], summary["manifest"].get("instances", []))
                 for run, summary in ((store, ours), (other, theirs))]
        if gates[0][:1] != gates[1][:1]:
            lines.append(f"  differs: model gate: {(gates[0][:1] or ['no record'])[0].split('  ', 1)[-1].strip()} | "
                         f"{(gates[1][:1] or ['no record'])[0].split('  ', 1)[-1].strip()}")
        for run_name, gate in zip((name, other.name), gates if other.name != store.name else gates[:1]):
            if len(gate) > 1:
                lines.append(f"  note: {run_name} paused its model gate; see its report for the instances "
                             "whose times are not comparable")
        lines.append(f"Resolved only by {name} ({len(only_ours)}): {', '.join(only_ours) or 'none'}")
        lines.append(f"Resolved only by {other.name} ({len(only_theirs)}): {', '.join(only_theirs) or 'none'}")
        if both:
            low, high = cls.paired_interval(len(only_ours), len(only_theirs), len(both))
            difference = (len(only_ours) - len(only_theirs)) / len(both)
            p_value = cls.mcnemar(len(only_ours), len(only_theirs))
            lines.append(f"Difference in resolved rate: {100 * difference:+.1f} points "
                         f"(95% interval {100 * low:+.1f} to {100 * high:+.1f}; McNemar exact p = {p_value:.3f})")
            if low <= 0 <= high:
                lines.append("No measurable difference.")
        lines += cls.arms(name, ours, other.name, theirs, both)
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
                "issue text": summary["manifest"].get("issue_text", "verbatim"),
                "refine first": f"on ({summary['refine']['version']})" if summary["refine"] is not None else "off",
                "task rules": ",".join(summary["manifest"].get("task_rules") or []) or "none",
                "review turn": "on" if summary["review"] is not None else "off",
                "hooks": ",".join(summary["manifest"].get("hooks") or []) or "none",
                "grading": "test files dropped" if summary.get("variant") == DROP_TEST_HUNKS else "plain",
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
        for summary, run in ((ours, name), (theirs, other)):
            hooks = summary.get("hooks")
            if hooks is not None:
                ran = [i for i in both if i not in hooks["never_ran"]]
                if not ran:
                    lines.append(f"In {run} the hooks never ran: this comparison says nothing about them.")
                elif len(ran) < len(both):
                    lines.append(f"In {run} the hooks ran in {len(ran)} of {len(both)} instances; "
                                 "the others ran as if there were none.")
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
    def gate_lines(cls, store: SweBenchRunStore, states: Dict[str, Any], instances: List[str]) -> List[str]:
        """
        What the model gate did during a run (§18): whether it refused other requests, and the
        pauses that let them through, with the instances that ran during one, whose times are
        not comparable with the rest. Nothing for a run made before the gate existed.

        Args:
            store: The run.
            states: Its instances' states.
            instances: The instances it covers.

        Returns:
            List[str]: Report lines.
        """
        from datetime import datetime

        from dreamference.swe_bench.swe_bench_gate_hold import SweBenchGateHold
        record = SweBenchGateHold.read(store)
        sessions, pauses = record["sessions"], record["pauses"]
        if not sessions:
            return []
        kinds = {session.get("gate") for session in sessions}
        if kinds == {"in force"}:
            lines = ["Model gate          in force: requests from anything but the run were refused"]
        elif "in force" in kinds:
            lines = ["Model gate          in force for part of the run only; in the rest other requests were "
                     "served beside it and held its starts back"]
        else:
            lines = ["Model gate          none in front of the model server: other requests were served beside "
                     "the run and held its starts back, as before the gate"]
        if not pauses:
            return lines
        stamp = lambda t: datetime.fromtimestamp(t).strftime("%m-%d %H:%M")
        total = sum(max(0.0, pause["end"] - pause["start"]) for pause in pauses)
        spans = ", ".join(f"{stamp(pause['start'])}-{datetime.fromtimestamp(pause['end']):%H:%M}" for pause in pauses)
        shared = []
        for instance_id in instances:
            state = states.get(instance_id) or {}
            try:
                started = datetime.strptime(state["started"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
                ended = started + float(state["wall_s"])
            except (KeyError, TypeError, ValueError):
                continue
            if any(started < pause["end"] and ended > pause["start"] for pause in pauses):
                shared.append(instance_id)
        lines.append(f"Gate paused         {len(pauses)} time(s), {cls.duration(total)} in all ({spans}); "
                     f"{len(shared)} instance(s) ran during a pause and may have shared the model with other "
                     "requests, so their times are not comparable"
                     + (f": {', '.join(shared)}" if shared else ""))
        return lines

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
