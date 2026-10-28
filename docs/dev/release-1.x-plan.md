# Release plan for the rest of 1.x

Written 2026-10-28 (UTC) from `origin/main` at `91dd7496`. Order: 1.7.3, then 1.8.0, 1.8.x, 1.9.0 (the Ragon rename, [RENAME_RAGON](../../specs/DREAMFERENCE_RENAME_RAGON.md)), then 2.0 ([2_0](../../specs/DREAMFERENCE_2_0.md)), which is paused until 1.x is complete. 1.7.2 is out (2026-10-28 11:43). Owner column: a running agent by role, `nobody` (unowned, can start now), or `owner` (only the owner can act). Dates are targets, in UTC.

**Standing blocker.** The weekly usage limit was hit on 2026-10-28 (about 12:07) and resets 2026-11-01 05:00. Five agents were stopped; on 2026-10-28 15:00 the coordinator's status listed the P4b/P4c, tree-rows and vision agents as waiting or locked on the GPU hosts. Every "agent" row below is at risk of slipping to that reset if its agent is among them; re-check the rows on 11-01.

## Releases

| Item | Owner | Blocker | Date |
|---|---|---|---|
| 1.7.3: installer fix `9b808e84` + engine pin `72c2a6a`; SGLang stays default | release-prep agent (prepared) | night 18 (engine `72c2a6a` against SGLang) must be clean; if not, ship the installer fix alone | 10-29 ~11:00 |
| 1.8.0: ling-serve as the default engine (`QWEN38_DEFAULT_ENGINE = ENGINE_LING`) | nobody for the cut; see the bar below | the bar, and the owner's OK | ~11-11 to 11-13 |
| 1.8.x: per-conversation fairness (fair pairing) | fair-pairing agent | needs the P4 paged memory and the 1.8.0 engine pin | after 1.8.0; none dated |
| 1.9.0: Ragon rename | rename-script agent (branch) | 1.x complete; the script's checks (leftover grep, suite, egress audit, throwaway-HOME migration, mixed-version mDNS) green | run after 11-18 |
| 2.0 | paused by the owner | 1.9.0 | not scheduled |

The spec gives 1.9.0 a target of 11-18 and also "about two days after 1.x completes"; the owner's instruction is to run the script after 11-18. Treat 11-18 as the earliest start, not the release date, and confirm.

## The 1.8.0 bar and what stands in front of it

Bar (all four, then the owner's OK): combined rate of 4 conversations at or above 34.6 tok/s; identical output; a no-loss SWE-bench night; paged memory P4b, then P4c, then P4d.

| Item | Owner | Blocker | Date |
|---|---|---|---|
| Paged KV: P4b merge (static map is P4a, merged) | P4b merge agent (second-puffin) | GPU lock on the dev host | 10-29 night soak |
| P4c, then P4d (shared KV and recurrent state pool, cross-request prefix reuse) | P4c agent | P4b merged; the usage-limit stop | P4c ~11-02 to 11-05, P4d ~11-06 to 11-09 (estimate; no agent has dated them) |
| 4-conversation rate and identical-output measurement | nobody | P4c/P4d; second-puffin night of 10-30 (baselines) is armed, do not re-arm | after P4d |
| No-loss night on the final pin | nobody | final engine commit | one night after the pin, ~11-10 |
| Engine pin moves in the Mightling repository (from `72c2a6a` / the 1.7.2 image) | nobody | the final engine commit; a new image on GHCR | with the bar |
| `ling-serve --plan-only` (engine `d4adbd5`) missing from the pinned image: the pre-load check falls back to the 0.50 arena | nobody (comes with the new pin) | the pin; confirm `check_host_safety()` reads the plan, and test it | with the pin |
| `LING_ENGINE_KV_PAGES_SINCE` may need to move to the first commit with `--kv-pages` plus the pool | nobody | the pin | with the pin |
| Vision libraries missing from the image; image parts become `[image]` | vision agent | usage-limit stop; image rebuild | before the bar night |
| LoRA absent from the ling recipe (ADAPTERS §9, Phase 2: unmerged LoRA in the engine) | vision/LoRA agent (same agent) | engine LoRA kernels; usage-limit stop | before the bar night |
| `X-Ling-Share-Scope` header (prefix sharing scope) | header agent | P4d (cross-request reuse) for any effect | with P4d |
| Engine raw tool call (`</parameter></function></tool_call>` as text, nights 12 and 15) | nobody | Fixed: `fix/toolcall-text`, parse only, in `b8e9451` and later. Remaining: a regression check on the final pin (add the two django cases to the pre-1.8.0 night) | with the bar night |
| Engine `/model_info` answered by ling-serve (the clients that read it) | nobody | confirm; listed as missing on 10-26 | with the pin |
| Release notes `DREAMFERENCE_RELEASE_1.7.3.md` and `_1.8.0.md`; `docs/dev/models-and-engines.md` and `host-safety.md` for the 4-session default (about 60 GB at 2 sessions today) | nobody | content depends on the measured numbers | draft 1.8.0 notes in parallel from 11-05 |
| Owner OK to cut 1.8.0 | owner | the bar | 11-11 to 11-13 |

## Other items 1.x still owes

| Item | Owner | Blocker | Date |
|---|---|---|---|
| Issue 3, "Release dry run failing" (open) | nobody | Cause was two Multilingual build tests that read the runner's free disk; fixed by `21d46765` on main. The dry run at 14:59 was still running. Confirm the next run closes the issue; also one `Event loop is closed` warning in the mypy ratchet output, not a failure | 10-29 |
| Defect list of 2026-10-19 (`specs/README.md`): seven code or note defects, none fixed (chat status ignores arguments; `audit egress --docs` ignores `--prompt`/`--json`; audit JSON name in the help; `compaction.rs` header; `FORWARDED_ENV` in `docs_index.rs`; `docs/dev/node.md` path; `--web` missing from AGENTS.md's list) | nobody | none; small, one branch | before 1.8.0 |
| 1.7.2 desktop visual checks on the AMD box (8 checks at 100 % and 150 % scaling) and the icon question | owner | at the screen | owner's daily batch, 09:00 |
| Windows preview is unsigned (patch `0024`); signing reopened | owner | company enrolment for code signing, not now | after 2.0 planning |
| Signal test (remove and unlink first, `ling web start`) | owner | owner's time | owner's daily batch |
| Two new GB10 machines: first boot, enrolment | owner | on site | 11-01 ~16:00 |
| New Mac mini M5 Pro (macOS runner, QA) | owner (first boot), then QA agent | arrives | ~11-11 |
| Ethernet cables and a UPS for the always-on nodes | owner | purchase | next week |
| Onyx backup folder and `onyx_*` volumes deleted | coordinator | date | 11-26 |
| GitHub Support purge request (old commits; engine `403d083`) | coordinator drafts, owner sends | owner | owner's daily batch |
| Ragon: name check, kept-on-purpose list, three asset sets, migration, mixed-version mDNS | rename-script agent | 1.9.0 rule: nothing else in that release | 11-18 and after |
| Night 18 (engine `72c2a6a` against SGLang) and night 15b (third-puffin) | armed, 10-28 21:00 | none | results 10-29 morning |
| Second-puffin nights 10-29 (P4 soak) and 10-30 (baselines) | armed, do not re-arm | none | 10-29, 10-30 |
| Nights 19 to 21, days 2 to 4, `flash-next`, `night16` held in the queue | coordinator | held; decide which are needed for the 1.8.0 bar and release the rest after 1.9.0 | decide 10-30 |

## Not 1.x (paused; do not start)

AMD/Windows port of the engine, the Metal port, several drafters, the Discord bridge, web search and the local search index, Matrix on the overlay, the advisor node, the compatibility mode beyond what 1.7.2 shipped, the sync spec's later phases. All are in [2_0](../../specs/DREAMFERENCE_2_0.md) or marked on hold.

## Keeping this current

The release manager updates the tables when a row changes and the coordinator's daily owner batch (09:00) carries the `owner` rows. A row leaves the table when its release is cut.
