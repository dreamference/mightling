# Host safety and the sandbox prerequisite

Developer notes behind the host-safety rules in `AGENTS.md`. Specs: `specs/DREAMFERENCE_INFERENCE.md`, `specs/DREAMFERENCE_SETUP.md` §3.3.

## The host-safety subsystem

`vllm_server/vllm_server_manager.py` + `psi_watchdog.py`. This is the least obvious part of the codebase and exists because unified memory means a model load can freeze the whole host rather than just OOM the container. Two layers:

- `VLLMServerManager.check_host_safety()` runs *before* a load: it inspects swap, `sysctl` values, and whether `earlyoom`/`systemd-oomd` is present and correctly configured, and aborts with an explanation rather than risking a lockup. `HostSafetySetup` (`vllm_server/host_safety_setup.py`, `ling-admin host setup`) applies what the check only prints; see [install-modes.md](install-modes.md#hostsafetysetup).
- `MemoryPressureWatchdog` runs *during* a load: it samples `/proc/pressure/memory` (PSI) on a thread, resolves the container's cgroup, and kills it if `avg10` spikes or `avg60` stays above threshold for the trip duration. The kill has three paths, chosen by what the host can still do:
  1. direct `SIGKILL` to the cgroup's PIDs (fastest, but needs root, which `ling-admin` normally is not: `_probe_direct_kill` settles that with signal 0 while the host is healthy);
  2. a kill request over dockerd's unix socket (one connect and one write, no fork: the path that actually runs);
  3. the `docker` CLI last, because forking a Go binary is the work least likely to be scheduled during a reclaim livelock.

  `_kill()` returns whether anything died, and the trip callback only fires when something did.

Host RAM exhaustion and GPU VRAM exhaustion are indistinguishable on GB10; the watchdog exists to make that distinction. Preserve both layers when touching `start_server()`. The SGLang engine runs from the same `docker run` prefix (`_docker_run_prefix`), so the layer is engine-independent.

Other memory-safety rules elsewhere: `server start` stops the code index's scopes before its pre-flight ([code-index.md](code-index.md)), the context engine's indexing is bounded because it runs beside a resident model server ([context-engine.md](context-engine.md)), and the diffusion sidecar starts before vLLM when it is switched on ([diffusion.md](diffusion.md)).

## bubblewrap needs an AppArmor profile, and this machine now has one

`kernel.apparmor_restrict_unprivileged_userns` is 1, so without a profile an unconfined process cannot create a user namespace: from a systemd user service (measured 2026-10-02) `bwrap` fails with "setting up uid map: Permission denied" and `ling sandbox -- true` with "bwrap: loopback: Failed RTM_NEWADDR".

Shells under PyCharm carry `snap.pycharm.pycharm (complain)`, where it is allowed, and a `--scope` started from one inherits that, which is why Night Shift, the code index and Codex's sandbox have all passed when run by hand. A unit, the Night Shift timer and a job sent over SSH do not inherit it: check `cat /proc/self/attr/current` before trusting a sandbox result, and see MIGHTLING_NODE §18.7.

The fix needs root: `ling-admin host setup` installs `/etc/apparmor.d/puffin-bwrap` (a profile granting `userns` to `/usr/bin/bwrap` alone, the shape of Ubuntu's own sandbox profiles; the file keeps its old name) after a sudo prompt, and every `ling-admin` run checks the sandbox from a throwaway user unit and asks to fix it or to turn Night Shift off (`dreamference/vllm_server/sandbox_prerequisite.py`, SETUP §3.3).

The profile was loaded on this machine on 2026-10-03: from a throwaway systemd user unit, `bwrap --unshare-user --unshare-net true` and `ling sandbox -- true` both succeed, and `ling-admin host check` reports nothing to do. Another GB10 still needs `host setup` once.

The desktop app's Chromium sandbox meets the same rule; see [desktop.md](desktop.md).
