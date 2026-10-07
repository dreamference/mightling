# Web chat

A chat assistant in your browser, answered by the same local model as the terminal agent. It is
built on [Onyx](https://github.com/onyx-dot-app/onyx), running its lightweight edition (a web
server, an API server and PostgreSQL, about 900 MB resident), and rebranded as Mightling.

```bash
mling-admin chat start
mling-admin chat configure --email you@example.com --password '<a strong password>'
```

Then open <http://localhost:3000>. See [Get started](getting-started.md#5-optional-the-web-chat-and-desktop-app)
for why you should always pass your own credentials.

## What it can do

| Feature | How it works | Skip with |
|---|---|---|
| **Chat with the local model** | Onyx is pointed at the model server on your machine. | |
| **Images** | Attach an image and ask about it. The default model reads images. | |
| **Web search** | Searches go through a SearXNG instance on your machine, which queries public search engines for you. No API key or account. | `--no-web` |
| **Voice input** | A microphone button, transcribed by a local Whisper server on the CPU. | `--no-voice` |
| **Gmail, read-only** | After `mling-admin chat gmail`, the assistant can search and read your connected mailboxes. It cannot send, delete or change mail. | `--no-gmail` |
| **Image search** | Finds images on the web and shows them in the chat. | `--no-image-search` |
| **Google sign-in** | `mling-admin chat google-auth` adds "Sign in with Google" to the login page. | |

The "Skip with" flags go on `mling-admin chat configure`. `--no-brand` keeps Onyx's own
branding.

## Managing it

```bash
mling-admin chat status       # version, containers and health
mling-admin chat logs         # container logs
mling-admin chat stop         # stop, keeping your data
mling-admin chat uninstall    # delete the deployment and all its data
```

`mling-admin onyx …` is an alias for the same commands.

## Privacy

Onyx's anonymous usage telemetry is switched off by `configure`. Chats and accounts are stored in
the local PostgreSQL database. Web search, image search and Gmail do reach the internet when you use
them; see [Privacy & security](privacy.md).
