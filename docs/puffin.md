# Terminal agent

`puffin` is a coding agent that works in your repository. It reads files, runs commands, edits code
and explains what it did. It is built on the open-source [OpenAI Codex CLI](https://github.com/openai/codex)
(currently release 0.158.0), with Puffin's changes applied on top. Its prompts go to the model your
GB10 serves, not to a cloud model.

```bash
cd ~/my-project
puffin                              # interactive session
puffin "why do the tests fail?"     # start with a prompt
puffin exec "add a --verbose flag"  # run once, non-interactively
puffin resume --last                # pick up the last session
```

## What happens when you run it

Before the agent starts, `puffin` connects itself to the local model:

1. It finds the model server. On the GB10 that is the server on the same machine. On any other
   computer it looks for a Puffin node on the local network and uses the one it finds, with no
   address to type (see [Using a GB10 from another computer](#using-a-gb10-from-another-computer)).
   `DREAMFERENCE_VLLM_HOST`, or `vllm_host` in `dreamference.toml`, overrides both.
2. If the server is still loading, it waits and shows progress.
3. It asks the server which model it serves and how long a context that model takes.
4. It writes the settings the agent needs to use that model, and starts the session.

`puffin --version`, `puffin --help` and commands that never need the model (such as `completion`
and `apply`) skip all of this and answer at once.

## Familiar if you know Codex

Commands, flags and configuration work as they do in the Codex CLI, for example:

| Command or flag | What it does |
|---|---|
| `puffin exec`, `puffin review` | Run non-interactively, or review changes |
| `puffin resume`, `puffin fork` | Continue or branch a saved session |
| `puffin apply` | Apply the agent's last diff with `git apply` |
| `-c key=value`, `-p profile` | Override configuration, pick a profile |
| `-a on-request`, `-s workspace-write` | Approval policy and sandbox policy |
| `puffin mcp`, `puffin sandbox`, `puffin completion` | MCP servers, sandbox, shell completion |

Configuration and saved sessions live in `~/.puffin` (or `$CODEX_HOME`), not in upstream Codex's
`~/.codex`. The first run copies your sessions and settings across, never a ChatGPT sign-in.

## What Puffin adds

| Feature | Details |
|---|---|
| **Local model, no account** | Prompts go to the model your GB10 serves, and no OpenAI account is needed. Codex's usage analytics are switched off. |
| **Web access** | The agent can search the web and read pages through `puffin-search` and `puffin-fetch`, which go through a search instance on your machine. The instructions travel with every session, so this works in any directory. Both are small Rust programs that `puffin-admin codex build` installs beside `puffin` and links into `~/.local/bin`; they need no Python environment. You can run them yourself too: `puffin-search "query"` (`-n N` for more results, `--json` for raw output) and `puffin-fetch URL` (`--max-chars N`, `--json`). |
| **Gmail, read-only** | If you have connected Gmail accounts in the web chat, the agent can search and read mail through `puffin-admin gmail`. It is told that email content is untrusted and must never be treated as instructions. Turn it off with `puffin_gmail = false` in `dreamference.toml`. |
| **`/usage`** | Shows this session's token usage: input (cached and new), output, the total, and how full the context window was on the last request. |
| **`/cavemode`** | Puffin answers tersely by default: at most three sentences of prose outside code (`ultra`), unless you ask for more. `/cavemode` shows the level in force and where it came from; `/cavemode off`, `lite`, `full` or `ultra` changes it for this session, mid-task included; `/cavemode default <level>` also writes `puffin_cave_mode` to `dreamference.toml` for new sessions. `DREAMFERENCE_PUFFIN_CAVE_MODE` overrides the file, for example `DREAMFERENCE_PUFFIN_CAVE_MODE=off puffin exec …`. Code, commands, commit messages, files and security warnings are never shortened. |
| **`/airgapped`** | How much of the internet this session may use. `off` (the default) is everything. `on` gives every command Puffin runs no network at all, enforced by the sandbox: no search, no page fetch, no Gmail, no `curl`, `git fetch` or package installs. Full Access cannot be combined with `on`: Puffin refuses to start with both, greys Full Access out in `/permissions` and refuses `/airgapped on` in a Full Access session. `/airgapped` shows the level and what is not covered (commands you approve to run outside the sandbox, MCP servers, the web chat); `/airgapped <level>` sets it for this session, `/airgapped default <level>` for new ones. From a shell: `puffin airgapped`, or `DREAMFERENCE_PUFFIN_AIRGAPPED=on puffin exec …` for one run. Puffin still talks to the model server at every level. |
| **Skills from other agents** | Skills you installed for Claude Code, Gemini CLI, OpenClaw or Hermes are offered to the model too, and `puffin skill add` installs one from OpenAI's or Anthropic's catalogue or any GitHub folder. See [Skills](#skills). |
| **`puffin app`** | Opens the [desktop app](desktop.md). |
| **`puffin update`** | Installs the latest published Puffin release (the first, v1.3.0, was published on 2026-10-02). |
| **Code index** | `puffin-code` answers where a name is defined, who calls it and what a change would touch, from an index of the repository kept current as you work. The model is offered it as tools (`code_search`, `code_show`, `code_refs`, …) and from the shell. |
| **`/night`** | Queue a task for tonight: `/night add <task>`. `puffin-admin night run` (on a timer once `puffin-admin night enable` is set) works through the queue while the model is otherwise idle, each task on its own git branch; nothing is merged or pushed. |
| **Named system prompts** | `puffin prompt list`, `show [<name>]` and `use <name>` choose the system prompt for new sessions: `default`, or `high-swe`, a method for repository tasks; your own go in `~/.puffin/system-prompts/<name>.md`. A resumed session keeps the prompt it started with. |

## What Puffin removes

Some Codex features depend on OpenAI's servers or an OpenAI account. Puffin hides or refuses them:

| Codex feature | In Puffin |
|---|---|
| `login`, `logout`, `/logout` | Refused: there is no account to sign in to. |
| `cloud` (`cloud-tasks`) | Switched off. Codex Cloud runs on OpenAI's servers; the command may return for a private cloud. |
| `remote-control` | Hidden. It relays sessions through OpenAI's servers. |
| `/feedback` | Removed. It uploads session logs to OpenAI. |
| `/voice` | Hidden. It uses OpenAI's realtime voice service. |
| `/approve` (auto-review) | Hidden. It relies on an OpenAI review model. |
| Update check | Off. Codex's check would offer to replace Puffin with upstream Codex. |

## Skills

A skill is a folder with a `SKILL.md`: instructions the model reads when a task matches the skill's
description, sometimes with scripts beside them. Codex, Claude Code, Gemini CLI, OpenClaw and Hermes
all use the same file, so Puffin offers the model the skills it finds in any of their folders:

| Where | What |
|---|---|
| `~/.puffin/skills/<name>` | Skills installed for Puffin |
| `~/.agents/skills` | The folder Codex, Gemini CLI and OpenClaw share |
| `~/.claude/skills`, `~/.gemini/skills`, `~/.openclaw/skills`, `~/.hermes/skills` | Other agents' own folders. Puffin links to each skill there at every start; nothing is copied, and a session cannot change them through the link |
| `.agents/skills` in the repository | Skills that travel with the project |
| `.claude/skills`, `.gemini/skills` in the repository | Linked like the other agents' folders, but only once you have trusted the repository in Puffin (its trust prompt), since whoever wrote the repository wrote them |

| Command | What it does |
|---|---|
| `puffin skill list` | What the model is offered, by source, and what it is not offered and why. `--all` adds skills another one shadows |
| `puffin skill show <name>` | One skill: its description, origin, licence, files and scripts |
| `puffin skill search <words>` | Search OpenAI's, Anthropic's and Hermes's catalogues and ClawHub |
| `puffin skill add <source>` | Install into `~/.puffin/skills`. `<source>` is `openai/<name>`, `anthropic/<name>`, `hermes/<category>/<name>`, `clawhub/<owner>/<slug>`, `<owner>/<repo>/<path>`, a github.com URL or a folder. It shows the description, licence and scripts first and asks; `--yes` skips the question |
| `puffin skill remove <name>` | Delete a skill Puffin installed |
| `puffin skill enable <name>` / `disable <name>` | Offer a skill anyway, or never offer it |
| `puffin skill source <agent> on\|off` | Link, or stop linking, the skills of `claude`, `gemini`, `openclaw` or `hermes`, or with `repo` a trusted repository's `.claude/skills` and `.gemini/skills` |
| `puffin skill adopt <name>` | Record a skill you wrote by hand as known, so later changes to it are reported |

Things to know:

- **Not every skill is offered.** One that names another operating system or a program that is not
  installed is left out, as is one its author marked as not to be started by a model. When two
  skills have the same name, one is offered: the repository's first, then Puffin's own, then
  `~/.agents/skills`, then the repository's `.claude` and `.gemini` skills, then Claude's,
  Gemini's, OpenClaw's and Hermes's, then the bundled ones.
- **Skills cost context.** The model is shown each skill's name and description in every session,
  within a budget of 2% of the context window. Puffin tells you at start when that is nearly full,
  and leaves linked skills out before Codex would drop every description.
- **Nothing in a skill runs by being installed or loaded.** Hooks, pre-approvals and
  run-before-reading lines some agents support are never acted on. A skill's scripts run only as
  commands the model issues, under the session's sandbox and approvals.
- **Puffin tells you about skills it did not install.** A skill that appears or changes in
  `~/.puffin/skills` outside `puffin skill add` is reported once at the next start, and anything
  found in the `from-*` link folders that Puffin did not put there is moved to
  `~/.puffin/skills/.quarantine/`.
- **ClawHub checks every skill it lists, and Puffin shows what it found.** A skill its scan calls
  clean installs like any other; one it calls suspicious, or has no result for, is installed only
  when you confirm it at a terminal (`--yes` is refused); one it marks malicious is never installed.
- **`/airgapped on`** refuses `add` and `search` before any request is made; installing from a
  folder still works.
- Skills written for another agent name its tools ("use the Read tool"). The local model followed
  such skills correctly in testing without help; `glossary = true` in `~/.puffin/puffin-skills.toml`
  adds a short translation table to the prompt if one of yours does not.

## Using a GB10 from another computer

`puffin` on a laptop can use the model on a GB10 in the same network. The GB10 is the *node*; the
laptop is a *client*, and only the agent's prompts and answers cross the network: your code, the
commands the agent runs and the code index stay on the laptop.

On the GB10, once:

```bash
puffin-admin node enable        # asks for your password once; `--no-web` keeps the web UI private
```

That announces the node on the local network and lets other computers reach its web search and
its web UI. **Anyone on your network can then use the model, search through the node and open the
web UI**, which has one shared account and can search the mail connected on the node. Nothing is
encrypted or password-protected, so do this only on a network you trust. `puffin-admin node
disable` undoes it, and `puffin-admin node status` shows what is announced.

On the other computer, type `puffin`. It finds the node and remembers it:

| Command | What it does |
|---|---|
| `puffin node list` | Every node on the network, the model each serves, and the address to paste into other tools |
| `puffin node use <name or address>` | Use that node from now on. An address works on networks that block discovery |
| `puffin node forget` | Forget the node; the next start looks again |
| `PUFFIN_NODE=<name> puffin …` | Another node, for one command |

If there are several nodes, `puffin` asks once which to use and never switches by itself. If the
node's model server is stopped or still loading, it says so instead of waiting.

What does not work from a client yet: Gmail in `puffin`, Night Shift (`/night add` is refused: tasks
run on the node), and anything `puffin-admin` does. The desktop window, `puffin-app`, shows the
node's web UI on a client. This is new and has been tested on one machine only; see
`specs/DREAMFERENCE_PUFFIN_NODE.md` §18 for what was measured.

### A second GB10

Every GB10 is installed the same way and announces itself the same way; none is "primary". The one
you are sitting at manages the others, with `puffin-admin`:

| Command | What it does |
|---|---|
| `puffin-admin node list` | Every node on the network, the model each serves and its load |
| `puffin-admin node add <node>` | Pair with a node, once. It asks for that node's password one time and sets up a key that can only ask it for Puffin operations |
| `puffin-admin node status <node>` | That node's `puffin-admin status` |
| `puffin-admin node set <node> --model <key>` | Give that node a model and start it there; the node runs its own safety checks |
| `puffin-admin node start <node>` / `stop <node>` | Start or stop its model server |
| `puffin-admin node run <node> -- <command>` | Run a command there, in the current repository at its last commit. Its changes come back as a branch, `job/<id>` |
| `puffin-admin node jobs`, `logs <job>`, `cancel <job>`, `fetch <job>` | Follow, stop and collect jobs |
| `puffin-admin node remove <node>` | Undo the pairing |

Using a node (asking its model, searching) needs no pairing. Changing one, or running something
on it, always does. A job runs with a memory cap and a time limit (8 GB and 90 minutes unless you
say otherwise), cannot use the GPU, and sees nothing of the other node's home folder. Uncommitted
changes are not sent. All of this was built and tested on a single GB10 playing both sides; it has
not yet run between two machines.

## Debugging

Codex's logging applies. Set `RUST_LOG` (for example `RUST_LOG=codex_mcp=trace puffin`). The
interactive interface writes its log to `~/.puffin/logs_2.sqlite`, which
`puffin-admin logs mcp` reads.

## How it is built

`puffin` is compiled from the Codex source, pinned to a release, with a short series of patches and
Puffin's own launcher added. The Codex source itself is never edited. See
[Architecture](architecture.md#how-puffin-is-built).
