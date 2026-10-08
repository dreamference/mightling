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
