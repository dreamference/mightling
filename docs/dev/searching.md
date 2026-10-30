# Searching this codebase

Developer notes behind the search line in `AGENTS.md`.

`ast-grep` (v0.45) is installed and is the right tool for structural queries ("every call site of this shape", "every method with this signature"), which `rg` can only approximate with regex. The repetition across the four runners makes these queries genuinely useful here.

**Always constrain the output.** `ast-grep` prints the *entire matched AST node*, so any pattern matching a declaration dumps the whole body. Measured on this repo (2026-10-29): `-p 'class VLLMServerManager'` prints **2,009 lines**; `rg -n "^class VLLMServerManager"` prints **1**. The default output is a token bomb.

Use this form instead; it collapses matches to `file:line` (offset verified against `rg`):

```bash
ast-grep -p '<pattern>' -l python dreamference/ --json=compact \
  | jq -r '.[] | "\(.file):\(.range.start.line + 1)"'
```

On `def $N(cls, $$$A)` (1,314 matches) that is **60,346 bytes vs. 3,277,315** for the bare command, 54x smaller. Use `--files-with-matches` (6,739 bytes) when only the file set matters.

Patterns verified working on this tree:

```bash
'subprocess.call($$$)'              # 13 hits — the four runners' launches, plus the desktop runner, the Codex builder and test runner and the CLI
'self.vllm_manager.check_health()'  # 3 hits — the health gate of Cline, Continue and OpenHands (not Codex)
'resolve_model_hf_repo($ARG)'       # 14 hits — alias→HF-repo translation sites
'shutil.which($X)'                  # 64 hits — every CLI-presence probe
'def $N(cls, $$$A)'                 # every classmethod
```

**Use `rg` for locating a definition** (`rg -n "^class Foo"` → one line, ~7 ms) and `ast-grep` for enumerating call sites by shape. Reaching for `ast-grep` to find a single symbol is strictly worse.

The code index, `ling-code`, answers definition, reference and caller queries across languages; see [code-index.md](code-index.md).
