# Mightling without sudo — the desktop app and `ling` on a locked-down Ubuntu

**Status:** Researched, measured and partly built on 2026-10-22 (branch `no-sudo/detection`, for 1.7.2 or later). Built: the detection and messages (§6.1–§6.3), the borrowed setuid helper for the app (§5.3), and the guard against a second `bwrap` profile (§6.4). Not built: a Flatpak (§4), recommended against for now.
**Goal:** Mightling's desktop app and `ling` work on Ubuntu 24.04 and later for a user without sudo, as on an enterprise workstation, or, where that cannot be done, say so at start with the exact one-time command an administrator runs, instead of failing obscurely.
**Builds on:** [host-safety.md](../docs/dev/host-safety.md#bubblewrap-needs-an-apparmor-profile-and-this-machine-now-has-one) (the `puffin-bwrap` profile), [desktop.md](../docs/dev/desktop.md) item 2 (the app's profiles), [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md) (§14: `on` is enforced by the sandbox), [DESKTOP_ELECTRON](./DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md) (no SUID helper shipped, no AppImage).

## 1. The problem

Ubuntu 23.10 and later set `kernel.apparmor_restrict_unprivileged_userns=1`: a program without an AppArmor profile that grants `userns` gets a user namespace with no capabilities in it, which is useless to a sandbox. Two of Mightling's sandboxes need one:

1. **Chromium's sandbox in the Electron app.** Without a profile for the app's binary, Chromium's zygote aborts with "No usable sandbox!" before the app's JavaScript runs. The `.deb` writes `/etc/apparmor.d/mightling` in `postinst`; a checkout's `ling-admin desktop install` and an unpacked build's `install-sandbox-profile.sh` write theirs, all three with sudo.
2. **`ling`'s command sandbox** (Codex's `linux-sandbox`, bubblewrap): every command the agent runs in Read Only or Auto, and the empty network namespace that enforces `/airgapped on` (patch 0019). `ling-admin host setup` writes `/etc/apparmor.d/puffin-bwrap` for `/usr/bin/bwrap`.

Profiles attach by path and live under `/etc`, so each is one root step per machine.

## 2. Measurements (2026-10-22)

On this GB10 (Ubuntu 24.04.4, apparmor 4.0.1, `puffin-bwrap` loaded), from transient user units, whose label is `unconfined` (PyCharm's terminals carry `snap.pycharm.pycharm (complain)` and pass anything). A user without the profile was simulated by a copy of the binary under `~/.local/opt/mightling-test/`, which no profile names, and, for `ling`'s bwrap (which Codex takes as `/usr/bin/bwrap` whatever is first on PATH, see §3.4), by `aa-exec -p unprivileged_userns`, the label Ubuntu stacks onto an unprofiled namespace. Nothing under `/etc` was changed and no sudo was run.

| Case | Result |
| --- | --- |
| `/usr/bin/bwrap --unshare-user --unshare-net --ro-bind / / /bin/true` (profiled) | exit 0 |
| The same from an unprofiled copy of bwrap | exit 1, `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` |
| `unshare -Ur` | `write failed /proc/self/uid_map: Operation not permitted` |
| `ling sandbox -- sh -c 'echo inside'` (profiled) | `inside` |
| `ling sandbox …` under `unprivileged_userns` | `bwrap: No permissions to create new namespace …` |
| `ling sandbox -c features.use_legacy_landlock=true …` | panic: `filesystem-restricted execution requires bubblewrap to isolate app-server sockets` |
| Unprofiled copy of the packaged app | `FATAL … No usable sandbox!`, core dumped |
| The same copy with `CHROME_DEVEL_SANDBOX=/opt/google/chrome/chrome-sandbox` | starts; zygote in new PID and network namespaces, renderer `Seccomp: 2`, broker and GPU process `Seccomp: 2` |
| Ubuntu 26.04.1 (the `desktop` machine, apparmor 5.0.0~beta1, no Mightling profile) | `/usr/bin/bwrap` probe exit 0; `ling sandbox` runs, the command labelled `bwrap//&unpriv_bwrap (enforce)`, `/etc` read-only |
| Flatpak | not installed on any machine reachable here; `apt-cache policy flatpak` on 24.04: candidate 1.14.6, not installed; installing it needs root |

Ubuntu 25.04 and later ship `bwrap-userns-restrict` in the `apparmor` package (Ubuntu 25.04 release notes, "AppArmor profile for bwrap"): bubblewrap may create its namespaces, and what runs inside is confined to `unpriv_bwrap` with every capability denied. So **on 25.04 and later `ling`'s sandbox needs no administrator**; on 24.04 it still does. The app needs its profile on every release: Ubuntu's own profiles cover other Electron apps at their packaged paths only (`/opt/Obsidian/obsidian`, `/usr/share/code/code`, `/usr/lib/slack/slack`, `/opt/google/chrome/chrome`, …), never a copy in a home folder.

## 3. `ling`'s sandbox without a user namespace — option (b)

### 3.1 No Landlock mode remains

The pinned Codex (`rust-v0.158.0`) still parses `--use-legacy-landlock` (`features.use_legacy_landlock`, deprecated with a startup notice), but `linux_run_main.rs` refuses it for any policy that restricts the filesystem: `ensure_legacy_landlock_mode_supports_policy` panics with "filesystem-restricted execution requires bubblewrap to isolate app-server sockets" (measured, §2). The only paths that start no bubblewrap are full-disk-write ones: Full Access, which runs no helper at all, and a full-write profile with the network off, which installs seccomp's socket filter alone. Neither restricts the filesystem, and neither masks the host's control sockets (Docker, D-Bus, systemd, 1.7.1's `host_sockets()`), which bubblewrap does. A launcher fallback to Landlock is therefore not possible, and adding one back would be a Codex patch the series' size cap (57,523 of 58,000 bytes) does not allow.

### 3.2 `/airgapped on` without the sandbox

`on` is enforced by bubblewrap's empty network namespace. With bubblewrap refused, each command fails closed, so nothing is silently weakened; but Codex classes the failure (`Operation not permitted`, exit 1) as a likely sandbox denial and, under `on-request` approvals, asks to run the command outside the sandbox, where it has the network. So `on` cannot be kept, and is refused rather than claimed: a configured `on` stops `ling` at start (as Full Access at `on` does), and `/airgapped on` and `ling airgapped default on` answer "Airgapped not changed" with the reason and the fix. Full Access is never offered as the way out.

### 3.3 What remains for a user with no administrator

On 24.04: nothing sandboxed. The user can still talk to the agent and approve each command to run outside the sandbox, which runs it with all their access; `ling` says this at every start (§6.1). On 25.04 and later: everything works. Containers are not a route: Docker needs the `docker` group (root-equivalent, an administrator's grant) and rootless Podman needs the same user namespace.

### 3.4 Which bwrap

Codex's helper runs the first `bwrap` on the PATH of the command's environment, and the measured runs used `/usr/bin/bwrap` even with another first on the launcher's PATH (the helper runs in the command's environment, which Codex builds, not in the launcher's; not chased further). The probe takes the launcher's PATH, which in practice is the same binary. Mightling ships no bundled bubblewrap: where none is installed, Codex's fallback to a bundled copy finds nothing and every command fails, so `ling` says to install `bubblewrap`.

## 4. Flatpak — option (a)

A Flatpak installed with `flatpak install --user` needs no root once Flatpak itself is on the machine, and Electron runs there under zypak, which redirects Chromium's sandbox to Flatpak's own (zypak README: "redirects Chromium's sandbox to use the Flatpak sandbox"; `org.electronjs.Electron2.BaseApp` includes it). But:

1. **Flatpak is not on Ubuntu by default, and installing it needs root** (`apt install flatpak`). On a locked-down workstation where the administrator has not provided it, it is not a route at all.
2. **Flatpak's sandbox is bubblewrap too.** It works on Ubuntu 24.04 only because Ubuntu ships `/etc/apparmor.d/flatpak` granting `userns` to `/usr/bin/flatpak`; a user cannot build the same grant for anything of their own.
3. **`ling`'s sandbox does not fit inside it.** Flatpak's seccomp filter refuses new user namespaces to the app (its source's "Don't allow subnamespace setups"; not measured here, no Flatpak is reachable), which is why zypak exists. Codex's bwrap inside the Flatpak fails as it does outside; `flatpak-spawn --host` leaves the Flatpak for the unprofiled host and is a full sandbox escape needing `--talk-name=org.freedesktop.Flatpak`; `flatpak-spawn --sandbox` is a different sandbox interface, which Codex's helper would need a patch to use. The app's commands would run in the Flatpak's one whole-app sandbox, with the network the app-server needs to reach the model server, so `/airgapped on` could not be enforced per command and would be refused.
4. **Cost:** a manifest, a Flathub-style runtime pin (`org.freedesktop.Platform` and the Electron BaseApp, per architecture), a `flatpak-builder` job in the release workflow, a repository or single-file bundle to host, and a second set of filesystem permissions to keep honest. None of it can be tested on the machines here without root.

**Recommendation:** no Flatpak now. It fixes the app's half only, and only where an administrator has already installed Flatpak — the same administrator who can install the `.deb`, which carries its own profile. Revisit if a customer's fleet standardises on Flatpak.

## 5. Chromium's sandbox without a profile — option (c)

### 5.1 What Chromium offers

Chromium's own document (docs/security/apparmor-userns-restrictions.md) gives four ways: turn the sysctl off (all programs), an AppArmor profile for the binary, the setuid sandbox helper (`chrome-sandbox` owned by root, mode 4755), or `--no-sandbox`, which "disables critical security features" and is the only answer it gives a user with no root. Mightling never passes `--no-sandbox` (desktop.md item 1, a test greps for it) and never ships a SUID helper of its own (installing one needs root anyway).

### 5.2 What other Electron apps do on Ubuntu 24.04

- **`.deb` at a packaged path:** Ubuntu's `apparmor` package ships `userns` profiles for many by path (1password, brave, code, Discord, element-desktop, github-desktop, msedge, obsidian, signal-desktop, slack, vivaldi). Needs root to install, as does ours, whose `postinst` writes the profile.
- **snap:** snapd generates the AppArmor profile at install; installing a snap needs root or a polkit admin.
- **AppImage or a tarball in the home folder:** no profile, so the documented answer is `--no-sandbox`, or a one-time profile by an administrator.
- **Flatpak:** zypak (§4).

### 5.3 The borrowed helper (built)

Chromium falls back to the setuid sandbox when it cannot use a user namespace and finds a helper: `chrome-sandbox` beside the binary, or, for a binary the user owns, the path in `CHROME_DEVEL_SANDBOX` (`setuid_sandbox_host.cc`; Chromium's document calls this "the safest way" for a build without a profile). Enterprise workstations very often have Google Chrome, whose package installs `/opt/google/chrome/chrome-sandbox` setuid root. Measured (§2): an unprofiled, user-owned copy of the app starts with it, its zygote in fresh PID and network namespaces and its renderers under seccomp-bpf.

So `ling app`, on a restricted system, when no profile covers the app's binary and the binary is the user's, looks for a root-owned setuid helper at the default paths of Google Chrome, Microsoft Edge, Brave, Vivaldi, Chromium, VS Code, Slack, Discord and Signal, and starts the app with `CHROME_DEVEL_SANDBOX` naming the first, saying so. A profile, where there is one, still wins (Chromium prefers its namespace sandbox when it can have one). Trade-offs, said in the spec and the message: the helper is another vendor's root-owned binary, already on the machine and usable by any user; Chromium calls the setuid sandbox "slightly weaker" than the namespace one; a helper from an old enough browser could speak another protocol version, which Chromium reports loudly ("setuid sandbox … not configured correctly", caught by `ling app`'s log watch), never silently. A launch from the desktop menu starts the binary directly and gets no variable; `install-sandbox-profile.sh` names the helper and the one-line start command too.

With no profile and no helper: `ling app` says the app will stop at start and what an administrator runs, then starts it (a profile that names the binary by a glob is not recognised, so the check warns rather than refuses) and reports the failure from the log as before.

## 6. Detection and messaging — option (d) (built)

### 6.1 `ling` at start

Before a session whose commands would use the sandbox (not Full Access, not a command inside a `ling` session), the launcher runs Codex's probe once (`bwrap --unshare-user --unshare-net --ro-bind / / /bin/true`) on the first `bwrap` on PATH (`ling-rs/src/userns.rs`). On a refusal it prints:

> ⚠️ ling's sandbox cannot start on this machine: /usr/bin/bwrap is refused a user namespace (bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted).
> Each command the agent runs fails in the sandbox and then asks to run outside it; approving runs it with all your access. /airgapped on is refused.
> The fix, once per machine, by an administrator: an AppArmor profile that lets /usr/bin/bwrap alone create its namespaces:
> `printf 'abi <abi/4.0>,\ninclude <tunables/global>\nprofile puffin-bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}\n' | sudo tee /etc/apparmor.d/puffin-bwrap >/dev/null && sudo apparmor_parser -r /etc/apparmor.d/puffin-bwrap`

The command is self-contained because a client has no `ling-admin` (clients are wheel-free); on a node a line adds that `ling-admin host setup` writes the same file. The answer depends on the machine: one profile already attaching to the bwrap but not in force is loaded (`sudo apparmor_parser -r <file>`); two or more make AppArmor attach none (openai/codex#38715) and the extra one is removed, ours before Ubuntu's; a sysctl that is not 1 means no profile helps (`user.max_user_namespaces`, Debian's `kernel.unprivileged_userns_clone`); no bwrap at all is `sudo apt install bubblewrap`. Never the sysctl, never `--no-sandbox`, never Full Access.

### 6.2 `/airgapped on`

A configured `on` stops the launch with the reason and the fix, beside the Full Access and Windows refusals; `/airgapped on` and `ling airgapped default on` answer "Airgapped not changed" with the same (§3.2).

### 6.3 `ling app`

Before starting the app: nothing where it can start its sandbox, the helper line of §5.3, or a warning with the fix (`install-sandbox-profile.sh` beside an unpacked binary, `ling-admin desktop install` for a checkout). After a failure, the log's last lines and both routes, as before.

### 6.4 `ling-admin host setup`

`SandboxPrerequisite.fix_commands()` no longer writes `puffin-bwrap` where another profile attaches to the same bwrap: it loads that one, and removes ours if both are there. Without this, a GB10 upgraded from 24.04 to 26.04 with `puffin-bwrap` in place would have two profiles for `/usr/bin/bwrap`, and bubblewrap would stop working for everyone.

## 7. Recommendation

1. Ship §6 and §5.3 (built). They make every case say what it is and what fixes it, and give the app a no-administrator route wherever a Chromium browser with its helper is installed.
2. Tell enterprise administrators, in the install docs, that there is one root step per machine on Ubuntu 24.04 and none for `ling` on 25.04 and later: install the `.deb` (its `postinst` writes the app's profile) and, on 24.04, the `puffin-bwrap` profile (or Ubuntu's `bwrap-userns-restrict` from `apparmor-profiles`; one of the two, never both).
3. No Flatpak, no Landlock fallback, no `--no-sandbox`, no SUID helper of our own.

## 8. How to test without the profile

The profile attaches by path, so a copy of the app at another path (`~/.local/opt/mightling-test/`) runs as a fresh user's would; run it from `systemd-run --user --wait --pipe`, whose label is `unconfined` (check `cat /proc/self/attr/current`), never from a PyCharm terminal. For `ling`'s bubblewrap, which is always `/usr/bin/bwrap`, run `ling` under `aa-exec -p unprivileged_userns` in such a unit. Nothing under `/etc`, no sudo.
