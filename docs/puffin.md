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

1. It finds the model server: `DREAMFERENCE_VLLM_HOST`, otherwise `vllm_host` in
   `dreamference.toml`, otherwise `http://localhost:8000`.
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
| **`puffin app`** | Opens the [desktop app](desktop.md). |
| **`puffin update`** | Installs the latest published Puffin release. No release has been published yet. |

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

## Debugging

Codex's logging applies. Set `RUST_LOG` (for example `RUST_LOG=codex_mcp=trace puffin`). The
interactive interface writes its log to `~/.puffin/logs_2.sqlite`, which
`puffin-admin logs mcp` reads.

## How it is built

`puffin` is compiled from the Codex source, pinned to a release, with a short series of patches and
Puffin's own launcher added. The Codex source itself is never edited. See
[Architecture](architecture.md#how-puffin-is-built).
