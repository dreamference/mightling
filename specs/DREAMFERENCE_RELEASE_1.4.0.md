# Puffin 1.4.0 — release notes

**Status:** the first public release. Drafted 2026-10-03 from `git log v1.3.0..main`, completed 2026-10-06 when `/apps`, the desktop Work window, observation masking and patch `0023` were merged. The text between the two rules is the GitHub release's description.

**Version.** setup.py and `dreamference.__version__` say `1.4.0`; the release workflow still stamps the version it is given into setup.py, `tauri.conf.json` and the `puffin` binary in its own checkout.

---

Puffin is OpenAI's Codex CLI running an open model on an NVIDIA GB10, with no cloud model, no OpenAI account and no phone-home, plus a browser chat assistant and a desktop app on the same local model. This is its first public release.

**Install** on a GB10 (arm64 Ubuntu, Docker with the NVIDIA Container Toolkit):

```bash
curl -fsSLO https://github.com/dgxcoder/dgxcoder/releases/latest/download/install.sh
bash install.sh
puffin-admin server start
cd ~/my-project && puffin
```

On any other Linux machine the same script installs the client only. Already on 1.3.0: `puffin update`.

## Highlights

- **Preview: Gmail, Google Drive and Calendar in `/apps`.** Codex's `/apps` works without an OpenAI sign-in and lists Puffin's own three apps; Connect goes through a local Google sign-in, so tokens stay on your machine. Connected apps reach the model as read-only tools, each answer framed as untrusted text, and none is offered at `/airgapped on`.
- **Preview: the desktop app has a Work window.** `puffin app --work` (or `puffin app <folder>`) drives `puffin` sessions: threads by project, streaming commands and diffs, approvals in the conversation, Stop, steer and undo, context use with Compress. The app server itself refuses Full Access at `/airgapped on`, for every client (patch `0023`).
- **Long sessions can mask old tool output** (off by default). Past 85% of the context, older outputs are replaced by a placeholder naming a saved copy, and any one output is capped at 8,000 tokens.
- **`/airgapped` is two levels and holds.** `off` or `on`; the DuckDuckGo-only level is gone (its engine answered with a CAPTCHA). At `on` a session is held by a seal outside the folders its commands can write, Full Access is refused together with `on` (at launch, in `/permissions`, and `/airgapped on` inside a Full Access session), Gmail is not offered, and `puffin update` keeps working from your own shell.
- **The sandbox works outside the IDE.** Every `puffin-admin` run checks that bubblewrap can create its sandbox and offers to fix it: `puffin-admin host setup` installs an AppArmor profile that lets `/usr/bin/bwrap`, and nothing else, create user namespaces. Night Shift's timer, jobs over SSH and tasks sent to another node depend on it.
- **The code index is actually used.** Its `code_*` tools now reach the local model (MCP tools are sent as plain functions), the prompt says when to use them, `search` reads function bodies, and the launcher waits up to 15 s for the index's server. On SWE-bench the agent went from 0 index queries in 24 instances to queries in 14 of 14.
- **Named system prompts.** `puffin prompt list|show|use`, `DREAMFERENCE_PUFFIN_PROMPT`, `puffin_prompt`; `default` is unchanged byte for byte, `high-swe` is a method for repository tasks, and `~/.puffin/system-prompts/<name>.md` adds your own. A resumed session keeps its prompt.
- **Skills from more places.** Hermes and ClawHub as install sources (ClawHub's malware verdict decides: malicious never, suspicious only after you confirm), and a trusted repository's `.claude/skills` and `.gemini/skills`.
- **More than one GB10.** Pair another node over SSH, use it as extra model capacity for Night Shift and SWE-bench, copy a model to it (`node sync-model`), run sandboxed jobs on it with environments, outputs and read-only binds, and hand it a night task (`/night add --on <node>`).

## By area

### Air gap
- Two levels, `off` and `on`; a leftover `duckduckgo` setting is ignored with a note (an older node's job naming it runs with no network).
- A session at `on` is sealed in `$XDG_RUNTIME_DIR`, which the sandbox mounts read-only, so a command rewriting the level files cannot loosen it; `/airgapped off` typed by you lifts it.
- `on` and Full Access are incompatible and refused together, with the reason.
- A session that starts at `on` prints what holds and what does not (shown by `puffin exec`; in the TUI see Known issues).
- `puffin-admin mcp`'s web tools follow the level; Gmail is not offered at `on`.

### Sandbox prerequisite
- New check on every `puffin-admin` run (about 25 ms, from a throwaway systemd user unit), with three choices: fix now (sudo), turn off what needs it (Night Shift), or not now. Never asked for `mcp`, `host`, `node serve-job`.
- `puffin-admin host setup` installs and loads `/etc/apparmor.d/puffin-bwrap`. Verified on the development GB10.

### Prompts (`/prompt`, Phase 1)
- Named prompts for new sessions from the environment, the config file or `puffin prompt use`; Night Shift (`[night] prompt`) and SWE-bench (`--prompt`) can name one. `/prompt` inside the TUI is not in this release.

### Skills (Phase 3)
- `puffin skill add hermes/<category>/<name>` and `clawhub/<owner>/<slug>`; `search` covers both.
- Repository skills linked as `from-repo-claude` / `from-repo-gemini` only for repositories you trusted in `~/.puffin/config.toml`.

### Nodes (Parts 2 and 3)
- Replica lanes: a paired node serving the same model takes part of Night Shift's and SWE-bench's model requests.
- `node list` shows free memory; `node sync-model <node> <model>` copies weights with checksums.
- `node run --setup`, `--out`, `--bind`; finished jobs are pruned.
- `/night add --on <node>` runs a night task on another node and brings its branch back.

### Code index (`puffin-code`)
- The index as MCP tools for the local model, a prompt block that says when to use it, body search, absolute paths, `refs`/`callers`/`impact` that add text matches when the graph has none.
- Executing indexers (rust-analyzer, scip-java, scip-dotnet) run when the model server is idle or stopped.
- Submodule trust shown in `status` and in each answer's header; a run stopped by `server start` no longer inflates the next run's memory request.

### SWE-bench (`puffin-admin swe-bench`)
- The code index as a second arm; the agent can write `/testbed`; the index's MCP server is required in the container; custom prompts are mounted and recorded with their hash.

### Diffusion model switched off
- The diffusion sidecar is never started, downloaded or shown (`DIFFUSION_ENABLED = False`); a leftover container is removed by `server start|stop|remove`. The code is kept and one constant brings it back.

### Egress audit
- The result names the traced binary and its SHA-256, and credits patches only to a matching install. Both audits (`exec` and `--tui`) pass on the current build.

### Desktop app
- The Work window beside Chat, on `puffin app-server`; Chat is unchanged and `puffin app` alone still opens it.
- Night Shift holds back while a Work turn is running.
- Not yet: review, git worktrees, settings pages, choosing a model.

### Apps
- `puffin-admin google start|stop|status` runs the local Google service; `server start` starts it on a node.
- Drive covers My Drive and shared drives (Docs and Slides as text, Sheets as CSV); Calendar covers every calendar.
- The prompt names the connected apps' tools instead of the older Gmail shell commands.

### Context budget
- Observation masking (patch `0021`, leaf crate `puffin-rs/masking`) and the 8,000-token cap per tool output; `puffin-code show` pages at 100 lines and folds long docstrings.
- `swe-bench run --mask on|off` measures it.

### Other
- Start-up lines (air gap, skills, Night Shift, prompt) appear inside the TUI's history.
- `puffin app-server` threads get the served model and Puffin's prompt, and the TUI's `/model` lists the local model without a ChatGPT sign-in.
- SWE-bench's runtime carries `liblzma`, which `puffin` now links.
- Night Shift holds the tasks of a night to the model server's KV pool.
- The compaction ledger hook is on by default; the interactive compaction limit follows the KV pool.
- Install from a release: `install.sh`, a package that works without a checkout, `puffin update` installs `puffin-code` too.
- Release assets carry Codex's licence and notice beside the binaries built from it.

## Known issues
- **Preview features.** `/apps` (patch `0022`) and the Work window are compiled, unit-tested and pass the egress audits (only loopback ports 8000 and 8767). Live so far: `/apps` lists its three rows and its Connect page works, and the app server lists the model; not yet exercised: searches against real Drive and Calendar accounts, and the Work window against a running app server. The app server's refusal of Full Access at `on` (patch `0023`) was checked live: refused with its reason at `on`, accepted at `off`.
- Drive and Calendar need Google's full scopes with GNOME's client (the read-only scopes are refused); Puffin's services only ever read.
- The Work window's links are not followable yet, and two windows opened separately run as two processes.
- The default model's SGLang image is pulled by `server start`, but the 122B fallbacks need a custom vLLM image that it cannot yet build for you (`Dockerfile.dflash`, then `Dockerfile.dense`).

## Licence
Puffin is AGPL-3.0-or-later. `puffin` is built from OpenAI's Codex (Apache 2.0, `codex-LICENSE.txt` and `codex-NOTICE.txt` in the assets).

---

## Specs added (no code yet)
Fleet (provisioning more GB10s), Python Quality (a ratcheted code standard; Phase 0, the pinned tools, is in), PDF reading and self-speeding, and the Advisor node (on hold).
