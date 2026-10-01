# Puffin Egress — audit and airlock

**Status:** proposed. Nothing in this spec is implemented yet. The mechanism in §4.2 was checked on this host on 2026-09-29 (details in §4.2), but not built.
**Superseded in part (2026-10-01):** the airlock's switch is now the `on` level of `/airgapped` ([PUFFIN_AIRGAPPED §5.4](./DREAMFERENCE_PUFFIN_AIRGAPPED.md)), not `puffin --airlock`. The mechanism (§4.1, §4.2), the ledger (§4.4) and the audit (§3) stand; the surface (§2), the allowlist (§4.3) and §5 are read through that spec, which allows only the model server at `on`.
**Target:** the `puffin` terminal agent. `puffin-admin` runs the audit.
**Builds on:**
- the network-channel work of patches `0013` and `0015` ([PUFFIN_CODEX](./DREAMFERENCE_PUFFIN_CODEX.md));
- the launcher's forced `chatgpt_base_url`;
- the trace scripts used on 2026-09-29 to find those channels (`netproof.sh`, `tuitrace.py`);
- `bwrap`, which Codex's own sandbox already uses.

---

## 1. Goal

Make "your code stays on your machine" something a user can check, not a promise.

- **Phase 1, `puffin-admin audit egress`:** run one real `puffin` session under a tracer and print every network destination and every process it started, with a verdict.
- **Phase 2, `puffin --airlock`:** run `puffin` where only an allowlist of local services is reachable. Every other attempt fails and is written to a ledger.

**Why it matters.**
- **Developers care:** 81% of developers report security and privacy concerns about AI coding agents.
- **Toggles are not enough:** at least one commercial agent was found uploading whole repositories over a channel that ignored its privacy toggle.
- **Our own record:** tracing puffin found five channels to OpenAI or GitHub. One needed a ChatGPT login; four needed none: Statsig metrics, featured plugins, a startup `git ls-remote` and the TUI announcement tip. All five are closed (`0013`, `0015`), and traced `exec` and TUI sessions now reach only `127.0.0.1:8000` (vLLM) and `127.0.0.1:8767` (the Gmail service). Phase 1 makes that trace a command anyone can rerun; Phase 2 makes a regression fail locally instead of leaking.

**Non-goals:**
- **Calling it air-gapped.** Web search and `puffin-fetch` reach the internet by design (§5), and the docs must never say "air-gapped" for a mode that allows them. The command `/airgapped` keeps to this: only its `on` level, which allows neither, carries the word ([PUFFIN_AIRGAPPED §1](./DREAMFERENCE_PUFFIN_AIRGAPPED.md)).
- **Inbound exposure.** The model server listening on `0.0.0.0:8000` is accepted by design: Puffin assumes the local network is trusted ([README](./README.md), "Accepted by design").
- **Containers.** The web chat's own egress (Onyx, SearXNG, the sidecars) is out of scope. Onyx's telemetry is handled by `configure` ([ONYX](./DREAMFERENCE_ONYX.md)).

---

## 2. Surface

This spec adds no slash command of its own. (When it was written the patch budget was reserved for `/night`, which is now built; the airlock's switch has since moved to `/airgapped`, see the note at the top.)

| Command | Phase | Effect |
|---|---|---|
| `puffin-admin audit egress [--tui] [--prompt "…"] [--json]` | 1 | Traces one session; prints destinations, processes, verdict; exit 0 on pass, 1 on an unexpected destination, 2 when the trace itself failed. |
| `puffin --airlock …` or `airlock = true` in `$CODEX_HOME/config.toml` | 2 | Runs `puffin` in the airlock. The launcher handles the flag before Codex parses its arguments, like `puffin app`, so it needs no patch. |
| `puffin airlock log [--since 1d]` | 2 | Prints the ledger (§4.4). |

**`codex build` runs the audit.** After a build with a new Codex release, `puffin-admin codex build` runs `audit egress` and prints the verdict. A failing verdict does not undo the build, but it is shown in red with the offending destinations. [PUFFIN_CODEX §6](./DREAMFERENCE_PUFFIN_CODEX.md) says to re-run the trace after every Codex bump; this makes that automatic.

---

## 3. Phase 1 — the audit

### 3.1. Method

This productises the 2026-09-29 procedure.
1. **Setup.** A throwaway git repository with one committed file, and a throwaway `CODEX_HOME`, so that no login, history or config of the user's influences the result, and none is touched.
2. **Exec session.** `strace -f -qq -e trace=connect,sendto,sendmsg,execve -s 256` around `puffin exec --skip-git-repo-check "<prompt>"`, with the default prompt `Reply with exactly: pong`.
3. **TUI session** (`--tui`). The same trace around the interactive TUI, driven on a pseudo-terminal: accept the trust prompt, send the prompt, wait for the reply, quit. The TUI-only announcement fetch was found this way and is invisible to `exec`.
4. **Destinations.** Every `sin_addr`/`sin6_addr` and port from `connect`, `sendto` and `sendmsg`, counted.
5. **DNS.** Every name in UDP payloads to port 53.
6. **Unix sockets.** Every `sun_path`.
7. **Processes.** Every `execve`, with git subcommands that touch a network (`ls-remote`, `fetch`, `clone`, `pull`, `remote-https`) listed separately.

### 3.2. Verdict

- **Pass:** every IP destination is loopback and on the allowlist (§4.3), no DNS query was sent, and no networked git subcommand ran.
- **Fail:** anything else, listed first.
- **Trace failed:** the session did not produce a reply, or `strace` could not attach. This is not a pass.

`--json` writes the full result to `~/.puffin/audit/<timestamp>.json` as well: destinations, DNS names, processes, verdict, the `puffin --version` output, and the Codex tag and patch hashes from the build stamp. Two audits can then be compared across builds.

### 3.3. Requirements

- **Tracer:** `strace`, installed here. Tracing one's own child needs no privilege under the default Yama `ptrace_scope` of 1.
- **Model server:** the session needs a model server; without one the audit reports "trace failed".

---

## 4. Phase 2 — the airlock

### 4.1. Model

`puffin` runs in its own network namespace, where the only interface is `lo`. Every outbound connection fails with `ENETUNREACH` at the kernel. On the namespace's `lo`, the launcher listens on each allowlisted port and relays each connection to the same port on the host's loopback. The allowed services therefore see ordinary loopback clients, and nothing else is routable.

This is the stronger form of what the patches do. The patches close channels one at a time at their call sites. The airlock needs no knowledge of the channels: a new one in a future Codex release fails closed.

### 4.2. Mechanism

**Starting the airlock.** `puffin --airlock` makes the launcher do the following before Codex parses its arguments:
1. **Relay directory.** It creates `$XDG_RUNTIME_DIR/puffin-airlock/<pid>/`, mode `0700`.
   - The path must stay short: unix socket paths are limited to 108 bytes, and a path under the scratch directory used for the checks failed with `AF_UNIX path too long`.
2. **Host-side relay.** It starts a relay, in-process on a thread of the launcher that stays outside the namespace. For each allowlisted port, the relay:
   - listens on a unix socket `<port>.sock` in that directory;
   - forwards each connection to `127.0.0.1:<port>` on the host.
3. **Re-exec.** It re-executes itself under `bwrap --unshare-net --dev-bind / / --bind <dir> <dir> --die-with-parent`, with an environment marker so the child knows it is inside.
4. **Inside the namespace,** the child launcher:
   - listens on `127.0.0.1:<port>` for each allowlisted port;
   - relays to the matching unix socket;
   - then continues into Codex exactly as today.

**Verified on this host (2026-09-29),** as an unprivileged user:
- `bwrap --unshare-net` works despite `kernel.apparmor_restrict_unprivileged_userns = 1`, because bwrap's AppArmor profile permits it;
- inside, `lo` is the only interface;
- an outbound connect fails with `[Errno 101] Network is unreachable`;
- a unix socket bind-mounted from the host is reachable;
- a listener on `127.0.0.1:8000` can be opened;
- a nested `bwrap --unshare-net` inside succeeds, which matters because Codex's own command sandbox runs bwrap inside ours.

**Not yet verified:**
- **Codex's sandbox inside the airlock.** It also uses Landlock and seccomp. Running its full command sandbox inside the airlock must be checked end to end, not only a bare nested bwrap.
- **`pasta`,** the alternative that would forward chosen ports without a relay, is not installed here. It would add a system dependency, so the relay stays the design unless it proves unworkable.

### 4.3. Allowlist

The allowlist is `airlock.allow` in `$CODEX_HOME/config.toml`, written by the launcher, with these defaults:

| Port | Service | Why |
|---|---|---|
| vLLM port (from the resolved `vllm_host`) | model server | The agent's model. |
| `8767` | Gmail search service | `puffin-admin gmail`. |
| `8888` | SearXNG | `puffin-search` (§5). |

**The diffusion endpoint (`8001`) is not allowed:** the agent does not use it. Unix sockets on the host are unaffected by a network namespace, which is intended: the Docker socket, the D-Bus session and the like stay reachable. If one of them should be closed too, that belongs to Codex's command sandbox, not the airlock.

### 4.4. Ledger

A connection that fails at the kernel leaves no trace by itself. The ledger is built from two sources:
- **DNS.** Inside the namespace, `/etc/resolv.conf` is bind-mounted to point at `127.0.0.1`. The launcher runs a stub resolver there that answers every query `REFUSED` and records the name, the time and the asking process (from `/proc/net/udp` inode to pid). Almost every real egress starts with a lookup, so this catches nearly all attempts, with the name the program wanted.
- **Direct-IP attempts** skip DNS. With `--airlock=trace`, the airlocked process tree also runs under `strace -f -e trace=connect -e status=failed`, and failed non-loopback connects are added to the ledger. This is off by default for its overhead.

The ledger is appended to `~/.puffin/airlock/ledger.jsonl`:

```json
{"at": "…", "session": "…", "kind": "dns", "name": "ab.chatgpt.com", "pid": 4242, "exe": "/home/…/puffin"}
```

`puffin airlock log` prints it grouped by name and executable. An empty ledger over a week of use is the record the goal asks for.

---

## 5. Search and fetch, honestly

- **Search goes out by design:** `puffin-search` reaches SearXNG on `127.0.0.1:8888`, and SearXNG queries the upstream engines from outside the airlock. The ledger cannot see those queries. The airlock's own docs and the agent's prompt must say plainly that searches leave the machine through SearXNG.
- **`puffin-fetch`** connects directly to the URL (or through `https_proxy` when set), so it fails inside the airlock. It fails with its existing error path, and the ledger records the lookup.

**The prompt must match the mode.** The launcher writes the model catalog, including `WEB_ACCESS_INSTRUCTIONS`, at every start. In airlock mode it writes a variant:
- search is available, and its queries leave the machine;
- `fetch` is not available.

Without that, the model would be told it can fetch pages, and would keep trying.

**Planned:** fetch through a local, logged proxy that asks for confirmation. That is out of scope here.

---

## 6. Where it is used

- **Opt-in.** Interactive use is opt-in (`--airlock` or the config key) until a release has run a week of the maintainer's own sessions with an empty ledger and nothing broken.
- **Night Shift** runs use the airlock by default once it exists ([NIGHT_SHIFT §6](./DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md)): no one is watching those sessions.
- **`puffin-admin drafter tune`** runs its training process in the airlock ([SELF_SPEEDING §7](./DREAMFERENCE_SELF_SPEEDING.md)).

---

## 7. Tests

- **Phase 1:**
  - unit tests of the trace parser on recorded `strace` output: IPv4, IPv6, DNS payloads, unix paths, `execve` lines, including the 2026-09-29 traces with the five channels, which must fail the verdict;
  - a live test (skips without a server): the audit passes on the current build.
- **Phase 2:**
  - launcher unit tests for the allowlist, the socket-path length check and the resolver stub;
  - a live test in which an airlocked `puffin exec` session answers through vLLM;
  - inside the same session, a `curl https://example.com` run by the agent fails, and `example.com` appears in the ledger;
  - `puffin-admin gmail status` and `puffin-search` still work inside it;
  - Codex's command sandbox still starts inside the airlock (§4.2, not yet verified).

---

## 8. Acceptance criteria

- **Audit:**
  - `audit egress` on the current build prints only allowlisted loopback destinations, no DNS and no networked git, and exits 0;
  - on a build without `0015` it fails and names all four channels.
- **Airlock:**
  - an airlocked TUI session works as usual: model, slash commands, Gmail, search;
  - removing `0015` from the build and repeating the session leaves the Statsig, plugin and announcement lookups in the ledger, and sends nothing out.
- **Docs:** nothing describes a mode that allows search or fetch as air-gapped.

---

## 9. Open questions

- **`puffin app`.** The desktop app launches its own process tree. Should the airlock also cover it, or is it only for the terminal agent?
- **MCP servers.** A user-configured MCP server that needs the network fails inside the airlock. Should the allowlist accept per-server exceptions (by port), or should such servers be run outside and reached over a unix socket?
