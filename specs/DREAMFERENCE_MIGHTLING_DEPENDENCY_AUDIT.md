# Dependency audit, October 2026

**Status:** audit of `main` at `47d5328` (2026-10-09), fixes merged into `main` (2026-10-09).
Every lockfile and dependency declaration in the repository was checked against the public
advisory databases. What could be fixed without a breaking change was fixed here; the rest is
listed with the reason it was left. The `ling-engine` submodule is out of scope.

## 1. Tools

| Ecosystem | Tool | Version | Database |
| --- | --- | --- | --- |
| Rust | `cargo audit` | 0.22.2 (the upstream aarch64 release binary) | RustSec advisory-db, fetched 2026-10-09 |
| Python | `pip-audit` | 2.10.1, in a throwaway virtualenv | PyPI / OSV; severities from the GitHub advisory of each alias |
| npm | `npm audit` | npm 11.17.0, Node 24.19.0 | npm registry (GitHub advisories) |
| GitHub Actions | read by hand | | |

## 2. What was audited

**Rust.**

- Standalone crates with their own lockfile, built `--locked`: `ling-web-rs/`, `ling-code-rs/`,
  `ling-docs-rs/`, and `ling-docs-rs/eval/rustpdf/` (a measurement harness, not shipped).
- The Codex workspace, two ways, never inside `codex/`: (a) the submodule's `codex-rs/Cargo.lock`
  at the pinned commit `064c6b8`, taken as a file; (b) the lockfile `ling` is actually built with:
  the pinned `codex-rs` exported to a scratch directory, `ling-rs/` copied in as `codex-rs/ling`,
  the 23 patches applied, and the lockfile completed by `cargo metadata` (no `--locked`, as the
  builder does). Beyond the `ling-*` crates themselves, (b) adds 12 third-party packages to (a)
  (`mdns-sd` and its socket helpers; `ferrisetw`, `junction` and the `windows` 0.57 crates, all
  Windows only; `zerocopy` 0.7) and moves `libc` and `fastrand` up a patch release. Both report
  the same findings.

**Python.**

- `setup.py`'s `install_requires` and `dev` extra, three ways: each declared minimum taken
  literally (`pkg==floor`), a fresh resolution of the declarations (what `install.sh` gets on a new
  machine; 90 packages, resolved with `pip install --dry-run --report`, nothing installed), and the
  development virtualenv `.venv/` as it stands.
- `ling-docs-rs/eval/requirements.txt` (the Phase 0 measurement environment).

**npm.** `desktop/electron/`, `desktop/ui/` and `ling-code-rs/indexers/`, the three folders with a
`package-lock.json`.

**GitHub Actions.** Every `uses:` in `.github/workflows/`.

## 3. Summary

| Where | Critical | High | Moderate | Low | Fixed here |
| --- | --- | --- | --- | --- | --- |
| Rust, standalone crates | 0 | 0 | 0 | 0 | nothing to fix |
| Rust, Codex workspace | 0 | 3 | 1 | 0 | no (report only, §4.2) |
| Python, declared minimums | 2 | 14 | 9 | 0 | **all 25**, by raising five floors |
| Python, fresh resolution | 0 | 0 | 0 | 0 | |
| npm, `desktop/electron` | 1 | 12 | 4 | 1 | no (§6) |
| npm, `desktop/ui`, `ling-code-rs/indexers` | 0 | 0 | 0 | 0 | |
| GitHub Actions on a mutable tag | | | | | none found |

Counts are distinct advisories per package. `npm audit` itself reports `desktop/electron` as
34 vulnerable packages (1 critical, 26 high, 4 moderate, 3 low), because it counts every package on
the path to a vulnerable one; the 18 advisories behind them are in §6.

Critical or high and not fixed: the three high Rust advisories in the Codex workspace (none
reachable as built, §4.2) and the electron build tooling's `tar`, `extract-zip`, `tmp` and `braces`
(development and release machines only, never shipped in the app, §6).

## 4. Rust

### 4.1 Standalone crates

No vulnerabilities in `ling-web-rs`, `ling-code-rs`, `ling-docs-rs` or `rustpdf`. Two warnings, with
no patched release to move to:

| Package | Advisory | Kind | Where | Status |
| --- | --- | --- | --- | --- |
| `paste` 1.0.15 | RUSTSEC-2024-0436 | unmaintained | `ling-docs-rs`, through `tokenizers` | not fixed: no successor release; a compile-time macro |
| `ttf-parser` 0.25.1 | RUSTSEC-2026-0192 | unmaintained | `ling-docs-rs/eval/rustpdf` | not fixed: measurement harness only |

No lockfile was changed, so there was nothing to re-test.

### 4.2 Codex workspace (report only)

Changing these needs the submodule's lockfile, which is never modified, and verifying a bump needs
the full Codex build, so each is recorded for the next Codex bump.

| Package | Advisory | Severity | Where | Status |
| --- | --- | --- | --- | --- |
| `hickory-proto` 0.25.2 | RUSTSEC-2026-0118, GHSA-3v94-mw7p-v465: NSEC3 proof validation loops forever | High | the network proxy's DNS resolver (`rama-dns` → `hickory-resolver`) | Not fixed: no patched 0.25.x (unaffected from 0.26.0-beta.1). Not reachable as built: the loop is in DNSSEC validation, and `rama-dns` enables only `tokio` and `system-config`, never a `dnssec-*` feature |
| `hickory-proto` 0.25.2 | RUSTSEC-2026-0119, GHSA-q2qq-hmj6-3wpp: quadratic name compression when encoding | Medium | same | Not fixed: needs 0.26.1, and upstream pins every `rama-*` crate to `=0.3.0-alpha.4`, which requires `hickory-resolver` 0.25. Waits on a Codex bump. The resolver encodes only its own small queries |
| `quick-xml` 0.39.4 | RUSTSEC-2026-0194: quadratic duplicate-attribute check | High (CVSS 7.5) | `plist` 1.9.0 ← `syntect` (the TUI's syntax themes) and `wayland-scanner` 0.31.10 (the TUI's clipboard, a build-time code generator) | Not fixed. Both inputs are files compiled into the binary, not data from outside. The workspace's own `quick-xml` is already 0.41.0. A semver-compatible fix exists for the next bump: `plist` 1.10.1 and `wayland-scanner` 0.31.11 move to `quick-xml` ≥ 0.41 |
| `quick-xml` 0.39.4 | RUSTSEC-2026-0195: unbounded namespace declarations in `NsReader` | High (CVSS 7.5) | same | Same as above |

Warnings (not vulnerabilities), all in the Codex workspace:

| Package | Advisory | Kind | Patched | Note |
| --- | --- | --- | --- | --- |
| `event-listener` 5.4.1 | RUSTSEC-2026-0221 | unsound | 5.4.2 | semver-compatible, next bump |
| `faster-hex` 0.10.0 | RUSTSEC-2026-0306 | unsound | 0.10.1 | through `gix`; semver-compatible, next bump |
| `memmap2` 0.9.10 | RUSTSEC-2026-0186 | unsound | 0.9.11 | through `gix`; semver-compatible, next bump |
| `scc` 2.4.0 | RUSTSEC-2026-0205 | unsound | 3.8.4 | through `serial_test`, tests only |
| `spin` 0.9.8 | | yanked | | through `flume` (required by `mdns-sd`, `rama-net` and `sqlx-sqlite`) and `heapless` 0.7 |
| `atomic-polyfill` 1.0.3 | RUSTSEC-2023-0089 | unmaintained | | through `heapless` 0.7 |
| `bincode` 1.3.3 | RUSTSEC-2025-0141 | unmaintained | | through `syntect` |
| `derivative` 2.2.0 | RUSTSEC-2024-0388 | unmaintained | | through `starlark` (exec policy) |
| `fxhash` 0.2.1 | RUSTSEC-2025-0057 | unmaintained | | through `starlark_map`, `bm25` |
| `paste` 1.0.15 | RUSTSEC-2024-0436 | unmaintained | | through `starlark`, `v8` |
| `proc-macro-error2` 2.0.1 | RUSTSEC-2026-0173 | unmaintained | | through `age` (secrets) |

The launcher's own `mdns-sd` also reaches `spin` through `flume`, but Codex's crates reach it too,
so dropping `mdns-sd` would not clear it; every item in this table waits on the Codex workspace.

## 5. Python

### 5.1 Declared minimums (fixed)

A fresh install was already clean, because `install.sh` resolves to current releases. The floors in
`setup.py` still allowed vulnerable releases, so a machine with older packages, or a resolver
constrained by something else, could keep one. Five floors were raised to the first release
without a published advisory. The development virtualenv already has a newer release of each
(requests 2.34.2, sentence-transformers 5.7.0, sqlite-vec 0.1.9, fonttools 4.63.0, Pillow 12.3.0),
so the change only records what the code already runs on; the test suite
(`pytest tests/ -q -k "not codex_branded_builder"`: 930 passed, 89 skipped) passes against it, and
`pip-audit` finds nothing in the new floors.

| Package | Advisory | Severity | Old floor | Status |
| --- | --- | --- | --- | --- |
| `sentence-transformers` | CVE-2026-68770 (GHSA-jhr6-gm9c-rqjv), security-control bypass to code execution | Critical | 3.0.0 | Fixed: `>=5.6.0` |
| `Pillow` | CVE-2023-50447 (GHSA-3f63-hfp8-52jq), `ImageMath.eval` code execution | Critical | 10.0.0 | Fixed: `>=12.3.0` |
| `Pillow` | CVE-2023-4863 / CVE-2023-5129 (GHSA-j7hp-h8jx-5ppr) and PYSEC-2023-175, the bundled libwebp heap overflow (two entries, counted as two) | High | 10.0.0 | Fixed |
| `Pillow` | CVE-2024-28219 (GHSA-44wm-f244-xhp3), `_imagingcms` buffer overflow | High | 10.0.0 | Fixed |
| `Pillow` | CVE-2026-54058, -54059, -54060, -55379, -55380, -59197, -59199, -59200, -59204, -59205 (ten out-of-bounds reads and writes, decompression bombs and native crashes in font, image and PDF decoders; GHSA-62p4-gmf7-7g93, -8v84-f9pq-wr9x, -5x94-69rx-g8h2, -45hq-cxwh-f6vc, -phj9-mv4w-65pm, -xj96-63gp-2gmr, -6r8x-57c9-28j4, -jjj6-mw9f-p565, -vjc4-5qp5-m44j, -9hw9-ch79-4vh6) | High | 10.0.0 | Fixed |
| `Pillow` | CVE-2026-42308, CVE-2026-42310, CVE-2026-55798, CVE-2026-59198 (font advance overflow, PDF hang, Windows viewer command, TGA encoder over-read) | Moderate | 10.0.0 | Fixed |
| `sqlite-vec` | CVE-2024-46488 (GHSA-vrcx-gx3g-j3h8), heap overflow in `npy_token_next` | High | 0.1.0 | Fixed: `>=0.1.3` |
| `requests` | CVE-2023-32681, CVE-2024-35195, CVE-2024-47081, CVE-2026-25645 (proxy header leak, `verify=False` reuse, `.netrc` leak, predictable temp file) | Moderate | 2.28.0 | Fixed: `>=2.33.0` |
| `fonttools` | CVE-2025-66034 (GHSA-768j-98cg-p3fv), `varLib` arbitrary file write | Moderate | 4.50.0 | Fixed: `fonttools[woff]>=4.60.2` |

`pyyaml`, `toml`, `rich`, `tensorizer`, `einops`, `beautifulsoup4` and the pinned `ruff` and `mypy`
have no advisory at their floors.

### 5.2 Fresh resolution and the measurement environment

No known vulnerabilities in the 90 packages a new install resolves to (including `torch`,
`transformers`, `huggingface_hub`, `urllib3`, `idna` and `soupsieve`), nor in
`ling-docs-rs/eval/requirements.txt`.

### 5.3 The development virtualenv

`.venv/` holds 17 vulnerable packages (175 advisories), among them `litellm`, `gitpython`,
`aiohttp`, `django`, `starlette` and `pyjwt`. None is declared by `setup.py` and nothing under
`dreamference/` or `tests/` imports any of them: they are leftovers of other tools installed into the
same environment and do not reach a user. Recreating `.venv/` from `pip install -e .[dev]` would drop
them; it was not done here.

## 6. npm

`desktop/ui/` and `ling-code-rs/indexers/`: no vulnerabilities.

`desktop/electron/`: every finding is in `devDependencies`, behind `@electron-forge/*` 7.11.2, the
packaging tool. Forge runs on a developer machine and in the release workflow; Vite bundles the
app, and none of these packages is in the shipped application. `npm audit fix` without `--force`
changes nothing (checked on a copy of the lockfile): the only fix npm offers is Forge 8.0.1, a
major version, and `braces` has no fixed release at all.

| Package | Advisory | Severity | Path | Status |
| --- | --- | --- | --- | --- |
| `tar` 6.2.1 | GHSA-23hp-3jrh-7fpw, unbounded decompression | Critical | `@electron/rebuild`, `@electron/node-gyp`, `cacache` | Not fixed: needs `tar` 7.5.21+, a major version under Forge 7; Forge 8 is a breaking upgrade |
| `tar` 6.2.1 | GHSA-34x7-hfp2-rc4v, -8qq5-rm4j-mr97, -83g3-92jg-28cx, -qffp-2rhf-9h96, -9ppj-qmqm-q256, -r6q2-hw4h-h46w, -8x88-c5mf-7j5w, -r292-9mhp-454m (path traversal and overwrite through links, a macOS race, loops and recursion) | High | same | Same. It extracts headers and prebuilt binaries from the npm registry and Electron's release host while native modules are rebuilt |
| `tar` 6.2.1 | GHSA-vmf3-w455-68vh, -w8wr-v893-vjvp, -gvwx-54wh-qm9j | Moderate | same | Same |
| `extract-zip` 2.0.1 | GHSA-jmr9-qjv8-65gv, GHSA-7pqw-9j4j-h8q3, symlink traversal and file write | High | `@electron/packager` | Not fixed: no fixed 2.x; Forge 8 |
| `tmp` 0.0.33 | GHSA-ph9p-34f9-6g65, path traversal through prefix | High | `external-editor` ← Forge's prompts | Not fixed: Forge 8 |
| `tmp` 0.0.33 | GHSA-52f5-9888-hmc6, symlinked `dir` | Low | same | Same |
| `braces` 3.0.3 | GHSA-vfj7-8cjw-p6xm, stack exhaustion | High | `micromatch` ← `fast-glob` ← Forge | Not fixed: no fixed release exists |
| `sprintf-js` 1.1.3 | GHSA-hp3w-g68c-fv3c, unbounded precision | Moderate | `roarr` ← `global-agent` ← `@electron/get` 3 | Not fixed: Forge 8 |

The way to clear these is moving `desktop/electron` to Electron Forge 8 as its own change, with the
`.deb` and the Mac preview rebuilt and checked; it is a toolchain upgrade, not a security patch.

## 7. GitHub Actions

Every third-party action in `.github/workflows/` is pinned to a full commit SHA with the tag as a
comment, `dtolnay/rust-toolchain` included. The remaining `uses:` are this repository's reusable
workflows and `./codex/.github/actions/setup-msvc-env`, a local composite action fixed by the
submodule commit that runs a script and calls no other action. None found on a mutable tag.

## 8. Re-running

- Rust: `cargo audit -f <Cargo.lock>` on each standalone lockfile; for Codex, audit a copy of the
  pinned `codex-rs/Cargo.lock` and of an export completed as in §2, never inside `codex/`.
- Python: `pip-audit -r <file> --no-deps --disable-pip` on the floors written as `==` pins and on a
  `pip install --dry-run --report` resolution of `setup.py`'s requirements.
- npm: `npm audit` in each folder with a `package-lock.json`; `npm audit fix --package-lock-only` on
  a copy first to see what it would change.
