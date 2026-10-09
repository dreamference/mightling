# Web chat

Two web UIs exist while the older one is being retired:

- **`ling web`**, Mightling's own: Ask and Work in a browser, the same page as the
  [desktop app](desktop.md), served by `ling` itself on port 3100. It replaces the Onyx chat.
- **The Onyx chat** on port 3000, described [further down](#the-onyx-chat). It still runs where it
  is installed, and goes away in a later release.

## `ling web`: Ask and Work in a browser

```bash
ling web start               # run it as a user service (loopback only)
ling web open                # sign this machine's browser in, with a one-time link
ling web status              # whether it runs, where, and how many devices are paired
ling web stop
```

The page opens on **Ask**, questions with no project, each in a scratch folder of its own; **Work**,
your projects and their threads, is one click away. Every request needs a sign-in, on this machine
too: `ling web open` signs your browser in with a link that works once.

### A phone or another computer

On a node (`ling-admin node enable`, which also starts `ling web` on the local network), other
devices can use it once they are paired. Pairing happens on the Mightling machine itself:

1. In Mightling on that machine, click **Pair a phone or another device** at the bottom of the
   sidebar (the **Devices** page, `/devices`), then **Show a pairing code**.
2. Scan the QR code with the phone's camera. It opens the pairing page on this machine with the
   code filled in; give the device a name and press **Pair**.

The QR code carries only this machine's address and an eight-digit code that works once, for ten
minutes. The device then stays signed in until you revoke it on the same page. If the phone is on
another network, such as your Tailscale network, open the other addresses under the QR code and scan
the one for that network.

Without a camera: `ling web pair` in a terminal prints the code and the address to open (and the
same QR code, drawn in the terminal), and `ling web devices` and `ling web revoke <device>` list and
remove paired devices.

From another device the page is text only: the microphone and the clipboard buttons need a secure
connection, which plain HTTP on the local network is not (selecting and copying text still works).

## The Onyx chat

A chat assistant in your browser, answered by the same local model as the terminal agent. It is
built on [Onyx](https://github.com/onyx-dot-app/onyx), running its lightweight edition (a web
server, an API server and PostgreSQL, about 900 MB resident), and rebranded as Mightling.

```bash
ling-admin chat start
ling-admin chat configure          # or --email you@example.com --password '<your password>'
ling-admin chat password           # the generated administrator account
```

Then open <http://localhost:3000> and sign in. The desktop app signs itself in on this machine;
on another machine, sign in with the account `ling-admin chat password` prints on the node.

### What it can do

| Feature | How it works | Skip with |
|---|---|---|
| **Chat with the local model** | Onyx is pointed at the model server on your machine. | |
| **Images** | Attach an image and ask about it. The default model reads images. | |
| **Web search** | Searches go through a SearXNG instance on your machine, which queries public search engines for you. No API key or account. | `--no-web` |
| **Voice input** | A microphone button, transcribed by a local Whisper server on the CPU. | `--no-voice` |
| **Gmail, read-only** | After `ling-admin chat gmail`, the assistant can search and read your connected mailboxes. It cannot send, delete or change mail. | `--no-gmail` |
| **Image search** | Finds images on the web and shows them in the chat. | `--no-image-search` |
| **Google sign-in** | `ling-admin chat google-auth` adds "Sign in with Google" to the login page. | |

The "Skip with" flags go on `ling-admin chat configure`. `--no-brand` keeps Onyx's own
branding.

### Managing it

```bash
ling-admin chat status       # version, containers and health
ling-admin chat logs         # container logs
ling-admin chat stop         # stop, keeping your data
ling-admin chat uninstall    # delete the deployment and all its data
```

`ling-admin onyx …` is an alias for the same commands.

### Privacy

Onyx's anonymous usage telemetry is switched off by `configure`. Chats and accounts are stored in
the local PostgreSQL database. Web search, image search and Gmail do reach the internet when you use
them; see [Privacy & security](privacy.md).
