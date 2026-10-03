# Roadmap

Work that is planned or designed but **not built yet**. Nothing here is a commitment to a date.

Already shipped and so no longer listed here: the first release (v1.3.0, published 2026-10-02,
installed in place by `puffin update`) and the code index (`puffin-code`, offered to the agent as
tools).

## Gmail, Google Drive and Calendar in `/apps`

*Status: designed; the scopes tested with Google.*

Codex's `/apps` without an OpenAI sign-in, listing Puffin's own apps: Gmail, Google Drive (My Drive
and shared drives) and Google Calendar, all read-only, connected through the local Google sign-in so
the tokens stay on your machine, and offered to the agent as tools rather than shell commands.

## A Codex-style desktop app

*Status: designed.*

Today's desktop window stays as **Chat**. A second window, **Work**, drives the terminal agent the
way OpenAI's Codex app does: threads, approvals, diffs, review and worktrees, all on your GB10.

## Keeping long tasks inside the context window

*Status: designed and measured on recorded runs.*

Unattended runs (Night Shift, benchmarks) fill the context mostly with old command output. Hiding
outputs older than the last few, and capping any single output, roughly halves how often a session
has to summarise itself.

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

Codex features that Puffin hides because they depend on OpenAI's servers, kept in the build so
they can come back on local infrastructure:

- **`cloud`:** running tasks on a private cloud.
- **`remote-control`:** driving sessions from another device through a relay you host.
- **Voice in the terminal agent:** local speech-to-text is already running for the web chat.
