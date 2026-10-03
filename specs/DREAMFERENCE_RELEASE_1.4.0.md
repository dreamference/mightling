# Puffin 1.4.0 — release notes (draft)

**Status:** draft, written 2026-10-03 from `git log v1.3.0..main` (126 commits, `v1.3.0` = `265ff75`, 2026-10-01). Not released, not tagged. It lives here rather than under `docs/` because everything in `docs/` is published to GitHub Pages on push. The release workflow writes GitHub's own notes (`--generate-notes`); this text is meant to be pasted over them.

**Version.** `dreamference.__version__` and setup.py still say `1.2.0`. That is expected: the release workflow stamps the tag's version into setup.py, `tauri.conf.json` and the `puffin` binary in its own checkout only, so the source is never bumped.

---

## Highlights

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

### Other
- Night Shift holds the tasks of a night to the model server's KV pool.
- The compaction ledger hook is on by default; the interactive compaction limit follows the KV pool.
- Install from a release: `install.sh`, a package that works without a checkout, `puffin update` installs `puffin-code` too.

## Specs added or reworked (no code yet)
Puffin Apps (Gmail, Drive and Calendar through `/apps` with no OpenAI sign-in; scope test done), Puffin Desktop (a Codex-app-shaped Work window beside today's Chat), Context Budget (masking old tool outputs), Fleet (provisioning more GB10s, with the user's answers), Python Quality (a ratcheted code standard; Phase 0 built), Advisor node (on hold).

## Known issues
- In the TUI, the start-up lines (`/airgapped on`'s and the skills line) are drawn under the first frame and not seen. A fix is in progress.
- `puffin app-server` threads ignore the launcher's model and so run without Puffin's prompt. A fix is in progress; nothing in this release uses `app-server`.
- Drive and Calendar need Google's full scopes with GNOME's client (the read-only scopes are blocked); Puffin's services will be read-only by construction.

---

## Before tagging

Must land, then one `puffin-admin codex build`, both egress audits, and the full suite:

1. **The start-up-line and app-server fixes** (worktree `quick-wins`, uncommitted at the time of writing) — or move them to Known issues as above.
2. **`swe/runtime-lzma`** (unmerged branch) if it is meant for this release.
3. **`tests/watchdog-leak`** (the test that leaves a watchdog thread running).
4. **The overnight prompt A/B's spec results** (PROMPT §6, SWE-bench §13), so the notes can say what `high-swe` measured.

Decide, not required:

- **Context budget** (`ctx/budget`, masking off by default, and the revised spec on `spec/context-budget-v2`): ship off-by-default in 1.4.0, or wait for its benchmark and ship in 1.5.0. Recommended: 1.5.0, after the measurement.
- **Apps Phase 1, the desktop Work window, Fleet provisioning**: in progress tonight, each needs a rebuild and live checks; recommended for 1.5.0.
- **Python quality**: Phase 0 (tool settings, pinned dev tools) is in; the ratchet test lands with Phase 1, which needs no other branch open.
