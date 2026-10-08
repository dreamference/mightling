# Searching this codebase

Developer notes behind the search line in `AGENTS.md`.

`ast-grep` (v0.45) is installed and is the right tool for structural queries ("every call site of this shape", "every method with this signature"), which `rg` can only approximate with regex. The repetition across the four runners makes these queries genuinely useful here.

**Always constrain the output.** `ast-grep` prints the *entire matched AST node*, so any pattern matching a declaration dumps the whole body. Measured on this repo: `-p 'class VLLMServerManager'` prints **1,306 lines**; `rg -n "^class VLLMServerManager"` prints **1**. The default output is a token bomb.

Use this form instead; it collapses matches to `file:line` (offset verified against `rg`):

```bash
ast-grep -p '<pattern>' -l python dreamference/ --json=compact \
  | jq -r '.[] | "\(.file):\(.range.start.line + 1)"'
```

On `def $N(cls, $$$A)` (25 matches) that is **1,223 bytes vs. 74,553** for the bare command, 61x smaller. Use `--files-with-matches` (465 bytes) when only the file set matters.

Patterns verified working on this tree:

```bash
'subprocess.call($$$)'              # 5 hits — every agent launch point
'self.vllm_manager.check_health()'  # 4 hits — the health gate each runner shares
'resolve_model_hf_repo($ARG)'       # 15 hits — alias→HF-repo translation sites
'shutil.which($X)'                  # 19 hits — every CLI-presence probe
'def $N(cls, $$$A)'                 # every classmethod
```

**Use `rg` for locating a definition** (`rg -n "^class Foo"` → one line, ~7 ms) and `ast-grep` for enumerating call sites by shape. Reaching for `ast-grep` to find a single symbol is strictly worse.

The code index, `ling-code`, answers definition, reference and caller queries across languages; see [code-index.md](code-index.md).
