# Puffin Cave Mode — `/cavemode`

**Status:** implemented (Phase 1) on 2026-10-01: `puffin-rs/src/cave.rs`, the level texts in `puffin-rs/cave/`, patch `0017-cave-mode`, `DreamferenceConfig.puffin_cave_mode`, and the tests of §8. The three Phase 1 checks ran against the live model (§10). The measurements in §1 were taken for it on 2026-09-30 and 2026-10-01; the §9 proposals that change a level's text are not in, because each needs a Phase 0 re-run first.
**Goal:** `puffin` answers tersely by default, so the model spends its slowest tokens (prose, 25.5 tok/s) and the user's reading time only on what the user needs. `/cavemode` switches the level at any moment, mid-turn included.
**Builds on:**
- Codex's World State sections and its extension registry: a `ContextContributor` can add a section that reaches the model's history only when its value changes, as the `git-attribution` extension does (`codex-rs/ext/git-attribution/src/world_state.rs`);
- the launcher crate `puffin-rs/`, which already holds the logic behind `/usage` (patch `0011`) and would hold the logic behind `/night` ([PUFFIN_NIGHT_SHIFT](./DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md));
- the configuration chain the launcher already reads for `vllm_host` and `puffin_gmail` (a `DREAMFERENCE_*` variable, then the TOML file, then the default);
- the benchmark in `scripts/cave_mode_bench/`, which produced §1.1 and is re-run when the default model changes.

**Needs one hook patch**, `0017-cave-mode` (2,993 bytes, §5.5), which raises the patch-size cap explicitly.

---

## 1. The finding that shapes the design

Measured on this GB10 with Qwen3.8-27B NVFP4 on SGLang (thinking off), before writing anything:

| What | Result |
|---|---|
| Where `puffin`'s output goes (52 rollouts with tool calls in `~/.puffin/sessions`, 2026-09-29/30, counted with Qwen3.8's tokenizer) | **75.5%** tool-call arguments, **23.1%** final answers, **1.3%** commentary between tool calls. With thinking off the model barely narrates: the prose is the final answer |
| Final answers in those rollouts | median 108 tokens, p90 416, max 1,931. The long tail is how-to questions answered as tutorials: numbered options, a setup script for each, a closing offer |
| What one token costs here | decode 25.5 tok/s for prose (50.3 code, 87.0 JSON); prefill ~1,700 tok/s. **One prose token costs the time of ~67 prompt tokens.** 81% of those sessions' input came from the prefix cache, so a few hundred tokens of rules cost a fraction of a second once, not (as with API billing) money on every call |
| What reading costs | ~250 words a minute is ~5.5 tokens a second: the user reads about five times slower than the model writes. A 400-token answer is some 70 s of reading |
| What the prompt asks for today | `base_instructions()` takes the longest bundled Codex template (gpt-5.5's), which carries Codex's "friendly" voice: "a vivid inner life … playful … wry humor", "user updates frequently, every 30s", "engineering prose with some life in it". 959 of the prompt's 4,989 tokens are voice |
| Qwen3.8's tokenizer on caveman's word rules | `configuration`/`cfg`, `implementation`/`impl`, `request`/`req`, `function`/`fn`, `authentication`/`auth`, `database`/`DB`: **one token each**. `X → Y` and `X causes Y`: 3 tokens each. `when it is not` 4, `when not` 2. Abbreviations and arrows save nothing; only leaving words out does |
| The benchmark (§1.1), 532 single-turn runs and 11 sessions of 8–9 turns | On questions, the default level (`ultra`) cut final answers to about half of `off`'s (0.42–0.57 in the three batches with the text appended as designed, 0.59 for the rejected `B` design) and wall time to 0.41–0.74 of it; on coding tasks nothing moved beyond the noise. 35 of 36 checks passed at `ultra` in the confirmation batch, as at `off`. In a session, the level holds only with a one-line reminder each turn |

Outside evidence, in the same direction:

- **JetBrains** (86 SkillsBench tasks, Claude Code, paired A/B, the caveman skill alone): **8.5% fewer output tokens, quality flat** (sign test p = 0.82). Agent output is mostly tool calls, edits and code, which a talking style does not touch.
- **CAVEWOMAN** (Adobe Research, 2026): output compression at L1 (telegraphic, function words dropped) cuts realised cost 1.4–2.4× per model with small accuracy changes; L2–L4 (keywords only, noun skeletons, 15-token budgets) collapse accuracy. **Input compression backfires**: models answer longer and less accurately when *they read* compressed text. Robustness does not follow model size; Qwen3.5-9B was the least robust of eight models.
- **Concise CoT** (Renze & Guven, 2024): asking for concision cut response length 48.7% with negligible loss, except math on GPT-3.5 (−27.7%).
- **Let Me Speak Freely?** (Tam et al., 2024): format restrictions degrade reasoning, the stricter the worse. With thinking off, Qwen3.8's visible answer is its only reasoning, which argues for constraining *what* it says over *how*.
- **Chain of Draft** (Xu et al., 2025) compresses the *reasoning* channel to a few words a step (7.6% of CoT's tokens, accuracy kept). It does not apply: `puffin` runs Qwen3.8 with thinking off (`enable_thinking: false`, effort `none`).

### 1.1 The benchmark

`scripts/cave_mode_bench/` (README there). Nine tasks in throwaway git workspaces, each with an automatic check. Four are coding tasks checked by tests: fix a failing test, implement a function from its docstring, rename a function across four files, add a CLI flag and its test. Five are questions checked for the facts the answer must contain: a question about the code in the workspace; a how-to (an immutable, hashable dataclass); a traceback to explain and fix; whether to run `git reset --hard HEAD~1` with uncommitted work to keep (the check also requires that nothing was run); an open how-to (a web-search tool in an MCP server). Each run is `puffin exec` with a fresh `CODEX_HOME`, one run at a time, levels interleaved; the level text goes in as `developer_instructions`, which is what the World State fragment will be.

**Compare within a batch only.** The median `off` run took 15.5, 21.2, 22.7, 17.4 and 19.8 s in the five batches: the server's load moved over the night. Ratios are the geometric mean over tasks of (median at the level / median at `off`), with a 95% bootstrap interval. Even `off` against itself spans roughly [0.7, 1.4] on the questions, so only large effects are visible.

| Batch | Level text | Runs | Pass | Questions: answer tokens | Questions: wall time | Coding: answer tokens | Coding: wall time |
|---|---|---|---|---|---|---|---|
| 1 | full v1, appended to the prompt | 27 | 27 | 0.70 [0.43, 1.08] | 0.70 [0.48, 1.28] | 0.58 [0.11, 1.44] | 0.85 [0.64, 1.64] |
| 1 | full v1, Codex's voice cut from the prompt (`B`) | 27 | 27 | 0.66 [0.39, 1.04] | 0.66 [0.48, 1.18] | 0.96 [0.28, 1.77] | 0.75 [0.53, 1.29] |
| 1 | ultra v1 (`B`) | 27 | 27 | 0.59 [0.35, 0.92] | 0.52 [0.39, 0.85] | 0.88 [0.29, 1.67] | 0.92 [0.61, 1.35] |
| 1 | lite v1 (`B`) | 27 | 27 | 0.89 [0.49, 1.35] | 0.80 [0.53, 1.64] | 1.08 [0.76, 2.02] | 0.77 [0.63, 1.37] |
| 1 | off, Codex's voice moved to the fragment (`B`) | 27 | 27 | 0.85 [0.47, 1.49] | 0.90 [0.51, 2.42] | 1.06 [0.46, 1.87] | 0.95 [0.67, 2.22] |
| 2 | full v2 (grammar rules made permissive, no example) | 36 | 36 | 0.76 [0.46, 1.17] | 0.63 [0.37, 1.00] | 0.85 [0.64, 1.74] | 0.94 [0.58, 1.39] |
| 2 | ultra v2 (three-sentence cap) | 36 | 35 | 0.42 [0.28, 0.76] | 0.41 [0.23, 0.81] | 0.77 [0.45, 1.44] | 0.76 [0.52, 1.47] |
| 3 | lite v3 | 36 | 36 | 0.82 [0.51, 1.19] | 0.71 [0.49, 1.13] | 1.02 [0.50, 1.65] | 0.88 [0.59, 1.25] |
| 3 | full v3 | 36 | 36 | 0.74 [0.50, 1.11] | 0.87 [0.56, 1.55] | 1.08 [0.59, 1.52] | 0.91 [0.61, 1.19] |
| 3 | ultra v3 | 36 | 35 | 0.57 [0.32, 0.91] | 0.74 [0.47, 1.33] | 0.87 [0.36, 1.41] | 0.85 [0.60, 1.13] |
| 4 | full v4 (questions only) | 20 | 20 | 0.76 [0.52, 1.24] | 0.77 [0.43, 1.21] | — | — |
| 4 | ultra v4 (the `debug` task only) | 6 | 6 | — | — | — | — |
| 5 | **ultra v4, the shipping text** | 36 | 35 | 0.54 [0.33, 0.92] | 0.72 [0.41, 1.14] | 0.87 [0.50, 2.16] | 0.83 [0.59, 1.14] |

`off` passed 154 of its 155 checks (once it transliterated "é" where the docstring said to replace it). Every miss at a cave level was at `ultra`, one each in batches 2, 3 and 5. v2 and v3 missed on the traceback task: one answered how without why, the other stated the rule ("never mutate a dict while iterating it") without the code that fixes it. v4 added "a fix shows the code or command" and passed that task 6 times of 6 in batch 4 and 4 of 4 in batch 5, but missed once on the open how-to: it wrote the example into a file nobody had asked for and answered in three sentences about the file. The cap displaced the content instead of shortening it (§9). One `B` full answer was re-scored as a pass after the check was widened to accept "iterated" as naming the cause.

**Sessions.** Eight questions in one workspace through `puffin exec` and `exec resume --last`: read code, how-to, make the change, a why, the difference between two library calls, write and run a test, a review, an explanation. Final-answer tokens per turn (the same questions in every row):

| Level | Rep | Turns 1–8 | Turns 1–4 | Turns 5–8 | Closing offers |
|---|---|---|---|---|---|
| off | 1 | 257 388 228 460 330 380 337 680 | 1,333 | 1,727 | 5 |
| off | 2 | 182 386 110 422 209 173 500 837 | 1,100 | 1,719 | 2 |
| off | 4 | 149 70 81 435 246 244 310 717 | 735 | 1,517 | 5 |
| full v3 | 1 | 311 291 212 253 121 242 244 344 | 1,067 | 951 | 3 |
| full v3 | 2 | 108 196 215 104 153 134 124 384 | 623 | 795 | 1 |
| full v3 + reminder | 2 | 104 225 139 323 193 46 231 412 | 791 | 882 | 2 |
| full v4 + reminder | 3 | 172 249 153 285 232 356 433 310 | 859 | 1,331 | 2 |
| ultra v3 | 1 | 139 119 137 382 171 247 268 678 | 777 | 1,364 | 3 |
| ultra v3 | 2 | 74 99 218 256 106 131 134 315 | 647 | 686 | 2 |
| ultra v3 + reminder | 2 | 53 62 104 67 34 86 73 141 | 286 | 334 | 0 |
| ultra v4 + reminder | 4 | 157 163 75 145 141 160 176 263 | 540 | 740 | 1 |

Without a reminder, ultra's three-sentence cap was gone by turn 4 of the first session (382 tokens, a bulleted list and "Want me to apply that?") and by turn 8 its answer was as long as `off`'s. With a one-line reminder before each user message, ultra v3 held at about a fifth of `off` for all eight turns of rep 2, with no closing offer. The reminder did nothing measurable for full, whose only number is a soft one ("about five sentences"). In rep 4 the shipping text held at about half of `off` over turns 5–8 (740 against 1,517 tokens). Rep 4 also adds a ninth turn, "Give me the full explanation of your last answer.", to test the way out every level promises. At `off` it produced 820 tokens. At `ultra` it produced 189: a five-step numbered walkthrough, so the three-sentence form was dropped as asked, but with no more depth than the 263-token answer it was expanding. The way out works in form, not in substance, against a reminder that repeats the cap every turn (§9).

### 1.2 What follows

1. **Cave mode shortens answers to questions; it does not touch coding work.** On the four coding tasks no level moved answer length or wall time beyond the noise: the time there is tool calls and exploration, and the closing summaries are already short. On questions, every level shortened the answer, roughly in order: lite 10–20%, full 25–35%, ultra 40–60%. This is JetBrains' finding, reproduced locally, and the reason this spec does not promise caveman's 65%.
2. **Content rules work; grammar rules do not.** The article rate barely moved (5.9–9.5% at the cave levels, 7.9–10.4% at `off`) and sentences did not get shorter (15–21 words at every level, 16.5 at `off`). What shortened answers was answer-first, one option instead of a menu, no unrequested code, and a hard sentence cap. The levels are written that way (§2), and the tokenizer check says the grammar tricks would save little anyway.
3. **Numbers hold, adjectives do not.** "At most three sentences" is followed; "short", "tight", "about five sentences" barely are. Permissive wording ("articles … may go", v2) did less than directives with an example (v1, v3).
4. **A level fades over a session unless it is repeated.** One fragment at the start of the conversation is not enough for Qwen3.8 past a few turns. A ~45-token reminder per user turn is, for a level with a hard cap; for `full`, whose budget is soft, it made no measurable difference (§5.2).
5. **Where Codex's voice sits makes no difference worth its cost.** Cutting the voice out of the system prompt (`B`) measured the same as appending the level to it, and moving the voice into a fragment changed behaviour even at `off`. The system prompt stays untouched (§5.1).
6. **The default is `ultra`**, by the rule written before the confirmation batch: ultra if it passes at least 35 of 36 checks and its reminded session's turns 5–8 sum to under half of `off`'s, otherwise full. Both held, the second narrowly: 35 of 36, and 740 against 1,517 tokens (0.49). Phase 2 (§7) is where real sessions confirm or reverse it.

---

## 2. Levels

| Level | What the user gets | Measured on questions (§1.1) |
|---|---|---|
| `off` | Codex's own voice, exactly as without this feature: no fragment is ever sent | — |
| `lite` | Full sentences. Answer first, every question answered, one option, no filler, recaps or closing offers, no code that was not asked for | answers −10–20% |
| `full` | lite, plus: articles and filler dropped, fragments allowed, about five sentences of prose outside code | answers −25–35%; fades over a session |
| `ultra` (default) | full, plus: **at most three sentences of prose outside code and one code block**, unless the user asks for more; after a change, only what changed and how it was checked | answers −41–58%, wall −26–59%; holds over a session with the reminder |

`lite` and `full` are kept as measured, documented fallbacks for users who want more room, not as recommendations. Every level other than `off` carries the exemptions of §4. The texts are in Appendix A, verbatim; each is about 270–430 tokens, and each has a one-line reminder of about 45 tokens.

What the same question gets (the traceback task, batch 3, rep 9; real outputs, quoted or described):

- **off** (239 tokens): a bold "Why it fails" paragraph, a bold "The fix", two labelled code blocks ("Option 1", "Option 2"), and a closing paragraph on when to prefer which.
- **full v3** (136): "You're deleting keys while iterating the dict itself, which invalidates the iterator and raises `RuntimeError`." Then the fix, two code blocks, one line of verification.
- **ultra v3** (123): "You're deleting keys from a dict while looping over it — the size changes mid-iteration. Fix: iterate over a snapshot, or build a new dict." One code block with both.

And the irreversible-action question (batch 3, rep 8), where every level must still warn in full sentences: `off` answered in 180 tokens (339 in the next repetition); ultra in 43: "No. `git reset --hard` discards your uncommitted changes in app.py. Use `git reset --soft HEAD~1` instead, which undoes the commit while keeping everything staged for re-commit." (The model's wording: the uncommitted edits stay unstaged.)

---

## 3. The command

`/cavemode` is a built-in slash command of the `puffin` TUI. It edits one small file (§5.3) and prints a few lines; it never calls the model, so it answers instantly, including while a turn is running.

| Form | Effect |
|---|---|
| `/cavemode` | Shows the level in force and where it came from (this session, `DREAMFERENCE_PUFFIN_CAVE_MODE`, the TOML file, or the built-in default), then the four levels with one line each |
| `/cavemode <level>` | Sets `off`, `lite`, `full` or `ultra` for this session. Takes effect at the next model request, mid-turn included |
| `/cavemode default <level>` | Also writes `puffin_cave_mode = "<level>"` to the configuration file (§5.3), so new sessions start at it. If `DREAMFERENCE_PUFFIN_CAVE_MODE` is set, says that the variable still wins |
| anything else | Prints the usage line. Nothing changes |

What `/cavemode` prints:

```
Cave mode: ultra (default)
  off    Codex's own voice
  lite   full sentences; answer first, one option, no filler
  full   terse; about five sentences outside code
  ultra  at most three sentences outside code       ← in force
More room: /cavemode full (this session) or /cavemode default full (new sessions).
```

**Rules:**
- **The level belongs to the session.** `puffin resume` keeps it; a new session, a fork, a side conversation and a subagent start at the default. (A subagent's text goes to the parent model, not to the user, and §4 already keeps text another model reads in normal prose.)
- **The model cannot change it.** "Explain in detail" gets a full answer to that question (every level says so) but leaves the level alone. Only `/cavemode` and configuration change it.
- **`puffin exec` and scripts** take the default, or `DREAMFERENCE_PUFFIN_CAVE_MODE=off puffin exec …` for one run.
- **Only the terminal agent.** The web chat (Onyx) has its own assistant prompt (`PUFFIN_ASSISTANT_INSTRUCTIONS`) and is not covered here.

---

## 4. What never changes

Every level except `off` carries these, in the level text itself (Appendix A):

- **Code, commands, paths, identifiers, numbers, units and quoted errors are exact.** Negations (`not`, `never`, `no`, `only`, `except`) are never dropped.
- **The tool calls are untouched.** Cave mode is about what the user reads. Edits, commands and code the model runs are outside it, and so is how it explores.
- **Anything that outlives the chat is normal prose:** code comments, commit messages, pull request and issue text, files the model writes (`AGENTS.md` from `/init` included).
- **Anything another model will read is normal prose:** plans, subagent task prompts, and handoff and compaction summaries. CAVEWOMAN measured what happens when a model *reads* compressed text: longer answers and lower accuracy. The terse register must not leak into the model's own input.
- **Full sentences where brevity is dangerous:** security warnings, irreversible actions (deleting, resetting, force-pushing), and ordered steps whose order fragments could blur. The level resumes afterwards.
- **Every question gets an answer, and a fix shows the fix.** A why-question gets its cause in one sentence before the fix, and a fix shows the code or command, not only the rule. Both rules exist because of the two misses in §1.1.
- **More on request.** If the user asks for detail or repeats a question, the answer is in full.

---

## 5. How it is built

### 5.1 The system prompt stays as it is

The level is a developer message added to the conversation; `base_instructions()` is not touched. Cutting Codex's voice out of the system prompt so that the level would own the register alone (`B` in §1.1) bought nothing measurable, changed behaviour even at `off`, and would tie the launcher to the wording of Codex's prompt, which moves with every Codex release. With the voice left in place, `off` is Codex's own behaviour by construction.

### 5.2 A World State section, from the launcher crate

Codex keeps some model-visible state as **World State sections**: each has a value, and a developer message is added to the history only when the value changes (`record_step_world_state_if_changed`, before every model request). Extensions contribute sections through `ContextContributor::contribute_world_state`; `git-attribution` is a complete example in under two hundred lines. Cave mode is one more. Its value is the level and, for every level but `off`, the current turn's id, so that it changes once per user turn and carries the reminder:

```rust
// puffin-rs/src/cave.rs (sketch)
pub fn install<C: Sync>(registry: &mut ExtensionRegistryBuilder<C>) {
    registry.prompt_contributor(Arc::new(CaveMode));
}

impl ContextContributor for CaveMode {
    fn contribute_world_state<'a>(&'a self, input: WorldStateContributionInput<'a>)
        -> ExtensionFuture<'a, Vec<WorldStateSectionContribution>> {
        Box::pin(async move {
            let level = level_for(&input.thread_id.to_string());
            vec![section(level, input.turn_id)]
        })
    }
}

fn section(level: Level, turn: &str) -> WorldStateSectionContribution {
    let value = match level {
        Level::Off => json!({"level": "off"}),
        _ => json!({"level": level.name(), "turn": turn}),
    };
    WorldStateSectionContribution::new("cave_mode", value.clone(), move |previous| {
        if matches!(previous, Known(v) if *v == value) {
            return None;                                   // same level, same turn: a later step
        }
        let before = match previous {
            Known(v) => v.get("level").and_then(Value::as_str),
            _ => None,
        };
        match (level, before) {
            (Level::Off, None | Some("off")) => None,      // never on, or still off: say nothing
            (Level::Off, Some(_)) => Some(fragment(OFF_TEXT)),
            (_, Some(b)) if b == level.name() => Some(fragment(level.reminder())), // new turn
            _ => Some(fragment(level.text())),             // first use, a switch, or after compaction
        }
    })
    .with_retained_fragment_matcher(move |role, text| role == "developer" && level.is_its_full_text(text))
}
```

A step inside the same turn has the same value, so nothing is added between tool calls; the first request of each user turn gets the reminder; a switch or a first use gets the full text.

Why this mechanism:
- **The prefix cache survives.** Fragments are appended; nothing earlier is rewritten. A switch costs the prefill of ~400 tokens (~0.25 s) and a turn ~45 tokens, where changing the system prompt would re-read the whole conversation: at 116K tokens, about two minutes at ~1,000 tok/s. Over a 100-turn session the reminders add ~4,500 tokens of history, which compaction handles like any other.
- **It takes effect mid-turn.** World State is recomputed before every model request, so `/cavemode full` typed while the agent works applies to its next message.
- **It is designed to survive compaction.** The retained-fragment matcher tells Codex the *full text* must still be in history; where the history is diffed against retained items (`render_history_diff`), a section whose fragment is missing is treated as absent and its full text is sent again. The matcher recognises only the current level's full text: neither a reminder nor an older level's text counts, because a reminder pointing at rules the model can no longer see would hold nothing. Whether the step after a compaction takes that path, rather than diffing against a persisted snapshot that still says "known", was read from the code, not observed; Phase 1 checks it (§7).
- **`off` costs nothing when never used.** A session that starts at `off` gets no fragment at all, so it is exactly upstream Codex. A session switched to `off` gets two lines saying the earlier rules no longer apply (the `DISABLED_INSTRUCTIONS` pattern of `git-attribution`).
- **The model sees it where it sees other developer messages.** Qwen3.8's patched chat template turns a system message after the first into a `<system-reminder>` inside the user turn (registry `chat_template_patches`); the benchmark's reminder was placed the same way.

**Registration** is one line after `codex_git_attribution::install(...)` in `codex-rs/app-server/src/extensions.rs`, the registry the TUI, `puffin exec` (an in-process app server) and app-server clients all use; and one line in the registry `cli/src/main.rs` builds for `puffin debug prompt-input`, so that command shows exactly what the model is sent (the tests use it, §8).

### 5.3 Where the level comes from

Resolved before every model request, first match wins:

1. **This session:** `$CODEX_HOME/cave_mode/<thread-id>`, a file holding the level name, written by `/cavemode <level>`. A file keyed by thread id survives `puffin resume` and needs no channel between the TUI and the extension, which may run in another process (`/daemon`).
2. **`DREAMFERENCE_PUFFIN_CAVE_MODE`.**
3. **`puffin_cave_mode`** in the TOML file the launcher already reads (`DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then `~/.config/dreamference/config.toml`; `config_file()` in `puffin-rs/src/lib.rs`). `/cavemode default` writes here with `toml_edit`, creating the user-level file if there is none.
4. **`DEFAULT_PUFFIN_CAVE_MODE`**, `"ultra"` (§1.2, item 6).

An invalid value at any tier is skipped, and `/cavemode` names it and where it was. The Python side mirrors tiers 2–4 exactly as it does `puffin_gmail`: `DreamferenceConfig.puffin_cave_mode`, validated against the four names, written by `save_config()` only when it differs from the default. Session files older than 30 days are deleted by the launcher at start.

### 5.4 The slash command

One more hook patch in `codex-patches/`, `0017-cave-mode`, modelled on `0011-usage-token-stats`:

- `codex-rs/tui/src/slash_command.rs`: the variant `Cavemode` (strum's kebab-case gives `cavemode`), placed after `Model`, since both set how the model answers; its description, "set how terse Puffin's answers are"; membership in `supports_inline_args()` and `available_during_task()` (true).
- `codex-rs/tui/src/chatwidget/slash_dispatch.rs`: one arm in `dispatch_command` (no arguments) and one in `dispatch_command_with_args`, both calling `puffin_launcher::cave::command(self.thread_id, args)` (generic over the id's `Display`, so each arm is one line), which returns the lines to print, added with `add_plain_history_lines` as `/usage` does; and `QueueDrain::Continue` in `queued_command_drain_result`, which is exhaustive.
- `codex-rs/app-server/Cargo.toml` and `src/extensions.rs`: the `puffin-launcher` path dependency and the `install` line (§5.2). `puffin-launcher` gains `codex-extension-api` from the workspace.
- `codex-rs/cli/src/main.rs`: the `install` line in the `debug prompt-input` registry. The CLI already depends on the launcher (patch `0002`).

Everything else (parsing, the tiers, the files, the texts, the printed lines) lives in `puffin-rs/src/cave.rs`, with its unit tests.

### 5.5 Budget

The series was capped at 22,000 bytes (`test_the_patches_stay_small`) and stood at 21,807. These hooks are ten hunks in five files: 2,993 bytes with diff headers, not the 2.3 KB first estimated, so the series is now 24,800 bytes over 15 patches and the cap goes to 25,000 in the same commit, explicitly and only by what it needs, as the product-name hooks did on 2026-09-30. `/night` needs its own raise on top.

---

## 6. Alternatives considered

| Option | Why not |
|---|---|
| Cut Codex's voice out of the system prompt; the level owns the register alone | Measured (`B`, §1.1): no gain over appending, `off` no longer behaved like upstream, and cutting Codex's prompt at fixed headings breaks with each Codex release |
| Rewrite the system prompt for the level at launch | No switching mid-session, and changing the system prompt discards the cached prefix of the whole conversation |
| Codex hooks (`UserPromptSubmit` with added context), as the caveman plugin does for Claude Code | Not a `/cavemode` command in the TUI; forks a process for every prompt; needs hook trust. The per-turn reminder it would give is what §5.2 does without a process |
| `contribute_turn_context` for the reminder | Codex records turn-context contributions only when the turn context itself changes (model, directory, permissions), not every turn |
| A skill (`$cavemode`) | A skill is instructions the model chooses to load. Nothing would hold the level, show it, or apply it to the next session |
| The collaboration mode's developer instructions (where Plan mode's text goes) | Switching to Plan mode replaces them: two unrelated settings in one slot |
| `model_verbosity` (the Responses API's `text.verbosity`) | A GPT-5 parameter. The launcher's catalog sets `support_verbosity: false`, and SGLang does not implement it |
| A cap on output tokens | Cuts code and patches mid-way; the token budgets CAVEWOMAN tested (L4, 15 tokens) collapsed accuracy |
| caveman's compressing proxy (shrinking what the model reads) | The input side, a separate question; CAVEWOMAN found input compression raises cost and lowers accuracy |
| The wenyan (classical Chinese) levels | A novelty; nothing gained for readers of English |
| Levels written as grammar rules (drop articles, fragments) | Measured: Qwen3.8 largely ignores them (§1.2, item 2), and the tokenizer check shows abbreviations and arrows save nothing anyway |

---

## 7. Phases

**Phase 0, measure (done for Qwen3.8, §1.1).** The default was chosen by the rule in §1.2, written before the confirmation batch ran. Re-run `scripts/cave_mode_bench` whenever the default model changes or a level text is edited, with `off` interleaved in the same batch, and apply the same rule.

**Phase 1, build.** `puffin-rs/src/cave.rs` (levels, texts, reminders, resolution, `command()`, the World State section), the hook patch with the cap raise, `puffin_cave_mode` in `DreamferenceConfig`, the tests of §8, and a paragraph in `specs/DREAMFERENCE_PUFFIN_CODEX.md` and `docs/puffin.md`. Three checks belong to this phase because the benchmark could not make them: that the reminder reaches the model as its own `<system-reminder>` before the model's first message of each turn (the benchmark placed it inside the user's message); that after `/compact` and one more turn the rollout holds the level's full text again, not only a reminder (§5.2); and what a compaction summary looks like at `ultra` (it must be normal prose, §4).

**Phase 2, look at real sessions.** After two weeks of use, the same split as §1's first row over `~/.puffin/sessions`: final-answer tokens (median, p90) before and after, how often `/cavemode` is used and to which level. Frequent switches to `full` or `off` mean the default is wrong for this user, whatever the benchmark said.

**Phase 3, optional.** A status-line item showing the level, if `/cavemode` alone proves too hidden.

---

## 8. Tests

- **Launcher unit tests** (`puffin-rs/src/cave.rs`, run with the other launcher tests in the export directory): level parsing, case-insensitive, unknown names rejected with the usage line; the resolution order of §5.3, each tier shadowing the next; `command()`'s output for every form; the section's render table: absent + non-`off` → full text; absent + `off` → nothing; same level, same turn → nothing; same level, new turn → reminder; any switch → the new full text; switch to `off` → the off text; the retained matcher accepting the current level's full text only (not its reminder, not another level's text); each text under 500 estimated tokens and each reminder under 60 (characters / 4, which overestimates: full's 1,836 bytes are 459 by the estimate and 432 tokens by the tokenizer).
- **Configuration** (`tests/`): `DreamferenceConfig.puffin_cave_mode` through all four tiers; an invalid value rejected; `save_config()` omitting the default; `DEFAULT_PUFFIN_CAVE_MODE` equal to the default in `puffin-rs/src/cave.rs` (read from the source, as other cross-language constants are).
- **Patch size:** `test_the_patches_stay_small` with the raised cap and a comment saying why.
- **Prompt check** (needs the model server only for `/v1/models`; skipped without it): `puffin debug prompt-input "hi"` contains exactly one `<cave_mode>` developer fragment, the default level's full text; with `DREAMFERENCE_PUFFIN_CAVE_MODE=off`, none.
- **Live, two turns** (`puffin exec`, then `exec resume --last`): the rollout holds the full text before turn 1 and the reminder before the model's first message of turn 2, and nothing between tool calls of one turn. Then `/compact` and one more turn: the full text is in the history again.
- **Live slash-command suite** (`tests/test_puffin_slash_commands.py` enumerates slash commands from the *submodule's* unpatched source, so a command a patch adds is not listed there and cannot get a case without failing its stale-case check; the checks below were run with the same pty harness from a script instead, §10): `/cavemode` lists the levels; `/cavemode full`, then a question, and the rollout holds full's text after ultra's; `/cavemode off` adds the off text and no reminder follows; `/cavemode loud` prints the usage line and changes nothing.
- **The benchmark** (`scripts/cave_mode_bench`) is not part of the suite; it drives the real model for about an hour. Phase 0 says when to run it.

---

## 9. Risks and open questions

- **An answer too terse to use.** Two of 72 single-turn answers at ultra v2/v3 left out the why or the fix itself; the rules added for them (§4) held 10 of 10 times on that task since, and 35 of 36 checks passed in the confirmation batch. The benchmark's traceback task watches it. One early run (full v1) also ended with a stray "Done." after its real summary; every level now requires "what changed and how you checked it" after a change.
- **Hidden caveats.** A three-sentence answer can drop a caveat the user needed. The exemptions (§4) cover the dangerous cases; the rest is one `/cavemode full` away, and `/cavemode` always shows the level in force.
- **The way out does not fully work.** Asked for "the full explanation" at turn 9, `ultra` dropped the three-sentence form but added no depth (189 tokens against 820 at `off`, §1.1): the reminder repeats the cap every turn and wins. Proposed for Phase 1, untested: the reminder gains "unless this message asks for more", and a request for detail ("explain", "in full", "more detail") skips that turn's reminder. Both change a level's text, so Phase 0 is re-run before they ship.
- **Content displaced, not cut.** Once (§1.1) `ultra` met its cap by writing the answer into a file the user had not asked for and describing the file. A rule against it ("never move an answer into a file to stay under the cap") is the same kind of Phase 1 change, with the same re-run.
- **The model changes.** The texts were tuned on Qwen3.8 with thinking off; CAVEWOMAN found robustness to terse output differs between models in ways size does not predict. With thinking on, the visible answer is no longer the only reasoning and terseness is safer still; either way Phase 0 is re-run.
- **Tuning to the benchmark.** The texts went through four versions on nine tasks. The rules added along the way are general (answer every question, show the fix, a sentence cap), but Phase 2's real-session check is the guard against having fitted the nine.
- **Compaction.** Two open points, both Phase 1 checks: whether the full text really comes back after `/compact` (§5.2), and whether the summary, exempt by rule but written with the level in context, is normal prose.
- **Open: should a fork or side conversation inherit the parent's level?** The extension sees only the new thread's id; inheriting needs the parent's, which the fork request carries. Deferred until someone asks.

---

## 10. Implementation notes (2026-10-01)

**What was built** is §5 as written, with these details settled in code:

- **`off` carries no retained-fragment matcher.** On the history path (`render_history_diff`) the *current* section's matcher decides whether a persisted snapshot still counts; a matcher on `off` would find no `off` text, report the section absent, and swallow the message that cancels the previous level.
- **`Unknown` is treated like `Absent`**: a level's full text is sent, `off` sends nothing. Nothing registers a legacy matcher, so the harness never reports `Unknown` for this section today.
- **Session files** are named by thread id and refused unless the id is alphanumeric with dashes, so nothing typed can escape `$CODEX_HOME/cave_mode/`. Files older than 30 days are deleted at launch.
- **`/cavemode default <level>`** writes `puffin_cave_mode` with `toml_edit` to the file the launcher already reads (`DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml`, then `~/.config/dreamference/config.toml`, created if absent) and also sets the current session.
- **Before the first message** there may be no thread yet; `/cavemode <level>` then says so and points at `/cavemode default`.
- **The texts are files**, `puffin-rs/cave/*.txt`, byte-for-byte `scripts/cave_mode_bench/levels/` (a Python test compares them), included with `include_str!`; the markers are stripped at run time because the harness adds them back.
- **The older `cave_mode` setting** (`puffin-admin run --cave`, which writes a fixed prompt into Cline's `.clinerules`) is unrelated and unchanged.

**Checked against the live model** (Qwen3.8-27B on SGLang), with the binary `puffin-admin codex build` installed from the committed patches:

| Check | Result |
|---|---|
| `puffin debug prompt-input "hi"` | One `<cave_mode>` item: ultra's full text, byte-identical to the measured file, as its own content item of the initial developer message. With `DREAMFERENCE_PUFFIN_CAVE_MODE=off`: none. With `lite`: lite's text |
| `puffin exec`, then `exec resume --last` | Turn 1: the full text in the initial context, then two tool calls with nothing between them. Turn 2: one developer message, ultra's reminder, recorded before the user's message, so the model reads it before writing anything in that turn |
| TUI, `/cavemode` | The listing, `ultra (default)`, the marker on the level in force; `/cavemode loud` prints the usage line and changes nothing |
| TUI, `/cavemode full`, two turns | full's full text before the first turn after the switch, full's reminder before the second |
| TUI, `/cavemode off`, one turn | the off text once, and no reminder in the following turn |
| TUI, `/cavemode ultra`, a turn, `/compact`, a turn | ultra's full text after the switch; after the compaction the history held no cave text, and the next turn's re-injected initial context carried ultra's **full text**, not a reminder (§5.2's expectation, now observed) |
| TUI, typing `/cav` | the popup offers `/cavemode`, "set how terse Puffin's answers are" |
| TUI, `/cavemode default full` | `puffin_cave_mode = "full"` appended to the configured TOML file with its other keys kept, the session file set to `full`, both reported |
| TUI, `puffin resume --last` after that | `/cavemode` reports `full (this session)`: the level belongs to the session and survives a resume |
| The compaction summary at `ultra` | normal prose with headings ("**Task:** User sends single-word prompts; assistant replies with exactly that one word."), as §4 requires |

Not checked: how Qwen3.8's chat template renders the per-turn developer message (§5.2 expects a `<system-reminder>` inside the user turn); the rollout shows it as its own developer message in the right place, but the rendered prompt at the server was not inspected.

**Codex's own tests** (`puffin-admin codex test`) were not re-run. A new slash command shifts the TUI's popup snapshots that list commands (for example `command_popup_default_items`), so those need Puffin snapshots accepted with `--accept-snapshots` and reviewed, which refuses anything but a name change: expect a handful of manual acceptances.

## Sources

- caveman (the skill, its levels, its per-prompt reinforcement hook): https://github.com/JuliusBrussee/caveman ; rules and switching: https://docs.caveman.so/docs/skill
- JetBrains, 86 tasks, skill only: https://blog.jetbrains.com/ai/2026/07/speak-to-ai-agents-like-cavemen-tosave-tokens/ ; coverage: https://www.infoworld.com/article/4193775/talk-like-a-caveman-prompts-save-tokens-but-far-less-than-promised.html
- CAVEWOMAN, output and input compression across eight models: https://arxiv.org/abs/2606.24083
- Concise Chain-of-Thought: https://arxiv.org/abs/2401.05618
- Chain of Draft: https://arxiv.org/abs/2502.18600
- Let Me Speak Freely? (format restrictions and reasoning): https://arxiv.org/abs/2408.02442
- Codex, World State and extensions: `codex-rs/core/src/context/world_state/mod.rs` (`render_history_diff`, retained fragments), `codex-rs/ext/extension-api/src/contributors/world_state.rs`, `codex-rs/ext/git-attribution/src/world_state.rs`, `codex-rs/core/src/session/mod.rs` (`record_step_world_state_if_changed`, `record_context_updates_and_set_reference_context_item`)

---

## Appendix A. The level texts

Verbatim, as `scripts/cave_mode_bench/levels/` holds them and as `puffin-rs/src/cave.rs` will. The opening and closing `<cave_mode>` markers are the section's markers.

**lite** (271 tokens):

```
<cave_mode>
Cave mode is lite. Until a later cave_mode message changes it, write every message to the user this way.
- Answer every question the user asked, first, in plain full sentences. A why-question gets the cause in one sentence, then the fix.
- Then only what the user needs to act on. After a change, say what changed and how you checked it.
- No filler, hedging, pleasantries, restating the question, recaps or closing offers.
- One answer: the option you recommend. Mention an alternative in one sentence only when the choice is the user's to make.
- No code the user did not ask for and does not need: no setup scripts, sample output or alternative versions.
- Between tool calls, write nothing unless you need a decision, must warn, or the plan changed.
- Keep code, commands, paths, identifiers, numbers, units and quoted errors exact.
- Write normal prose in anything that outlives the chat or that another model will read: code comments, commit messages, pull request and issue text, files you write, plans, and handoff or compaction summaries.
- If the user asks for more detail or repeats a question, answer in full.
</cave_mode>
```

**full** (432 tokens):

```
<cave_mode>
Cave mode is full. Until a later cave_mode message changes it, write every message to the user this way.
- Answer every question the user asked, first. A why-question gets the cause in one sentence, then the fix.
- Keep prose outside code to about five sentences; go longer only when the user asks or the steps need it. After a change, say what changed and how you checked it.
- A fix shows the code or command, not only the rule.
- Drop articles, filler (just, really, basically, actually, simply), pleasantries, hedging, restating the question, recaps and closing offers. Fragments are fine. Pattern: [thing] [action] [reason]. [next step].
- One answer: the option you recommend. Mention an alternative in one line only when the choice is the user's to make.
- No code the user did not ask for and does not need: no setup scripts, sample output or alternative versions.
- Between tool calls, write nothing unless you need a decision, must warn, or the plan changed.
- Keep code, commands, paths, identifiers, numbers, units and quoted errors exact. Never drop not, never, no, only or except. Do not abbreviate words or write arrows: they save nothing.
- Write normal prose in anything that outlives the chat or that another model will read: code comments, commit messages, pull request and issue text, files you write, plans, and handoff or compaction summaries.
- Write full sentences for security warnings, irreversible actions (deleting, resetting, force-pushing) and ordered steps where fragments could be misread.
- If the user asks for more detail or repeats a question, answer in full.
Example. Not: "Sure! The issue you're seeing is most likely caused by the expiry check in the auth middleware, which uses a strict comparison." Yes: "Bug in auth middleware: expiry check uses `<`, needs `<=`. Fix:"
</cave_mode>
```

**ultra**, the default (402 tokens):

```
<cave_mode>
Cave mode is ultra. Until a later cave_mode message changes it, write every message to the user this way.
- Answer every question the user asked, first. A why-question gets the cause in one sentence, then the fix.
- At most three sentences of prose outside code, and at most one code block, unless the user asks for more. After a change: what changed and how you checked it, nothing else.
- Drop articles, filler, pleasantries, hedging, restating the question, recaps and closing offers. Fragments are fine. State each fact once.
- A fix shows the code or command, not only the rule.
- One answer: the option you recommend. No alternatives unless the choice is the user's to make.
- No code the user did not ask for and does not need: no setup scripts, sample output or alternative versions.
- Between tool calls, write nothing unless you need a decision or must warn.
- Keep code, commands, paths, identifiers, numbers, units and quoted errors exact. Never drop not, never, no, only or except. Do not abbreviate words or write arrows: they save nothing.
- Write normal prose in anything that outlives the chat or that another model will read: code comments, commit messages, pull request and issue text, files you write, plans, and handoff or compaction summaries.
- Write full sentences for security warnings, irreversible actions (deleting, resetting, force-pushing) and ordered steps where fragments could be misread.
- If the user asks for more detail or repeats a question, answer in full.
Example. Not: "Sure! The issue you're seeing is most likely caused by the expiry check in the auth middleware, which uses a strict comparison." Yes: "Auth middleware expiry check uses `<`; needs `<=`. Fix:"
</cave_mode>
```

**Reminders** (40–46 tokens each), sent before the model's first message of each later turn:

```
<cave_mode>
Cave mode lite still applies: answer every question first, in full sentences; no filler, recaps or closing offers; one option.
</cave_mode>
<cave_mode>
Cave mode full still applies: answer every question first; about five sentences of prose outside code; one option; no recaps or closing offers.
</cave_mode>
<cave_mode>
Cave mode ultra still applies: answer every question first; at most three sentences of prose outside code and one code block; no alternatives, recaps or closing offers.
</cave_mode>
```

**off**, sent only when a session switches from another level to `off`:

```
<cave_mode>
Cave mode is off. The earlier cave_mode rules no longer apply; write as the rest of your instructions describe.
</cave_mode>
```
