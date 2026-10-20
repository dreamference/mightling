# Phone messengers

You can ask Mightling questions from your phone, through **Signal**, **Matrix** or **Telegram**.
A question you send becomes an Ask thread on the node, the same kind `ling web` opens, and the
answer comes back in the same chat.

Every messenger is **off by default**. Installing or updating Mightling turns none of them on. Until
you run a messenger's setup command on the node, nothing for it runs, listens on a port, or connects
anywhere. When one is on, `ling-admin audit egress` lists it in every report as a declared exception.

| Messenger | Privacy | Turn it on | Turn it off |
|---|---|---|---|
| **Signal** | End-to-end encrypted. Uses your own account, in Note to Self. | `ling signal setup` | `ling signal remove` |
| **Matrix** | Your own homeserver on the node, reached over your tailnet. | `ling-admin matrix start`, then `ling chat start` | `ling chat stop`, `ling-admin matrix stop` |
| **Telegram** | Less private: messages pass through Telegram's servers unencrypted. | `ling chat telegram setup`, then `ling chat start` | `ling chat telegram off` |

All three need `ling web` running on the node (`ling web start`). The air gap applies to the two
that leave the machine. At `/airgapped on`, Signal sends nothing, not even a read receipt, and
Telegram pauses. When the air gap is off again, Signal tells you what you missed. Matrix keeps
working, because its homeserver is on the node and has no route out.

**Matrix and Telegram are previews.** They are tested against stand-ins of a homeserver and of
Telegram's Bot API, not yet against the real services. Signal has been used with a real account and
is not a preview.

## How a phone chat behaves

- **Text, images and files.** A screenshot or photo is read on the node: its text goes to
  Mightling as `[Screenshot text: …]`, and the image itself too when the model being served takes
  images. If nothing can be read from it, the bridge says so ("I can't read this image; describe it
  or paste the text.") instead of asking Mightling about nothing. A file is saved in the thread's
  folder. What a bridge cannot take (a voice message, a sticker) is named in one line, never
  dropped silently.
- **A new thread after a pause.** A message after four hours of silence starts a new thread, and
  the bridge says so: "New thread (the last message was 16h00m ago); /use 2 to go back." Change
  the pause, or switch it off with 0, in `~/.config/dreamference/config.toml`:

  ```toml
  [chat]
  new_thread_after_hours = 4
  ```
- **Reading text in images needs Tesseract** on the node, offline: `ling signal setup` installs it;
  otherwise `sudo apt install tesseract-ocr`. It reads every installed language; add one with
  `sudo apt install tesseract-ocr-<code>` (for example `tesseract-ocr-por`).

- **Signal and attachments.** For images in Note to Self to reach Mightling, the bridge lets
  signal-cli download the attachments your account receives (Signal offers no way to fetch one
  later). Every attachment that is not yours in Note to Self is deleted as it arrives, and the
  folder is swept every minute, so nothing from your other conversations is kept; it does touch
  the bridge's private folder on the node for a moment.

## Signal, in Note to Self

The bridge is linked to **your own Signal account** as one more device, the way Signal Desktop is.
You write to Mightling in **Note to Self**, and its replies appear there too, each starting with 🐦.
The bridge reads Note to Self only. It drops every other message it receives without storing or
answering it.

Run this on the node:

```bash
ling signal setup --dry-run    # prints every change it would make; changes nothing
ling signal setup
```

Setup lists what it will change and asks for your password once (sudo). It then does the following:

- It creates a system account, `mightling-signal`. The bridge runs as that account, so the agent can
  never read your Signal keys.
- It downloads signal-cli and the Java runtime signal-cli needs. Each download is pinned by URL and
  SHA-256 and checked before it is unpacked. Nothing is bundled with Mightling, and no Java package is
  installed system-wide.
- It also checks signal-cli against its maintainer's OpenPGP signature, with a key pinned in Mightling.
- It installs the bridge and its system unit, `mightling-signal.service`.
- It shows a QR code. On your phone, open **Signal → Settings → Linked devices → Link new device**
  and scan the code.
- If the phone offers to **transfer your message history**, choose **Don't transfer**.
- It offers to make Note to Self's messages disappear after a week. Note to Self has a single timer,
  so this applies to your own notes as well as to Mightling's replies.

When setup has finished, open Note to Self and write `/help`. The commands are `/new` (start a new
thread), `/threads`, `/use N`, `/stop` and `/status`. When Mightling asks to run something, you
answer `YES` or `NO`.

**What linking means.** A linked device can read every message your account receives from now on,
and can send messages as you. signal-cli decrypts everything that arrives. The bridge acts on Note
to Self only, but the keys it holds are keys to your whole account. They are kept in
`/var/lib/mightling-signal` (mode 0700, owned by the bridge's own account), out of the agent's reach.

**Day to day**, on the node:

```bash
ling signal status             # running, paired, the air gap, strangers ignored, versions
ling signal stop               # stops the bridge now; it stays off until `ling signal start`
ling signal start
ling signal setup --refresh    # after `ling update`: installs the new bridge for its account
ling signal remove             # undoes setup and deletes the keys; your Ask threads stay
```

`ling signal remove` cannot unlink the device from your phone. Do that on the phone too: **Settings →
Linked devices**, then remove "Mightling". Signal also removes a linked device that has been offline
for about 30 days. If the node is switched off that long, run `ling signal setup` again.

While a benchmark run holds the model on the node, Mightling does not answer from Signal. It
replies with the run, how far it has got and roughly how long is left, and you ask again later.

If you would rather not link your own account, `ling signal setup --number +15551234567` registers a
separate number for Mightling instead (it needs a CAPTCHA and an SMS or voice code).

**More than one Signal account.** Each Signal account gets a bridge of its own, with a name. The
first is always `default`, set up by plain `ling signal setup` (a name given for the first one is
refused); add another with `--name`:

```bash
ling signal setup --name work            # links a second account (or --number for its own number)
ling signal status                       # one line per bridge
ling signal stop --name work             # with several bridges, the commands need --name
ling signal remove --name work           # removes that bridge only; the others keep running
```

Each bridge has its own system account and keys, so one cannot read another's. A node runs at most
four, and setup needs 2 GB of memory to spare. A Signal account can have only one bridge. For now,
every bridge on a node answers with the same Linux user's agent.

The bridge runs only on a Linux node. See `specs/DREAMFERENCE_MIGHTLING_SIGNAL.md` in the repository
for the design and what has been verified.

## Matrix, over Tailscale

Matrix runs on a private homeserver on the node. The homeserver is a container on an internal Docker
network with no route out of the machine. Your phone reaches it through **Tailscale**, so Tailscale
is a prerequisite:

1. Install Tailscale on the node and sign in. Mightling does not install Tailscale, because installing
   it needs root and signing in uses your own Tailscale account.
2. Turn on HTTPS once for your tailnet, in the Tailscale admin console (**DNS → HTTPS Certificates**).
   Your phone then gets a real certificate.
3. Install Tailscale on your phone and sign in to the same tailnet.

Then, on the node:

```bash
ling-admin matrix start              # the homeserver, its loopback proxy and `tailscale serve`
ling-admin matrix add-user <name>    # your account; its password is shown once
ling chat start                      # the bridge, as the user unit mightling-chat.service
```

In **Element X** on your phone, sign in to `https://<the node's tailnet name>` with that account,
then accept the invite from Mightling.

The homeserver's name is the node's tailnet name, and **it cannot change later**, because every
account's id ends in it. If you rename the machine in Tailscale, `start` refuses to run until you
rename it back. The alternative is `ling-admin matrix remove --yes`, which deletes the homeserver and
its accounts so you can start again. The tailnet name also appears in public Certificate Transparency
logs. Your messages do not.

Push notifications are off: the homeserver has no route out. `ling-admin matrix push on` lets them
out. A push notification carries an event id but no message text. Even so, it reveals that a message
arrived, and when.

`ling-admin matrix stop` turns the homeserver off until the next `start`. Your accounts and messages
are kept. `ling-admin matrix status` shows how it stands.

## Telegram

Telegram is the less private choice. Everything you send the bot, and every answer, passes through
Telegram's servers unencrypted. Answers can include what the agent reads for you, such as mail,
files and code. Setup shows this warning and asks you to type `yes`.

```bash
ling chat telegram setup     # the warning, then the bot token from @BotFather (typed, not echoed)
ling chat telegram pair      # an 8-digit code to send the bot as /pair <code>
ling chat start
```

`ling chat telegram users` lists the paired accounts, and `ling chat telegram remove <id>` removes
one. `ling chat telegram off` deletes the token and the pairings; revoke the token in @BotFather
(`/revoke`) as well. `ling chat stop` stops the bridge for every messenger, and it stays off until
`ling chat start`.
