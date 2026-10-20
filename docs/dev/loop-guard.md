# The loop guard

What stops the model when it repeats itself: a cap on each response's length, applied by the model gate, and a detector on the streamed text, in the launcher. Code: `ling-rs/src/loop_guard.rs` (the Codex extension), `ling-rs/loop-guard/` (the detector), `dreamference/vllm_server/model_gate_guard.py` (the gate's half). Built on 2026-10-20 on the owner's decision of that morning.

## What happened

On 2026-10-20 a phone question stalled for 15 minutes. The request (thread `01a14fb7`, 08:53:11) received no SSE event at all until Codex's 900 s idle timeout fired at 09:08:11 and its retry succeeded; meanwhile SGLang decoded from 38.9K to 110.9K tokens at a draft acceptance of 0.95 to 0.99, the signature of exact repetition (`docker logs dreamference-vllm-8000`). The same failure is reported upstream on the same stack (huggingface.co/Qwen/Qwen3.8-27B discussion 178, 20K to 55K tokens).

It was not a thinking loop. Every `ling` surface runs the model with thinking off: the launcher's catalog advertises one reasoning level, `none` (`model_catalog` in `ling-rs/src/lib.rs`), Codex sends `reasoning.effort: "none"`, and SGLang's `ResponsesRequest` turns that into `enable_thinking: false`. No rollout since September has a reasoning item, and every token count has `reasoning_output_tokens: 0`. Nothing streamed because SGLang's `qwen3_coder` tool parser holds a `<parameter=…>` value until `</parameter>`: a loop inside a tool call's argument (an `exec` script) produces no event, so no client-side watch of the stream can see it. Only a length limit the server enforces ends it.

## What each surface sends, before and after

The engine is SGLang v0.5.21 (`--sampling-defaults model`, the default): a request that omits sampling gets the checkpoint's `generation_config.json`, temperature 1.0, top_p 0.95, top_k 20 (the start log: "Using default chat sampling params from model generation config"), and the request model's own defaults, presence_penalty 0, min_p 0, repetition_penalty 1. Those are the Qwen3.8 card's thinking-mode values; its non-thinking values, the mode `ling` runs in, are temperature 0.7, top_p 0.8, top_k 20, min_p 0, presence_penalty 1.5 (verified on the card 2026-10-20). Codex has no sampling setting and sends none, and no `max_output_tokens`.

| Surface | Before | After (defaults) |
| --- | --- | --- |
| TUI (`ling`, `ling resume`) | effort `none`, no sampling, no cap | the same, plus a cap of 32,768 tokens per response and the detector |
| `ling exec` (and so SWE-bench and Night Shift) | effort `none`, no sampling, no cap | **unchanged, byte for byte**: no metadata, no interceptor |
| Work threads (`ling app-server`, cwd outside `ask/`) | as the TUI | cap 32,768 and the detector |
| Ask threads (`$CODEX_HOME/ask/q-*`) | as the TUI | cap 8,192 and the detector |
| Phone threads (`$CODEX_HOME/ask/chat-*`, the bridges' `chat` prompt) | as Ask | cap 4,096 and the detector |

Sampling is unchanged everywhere by default (owner, 2026-10-20); the non-thinking values exist as an arm. The per-response output measured on this machine sets the caps: Ask p99.9 361 tokens (128 responses), every saved session p99.9 886, SWE-bench p99.9 2,766 and max 21,196 (a runaway compaction summary, 39,107 responses). At the 90 to 100 tok/s a loop decodes at, 4,096 tokens end a buffered loop in about 45 s instead of 15 minutes.

## The budget: why a length cap and not a thinking budget

SGLang v0.5.21 has a per-request thinking budget (`max_thinking_tokens`, enforced by the reasoner grammar), but only on the native `/generate` endpoint, and only with `--enable-strict-thinking`, which applies a token filter to every request's thinking server-wide. `/v1/responses`, the only endpoint `ling` speaks, carries neither it nor `custom_logit_processor`. And thinking is off anyway. So the budget is `max_output_tokens`, which `/v1/responses` honours.

Codex builds the request body and offers no way to change it. What its extension API lets the launcher add is `client_metadata`, so the launcher names the cap there (`ling_max_output_tokens`, with `ling_surface` and, for the arm, `ling_sampling`), and the model gate, which reads each `/v1/responses` body sent whole and under 64 MB (anything else streams through untouched, as before), sets `max_output_tokens` (and the arm's sampling fields) **only where the body has none**. A body with no `ling_*` key is passed on byte for byte. The gate's probe answers `loop_guard: 1` when it has these rules.

## The detector

Three checks on the text of each streamed reasoning item and each answer (`ling-rs/loop-guard`), counted in *pieces*, the Qwen pre-tokenizer's splits, which count close to tokens without the vocabulary:

- **periodic suffix**: the last pieces are one block of 32 to 2,048 pieces repeated 4 times back to back (`min_pattern_size`, `max_pattern_size`, `min_count`); a block that is itself a repetition of something shorter than 32 pieces is left to the other checks;
- **compression**: the last 16,384 bytes deflate below 0.10 of their size (`compression_window`, `min_compression_ratio`), checked every 2 KB; this catches near loops whose copies differ in a number or a word;
- **runs**: one piece 64 times in a row (`max_run`) or one character 400 times (`max_char_run`), the single-character runs substring checks miss.

The names are the ones the ling-engine server rule takes (`repetition_detection`, after vLLM's `RepetitionDetectionParams`), so one configuration can drive both; the server counts tokens.

**Measured** (`ling-rs/loop-guard/tests/corpus.rs`, on 2026-10-20): the corpus is every text the model wrote that is saved here, 203,009 distinct texts from 1,976 files (the exec logs and rollouts of every SWE-bench night, and `~/.mightling/sessions`): 129,385 shell commands, 38,441 tool inputs, 35,163 messages, 20 reasoning items, 77 MB. **False positives: 0 of 203,009** at the defaults. The margins: the lowest deflate ratio of any 8 KB window of a long normal text was 0.17 (320 texts of 8 KB or more), of the repository's own source files 0.19 to 0.24, against the 0.10 threshold; a matrix literal had three identical rows, never four; the longest run of one piece was 40. Tighter settings flagged normal output: `min_count` 3 flagged five (matrix rows in sympy tests), `max_run` 32 flagged two (a 40-digit Decimal test). True positives: one in seven of the same texts made to loop (the last 100, 400, 1,600 or 6,000 bytes repeated, exactly or with a counter in each copy): 1,037 of 1,037 stopped. An exact loop of 400 bytes or more is stopped after 1.2 to 18 KB of repetition, a short or near loop when the compression window fills, after at most about 16 KB (3,000 to 6,500 pieces).

Rebuild the corpus and rerun both measurements with:

```bash
.venv/bin/python scripts/loop_guard_corpus.py /tmp/corpus.jsonl
LOOP_GUARD_CORPUS=/tmp/corpus.jsonl cargo test --release -p ling-loop-guard --test corpus -- --ignored --nocapture
```

`LOOP_GUARD_THRESHOLDS=2048,32,4,64,400,16384,0.10` (the seven thresholds in order) tries other values.

## What happens on a stop

`ling-rs/src/loop_guard.rs` is a `ModelRequestContributor` and `ModelResponseInterceptor`, Codex's own extension points, installed from `cave::install`, so it costs no patch bytes (the series is at 44,952 of its 45,000 cap). The interceptor wraps each response stream:

1. The response is cut: the upstream stream is dropped, which closes the connection and stops the server generating; the watched item is finished with its text up to where the loop began (the first copy kept).
2. Tool calls are held until the response completes. A response the server ends as incomplete (the cap, or ling-engine's repetition stop) has its tool calls dropped, so a call cut short is never run. Codex would turn `response.incomplete` into a retryable stream error and send the same request up to five times more; the guard turns it into a stop instead.
3. The first stop adds a developer message ("You were repeating yourself … answer now with what you have", or the length variant) and completes the response with `end_turn: false`, so Codex sends the next request in the same turn. SGLang renders the developer message as a system message, which the chat-template patch turns into a `<system-reminder>`.
4. If the next response of that thread is stopped too, the turn ends with a plain assistant message: "I stopped this answer because I kept repeating myself …". A response that completes normally resets this.

That is "at most once per turn" read as at most once per chain of responses: the extension API gives a request its thread id but not its turn, and a long agentic turn that loops twice, an hour apart, deserves two rescues. Two stops in a row always end the turn.

`tests/test_loop_guard_binary.py` runs the built binary (`MIGHTLING_BIN=…`, skipped for a `ling` without the guard) against a stand-in Responses server and checks the whole path through Codex: a looping reasoning is cut, the server sees the client hang up, and the next request carries the nudge and the reasoning before its loop; an incomplete response with a cut tool call is neither retried nor run; two stops in a row end the turn; `exec` with nothing switched on sends no `ling_*` metadata. All four passed on 2026-10-20.

Each stop is a line in `$CODEX_HOME/loop-guard/events.jsonl` (time, thread, surface, check, action, pieces streamed, items dropped). `ling loop-guard` prints each surface's settings and the counts by surface, check and action; a SWE-bench run counts them per instance (`loop_guard` in its state, a "Loop guard" line in its report).

## Settings

Defaults per surface as in the table. In the Dreamference TOML file, a `[loop_guard]` table: `<surface>_max_output_tokens` (0 is no cap), `<surface>_guard` (true or false), `sampling = "nonthinking"`, and the seven thresholds by name. For one process, overriding the file: `DREAMFERENCE_MIGHTLING_LOOP_GUARD=on|off`, `DREAMFERENCE_MIGHTLING_OUTPUT_BUDGET=<tokens>|0`, `DREAMFERENCE_MIGHTLING_SAMPLING=nonthinking|default`.

## Benchmark arms

SWE-bench and Night Shift run `ling exec`, which is left alone, so their numbers stay comparable. `ling-admin swe-bench run` takes the three layers as arms, recorded in the manifest (`loop_guard`, `output_budget`, `sampling`) and compared by `report --against`: `--loop-guard`, `--output-budget TOKENS`, `--sampling nonthinking`. A run refuses to start rather than silently run an arm as the default: with a `ling` that does not read the variables, or, for the cap and the sampling arm, with a gate that predates them (no `loop_guard` in its probe; a replica lane whose gate predates them is skipped).

## Deployment

- The launcher half arrives with `ling-admin codex build` (never during a night).
- The gate half arrives with the next `ling-admin server start`, which copies `model_gate_guard.py` beside the service. Until then the caps are named in the metadata and ignored, and the detector alone works.
- The phone surface needs `ling web`'s scratch folders for the bridges' `chat` prompt to be named `ask/chat-<hex>` (the bridges' change, part C of the chat fixes); before it, phone threads count as Ask.

## Limits

- A loop inside a buffered tool call is ended by the cap only, after the cap's worth of tokens.
- Tool-call arguments are not watched: a streamed `apply_patch` with repeated lines is normal code.
- The cap is per response, not per turn; the bridges' wall-clock budget bounds a whole answer.
- The detector's margins rest on this machine's outputs, mostly code; prose-heavy Ask answers are a smaller share of the corpus (35,163 messages).
