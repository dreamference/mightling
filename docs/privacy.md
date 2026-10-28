# Privacy & security

Mightling's model runs on your machine. Your code, prompts and conversations are processed there and
stored there. This page lists everything that *does* reach the network, and what Mightling switches
off.

## What reaches the network, and when

| When | What is sent | To |
|---|---|---|
| Installing or starting services | Downloads of container images, Python packages and the speech-to-text model | Docker registries, PyPI, Hugging Face |
| Downloading a model | Requests for the model's files | Hugging Face |
| Building `ling` for the first time | Downloads of the Rust toolchain, build dependencies and a prebuilt V8 engine (checked against pinned checksums) | rustup, crates.io, GitHub |
| The agent searches the web | The search query | Public search engines, through the SearXNG instance on your machine |
| The agent fetches a page | A request for that URL | The website |
| Gmail, Drive or Calendar, if you connect an account | Read-only requests for your mail, files or events | Google |
| Image search, if you use it | The image query, then downloads of the matching images | Public search engines through SearXNG, then the sites hosting the images |
| `ling update` | A check for, and download of, the latest Mightling release | GitHub |
| Signal, only after `ling signal setup` | Your Note to Self messages and Mightling's replies, end-to-end encrypted; setup itself downloads signal-cli and a Java runtime, pinned | Signal's servers; GitHub for the downloads |
| Telegram, only after `ling chat telegram setup` | What you send the bot and its answers, unencrypted | Telegram's servers |
| Matrix, only after `ling-admin matrix start` | Your chat with the homeserver on the node, over your tailnet; with push on, push notifications (no text) | Your phone through Tailscale; with push on, the push gateway |

Apart from these, Mightling does not send your repositories, prompts or chats anywhere. The only content that leaves the machine is what these features need: a search query,
a URL, a mailbox read.

!!! note "A home of its own"
    `ling` keeps its settings and sessions in `~/.mightling`. It never reads `~/.codex`, so a cloud
    sign-in stored there by another tool is never visible to it; on first run it copies sessions,
    history and settings from there, and never the sign-in. Usage analytics are switched off in the
    code itself, so no setting or sign-in can turn them back on.

## What Mightling switches off

- **Cloud services in the agent.**
  - `login`/`logout` are refused: there is no account.
  - `cloud` and `remote-control`, which run or relay sessions on a vendor's servers, are hidden or
    refused.
  - `/feedback`, which uploaded session logs, is removed.
  - `/voice` and `/approve`, which depend on cloud models, are hidden.
  - `/import` is hidden: its plugin step fetches marketplaces inside `ling`'s own process, where
    `/airgapped on` cannot stop it. The desktop app's Import follows the level.
  - Plugins are allowed from marketplaces you add (a git repository or a folder); the vendor's own
    plugin catalogue and plugin sharing are closed.
  - The only update check is Mightling's own, `ling update`.
  - Usage analytics are disabled in the code (patch `0013`), and `ling` never reads `~/.codex`,
    where another tool may keep a cloud sign-in.
- **Network exposure.** `ling web` serves this machine only, and other devices only once paired on
  an advertised node; the sidecars (search, Google, images, voice) are published on `127.0.0.1`
  only. OpenHands, if you use it, is published on `127.0.0.1:3001`.
  **The main model server is the exception, by design:** Mightling assumes your local network is
  trusted. The model server listens on every interface at port 8000, with no API key, because
  image search and OpenHands run in Docker containers and reach it through the Docker bridge, which a
  loopback-only server would not answer. Another machine on your network can therefore send it
  prompts (it reads nothing of yours, but it can use the model). If you run Mightling on a network
  you do not trust, block the port for everything but the bridge, for example
  `sudo ufw deny in on <your-LAN-interface> to any port 8000`.

  See [Terminal agent](ling.md#what-mightling-removes).

## The agent's sandbox

`ling` runs shell commands inside its sandbox (bubblewrap on Linux, Seatbelt on macOS, the Windows
sandbox), which limits what they can write. Network access from that sandbox is **on** whenever
`/airgapped` is off, in every permission mode, Read Only included: the agent's web search and page
fetching are shell commands, and so are `pip install`, `npm install` and `git fetch`. To keep the
sandbox off the network without air-gapping the session, set
`sandbox_workspace_write.network_access = false` in `~/.mightling/config.toml` (it then holds for
Read Only too) or `DREAMFERENCE_MIGHTLING_READ_ONLY_NETWORK=off` for Read Only alone. With
`/airgapped` off the sandbox is otherwise the same as Codex's: it does not stop a command from
talking to a service on this machine, so **a command can use Docker if your account can, and Docker
access is root on the machine**, in every mode, Read Only included. If that matters for a project,
use `/airgapped on`, which also masks those sockets. You choose the sandbox mode (Read only, Workspace, Full Access) in `/permissions`, the desktop app
and the browser page; Full Access runs commands with no sandbox at all.

## Git in a folder the agent can write

The agent can edit a repository's `.git/config` and `.gitattributes`, and git can be told there to
run a program (a pager, a filter, a diff tool, an SSH or proxy command, a credential or signing
helper). Mightling's own host-side `git` calls, made outside the sandbox by the web UI, the
terminal agent's launcher, `ling delegate` and `ling monitor`, the code index, the desktop app,
Night Shift and node jobs, therefore refuse every such setting: the program a repository's config
names is not run. Repository hooks (`.git/hooks`), by contrast, do run, so committing from the
desktop app runs `pre-commit`; if an untrusted repository or an agent session could have written
into `.git/hooks`, inspect it before committing.

## Air-gapped sessions

`/airgapped on` (or `mightling_airgapped = "on"` in `dreamference.toml`, or
`DREAMFERENCE_MIGHTLING_AIRGAPPED=on` for one run) gives every command the agent runs an empty network
namespace: no search, no page fetch, no mail, no `curl`, `git fetch` or package install. Only the
model server is reached. Full Access, which runs commands outside the sandbox, cannot be combined
with it: Mightling refuses to start with both, greys Full Access out in `/permissions` and in the
desktop app, and refuses `/airgapped on` in a Full Access session. A session that starts at `on`
loads no plugin, and installing one or adding a marketplace is refused, since `git` fetches them
outside the sandbox. `/airgapped` lists what the level does not cover, such as commands you approve
to run outside the sandbox, MCP servers you configured, and the plugins of a session switched to
`on` after it started. At `on` the sandbox also masks the machine's control sockets (Docker,
containerd, Podman, the D-Bus buses, the systemd user manager). In the desktop app and the browser
page, a `!` command typed in the composer runs in your own shell with no sandbox, so it is refused at
`on`; in the terminal, `!` is yours at either level. See
[Terminal agent](ling.md#what-mightling-adds).

## Email is untrusted input

An email can contain text written to manipulate an agent that has a shell. When the agent reads mail:

- it is told that email content is data, never instructions;
- each message it reads is framed with visible untrusted-content markers;
- the Gmail service is read-only: it cannot send, delete or change mail.

No single one of these is a guarantee, so treat mail-driven agent actions with the same care as any
other untrusted input.

## Accounts

Mightling has no accounts: one person owns each machine. `ling web` signs in this machine's browser
with a link that works once (`ling web open`) and other devices by pairing (see [Web UI](web.md)).
The retired Onyx web chat's accounts and saved chats stay in its Docker volumes until you delete
them with `ling-admin chat remove`.
