# The egress audit

Developer notes behind the egress-audit line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_EGRESS.md` §10. The channels it found are listed in [launcher.md](launcher.md#channels-to-the-vendor-closed).

`ling-admin audit egress` is the trace that found the upstream vendor's channels, as a command (`dreamference/audit/`). Since 2026-10-10 a client runs the same audit as `ling audit egress` from the launcher alone (`ling-rs/src/audit.rs`: the Windows ETW recorder, a Linux strace recorder, one verdict; spec §12), with the Python parser's rules ported and its recorded traces copied to `ling-rs/audit-fixtures/`; the interface, app, web and file-index variants stay here. It runs one real `ling exec` under `strace -f -e trace=connect,sendto,sendmsg,sendmmsg,execve` in a throwaway repository and `CODEX_HOME`, and passes only if every IP destination is an allowlisted loopback port (the model server, Gmail on 8767, SearXNG on 8888), no DNS query was sent and no git command reached a network; exit 0, 1 or 2 (the trace itself failed, which is never a pass).

Two things the parser had to learn from a recording: glibc sends lookups with `sendmmsg` (untraced, a query leaves no name), and a connect to `127.0.0.53:53` is loopback yet still a query that leaves the machine.

Modes:

- `--tui` traces the full-screen interface, which starts things `exec` never does. `ling-admin codex build` runs both the `exec` and the `--tui` trace after installing a new binary (`--no-audit` skips it; without a model server it says so and does not wait).
- `--app` checks the desktop app; see [desktop.md](desktop.md).
- `--docs` proves the local file index opens no socket; see [local-file-index.md](local-file-index.md).

Re-run it after any Codex bump. The airlock (Phase 2) is not built.

**Declared exceptions.** The phone messengers talk to outside services by design, outside any traced session, and only once turned on. Every report ends with one line per bridge that is on (`EgressAudit.declared_exceptions`): the system unit `mightling-signal.service` (signal-cli to Signal's servers), the user unit `mightling-chat.service` with a Telegram token (the Bot API), and the user socket `mightling-matrix-proxy.socket` (the homeserver offered to the tailnet, with push on or off). With every bridge off it adds nothing, and they never change the verdict. See [messengers.md](messengers.md).

Every unattended caller of `ling` (Night Shift, the egress audit, SWE-bench, the Codex test runner) names the model server in `DREAMFERENCE_VLLM_HOST`, because a node browse is a multicast DNS query and the audit counts one as a failure.

The audit does not cover a proxy variable left in the user's shell. `ling` and `ling-admin` exempt loopback and the model server from it through `NO_PROXY` instead (spec §11; [launcher.md](launcher.md#what-the-launcher-does)).
