# Mightling sandbox: the home folder hidden from commands

**Status:** draft for 1.7.0 on branch `security/sandbox-home`, not merged; it waits for the owner's review and the decisions in §9. It answers finding 13 of the [October 2026 security review](./DREAMFERENCE_SECURITY_REVIEW_2026-10.md). Built and unit-tested; the live bubblewrap check outside PyCharm is §8.2.
**Goal:** under the read-only and workspace-write sandboxes, a command `ling` runs cannot read the user's home folder: SSH keys, browser profiles, `~/.mightling`'s sessions and secrets, other projects. It still sees the workspace, the paths the user allowed, the toolchains it needs and ling's own tools.
**Target:** `ling` on Linux (bubblewrap). macOS (Seatbelt) and Windows (the elevated sandbox) are unchanged.
**Related:**
- [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md) §14: the same hook point in Codex's sandbox helper, for the network.
- [MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md): the patch series and its size cap.
- [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md) §18: the node's job sandbox, which already hides the home folder behind an empty tmpfs.

---

## 0. Decisions, stated first

1. **Deny by default, with an allowlist.** Everything outside the home folder reads as before. Inside it, a command sees only the workspace, the paths listed in §2 and the paths the user adds. A denylist of known secrets was rejected because it cannot hide "other projects". It would also go stale inside `~/.mightling`, and Codex's bubblewrap builder cannot carve a readable path back out of a denied folder (§3.2).
2. **The rule is applied in Codex's sandbox helper**, which every sandboxed command passes through, by one line beside the air-gap hook in patch `0019`. The logic is the leaf crate `ling-rs/sandbox-home` (`ling-sandbox-home`). Configuration alone cannot do it (§3.2).
3. **Only the user-level file configures it**, as a `[sandbox]` table in `~/.config/dreamference/config.toml`. A repository's `dreamference.toml` is never read for it, because a sandboxed command can write that file.
4. **Full Access and `/airgapped` are unchanged.** Full Access has no sandbox. `/airgapped on` still takes the network, decided first, from the same helper.

## 1. The finding

Codex's built-in profiles on Linux are `:read-only` (`:root = read`) and `:workspace` (`:root = read`, the workspace, `/tmp` and `$TMPDIR` writable). Bubblewrap mounts them as `--ro-bind / /` plus the writable binds, so any command the model runs can read whatever the user can read. A prompt injected through a fetched page, a dependency's README or an email can therefore have the agent `cat ~/.ssh/id_ed25519`, `~/.config/dreamference/gmail/credentials.json` or `~/.mightling/history.jsonl` and carry the contents out through `ling-fetch`, `curl` or a commit. At `/airgapped on` the second step is closed but the first is not. Writes were never the issue: outside the workspace everything is read-only.

## 2. What is hidden, what stays readable, and why

The home folder (`$HOME`, resolved to its real path) becomes an empty directory. Into it, read-only:

| Readable | Why |
|---|---|
| The workspace (the sandbox's working directory) and every writable root (`--add-dir`, `sandbox_workspace_write.writable_roots`) | The work itself. Codex mounts the writable roots as before; for the read-only sandbox, `:project_roots = read` is added. |
| The repository that holds the working directory | A session started in a subfolder still runs `git` and sees its whole repository. |
| A git worktree's shared git folder (`commondir`) | Night Shift's worktrees need the main repository's objects and refs. Only `<main>/.git` is readable, not the main checkout's files. |
| `.cargo`, `.rustup`, `.local/bin`, `.local/lib`, `.local/share/uv`, `.pyenv`, `.virtualenvs`, conda folders, `.nvm`, `.npm`, `.bun`, `.deno`, `.volta`, `.local/share/pnpm`, `go`, `.gradle`, `.m2`, `.sdkman`, `.rbenv`, `.ghcup`, `.opam`, `.elan`, `.juliaup`, `.local/share/mise`, and the caches `.cache/{pip,uv,pypoetry,yarn,go-build}` | Toolchains and package caches (`DEFAULT_READABLE`). A path that does not exist costs nothing. |
| Every folder on the command's `PATH` that lies in the home folder; the virtualenv a `PATH` folder belongs to; for each program there that is a link or a `#!` script, the folder it really lives in (its virtualenv, its `.nvm` version folder, else its own folder unless that is a shared one such as `~/.local/share`, then the file alone) | Programs the user's shell finds keep working. |
| The folders named by `CARGO_HOME`, `RUSTUP_HOME`, `VIRTUAL_ENV`, `CONDA_PREFIX`, `PYENV_ROOT`, `NVM_DIR`, `PNPM_HOME`, `BUN_INSTALL`, `VOLTA_HOME`, `DENO_DIR`, `GOPATH`, `GOMODCACHE`, `SDKMAN_DIR` | Toolchains installed somewhere else. |
| `.gitconfig`, `.config/git` | Commits carry the user's identity. |
| `.local/share/dreamference/mightling` (ling's binaries, the indexers), `.cache/codebase-memory-mcp` (the code index's graph stores), `.config/dreamference/config.toml` | `ling-code` queries run inside the sandbox; `ling-search`, `ling-fetch` and `ling-code` read the user-level file. Nothing else in `~/.config/dreamference` (Gmail tokens, the service secret, pairing and signing keys). |
| The sandbox helper's own folder (`current_exe`) | The helper re-enters itself inside bubblewrap to apply seccomp. |
| In `$CODEX_HOME`: `skills`, `plugins`, `shell_snapshots`, `tmp`, `airgapped`, `ling-code.toml`; and the targets of `skills/from-<agent>/<name>` links | The skills the model opens, the shell snapshot every command sources, the folder Codex puts on `PATH` for `apply_patch`, the air-gap level files, the code index's submodule choices. Only the linked skills of another agent are readable, not the rest of that agent's folder. |
| What the user lists (§4) | Their choice. |

**Hidden again inside those** (`DEFAULT_HIDDEN`, masked as empty files when they exist): `.cargo/credentials{,.toml}`, `.config/git/credentials`, `.gradle/gradle.properties`, `.m2/settings.xml`, `.m2/settings-security.xml`, `.local/share/uv/credentials`, `.cache/huggingface/{token,stored_tokens}`.

**Hidden** is everything else in the home folder, for example `.ssh`, `.gnupg`, browser profiles (`.mozilla`, `.config/google-chrome`, `snap/…`), `.aws`, `.kube`, `.docker/config.json`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, `.config/gh`, the keyrings, `~/.codex`, other agents' folders, the shell's rc files, `~/Documents`, and other projects. It also covers `$CODEX_HOME`'s sessions, history, logs, databases, `config.toml`, `ling web`'s device keys and the app-server socket.

**Outside the home folder nothing changes.** Every directory beside the home folder and beside each of its ancestors is mounted read-only as before: `/usr`, `/etc`, `/opt`, `/proc`, `/sys`, `/run`, `/tmp`, other users' homes, and so on. `/dev` is the exception: the sandbox still mounts its own minimal `/dev`, as Codex always did. An entry that resolves to the home folder, into it or above it, such as `/home -> /data/home`, is not mounted.

## 3. How it is built

### 3.1 The hook

Patch `0019` adds two lines to Codex's sandbox helper (`linux-sandbox`): a dependency line, and `ling_sandbox_home::hide_home(&mut permission_profile);` after the air-gap hook in `resolve_permission_profile`. That is 115 bytes, and the series grows to 43,073 bytes, over the 43,000 cap, so the cap is raised to 43,250 (§9, decision 6). Every command Codex sandboxes on Linux goes through this function, whatever started it: the TUI, `ling exec`, the app-server (so the desktop app and `ling web`), subagents, Night Shift's sessions and the sandboxed reads of Codex's file-system layer.

`hide_home` acts only on a `Managed` profile whose filesystem entries read `:root` and do not write it, which means the read-only and workspace-write sandboxes. For those:

1. it removes `:root = read`;
2. it adds one `read` entry for every directory beside the home folder and beside each of its ancestors (§2, last paragraph);
3. it adds `read` entries for the plan's paths inside the home folder, and `deny` entries for the hidden credential files;
4. it adds `:project_roots = read` if no entry names the workspace;
5. it keeps every other entry: writable roots, Codex's protected `.git`/`.codex`/`.agents`, and the user's own deny rules.

The helper runs twice per command, once outside bubblewrap and once inside it. The second time the profile no longer reads `:root`, so nothing is enumerated again.

### 3.2 Why not configuration

Codex 0.158 has three ways to configure filesystem access, and none of them can do this:

- **Deny-read rules** (`[permissions.<profile>.filesystem]` entries with `none`, or managed `deny_read` requirements). Bubblewrap masks a denied folder with an empty tmpfs and then reopens only the **writable** paths under it (`append_existing_unreadable_path_args`). "Deny `~`, read `~/.cargo`" therefore hides `~/.cargo`, and a deny list naming each secret cannot hide other projects.
- **A named permission profile** in `config.toml` with `:minimal` plus explicit reads. It is replaced as soon as the user picks a preset in `/permissions`, passes `-s`/`--sandbox`, or a client of the app-server sends a `sandboxPolicy`. `sandbox_mode` and `default_permissions` also cannot be set together. Every such path would need its own hook, and the list of folders beside the home folder depends on the machine.
- **Managed requirements** (`/etc/codex/requirements.toml`, which needs root) can only deny, so they have the same shadowing problem as the first option.

A policy that does not read `/` is built by Codex as `--tmpfs /` plus one `--ro-bind` per readable root. That is the mechanism `:minimal` already uses, and it is exactly what the rewritten profile produces. The rule needs no change to Codex's bubblewrap code.

### 3.3 The crate

`ling-rs/sandbox-home` depends on `codex-protocol`, `codex-utils-absolute-path`, `codex-utils-path-uri`, `toml` and `ling-airgapped`. It takes `HOME`, `$CODEX_HOME` and the user-level file's path from `ling-airgapped`, so all three are decided the same way as the air-gap level.

- `plan(&Inputs) -> Option<Plan>` is pure apart from reading the filesystem, and the tests drive it with a scratch tree.
- `apply(&mut entries, &Plan)` rewrites the entries.
- `hide_home` gathers the inputs from the helper's environment and arguments (`--sandbox-policy-cwd`, `PATH`, the toolchain variables, `current_exe`).
- The launcher shows the result with `ling sandbox-home` (`ling-rs/src/sandbox_home.rs`).

## 4. How a user adds a path

- **Read-only:** in `~/.config/dreamference/config.toml`:

  ```toml
  [sandbox]
  readable = ["~/datasets", "/home/me/other-repo"]
  ```

  `~/` is expanded and relative paths are ignored. A table, not flat keys: `ling-admin` rewrites the file's flat keys from its own settings and keeps tables as they are, as it does for `[night]` and `[node]`.
- **Read-write:** Codex's own `--add-dir <path>` or `sandbox_workspace_write.writable_roots`, as before. A writable root is mounted and so is visible.
- **For one command:** under `on-request` approval, a command that fails for want of a path can be rerun outside the sandbox with the user's approval, as today.
- **Off:** `[sandbox] home = "visible"` restores the old behaviour. There is no environment variable for it, because a project's `.codex/config.toml` can set a command's environment.
- **What is visible:** `ling sandbox-home` lists the readable paths and the hidden credential files for a command started in the current directory.

## 5. Interactions

| Setting | Effect |
|---|---|
| Full Access | Unchanged: there is no sandbox, and the helper is not run. |
| `/airgapped on` | Unchanged: the network is taken first by the same function. The seals under `/run/user/<uid>/ling-airgapped` stay visible read-only (`/run` is beside the home folder). The level files in `$CODEX_HOME/airgapped` stay readable for `ling-search` and `ling-fetch`. |
| Read-only sandbox (`ling exec`'s default) | The workspace and the §2 paths are readable; nothing is writable. |
| `ling` started in the home folder | The workspace is the home folder, so it is writable and visible as before. `ling-airgapped` already warns about this case (`level_files_exposed`). |
| Night Shift | Its sessions run in a worktree under the workspace-write sandbox. The worktree and the main repository's git folder are visible; the runner's own test command runs outside the sandbox as before. |
| SWE-bench | Unchanged: it runs with the sandbox bypassed, inside the instance's container. |
| Node jobs | Unchanged: the job's own bubblewrap already hides the home folder. Codex's sandbox inside it applies this rule to an already empty home. |
| A permission profile that does not read `/` or writes it | Untouched: already narrower, or not a filesystem sandbox. |

## 6. Effects a user will notice

- `git push`/`git fetch` over SSH from a sandboxed command fails (no `~/.ssh`). They need approval to run outside the sandbox. Night Shift never pushes.
- `ling-admin gmail …` from a sandboxed shell, the fallback used when Mightling's apps are not offered, can no longer read the service secret. The Gmail tools, which are MCP servers, are unaffected.
- `ling-admin` from a source checkout (its virtualenv is linked from `~/.local/bin`) finds its virtualenv but not the editable package's source, unless the session works in that checkout. A release install is unaffected.
- **Not yet measured:** writes into the hidden home folder (for example `pip install --user`) may land in the sandbox's own tmpfs, which disappears with the command, rather than failing as before. §8.2 checks this.
- The shell's rc files are hidden. Codex's shell snapshot, which is readable, still gives commands the user's `PATH` and functions.

## 7. Not covered

- **`view_image`** reads a path in `ling`'s own process when the session's profile reads `:root`. That is the configuration's profile, not the rewritten one, so it can still show the model an image from the home folder. It cannot return a file's text. Closing it needs a second hook in core (§9, decision 5).
- **Unix sockets under `/run/user/<uid>`**: `ssh-agent` (`$SSH_AUTH_SOCK`), `gpg-agent`, the session D-Bus (and with it the keyring's Secret Service) and a rootless Docker socket. A read-only mount does not stop `connect()`, so a command can still sign with the user's SSH key or ask the keyring. This is the same finding through another door (§9, decision 4).
- **Bind mounts of the home folder elsewhere** (`mount --bind ~/x /mnt/x`) are mounted with their parent. The live check lists them with `findmnt`.
- **Environment variables** reach commands as Codex's `shell_environment_policy` decides. That is unchanged.
- **MCP servers and approved escalations** run outside the sandbox, as before.

## 8. Tests

### 8.1 Built and passing

- `ling-rs/sandbox-home` unit tests (`cargo test --release -p ling-sandbox-home` in a scratch export): secrets, browser profiles, ling's state and other projects hidden; toolchains, skills, ling's tools and the workspace readable; credential files hidden again; a subfolder session sees its repository; a worktree sees the shared git folder but not the main checkout; `PATH` programs bring their real folders but never the home folder or a shared parent; the user's `[sandbox]` table adds paths or switches the rule off, and a file that does not parse keeps it on; links beside the home folder that lead into it are skipped; the rewritten workspace-write and read-only profiles are checked with Codex's own resolver (`can_read_local_path_with_cwd`, `get_unreadable_roots_with_cwd`); Full Access, external sandboxes, `:root = write` and an already rewritten profile are left alone; `--sandbox-policy-cwd` is parsed in both spellings.
- `ling-launcher`: `ling sandbox-home`'s text.
- Python, `tests/test_sandbox_home.py`: the hook and the dependency are in `0019`, after the air-gap hook; no secret is in the default lists; only what commands need of `$CODEX_HOME` is listed; every hidden file lies in a readable folder; `ling-admin`'s `save_config` keeps the `[sandbox]` table. `tests/test_codex_branded_builder.py`: the cap.

### 8.2 Live check (pending, outside PyCharm's AppArmor profile)

A shell under PyCharm runs in `snap.pycharm.pycharm (complain)`, where bubblewrap results prove nothing. Run the helper built from the export, not the installed `ling`, from a throwaway user unit, against a scratch home:

```bash
S=<scratch>; H=$S/home/u
mkdir -p $H/.ssh $H/.cargo/bin $H/proj/.git $H/other && echo key > $H/.ssh/id && echo tok > $H/.cargo/credentials.toml
P='{"type":"managed","file_system":{"type":"restricted","entries":[{"path":{"type":"special","value":{"kind":"root"}},"access":"read"},{"path":{"type":"special","value":{"kind":"project_roots"}},"access":"write"}]},"network":"restricted"}'
systemd-run --user --wait --pty -p WorkingDirectory=$H/proj --setenv=HOME=$H --setenv=CODEX_HOME=$H/.mightling \
  $S/target/release/codex-linux-sandbox --sandbox-policy-cwd $H/proj --permission-profile "$P" -- \
  sh -c 'cat /proc/self/attr/current; ls -a ~; cat ~/.ssh/id; cat ~/.cargo/credentials.toml | wc -c; ls ~/other; touch ~/x && echo wrote-home; touch ~/proj/y && echo wrote-workspace; ls /usr/bin | head -1; findmnt -rn -o TARGET,SOURCE | grep "$HOME" | head'
```

Expected: the AppArmor label is not PyCharm's; `ls -a ~` shows only `.cargo` and `proj`; `.ssh/id` and `other` do not exist; the credentials file is empty; the workspace write succeeds; `/usr/bin` lists. Also record whether `touch ~/x` fails or lands in the tmpfs. Then, with a built `ling` (not installed) and a scratch `HOME` and `CODEX_HOME`, run `ling sandbox -- ls ~` and `ling exec` in a scratch repository against a model server that is not night 5's. `ling-admin audit egress` is unaffected because the network policy is unchanged.

## 9. Decisions for the owner

1. **Allowlist versus denylist.** Recommended: the allowlist built here. The denylist cannot hide other projects, and Codex's bubblewrap builder cannot reopen a readable folder under a denied one.
2. **The default readable list (§2).** Recommended: as listed. Open questions:
   - `.npmrc`: hidden here; it often holds a registry token.
   - The shell rc files: hidden; the snapshot covers `PATH`.
   - `.cache/codebase-memory-mcp`: readable, so the code index works. It holds the symbol graphs of every indexed project, which is a summary of other projects. The narrower option is to open only the store of the session's repository.
   - `.m2` and `.gradle`: readable, with their settings files hidden.
3. **`ling-admin gmail` in a sandboxed shell.** (a) Leave the secret hidden; the apps' MCP tools are the way to mail. (b) Make `~/.config/dreamference/gmail/service-secret` readable so the shell fallback keeps working. Recommended: (a). The fallback should then not be offered while this rule is on.
4. **Sockets under `/run/user/<uid>`.** (a) Leave them as they are. (b) Mask `/run/user/<uid>`, keeping `ling-airgapped`'s seal folder and what Codex's own daemon socket handling needs, so `ssh-agent`, D-Bus and the keyring become unreachable. Recommended: (b) as a follow-up on this branch, after the live check confirms what breaks: notifications, `gh`'s keyring and `docker` rootless.
5. **`view_image`'s in-process reads.** (a) Accept: images only. (b) A second hook in core, so the session's own profile is rewritten and in-process reads go through the sandboxed file system. Recommended: (a) for 1.7.0, recorded here.
6. **The patch cap.** Raise it from 43,000 to 43,250 for the 115 bytes in `0019`. The alternative, with no patch bytes, is the launcher re-executing the helper with a rewritten `--permission-profile` argument at `main()`. It is more fragile and not recommended.
7. **The working directory is the home folder.** (a) Nothing more: it is the user's choice. (b) A start-up line saying the whole home folder is the workspace. (c) Refuse. Recommended: (b), beside `ling-airgapped`'s existing check.
