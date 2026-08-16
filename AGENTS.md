# Agent instructions

## You have web access

Shell commands, run the same way you run any other command:

```bash
scripts/searxng-search.sh "your query here"      # search: Title / URL / Snippet per result
scripts/websearch "your query here"              # same, with -n for result count
scripts/websearch --read "https://example.com"   # fetch a page as readable text
```

Either search command works — they query the same local SearXNG instance. Use
`scripts/websearch --read <url>` when snippets are not enough and you need the page itself.

Use them whenever the answer depends on something you cannot know: today's weather or tides,
current events, release versions, live documentation, anything dated. Search first, then `--read`
a promising URL from the results if the snippets are not enough.

Do **not** say you cannot browse the web. You can, through these commands.

Do not use `curl` or `wget` for this. They are frequently blocked by the sandbox and return nothing,
which looks like the site being down rather than the command being unavailable.

Flags: `-n N` for more results (default 5), `--max-chars N` for longer page text (default 8000).

### Why a command and not a tool call

There is no MCP search tool. There was one, and it worked as far as being registered and offered —
but Codex exposes MCP tools only inside its `exec` JavaScript runtime as
`tools.mcp__server__tool(...)`, and calling such a name directly fails with `unsupported call`.
Since that wrapping was unreliable in practice, search is a shell command instead. Use the commands
above; do not look for a search tool.

### How it works

Search runs against a SearXNG instance on this machine (`127.0.0.1:8888`), which queries upstream
engines on your behalf — no API key, no account, and no query addressed to a search company. If it
reports the instance is unreachable, the error names the command to restart it.

## Project

See `CLAUDE.md` for architecture, conventions and the model matrix. Tests:
`.venv/bin/python -m pytest tests/ -q`.
