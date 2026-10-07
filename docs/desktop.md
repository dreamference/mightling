# Desktop app

`mling-app` shows the [web chat](web-chat.md) in a window of its own, with an application-menu
entry, a dock icon and no browser tabs or address bar. It is a thin window around the same local
web chat. The window and the browser show the same conversations and the same features.

## Install and run

The web chat has to be running first (`mling-admin chat start`).

```bash
mling-admin desktop install    # build tools: Rust, the Tauri CLI, GTK/WebKit headers (sudo once)
mling-admin desktop run        # build if needed, register the launcher entry, open the window
```

After the first run, open it from your application menu, or with:

```bash
mling app
```

`mling-admin desktop status` reports whether everything needed to build and run it is present.
`mling-admin desktop build` produces an installable `.deb` package and an AppImage.

## The Work window

A second window, **Work**, drives the terminal agent from a desktop window, on your
GB10. It talks to one `mling app-server` of its own and needs no web chat:

```bash
mling app --work               # open Work
mling app ~/my-project         # Work, with a new thread on that folder
mling app --thread <id>        # Work, on an existing thread
```

- **Threads grouped by project**, with replies, commands and their output, file changes and the
  turn's diff as they stream.
- **Approvals in the conversation**, Stop (or Esc), steering while a turn runs, and undo of the last
  turn.
- **Context use** with a Compress button, the served model and the `/airgapped` level.
- **A permission picker** that greys out Full Access at `/airgapped on`; the server itself refuses
  that combination for every client.

Not there yet: review, git worktrees, settings pages and choosing a model (it is shown, not chosen).
Night Shift holds back while a Work turn is running.

## Notes

- **The web chat must be running.** If it is not answering, `mling app` and
  `mling-admin desktop run` say so and name the command that starts it, instead of opening an
  empty window.
- **Always light.** The window always uses the light theme, whatever your desktop theme is.
- **Fresh styling on every launch.** The window clears its cached pages each time it opens, so
  changes to the web chat's look show up immediately. You stay signed in.
- **New-tab links do nothing in the window.** A link that would open a new browser tab (an
  external citation, for example) is inert in the app. Open the web chat in a browser when you need
  those.
