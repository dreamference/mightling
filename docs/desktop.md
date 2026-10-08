# Desktop app

`ling-app` shows the [web chat](web-chat.md) in a window of its own, with an application-menu
entry, a dock icon and no browser tabs or address bar, and a second window, **Work**, that drives
the coding agent. It is built the way the best-known desktop coding agents are built: an Electron
app with its own rendering engine and the agent, `ling`, bundled inside, so typing stays smooth
whatever graphics driver the machine has.

## Install and run

The easiest way is the `.deb` on the release page: it installs the app, its launcher entry and the
`ling-app` command. The web chat has to be running first (`ling-admin chat start`).

From a checkout:

```bash
ling-admin desktop install    # the app's packages (Node.js 20+ needed) and, once, a sandbox profile (sudo)
ling-admin desktop run        # build if needed, register the launcher entry, open the window
ling-admin desktop build      # the installable .deb, in desktop/electron/out/make
```

After the first run, open it from your application menu, or with:

```bash
ling app
```

`ling-admin desktop status` reports whether everything needed to build and run it is present.

## On a Mac (preview)

The release page carries the app for macOS as a preview: `Mightling-<version>-arm64-preview.dmg`
for Apple silicon and `Mightling-<version>-x64-preview.dmg` for Intel Macs. On a Mac the app is a
client: Chat shows the web chat of the GB10 on your network, and Work runs the `ling` bundled in the
app against that GB10's model. It finds the GB10 the way `ling` does (the node must have run
`ling-admin node enable`); with several on the network, choose one with `ling node use <name>`.

**It is not signed with an Apple Developer ID and not notarized.** The app carries an ad-hoc
signature only (Apple silicon runs nothing without one), which proves nothing about who built it, so
macOS refuses to open it the first time. Check the download first: its SHA-256 is listed in the
release's `SHA256SUMS`, which is signed.

```bash
shasum -a 256 ~/Downloads/Mightling-*-preview.dmg    # compare with the line in SHA256SUMS
```

Then:

1. Open the dmg and drag **Mightling** onto **Applications**. Run it from there, not from the dmg.
2. Open it once (double-click). macOS says it cannot verify the app; click **Done** (or **OK**).
3. Open **System Settings → Privacy & Security**, scroll to *Security*, and click **Open Anyway**
   next to the line about Mightling. Confirm with your password. From then on it opens normally.

On macOS 14 (Sonoma) and earlier there is a shorter way: **Control-click** (or right-click)
Mightling in Applications, choose **Open**, then **Open** again in the dialog. macOS 15 (Sequoia)
removed that shortcut; use step 3 there.

Or, in Terminal, remove the quarantine mark the browser put on the download, which is what makes
macOS ask:

```bash
xattr -dr com.apple.quarantine /Applications/Mightling.app
```

The first time the app looks for your GB10, macOS asks whether Mightling may find devices on your
local network: allow it, or the app cannot reach the node (System Settings → Privacy & Security →
Local Network turns it back on). Because the signature is ad hoc, macOS may ask again after each
update.

What the preview does not do yet: `ling app` in a Mac terminal does not open it (open it from
Applications or Spotlight), it does not update itself (download the new dmg), and closing its last
window quits it, as on Linux.

## The Work window

**Work** drives the terminal agent from a desktop window, on your GB10. It talks to one
`ling app-server` of its own, the `ling` bundled in the app, and needs no web chat:

```bash
ling app --work               # open Work
ling app ~/my-project         # Work, with a new thread on that folder
ling app --thread <id>        # Work, on an existing thread
```

- **Threads grouped by project**, with replies, commands and their output, file changes and the
  turn's diff as they stream.
- **Approvals in the conversation**, Stop (or Esc), steering while a turn runs, and undo of the last
  turn.
- **Context use** with a Compress button, the served model and the `/airgapped` level.
- **A permission picker** that greys out Full Access at `/airgapped on`; the server itself refuses
  that combination for every client.
- **One app:** opening `ling app` again brings the running window forward; `mightling://thread/<id>`
  links open Work on that thread.

Not there yet: review, git worktrees, settings pages and choosing a model (it is shown, not chosen).
Night Shift holds back while a Work turn is running, and the machine stays awake until it ends.

## Notes

- **The web chat must be running.** If it is not answering, `ling app` and
  `ling-admin desktop run` say so and name the command that starts it, instead of opening an
  empty window.
- **Nothing leaves the machine.** The app has no crash reporting, no update checks and none of the
  background services a browser engine normally talks to; `ling-admin audit egress --app` traces a
  whole session of it and lists every connection it made.
- **Fresh styling on every launch.** The window clears its cached pages each time it opens, so
  changes to the web chat's look show up immediately. You stay signed in.
- **Links open in your browser.** A link that would open a new tab (an external citation, for
  example) opens in the system browser; the window stays on the chat.
- **Installed size.** The engine is bundled, so the package is larger than a window on the
  system's browser engine would be: see the release page for the current size.
