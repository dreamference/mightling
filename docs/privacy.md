# Privacy & security

Mightling's model runs on your machine. Your code, prompts and conversations are processed there and
stored there. This page lists everything that *does* reach the network, and what Mightling switches
off.

## What reaches the network, and when

| When | What is sent | To |
|---|---|---|
| Installing or starting services | Downloads of container images, Python packages and the speech-to-text model | Docker registries, PyPI, Hugging Face |
| Downloading a model | Requests for the model's files | Hugging Face |
| Building `mling` for the first time | Downloads of the Rust toolchain, build dependencies and a prebuilt V8 engine (checked against pinned checksums) | rustup, crates.io, GitHub |
| The agent or web chat searches the web | The search query | Public search engines, through the SearXNG instance on your machine |
| The agent fetches a page | A request for that URL | The website |
| Gmail, Drive or Calendar, if you connect an account | Read-only requests for your mail, files or events | Google |
| Signing in to the web chat with Google, if you enable it | The sign-in exchange | Google |
| Image search, if you use it | The image query, then downloads of the matching images | Public search engines through SearXNG, then the sites hosting the images |
| `mling update` | A check for, and download of, the latest Mightling release | GitHub |

Apart from these, Mightling does not send your repositories, prompts or chats anywhere. The only content that leaves the machine is what these features need: a search query,
a URL, a mailbox read.

!!! note "A home of its own"
    `mling` keeps its settings and sessions in `~/.mightling`. It never reads `~/.codex`, so a cloud
    sign-in stored there by another tool is never visible to it; on first run it copies sessions,
    history and settings from there, and never the sign-in. Usage analytics are switched off in the
    code itself, so no setting or sign-in can turn them back on.

## What Mightling switches off

- **Web chat telemetry.** Onyx's anonymous usage reporting is disabled when you run `configure`.
- **Cloud services in the agent.**
  - `login`/`logout` are refused: there is no account.
  - `cloud` and `remote-control`, which run or relay sessions on a vendor's servers, are hidden or
    refused.
  - `/feedback`, which uploaded session logs, is removed.
  - `/voice` and `/approve`, which depend on cloud models, are hidden.
  - The only update check is Mightling's own, `mling update`.
  - Usage analytics are disabled in the code (patch `0013`), and `mling` never reads `~/.codex`,
    where another tool may keep a cloud sign-in.
- **Network exposure.** The web chat is published on `127.0.0.1` only (ports 80 and 3000), so its
  admin account is not reachable from other machines on your network; `configure` applies this.
  OpenHands, if you use it, is published on `127.0.0.1:3001`.
  **The main model server is the exception, by design:** Mightling assumes your local network is
  trusted. The model server listens on every interface at port 8000, with no API key, because the
  web chat and OpenHands run in Docker containers and reach it through the Docker bridge, which a
  loopback-only server would not answer. Another machine on your network can therefore send it
  prompts (it reads nothing of yours, but it can use the model). If you run Mightling on a network
  you do not trust, block the port for everything but the bridge, for example
  `sudo ufw deny in on <your-LAN-interface> to any port 8000`.

  See [Terminal agent](mling.md#what-mightling-removes).

## The agent's sandbox

`mling` runs shell commands inside its Linux sandbox, which limits what they can write. Network
access from that sandbox is **on** by default, because the agent's web search and page fetching are
shell commands.

## Air-gapped sessions

`/airgapped on` (or `mightling_airgapped = "on"` in `dreamference.toml`, or
`DREAMFERENCE_MIGHTLING_AIRGAPPED=on` for one run) gives every command the agent runs an empty network
namespace: no search, no page fetch, no mail, no `curl`, `git fetch` or package install. Only the
model server is reached. Full Access, which runs commands outside the sandbox, cannot be combined
with it: Mightling refuses to start with both, greys Full Access out in `/permissions` and in the
desktop app, and refuses `/airgapped on` in a Full Access session. `/airgapped` lists what the level
does not cover, such as commands you approve to run outside the sandbox. See
[Terminal agent](mling.md#what-mightling-adds).

## Email is untrusted input

An email can contain text written to manipulate an agent that has a shell. When the agent reads mail:

- it is told that email content is data, never instructions;
- each message it reads is framed with visible untrusted-content markers;
- the Gmail service is read-only: it cannot send, delete or change mail.

No single one of these is a guarantee, so treat mail-driven agent actions with the same care as any
other untrusted input.

## Accounts

The web chat's accounts live in its local database. Always create the administrator account with
your own e-mail and password (see [Get started](getting-started.md#5-optional-the-web-chat-and-desktop-app)).
