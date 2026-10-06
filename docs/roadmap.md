# Roadmap

Work that is planned or designed but **not built yet**. Nothing here is a commitment to a date.

Already shipped and so no longer listed here: the code index (`puffin-code`, offered to the agent
as tools), Gmail, Google Drive and Calendar in `/apps`, the desktop app's Work window, and
observation masking with a per-output cap (built, off by default).

## The Work window, continued

*Status: Phase 1 built.*

The desktop app's **Work** window drives `puffin` sessions: threads, approvals, diffs, Stop, steer
and undo. Still to come: review, git worktrees, settings pages and choosing a model from the window.

## Keeping long tasks inside the context window

*Status: built, off by default; being measured.*

Unattended runs (Night Shift, benchmarks) fill the context mostly with old command output. Hiding
outputs older than the last few, and capping any single output, roughly halves how often a session
has to summarise itself on recorded runs. It becomes the default once a benchmark shows it does not
cost results.

## Setting up more GB10s

*Status: designed.*

After NVIDIA's first-boot wizard, one command on an existing node installs Puffin on new units,
applies the host settings, copies the model over the network and pairs them.

## Documents

*Status: idea.*

Search across PDFs, Word, PowerPoint and spreadsheet files as well as code:

- documents converted to Markdown locally, then indexed by section;
- editing spreadsheets and documents safely, with a backup and a diff each time.

## Returning features, locally

Features that Puffin hides because they depend on a vendor's cloud, kept in the build so they can
come back on local infrastructure:

- **`cloud`:** running tasks on a private cloud.
- **`remote-control`:** driving sessions from another device through a relay you host.
- **Voice in the terminal agent:** local speech-to-text is already running for the web chat.
