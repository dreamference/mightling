# Web UI

**`ling web`** is Mightling in a browser: Ask and Work, the same page as the
[desktop app](desktop.md), served by `ling` itself on port 3100. It replaced the Onyx web chat on
port 3000, which is retired ([below](#coming-from-the-onyx-web-chat)).

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

## Image search and voice

Two local services add pictures and dictation to Ask, in the browser and in the app:

```bash
ling-admin searxng start     # web search, which image search uses too
ling-admin images start      # image search: the agent gets an `image_search` tool
ling-admin voice start       # speech-to-text for the microphone button (Whisper, on the CPU)
```

- **Images.** Ask for pictures ("show me a puffin in flight") and the agent searches the web, has
  the served model pick the best matches, and keeps them on this machine. They appear in the answer,
  served by `ling web` and the app from that local copy. Off at `/airgapped on`.
- **Voice.** The 🎤 button in the composer records; press it again and the text appears in the
  composer, for you to read before sending. Transcription runs on this machine, so it works at
  `/airgapped on`. The button is shown in the app and in a browser on this machine; from another
  device the page is text only (see above).

`ling-admin images status` and `ling-admin voice status` show whether each runs.

## Coming from the Onyx web chat

The Onyx web chat (port 3000) was retired: its code is gone, and `ling-admin chat …` now only says
where each job went. On a machine that still has its containers, `ling-admin` offers once to remove
them; `ling-admin chat remove` does the same at any time, in two steps, each asked separately:

1. **The containers** (`docker compose … down`): the chat stops. Its data is kept.
2. **The data**: its Docker volumes (saved chats and accounts, which are not imported anywhere),
   Onyx's images (about 4.9 GB) and the image search and speech containers it had started, which
   `ling-admin images start` and `voice start` recreate. This cannot be undone.

`ling-admin chat status` shows what is left; `ling-admin chat remove --yes --delete-data` does both
without asking. The deployment folder (`~/.config/onyx/deployment`) is left for you to delete.
