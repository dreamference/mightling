# The home folder hidden from sandboxed commands

Developer notes behind the sandbox line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_SANDBOX_HOME.md` (security review finding 13). Drafted for 1.7.0 on `security/sandbox-home`.

## Where it lives

- **The hook:** one line in patch `0019`, `ling_sandbox_home::hide_home(&mut permission_profile);`, in `linux-sandbox`'s `resolve_permission_profile`, right after the air-gap hook. Every command Codex sandboxes on Linux passes through it. It runs twice per command: outside bubblewrap, then inside it to apply seccomp. The second run sees a profile that no longer reads `:root` and does nothing.
- **The rule:** the leaf crate `ling-rs/sandbox-home` (`ling-sandbox-home`). It depends on `codex-protocol`, so its tests run in the build's export with `cargo test --release -p ling-sandbox-home`, never in `codex/`.
- **The view:** `ling sandbox-home` (`ling-rs/src/sandbox_home.rs`) prints what a command started in the current directory could read.

## Things found the hard way

- **Codex's deny-read rules cannot reopen a readable folder under a denied one.** `bwrap.rs` masks a denied folder with an empty tmpfs and recreates only the *writable* paths under it. "Deny `~`, read `~/.cargo`" therefore hides `~/.cargo`. The rule instead removes `:root = read` and lists every directory beside the home folder and its ancestors. Codex then builds the sandbox as `--tmpfs /` plus one `--ro-bind` per root, the same mechanism as its `:minimal` profile.
- **Never list `/dev`:** the sandbox mounts its own minimal `/dev`, and a read-only bind of the host's would replace it.
- **The user's settings are a `[sandbox]` table, not flat keys:** `ConfigFileStorageManager.save_config_dict` rewrites the flat keys it knows and keeps tables, so a flat `mightling_sandbox_*` key would vanish at the next `ling-admin main-model set`.
- **A shell under PyCharm proves nothing about bubblewrap** (`snap.pycharm.pycharm (complain)`): run the live check in the spec (§8.2) from `systemd-run --user`.
- **The worktree's Python package is not the venv's:** `.venv` has an editable install of the main checkout, so the Python suite in a worktree needs `PYTHONPATH=<worktree>`.
