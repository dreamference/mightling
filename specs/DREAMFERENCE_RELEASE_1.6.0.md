# Mightling 1.6.0 — release notes

**Status:** draft, started 2026-10-08. Not released. Each change adds its own section; the text
between the two rules is the GitHub release's description.

**Checklist before publishing (Mac preview):**
- Dispatch the release with `build_clients` **and** `build_mac_preview` on; both dmgs are among the assets and listed in `SHA256SUMS`.
- The release description starts with the "Mac desktop app (preview, unsigned)" paragraph the workflow writes when a `-preview.dmg` is attached.
- On a Mac, if one is at hand: the dmg's checksum matches `SHA256SUMS`, the app opens after **Open Anyway** (or `xattr -dr com.apple.quarantine`), finds the node and shows Chat and Work (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §10.2).

---

### The desktop app on a Mac (preview)

**Mightling for macOS, as a preview.** The release now carries the desktop app for Macs:
`Mightling-1.6.0-arm64-preview.dmg` for Apple silicon and `Mightling-1.6.0-x64-preview.dmg` for
Intel, for macOS 12 or later. On a Mac the app is a client of your GB10: Chat shows the node's web
chat, and Work drives the `ling` bundled in the app against the node's model. It finds the node on
your network by itself, as `ling` does (run `ling-admin node enable` on the GB10 first).

**It is not signed with an Apple Developer ID and not notarized** (signed ad hoc only), so macOS
will not open it the first time. Check the dmg against the release's signed `SHA256SUMS`, drag
Mightling to Applications, then:
- try to open it once, and click **Open Anyway** in **System Settings → Privacy & Security**;
- on macOS 14 and earlier you can instead Control-click (or right-click) the app, choose **Open**,
  and confirm;
- or, in Terminal: `xattr -dr com.apple.quarantine /Applications/Mightling.app`.

Allow it when macOS asks whether Mightling may find devices on your local network; that is how it
reaches the GB10. The preview does not update itself, and `ling app` in a Mac terminal does not open
it yet. Full steps: the Desktop app page of the documentation.

---
