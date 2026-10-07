# Mightling 1.5.0 — release notes

**Status:** draft, 2026-10-07, on branch `rename/mightling`; not published. 1.5.0 is the first
release under the name Mightling. If `release/1.4.2` is published first, its notes stay as they are
and these follow; if not, merge 1.4.2's highlights into this description before publishing. The text
between the two rules is the GitHub release's description.

**Version.** Set to 1.5.0 when this is released, everywhere 1.4.x is set today: `setup.py`,
`dreamference/__init__.py`, the MCP server's `serverInfo`, `desktop/src-tauri/tauri.conf.json`, and
`desktop/src-tauri/Cargo.toml` with its lock. The release workflow stamps it into the binaries.

**Checklist before publishing:**
- **Both asset sets:** the release carries both `mling-<target>.gz` and `puffin-<target>.gz`, both checksum files and `codex-code-mode-host`.
- **Upgrade from a real 1.4.1 install** in a scratch HOME:
  1. `install.sh --version 1.4.1 --role client`;
  2. `puffin update`, which must install 1.5.0;
  3. `puffin` prints the notice, after which `mling --version` says 1.5.0, `puffin` is gone and `~/.mightling` holds the sessions.
- **The installer:** `install.sh` from 1.5.0 over a 1.4.1 install moves the folder and removes the old links.

---

**Puffin is now Mightling.** Same product, new name: an AI assistant already sells under the name
Puffin, and a confidential tool should not be confused with someone's cloud service. The bird stays.

**Updating from Puffin 1.4.x:**
1. Run `puffin update` one last time. It installs Mightling.
2. Run `puffin` once more. It moves your installation over and says so:
   - your sessions, history, settings and skills go to `~/.mightling`;
   - the binaries move to `~/.local/share/dreamference/mightling`;
   - the command links are replaced;
   - the `puffin_*` settings in `dreamference.toml` become `mightling_*`.

   No cloud sign-in or log is copied, and `~/.puffin` is left as it was.
3. From then on the command is **`mling`**. The old names are gone, not aliased.
4. **On a GB10 node,** run the installer again for **`mling-admin`**:
   `curl -fsSL https://github.com/dreamference/mightling/releases/latest/download/install.sh | bash`.
   Its first run moves the Night Shift timer, the network advertisement (which asks for sudo once) and paired machines' keys over to the new names.

| Before | Now |
|---|---|
| `puffin`, `puffin-search`, `puffin-fetch`, `puffin-code`, `puffin-app` | `mling`, `mling-search`, `mling-fetch`, `mling-code`, `mling-app` |
| `puffin-admin` | `mling-admin` |
| `puffin-admin puffin …` (the web chat) | `mling-admin chat …` (`onyx` still works) |
| `~/.puffin` | `~/.mightling` |
| `puffin_*` settings, `DREAMFERENCE_PUFFIN_*`, `PUFFIN_*` variables | `mightling_*`, `DREAMFERENCE_MIGHTLING_*`, `MIGHTLING_*` |
| Network service `_puffin-node._tcp` | `_mightling-node._tcp` |

**Mixed versions on one network:** a node and a client find each other only when both run
Mightling. A client that finds no node says so, and that a node still on Puffin needs
`puffin update` there.

**The desktop app** installs as Mightling and replaces the Puffin package.

---
