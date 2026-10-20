# Python Code Quality — a Stanford-grade standard for `dreamference/`, enforced by a ratchet

**Status:** Phase 0 built (2026-10-03, §10). **Phase 1 built (2026-10-20, §11) without its two tree-wide commits:** the ratchet is in the suite (`tests/test_code_quality.py` for ruff, the formatter and the §3.9 checks; `tests/test_typing.py` for mypy), the baselines recorded on the tree as it stood, and the seven dead shims deleted; `ruff format` and `ruff check --fix` over everything (§6, Phase 1 steps 1 and 2) were not run, by the owner's decision, because many branches were in flight, so the formatter is ratcheted per file (§11). §2's numbers were measured earlier on `main` at `584f26e` with flake8 7.3 and an AST script; §10.1 gives ruff's counts at Phase 0 and §11.2 at Phase 1.
**Goal:** make the Python half of Mightling read like code written to a teaching standard: small, deep modules, one idea per function, names that make comments unnecessary for the *what* and comments that carry the *why*. The standard is checked by tools wherever a tool can decide, by a fixed review checklist where it cannot, and it may only get stricter over time.
**Scope:** `dreamference/` and `tests/` (Python). Not the Rust crates (`ling-rs/`, `ling-web-rs/`, `ling-code-rs/`, which have `cargo clippy`), not `desktop/`, not the `codex/` submodule (never modified), not `scratch/`.

---

## 1. What "Stanford-level" means here

There is no single Stanford coding standard. Two Stanford sources between them cover what a code base needs, and they are adopted together, with the second winning where they disagree:

1. **CS106A's style guide** ([CS106A Style Guide](https://web.stanford.edu/class/archive/cs/cs106a/cs106a.1258/resources/style_guide.html); Nick Parlante's [Python Style Basics](https://cs.stanford.edu/people/nick/py/python-style-basics.html)): descriptive snake_case names, a docstring for every file and function, inline comments only for code that is not obvious, each function doing "one conceptual thing", named constants instead of magic numbers, functions "preferably less than 10 lines" and "the levels of indentation to 3 or fewer", stated there as guidelines, not strict rules. It is PEP 8 taught as habit.
2. **John Ousterhout's CS 190, Software Design Studio**, as written up in *A Philosophy of Software Design* (2nd ed., 2021): complexity is the enemy; modules should be **deep** (a simple interface over a lot of functionality); information should be hidden in one place; comments describe what the code cannot say. Its list of **red flags** is the review checklist of §5.

**Where they disagree, CS 190 wins.** CS106A's "less than 10 lines" is a rule for first programs. Applied to a code base, it produces what CS 190 calls *classitis* and *shallow modules*: many tiny functions whose interfaces are as complicated as their bodies, and a reader who has to jump between them to follow one idea. This standard therefore limits **complexity and nesting** hard (§3.4) and uses **length** only as a trigger for a look (§5), not as a limit.

PEP 8 and PEP 257 are the base layer, and the **Google Python Style Guide's docstring format** stays, because it is already the project's norm (AGENTS.md "Conventions").

---

## 2. Where the code is today (measured)

`dreamference/`: 123 files, 31,965 lines, 914 functions, 108 classes. `tests/`: 37 files, 11,391 lines.

| Measure | Value | Comment |
|---|---|---|
| Function length | median 17 lines, p90 48, max **1,265** | 81 functions over 50 lines, 12 over 100 |
| McCabe complexity (flake8 `C901`) | 44 functions above 10, 15 above 15, 5 above 25 | |
| The worst five by complexity | `DreamferenceCLIController.run_cli` **228** (1,265 lines); `GmailSearchService.serve` 37; `VLLMServerManager.build_launch_command` 33; `ContainerDiagnostics.get_diagnostics_line` 30; `VLLMServerManager.start_server` 28 (367 lines) | `run_cli` alone is a fifth of the problem |
| Other long functions | `build_parser` 414 lines (complexity 1: a flat list, which is fine); `DreamferenceConfig.__init__` 224; `ModelDownloader.tensorize_model` 191; `OnyxRunner.configure` 160 (deleted with Onyx in 1.7.0) | |
| Public functions without a docstring | 56 of 674 | the norm is already high |
| Unannotated parameters | 8 of 1,219 | annotations are already the norm; **`Any` appears 373 times** |
| `except Exception` | 111 | many are deliberate (host safety must not crash a watchdog), none say so |
| bare `except:` | 2 | |
| Lines over 100 / 120 characters | 983 / 216 | |
| Other flake8 findings | 23 unused imports, 10 f-strings without placeholders, 22 `if x: y` on one line, 2 unused variables, ~140 whitespace and blank-line findings | mechanical |
| One class per file | 2 files break it: `chat/image_search_service.py` (7 classes), `cli/code_index_setup.py` (2) | |
| Dead shim modules | 7 (`dreamference/{cli,config,hardware,runner,context_engine,mcp_server,vllm_server}.py`), each a `from … import *` shadowed by its package (AGENTS.md "Gotchas") | they are the source of the 7 `F403` star-import findings |
| Tooling | flake8 7.3 in `.venv`; no ruff, mypy, pyright or formatter; no `pyproject.toml`; no test job in `.github/workflows/` (only `docs.yml`, `release.yml`) | |

Two readings follow. The **habits are already good** (docstrings, annotations, `Final` constants, one class per file): what is missing is a check that keeps them, and the cleanup of a handful of very large functions. And **the problem is concentrated**: five functions hold most of the complexity, so the refactoring phase (§6, Phase 3) is a short list, not a rewrite.

---

## 3. The rules a tool decides

All of these are enforced by **ruff** (lint and format, one binary, pinned) and **mypy**, configured in a new `pyproject.toml` that holds tool settings only (`setup.py` stays the build file). Rule codes are ruff's.

### 3.1 Layout
- `ruff format`, line length **100**. Long strings and comments the formatter cannot break are allowed up to **120** (`E501` with `max-line-length = 120`).
- Imports sorted and grouped (`I`): standard library, third party, `dreamference`.
- Modern syntax for Python 3.12 (`UP`): `dict[str, X]` and `X | None`, not `Dict`/`Optional`; `typing.Final` stays.

### 3.2 Names (CS106A, PEP 8)
- `N` (pep8-naming): snake_case functions and variables, CapWords classes, UPPER_SNAKE constants.
- `A` (builtins): no name shadows a builtin (`id`, `type`, `input`, `dir`, `format`).
- Single-letter names only for loop indices and comprehension variables (`E741` bans the ambiguous ones; the rest is §5's *vague name*).

### 3.3 Documentation (CS106A "a comment for each function", PEP 257, Google style)
- `D` with `convention = "google"`: every module and every public class, method and function has a docstring; `Args:`/`Returns:`/`Raises:` where they exist.
- Private helpers (`_name`) need a docstring only when the name does not say what they do; that is §5, not a tool.
- No commented-out code (`ERA`).

### 3.4 Decomposition (CS106A "one conceptual thing", CS 190 deep modules)
| Rule | Limit | Code |
|---|---|---|
| McCabe complexity | **10** | `C901` |
| Branches per function | 12 | `PLR0912` |
| Statements per function | 50 | `PLR0915` |
| Arguments per function | 6 (`self`/`cls` not counted; keyword-only arguments are the escape) | `PLR0913` |
| Return statements | 6 | `PLR0911` |
| Nested blocks | **3** (CS106A's indentation limit) | `PLR1702` |

Function **length** has no hard limit, for §1's reason; a function over 40 lines (about the p90 today) is a review trigger (§5). Data, not logic, is exempt from both: a flat table such as `build_parser` or `ModelMatrixRegistry.MATRIX` is long and simple, and is the right shape.

### 3.5 Constants (CS106A "avoid magic numbers")
- `PLR2004`: no unnamed numbers or strings in comparisons. Module constants are `UPPER_SNAKE: Final[...]`, as today. `0`, `1`, `-1`, `""` and HTTP status codes through `http.HTTPStatus` are allowed.

### 3.6 Errors
- No bare `except:` (`E722`).
- `except Exception` (`BLE001`) only with a stated reason on the line: `# noqa: BLE001 — <why>`. The reasons this code base actually has are kept and named, e.g. "a watchdog thread must never die", "a probe that fails means 'unknown', not a crash".
- No `try: … except …: pass` (`S110`, `SIM105`): either handle it, log it, or use `contextlib.suppress` with the exception named.
- `raise … from err` inside `except` (`B904`).

### 3.7 Correctness lints (cheap, high yield)
`F` (pyflakes), `B` (bugbear: mutable default arguments, loop-variable closures), `SIM` (simplifiable conditionals), `RET` (return consistency), `PTH` (`pathlib` over `os.path`, in new code), `C4` (comprehensions), `PIE`, `RUF`.

### 3.8 Types
- **mypy**, raised one package at a time (§6, Phase 4) to `strict = true`.
- `Any` (`ANN401`) is not allowed in a **public** signature. Inside a function it is allowed where JSON from an outside service arrives, and should be narrowed to a `TypedDict` at the boundary where its shape is known (Onyx's admin API, `/v1/models`, the registry's `launch_overrides`).

### 3.9 Project conventions, checked by the ratchet test (§4) because no linter knows them
- **One class per file**, the file named after the class in snake_case (AGENTS.md). Exempt: a file that is copied into a container and run there as one script; the exemption is a list in the test, each entry with its reason.
- **`__init__.py` is a facade** with an explicit `__all__` and no logic.
- **No module-level logic** beyond constants, imports and `__all__`.
- **`print` only in `cli/`** and in the console-facing methods it calls; long-running services (`gmail_search_service`, `image_search_service`, the MCP server, `diffusion_openai_service`) log through `logging` (`T201` with per-file ignores).

---

## 4. Enforcement: a ratchet, in the test suite

The project already guards limits with tests (`test_the_patches_stay_small`). Code quality uses the same mechanism, so it needs no CI job and runs in the existing ~8-second suite.

- **`tests/test_code_quality.py`** runs `ruff check --output-format json` over `dreamference/` and `tests/`, counts findings **per file and per rule**, and compares them with **`tests/quality_baseline.json`**.
  - A count that **rises** fails, naming the file, the rule and the new finding.
  - A **new file** has a baseline of zero: new code meets the whole standard from its first commit.
  - A count that **falls** passes and prints the command that lowers the baseline (`… --update-baseline`), so cleanup is locked in by the commit that does it.
  - The same test runs `ruff format --check` (no baseline: once Phase 1 has formatted everything, it is all or nothing; until that commit is made it is ratcheted per file, §11) and the checks of §3.9.
- **mypy** gets the same treatment in `tests/test_typing.py`, per package, with a baseline per package that may only fall; a package that reaches zero is moved to `strict` and its entry removed.
- **The tools are pinned dev dependencies** (`extras_require={"dev": ["ruff==<x>", "mypy==<y>"]}`, installed with `.venv/bin/pip install -e .[dev]`). The tests **fail**, with that command in the message, when the tools are missing: a skip would let the standard lapse silently on any machine that lacks them. They run offline, as the rest of the suite does.
- **Exceptions are visible.** Every `# noqa` carries a rule code and a reason (`RUF100` removes stale ones). A per-file ignore in `pyproject.toml` carries a comment. No blanket `# type: ignore`; always `# type: ignore[code]`.
- **Optional:** a `pre-commit` hook running `ruff format` and `ruff check --fix` on staged files. It is a convenience; the test is the gate.

---

## 5. The rules a reviewer decides: CS 190's red flags

A tool cannot tell a deep module from a shallow one. Each change touching `dreamference/` is read against this list, by a human or by `/code-review`, and a finding names the flag:

| Red flag | What it looks like here |
|---|---|
| **Shallow module** | A class or function whose interface is about as complicated as its body: a wrapper that forwards five arguments to one call |
| **Information leakage** | Two modules that both know one fact: a container name, a port, a path layout, a config key. The fact belongs in one constant or one method (as `TIFFANY_BLUE` and `ONYX_LOGO_PATHS` already are) |
| **Temporal decomposition** | Code split by *when* things happen (`step1`, `step2`) instead of by what each piece knows |
| **Overexposure** | Callers must understand a rarely-used feature to use a common one |
| **Pass-through method** | A method that only calls another with the same arguments |
| **Repetition** | The same few lines in several places (the four runners' health gates are the known candidate) |
| **Special-general mixture** | A general mechanism that contains one caller's special case |
| **Conjoined methods** | Two functions that cannot be understood without reading each other |
| **Comment repeats code** | A comment saying what the next line plainly says |
| **Implementation contaminates interface** | A docstring describing how, where callers need what |
| **Vague name** | `data`, `info`, `result`, `tmp`, `handle`, `process` for something that has a precise name |
| **Hard to pick a name / hard to describe** | The function does two things; split it or merge it |
| **Nonobvious code** | Behaviour a reader cannot predict from a quick read, with no comment saying why |

Two triggers for a closer look, not limits: a function over **40 lines**, and a class over **15 public methods**.

**Comments that carry history stay.** This code base keeps hard-won findings in comments and docstrings ("found the hard way", why a sidecar starts before vLLM, why a Next.js chunk needs a trailing newline). That is exactly CS 190's kind of comment (it says what the code cannot) and §3's rules must never be satisfied by deleting it.

---

## 6. Phases

Every phase is **behaviour-preserving**: the full suite passes before and after, with no test changed except to add one.

**Phase 0: configure and measure (no code change).** Add `pyproject.toml` with §3's rule set and `extras_require["dev"]`. Run ruff and mypy and record the real baseline, which replaces §2's flake8 numbers. Confirm the container-script exemption list of §3.9 (whether `image_search_service.py` is copied into a container as one file). Decide any rule that turns out noisy here, in writing, in this spec.

**Phase 1: mechanical** (steps 3 and 4 built 2026-10-20, steps 1 and 2 not yet: §11). One commit each, listed in `.git-blame-ignore-revs` so `git blame` skips them:
1. `ruff format` over everything.
2. `ruff check --fix` with **safe** fixes only (imports, whitespace, pyupgrade, f-strings without placeholders).
3. Delete the seven dead shim modules. Before deleting, a test asserts that `import dreamference.hardware` (etc.) resolves to the package's `__init__.py`, which is what AGENTS.md says already happens.
4. Land `tests/test_code_quality.py` with the baseline.

The formatting commit touches nearly every file, so it is made **when no other branch is open** (on 2026-10-03 there were six worktrees with work in flight); a branch opened before it is rebased with `ruff format` run on its side first.

**Phase 2: the cheap findings.** Docstrings for the 56 public functions, the 2 bare `except`s, a reason on each of the 111 `except Exception`s (or a narrower exception where one is known), unused variables, the two multi-class files. Baseline falls with each commit.

**Phase 3: the five functions.** One function per change, tests first where coverage is thin:
1. **`run_cli`** (complexity 228, 1,265 lines): a dispatch table from subcommand to one handler method per command group (`_cmd_server`, `_cmd_node`, `_cmd_night` …), each handler under the §3.4 limits. The parser (`build_parser`) stays a flat table.
2. **`build_launch_command`** (33): one method per argument family (engine, memory, speculation, parsers), composed in order. The registry stays the source of truth for flags (`docs/dev/models-and-engines.md`).
3. **`start_server`** (28, 367 lines): split along its existing stages (pre-flight, sidecar, launch, watch). **Both host-safety layers are preserved** and their order is unchanged; this is the riskiest refactor here and gets a test per stage boundary before it is touched.
4. **`GmailSearchService.serve`** (37): routing table plus one handler per endpoint.
5. **`ContainerDiagnostics.get_diagnostics_line`** (30), then `DreamferenceConfig.__init__` (224 lines: the 4-tier resolution is one rule applied per field; make it a table of fields and one resolver).

**Phase 4: types.** mypy package by package, from the pure ones out: `config/` → `hardware/` → `context_engine/` → `node/` → `night_shift/` → `vllm_server/` → `runner/` → `mcp_server/` → `chat/` → `cli/`. Each package reaches `strict` before the next starts.

---

## 7. Done when

- `ruff format --check` and `ruff check` pass with an **empty** baseline for `C901`, `PLR0912`, `PLR1702`, `E722` and `D`, and a baseline for every other rule that is lower than at Phase 0.
- mypy `strict` passes for `config/`, `hardware/` and `context_engine/` at least.
- No function in `dreamference/` has complexity above 10 except the ones listed by name, with a reason, in `pyproject.toml`.
- AGENTS.md "Conventions" points at this spec and names the command that runs the checks.
- The suite still runs offline and in about the same time.

---

## 8. Not proposed

- **A rewrite, or a style change for its own sake.** Code that meets §3 is not touched.
- **CS106A's 10-line limit as a hard rule** (§1).
- **pylint** in addition to ruff: ruff implements the pylint rules this standard uses, one tool is easier to pin, and it is fast enough to sit in the suite.
- **Black** separately: `ruff format` is Black-compatible.
- **A coverage threshold.** Coverage measures what ran, not what was checked; this spec is about readability. Worth a spec of its own if wanted.
- **The Rust crates.** `cargo clippy -D warnings` per crate is the analogue and belongs in their own specs.
- **Handing this standard to `ling` itself** (as a skill or prompt block for the agent's own output). Possible later, and measurable with SWE-bench; out of scope here.

---

## 9. Risks

- **Churn against parallel work.** The format commit conflicts with every open branch (§6, Phase 1 says when to make it).
- **Refactoring host-safety code** (`start_server`). A change in the order of the pre-flight, the sidecar start and the watchdog can freeze the host (`docs/dev/host-safety.md`). Mitigation: per-stage tests first, and no change to the order.
- **Rules that do not fit.** `PLR2004` may be noisy against the registry's numeric tables, `T201` against the CLI's voice. Phase 0 decides with real counts, and a per-file ignore with a reason is an acceptable answer.
- **Over-decomposition.** The limits of §3.4 can be met by splitting a function into shallow pieces, which §5 flags. The reviewer's checklist is part of the standard, not decoration.

---

## 10. Phase 0, as built (2026-10-03)

- **`pyproject.toml`** holds tool settings only, with no `[build-system]` table, so pip and `python -m build` keep building from setup.py through setuptools' legacy backend. It selects §3's rules; `PLR1702` (nested blocks) is a ruff preview rule, turned on alone with `explicit-preview-rules`. The rules the formatter owns (`W191`, `E111`, `E114`, `E117`, `D206`, `D300`) are ignored so that `ruff format` and `ruff check` cannot disagree.
- **Pinned** in setup.py: `extras_require={"dev": ["ruff==0.16.10", "mypy==2.4.0"]}`. The release workflow's test job installs `.[dev]`. `.ruff_cache/` and `.mypy_cache/` are ignored.
- **Decided here, as §6 asked:**
  - **Tests are exempt** from the docstring rules (`D100`–`D107`), `PLR2004`, `T201` and `ANN401`: a test's name is its documentation, and an assert compares literal expected values. All other rules apply to `tests/`.
  - **`T201` (print) stays ratcheted, not ignored.** Its 502 findings sit in console-facing managers the CLI calls (`onyx_runner.py` 77, `swe_bench_command.py` 33, `vllm_server_manager.py` 31, …), which §3.9 allows. Phase 2 decides per module: a per-file ignore with a reason for the console-facing ones, `logging` for the long-running services.
  - **`PLR2004` keeps strings in scope** (`allow-magic-value-types = []`), as §3.5 says; 127 of its 420 findings are in `run_cli`, which Phase 3 rewrites anyway.
  - **The container-script exemption** from one-class-per-file is confirmed for `chat/image_search_service.py` (staged as one file into a `python:3-slim` container), `chat/gmail_search_service.py` and `vllm_server/diffusion_openai_service.py` (bind-mounted entrypoint). `cli/code_index_setup.py` (two classes) is not exempt.
  - **mypy is measured, not yet in the suite.** A cold run takes 19 s, more than twice the suite, so `tests/test_typing.py` arrives with Phase 4 and uses mypy's incremental cache.

### 10.1 The baseline, ruff beside §2's flake8

ruff 0.16.10 over `dreamference/` and `tests/`, at `3624947`: **5,452 findings in 162 files** (5,014 in `dreamference/`, 438 in `tests/`). 2,766 have a safe automatic fix, 785 an unsafe one, 1,901 are manual. `ruff format --check`: 130 files would be reformatted, 38 already are. The ratchet's own project checks (§3.9) add 2: one second class (`cli/code_index_setup.py`) and one `__init__.py` without `__all__` (the top-level `dreamference/__init__.py`, which holds `__version__`); no module does work at import time.

| Measure (`dreamference/`) | flake8 / AST (§2, `584f26e`) | ruff (`3624947`) | Note |
|---|---|---|---|
| Complexity above 10 (`C901`) | 44 | **40** | 13 above 15, 5 above 25: `run_cli` **213** (was 228), `GmailSearchService.serve` 33, `build_launch_command` 33, `SweBenchRunner.run` 29 (new since §2), `start_server` 27, `get_diagnostics_line` 25 |
| Other decomposition limits | — | `PLR1702` nesting 105, `PLR0913` arguments 19, `PLR0911` returns 17, `PLR0912` branches 14, `PLR0915` statements 12 | new measures; nesting is the largest |
| `except Exception` (`BLE001`) | 111 | 112 | plus `S110` try/except/pass 45, `SIM105` 23 |
| bare `except:` (`E722`) | 2 | 2 | |
| Lines over 120 (`E501`) | 216 | 219 | the 100–120 band is the formatter's job, not a finding |
| Missing docstrings, public (`D100`–`D107`) | 56 functions | 34 (`D103` 17, `D100` 9, `D102` 6, `D101`, `D107`) | ruff counts what the Google convention requires; §2 counted every public function |
| Docstring format (`D212`, `D205`) | not measured | 945, 270 | the house style puts the summary on the second line; `D212` is safe-fixable, so Phase 1 settles it |
| Old typing syntax (`UP006`, `UP045`, `UP035`) | not measured | 1,349 in the `UP` family | `Dict`/`Optional` → `dict`/`X \| None`, safe-fixable in Phase 1 |
| `os.path` (`PTH`) | not measured | 494 | §3.7 asks for `pathlib` in new code; the ratchet keeps old code's count from rising |
| Magic values (`PLR2004`) | not measured | 420 | see above |
| `Any` in a signature (`ANN401`) | 373 uses of `Any` anywhere | 48 in signatures | the rule covers signatures only |
| Unused imports, empty f-strings, one-line `if` | 23, 10, 22 | 19, 10, 23 | |
| Star imports (`F403`) | 7 | 7 | the dead shims |

mypy 2.4.0, default strictness, `ignore_missing_imports`: **89 errors in 29 of 120 files**: `cli` 25, `chat` 17, `node` 14, `swe_bench` 12, `night_shift` 7, `vllm_server` 7, `mcp_server` 5, `runner` 5, `hardware` 2, `audit` 1. `config/` and `context_engine/` have none, which is where Phase 4 starts.

### 10.2 The ratchet test, written and held for Phase 1

`tests/test_code_quality.py` and `tests/quality_baseline.json` implement §4 as written: ruff's findings and the PQ checks of §3.9 (PQ001 one class per file, PQ002 `__init__.py` without `__all__`, PQ003 work at import time), counted per file and rule; a rise fails and names it; a new file counts from zero; a fall prints the update command; a ruff other than the pinned one fails with the install command; `--update-baseline` refuses to raise a count unless given `--allow-raise`. It runs in 0.7 s and passes against the tree it recorded.

It is **on the branch `quality/phase0`, not on `main`**, because of what it would do to work in flight: new code written in today's house style (`Optional[...]`, the summary on a docstring's second line, `os.path`) fails it from its first commit, while AGENTS.md tells every author to match the surrounding style. Checked against the branches open on 2026-10-03: `fleet/provision`'s new `node/fleet_session.py` would fail with 7 rule counts above zero (`D212` 15, `D205` 11, `UP045` 11, `UP006` 8, `PLR2004` 4, `UP035`, `PTH123`), and `swe/runtime-lzma` with one (`UP006` 2→3 in `swe_bench_runtime.py`); `ctx/budget` and `tests/watchdog-leak` would pass. Phase 1's safe-fix commit converts the tree to the style the rules ask for; the test lands right after it with a re-recorded baseline, which is §6's order anyway (step 4).

---

## 11. Phase 1, as built (2026-10-20)

The owner's decision of 2026-10-20: land the ratchet now, **without** the `ruff format` and `ruff check --fix` commits (§6, Phase 1 steps 1 and 2), because a change touching nearly every file would conflict with every branch in flight that day. Step 3 (the shims) and step 4 (the test) are built; steps 1 and 2 remain for a day with no branch open, and each is then one commit listed in `.git-blame-ignore-revs`, followed by `--update-baseline` (the counts only fall).

- **`tests/test_code_quality.py`** is §10.2's test with three changes. **The formatter is ratcheted per file**, not all or nothing as §4 says: `ruff format --check` gives one `ruff-format` finding per file it would change, counted like a rule, so the 179 files unformatted on 2026-10-20 are in the baseline and every other file, a new one included, must stay formatted. A fall is reported through `warnings.warn` (shown in `pytest -q`'s summary, where a passing test's `print` is not). A rise names the file and the rule and says that `ruff format <file> && ruff check --fix <file>` fixes most findings in new code (§10.2's `D212`, `UP006`, `UP045`, `I001` are all safe fixes).
- **`tests/test_typing.py`** is §4's mypy ratchet, landed now rather than with Phase 4: mypy 2.4.0 with `[tool.mypy]`'s settings, errors only (notes such as `annotation-unchecked` are not counted), per file and error code like ruff, against **`tests/typing_baseline.json`**. §10's objection was its cold time; it uses mypy's incremental cache in `.mypy_cache/` at the checkout's root, so the first run in a fresh checkout or worktree takes about 14 s and every later one 0.4 s. The per-package view of §4 is the sum over a package's files; a package that reaches zero is moved to `strict` in `pyproject.toml` (Phase 4).
- Both fail, never skip, when a tool is missing or not the pinned version, with the install command (`.venv/bin/pip install -e .[dev]`); the release workflow's test job installs `.[dev]`.
- **The seven shims are deleted** (`dreamference/{cli,config,context_engine,hardware,mcp_server,runner,vllm_server}.py`), with a test that each name resolves to its package's `__init__.py` and that no module of that name comes back. That removed the 7 `F403` findings.
- **Time:** both ratchets add 1.2 s to a warm suite (ruff 0.1 s, the format check 0.1 s, mypy 0.4 s, plus process start-up), 14 s more on a checkout's first run.

### 11.1 Running and lowering

```bash
.venv/bin/python -m pytest tests/test_code_quality.py tests/test_typing.py -q
.venv/bin/python tests/test_code_quality.py --update-baseline   # after a cleanup: ruff, format, PQ
.venv/bin/python tests/test_typing.py --update-baseline         # after a typing fix
```

`--update-baseline` refuses to raise a count; `--allow-raise` writes anyway and the commit says why (re-recording on a rebase over others' merges is the expected case).

### 11.2 The baseline at Phase 1

ruff 0.16.10 on `main` at the merge: **7,957 findings in 210 files** (7,063 in `dreamference/`, 894 in `tests/`), of which 179 are `ruff-format` (one per unformatted file). The count has grown by 46 % since §10.1's 5,452. The largest families: `UP` 2,137 (`UP006` 1,170, `UP045` 696), `D212` 1,222, `PTH` 858, `PLR2004` 643, `E501` 609, `T201` 549, `D205` 547. Decomposition: `C901` 63, `PLR1702` 141, `PLR0911` 25, `PLR0912` 25, `PLR0913` 25, `PLR0915` 20. Errors: `BLE001` 119, `S110` 48, `SIM105` 39, `E722` 2. Missing public docstrings 34 (`D103` 17, `D102` 12, `D100` 2, `D107` 2, `D101` 1). Project checks: `PQ001` 1 (`cli/code_index_setup.py`), `PQ002` 1 (`dreamference/__init__.py`).

Complexity above 25: `run_cli` **224**, `SweBenchRunner.run` 40, `GmailSearchService.serve` 33, `build_launch_command` 33, `start_server` 29, `get_diagnostics_line` 25; then `save_config` and `SweBenchIssueTargets.conditions` 23, `tensorize_model` 21, `image_search_service.serve` 20. `SweBenchRunner.run` has overtaken the rest of §6 Phase 3's list and belongs on it.

The files with the most findings: `cli/dreamference_cli_controller.py` 463, `vllm_server/vllm_server_manager.py` 259, `runner/codex_branded_builder.py` 210, `chat/gmail_search_service.py` 205, `swe_bench/swe_bench_report.py` 191, `swe_bench/swe_bench_runner.py` 189, `audit/egress_audit.py` 187, `chat/image_search_service.py` 165. By the decomposition rules alone: the CLI controller 56, `swe_bench_runner.py` 25, `swe_bench_instance_run.py` 21, `gmail_search_service.py` 20, `vllm_server_manager.py` 16.

mypy 2.4.0: **136 errors in 40 files** (89 in 29 at Phase 0): `swe_bench` 35, `cli` 25, `vllm_server` 25, `node` 17, `chat` 13, `night_shift` 9, `runner` 7, `hardware` 2, `audit`, `config` and `mcp_server` 1 each; `context_engine` none. By code: `arg-type` 50, `union-attr` 28, `assignment` 20, `attr-defined` 10. `config/` has gained one since Phase 0, so Phase 4 starts with `context_engine/`, or with that one fix.

---

## Sources

- [CS106A Style Guide](https://web.stanford.edu/class/archive/cs/cs106a/cs106a.1258/resources/style_guide.html), Stanford, read 2026-10-03.
- Nick Parlante, [Python Style Basics — PEP8](https://cs.stanford.edu/people/nick/py/python-style-basics.html), Stanford.
- John Ousterhout, *A Philosophy of Software Design*, 2nd ed. (Yaknyam Press, 2021), the text of Stanford's CS 190; red flags as summarised at the end of the book.
- [PEP 8](https://peps.python.org/pep-0008/), [PEP 257](https://peps.python.org/pep-0257/), the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) (docstring format).
- [ruff rules](https://docs.astral.sh/ruff/rules/) for the codes in §3.
