# Roadmap

Work that is planned or designed but **not built yet**. Nothing here is a commitment to a date.

Already shipped and so no longer listed here: the code index (`ling-code`, offered to the agent
as tools), the local document index (`ling-docs`, [Your documents](documents.md)), Gmail, Google
Drive and Calendar in `/apps`, the desktop app's Work window (including review, git worktrees and
settings pages), setting up more GB10s (`ling-admin node provision`), and observation masking with
a per-output cap (built, off by default).

## The Work window, continued

*Status: Phases 1–3 built.*

The desktop app's **Work** window drives `ling` sessions: threads, approvals, diffs, review, git
worktrees, settings pages, Stop, steer and undo. Still to come: choosing a model from the window.

## Keeping long tasks inside the context window

*Status: built, off by default; being measured.*

Unattended runs (Night Shift, benchmarks) fill the context mostly with old command output. Hiding
outputs older than the last few, and capping any single output, roughly halves how often a session
has to summarise itself on recorded runs. It becomes the default once a benchmark shows it does not
cost results.

## Documents

*Status: indexing and search shipped ([Your documents](documents.md)); editing is an idea.*

- editing spreadsheets and documents safely, with a backup and a diff each time.

## Returning features, locally

Features that Mightling hides because they depend on a vendor's cloud, kept in the build so they can
come back on local infrastructure:

- **`cloud`:** running tasks on a private cloud.
- **`remote-control`:** driving sessions from another device through a relay you host.
- **Voice in the terminal agent:** local speech-to-text already serves the microphone in the app and
  in `ling web` (`ling-admin voice start`).
