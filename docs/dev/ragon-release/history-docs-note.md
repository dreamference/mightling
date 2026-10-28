The note for the top of history documents (decision 13), as one line under the title of every spec
and of each dated dev note the script renames or leaves:

```markdown
> Mightling was renamed Ragon in 1.9.0. Names below that describe the past keep their old form.
```

The spec header the script writes is the shorter form in the spec (`Mightling was renamed Ragon in
1.9.0.`); this longer line is for the dated documents the script leaves untouched (the release notes
of 1.4 to 1.8, `docs/dev/sessions.md`, `paper/puffin.*` has none: it is a PDF). A document that
already carries the line is not given a second.

For `docs/dev/install-modes.md`, "Old release names", add:

```markdown
Mightling was renamed Ragon in 1.9.0 (2026-11; `specs/DREAMFERENCE_RENAME_RAGON.md`). 1.9.0 publishes
three asset sets from the same binaries: `ragon-*`, `ling-*` (for 1.8.x updaters) and `puffin-*` (for
1.4.x updaters); from 1.9.1 only `ragon-*`. The migration (`ragon-rs/src/rename.rs`,
`dreamference/cli/legacy_name_migration.py`) handles all three layouts.
```

For `AGENTS.md`, the line in "Project" becomes: "The product was called Puffin until 1.5 and
Mightling until 1.9 (renamed 2026-11, spec `specs/DREAMFERENCE_RENAME_RAGON.md`)."
