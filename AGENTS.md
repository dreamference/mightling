# Agent instructions

## You have web access

```bash
mling-search "your query here"              # search; -n N for more results (default 5)
mling-fetch "https://example.com"           # fetch a page as readable text
```

Do not use `curl` or `wget` for this — the sandbox usually blocks them, which looks like the site
being down rather than the command being unavailable. There is no web search *tool*; search is a
shell command.

The full instructions are appended to the system prompt by the `mling` launcher
(`WEB_ACCESS_INSTRUCTIONS` in `mling-rs/src/lib.rs`), so they apply in every workspace, not only this one — that is the
reason this section is a pointer rather than a copy.

## Project

See `CLAUDE.md` for architecture, conventions and the model matrix. Tests:
`.venv/bin/python -m pytest tests/ -q`.
