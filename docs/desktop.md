# Desktop app

`ling-app` is Mightling in a window of its own, with an application-menu entry, a dock icon and no
browser tabs or address bar. Its main window has the same two views as
[`ling web`](web.md#ling-web-ask-and-work-in-a-browser) in a browser: **Ask**, questions with
no project, and **Work**, the coding agent on your projects, one click apart. It is built the way
the best-known desktop coding agents are built: an Electron app with its own rendering engine and
the agent, `ling`, bundled inside, so typing stays smooth whatever graphics driver the machine has.

## Install and run

On a Mac and on Windows, the one-line installer that installs `ling` installs the app too (from
1.7.2):

```bash
curl -fsSL https://github.com/dreamference/mightling/releases/latest/download/install.sh | bash
```

```powershell
irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1 | iex
```

The app is an unsigned preview, but a file a script downloads carries no browser quarantine mark
or Mark of the Web, so it opens without Gatekeeper's or SmartScreen's first-launch dialog.
`ling update` then updates the app with the terminal agent, from the same release and under the
same checks (it leaves the app alone while it is running: quit it and run `ling update` again).
`--no-app` (macOS) or `-NoApp` (Windows) leaves the app out.

On Linux, the easiest way is the `.deb` on the release page: it installs the app, its launcher
entry and the `ling-app` command. It needs no web server: the app runs the agent itself.

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

### From a zip or an unpacked folder (Linux)

Ubuntu 24.04 and later let a program create the user namespace Chromium's sandbox needs only if
an AppArmor profile names it, and the app ships no setuid sandbox helper. The `.deb` writes that
profile for `/usr/lib/mightling/Mightling`, and `ling-admin desktop install` writes one for a
checkout's build. Nothing installs a zip or a copied `Mightling-linux-<arch>/` folder, so without
a profile the app dies at start with **`No usable sandbox!`** (opened with `ling app`, nothing
appears at all). Before the first start, run the script beside the binary once:

```bash
cd ~/Mightling-linux-arm64          # wherever you unpacked it
./install-sandbox-profile.sh        # prints the two sudo commands, then runs them (sudo asks once)
./Mightling
```

The profile names the binary's path, so moving the folder needs the script run again (from the
new place; one unpacked copy at a time). `./install-sandbox-profile.sh --print` shows the profile
and changes nothing. A build older than the script needs the same profile written by hand, with
`APP` set to the binary's absolute path:

```bash
APP="$HOME/Mightling-linux-arm64/Mightling"
sudo tee /etc/apparmor.d/mightling-unpacked >/dev/null <<EOF
abi <abi/4.0>,
include <tunables/global>
profile mightling-unpacked "$APP" flags=(unconfined) {
  userns,
}
EOF
sudo apparmor_parser -r -T -W /etc/apparmor.d/mightling-unpacked
```

Never start it with `--no-sandbox` instead: that turns off the sandbox around every page the app
shows.

## On a Mac (preview)

The app runs on Apple silicon and Intel Macs, macOS 12 or later. On a Mac it is a client: Ask and
Work run the `ling` bundled in the app against the model of the GB10 on your network. It finds the
GB10 the way `ling` does (the node must have run `ling-admin node enable`); with several on the
network, choose one with `ling node use <name>`.

`install.sh` (above) installs it from the release's `Mightling-<version>-arm64-preview.dmg` or
`-x64-preview.dmg`, checked against the signed `SHA256SUMS`, into `/Applications` when your account
may write there (an administrator's may) and `~/Applications` otherwise. **It is not signed with an
Apple Developer ID and not notarized**: it carries an ad-hoc signature only (Apple silicon runs
nothing without one). Installed by the script, it carries no quarantine mark, so macOS opens it
without asking; a dmg downloaded in a browser is stopped the first time ([other ways to
install](#other-ways-to-install)).

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

The preview never asks for your keychain: it starts without the "Mightling Safe Storage" entry
that Chromium apps keep there (each unsigned build would ask again, and a start could wait on the
prompt with no window). The app keeps no password or token; the one thing that entry would protect
is the cookies of pages opened in the preview tab, which are stored without it. This will change
once the app is signed with a Developer ID.

What the preview does not do yet: `ling app` in a Mac terminal does not open it (open it from
Applications or Spotlight), it does not update itself (`ling update` does), and closing
its window quits it, as on Linux.

## On Windows (preview)

From 1.7.0 a release carries `Mightling-Setup-<version>-arm64.exe` (Windows on Arm) and
`Mightling-Setup-<version>-x64.exe` (x86-64) for Windows 11, with `ling.exe` inside, a client of a
Mightling node on your network. From 1.7.2, `install.ps1` (above) checks the one for your PC against
the release's signed `SHA256SUMS` and runs it silently: it installs for you alone, with no
administrator, under `%LOCALAPPDATA%\Programs\Mightling\App`, and adds a Start menu entry. The
installers are not signed, so Smart App Control must be off. `ling update` updates it with the
command-line client; to remove the app, use Settings → Apps → Installed apps.

## Other ways to install

The release page's dmg and Windows installer can also be downloaded and opened by hand. They are
unsigned previews, and a browser marks what it downloads, so macOS and Windows stop them the first
time. Check each download against the release's `SHA256SUMS`, which is signed, first:

```bash
shasum -a 256 ~/Downloads/Mightling-*-preview.dmg    # compare with the line in SHA256SUMS
```

On a Mac:

1. Open the dmg and drag **Mightling** onto **Applications**. Run it from there, not from the dmg.
2. Open it once (double-click). macOS says it cannot verify the app; click **Done** (or **OK**).
3. On macOS 15 (Sequoia) and later, open **System Settings → Privacy & Security**, scroll to
   *Security*, and click **Open Anyway** next to the line about Mightling. Confirm with your
   password. From then on it opens normally. On macOS 14 (Sonoma) and earlier, **Control-click**
   (or right-click) Mightling in Applications, choose **Open**, then **Open** again in the dialog.

Or, in Terminal, remove the quarantine mark the browser put on the download, which is what makes
macOS ask:

```bash
xattr -dr com.apple.quarantine /Applications/Mightling.app
```

On Windows, run the installer; SmartScreen warns the first time: **More info**, then **Run
anyway**. Smart App Control must be off (Windows Security → App & browser control → Smart App
Control settings).

## Ask and Work in the app window

The app window talks to one `ling app-server` of its own, the `ling` bundled in the app. Its
sidebar switches between the two views:

- **Ask**: a question with no project. Each question is a thread in a scratch folder of its own
  under `~/.mightling/ask/`, answered with the `ask` prompt (web search with numbered sources, your
  files, your apps). Attach images or files with the clip button or by pasting a screenshot; they
  are copied into the question's folder. Search, rename and archive questions in the sidebar.
- **Work**: projects and their threads, as below.

A file the agent writes for a question (a report, a page, a picture) shows in the answer as a card;
**Open** shows it beside the answer, and it opens there by itself when **Settings › General › Open a
document, PDF or image the agent creates** is on (the default). The question's header has **Files**
(Ctrl+Shift+E), its folder's tree beside the answer, and a button that opens the folder in your file
manager. Work never shows a question's files, and a question shows only its own.

**Quick Entry** asks a question from any app. Turn it on in **Settings › General › Quick Entry**: its
shortcut (Ctrl+Alt+Space until you press another in the field) opens a small box above your windows;
Enter asks the question in Ask and brings the app forward with the answer, Esc closes the box. The
tray icon's **New question…** opens the same box. On a Wayland session an app cannot take a
system-wide shortcut: bind `xdg-open ling://quick` to a key in your desktop's keyboard settings
instead. The shortcut works while the app runs; tick **Keep Mightling in the tray when its window
closes** so that closing the window leaves it running (Quit still ends it). Both are off until you
turn them on. `ling://ask?prompt=…` opens a new question with that text in the composer.

**Links in answers.** A web address in an answer (a cited source, a page the agent mentions) is a
link. Clicking it first shows its site and its full address; **Open in your browser** opens it in
your usual browser, **Copy the address** copies it. With `/airgapped on` nothing opens: the dialog
offers the copy only. In `ling web` the link opens in a new tab after the same question.

**Incognito questions.** **Incognito** in the header of a new question starts one that is not
saved: it is not listed, not kept as a session, not fed to the memories and not found by later
searches. A dashed frame and a note stay on it as a reminder. When you leave it (another question,
a new one, Work, or closing the app), the question, its answers and its folder with every file in
it are deleted for good. The desktop app only.

**Side chat.** **Ctrl+Alt+S** (or `/side`) opens a side chat beside the thread you are in: a free
chat that is not saved, with no folder of its own (it may read the thread's folder, and changes
nothing). Your first message in it carries a recap of the thread, its latest messages. Closing it,
or going to another thread, deletes it; **Keep as a thread** copies it into a saved thread first.
The saved recap of a thread, a copy of it asked where the work stands, is **Recap in a saved copy**
on the thread's menu.

**An AGENTS.md for a repository.** `/init` in Work asks the agent to read the repository (its
build, test and lint commands, its layout and conventions) and write a short `AGENTS.md` at its
root. When it already has one, the agent keeps it and edits only what is wrong or missing. Either
way the file is the turn's change, in the diff, under the thread's permissions: keep it or undo it
there. Words after `/init` go to the agent as your note.

**Earlier questions.** In **Settings › Memories**, **Let the agent search your earlier Ask
questions** (off until you turn it on) keeps a copy of each Ask question and its answers under
`~/.mightling/ask-history/` and gives the agent a skill to search and read them, on this machine and
read only, when you refer to something asked before. Incognito questions are never kept, archived
ones are removed, and turning it off deletes the copies.

**The composer.** A long paste (2,000 characters or 40 lines) becomes an attachment chip: in Ask a
file in the question's folder, in Work a block after your words (Settings › General turns this
off). With **Settings › Suggested prompts** on, the message you are likely to send next shows in
grey in the empty composer; Tab takes it. Every finished turn offers **Fork from here** (a new
thread with the conversation up to there) and **Edit in a new thread** (the conversation before
that message, the message in the composer to change), leaving the thread as it is. A queued
message can be edited and moved up or down.

**Summaries of finished chats.** A chat left alone for a day gets a two-line summary, what you
wanted and what came of it, written by this machine's model in a read-only thread that is not kept,
one chat at a time and only while nothing else is running. Point at the chat in the sidebar to see
it under the title; Search threads shows it too and finds a chat by it. A chat that changes is
summarized again a day later. **Settings › Suggested prompts** turns it off (it is on by default).

**Earlier versions of a message.** Editing a message, with **Edit** on the last one (or Esc Esc)
or **Edit in a new thread** on any, keeps the message as you sent it: the new words go to a copy of
the thread made before it, and the message then shows `‹ 2 / 3 ›`. Each version is the thread it was
sent in; the arrows open that thread as you left it. Nothing is deleted. An incognito question is
never copied, so its Edit still takes the message back.

The app itself decides what an Ask thread is: the window only names the `ask` prompt, and the app
composes its text, makes the folder and keeps the thread inside it, with the same rules `ling web`
applies. So a question asked in the app and one asked in a browser live in the same place.

```bash
ling app --work               # open the app window on Work
ling app ~/my-project         # Work, with a new thread on that folder
ling app --thread <id>        # Work, on an existing thread
ling app                      # the app window on Ask
```

### Several windows

**File › New Window** (Ctrl+Shift+N) opens another window, and each window shows its own question,
thread or project. All of them talk to the app's one agent server: no window starts a second one,
and everything a window sends is checked by the same rules. The menu's **Ask** and **Work** entries
(Alt+1 and Alt+2), the tray icon's and `ling app` bring the window you used last forward on the view
asked for, or open one if none is open; a second `ling app` or a `ling://` link never starts a
second copy of the app. The windows you had open when you quit come back where they were, on the
view they showed. Closing the last window quits the app, as before (unless Settings › General keeps
it in the tray).

A thread open in two windows has **one composer**: the window that showed it first, or the one it
was popped out into, sends its messages, answers its approvals and stops its turns. The other
window follows the thread read-only and says so above where the composer would be, with **Go to
that window** and **Use the composer here**, which moves the composer to it.

The **terminal** and the **diff** can each have a window of their own: the button in the terminal
panel's or the review pane's header, or **Window › Terminal in Its Own Window** and **Diff in Its
Own Window**. The detached pane stays live (its terminals are the same shells, its diff follows the
thread's turns); close its window, or press its **Dock** button, and it goes back where it was. A
new terminal opened from a detached pane runs with the thread's permissions, as one opened in the
thread's window does.

In 1.5 the menu's Ask opened a separate window on [`ling web`](web.md#ling-web-ask-and-work-in-a-browser),
which ran a second agent server on the same `~/.mightling`; the app no longer starts or uses
`ling web`. `ling web` itself is unchanged, for browsers and phones.

Shortcuts are written here as on Linux and Windows. On a Mac, Ctrl is the Command key (Cmd+P
searches files, Cmd+T opens a preview) and Alt is Option; Control+Tab still cycles threads and
Control+L still clears a terminal tab. Settings › Keyboard shortcuts shows each one with the Mac's keys.

### Light and dark

The app follows your computer's light or dark theme, and changes with it while it runs. To keep one
of them whatever the computer uses, open **Settings › Appearance** and pick **Light** or **Dark**
(**System** goes back to following the computer). The choice is kept for the next start, and every
window of the app, the pet's bubble included, changes at once. On the same page you can change the
accent, background and text colours of the theme in front; each theme keeps its own.

### Work

- **Threads grouped by project**, with replies, commands and their output, file changes and the
  turn's diff as they stream.
- **Approvals in the conversation**, Stop (or Esc), steering while a turn runs, and undo of the last
  turn.
- **Context use** with a Compress button, the served model and the `/airgapped` level.
- **A permission picker** with Read only, Workspace and Full Access, as in Codex. Full Access is
  greyed out only at `/airgapped on`; the server itself refuses that combination for every client.
- **`!` commands:** `!git status` in the composer runs the command in your own shell, in the
  thread's folder and with no sandbox, as in the Codex app, and shows its output in the thread. It
  is refused at `/airgapped on`, since nothing would keep it off the network.
- **One app:** opening `ling app` again brings the running window forward; `mightling://thread/<id>`
  links open Work on that thread.
- **Local or Worktree** under the composer of a new thread in a git project. **Local**, the
  default as in Codex's app, edits your checkout in place, so your editor sees every change.
  **Worktree** gives the thread its own checkout from the branch you pick; the thread's header says
  where its work is, and **Hand off** brings it to your local checkout. `/worktree on` in `ling`
  (or `ling worktree on`) makes Worktree the default here too.
- **Night Shift inside a thread:** `/night add <task>` in a Work thread (or Queue in Scheduled
  with "With what was said in …" ticked) queues the task for tonight and shows it in that thread
  as a card: queued, running, then done with its attempts and the branch chosen. The next morning
  the card carries the task's part of the night's report as a note, for you to read; it is never
  sent to the model. **Apply** runs `ling night apply` in the thread's folder, bringing the branch
  there as uncommitted changes, and **Open the result** opens the run in Scheduled.
- **Night results in the inbox:** each finished run in Scheduled's inbox stays unread until you open
  it, counted on Scheduled in the sidebar and in the tray's menu; **Mark all as read** clears them.
  `ling-admin night archive` (runs older than four weeks, or `--weeks N`) moves old runs' records,
  transcripts and reports to `~/.mightling/night/archive/`, keeping their branches, and Scheduled's
  **Archive** searches them.
- **Ask one click away**, in the same window's sidebar.

Not there yet: review, settings pages and choosing a model (it is shown, not chosen).
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
