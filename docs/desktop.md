# Desktop app

`puffin-app` shows the [web chat](web-chat.md) in a window of its own, with an application-menu
entry, a dock icon and no browser tabs or address bar. It is a thin window around the same local
web chat. The window and the browser show the same conversations and the same features.

## Install and run

The web chat has to be running first (`puffin-admin puffin start`).

```bash
puffin-admin desktop install    # build tools: Rust, the Tauri CLI, GTK/WebKit headers (sudo once)
puffin-admin desktop run        # build if needed, register the launcher entry, open the window
```

After the first run, open it from your application menu, or with:

```bash
puffin app
```

`puffin-admin desktop status` reports whether everything needed to build and run it is present.
`puffin-admin desktop build` produces an installable `.deb` package and an AppImage.

## Notes

- **The web chat must be running.** If it is not answering, `puffin app` and
  `puffin-admin desktop run` say so and name the command that starts it, instead of opening an
  empty window.
- **Always light.** The window always uses the light theme, whatever your desktop theme is.
- **Fresh styling on every launch.** The window clears its cached pages each time it opens, so
  changes to the web chat's look show up immediately. You stay signed in.
- **New-tab links do nothing in the window.** A link that would open a new browser tab (an
  external citation, for example) is inert in the app. Open the web chat in a browser when you need
  those.
