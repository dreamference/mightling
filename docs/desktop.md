# Desktop app

`ling-app` is Mightling in a window of its own, with an application-menu entry, a dock icon and no
browser tabs or address bar. Its main window has the same two views as
[`ling web`](web.md#ling-web-ask-and-work-in-a-browser) in a browser: **Ask**, questions with
no project, and **Work**, the coding agent on your projects, one click apart. It is built the way
the best-known desktop coding agents are built: an Electron app with its own rendering engine and
the agent, `ling`, bundled inside, so typing stays smooth whatever graphics driver the machine has.

## Install and run

The easiest way is the `.deb` on the release page: it installs the app, its launcher entry and the
`ling-app` command. It needs no web server: the app runs the agent itself.

From a checkout:

```bash
ling-admin desktop install    # the app's packages (Node.js 22.13+ needed) and, once, a sandbox profile (sudo)
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

Work in a project under `~/Documents`, `~/Desktop` or `~/Downloads` waits on macOS's folder-access
dialog ("Mightling would like to access files in your Documents folder"): until you allow it, the
thread shows nothing and the agent cannot read the project (System Settings → Privacy & Security →
Files and Folders changes it later). Projects anywhere else in your home folder need no dialog.

The review pane, worktrees and commits need Apple's Command Line Tools, which give the Mac its `git`.
Without them the app never runs the `git` the Mac comes with (which would only pop up macOS's offer
to install the tools, in any folder): those features show as unavailable, with a hint, and work as
soon as the tools are there. A `git` of your own on the app's PATH, such as Homebrew's, is used
instead. Install the tools once in Terminal:

```bash
xcode-select --install
```

What the preview does not do yet: `ling app` in a Mac terminal does not open it (open it from
Applications or Spotlight), it does not update itself (download the new dmg), and closing its
window quits it, as on Linux.

## On Windows (preview)

From 1.7.0 a release carries `Mightling-Setup-<version>-arm64.exe` (Windows on Arm) and
`Mightling-Setup-<version>-x64.exe` (x86-64) for Windows 11, with `ling.exe` inside, a client of a
Mightling node on your network. Check the installer against the release's `SHA256SUMS`, then run it:
it installs for you alone, with no administrator, under `%LOCALAPPDATA%\Programs\Mightling\App`,
and adds a Start menu entry. The installers are not signed: SmartScreen warns the first time (More
info, then Run anyway), and Smart App Control must be off. To update, run the next release's
installer over it; to remove it, use Settings → Apps → Installed apps. `ling update` and
`install.ps1` update the command-line client only, not the app.

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
ling app                      # the app window on Ask
```

There is one window. The menu's **Ask** and **Work** entries (Alt+1 and Alt+2), the tray icon's
and `ling app` bring it forward on the view asked for; none opens a second window. In 1.5 the
menu's Ask opened a separate window on [`ling web`](web.md#ling-web-ask-and-work-in-a-browser),
which ran a second agent server on the same `~/.mightling`; the app no longer starts or uses
`ling web`. `ling web` itself is unchanged, for browsers and phones.

Shortcuts are written here as on Linux and Windows. On a Mac, Ctrl is the Command key (Cmd+P
searches files, Cmd+T opens a preview) and Alt is Option; Control+Tab still cycles threads and
Control+L still clears a terminal tab. Settings › Keyboard shortcuts shows each one with the Mac's keys.

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

- **No web server needed.** Ask and Work run on the agent bundled in the app, one agent server for
  the whole app; `ling web` does not have to be running.
- **Nothing leaves the machine.** The app has no crash reporting, no update checks and none of the
  background services a browser engine normally talks to; `ling-admin audit egress --app` traces a
  whole session of it and lists every connection it made.
- **Fresh pages on every launch.** `ling app` clears the window's cached pages each time it opens
  Ask, so an upgraded app never shows a page from before the upgrade.
- **Links open in your browser.** A link that would open a new tab (an external citation, for
  example) opens in the system browser; the window stays where it is.
- **Installed size.** The engine is bundled, so the package is larger than a window on the
  system's browser engine would be: see the release page for the current size.
