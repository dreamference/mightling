# Terminal agent

`ling` is a coding agent that works in your repository. It reads files, runs commands, edits code
and explains what it did. Its prompts go to the model your GB10 serves, never to a cloud model.

```bash
cd ~/my-project
ling                              # interactive session
ling "why do the tests fail?"     # start with a prompt
ling exec "add a --verbose flag"  # run once, non-interactively (from a script: < /dev/null)
ling resume --last                # pick up the last session
```

## What happens when you run it

Before the agent starts, `ling` connects itself to the local model:

1. It finds the model server. On the GB10 that is the server on the same machine. On any other
   computer it looks for a Mightling node on the local network and uses the one it finds, with no
   address to type (see [Using a GB10 from another computer](#using-a-gb10-from-another-computer)).
   `DREAMFERENCE_VLLM_HOST`, or `vllm_host` in `dreamference.toml`, overrides both.
2. If the server is still loading, it waits and shows progress.
3. It asks the server which model it serves and how long a context that model takes.
4. It writes the settings the agent needs to use that model, and starts the session.

`ling --version`, `ling --help` and commands that never need the model (such as `completion`
and `apply`) skip all of this and answer at once.

While a SWE-bench run is using the model, the model server turns every other request away, from
`ling`, the web UI, the desktop app and the messengers alike, and says so: "The model is running a
benchmark (night 1, 37/100 done, about 9 h left). Try later or run `ling-admin night pause`."
`ling-admin night pause` (one hour; `--for 2h` for longer) lets you work meanwhile, and
`ling-admin night resume` hands the model back to the benchmark.

## Commands and flags

The everyday commands and flags:

| Command or flag | What it does |
|---|---|
| `ling exec`, `ling review` | Run non-interactively, or review changes. `ling exec` reads more input from standard input when it is not a terminal, so a script, a cron job or another program that starts it must close stdin (`< /dev/null`) or the run waits for input that never comes |
| `ling resume`, `ling fork` | Continue or branch a saved session |
| `ling apply` | Apply the agent's last diff with `git apply` |
| `-c key=value`, `-p profile` | Override configuration, pick a profile |
| `-a on-request`, `-s workspace-write` | Approval policy and sandbox policy |
| `ling mcp`, `ling sandbox`, `ling completion` | MCP servers, sandbox, shell completion |

Configuration and saved sessions live in `~/.mightling` (or `$CODEX_HOME`). If another tool left
sessions in `~/.codex`, the first run copies them and your settings across, never a cloud sign-in.

## What Mightling adds

| Feature | Details |
|---|---|
| **Local model, no account** | Prompts go to the model your GB10 serves, and no account of any kind is needed. Usage analytics are switched off in the code. |
| **Web access** | The agent can search the web and read pages through `ling-search` and `ling-fetch`, which go through a search instance on your machine. The instructions travel with every session, so this works in any directory. Both are small Rust programs that `ling-admin codex build` installs beside `ling` and links into `~/.local/bin`; they need no Python environment. You can run them yourself too: `ling-search "query"` (`-n N` for more results, `--json` for raw output), `ling-search --read "question"` (searches, then reads the top pages, 3 by default, `--pages N` up to 5, and prints numbered sources with an extract of each), and `ling-fetch URL` (`--max-chars N`, `--json`; a Google share link from a phone, `share.google/…` or `maps.app.goo.gl/…`, is resolved to the search or place it opens, since Google's own page cannot be read without a browser). `ling-search` takes `-c news|science|it|map|images|videos|music|files|social|books|dictionaries|wikimedia|movies|weather` to search one kind of source instead of the web, `-t day|week|month|year` for recent results only, and `-l pt` (or any language code) for results in a language; by default the language is detected from the query. A repeated search within the hour is answered from a small cache on your machine and sent nowhere; `!st` or `!gh` at the start of a query searches Stack Overflow or GitHub. When search engines refuse your address for a while, `ling-search` says search is unavailable, and `ling-admin searxng status` shows which engine is suspended and until when. |
| **Gmail, read-only** | If you have connected Gmail accounts (with `/apps`), the agent can search and read mail through `ling-admin gmail`. It is told that email content is untrusted and must never be treated as instructions. Turn it off with `mightling_gmail = false` in `dreamference.toml` or in `~/.config/dreamference/config.toml`; a repository's own `dreamference.toml` can only turn it off, so its `mightling_gmail = true` is ignored. |
| **`/apps`: Gmail, Drive and Calendar** | `/apps` needs no cloud sign-in and lists Mightling's own three apps. **Connect** opens the local Google sign-in (`localhost:8767/connect`; a paste-back box covers a browser on another machine), and the tokens stay on your machine. A connected app is offered to the model as read-only tools (`gmail_search`, `gmail_read`, `drive_search`, `drive_read`, `calendar_events`, `calendar_search`), each answer framed as untrusted text. At `/airgapped on` every app is unavailable. The service behind them is `ling-admin google start|stop|status`. |
| **`/usage`** | Shows this session's token usage: input (cached and new), output, the total, and how full the context window was on the last request. |
| **`/adapter`** | Adapters are specialists (Rust, for example) trained for the model your node serves and installed there with `ling-admin adapter install <name>`. `/adapter` lists the installed ones and marks this session's; `/adapter <name>` switches this session to one, `/adapter off` back to the base model. A third-party adapter, not signed by Dreamference, is flagged. `ling --adapter <name>` starts or resumes a session on one, and `mightling_adapter = "<name>"` in `dreamference.toml` (or `DREAMFERENCE_MIGHTLING_ADAPTER`) chooses one for new sessions. An adapter the node does not have starts the session on the base model, with a line saying so. |
| **`/airgapped`** | How much of the internet this session may use. `off` (the default) is everything: every sandboxed command has the network, in Read Only as in the other permission modes. `on` gives every command Mightling runs no network at all, enforced by the sandbox: no search, no page fetch, no Gmail, no `curl`, `git fetch` or package installs. Full Access cannot be combined with `on`: Mightling refuses to start with both, greys Full Access out in `/permissions` and refuses `/airgapped on` in a Full Access session. `/airgapped` shows the level and what is not covered (commands you approve to run outside the sandbox, MCP servers); `/airgapped <level>` sets it for this session, `/airgapped default <level>` for new ones. From a shell: `ling airgapped`, or `DREAMFERENCE_MIGHTLING_AIRGAPPED=on ling exec …` for one run. Mightling still talks to the model server at every level. |
| **Plugins** | Codex's plugins work while `/airgapped` is off: add a marketplace from a git repository or a folder (`ling plugin marketplace add owner/repo`, or the desktop app's Skills and tools › Plugins), then install from it with `/plugins` or `ling plugin add <plugin>@<marketplace>`. A plugin can bring skills, MCP servers, hooks and apps; its MCP servers and hooks run as you, outside the sandbox, so add only what you trust. The vendor's own catalogue stays closed. At `/airgapped on` a session starts without plugins, and installing a plugin or adding or upgrading a marketplace is refused; a session switched to `on` keeps the plugins it loaded until you restart it. |
| **Skills from other agents** | Skills you installed for Claude Code, Gemini CLI, OpenClaw or Hermes are offered to the model too, and `ling skill add` installs one from the public skill catalogues or any GitHub folder. See [Skills](#skills). |
| **`ling app`** | Opens the [desktop app](desktop.md). |
| **`ling update`** | Installs the latest published Mightling release. |
| **`ling uninstall`** | Removes a client installed by `install.sh` or `install.ps1`: its programs, the links in `~/.local/bin` (Linux, macOS), and on Windows its `Path` entry and its firewall rules (one Administrator prompt; over SSH it prints the command for an administrator). It asks first (`--yes` does not). `~/.mightling`, with your sessions and settings, stays unless you add `--purge`. A node, and a `ling` built from a checkout, are not removed this way. |
| **Code index** | `ling-code` answers where a name is defined, who calls it and what a change would touch, from an index of the repository kept current as you work. The model is offered it as tools (`code_search`, `code_show`, `code_refs`, …) and from the shell. |
| **`/night`** | Queue a task for tonight: `/night add <task>`. `ling-admin night run` (on a timer once `ling-admin night enable` is set) works through the queue while the model is otherwise idle, each task on its own git branch; nothing is merged or pushed. `ling night apply <id>` (or `/night apply <id>`) in the repository brings a finished task's changes into your checkout as uncommitted changes to review and commit; when they conflict with your own changes it refuses and changes nothing. Each task is attempted three times, one after another, and only the best attempt is committed: an attempt whose tests fail is dropped, and among those left the model compares the diffs in pairs and picks one (with no test command to run, the model's comparison alone decides). The morning report lists every attempt, which one was chosen and why, and where each one's diff and logs are. `/night add --best-of 1 <task>` runs a task once (or `[night] best_of = 1` in `dreamference.toml`, for every task); `--best-of N` asks for N. A task sent to another node with `--on <node>` is worked once there. Three attempts take about three times as long as one, so a short window may fit fewer: the report says when it did. Night Shift can also fill its own queue (off by default): with `[night] autofill = true` in `dreamference.toml`, each night begins by queueing up to five small tasks (`[night] autofill_max`, 1 to 5) from the failing tests of the latest failed CI run, the desktop QA report and unchecked items (`- [ ]` and `TODO:` lines) in the repository's `specs/` documents. They are ordinary tasks on their own branches, marked `[autofill:...]` on their first line, with the test that must pass written into them; a finding is queued at most once in 14 days, anything that mentions the sandbox, credentials, signing or similar is skipped, and nothing is merged. `ling-admin night autofill --dry-run` shows what it would queue. Review each branch in the morning like any other Night Shift result. `ling-admin night archive` moves finished runs older than four weeks (`--weeks N` for another age), with their transcripts and the old reports, to `~/.mightling/night/archive/`; their branches stay. |
| **`/worktree`: in place or a worktree** | By default a session edits your checkout in place, as Codex does, and your editor sees every change at once. `/worktree on` (or `ling worktree on`) makes new sessions in a git repository start in a fresh git worktree on a branch of their own (`ling/<id>`, under `~/.mightling/session-worktrees/`), so your checkout is untouched while the agent works; `/worktree off` goes back. The footer always shows the folder and branch the session edits. In a worktree session, `/worktree status` shows what is not yet applied, `/worktree apply` brings the changes into your checkout as uncommitted changes (all or nothing: it refuses, changing nothing, if they conflict with yours), and `/worktree discard` drops them, keeping a snapshot under `refs/mightling/snapshots/`. `ling worktree list` names a repository's worktree sessions. The worktree starts from your last commit: uncommitted changes, ignored files and submodules are not in it. `/worktree` alone opens Codex's own worktree picker. The desktop app's Local/Worktree toggle starts on the same choice. |
| **Refine mode (off by default)** | Each new task is first studied by a separate session that changes nothing and writes a refined description (intent, requirements, every code path, edge cases, what must not change, acceptance checks); a fresh session then does the task with that description. It runs by itself with no stop: in `ling exec`, on the first prompt of each interactive session, and for each new Night Shift task. On SWE-bench it solved 20 of 24 tasks against 16 and 17 without it, at about two and a half times the time; it becomes the default only if a 100-task comparison confirms that. Switch it on with `ling --refine`, `DREAMFERENCE_MIGHTLING_REFINE=on` or `mightling_refine = true` in `dreamference.toml` (`[night] refine` for Night Shift alone); `ling refine` shows the setting. A second version of its texts, refine-v2, is built for a comparison night and chosen with `ling --refine-version v2`, `DREAMFERENCE_MIGHTLING_REFINE_VERSION=v2`, `mightling_refine_version = "v2"` or `[night] refine_version = "v2"`; v1 stays the default. |
| **Guards (off by default)** | Two checks on the agent's own work, built from what went wrong in benchmark runs. The test guard points out a change that weakens an existing test (an assertion rewritten or removed, a test deleted or skipped) and holds the first stop once while it stands, so the agent decides from your task whether that behaviour was meant to change. The finish check holds the first stop once when source files changed and no test has run on them since, when the agent calls failures pre-existing without having run the tests on the unchanged code, or when it stops announcing a next step. Each hold happens at most once per session and never refuses anything. Switch them on with `mightling_test_guard = true` and `mightling_finish_check = true` in `dreamference.toml`, or `DREAMFERENCE_MIGHTLING_TEST_GUARD=on` and `DREAMFERENCE_MIGHTLING_FINISH_CHECK=on`; Night Shift's sessions follow the same settings. They work in a git repository only. |
| **Named system prompts** | `ling prompt list`, `show [<name>]` and `use <name>` choose the system prompt for new sessions: `default`; `offline`, the same without the web and email instructions, for a machine with no network; or `high-swe`, a method for repository tasks; your own go in `~/.mightling/system-prompts/<name>.md`. A resumed session keeps the prompt it started with. |

## What Mightling removes

Anything that would need a vendor's cloud or an account is hidden or refused:

| Feature | In Mightling |
|---|---|
| `login`, `logout`, `/logout` | Refused: there is no account to sign in to. |
| `cloud` (`cloud-tasks`) | Switched off: it runs tasks on a vendor's servers. It may return for a private cloud. |
| `remote-control` | Hidden. It relays sessions through a vendor's servers. |
| `/feedback` | Removed. It uploaded session logs. |
| `/voice` | Hidden. It needs a cloud voice service. |
| `/approve` (auto-review) | Hidden. It needs a cloud review model. |
| `/import` | Hidden. Its plugin step fetches marketplaces inside `ling`'s own process, where `/airgapped on` cannot stop it; the desktop app's Import follows the level. |
| Update check | Mightling's own: `ling update`. |

## Skills

A skill is a folder with a `SKILL.md`: instructions the model reads when a task matches the skill's
description, sometimes with scripts beside them. Mightling, Claude Code, Gemini CLI, OpenClaw and Hermes
all use the same file, so Mightling offers the model the skills it finds in any of their folders:

| Where | What |
|---|---|
| `~/.mightling/skills/<name>` | Skills installed for Mightling |
| `~/.agents/skills` | The folder several agents, Gemini CLI and OpenClaw among them, share |
| `~/.claude/skills`, `~/.gemini/skills`, `~/.openclaw/skills`, `~/.hermes/skills` | Other agents' own folders. Mightling links to each skill there at every start; nothing is copied, and a session cannot change them through the link |
| `.agents/skills` in the repository | Skills that travel with the project |
| A trusted project's `.codex/config.toml` | Once you trust a folder, Codex reads its `.codex/config.toml` above your own settings. Five keys in it can replace the system prompt the model is given: `model_catalog_json`, `model_instructions_file`, `instructions`, `developer_instructions` and `experimental_compact_prompt_file`. That changes what the model is told, not what is enforced: the air gap, the sandbox and the guards still apply. Only a folder you trusted can do this; trust only repositories whose authors you trust. |
| `.claude/skills`, `.gemini/skills` in the repository | Linked like the other agents' folders, but only once you have trusted the repository in Mightling (its trust prompt), since whoever wrote the repository wrote them |

| Command | What it does |
|---|---|
| `ling skill list` | What the model is offered, by source, and what it is not offered and why. `--all` adds skills another one shadows |
| `ling skill show <name>` | One skill: its description, origin, licence, files and scripts |
| `ling skill search <words>` | Search the public skill catalogues, Hermes's and ClawHub |
| `ling skill add <source>` | Install into `~/.mightling/skills`. `<source>` is `openai/<name>`, `anthropic/<name>`, `hermes/<category>/<name>`, `clawhub/<owner>/<slug>`, `<owner>/<repo>/<path>`, a github.com URL or a folder. It shows the description, licence and scripts first and asks; `--yes` skips the question |
| `ling skill remove <name>` | Delete a skill Mightling installed |
| `ling skill enable <name>` / `disable <name>` | Offer a skill anyway, or never offer it |
| `ling skill source <agent> on\|off` | Link, or stop linking, the skills of `claude`, `gemini`, `openclaw` or `hermes`, or with `repo` a trusted repository's `.claude/skills` and `.gemini/skills` |
| `ling skill adopt <name>` | Record a skill you wrote by hand as known, so later changes to it are reported |

Things to know:

- **Not every skill is offered.** One that names another operating system or a program that is not
  installed is left out, as is one its author marked as not to be started by a model. When two
  skills have the same name, one is offered: the repository's first, then Mightling's own, then
  `~/.agents/skills`, then the repository's `.claude` and `.gemini` skills, then Claude's,
  Gemini's, OpenClaw's and Hermes's, then the bundled ones.
- **Skills cost context.** The model is shown each skill's name and description in every session,
  within a budget of 2% of the context window. Mightling tells you at start when that is nearly full,
  and leaves linked skills out before the budget would drop every description.
- **Nothing in a skill runs by being installed or loaded.** Hooks, pre-approvals and
  run-before-reading lines some agents support are never acted on. A skill's scripts run only as
  commands the model issues, under the session's sandbox and approvals.
- **Mightling tells you about skills it did not install.** A skill that appears or changes in
  `~/.mightling/skills` outside `ling skill add` is reported once at the next start, and anything
  found in the `from-*` link folders that Mightling did not put there is moved to
  `~/.mightling/skills/.quarantine/`.
- **ClawHub checks every skill it lists, and Mightling shows what it found.** A skill its scan calls
  clean installs like any other; one it calls suspicious, or has no result for, is installed only
  when you confirm it at a terminal (`--yes` is refused); one it marks malicious is never installed.
- **`/airgapped on`** refuses `add` and `search` before any request is made; installing from a
  folder still works.
- Skills written for another agent name its tools ("use the Read tool"). The local model followed
  such skills correctly in testing without help; `glossary = true` in `~/.mightling/ling-skills.toml`
  adds a short translation table to the prompt if one of yours does not.

## Using a GB10 from another computer

`ling` on a laptop can use the model on a GB10 in the same network. The GB10 is the *node*; the
laptop is a *client*, and only the agent's prompts and answers cross the network: your code, the
commands the agent runs and the code index stay on the laptop.

On the GB10, once:

```bash
ling-admin node enable        # asks for your password once; `--no-web` keeps the web UI private
```

That announces the node on the local network and lets other computers reach its web search and
its web UI. **Anyone on your network can then use the model and search through the node**, and
nothing is encrypted on the local network, so do this only on a network you trust; the web UI
(`ling web`, port 3100) serves another computer or phone only once paired on the node
(`ling web pair`). `ling-admin node disable` undoes it, and `ling-admin node status` shows what is
announced.

On the other computer, type `ling`. It finds the node and remembers it:

| Command | What it does |
|---|---|
| `ling node list` | Every node on the network, the model each serves, and the address to paste into other tools |
| `ling node use <name or address>` | Use that node from now on. An address works on networks that block discovery |
| `ling node forget` | Forget the node; the next start looks again |
| `MIGHTLING_NODE=<name> ling …` | Another node, for one command |

If there are several nodes, `ling` asks once which to use and never switches by itself. If the
node's model server is stopped or still loading, it says so instead of waiting.

What does not work from a client yet: Gmail in `ling`, Night Shift (`/night add` is refused: tasks
run on the node), and anything `ling-admin` does. The desktop app, `ling-app`, runs its own
bundled `ling` against the node's model. See
`specs/DREAMFERENCE_MIGHTLING_NODE.md` §18 for what was measured.

### A second GB10

Every GB10 is installed the same way and announces itself the same way; none is "primary". The one
you are sitting at manages the others, with `ling-admin`:

| Command | What it does |
|---|---|
| `ling-admin node list` | Every node on the network, the model each serves and its load |
| `ling-admin node add <node>` | Pair with a node, once. It asks for that node's password one time and sets up a key that can only ask it for Mightling operations |
| `ling-admin node status <node>` | That node's `ling-admin status` |
| `ling-admin node set <node> --model <key>` | Give that node a model and start it there; the node runs its own safety checks |
| `ling-admin node start <node>` / `stop <node>` | Start or stop its model server |
| `ling-admin node run <node> -- <command>` | Run a command there, in the current repository at its last commit. Its changes come back as a branch, `job/<id>` |
| `ling-admin node jobs`, `logs <job>`, `cancel <job>`, `fetch <job>` | Follow, stop and collect jobs |
| `ling-admin node remove <node>` | Undo the pairing |

Using a node (asking its model, searching) needs no pairing. Changing one, or running something
on it, always does. A job runs with a memory cap and a time limit (8 GB and 90 minutes unless you
say otherwise), cannot use the GPU, and sees nothing of the other node's home folder. Uncommitted
changes are not sent. All of this was built and tested on a single GB10 playing both sides; it has
not yet run between two machines.

### Setting up a node from far away

A new GB10 can be set up from the one you sit at, over the network or over the
[overlay](remote.md) when it is at another site:

```bash
ling-admin node provision <name> --user <the account on that machine>
```

It installs Mightling there, gives the node its model, pairs it and starts it; run on nodes that are
already paired, `ling-admin node provision --all` updates them all. With no name it lists the
unprovisioned GB10s it finds on the network. `--dry-run` connects and reads only, then says what
would change. It asks for the new machine's password once (`--per-host-password` asks for each).

By default the model's files (about 22 GiB for the default model) are copied from the GB10 you run
it on. For a node on a slow connection to you, let the node download them itself:

```bash
ling-admin node provision <name> --user <account> --model-source hf
```

The node fetches the public weights from Hugging Face, at the exact revision your own GB10 holds,
and resumes a download that is cut off. Every file is then checked against your copy by its
SHA-256 checksum; only a file that Hugging Face does not carry, or whose checksum differs, is sent
from your GB10. If two machines are provisioned in one command and the download keeps failing on
the second, it takes the files from the first over their own network. The default, `--model-source
copy`, stays as it was.

### Restarting a hung node

If a node stops answering, `ling-admin node power-cycle <node>` switches its smart plug off and on
again. See [Away from home](remote.md#how-do-i-restart-a-hung-gb10-from-a-distance) for the setup.

## Debugging

Set `RUST_LOG` (for example `RUST_LOG=codex_mcp=trace ling`). The
interactive interface writes its log to `~/.mightling/logs_2.sqlite`, which
`ling-admin logs mcp` reads.

## How it is built

`ling` is compiled from source pinned to a release, with Mightling's own launcher and a short series
of patches added. See
[Architecture](architecture.md#how-ling-is-built).
