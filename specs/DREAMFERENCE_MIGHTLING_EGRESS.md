# Mightling Egress — audit and airlock

**Status:** Phase 1 (the audit) implemented: `exec` sessions on 2026-10-01, the full-screen interface (`--tui`) and the audit after `ling-admin codex build` on 2026-10-02: `dreamference/audit/`, `ling-admin audit egress`; §10 records what was built and where it differs. Phase 2 (the airlock) is not built; its mechanism in §4.2 was checked on this host on 2026-09-29 (details in §4.2).
**Superseded in part (2026-10-01):** the airlock's switch is now the `on` level of `/airgapped` ([MIGHTLING_AIRGAPPED §5.4](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)), not `ling --airlock`. The mechanism (§4.1, §4.2), the ledger (§4.4) and the audit (§3) stand; the surface (§2), the allowlist (§4.3) and §5 are read through that spec, which allows only the model server at `on`.
**Target:** the `ling` terminal agent. `ling-admin` runs the audit.
**Builds on:**
- the network-channel work of patches `0013` and `0015` ([MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md));
- the launcher's forced `chatgpt_base_url`;
- the trace scripts used on 2026-09-29 to find those channels (`netproof.sh`, `tuitrace.py`);
- `bwrap`, which Codex's own sandbox already uses.

---

## 1. Goal

Make "your code stays on your machine" something a user can check, not a promise.

- **Phase 1, `ling-admin audit egress`:** run one real `ling` session under a tracer and print every network destination and every process it started, with a verdict.
- **Phase 2, `ling --airlock`:** run `ling` where only an allowlist of local services is reachable. Every other attempt fails and is written to a ledger.

**Why it matters.**
- **Developers care:** 81% of developers report security and privacy concerns about AI coding agents.
- **Toggles are not enough:** at least one commercial agent was found uploading whole repositories over a channel that ignored its privacy toggle.
- **Our own record:** tracing ling found five channels to the upstream vendor or GitHub. One needed a ChatGPT login; four needed none: Statsig metrics, featured plugins, a startup `git ls-remote` and the TUI announcement tip. All five are closed (`0013`, `0015`), and traced `exec` and TUI sessions now reach only `127.0.0.1:8000` (vLLM) and `127.0.0.1:8767` (the Gmail service). Phase 1 makes that trace a command anyone can rerun; Phase 2 makes a regression fail locally instead of leaking.

**Non-goals:**
- **Calling it air-gapped.** Web search and `ling-fetch` reach the internet by design (§5), and the docs must never say "air-gapped" for a mode that allows them. The command `/airgapped` keeps to this: only its `on` level, which allows neither, carries the word ([MIGHTLING_AIRGAPPED §1](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)).
- **Inbound exposure.** The model server listening on `0.0.0.0:8000` is accepted by design: Mightling assumes the local network is trusted ([README](./README.md), "Accepted by design").
- **Containers.** The web chat's own egress (Onyx, SearXNG, the sidecars) is out of scope. Onyx's telemetry is handled by `configure` ([ONYX](./DREAMFERENCE_ONYX.md)).

---

## 2. Surface

This spec adds no slash command of its own. (When it was written the patch budget was reserved for `/night`, which is now built; the airlock's switch has since moved to `/airgapped`, see the note at the top.)

| Command | Phase | Effect |
|---|---|---|
| `ling-admin audit egress [--tui] [--prompt "…"] [--json]` | 1 | Traces one session; prints destinations, processes, verdict; exit 0 on pass, 1 on an unexpected destination, 2 when the trace itself failed. |
| `ling --airlock …` or `airlock = true` in `$CODEX_HOME/config.toml` | 2 | Runs `ling` in the airlock. The launcher handles the flag before Codex parses its arguments, like `ling app`, so it needs no patch. |
| `ling airlock log [--since 1d]` | 2 | Prints the ledger (§4.4). |

**`codex build` runs the audit.** After a build with a new Codex release, `ling-admin codex build` runs `audit egress` and prints the verdict. A failing verdict does not undo the build, but it is shown in red with the offending destinations. (As built, §10.5: after every build that installs a new `ling`, both kinds of session, `--no-audit` to skip; the verdict is marked ❌, the CLI's mark for a failure, not coloured.) [MIGHTLING_CODEX §6](./DREAMFERENCE_MIGHTLING_CODEX.md) says to re-run the trace after every Codex bump; this makes that automatic.

---

## 3. Phase 1 — the audit

### 3.1. Method

This productises the 2026-09-29 procedure.
1. **Setup.** A throwaway git repository with one committed file, and a throwaway `CODEX_HOME`, so that no login, history or config of the user's influences the result, and none is touched.
2. **Exec session.** `strace -f -qq -e trace=connect,sendto,sendmsg,sendmmsg,execve -s 256` (as built: `sendmmsg` is how glibc sends a lookup's queries, and without it a DNS query leaves a connect to the resolver and no name, §10) around `ling exec --skip-git-repo-check "<prompt>"`, with the default prompt `Reply with exactly: pong`.
3. **TUI session** (`--tui`). The same trace around the interactive TUI, driven on a pseudo-terminal: accept the trust prompt, send the prompt, wait for the reply, quit. The TUI-only announcement fetch was found this way and is invisible to `exec`.
4. **Destinations.** Every `sin_addr`/`sin6_addr` and port from `connect`, `sendto` and `sendmsg`, counted.
5. **DNS.** Every name in UDP payloads to port 53.
6. **Unix sockets.** Every `sun_path`.
7. **Processes.** Every `execve`, with git subcommands that touch a network (`ls-remote`, `fetch`, `clone`, `pull`, `remote-https`) listed separately.

### 3.2. Verdict

- **Pass:** every IP destination is loopback and on the allowlist (§4.3), no DNS query was sent, and no networked git subcommand ran.
- **Fail:** anything else, listed first.
- **Trace failed:** the session did not produce a reply, or `strace` could not attach. This is not a pass.

`--json` writes the full result to `~/.mightling/audit/<timestamp>-<exec|tui>.json` as well (the session kind is in the name since 2026-10-02, so a build's two records cannot collide): destinations, DNS names, processes, verdict, the `ling --version` output, and the Codex tag and patch hashes from the build stamp. Two audits can then be compared across builds.

### 3.3. Requirements

- **Tracer:** `strace`, installed here. Tracing one's own child needs no privilege under the default Yama `ptrace_scope` of 1.
- **Model server:** the session needs a model server; without one the audit reports "trace failed".

---

## 4. Phase 2 — the airlock

### 4.1. Model

`ling` runs in its own network namespace, where the only interface is `lo`. Every outbound connection fails with `ENETUNREACH` at the kernel. On the namespace's `lo`, the launcher listens on each allowlisted port and relays each connection to the same port on the host's loopback. The allowed services therefore see ordinary loopback clients, and nothing else is routable.

This is the stronger form of what the patches do. The patches close channels one at a time at their call sites. The airlock needs no knowledge of the channels: a new one in a future Codex release fails closed.

### 4.2. Mechanism

**Starting the airlock.** `ling --airlock` makes the launcher do the following before Codex parses its arguments:
1. **Relay directory.** It creates `$XDG_RUNTIME_DIR/mightling-airlock/<pid>/`, mode `0700`.
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
| `8767` | Gmail search service | `ling-admin gmail`. |
| `8888` | SearXNG | `ling-search` (§5). |

**The diffusion endpoint (`8001`) is not allowed:** the agent does not use it. Unix sockets on the host are unaffected by a network namespace, which is intended: the Docker socket, the D-Bus session and the like stay reachable. If one of them should be closed too, that belongs to Codex's command sandbox, not the airlock.

### 4.4. Ledger

A connection that fails at the kernel leaves no trace by itself. The ledger is built from two sources:
- **DNS.** Inside the namespace, `/etc/resolv.conf` is bind-mounted to point at `127.0.0.1`. The launcher runs a stub resolver there that answers every query `REFUSED` and records the name, the time and the asking process (from `/proc/net/udp` inode to pid). Almost every real egress starts with a lookup, so this catches nearly all attempts, with the name the program wanted.
- **Direct-IP attempts** skip DNS. With `--airlock=trace`, the airlocked process tree also runs under `strace -f -e trace=connect -e status=failed`, and failed non-loopback connects are added to the ledger. This is off by default for its overhead.

The ledger is appended to `~/.mightling/airlock/ledger.jsonl`:

```json
{"at": "…", "session": "…", "kind": "dns", "name": "ab.chatgpt.com", "pid": 4242, "exe": "/home/…/ling"}
```

`ling airlock log` prints it grouped by name and executable. An empty ledger over a week of use is the record the goal asks for.

---

## 5. Search and fetch, honestly

- **Search goes out by design:** `ling-search` reaches SearXNG on `127.0.0.1:8888`, and SearXNG queries the upstream engines from outside the airlock. The ledger cannot see those queries. The airlock's own docs and the agent's prompt must say plainly that searches leave the machine through SearXNG.
- **`ling-fetch`** connects directly to the URL (or through `https_proxy` when set), so it fails inside the airlock. It fails with its existing error path, and the ledger records the lookup.

**The prompt must match the mode.** The launcher writes the model catalog, including `WEB_ACCESS_INSTRUCTIONS`, at every start. In airlock mode it writes a variant:
- search is available, and its queries leave the machine;
- `fetch` is not available.

Without that, the model would be told it can fetch pages, and would keep trying.

**Planned:** fetch through a local, logged proxy that asks for confirmation. That is out of scope here.

---

## 6. Where it is used

- **Opt-in.** Interactive use is opt-in (`--airlock` or the config key) until a release has run a week of the maintainer's own sessions with an empty ledger and nothing broken.
- **Night Shift** runs use the airlock by default once it exists ([NIGHT_SHIFT §6](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)): no one is watching those sessions.
- **`ling-admin drafter tune`** runs its training process in the airlock ([SELF_SPEEDING §7](./DREAMFERENCE_SELF_SPEEDING.md)).

---

## 7. Tests

- **Phase 1:**
  - unit tests of the trace parser on recorded `strace` output: IPv4, IPv6, DNS payloads, unix paths, `execve` lines, including the 2026-09-29 traces with the five channels, which must fail the verdict;
  - a live test (skips without a server): the audit passes on the current build.
- **Phase 2:**
  - launcher unit tests for the allowlist, the socket-path length check and the resolver stub;
  - a live test in which an airlocked `ling exec` session answers through vLLM;
  - inside the same session, a `curl https://example.com` run by the agent fails, and `example.com` appears in the ledger;
  - `ling-admin gmail status` and `ling-search` still work inside it;
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

- **`ling app`.** The desktop app launches its own process tree. Should the airlock also cover it, or is it only for the terminal agent?
- **MCP servers.** A user-configured MCP server that needs the network fails inside the airlock. Should the allowlist accept per-server exceptions (by port), or should such servers be run outside and reached over a unix socket?

---

## 10. As built (2026-10-01): Phase 1, exec sessions

### 10.1 What exists

`ling-admin audit egress [--prompt "…"] [--json]`, in `dreamference/audit/`:

| Piece | Path |
|---|---|
| The trace's content (destinations, resolvers, names, unix sockets, processes, networked git) | `egress_trace.py` |
| Reading strace's output | `strace_parser.py` |
| The verdict and its exit code (0 pass, 1 unexpected destination, 2 trace failed) | `egress_verdict.py` |
| The session, the judgement, the report, the JSON result | `egress_audit.py` |

**Run on this machine, twice, both a pass.** At 21:34 on the 16-patch build of `rust-v0.158.0` installed at 19:10, and at 21:44 on the 17-patch build that the `/airgapped` work installed at 21:41 (the kept record, `~/.mightling/audit/20261001-214436.json`, `build_matches_checkout: true`). Each time one `ling exec "Reply with exactly: pong"` in a throwaway repository with a throwaway `CODEX_HOME` connected to `127.0.0.1:8000` (twice) and `127.0.0.1:8767` (once), sent no DNS query, opened no unix socket but glibc's absent `nscd` one, and ran no networked git command; 2.7 s. A second run whose prompt made the agent run `curl https://example.com` also passed, correctly: Codex's read-only sandbox refuses the `socket` call (`socket(AF_INET6, SOCK_DGRAM, …) = -1 EPERM` in a trace taken with `socket` added), so `curl` never reaches a `connect` or a lookup and exits 6.

### 10.2 Where it differs from §2 and §3

- **`sendmmsg` is traced** (§3.1 listed `connect`, `sendto`, `sendmsg`). On this machine a lookup is a `connect` to `127.0.0.53:53` followed by one `sendmmsg` carrying the A and the AAAA query.
- **Port 53 is a resolver, not a destination.** A connect to `127.0.0.53:53` is loopback, and a query sent there still leaves the machine through systemd-resolved. So resolvers are listed apart, every name asked is a failure, and a query whose name could not be read (strace cuts payloads at 256 bytes) fails too, naming the resolver.
- **A connect counts whatever it returned**: `EINPROGRESS` is the normal result of a non-blocking connect, and a refused or unreachable one was still an attempt.
- **`127.0.0.1:9` is a failure with its own wording.** It is where the launcher points `chatgpt_base_url`; a connect there is a ChatGPT-backend call that no patch closes and that failed only because of the redirect.
- **`git ls-remote --get-url` is not networked** (it prints a URL after applying `insteadOf`; `ling-code` uses it for the submodule policy). git's subcommand is the first word that is neither an option nor an option's value, so a directory named `fetch` or a `--grep pull` is not a finding. `push` is counted as networked beside §3.1's list.
- **A failed `execve` is not a process**: it is the shell walking `PATH`. An `execve` interrupted in the trace (`<unfinished ...>`) is counted when its `resumed` line reports success.
- **The code index is off for the traced session** (`code_index_enabled = false` in a throwaway config named by `DREAMFERENCE_CONFIG_PATH`). Its indexers run detached, in their own network-less sandbox, and outlive the session; they are not what this trace can show.
- **The JSON result also says whether the audited binary was built from the checkout as it is** (`build_matches_checkout`): the patch hashes are the checkout's.
- **The session is stopped with its whole process group** after 300 s, so a session that never answers (no model server) ends as "trace failed" and leaves nothing running.

### 10.3 Tests (`tests/test_egress_audit.py`, 14)

- The recorded trace of the passing session (`tests/fixtures/egress/exec_pass.strace`, 347 lines, 22 of them unfinished or resumed).
- The same trace with the channels of `0013` and `0015` written back in (`exec_leaks.strace`): the verdict fails and names `ab.chatgpt.com`, `chatgpt.com`, `raw.githubusercontent.com`, the `git ls-remote` of `openai/plugins`, each address, and the `127.0.0.1:9` redirect. **These lines are written by hand in strace's format**: the traces of 2026-09-29 were in a scratch folder that has since been deleted, so §7's "the 2026-09-29 traces" could not be used.
- A real recording of `curl https://example.com` (`curl_example.strace`): the name, the resolver and the four addresses are read from what glibc and the kernel actually printed.
- A session with no reply, and an empty trace, are "trace failed"; a local port off the allowlist fails; the escapes and the DNS decoder; git commands that reach nothing; the report; the audit run end to end with a stand-in for strace (throwaway repository and home, both removed afterwards; the JSON written to the real `CODEX_HOME`); missing strace or `ling`.

### 10.4 Not built

- ~~**§8's acceptance on a build without `0015`**~~ Done on 2026-10-03. A scratch build of the same tree with every patch except `0015` (built in `~/.cache/dreamference/puffin-codex/no0015`, the installed build untouched) **fails `audit egress --tui` with exit 1**, naming all four channels: DNS lookups of `ab.chatgpt.com` (the metrics exporter), `git ls-remote` and a fetch of `github.com/openai/plugins`, `raw.githubusercontent.com` (the announcement tip only the interface fetches), and a connect to `127.0.0.1:9`, reported as a ChatGPT-backend call no patch closes. The installed build passed the same audit the same day (model server and Gmail only, no DNS). Results: `~/.mightling/audit/20261003-131301-tui.json` (pass) and `20261003-132456-tui.json` (fail). That run's file listed `0015` among the patches, because the result recorded the checkout's patches rather than the traced binary's; since then the result names the traced binary and its SHA-256, and records the patch list only when the binary is the installed build and that build matches the checkout.
- **Phase 2**, the airlock and its ledger (§4): its switch is now the `on` level of `/airgapped`.

### 10.5 The interface, and the audit after a build (2026-10-02)

**`ling-admin audit egress --tui`** traces the full-screen interface instead of `ling exec` (`dreamference/audit/tui_session.py`, `TuiSession`). The same strace is put around `ling` with no subcommand, on a pseudo-terminal of 160×50 with `TERM=xterm-256color`; the audit waits for the composer, types the prompt, waits for the reply, lets the session settle for 3 s, types `/quit`, and reads the trace.

Where it differs from §3.1 step 3:

- **The trust prompt is not answered; it is not shown.** The throwaway `CODEX_HOME` is given `[projects."<repo>"] trust_level = "trusted"` before the session starts (the launcher edits `config.toml` in place and keeps the table), as the live slash-command tests do. So the trust screen itself, and any other first-run screen, is not part of the trace.
- **The reply is read from the session file, not from the screen.** The screen also shows the typed prompt, and the default prompt contains the word the reply consists of. A session has replied when a `rollout-*.jsonl` under the throwaway home holds a `task_complete` event with a non-empty `last_agent_message`.
- **The terminal is driven with `pexpect` and rendered with `pyte`**, which the live tests already use and which are not dependencies of the package. Without them `--tui` is "trace failed" (exit 2) and prints the `pip install` line, as a missing `strace` does.
- **A session that does not end is stopped with its process group**, after the same 300 s; "trace failed" then also says whether the interface had opened and taken the prompt.
- **The interface's commands can reach the network; `exec`'s cannot.** The launcher gives the interface's workspace-write sandbox `network_access = true`, while `ling exec` runs read-only with none. A command the model chooses to run in the traced interface session is therefore traced with network access. The default prompt asks for one word so that no command runs; `--prompt` is how to look at what a task does.

**Run on this machine, 2026-10-02 11:49, both a pass**, on the `ling` installed 2026-10-01 21:41 (build key `064c6b8c737f-4b9f72c9a5ce`, 17 patches; `build_matches_checkout: false`, because the launcher source had moved on since). Records: `~/.mightling/audit/20261002-114937-tui.json`, `20261002-114948-exec.json`.

| | `exec` | interface |
|---|---|---|
| `127.0.0.1:8000` (model server) | 2 connects | 3 |
| `127.0.0.1:8767` (Gmail service) | 1 | 1 |
| DNS queries, networked git | none | none |
| Unix sockets | glibc's absent `nscd` | the same, and `/run/user/1000/bus` (twice, with one message to `/org/a11y/bus`) |
| Wall time | about 3 s | 17 s |

The session bus is the one thing the interface opens that `exec` does not. It is a unix socket on this machine and is listed, not judged; which part of the interface asks the accessibility service was not looked into.

**After a build.** `ling-admin codex build` now ends with the audit when, and only when, it installed a new `ling`: the build was not current beforehand, or `--force` was given, and it succeeded. `EgressAudit.after_build()` traces an `exec` session and then the interface, and writes both records.

- **Not every build:** §10.4's old objection was a model request in every build. A `codex build` that finds the binary current runs nothing. Whether the binary was current is asked *before* the build, because afterwards a build that compiled and one that found nothing to do both report success.
- **It never waits for a model server.** The server is asked once with a 3 s timeout; if it does not answer, one line says the audit was skipped and names the two commands to run later. Without that check a build on a machine whose server is down would end with the launcher's five-minute wait.
- **It never changes the build's exit code.** The binary is installed either way. An unexpected destination ends with a ❌ line saying the build reaches something it should not; a trace that failed says the audit could not show what the build does; an audit that itself breaks is reported in one line.
- **`--no-audit`** skips it. Without `pexpect` and `pyte` only the `exec` session is traced, and a line says so.
- **Where the hook is:** in the CLI's `codex build` branch, not in `CodexBrandedBuilder`, so nothing that calls the builder from a test can start a session.
- **Not exercised by a real build.** On 2026-10-02 several tasks were rebuilding `ling` and the model server was shared with a benchmark, so no `codex build` was started for this. The branch is covered by tests with the builder and the audit replaced; the two sessions it runs are the ones measured above.

### 10.6 Tests added (`tests/test_egress_audit.py`, 27 in all)

- A recorded trace of a real interface session (`tests/fixtures/egress/tui_pass.strace`, 385 lines): the same destinations as `exec` plus one more connect to the model server, and the session bus among the unix sockets.
- The session file: a prompt alone is not a reply; a `task_complete` with a message is.
- The interface played by a stand-in on a real pseudo-terminal, under a stand-in `strace`: `ling` is started with no subcommand on a terminal, the prompt and then `/quit` are typed, the throwaway home trusts the throwaway repository, both are removed afterwards, and the record is `…-tui.json` with `"session": "tui"`.
- A stand-in that never answers: "trace failed", the report says the interface had taken the prompt, and the process is gone.
- `--tui` without `pexpect` and `pyte`.
- After a build: both sessions traced and recorded; no model server, no session and no wait; a failed trace does not hide a failing verdict; a broken audit is reported, not raised.
- `codex build`: audited when a new binary was installed or `--force` was given; not when the build was current, failed, or `--no-audit` was given; its exit code is the build's whatever the audit returned.
