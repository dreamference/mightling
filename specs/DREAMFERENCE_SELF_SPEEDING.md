# Self-Speeding — the drafter learns your code

**Status:** proposed. Nothing in this spec is implemented yet.
**Target:** the speculative drafter of the served model. For the default this is `z-lab/Qwen3.5-122B-A10B-DFlash`, drafting 12 tokens for `Intel/Qwen3.5-122B-A10B-int4-AutoRound`.
**Builds on:**
- `resolve_speculative_config()` and the `--draft-model` layering ([INFERENCE](./DREAMFERENCE_INFERENCE.md));
- the compile-cache signature (`_compile_cache_signature`), which already includes the draft model;
- `ModelDeepInspector.profile_acceptance_by_workload()` behind `ling-admin main-model inspect --deep`;
- the session logs in `~/.mightling/sessions/`;
- the overnight window of [MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md).

---

## 1. Goal

Make `ling` faster on *this user's* work by retraining the drafter on the user's own sessions, overnight and on this machine. Nothing leaves the host, and the answers do not change.

**Why it is safe to try.** Speculative decoding is lossless: the target model verifies every drafted token, so a better or worse drafter changes speed only, never output. The only risk is a slower machine, and §6 refuses to promote a drafter that is slower.

**Why it is worth trying.**
- **Speed is the complaint.** Single-stream speed is the number-one complaint about local agents on this hardware.
- **Measured headroom here:**
  - Since today's 14:18 restart, vLLM's counters show 2,306 accepted of 11,763 drafted tokens (19.6%), over the live slash-command suite and a few chats.
  - The registry records 19–38% acceptance on prose, while structured output saturates the draft window. The spread between task types is what adaptation targets.
- **Published results:**
  - Retraining the drafter on real traffic raised acceptance by 0.1–0.65 and cut latency 1.42–2.17×.
  - Domain-trained drafters gained 11–25%, and offline training beat online.
  - About 2k samples sufficed for structured domains.

**Non-goals:**
- **Changing the target model:** no fine-tuning of the model that answers.
- **Training while serving:** vLLM must be stopped (§5).
- **Sharing drafters:** their weights encode the user's code and mail, so they never leave the machine (§7).

---

## 2. Surface

No slash command. Tuning stops the model server, which the `ling` session asking for it depends on, so it cannot be triggered from inside an agent session. Everything is in `ling-admin`:

| Command | Effect |
|---|---|
| `ling-admin drafter stats [--since 7d]` | Acceptance per workload class from the measurement log (§3), plus the live counters. |
| `ling-admin drafter collect` | Builds the training set from sessions (§4). Reports size, class mix and what was excluded. The server may keep running. |
| `ling-admin drafter tune [--budget 3h]` | Trains a candidate (§5). Refuses unless vLLM is stopped. Checkpoints, so it can stop at the budget or window end and resume. |
| `ling-admin drafter evaluate <id>` | A/B against the current drafter (§6). Starts vLLM with the candidate, measures, and restores the server as it was. |
| `ling-admin drafter promote <id>` | Makes the candidate the drafter (§6.3). |
| `ling-admin drafter rollback` | Returns to the previous drafter, or the registry's. |
| `ling-admin drafter list` | Candidates, with training data size, dates, evaluation results and which one is active. |

**Scheduling.** With Night Shift enabled, `drafter.auto = true` lets the night run take the tail of its window:
1. once the task queue is empty, stop vLLM;
2. `collect`, `tune` and `evaluate` within the remaining budget;
3. restart vLLM with the drafter that serves best.

Promotion is never automatic unless `drafter.auto_promote = true`. The morning report ([NIGHT_SHIFT §5.6](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)) says what happened.

---

## 3. Phase 0 — measure before training

A week of numbers comes before any training, because without them nothing in §6 can be judged.

**The problem.** vLLM's counters (`vllm:spec_decode_num_accepted_tokens_total`, `…num_draft_tokens_total` and the per-position counters) are engine-wide and cumulative. Per-request acceptance is not exposed, so attributing real traffic to task types is impossible while requests overlap.

**Idle sampling.** `drafter stats` takes readings with the delta method `profile_acceptance_by_workload()` already uses: snapshot, send one request, snapshot again. It uses two probe sets:
- **fixed probes:** the existing `WORKLOAD_PROBES`, so readings are comparable over time;
- **replayed prompts:** up to 50 prompts sampled from the user's recent sessions and classified (§4.2), sent one at a time at temperature 0 when vLLM has been idle for 10 minutes.

**The log.** Each reading appends a line to `~/.cache/dreamference/drafters/acceptance.jsonl`:

```json
{"at": "…", "drafter": "z-lab/Qwen3.5-122B-A10B-DFlash", "class": "code", "accepted": 812, "drafted": 2940, "tau": 3.1, "tok_s": 47.6}
```

This log is the baseline, and it is what shows whether the user's work differs from what the stock drafter was trained on. If replayed prompts accept as well as the fixed probes, there is little to gain, and `drafter stats` says so.

---

## 4. Training data

### 4.1. Source

The source is Codex rollout files under `~/.mightling/sessions/YYYY/MM/DD/*.jsonl`: each turn's full request context and the target model's response. Only sessions served by the **current target model** are used. A drafter learns one target's distribution, and sessions from other models teach it the wrong one.

### 4.2. Selection

- **Classes:** each turn is classified as `code`, `structured`, `tool-call`, `prose` or `repetitive` by the same rules as the probes (code fences, JSON parse, tool-call markers).
- **Balance:** the set is balanced toward classes where §3 shows the most headroom.
- **Deduplication:** by response hash.
- **Held out:** 10% of sessions, chosen by session and not by turn, so that no conversation is split between training and evaluation.
- **Minimum size:** about 2k turns. Below it, `collect` refuses and says how many more are needed.

### 4.3. Exclusions

These are dropped, and `collect` reports each count:
- turns whose context contains Gmail results;
- turns whose context contains `ling-admin gmail read` output;
- `.env`-like content, and matches of a credential pattern list;
- sessions under paths listed in `drafter.exclude_paths`;
- ephemeral sessions (`--ephemeral` writes none anyway).

The drafter will still learn from the user's code: that is the point. §7 is what keeps it on the machine.

---

## 5. Training

**Framework: SpecForge** (MIT licence), which has DFlash training support.
- z-lab's DFlash code is MIT, but its training recipe is only announced.
- AdaFlash, which adapts DFlash drafters to the target's actual outputs (reported 1.69× against DFlash's 1.02× at concurrency 128), has public code with **no licence**. Its paper may be used; its code may not be copied.

**The hard constraint.** DFlash drafters are trained on the target model's hidden states, so the target must be loaded in the training process.
- The INT4 AutoRound checkpoint is about 71 GiB. That is possible only with vLLM stopped, when about 100 GB is available.
- Whether SpecForge can load that checkpoint on SM121 is **unverified**, and it is the first thing to establish. Both routes below meet that constraint.

**Route A:** SpecForge online training, with the target model loaded in its own framework.

**Route B:** capture the hidden states first, then train the drafter alone.
1. Run the training prompts through the target once, and store the hidden states the drafter conditions on (fp16, sharded, under `~/.cache/dreamference/drafters/<id>/states/`).
2. Free the target.
3. Train the drafter alone, in a few GiB.

Route B separates the two memory peaks and lets training resume without reloading the target. Disk cost has to be measured; a first estimate is tens of GB for 2k turns.

**Starting point.** Training fine-tunes the current drafter's weights, not a fresh one, so a short budget still helps.

**Memory and time.** Admission as in [MIGHTLING_CODE_INDEX §5.5](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md): available memory minus a reserve, checked against the last recorded peak for that step. A run that cannot fit is deferred, not attempted. Each step checkpoints, so `--budget` or the window's end stops it cleanly.

**Output.** `~/.cache/dreamference/drafters/<id>/` holds:
- the weights in the drafter's original format, loadable by vLLM as a `--draft-model` path;
- `meta.json`: target model, base drafter, data size, class mix, exclusions, training steps and time.

---

## 6. Evaluation and promotion

### 6.1. Protocol

`drafter evaluate <id>` starts vLLM with the candidate as the draft model. It measures, at temperature 0, with one request at a time:
- the fixed probes;
- the held-out session prompts, up to 200.

It then repeats the same measurement with the current drafter. Each run records:
- acceptance;
- mean acceptance length (τ);
- per-position acceptance;
- output tokens per second.

### 6.2. Correctness check

Speculative decoding is lossless, so at temperature 0 the two runs' outputs must be byte-identical.
- Any difference means a serving bug (a drafter or engine mismatch), not a quality change.
- It fails the evaluation outright, and the differing prompts are kept for inspection.

### 6.3. Promotion

**The candidate must win.**
- Held-out tokens per second improve by at least `drafter.min_gain` (default 5%).
- No class regresses by more than `drafter.max_regression` (default 3%).
- The correctness check passes.

**How it is applied.** The result is written as the configured `draft_model` path. `resolve_speculative_config()` already layers an explicit draft model onto a recipe that names an external drafter, keeping its method (`dflash`), depth and attention backend. Because the draft model is part of the compile-cache signature, the next start rebuilds the torch.compile cache: a one-time cold compile of 8–12 minutes, which the output announces.

**Rollback.** `drafter rollback` removes the override. The last three candidates are kept, and older ones are deleted with their captured states.

### 6.4. When the target model changes

A promoted drafter is tied to its target model in `meta.json`. If the configured main model differs, the override is ignored with a one-line warning, and the recipe's drafter is used.

---

## 7. Privacy

- **Training data:** sessions, the extracted training set and the captured states stay under `~/.cache/dreamference/drafters/`, mode `0700`.
- **The drafter weights are derived from the user's data** and are treated like it. No command exports them, and the paper and docs publish only aggregate acceptance numbers.
- **Network:** training runs with no network need beyond the already-cached base drafter and SpecForge's own installation. Once the egress airlock exists, `drafter tune` runs inside it.

---

## 8. Tests

- **Unit tests:**
  - session parsing and classification;
  - the exclusion rules, with a fixture session holding Gmail output and a fake credential;
  - held-out split by session;
  - admission refusal with vLLM running;
  - promotion thresholds;
  - rollback;
  - target-mismatch handling;
  - the draft-model override reaching `--speculative-config` JSON and the compile-cache signature.
- **Evaluation with a fake server:** a scripted `/metrics` and chat endpoint. The comparison logic and the correctness check are tested with identical and with divergent outputs.
- **Live smoke test (manual, needs a stopped server and time):** `collect` on this machine's sessions, then a 20-minute `tune --budget`, then `evaluate`. It proves the pipeline end to end; it does not have to win.

---

## 9. Acceptance criteria

- **Phase 0:** a week of `acceptance.jsonl` readings exists, and `drafter stats` shows acceptance per class for the stock drafter.
- **Pipeline:**
  - `collect → tune → evaluate` completes on this machine within one six-hour window, without an earlyoom kill;
  - the server ends in the state it started in.
- **Correctness:** it holds on every evaluation.
- **Promotion:** a promoted drafter shows the gain on held-out sessions in a later `drafter stats`, not only in its own evaluation.

---

## 10. Open questions

- **Loading the target in SpecForge:** can it load `Intel/Qwen3.5-122B-A10B-int4-AutoRound` (AutoRound INT4, hybrid GDN layers) on SM121? This is the first thing to verify. If it cannot, Route B needs another way to capture states. For example, a vLLM hidden-state capture path, if the pinned image has one.
- **Drafter licence:** the licence of the z-lab drafter weights, and whether fine-tuned derivatives may be kept locally, must be checked on its model card before §5 is built.
- **Tuning depth:** `profile_acceptance_by_workload()` already suggests a window size per class. Should a tuned drafter also get a tuned `num_speculative_tokens`, or is that a separate, simpler experiment to run first?
