# Desktop app

`ling-app` is Mightling in a window of its own, with an application-menu entry, a dock icon and no
browser tabs or address bar. Its main window has the same two views as
[`ling web`](web-chat.md#ling-web-ask-and-work-in-a-browser) in a browser: **Ask**, questions with
no project, and **Work**, the coding agent on your projects, one click apart. It is built the way
the best-known desktop coding agents are built: an Electron app with its own rendering engine and
the agent, `ling`, bundled inside, so typing stays smooth whatever graphics driver the machine has.

## Install and run

The easiest way is the `.deb` on the release page: it installs the app, its launcher entry and the
`ling-app` command. It needs no web chat: the app runs the agent itself.

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
client: Ask and Work run the `ling` bundled in the app against the model of the GB10 on your
network. It finds the GB10 the way `ling` does (the node must have run
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

## Ask and Work in the app window

The app window talks to one `ling app-server` of its own, the `ling` bundled in the app. Its
sidebar switches between the two views:

- **Ask**: a question with no project. Each question is a thread in a scratch folder of its own
  under `~/.mightling/ask/`, answered with the `ask` prompt (web search with numbered sources, your
  files, your apps). Attach images or files with the clip button or by pasting a screenshot; they
  are copied into the question's folder. Search, rename and archive questions in the sidebar.
- **Work**: projects and their threads, as below.

The app itself decides what an Ask thread is: the window only names the `ask` prompt, and the app
composes its text, makes the folder and keeps the thread inside it, with the same rules `ling web`
applies. So a question asked in the app and one asked in a browser live in the same place.

```bash
ling app --work               # open the app window on Work
ling app ~/my-project         # Work, with a new thread on that folder
ling app --thread <id>        # Work, on an existing thread
```

The menu's **Ask** entry (and `ling app` alone) still opens the separate Ask window, which shows
`ling web` on this machine (see [`ling web`](web-chat.md#ling-web-ask-and-work-in-a-browser));
the app starts it when nothing answers and stops it on quit. On Windows, where `ling web` is not
available yet, the menu's Ask opens the app window on Ask instead.

### Work

- **Threads grouped by project**, with replies, commands and their output, file changes and the
  turn's diff as they stream.
- **Approvals in the conversation**, Stop (or Esc), steering while a turn runs, and undo of the last
  turn.
- **Context use** with a Compress button, the served model and the `/airgapped` level.
- **A permission picker** that greys out Full Access at `/airgapped on`; the server itself refuses
  that combination for every client.
- **One app:** opening `ling app` again brings the running window forward; `mightling://thread/<id>`
  links open Work on that thread.
- **Ask one click away**, in the same window's sidebar.

Not there yet: review, git worktrees, settings pages and choosing a model (it is shown, not chosen).
Night Shift holds back while a Work turn is running, and the machine stays awake until it ends.

## Notes

- **No web chat needed.** Ask and Work run on the agent bundled in the app; the Ask window starts
  `ling web` itself when it is not running.
- **Nothing leaves the machine.** The app has no crash reporting, no update checks and none of the
  background services a browser engine normally talks to; `ling-admin audit egress --app` traces a
  whole session of it and lists every connection it made.
- **Fresh styling on every launch.** The window clears its cached pages each time it opens, so
  changes to the web chat's look show up immediately. You stay signed in.
- **Links open in your browser.** A link that would open a new tab (an external citation, for
  example) opens in the system browser; the window stays where it is.
- **Installed size.** The engine is bundled, so the package is larger than a window on the
  system's browser engine would be: see the release page for the current size.
