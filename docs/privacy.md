# Privacy & security

Puffin's model runs on your machine. Your code, prompts and conversations are processed there and
stored there. This page lists everything that *does* reach the network, and what Puffin switches
off.

## What reaches the network, and when

| When | What is sent | To |
|---|---|---|
| Installing or starting services | Downloads of container images, Python packages and the speech-to-text model | Docker registries, PyPI, Hugging Face |
| Downloading a model | Requests for the model's files | Hugging Face |
| Building `puffin` for the first time | Downloads of the Rust toolchain, build dependencies and a prebuilt V8 engine (checked against pinned checksums) | rustup, crates.io, GitHub |
| The agent or web chat searches the web | The search query | Public search engines, through the SearXNG instance on your machine |
| The agent fetches a page | A request for that URL | The website |
| Gmail features, if you connect an account | IMAP reads of your mailbox | Google |
| Signing in to the web chat with Google, if you enable it | The sign-in exchange | Google |
| Image search, if you use it | The image query, then downloads of the matching images | Public search engines through SearXNG, then the sites hosting the images |
| `puffin update` | A check for, and download of, the latest Puffin release | GitHub |

Your repositories, prompts, chats and usage are not sent anywhere by Puffin. The only content that
leaves the machine is what these features need: a search query, a URL, a mailbox read.

## What Puffin switches off

- **Web chat telemetry.** Onyx's anonymous usage reporting is disabled when you run `configure`.
- **OpenAI services in the agent.**
  - `login`/`logout` are refused.
  - `cloud` and `remote-control` are hidden or refused.
  - `/feedback`, which uploads logs to OpenAI, is removed.
  - `/voice` and `/approve` are hidden.
  - Codex's own update check is off.

  See [Terminal agent](puffin.md#what-puffin-removes).

## The agent's sandbox

`puffin` runs shell commands inside Codex's Linux sandbox, which limits what they can write. Network
access from that sandbox is **on**, because the agent's web search and page fetching are shell
commands. The sandbox controls where files can be written; it does not block network access.

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
