# Roadmap

Work that is planned or designed but **not built yet**. Nothing here is a commitment to a date.

## First release

The release pipeline builds the Python package, the desktop app (`.deb` and AppImage) and the
`puffin` binaries for arm64, and attaches them to a GitHub release. Once the first release is
published, `puffin update` installs new releases in place, after verifying their checksums.

## Code index for the agent

*Status: design proposed.*

Today the agent explores code with text search and file reads, one round at a time. On a large
repository that is slow, costs many tokens, and misses calls made through traits, generics or
re-exports. The design adds `puffin code`, backed by two layers:

- **A fast, always-current index for every language.** It is built on
  [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) (MIT): 158 languages,
  call graphs, and search by meaning, all local.
- **Compiler-exact references** from SCIP indexers (rust-analyzer, Pyright and others) wherever a
  project builds.

Every answer says whether it is exact or approximate. The agent is told to double-check before a
rename or signature change whenever any part of the answer is approximate.

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
