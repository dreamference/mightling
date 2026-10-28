# ask-evals

The phone-chat assistant's own regression suite: [specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md](../../specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md) §5 (design), §9 (Phase 0) and §11 (what was built). It drives the real `ling` against the model server, so it is not part of the test suite. Its harness is tested in `tests/test_ask_evals.py` with a stand-in `ling` and no model.

```bash
.venv/bin/python scripts/ask_evals/run.py --plan                       # the scenarios; runs nothing
.venv/bin/python scripts/ask_evals/run.py --reps 1,2,3                 # every scenario, three times
.venv/bin/python scripts/ask_evals/run.py --only s6-,s7- --reps 1      # some families
.venv/bin/python scripts/ask_evals/run.py --rescore ~/.cache/dreamference/ask-evals/runs/<stamp>
```

- **Offline by construction.** Each session runs in `bwrap --unshare-net`: loopback only. The stand-in SearXNG and proxy (`askevals/standins.py`) listen there, and the model comes in through a Unix socket in `$XDG_RUNTIME_DIR`, joined to `127.0.0.1:<--model-port>` outside (`askevals/forward.py`). Codex's own sandbox (`workspace-write`) runs nested inside, as in production.
- **One session per scenario run**, with a fresh `CODEX_HOME` and scratch folder under `~/.cache/dreamference/ask-evals/runs/`, never `/tmp`. The first turn is `ling exec --json`, each later one `ling exec … resume --last`. The prompt is `ask` (`DREAMFERENCE_MIGHTLING_PROMPT`).
- **The harness's own bound:** a turn that prints nothing for 180 s, or runs past its limit (600 s by default), is cut and counted.
- **Scenarios** are `scenarios/<id>.json`, all synthetic. A scenario holds:
  - `turns` (text, images drawn by `askevals/images.py`, an optional `faketime` offset);
  - `web` (what the stand-ins answer, faults included);
  - `expect` (the checks in `askevals/checks.py`).

  A turn's text is what the bridge would compose: an image whose text Tesseract read arrives as `[Screenshot text: …]`.
- **Scores are rules**, never a model: tool calls, seconds, search-engine scraping (by command and by the proxy's log), sleeps, fetches per URL, abstain/ask/answer, required and forbidden words, reply language, and repetition loops (`askevals/metrics.py`). A scenario fails when it fails in two of three repetitions.
- **The night slot.** `ASK_EVALS=1` in `scripts/swe_bench_night1.sh` runs the suite before the arms, with `--model-port 18000 --hold-gate "night N, assistant evals"`. It is off by default (the owner's D2).
- **Needs:** `bwrap` with the AppArmor profile (`ling-admin host setup`), Pillow (the venv has it), and, for the stale-thread scenario, `faketime` (`apt install faketime`). Without `faketime` that scenario is reported as skipped, never passed.
