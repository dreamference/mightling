# Tests never touch the real machine

Developer notes behind the first hard rule in `AGENTS.md`.

## The user's files

`tests/conftest.py` gives every test its own `HOME` and a scratch Onyx `.env`, and makes an unmocked `OnyxRunner._recreate_service` fail the test. Before it existed, the suite rewrote the real `~/.continue/config.json` on every run, and a new `configure()` step once rewrote the real Onyx `.env` and recreated the live nginx container.

HOME alone does not reach paths a module computed at import (`VLLM_CACHE_HOME`, the ling install paths), so the same fixture re-points every string/`Path` attribute of every `dreamference` module that lies under the real home (outside the checkout) into the test's home. Before that, a test stamped the real compile-signature file and the next real `server start` discarded the torch.compile cache.

## Running containers

Until 2026-09-29 every offline run of the `configure` tests recreated the live Gmail sidecar, joined SearXNG and the speech-to-text sidecar to Onyx's network, copied test logos into the web server, rewrote its bundle and patched the API server through `docker exec`; once HOME was isolated, the recreated Gmail sidecar carried a temp folder and test secret and Gmail search answered "unauthorised".

conftest now fails any real `docker` command that changes something (reads like `docker info` stay allowed) and stubs those `OnyxRunner`/UI-patcher methods; a test of one of them restores it from the `REAL_*` names in conftest. A test needing a real path must opt in explicitly.

## The same rule elsewhere

- Tests never write `/etc/avahi` or run sudo (conftest points `NodeServiceFile.service_path` at scratch; [node.md](node.md)).
- Night Shift's tests use a scripted stand-in for `ling` and never create a real scope or unit ([night-shift.md](night-shift.md)).
- SWE-bench's tests never start a container or touch the real cache ([swe-bench.md](swe-bench.md)).
- `ling-docs` tests never run with the real `HOME` ([local-file-index.md](local-file-index.md)).
- The vLLM launcher is tested against test-only recipes (`vllm_recipes` in `tests/conftest.py`; [models-and-engines.md](models-and-engines.md)).
