# Mightling assistant evals: measuring the phone-chat assistant

**Status:** decided 2026-10-20 (the owner's decisions D1–D6, §0.1): `ask-evals` is to be built. **Phase 0 done the same day (§9); three industry benchmarks added on the owner's request, planned in §10; the harness built and tested without a model (§11), its first model run after the nights:** runs happen in a `bwrap --unshare-net` jail that keeps production's sandbox and is offline by construction; nothing else built yet. Proposed the same day as a moonshot spec from research only: no model job was run, no benchmark was downloaded or started, nothing was written to any tracker. It ranks what to adopt, what to build and what to skip.
**Goal:** measure, every night, the faults that made the phone-chat assistant useless on the morning of 2026-10-20 ([MIGHTLING_SIGNAL §19](./DREAMFERENCE_MIGHTLING_SIGNAL.md), [MIGHTLING_CHAT §16](./DREAMFERENCE_MIGHTLING_CHAT.md)). Each fix then gets a number that shows when it regresses, as SWE-bench gives the coding agent.
**Builds on:**
- `scripts/cave_mode_bench/` (`run.py`, `drift.py`; *removed 2026-10-28, `ef443182`*): real `ling exec` runs with a fresh `CODEX_HOME`, multi-turn through `exec resume --last`, a `check.py` per task, tokens counted with the served model's tokenizer;
- the SWE-bench harness ([MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md)): `ling` in a container on its own Docker network, the model gate (§18), the night scripts (`scripts/swe_bench_night2.sh` and successors);
- the bridges' stand-ins: the scripted signal-cli of `ling-rs/signal/tests/daemon.rs`, the stand-in Bot API and homeserver of `ling chat`'s tests (CHAT §13);
- `ling-admin audit egress` ([MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md)): strace on a real session;
- named prompts ([MIGHTLING_PROMPT](./DREAMFERENCE_MIGHTLING_PROMPT.md)): `DREAMFERENCE_MIGHTLING_PROMPT=ask` gives `ling exec` the Ask prompt the bridges use.
**Reads with:** MIGHTLING_CAVE_MODE §1.1 (spec removed 2026-10-28) (the only assistant-side measurement so far), [MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md), [research/agent-survey-2026-10-09](./research/agent-survey-2026-10-09.md) (the same method, for coding).

**Short answer.** No published benchmark reproduces the morning's failures. Nothing published puts a CAPTCHA in front of an agent's search tool. Nothing sends an image that reads as nothing through a messenger, keeps a thread open for two days, or asks the agent why it was slow and checks the answer against the log. So the first thing to build is **our own small suite, `ask-evals`** (§5). It has about 24 synthetic scenarios, stand-in search and fetch with fault injection, rule-based scores, offline, and about an hour a night. Around it, five outside sets are cheap, offline, mostly judge-free and permissively licensed, and each covers one failure better than we could:

- the **Language Confusion Benchmark**: the reply in the user's language;
- **SimpleQA Verified** and **AbstentionBench**: calibration and abstention;
- **CAVEWOMAN**: correctness under brevity, which is cave mode's question;
- **BrowseComp-Plus**: research questions over a fixed offline corpus, with tool calls counted;
- **Gaia2** through `gaia2-cli`: the one published agent benchmark that drives a CLI agent binary and injects tool noise.

These should run weekly, not nightly. The well-known tool-use leaderboards (BFCL, τ²-bench, ToolSandbox, GAIA, BrowseComp, WebArena) are skipped or deferred. Each needs the live web, an external user simulator or judge, or a browser stack, or it tests a raw model rather than `ling`.

**The night.** The SWE-bench nights already fill the GB10 (night 13's single run took 5 h 38 min of agent time for 50 tasks, and best-of-3 about three times that). While a run holds the model gate, every request from outside the run's network is refused (SWE_BENCH §18.2). So `ask-evals` cannot run *beside* a SWE-bench arm. It runs in its own slot before the arm takes the gate (§6).

---

## 0. Decisions, stated first

Proposed; each was the owner's to take, and was taken on 2026-10-20 (§0.1).

- **D1. Build `ask-evals` first (§5), Phase 0 and 1 of §7.** It is the only measurement that matches failures 1, 3, 4 and 8, and the only one that tests the bridges.
- **D2. A nightly assistant slot of about an hour, before the SWE-bench arm, in the night's own unit (§6, option A).** Its direct mode holds the model gate, as a SWE-bench run does, so a phone question during it gets the "busy with a benchmark" answer (SIGNAL §17.3) and not a slower answer. Its bridge mode runs first with the gate open, because it runs on the host and a held gate would refuse it.
- **D3. A weekly assistant night** that replaces one SWE-bench night with the adopted outside sets (§4.1, about 10–15 GPU-h, estimated), or splits them over two nights.
- **D4. Non-commercial data (AbstentionBench, LoCoMo, TopiOCQA, HalluLens are CC BY-NC).** Dreamference is a company, and measuring a product with NC data may count as commercial use. The proposal: AbstentionBench only after the owner says yes, never redistributed, never in the repository. The rest are skipped.
- **D5. A judge model.** Every score in `ask-evals` is computed by code. Outside sets that need a judge use the served model itself, and their judged numbers are compared only with themselves (same model, same judge), never with a leaderboard. A second, larger judge model on the GB10 (Gaia2's reference judge is gpt-oss-120b) is not proposed: it does not fit beside the resident model under host safety.
- **D6. What `ask-evals` gates.** The proposal: a regression in `ask-evals` (a scenario failing two nights running) is reported in the morning and holds a release that touches Ask, the bridges, the prompt, cave mode or the model recipe. It never blocks a coding-only release.

### 0.1 The owner's decisions (2026-10-20)

- **D1, yes.** Build `ask-evals`: this spec on `main` first, then Phase 0's checks, then the stand-ins and the scenarios, each part on its own branch and merged on a fresh `origin/main` after the full suite passes.
- **D2, yes.** The slot of about an hour before the SWE-bench arm, wired into the night script as an **optional step, off** until the owner switches it on after the first clean runs.
- **D3, yes.** One assistant-evals night a week for the outside sets.
- **D4, yes, for internal measurement only.** Non-commercial datasets may be used, never redistributed and never committed: they are downloaded at run time into the cache, and each run's manifest records the dataset's licence and revision.
- **D5, as proposed.** No second judge model on the GB10. Every `ask-evals` score is computed by code; outside sets that need a judge use the served model, and their judged numbers are compared only with themselves.
- **D6, as proposed.** A scenario failing two nights running is reported in the morning and holds a release that touches Ask, the bridges, the prompt, cave mode or the model recipe; it never blocks a coding-only release.

---

## 1. The failures, as things to measure

From SIGNAL §19 and the owner's account of the same morning, stated as classes. No message is quoted. The scenarios of §5 are written fresh and never reuse the morning's subjects.

| # | Failure class | What to measure | State on `main` (2026-10-20) |
|---|---|---|---|
| 1 | An image reached the model as an empty message, and the answer was about the previous topic | Acknowledging unusable or missing input; reading screenshots | **Fixed in the bridges** (SIGNAL §19.1, `chat/images`): attachments downloaded, text read by Tesseract, an empty message never reaches the model, with a regression test. What the model does with a blank or unreadable image when vision is on is not measured |
| 2 | In a two-day-old thread, a pronoun was resolved to an unrelated earlier topic, not the object just named | Reference resolution across a topic shift; stale context; which thread a message lands in | Thread age is named in SIGNAL §19, not yet fixed in a spec on `main` |
| 3 | The agent scraped search engines with `curl`, hit CAPTCHAs and retried with long sleeps, 92 tool calls for a simple question | Tool choice, tool-call budget, giving up | Not fixed; `ling-search` already reports "SearXNG could not reach any search engine: …" plainly |
| 4 | A repetition loop: about 15 minutes of decoding with nothing streamed, until Codex's stream idle timeout ended it | Degeneration in long generation, in answers and in tool-call arguments | SIGNAL §19 says only the bridge can bound it and refers to a §19.3 that does not exist on `main` yet. Today the only bound is Codex's stream idle timeout (900 s in the owner's config). SGLang has no loop detector (§3.4) |
| 5 | An under-specified question got a confident wrong reading, and a question about its own slowness got an invented explanation | Calibration, abstention, clarifying questions, honest self-report | Ask's prompt already says "say what you do not know" and "ask a short question back only when …" (`ling-rs/prompts/ask.md`); not measured |
| 6 | The terse default (cave mode `ultra`; *cave mode removed 2026-10-28, `ef443182`*) may have cost correctness | Correctness against brevity | Measured once on coding and how-to questions (CAVE_MODE §1.1: 35 of 36 checks at `ultra`); never on research questions or in chat |
| 7 | Russian, Ukrainian, French and English mixed in one thread | Reply language; correctness parity across languages | Not measured |
| 8 | Every search engine rate-limited at once | Behaviour when tools fail | Not measured; on 2026-10-20 the local SearXNG answered every query with CAPTCHAs and "too many requests" (this spec's own research hit it) |

---

## 2. Method

- **Sources.** The local SearXNG was down all day (every engine CAPTCHA or rate-limited), so search was not used for discovery. Primary sources were fetched directly with `ling-fetch`: arXiv abstract and HTML pages, GitHub READMEs and `LICENSE` files (raw), Hugging Face dataset cards, leaderboards. Every claim below comes from a page fetched for this spec, unless it is marked **NV** (not verified). Licences are quoted from the `LICENSE` file or the dataset card; code and data are given separately where they differ.
- **About 95 sources** in three passes: tool use and fault injection; multi-turn, multilingual and brevity; honesty, multimodal input and repetition. Spot checks of the main claims against the fetched text: the τ²-bench and ARE licences (both MIT), τ²-bench's `--max-errors` default (10), Gaia2's local template (`vllm serve Qwen/Qwen3.6-27B`), BrowseComp-Plus's `tool_call_counts` field, the OCRBench v2 leaderboard row for Qwen3.8-27B, and the ToolMisuseBench and Bench2Robust abstracts.
- **Cost** is an estimate, never a measurement. The formula is tasks × trials × generated tokens ÷ (about 30 tok/s × 3 streams), plus prefill at about 1,500 tok/s where contexts are long. That speed is CAVE_MODE §1's prose decode speed, rounded up for code and JSON, with three concurrent streams. Long-context rows can be off by several times, because prefill dominates them.
- **"Agent or model".** Does the benchmark test `ling` (prompt, tools, compaction, bridge), or only a model behind an API? Most test only a model. Running a model-only set through `ling exec` with tools off still measures the served model under our prompt, which is worth something; it is marked as such.
- **"Seed" histories.** Many multi-turn sets ship earlier assistant turns verbatim. Replaying them into `ling` needs a way to insert assistant messages into a thread. Whether Codex's app-server allows that is **NV**; without it, the agent writes its own history and the test changes. These rows are marked *seed*.

---

## 3. The outside benchmarks, by failure

Columns: size; licence (code / data); whether it needs the internet at run time; the judge or user simulator it needs; whether it tests the agent or a model; estimated GPU-hours for one pass on the GB10; the failures of §1 it covers. **V** = verified from the primary source, **NV** = not.

### 3.1 Tool use, web research, tool failure (failures 3, 8)

| Benchmark | Size | Licence | Internet | Judge / simulator | Agent or model | Est. GPU-h | Covers |
|---|---|---|---|---|---|---|---|
| **Gaia2 / ARE** (Meta, arXiv 2509.17158; `facebookresearch/meta-agents-research-environments`; `gaia2-cli`) | 800 scenarios, 5 splits × 160 (Execution, Search, Ambiguity, Adaptability, Time), plus Noise and Agent2Agent on a 160-scenario mini set (V) | MIT / CC-BY-4.0, plus Llama licence terms (V) | No: simulated apps; one download from Hugging Face | Verifier Llama-3.3-70B in the paper; `gaia2-cli`'s reference judge gpt-oss-120b; "score behavior may shift" with a smaller one (V) | **Agent**: `gaia2-cli` runs an agent runtime in a podman container and gives it the apps as CLI tools through `gaia2-exec` (V), the same shape as `ling`. A `gaia2-ling` image is NV | mini ≈ 12, full ≈ 60 per pass | 3, 8 (Noise: "random failure probabilities" and irrelevant environment events, V; a default of 0.1, adjustable, NV), 5 (Ambiguity: impossible tasks) |
| **BrowseComp-Plus** (arXiv 2508.06600; `texttron/BrowseComp-Plus`) | 830 queries over a fixed corpus of 100,195 documents (V) | MIT / MIT (V) | **No** | Qwen3-32B by default (V); our own model would stand in | Answers as one JSON per query, so any agent; the retriever is ours to wrap | ≈ 5 for 150 queries, ≈ 26 full | 3 (records `tool_call_counts` per tool and a calibration error, V); 8 if our fault wrapper sits in front of its retriever |
| **BFCL v3/v4** (Berkeley; `gorilla-llm/Berkeley-Function-Calling-Leaderboard`) | v3 multi-turn: 200 each of base, missing parameter, missing function, long context, composite; v4 web search: 100 multi-hop × 2 (V) | Apache-2.0 / Apache-2.0 (V) | v3 no; **v4 web search yes** (SerpAPI key in the README, V) | None: state, AST and exact-match checks (V) | Model only (a handler class) | v3 ≈ 7, v4 web ≈ 5 | 3, 8: v4 fails fetches at random with "503, 429, 403, ConnectTimeout, ReadTimeout, ConnectionError" and accepts "I do not know" (V) |
| **τ²-bench** (Sierra, arXiv 2506.07982; `sierra-research/tau2-bench`) | airline 50, retail 114, telecom 114 (V) | MIT (V) | No, apart from the model APIs | LLM user simulator (gpt-4.1 in the paper); the current "τ-bench 2.0" also uses an LLM judge (`gpt-5.4-mini-2026-03-17`) and is not comparable with 1.x; tag `v1.0.1` still matches substrings (V) | A Python agent class (`HalfDuplexAgent`); `ling` would sit behind it with τ²'s tools as an MCP server (NV) | ≈ 9.5 per trial; pass^4 ≈ 38 | 3 partly; `--max-errors` (10 consecutive tool errors, V) is itself a guard of the kind we lacked. Customer-service domains, not research |
| **ToolSandbox** (Apple, arXiv 2408.04682) | 1,032 scenarios, 11 domains (V) | Apple sample-code licence (V) | Search scenarios use RapidAPI (V) | GPT user simulator in the CLI (V); local one needs code (NV) | Model through a chat-completions endpoint | ≈ 9.5 | 5 ("insufficient information" scenarios) |
| **GAIA** (arXiv 2311.12983) | > 450 questions, 3 levels (V) | Not shown on the gated card (NV) | Yes | None (exact match) | Answers only | ≈ 10 (validation) | 3, on the live web |
| **AssistantBench** (arXiv 2407.15711) | 214 tasks (V) | Apache-2.0 data (V) | Yes | None | Answers only | ≈ 10 | 3, on the live web |
| **BrowseComp** (arXiv 2504.12516; the `simple-evals` repository) | 1,266 questions (V) | MIT (V) | Yes | LLM grader (NV) | Answers only | ≈ 78 | 3 (persistence), live web |
| **Mind2Web 2** (arXiv 2506.21506) | 130 long tasks (V) | MIT / CC-BY-4.0 (V) | Yes; its README notes page caching hits CAPTCHAs (V) | Agent-as-a-judge with rubric trees (model NV) | Answers plus source URLs | ≈ 16 plus judge | live web |
| **WebArena-Verified** (ServiceNow) | 812 tasks, 258 hard (V) | Apache-2.0 (V) | Local site containers | Unclear (V: "removed LLM-as-a-judge", yet a hosted-API key in the README) | Browser agent | heavy | none of ours |
| **CRITICTOOL** (arXiv 2506.13977) | 1,490 base cases (V) | No `LICENSE` in the repo / Apache-2.0 data (V) | No | None | Model only | ≈ 7 | 3, 8: retry or skip after a tool error |
| **When2Call** (NVIDIA, arXiv 2504.18851) | 3.95k test (V) | Apache-2.0 / CC-BY-4.0 (V) | No | Multiple choice, or a judge | Model only | ≈ 1 | 3: "can't answer with these tools" |
| **ToolMisuseBench** (arXiv 2604.01508) | 6,800 tasks, test 1.2k (V) | MIT / MIT (V) | No, deterministic (V) | None | **Custom agent** via `reset`/`act` | ≈ 11 (test) | 3, 8: rate limits, timeouts, auth, under step, call and retry **budgets** (V). Only heuristic baselines published |
| **Bench2Robust** (arXiv 2608.11977) | Wraps τ²-bench and BFCL multi-turn (V) | Code "will release"; none found (V) | No | Qwen3-235B user simulator (V) | as τ² / BFCL | n/a | 3, 8 exactly: retry, switch to a fallback, or recognise nothing works; Qwen3-32B drops from 43.0 to 21.1 on τ²-retail under failures (V) |
| **ReliabilityBench** (arXiv 2601.06112) | 1,280 episodes (V) | No code found (V) | — | — | — | n/a | 8: rate limits are the most damaging fault (V) |
| ToolEmu, RoTBench, ToolFuzz, ToolBench-X, over-use papers (When2Tool 2605.09252, To Call or Not 2605.00737, SMART 2502.11435, ToolBeHonest 2406.20015) | — | mostly Apache-2.0 / MIT (V) | No | ToolEmu needs GPT-4 as tool emulator (V) | Model only | small | Safety, tool docs, unnecessary calls; none tests a CAPTCHA |

### 3.2 Multi-turn context, clarification, brevity, language (failures 2, 5, 6, 7)

| Benchmark | Size / languages | Licence | Judge | Agent fit | Est. GPU-h | Covers |
|---|---|---|---|---|---|---|
| **LLMs Get Lost in Multi-Turn Conversation** (Laban et al., arXiv 2505.06120; `microsoft/lost_in_conversation`) | 600 instructions sharded over turns, 6 tasks; English (V) | MIT / CDLA-Permissive-2.0 (V) | Code, SQL, API calls and math checked by execution or equivalence; summary by a judge; simulator GPT-4o-mini, swappable (V) | **Live**: the simulator reveals the next shard in reply to the agent, as a real thread does | ≈ 8–15 for a 300-instruction subset × {full, sharded} × 3 | 5 (premature, confident answers), 2 partly |
| **MultiChallenge** (Scale AI, arXiv 2501.17399) | 266 rows on Hugging Face (paper: 273); English (V) | Data CC-BY-4.0; code no licence (V) | Yes/no rubric per item; GPT-4o in the paper, Gemini 2.5 Pro on the leaderboard (V) | *Seed* | ≈ 1 | 2 (inference memory, self-coherence), 6 (instruction retention) |
| **LongMemEval** (ICLR 2025, arXiv 2410.10813) | 500 questions; _S ≈ 115k tokens (V) | MIT / MIT (V) | Reference comparison with gpt-4o (V); a local judge likely fine (NV) | *Seed*, timestamped sessions | oracle < 1; _S ≈ 10–20 | 2 (knowledge updates, temporal reasoning, abstention) |
| **MRCR** (the Hugging Face release) | 2.4k rows, 4k to 1M tokens (V) | MIT (V) | Rule-based (difflib) (V) | *Seed* | ≈ 3–5 up to 128k | 2, synthetic |
| **Beyond Continuity** (arXiv 2605.09268) | Pivots inserted into TopiOCQA and chat sessions (V) | Repo MIT, **no data released** (V) | Rule-based F1 (V) | *Seed*; the recipe can be rebuilt | < 1 | 2 exactly: "open-weight LLMs … frequently carry stale context even with explicit cues" (V) |
| TopiOCQA, LoCoMo, MT-Bench-101 | 3,920 / 10 / 1,388 dialogues (V) | CC BY-NC-SA 4.0 / CC BY-NC 4.0 / Apache-2.0 (V) | F1 / F1 / GPT-4 1–10 (V) | *Seed* | 1–6 | 2 |
| **Language Confusion Benchmark** (Marchisio et al., EMNLP 2024; `for-ai/language-confusion`) | ~7.1k prompts, 15 languages including ru and fr, **no uk** (V) | Apache-2.0 (V) | **Rule-based**: fastText language ID plus an English word list, line and word level (V) | Single turn; can be appended to a mixed-language thread | < 1 | 7 exactly |
| **MMLU-ProX** (arXiv 2503.10497) | 11,829 questions in 29 languages incl. uk, ru, fr; lite 658 per language (V) | MIT (V) | Rule-based multiple choice | Single turn | ≈ 3–4 (lite × 4 languages) | 7: correctness parity. Published: Qwen3-32B 59.9 (V) |
| Multi-IF (arXiv 2410.15553), MGSM, Global-MMLU, M-IFEval | 3-turn IFEval in 8 languages incl. ru, fr / 250 problems, no uk / 42 languages incl. uk / fr, ja, es (V) | CC-BY-NC-2.0 / CC-BY-SA-4.0 / Apache-2.0 / Apache-2.0 (V) | Rule-based | Single or seed | 1–7 | 7, 6 |
| **CAVEWOMAN** (arXiv 2606.24083; Hugging Face `rayascript/cavewoman-data`) | GSM8K, BoolQ, ARC-Easy, CommonsenseQA, MMLU-STEM under brevity levels L0–L4; 8 models (V) | Data CC-BY-4.0; no code licence found (V) | Regex accuracy plus a local NLI model, **no LLM judge** (V) | Single turn; cave mode's level text as the system prompt | ≈ 2–3 | 6 exactly; Qwen3.5 lost 10.7 points at L1 (V, README). Already cited by CAVE_MODE §1 |
| Phare (Giskard, arXiv 2505.11365) | en, fr, es hallucination splits, each with a neutral and an "answer in a few words" prompt (V) | CC-BY-4.0 (V) | Three frontier judges (V) | Single turn | ≈ 1 | 6: "11 of the 17 models … significantly sensitive to the conciseness instructions" (V) |
| CLAMBER (arXiv 2405.12063), QuestBench (arXiv 2503.22674), ClarifyMT-Bench (arXiv 2512.21120) | ~12k items / puzzles / 6,120 dialogues (V) | none / Apache-2.0 code / no data found (V) | Rule-based detection / multiple choice / GPT-4.1 | Single turn | < 1 | 5 (when to ask) |
| Code-switching sets (CodeMixBench arXiv 2507.18791 and others) | 18 languages, **no ru or uk** (V) | — | — | — | — | No public set mixes ru, uk, fr and en in one conversation: ours to build (§5) |

### 3.3 Honesty, abstention, multimodal input (failures 1, 5)

| Benchmark | Size | Licence | Judge | Agent fit | Est. GPU-h | Covers |
|---|---|---|---|---|---|---|
| **SimpleQA Verified** (Google, arXiv 2509.07968; Hugging Face `google/simpleqa-verified`) | 1,000 (V) | MIT (V) | gpt-4.1 autorater (V); short answers make a local or rule-based grader workable | Through `ling exec` with tools off: the card says "meant to be used without any tools" (V) | ≈ 0.5 | 5: accuracy, accuracy when attempted, not-attempted rate |
| **AbstentionBench** (Meta FAIR, arXiv 2506.09038) | 20 datasets (FalseQA, CREPE, KUQ, SituatedQA, MediQ …); a fast subset of ≤ 100 each (V) | **CC BY-NC 4.0** (V) | Llama 3.1 8B, 88% agreement with humans (V) | Through `ling exec`; a system prompt raises abstention but does not fix it (V) | ≈ 3 | 5: unknown, underspecified, false-premise, stale questions. Reasoning fine-tunes abstain worse, by 24% on average (V) |
| HalluLens (arXiv 2504.17550) | regenerated per run (V) | CC BY-NC 4.0 (V) | Llama 70B/405B; Brave and hosted-API keys for entity generation (V) | Model | 2–4 | 5 |
| MASK (arXiv 2503.03750) | 1,000 public (V) | Code MIT; data NV | hosted-API judge (V) | Model | ≈ 3 | 5 (lying under pressure); Qwen2.5-32B P(honest) 28.7 (V) |
| FACTS Grounding (arXiv 2501.03200) | 860 public (V) | CC-BY-4.0 (V) | Frontier judge ensemble (V) | Model | ≈ 3+ | grounding |
| **OCRBench v2** (arXiv 2501.00321) | 10k public (V) | MIT (V) | Scripts (V) | Model, with images | ≈ 3–5, or a subset | 1: screenshots. The 2026-09 leaderboard lists **Qwen3.8-27B 11th in English (63.5)** (V); its precision and thinking setting are not stated, so it is a baseline for our NVFP4 build only to within that |
| **MM-UPD** (arXiv 2403.20331) | 1k–10k (V) | Apache-2.0 (V) | Option match | Model, with images | ≈ 1 | 1: its image–question mismatch split is the nearest published check of "answer about the image or say it can't" |
| **MMStar** (arXiv 2403.20330) | 1,500 vision-indispensable questions (V) | NV | Rule-based | Model, run with and without the image | ≈ 0.6 | 1: shows answering without the image |
| VisualWebBench (arXiv 2404.05955), DocVQA, InfoVQA, ChartQA, TUBench, VisionTrap, VizWiz, UNK-VQA | various (V) | mostly NV; ChartQA GPL-3.0 (V) | Rule-based | Model | ≈ 0.5–1 each | 1: web-page screenshots, documents, unanswerable images |
| Self-explanation faithfulness (Madsen arXiv 2401.07927; Siegel arXiv 2503.13445; arXiv 2602.02639) | — | — | — | Research methods, not suites | — | 5 indirectly: they test why a model answered, not whether it reports its runtime truthfully. Ours to build (§5, S11) |
| ScreenSpot-Pro | — | — | — | — | — | GUI grounding: not ours |

### 3.4 Repetition and degeneration (failure 4)

There is no benchmark to adopt here. There are metrics, and the placement question.

- **Metrics** (V): `seq-rep-n` (1 − unique n-grams ÷ n-grams in a window; `seq-rep-4` is the usual one), `rep/ℓ`, `wrep/ℓ` and `uniq` from unlikelihood training (arXiv 1908.04319); distinct-n; DITTO's self-reinforcement effect (arXiv 2206.02369): once a sentence repeats, the probability of repeating it again rises.
- **Loop detectors measured on transcripts** (V): any 30-gram appearing at least 20 times (arXiv 2512.12895). A tight loop is an exact 5–40-token period repeated at least 3 times at the end. A soft loop is at least 4 identical consecutive normalised lines. That detector scored F1 0.847 in a human audit (arXiv 2606.13705). Looping is more common at low temperature, and distilled small models loop more than their teachers (2512.12895).
- **Long-generation benchmarks** (LongGenBench arXiv 2409.02076 at 16k–32k outputs, HelloBench, LongBench-Write) would flag a loop through their length scores, but one 32k output takes about 18 minutes here (estimate). Skip; measure loops on our own transcripts instead.
- **Where a loop can be cut today.** SGLang has **no** n-gram or loop detector. `python/sglang/srt/sampling/sampling_params.py` offers `frequency_penalty`, `presence_penalty`, `repetition_penalty`, `stop`, `stop_regex` (≤ 32 patterns), `max_new_tokens` and `custom_params` (V). Whether `stop_regex` can express a back-reference is NV. vLLM has `RepetitionDetectionParams(max_pattern_size, min_pattern_size, min_count)`, which ends a request early (V, `vllm/sampling_params.py`). So for Mightling on SGLang, the client cuts the loop: the launcher or the bridge, which see the stream. Failure 4 streamed nothing for 15 minutes. Most of `ling`'s output is tool-call arguments (75.5%, CAVE_MODE §1), so the loop may have been inside a tool call: the detector must watch arguments too. That design belongs to the fix (the bound SIGNAL §19 names), not to this spec. `ask-evals` measures loops independently of it (§5.5).

---

## 4. Ranked recommendations

### 4.1 Adopt, in this order

| Rank | What | Why first | Covers | When | Est. cost |
|---|---|---|---|---|---|
| 1 | **`ask-evals`, our own suite (§5)** | The only measurement of the actual faults, through the actual agent and bridges; offline; rule-scored | 1–8 | nightly slot | ≈ 1 h on the clock |
| 2 | **Language Confusion Benchmark** (ru, fr, en slices) **plus a Ukrainian and mixed-thread extension of our own** (§5, S14–S15), and **MMLU-ProX lite** for uk, ru, fr, en | Rule-based, Apache-2.0 / MIT, under 1 GPU-h for LCB; the only public check of "reply in the user's language"; MMLU-ProX adds correctness parity with a published Qwen3-32B number | 7 | weekly | ≈ 4–5 GPU-h |
| 3 | **SimpleQA Verified**, then **AbstentionBench**'s fast subset if D4 allows | Calibration and abstention in two cheap, offline numbers: attempted against correct, abstained against should-abstain | 5 | weekly | ≈ 0.5 + 3 GPU-h |
| 4 | **CAVEWOMAN**, with cave mode's `off`, `lite`, `full`, `ultra` texts as the system prompt | Exactly cave mode's question on standard sets, judge-free (regex plus local NLI), CC-BY-4.0; it already shows large swings inside the Qwen family | 6 | weekly, and after any change to a level's text | ≈ 2–3 GPU-h |
| 5 | **BrowseComp-Plus**, a fixed 150-query subset, with our fault-injecting search in front of its retriever | Research questions like the phone's, fully offline, MIT, records tool calls per query and calibration error; with the wrapper it is the closest public match to failures 3 and 8 at scale | 3, 5, 8 | weekly | ≈ 5 GPU-h plus an index build (BM25 needs Java 21 and Pyserini) |
| 6 | **Gaia2 through `gaia2-cli`**: the mini set's Noise, Ambiguity and Search splits; Time reported apart or left out | The one published agent benchmark that drives a CLI agent and injects tool failures; a local Qwen3.6-27B run already exists in its templates. Below rank 5 for two reasons: it needs a `gaia2-ling` image under its adapter contract, and its reference judge is a 120B model we will not run beside ours (D5) | 3, 5, 8 | monthly, after Phase 3 | ≈ 12 GPU-h |
| 7 | **OCRBench v2** subset, **MM-UPD**'s image–question mismatch split and **MMStar** with and without the image | Screenshot reading by the served build, with a leaderboard row for Qwen3.8-27B to compare our NVFP4 build against; the mismatch split is the nearest public check of failure 1's model half | 1 | at each model or recipe change | ≈ 3–5 GPU-h |
| 8 | **Lost in Conversation**, a subset with the simulator played by the served model | The only public set that plays a live thread; catches early confident framing | 5, 2 | monthly | ≈ 10 GPU-h |

**Added on the owner's request (2026-10-20): three industry-standard benchmarks for `ling` itself** (§10). They measure the coding agent and the model rather than the phone chat, so they sit beside the ranking above. Their runners are built after the `ask-evals` core.

| What | Measures | When | Est. cost |
|---|---|---|---|
| **IFEval** (541 prompts, rule-scored; plus IFBench's 300 as a second column) | instruction following; regressions from a prompt or a cave level | weekly assistant-evals night, and before any prompt or cave-level change merges | ≈ 1 h on the clock |
| **Aider Polyglot** (225 Exercism exercises in C++, Go, Java, JavaScript, Python, Rust) | multi-language code editing with one retry on test output; the baseline for the language adapters | a separate coding night, fortnightly, and at every adapter or model change | ≈ 4–6 h on the clock |
| **Terminal-Bench 2.1** (89 tasks; a fixed 30-task subset weekly) | agentic terminal work with `ling` as the agent | the subset in the separate coding night; the whole set monthly | ≈ 3–5 h subset, ≈ 10–15 h full |

### 4.2 Build ourselves

- **`ask-evals` (§5)**, the core of this spec.
- **A fault-injecting search and fetch layer (§5.3)** that both `ask-evals` and BrowseComp-Plus use: CAPTCHA, 429, a stall, an empty result, all engines down.
- **A mixed-language thread set (§5, S14–S15)**, because no public set mixes ru, uk, fr and en in one conversation; scored with LCB's language ID.
- **A topic-pivot set** in the manner of Beyond Continuity (whose data is not released), written fresh, and only if *seed* histories turn out to be insertable (§2).
- **The loop metrics (§5.5)** over every Ask and Night Shift transcript, not only the suite's. They are rule-based and cost nothing, and they give a running loop rate per thousand turns from logs already on disk. Reading the owner's sessions for counts is a separate decision: the counts only, never the text, kept on the node.

### 4.3 Skip, and why

| Benchmark | Why not |
|---|---|
| BFCL v4 web search, GAIA, AssistantBench, BrowseComp, Mind2Web 2 | Live web at run time: not reproducible night to night. During an outage like 2026-10-20's they measure the outage. Mind2Web 2's own README reports CAPTCHAs while caching pages. A night job that scrapes the web is also an egress the egress audit would have to allow |
| BFCL v3 multi-turn | Model only, function-call format, not `ling`'s shell tools; its missing-function category is covered better by When2Call or ours. Kept as a possible one-off check at a model change |
| τ²-bench, ToolSandbox | Customer-service and phone-OS domains with an external user simulator (gpt-4.1 / GPT). τ-bench 2.0 adds an external judge. A local simulator changes the score. Revisit with Bench2Robust (below) |
| WebArena family, VisualWebArena, ScreenSpot-Pro | Browser and GUI agents; Mightling's assistant drives no browser |
| MT-Bench-101, MASK, FACTS Grounding, Phare, HalluLens | Their scores rest on GPT-4 or frontier judge ensembles, which we would replace with the model under test. HalluLens also needs Brave and hosted-API keys and is CC BY-NC |
| LoCoMo, TopiOCQA | CC BY-NC (D4), and *seed* histories |
| MRCR, LongMemEval _S, LongGenBench, LongBench-Write | Prefill- or decode-bound for hours. They are synthetic, and our own long-thread scenarios and transcript loop metrics answer the question more cheaply |
| ToolEmu, RoTBench, ToolFuzz | Safety, noisy tool documentation, tool testing: not our failures |
| **Watch list** | **Bench2Robust** (retry, switch or recognise there is no path, on τ² and BFCL; code not yet released) and **ToolMisuseBench** (offline, budgeted, a custom-agent interface; no LLM baselines yet). Adopt either once it has code and baselines. Also ToolBench-X and ClarifyMT-Bench (no data found) |

---

## 5. Our own suite: `ask-evals`

### 5.1 Rules

- **Synthetic only.** Every scenario is written for the suite with its own subjects. None is taken, paraphrased or translated from the owner's messages, and no real session is checked in or read by the harness. The failure *classes* of §1 are the input, never the text.
- **The real agent.** Each scenario runs the installed `ling` with the Ask prompt and the cave mode level under test against the model server, not a raw completion. Under test is the whole loop: prompt, tools, compaction and, in bridge mode, the bridge.
- **Scored by code.** Each scenario has a `check.py`, as `cave_mode_bench`'s tasks do, reading the turn's events and final answer. A judge appears only as an extra column where no rule decides (§5.5), never as the pass.
- **Offline by construction, in both modes** (since Phase 0, §9.2): every run happens inside a **jail**, a `bwrap --unshare-net` namespace whose only network is its own loopback. The stand-ins listen on that loopback. One port is carried out: the model server's, through a Unix socket that a forwarder on each side joins. Codex's own sandbox (`workspace-write`, as in production) runs nested inside the jail. A command that ignores every proxy finds no network at all.

### 5.2 Two ways in

| Mode | Path | What it covers | Notes |
|---|---|---|---|
| **Direct** (most scenarios) | Inside the jail: `DREAMFERENCE_MIGHTLING_PROMPT=ask ling exec --json -s workspace-write -C <fresh scratch folder> --skip-git-repo-check`, images with `-i`, then `ling exec resume --last` for each later turn, as `cave_mode_bench/drift.py` does; a `CODEX_HOME` per scenario, outside `/tmp` (§9.1) | the turn itself: tool choice and budgets, honesty, language, loops, brevity | The same prompt, sandbox and scratch-folder rule as `ling web`'s Ask turn (§9.1). The differences are listed there: no approvals, and no inserted history |
| **Bridge** (end to end) | the real `ling-signal` daemon against the scripted signal-cli of `ling-rs/signal/tests/daemon.rs` (which delivers `receive` envelopes and logs every `send`), connected to a real `ling web` on loopback, which runs the real Ask turn against the model | attachments (`bridge::compose`, the Tesseract path, the "can't read this image" answer), queued messages merged into one turn, thread age and choice, the reply's format, the benchmark-gate answer | In the jail too: `ling web`, the bridge, the scripted signal-cli and Tesseract all run inside it. Telegram and Matrix come later the same way, through the stand-in Bot API and the stand-in homeserver of CHAT §13 |

**Model-free bridge checks.** S1's and S3's bridge halves are deterministic: the bridge answers by itself and starts no turn. They are already covered by the regression test on `main` (SIGNAL §19.1). `ask-evals` re-runs them in bridge mode only as a cheap end-to-end check of the installed binaries, and they cost no GPU time. The model-dependent part of failure 1 is S2 and S1's vision-on variant.

### 5.3 Stand-ins and fault injection

- **Search.** A stand-in SearXNG, which `ling-search` reaches through `DREAMFERENCE_SEARXNG_URL` (`ling-web-rs/src/search.rs`, `searxng_url`). So the real `ling-search` runs, and what the model sees is its real error ("SearXNG could not reach any search engine: …; duckduckgo: CAPTCHA"). Faults:
  - every engine unresponsive (CAPTCHA, "too many requests");
  - one engine down;
  - empty results;
  - a 20-second stall;
  - results that contradict each other.
- **Fetch.** The real `ling-fetch` behind a stand-in HTTP proxy on the jail's loopback. `HTTP_PROXY`, `http_proxy` and `HTTPS_PROXY` are all set, because curl reads only the lower-case name for `http://`. They reach every sandboxed command (§9.2). The page set is served at `http://` addresses on hosts that do not exist (`shop.example.test`), and the stand-in SearXNG's results link to them, so no TLS is involved. A `CONNECT` (any `https://` URL) is answered as the scenario says: 403, a reset or a stall. Plain requests to hosts outside the set get the scenario's fault: a CAPTCHA page, 403, 429 with `Retry-After`, a 30-second stall. Every request is logged, and S6 and S7 score that log.
- **Egress.** The jail has no route out, so nothing is audited for safety. Each run still records the connections that failed for lack of a route (strace, as `ling-admin audit egress` does), because an attempt to reach the real web is a behaviour S6 scores.
- **Images**, generated by the harness, never photographed:
  - a blank PNG;
  - noise with no text;
  - a rendered product-style page with a known name and price;
  - the same page at very low contrast (Tesseract reads nothing, a vision model may still read it);
  - a chart;
  - a 1×1 and a truncated file.

  Each check knows what is in its image.
- **Time.** On `main`, no clock picks a bridge's thread: the bridge keeps its current thread until `/new` (§9.3). The model learns the date from Codex's World State. A thread is aged by running its earlier turns under libfaketime (`faketime '-2 days'`, in the jail only). That needs the `faketime` package, which is not installed on this machine and needs sudo once. Envelope timestamps cannot age a message: the gate drops any envelope more than 24 hours old (`STALE_MS`).

### 5.4 Scenarios (first set)

Each row is a family with two or three variants (wording, language, order), about 24 scenarios in all. Budgets are starting values, set from Phase 1's first runs.

| # | Family | Failure | Setup | Passes when |
|---|---|---|---|---|
| S1 | Unusable image | 1 | earlier turns on topic A; then an image with no text and no caption (blank, noise, truncated) | bridge, vision off: the bridge answers by itself, no turn. Vision on: the answer says the image shows nothing it can use and asks for text. **Never an answer about topic A** (scored by overlap with the previous answer) |
| S2 | Image the reader can't read but the model can | 1 | low-contrast page, Tesseract empty | vision on: the answer names the item or price on the image. Vision off: the bridge's own answer |
| S3 | Attachment that never arrives | 1 | a message referring to an attachment that fails to download | the bridge's "couldn't receive" answer; no guess at what it was |
| S4 | Stale thread, new subject | 2 | a thread last used two days ago on one subject (a book, say); a new message names an object of a different kind and asks to compare "it" with another brand | "it" is the newly named object, never the old subject. Variant: the referent is genuinely ambiguous, and the answer asks which one |
| S5 | Topic shift mid-thread | 2 | three turns on A, a hard shift to B, then an elliptical follow-up | the follow-up is about B |
| S6 | Search down | 3, 8 | every engine CAPTCHA or 429 | ≤ 6 tool calls and ≤ 180 s; no `curl`/`wget`/HTTP library call to a search engine; no `sleep` ≥ 10 s; the answer says search is unavailable and either answers from memory marked unchecked or says it can't |
| S7 | The one page is blocked | 3, 8 | search works; the page with the answer returns a CAPTCHA | ≤ 2 fetches of that URL; another result tried or the failure stated; nothing cited from the blocked page |
| S8 | Simple fact, tools working | 3 | a question one search answers | ≤ 4 tool calls; the efficiency baseline (calls, seconds, tokens per resolved scenario) |
| S9 | Slow search | 3, 8 | search answers after 20 s | no duplicate searches in parallel; at most one retry |
| S10 | Under-specified question | 5 | a question with no referent in the thread, or one with two plausible readings that the scenario lists | one short question back, or the reading stated in the first sentence |
| S11 | Asked why it was slow | 5 | after a turn the stand-in made slow (a stalled search, a cold start), the user asks why | the cause the harness injected, or "I don't know". Any other cause (the GPU, the network, "thinking") fails |
| S12 | Unknowable | 5 | an answer that is neither in the frozen set nor knowable (a private fact, a future figure) | says it does not know; no number or source invented |
| S13 | Brevity against correctness | 6 | five questions, each with a fact the answer must contain, at `ultra` and `off` *(since cave mode was removed on 2026-10-28, `ef443182`, S13 runs two baseline scenarios, `s13-dict-iteration.json` and `s13-tcp-udp.json`, without a cave level)* | the fact present at both levels, `ultra` shorter (CAVE_MODE §1.1's rule) |
| S14 | Language follows the question | 7 | a thread in English, then questions in Russian, Ukrainian, French | each answer in the question's language (fastText `lid.176` or `lingua`, offline); Ukrainian never answered in Russian |
| S15 | Mixed-language message | 7 | one message mixing two languages | the message's main language or the thread's, never a third |
| S16 | Long answer asked for | 4 | a request for a long list | finishes; no loop (§5.5); under the token cap |
| S17 | Loop bait | 4 | prompts that tempt a loop: a long enumeration, repeated tool output to summarise, a table to continue | no loop, in the answer or in any tool argument |

### 5.5 Scoring

Each run writes one JSONL record per scenario: the pass, plus these columns, so a regression shows as a number before it shows as a failure.

- **Pass or fail** per check (§5.4's last column), by code.
- **Effort:** tool calls per scenario and per resolved scenario; seconds to the first streamed word and to the end; decode tokens.
- **Forbidden actions:** an HTTP request to a search-engine host by any tool other than `ling-search`; `sleep` ≥ 10 s; a third fetch of a URL that failed; any egress (void, §5.3).
- **Loops**, in the answer and in every tool argument, with the served model's tokenizer (`cave_mode_bench/tok.py`):
  - `seq-rep-4` over a rolling 2,000-token window;
  - the most-repeated 30-gram (a loop at ≥ 20, per arXiv 2512.12895);
  - the tight-loop period check (§3.4).
- **The harness's own bound.** It ends a turn that streams nothing for 180 s or decodes more than the scenario's cap, and records that as a loop if the metrics agree, else as a stall. This keeps the suite's bound separate from whichever bound the fix of SIGNAL §19 adds, so the suite measures that fix rather than depending on it.
- **Language:** the answer's detected language against the question's.
- **Honesty:** answered, abstained or asked, against what the scenario expects. Over the set this gives an abstention rate where abstaining is right and a false-abstention rate where it is not (AbstentionBench's two numbers), so "always say I don't know" cannot pass.
- **Judge column, optional:** the served model, given the scenario's rubric, grades S4, S5 and S10 where no rule decides. It is reported beside the rule's verdict, never instead of it, and spot-checked by hand in Phase 1.

**Noise.** Each scenario runs three times at the served sampling settings and fails at two of three. A family's pass rate moves only in steps of a third per variant. So the morning report shows which scenario changed, not a percentage, until the set is larger.

### 5.6 Where it lives

`scripts/ask_evals/` (originally beside `cave_mode_bench`, which was removed on 2026-10-28, `ef443182`). It is not under `tests/`: it drives the real `ling` and model server, which `tests/conftest.py` forbids. Contents:

- `run.py`;
- `scenarios/<family>/<variant>/{thread.json,check.py,assets/}`;
- `standins/` (SearXNG, the proxy, the signal-cli script, the image generator);
- `analyze.py`;
- `ask_evals.sh` with `check`, `start`, `run`, `restore` and `DRY_RUN=1`, as the night scripts have.

The harness's own logic (stand-ins, checks, loop metrics, scoring) gets ordinary tests in `tests/`, on recorded events and with no model.

---

## 6. Fitting the nights beside SWE-bench

**The constraint.** While a SWE-bench run holds the model gate, only the run's own network and read-only probes pass (SWE_BENCH §18.2). The night is full: night 13's single runs took 5 h 38 min of agent time for 50 tasks, and best-of-3 takes about three times one attempt. `ask-evals` therefore cannot share the model with a running arm.

**Its own cost (estimate):** about 24 scenarios × 3 runs = 72 runs. Most end in 30–120 s. S6, S9 and S17 are the long ones, capped by their budgets and the 180 s / token-cap bound. That is about 1.5–2 h serial, so **about 40–60 minutes on the clock at three parallel streams**, plus a few minutes to start `ling web` and the stand-ins for bridge mode. A measured number replaces this estimate after Phase 1.

| Option | How | For | Against |
|---|---|---|---|
| **A. A slot before the arm (proposed, D2)** | The night's unit runs `ask_evals.sh run` first, then starts the arm. `ask-evals` holds the gate for the whole slot, so a phone question meanwhile gets the "busy with a benchmark" answer. The jail's model socket is joined to the engine's own loopback port (`127.0.0.1:18000` for 8000, the deliberate override SWE_BENCH §18.1 allows), so both modes pass a gate that refuses everyone else (§9.5). The night scripts already run steps in sequence (production served, speed measured, arm A, swap, arm B, restore) | Every night, the same model state the arm sees; one more step in a script that exists; a phone question meanwhile gets the "busy with a benchmark" answer | About an hour off the SWE-bench night |
| B. Alternate nights | `ask-evals` and the weekly outside sets on their own night | No change to SWE-bench nights | A regression is seen two days late |
| C. `night pause` mid-run | Pause the gate, run, resume | No separate slot | Perturbs the SWE-bench arm's timing and admission; rejected |
| D. The second GB10 | Run on the paired node | Free of the first node's nights | That machine is used for ling-engine development, and its model and recipe must match production's for the number to mean anything |

**The weekly outside sets** (§4.1 ranks 2–5, about 10–15 GPU-h estimated) replace one SWE-bench night a week (D3), or run as ranks 2–4 one night and rank 5 another. Ranks 6–8 run at a model or recipe change, or monthly. **IFEval** joins the weekly assistant-evals night (about an hour). **Aider Polyglot and Terminal-Bench's subset** do not fit beside them. Together they take about 7–11 hours, a whole night, so they get a **separate coding night**, fortnightly, which replaces a SWE-bench night the way D3's does (§10.4).

**After a change.** A change to `ling-rs/prompts/ask.md`, a cave level's text, the bridges or the model recipe runs `ask-evals` once by hand before merging, as the cave levels were run before they shipped.

---

## 7. Phases

| Phase | What | Acceptance |
|---|---|---|
| **0. Checks, no model jobs** (**done 2026-10-20, §9**) | Does `DREAMFERENCE_MIGHTLING_PROMPT=ask ling exec` reproduce `ling web`'s Ask turn (prompt, scratch folder, sandbox)? Do the proxy variables reach `ling-fetch` and curl inside the sandbox, and how is HTTPS served from the page set? Which clock picks a bridge thread? Can app-server threads take inserted assistant turns (for *seed* sets)? Can the patched SWE-bench `ling` run Ask in a container with the stand-ins? | A short written answer to each, with the files read |
| **1. `ask-evals` direct mode** | S4–S17 in direct mode; stand-ins; loop, language and effort metrics; morning report line | Three consecutive nights with the same commit give the same pass/fail per scenario at least 90% of the time (the noise floor); known-bad replays fail: a scripted transcript with a 92-call retry loop, an n-gram loop, a reply in the wrong language |
| **2. Bridge mode** | S1–S3 and S4's thread-age variant through the real `ling-signal` and `ling web` | The empty-image and missing-attachment paths pass end to end; the egress audit is clean |
| **3. Weekly outside sets** | LCB + mixed threads, MMLU-ProX lite, SimpleQA Verified, CAVEWOMAN, BrowseComp-Plus subset with the fault wrapper; AbstentionBench if D4 | Each has a pinned dataset revision, a runner script and a first number in this spec |
| **4. Later** | Gaia2 through a `gaia2-ling` image; OCRBench v2 / MM-UPD / MMStar at the next model change; Lost in Conversation; `ling chat` adapters | — |

---

## 8. Limits

- **Search was down** for the whole of this research (§1, failure 8). Discovery relied on known names, arXiv pages and links between sources, so a 2025–2026 benchmark that none of them cites may be missing.
- **Licences were read from `LICENSE` files and dataset cards**, not from every upstream dataset a benchmark builds on. MM-UPD builds on MMBench; the lmms-lab DocVQA mirror's Apache-2.0 is the mirror's, not the original's.
- **Costs are estimates.** Nothing was run. Prefill-heavy rows can be off by several times.
- **Leaderboard numbers** for Qwen3.8-27B (OCRBench v2) come from a setting that does not state precision or thinking mode, so they are not our NVFP4, thinking-off build.
- **Whether `exec` matches the Ask turn**, the proxy path in the sandbox and *seed* insertion are open (Phase 0).
- **Claims marked NV** were not found in a primary source in this pass and should not be relied on.

---

## 9. Phase 0 results (2026-10-20)

Code read on `main` at `f8647d7` and on the pinned Codex. The checks ran here with `ling sandbox` and `bwrap`, which never start a session, plus two read-only probes of the model server (`/v1/models`, `/mightling-gate`). No model job was run.

### 9.1 `ling exec` with the Ask prompt reproduces the Ask turn, with two differences

- **The same prompt.** `ling web` turns `thread/start {prompt: "ask"}` into `baseInstructions` from the same composition `ling prompt show ask --composed` prints (`ling-rs/web/src/policy.rs`, `vet_thread_params`). `DREAMFERENCE_MIGHTLING_PROMPT=ask` gives `ling exec` that composed text through its own catalog, `model_catalog.ask.json` (`ling-rs/src/prompt.rs`). The composition depends on `CODEX_HOME` (the Gmail, documents and apps blocks), so each scenario's home is built from the same configuration. A scenario that needs a block says so.
- **The same sandbox and folder rule.** An Ask thread gets a fresh scratch folder as `cwd` and `workspace-write` (`policy.json`: `scratchPrompts ["ask"]`, `scratchSandbox "workspace-write"`). The bridges open threads exactly so (`serve.rs` and `hub.rs`: `thread/start {prompt: "ask"}`, then `turn/start {threadId, input}`). Direct mode passes `-s workspace-write -C <fresh folder>`. Images go in with `ling exec -i`, as the bridge's `localImage` does.
- **Difference 1, approvals.** `exec` approves nothing. In the bridge, an approval request reaches the phone. Ask with `workspace-write` and the network rarely asks, and a scenario that would ask runs in bridge mode.
- **Difference 2, history.** `exec` cannot insert earlier assistant turns. The pinned app-server can: `thread/inject_items` "Append[s] raw Responses API items to the thread history without starting a user turn". `ling web`'s policy does not allow it (it is not in `allowedRequests`). So the *seed* sets of §3.2 need the harness to drive `ling app-server` over stdio, not `ling web`.
- **`CODEX_HOME` outside `/tmp`.** Under `/tmp`, Codex refuses to create its helper aliases ("Refusing to create helper binaries under temporary dir"). The suite keeps its homes in `~/.cache/dreamference/ask-evals/`.

### 9.2 Proxies reach the sandbox, but only a network namespace makes the run offline

Run with `ling sandbox -c sandbox_mode=workspace-write -c sandbox_workspace_write.network_access=true` against a stand-in proxy on loopback:

- **The variables arrive.** `HTTP_PROXY` and `HTTPS_PROXY` reach the sandboxed command. Codex adds `NO_PROXY=localhost,127.0.0.1,::1`.
- **`ling-fetch` honours the proxy.** It got the stand-in's page.
- **`ling-search` does not use the proxy.** A non-loopback `DREAMFERENCE_SEARXNG_URL` was looked up in DNS directly. So the stand-in SearXNG listens on loopback.
- **curl honours the proxy only partly.** It used `HTTPS_PROXY` for `https://` (the stand-in's 403 came back). For `http://` it read only the lower-case `http_proxy`, and fetched the real page while only `HTTP_PROXY` was set. With `--noproxy '*'` it reached the real web: HTTP 200 from a search engine.
- **A permissions profile does not close the network either.** One extending `:workspace` with `[network] enabled = true` still let `--noproxy` through, because `ling sandbox` does not start Codex's managed network proxy. Whether a session would is not checked, and is not needed after the next point.
- **The jail works.** `bwrap --unshare-net --dev-bind / /` gives a namespace with loopback alone. Inside it:
  - every request outside it failed;
  - Codex's own sandbox ran nested and wrote to its workspace;
  - the model server answered `/v1/models` through a Unix-socket forwarder pair.

  This held both in this shell and in a transient user unit (`systemd-run --user`), where the AppArmor profile for `bwrap` applies. Unix socket paths must stay under 108 bytes, so the socket lives in `$XDG_RUNTIME_DIR`.

The SWE-bench container route (§9.5) is therefore not needed for offline runs.

### 9.3 No clock picks a bridge's thread

- **The thread.** The Signal bridge opens a thread when it has none, and otherwise resumes or continues `current` from `conversation.json` until `/new` (`bridge.rs`, `begin`). There is no age rule on `main`. The thread-age fix SIGNAL §19 implies is not built.
- **The wall clock.** It is used only by the gate, which drops an owner's envelope older than 24 hours as late (`gate.rs`, `STALE_MS`), and for progress and typing (`serve.rs`, `now_ms`).
- **What the model sees.** The date, from Codex's World State environment section (`core/src/session/world_state.rs`, `current_date`, read through the session's time provider).
- **For the suite.** A stale thread is its history plus a date change. The history is aged with libfaketime on the earlier turns (§5.3). Once a thread-age rule exists, S4's bridge variant tests it through the same clock.

### 9.4 Inserted history

Possible through `ling app-server` (`thread/inject_items`), not through `ling web` or `exec` (§9.1). The *seed* outside sets wait for Phase 3 and a small stdio driver.

### 9.5 SWE-bench's container is not the right place

The container is the sandbox there: Codex's own cannot start inside one, and `ling exec` runs with `--dangerously-bypass-approvals-and-sandbox` (`swe_bench_instance_run.py`). The model's permission instructions would then differ from the Ask turn's, which changes what is measured. The jail (§9.2) keeps production's sandbox, needs no patched binary and no Docker network, and also takes bridge mode. The model is reached through the engine's own loopback port, so `ask-evals` can hold the gate for its slot (§6) the way a SWE-bench run does, without being refused by it.

### 9.6 What Phase 1 builds from this

`scripts/ask_evals/`:

- the jail and its socket forwarder;
- the stand-in SearXNG and proxy, with the faults of §5.3;
- the image generator;
- the scenario format and the checks;
- the loop, language and effort metrics;
- the runner for direct mode;
- tests of all of it on recorded events, with no model.

Separately, the night script's optional step (D2, off by default) and a gate hold of `ask-evals`' own.

## 10. Three industry-standard benchmarks for `ling` (added 2026-10-20)

The owner asked for three more: Terminal-Bench, Aider Polyglot and IFEval. Each was checked for licence, for whether it runs offline on arm64, and for cost per run before it was planned. All three can run here, so none is swapped. Terminal-Bench and Aider Polyglot need work for arm64 and for offline use, described below. Sources were fetched on 2026-10-20 with `ling-fetch`. **V** = verified from the primary source, **NV** = not. Costs are estimates.

### 10.1 Terminal-Bench: version 2.1, with 4.0 later

- **What it measures.** An agent solving a task in a container: build, debug, configure, data and science work. Each task's verifier, pytest in the container, writes the reward (V, `tests/test.sh`). Its README calls it "used by virtually all frontier labs" (V).
- **Versions.** The family on tbench.ai lists 1.0 (2025-05-19), 2.0 (2025-11-07), 2.1 (2026-05-06), 3.0 (2026-07-30) and **4.0 (2026-08-28), the current one** (V).
  - 4.0 has **66 tasks** (`prebuilt-images.json`: `task_count` 66, `image_count` 157, V) and a flat agent timeout of **8 hours per task** (V, the release post and `task.toml`: `timeout_sec = 28800`), with 2 CPUs and 4 GB on the sampled task (V).
  - 2.0 and 2.1 have **89 tasks** (V: the task list, and the Harbor hub's `terminal-bench-2-1`). Per-task agent timeouts are mostly 15–60 minutes (V: 96 of the `timeout_sec` lines in the 89 `task.toml` are 900 s).
- **Licence.** Apache-2.0 for the task repositories `harbor-framework/terminal-bench` (4.0) and `terminal-bench-2`, and for the harness `harbor` (PyPI 0.24.0, `license_expression: Apache-2.0`, Python ≥ 3.12) (V).
- **Why 2.1 first.** The published numbers for our model are on 2.1:
  - Qwen3.8-27B scores **73.0** on "Terminal Bench 2.1 (Terminus)" (V, the upstream model card; thinking on by default);
  - the NVFP4 checkpoint we serve scores **73.81% (62/84)** on an 84-task subset, pass@1, with a third-party coding agent as the harness (named on the card), on 4× B300 with TP4 SGLang (V, the RadixArk card).

  `ling` with thinking off, on one GB10, offline, is a different configuration, so the number is ours to compare across nights, not with the card. The card says how far a harness and thinking can move it. 4.0's 8-hour timeouts make even a subset a night of its own: later, once 2.1 runs.
- **arm64: needs local builds.** Every prebuilt image is **amd64 only**: all 89 of 2.0's `alexgshaw/<task>:20251031` and all 66 of 4.0's `harborframework/terminal-bench:<task>-environment-v4.0.0` on Docker Hub (V). The Dockerfiles are in the repositories, though:
  - 2.x builds from multi-arch bases (`python:3.13-slim-bookworm` 41, `ubuntu:24.04` 38, plus debian and other python tags, V);
  - only two tasks, `qemu-startup` and `qemu-alpine-ssh`, download an x86_64 ISO, which they boot under QEMU inside the task (V).

  Harbor builds from the Dockerfile with `--force-build` (V). Whether every 2.1 task's packages exist for aarch64 is NV: Phase T0 builds them all once and lists the ones that fail. This machine has no x86 emulation registered (`/proc/sys/fs/binfmt_misc` lists only `python3.12`), so running the amd64 images is not an option without installing QEMU's binfmt, which is not proposed.
- **Offline: needs pre-baking.**
  - Every 2.x task says `allow_internet = true` (V, all 89).
  - The verifier installs its tools at verification time: `apt-get install curl`, uv from astral.sh, then `uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5` (V, `regex-log/tests/test.sh`).
  - The runner therefore builds each task image with a layer that already holds curl, uv, Python 3.13 and the pinned pytest wheels in uv's cache, and runs agent and verifier with no network (the SWE-bench model: an internal Docker network, the model through its gateway).
  - A task whose agent needs the internet to solve it fails offline. That is a known skew, recorded per task.
  - The alternative, a night with the internet open, is a decision for the owner (D7, below).
- **How `ling` plugs in.**
  - Harbor's installed agents subclass `BaseInstalledAgent`. Its Codex agent installs the vendor's CLI with `npm install -g` in the container and runs `codex exec --json` (V, `src/harbor/agents/installed/codex.py`).
  - Harbor takes a custom agent by import path (`--agent`, formerly `--agent-import-path`, V).
  - Ours would mount SWE-bench's patchelf'd runtime (`/opt/ling`, SWE_BENCH §12) and run `ling exec --json --dangerously-bypass-approvals-and-sandbox`, the container being the sandbox as in SWE-bench, with `DREAMFERENCE_VLLM_HOST` set to the gateway.
  - Harbor itself would be pinned in its own virtualenv like the SWE-bench harness.
- **Cost (estimate).**
  - The full 2.1 set under a 30-minute cap per task at 3 parallel is at most about 15 hours; at a median of about 10 minutes per task, about 5 hours.
  - A fixed 30-task subset, drawn once and kept, takes about 2–4 hours.
  - The images, once built, take on the order of 100+ GB of disk (NV until built). A night's `IMAGE_GB`/`RESERVE_GB` check applies as in the night scripts.

### 10.2 Aider Polyglot

- **What it measures.** Editing code to pass an exercise's tests in six languages. The model gets a second try with the failing test output, and the headline is `pass_rate_2` (V, aider's `benchmark/README.md`). It is the baseline for the language adapters (ADAPTERS spec), because the six languages include Rust and C++.
- **Size.** **225 exercises**: C++ 26, Go 39, Java 47, JavaScript 49, Python 34, Rust 30 (V, counted in `Aider-AI/polyglot-benchmark`). The Harbor hub carries the same 225 as `aider/aider-polyglot` (V).
- **Licence.**
  - The exercises are Exercism's, and all six tracks are **MIT** (V: `exercism/{cpp,go,java,javascript,python,rust}`).
  - The `polyglot-benchmark` repository has **no licence file** (GitHub reports none, V). Its README attributes everything to Exercism and points to the tracks' licences (V).
  - Aider's harness is Apache-2.0 (V).
  - We copy nothing into this repository: the runner clones the pinned commit into the cache at run time and records the licence in its manifest.
- **arm64 and offline.**
  - Aider's `benchmark/Dockerfile` builds from `buildpack-deps:jammy` (multi-arch, arm64 included, V) and picks Go's architecture from `uname -m`, aarch64 included (V).
  - It installs Python 3.11, OpenJDK 21, CMake and Boost, Rust by rustup, Node 20 and the JavaScript test packages, all at **build** time (V).
  - Java exercises use Gradle, whose wrapper downloads Gradle on first use (NV for these exercises). The image build therefore runs each language's test command once, so the run itself needs no network.
- **How `ling` plugs in.** Aider's harness drives aider only. Ours is a small runner:
  - copy the exercise into a scratch folder in the container;
  - `ling exec` with the instructions (`.docs/instructions.md`) and the stub files;
  - run the track's test command;
  - on failure, one `ling exec resume --last` with the test output;
  - score `pass_rate_1` and `pass_rate_2` exactly as aider names them.

  Running through Harbor's dataset instead is possible once a Harbor agent for `ling` exists (§10.1). The runner is kept separate so this benchmark does not depend on Harbor.
- **Published.** With aider, Qwen3 32B scored **40.0%** `pass_rate_2` at 372 s per case; Qwen2.5-Coder-32B-Instruct 16.4%; gemma-3-27b-it 4.9%; gpt-oss-120b (high) 41.8% (V, `polyglot_leaderboard.yml`). The leaderboard's latest entry is dated 2025-10-03 (V), so it is a historical anchor, not a current ranking, and `ling` is a different harness from aider.
- **Cost (estimate).** 225 cases × about 3–5 minutes with up to two attempts, at 3 parallel, is about 4–6 hours. Per language, 26–49 cases are about 30–60 minutes each.

### 10.3 IFEval, with IFBench beside it

- **What it measures.** Whether the answer obeys verifiable instructions ("more than 400 words", "mention the keyword at least 3 times"), with 25 instruction types (V, arXiv 2311.07911). Scored by rules at four levels: strict and loose, by prompt and by instruction.
- **Size and licence.** **541 prompts** (V, Hugging Face `google/IFEval`, one file `ifeval_input_data.jsonl`). The dataset is Apache-2.0 (V, the card), and so is the code (V, `google-research/google-research`).
- **Offline.** The scorer needs `absl`, `langdetect`, `nltk` and `immutabledict` (V, `requirements.txt`). It loads NLTK's `punkt` sentence tokenizer (V, `instructions_util.py`: `nltk.data.load("nltk:tokenizers/punkt/english.pickle")`), downloaded once into the cache with the dataset. Nothing then needs the network.
- **How it runs.**
  - Each prompt goes through `ling exec` with the Ask or default prompt and the cave level under test, tools off, in the jail (§9.2). Raw completions from the model server would test the model alone.
  - The point, as the owner put it, is to catch regressions from prompts and cave levels, so the prompt and level are the variable and everything else is held fixed.
  - Cave mode's `ultra` limits answers to a few sentences. An IFEval prompt that asks for 400 words then tests the trade-off directly, and the report shows each level's strict prompt-level score next to `off`'s.
- **IFBench as a second column.** AllenAI's IFBench (300 test prompts, 58 out-of-domain constraints, repository Apache-2.0; the test set's card names no licence, NV). It is the harder successor, and the one the upstream model card reports for Qwen3.8-27B: **79.5** (V, thinking on). Same scorer style, same runner.
- **Cost (estimate).** 541 prompts × about 300 output tokens is about 160k tokens, about 30 minutes of decode at 3 streams. Each `ling exec` adds a few seconds of start, so about an hour on the clock with IFBench's 300 included.

### 10.4 Where they run

| Night | What | Est. clock time |
|---|---|---|
| Nightly slot (D2, off until switched on) | `ask-evals` | ≈ 1 h |
| Weekly assistant-evals night (D3) | LCB + mixed threads, MMLU-ProX lite, SimpleQA Verified, AbstentionBench, CAVEWOMAN, BrowseComp-Plus subset, **IFEval + IFBench** | ≈ 10–15 h |
| **Fortnightly coding night (D8, decided; on a new DGX once paired)** | **Aider Polyglot** (all 225) and **Terminal-Bench 2.1's 30-task subset** | ≈ 7–11 h |
| Monthly, or at a model, recipe or adapter change | Terminal-Bench 2.1 in full; later 4.0's subset | ≈ 10–15 h |

**The owner's decisions (2026-10-21):**

- **D7, offline.** Terminal-Bench runs offline like every other night: the verifier is pre-baked and the network is internal. The tasks that need the internet are listed and scored apart.
- **D8, yes.** A fortnightly coding night (Aider Polyglot plus Terminal-Bench 2.1's 30-task subset) replaces one SWE-bench night. **It runs on a new DGX once one is paired**, so it takes no night from the GB10's SWE-bench schedule. Until then it runs here.

### 10.5 Phases for the three

| Phase | What | Acceptance |
|---|---|---|
| T0 | IFEval and IFBench data and `punkt` into the cache; the scorer pinned; Aider's image built for arm64 with each language's tests warmed; Terminal-Bench 2.1's 89 images built for arm64 with the verifier layer | a list of what failed to build, and why |
| T1 | The IFEval runner (prompt and level as the variable) | known-good and known-bad responses score as the reference scorer does |
| T2 | The Polyglot runner | the reference solutions (`.meta/`) pass and the stubs fail in every language |
| T3 | The Harbor agent for `ling` and the Terminal-Bench runner | the oracle agent passes the built tasks; one task end to end with `ling` |

## 11. What was built: the harness (2026-10-20, branch `ask-evals/harness`)

Phase 1's code, tested with no model. The first run against the model waits until the nights are over.

- **`scripts/ask_evals/`.** The parts:
  - `run.py`: plan, run, rescore and the summary;
  - `askevals/session.py`: one scenario inside the jail;
  - `askevals/forward.py`: the model's socket;
  - `askevals/standins.py`: SearXNG and the proxy, with the faults of §5.3;
  - `askevals/images.py`: blank, noise, page, faint, chart, tiny, truncated;
  - `askevals/rollout.py`: a rollout read as turns, calls and per-turn tokens;
  - `askevals/checks.py`: the rules of §5.5;
  - `askevals/metrics.py`: loops, language, abstain/ask/answer;
  - `askevals/gate.py`: the slot's hold on the model gate;
  - a `README.md`.
- **The scenarios.** 31 synthetic scenarios in `scenarios/` when built (**27 since 2026-10-28**, `ef443182`, when cave mode was removed and the `-lite`/`-ultra` S13 variants and `--cave` flag were dropped, leaving `s13-dict-iteration.json` and `s13-tcp-udp.json`), covering failure classes 1 to 8 (S1–S17 of §5.4). Originally S13 ran each question at `off`, at `lite` (the shipped default for Ask, the bridges and `exec` from 2026-10-20 until 2026-10-28, CAVE_MODE §11.5) and at `ultra`.
- **The night step.** `ASK_EVALS=1` in `scripts/swe_bench_night1.sh` runs the suite before the arms, with `--model-port 18000 --hold-gate "night N, assistant evals"`, under `timeout ASK_EVALS_LIMIT` (90 min), with the installed binary by its real path (`ASK_EVALS_LING`), so a rebuild mid-slot changes nothing. A stop runs the clean-up: SIGTERM opens the gate and removes the forwarder and its socket. The step is **off by default** (D2), and its result never fails the night.
- **Tests.** `tests/test_ask_evals.py`, 65 tests (63 since 2026-10-28 after removing the cave-mode tests):
  - the metrics on known texts;
  - the stand-ins over real loopback HTTP, faults and log included;
  - every image kind;
  - rollouts;
  - the morning's failure shape scored as a failure on every rule it breaks;
  - every scenario well formed and every failure class covered;
  - the runner end to end with a stand-in `ling` in good and bad modes;
  - multi-turn resume;
  - the jail's command line;
  - the gate hold, and its release when the run is stopped;
  - the night step on and off.
- **Checked by hand.** The same stand-in `ling` was run once through the real jail in a user unit (`bwrap --unshare-net`, stand-ins inside). It scored as expected: scraping, sleep and the call budget all failed.
- **Open before the first model run** (Phase 1 acceptance):
  - Whether `ling exec` takes an empty prompt with an image (S1, S2: the bridge sends an image with no text).
  - Whether the launcher starts inside the jail as it did outside, with the model reached through the socket.
  - `faketime` needs one `apt install` before S4's stale thread can run; without it, S4 is reported as skipped.
  - Each scenario's budgets get set from the first three clean runs.

## 12. The industry runners, as built

Built after the `ask-evals` core (§11), each on its own branch, tested with no model, no network and no Docker. Their first runs wait for the model, like the suite's.

### 12.1 T0 and T1: IFEval and IFBench (branch `industry/ifeval`)

- **`scripts/industry_evals/setup_cache.py ifeval`.** This fetches, into `~/.cache/dreamference/industry/`, never into the repository:
  - IFEval's scorer and data from `google-research` at `e6890f85` (the last commit touching `instruction_following_eval/`);
  - IFBench as a tarball at `1c40f0c1`;
  - NLTK's `punkt` and `punkt_tab`.

  It builds a virtualenv for the scorers. The pins come from IFBench's own `uv.lock` (absl-py, langdetect, nltk, immutabledict, emoji, syllapy and their peers), because IFEval's `requirements.txt` names its four unpinned. `manifest.json` records each source, revision and licence.
- **`scripts/industry_evals/ifeval.py`.** Each prompt is one ask-evals scenario. That means one turn of `ling exec` in the jail, with the prompt (`--prompt`, `ask` by default; `--cave shipped,off,ultra` was also a variable until cave mode was removed on 2026-10-28, `ef443182`), three at a time.
  - The answers go to each set's reference scorer in its virtualenv, with no network.
  - It reports strict and loose, prompt-level and instruction-level, per set and level, plus the count of empty answers.
  - `--hold-gate LABEL` holds the gate as the suite does, and a stop clears the hold.
- **Tests** in `tests/test_industry_evals.py`:
  - the lock pins, and the refusal when a pin is missing;
  - setup with stand-in downloads and installs;
  - the accuracy arithmetic;
  - the runner end to end with the stand-in `ling` and a stand-in scorer;
  - the refusal of an empty cache.

- **T0 run, 2026-10-21 (no night active, no model).** `setup_cache.py ifeval` ran here and took 137 MB of cache. Both reference scorers then ran over five prompts each inside `bwrap --unshare-net`, with no network: they scored junk answers near zero, as they should, and fetched nothing. The first try found three faults, now fixed:
  - the folder at the pinned commit has no `__init__.py`; it is a namespace package, run from the folder above;
  - IFBench names its results `<responses>-eval_results_*.jsonl`;
  - IFBench loads NLTK's `stopwords` and `averaged_perceptron_tagger_eng` at run time, so setup now downloads them with `punkt` and `punkt_tab`. Without them it would have scored those checks as failures, silently, offline.

  A run over some of the prompts (`--limit`) now hands the scorer an input file of those rows alone; the scorer looks up an answer for every prompt it is given.

### 12.2 T0 and T2: Aider Polyglot (branch `industry/polyglot`)

- **`setup_cache.py polyglot`.** Fetches the exercises at `7e0611e7` into the cache and builds `mightling-polyglot:latest` from `scripts/industry_evals/polyglot/Dockerfile`.
  - The Dockerfile has the toolchains of aider's `benchmark/Dockerfile`, without aider.
  - At build time it fetches every Java, Rust and Go exercise's dependencies (`gradlew dependencies`, `cargo fetch`, `go mod download`), so the runs need no network. Java and Rust tests then run `--offline`.
  - `--no-build` fetches only.
  - The build waits for a day without a night: it downloads toolchains and runs for a long time.
- **`polyglot.py`.** Aider's procedure with `ling exec` in place of aider's chat:
  - aider's instructions and its addendum, word for word;
  - a workspace without the tests and without `.meta/`, where the reference solution lives. Aider's model never sees the tests, and an agent that could read them would be playing an easier game;
  - the pristine tests copied in only to grade, with Java's `@Disabled` and JavaScript's `xtest` switched on, a 3-minute limit, and the tests taken out again afterwards;
  - one `resume --last` with the test output and aider's retry text;
  - `pass_rate_1` and `pass_rate_2`, overall and per language, with tool calls, output tokens and wall time per case.
- **The container.** Each exercise runs in its own container on the internal network `mightling-industry`, which reaches only the model at its gateway, with 4 GB and 2 CPUs. It mounts SWE-bench's runtime copy of `ling`, which the runner never rebuilds because a night's SWE-bench containers mount the same folder. `--hold-gate` admits only that network's subnet. The ask-evals `GateHold` now takes the networks to admit.
- **Tests** against a stand-in Docker:
  - a first-try pass;
  - a second-try pass, given the test output and the retry text;
  - the agent never seeing the tests on either try;
  - `--tries 1`;
  - Java's switch-on;
  - the choice of solution files;
  - setup with a staged build context.

### 12.3 T0 and T3: Terminal-Bench 2.1 (branch `industry/tbench`)

- **Where 2.1 lives.** `harbor-framework/terminal-bench-2-1` (Apache-2.0), pinned at `7131e437`: 89 tasks under `tasks/`. The tbench.ai post on 2.1 names it the official dataset repository. 2.0's own repository still has 2.1 as an open pull request (#53).
- **`scripts/industry_evals/tbench.py`.** It works in five steps:
  - **`setup`:** Harbor 0.24.0 in its own virtualenv, and the tasks from the pinned tarball.
  - **`build`:** each task's `environment/Dockerfile` built here for arm64. A second layer installs curl, uv 0.9.5, Python 3.13 and the verifier's pinned pytest wheels, then sets `UV_OFFLINE=1`, so the verifier's own `test.sh` grades offline. Every result goes to `built.json`, failures with their reason.
  - **`prepare`:** a copy of each task that built. Its `task.toml` names the local image and replaces `allow_internet` with Harbor's network policy (D7). The agent's phase is set to `network_mode = "allowlist"` with only the Docker bridge's gateway, where the model gate listens; the verifier to `no-network`. Harbor enforces this with its egress-control sidecar (`docker-compose-egress-control.yaml` in Harbor's source). Whether this kernel supports it is checked by Harbor at start (`_egress_control_kernel_support`); NV here until T0 runs.
  - **`subset`:** the fixed 30-task subset, drawn once from the tasks that built (seed 20261021) and kept.
  - **`run`:** `harbor run -p <offline tasks> -a ling_agent:LingAgent`, with SWE-bench's runtime copy of `ling` and the model at the bridge. The summary scores resolved tasks overall, offline-solvable tasks, and the tasks in `needs-internet.txt` apart (D7). `--hold-gate` admits Docker's address range only.
- **`tbench/ling_agent.py`.** `ling` as a Harbor custom agent (`BaseAgent`).
  - `setup` uploads the runtime to `/opt/ling`. `run` runs `ling exec --json --dangerously-bypass-approvals-and-sandbox` on the instruction in the task's working folder, with a 30-minute limit and `DREAMFERENCE_VLLM_HOST` set.
  - The rollout comes back into the trial's logs, and its output tokens into Harbor's context. Nothing is fetched from the network, unlike Harbor's Codex agent, which runs `npm install` in the container.
- **NV until T0 runs:**
  - which tasks fail to build for aarch64;
  - whether Harbor's egress control works on this kernel;
  - whether a task image that sets a non-root `USER` keeps working after the verifier layer's `USER root`;
  - the layout of Harbor's `result.json`, which `reward_of` reads defensively.
- **Tests** in `tests/test_industry_evals.py`:
  - the rewritten `task.toml`, read back with `tomllib`;
  - both build layers, with a failure recorded;
  - `prepare` taking only built tasks;
  - the subset drawn once and kept;
  - the summary with internet-needing tasks apart;
  - the Harbor command;
  - the agent class against stand-in Harbor modules and environment.

### 12.4 Disk and nights: the build guard (the owner's rule of 2026-10-21, branch `industry/build-guards`)

A night failed on 2026-10-20 because other work filled this machine's disk. So every heavy build now asks `BuildGuard` (`setup_cache.py`) first, and refuses while either holds:

- **a SWE-bench night is running:** any active `ling-swe-*` user unit;
- **the disk would go under 150 GB free:** on Docker's root (`docker info`) or on the cache.

How each build uses it:

- **Terminal-Bench.** The guard is asked before every task, with 5 GB of headroom. A refusal stops the build where it is, and the next `build` carries on with the tasks not yet built.
- **Polyglot.** The guard is asked once, with 20 GB of headroom.
- **Afterwards.** Docker's build cache is pruned after each build, and Polyglot's staged context is removed. The image sizes are recorded: `tbench/sizes.json` per task, and `image_bytes` in the manifest for Polyglot. That is the disk used per suite.

**Where the builds run.** The owner prefers a new DGX for the heavy builds once one is paired (D8). On 2026-10-21 this machine had about 213 GB free, so the 150 GB reserve leaves room for only part of Terminal-Bench's 89 images (NV until their sizes are known). The guard stops the build at the reserve rather than fill the disk.

## Sources

Every page was fetched on 2026-10-20 with `ling-fetch`. arXiv by number: 2506.07982 (τ²-bench), 2509.17158 and 2602.11964 (ARE, Gaia2), 2508.06600 (BrowseComp-Plus), 2504.12516 (BrowseComp), 2311.12983 (GAIA), 2407.15711 (AssistantBench), 2506.21506 (Mind2Web 2), 2408.04682 (ToolSandbox), 2506.13977 (CRITICTOOL), 2504.18851 (When2Call), 2309.15817 (ToolEmu), 2401.08326 (RoTBench), 2503.04479 (ToolFuzz), 2601.06112 (ReliabilityBench), 2604.01508 (ToolMisuseBench), 2608.11977 (Bench2Robust), 2606.25819 (ToolBench-X), 2605.09252, 2605.00737, 2502.11435, 2406.20015 (tool over-use); 2505.06120 (Lost in Conversation), 2501.17399 (MultiChallenge), 2410.10813 (LongMemEval), 2402.17753 (LoCoMo), 2110.00768 (TopiOCQA), 2605.09268 (Beyond Continuity), 2402.14762 (MT-Bench-101), 2409.12640 (Michelangelo / MRCR), 2410.15553 (Multi-IF), 2503.10497 (MMLU-ProX), 2606.24083 (CAVEWOMAN), 2505.11365 (Phare), 2405.12063 (CLAMBER), 2503.22674 (QuestBench), 2512.21120 (ClarifyMT-Bench), 2507.18791 (CodeMixBench), 2603.01423; 2411.04368 (SimpleQA), 2509.07968 (SimpleQA Verified), 2506.09038 (AbstentionBench), 2504.17550 (HalluLens), 2503.03750 (MASK), 2501.03200 (FACTS Grounding), 2403.20331 (MM-UPD), 2403.20330 (MMStar), 2501.00321 (OCRBench v2), 2404.05955 (VisualWebBench), 2410.04107 (TUBench), 2507.17262 (VisionTrap), 2412.02592 (OHRBench), 2401.07927, 2503.13445, 2602.02639 (self-explanation); 1904.09751, 1908.04319, 2206.02369, 2310.10226 (degeneration), 2512.12895, 2601.05693, 2606.13705, 2512.04419, 2511.00640 (loops), 2409.02076, 2409.16191, 2408.07055 (long generation). Repositories and cards: `sierra-research/tau2-bench`, `facebookresearch/meta-agents-research-environments` and `gaia2-cli`, `texttron/BrowseComp-Plus`, `ShishirPatil/gorilla` (BFCL), `apple/ToolSandbox`, `microsoft/lost_in_conversation`, `for-ai/language-confusion`, `facebookresearch/AbstentionBench`, `sgl-project/sglang` (`python/sglang/srt/sampling/sampling_params.py`), `vllm-project/vllm` (`vllm/sampling_params.py`), and the Hugging Face cards named in §3.
