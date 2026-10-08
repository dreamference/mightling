#!/usr/bin/env bash
# Installs Mightling from a release, with no checkout of the repository and nothing compiled.
#
#   ./install.sh [--role client|node] [--version X.Y.Z] [--no-advertise]
#                [--from <dir>] [--no-host-setup] [--no-model]
#
# What it installs depends on the machine:
#
#   client   the `ling` terminal agent and its commands (`ling-search`, `ling-fetch`,
#            `ling-code` when the release carries it): prebuilt binaries, downloaded from the
#            release, checked against its checksum file, placed in
#            ~/.local/share/dreamference/mightling/bin and linked into ~/.local/bin. Releases carry it
#            for arm64 and x86-64 Linux and for macOS (Apple silicon and Intel).
#   node     the client, plus `ling-admin` (the Python package, from the release's wheel, in a
#            virtualenv of its own) and everything else a node needs, with no question asked:
#            python3-venv where the system lacks it; the host settings a model load needs
#            (`ling-admin host setup --yes`); you in the `docker` group; lingering, so jobs and
#            Night Shift tasks outlive a logout; the node offered to the local network
#            (`ling-admin node enable --yes`), so `ling` on your other computers finds it with no
#            address typed; and the default model, downloaded and served (`ling-admin model
#            download`, `ling-admin server start`, logged to ~/.local/state/dreamference/
#            install-<time>.log). It ends with a summary of every step, done or failed. This is
#            what a GB10 (DGX Spark and its siblings) gets by default; every other machine gets
#            the client.
#
# You can run it on a fresh GB10 and leave. A node install changes the machine outside your home
# folder, so it needs root, asked for once at the very start: nothing at all when sudo needs no
# password (root, or a NOPASSWD rule), otherwise sudo's own password prompt, after which sudo is
# kept from expiring until the script ends. With no terminal to ask on (`curl … | bash` over a
# plain ssh) and a sudo that wants a password, it stops before anything is downloaded and says how
# to run it so that it can ask: download it, then `bash install.sh` in a terminal (or `ssh -t`).
#
# System updates. A node also checks, at the start, for package upgrades (`apt-get update`;
# NVIDIA's DGX, CUDA and driver packages are named) and firmware (`fwupdmgr`). At a terminal it
# asks the one question it ever asks, default no: install them now? Yes installs them after
# everything else (`apt-get full-upgrade`, firmware staged for the next boot); it never reboots,
# and the summary says when to. With no terminal they are only listed, with the commands.
#
# --no-advertise keeps the node off the local network (`ling-admin node enable` offers it later).
#
# It downloads the same assets, by the same names and with the same checks, as `ling update`
# (ling-rs/src/update.rs), so a machine installed this way is updated by that command.
#
# The repository is public, so no token is needed. One is used when present (GH_TOKEN,
# GITHUB_TOKEN, or a logged-in `gh`): it raises GitHub's rate limit, and it is what reads a private
# fork named by MIGHTLING_RELEASE_REPO.
# MIGHTLING_RELEASE_REPO names another repository (a fork), MIGHTLING_RELEASE_API another API root (the
# tests' stand-in server); MIGHTLING_INSTALL_DIR and MIGHTLING_VENV move the two directories.
#
# Releases from 1.5.0 on are signed: SHA256SUMS, which lists every file of the release, carries an
# Ed25519 signature by Mightling's release key (SHA256SUMS.sig), checked here with OpenSSH's
# `ssh-keygen -Y verify` (OpenSSH 8.1 or newer, which Linux distributions and macOS ship) against
# the key below. Nothing is installed from a signed release whose signature does not verify, or
# from a release from 1.5.0 on that is unsigned. specs/DREAMFERENCE_RELEASE_SIGNING.md.
#
# --from <dir> installs from a directory holding the same asset names and the same checksum and
# signature files instead of a GitHub release, with no network for Mightling's own files
# (`ling-admin node provision` copies such a bundle from another node; specs/
# DREAMFERENCE_MIGHTLING_FLEET.md §7.2). The checks are the same. A `VERSION` file in it names the
# version, and a `wheelhouse/` folder in it, if present, holds the Python dependencies so pip needs
# no index either. --no-host-setup and --no-model are for provisioning, which does those parts
# itself: the root half with `sudo ling-admin node prepare` (host settings, docker group,
# lingering), and the model copied from the machine provisioning it. With --no-host-setup this
# script needs no root, and checks for no system updates.
#
# MIGHTLING_LINGER_DIR, MIGHTLING_PCI_DEVICES and the PATH are where the tests point it at
# stand-ins; nothing else reads them.
#
# For a development install from a checkout, use scripts/install_gb10.sh instead.
set -euo pipefail

# The release keys, the lines of ling-rs/release-signing.pub (a test keeps them equal).
RELEASE_KEYS='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKIG5+J3RTa4AaoT0o2qIhVr7bGvaa+T5b84rDv31ptj mightling-release-2026-10'
SIGNED_SINCE="1.5.0"

REPO="${MIGHTLING_RELEASE_REPO:-dreamference/mightling}"
API="${MIGHTLING_RELEASE_API:-https://api.github.com}"
INSTALL_DIR="${MIGHTLING_INSTALL_DIR:-$HOME/.local/share/dreamference/mightling}"
VENV_DIR="${MIGHTLING_VENV:-$HOME/.local/share/dreamference/venv}"
LINK_DIR="$HOME/.local/bin"
ROLE=""
VERSION=""
ADVERTISE=1
FROM=""
HOST_SETUP=1
MODEL=1

# A line that cannot be written (a reader that went away) is dropped, never the end of the run.
say()  { printf '%s\n' "$*" 2>/dev/null || true; }
fail() { printf '❌ %s\n' "$*" >&2 2>/dev/null || true; exit 1; }

# The comment block at the top, without its `# `.
usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; }

# What the summary lists: `step done|failed|off|available <text>`. A step is done or failed; `off`
# is only ever an opt-out given on the command line, `available` only the system updates.
STEPS=()
step() { STEPS+=("$1|$2"); }

LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dreamference"
LOG="$LOG_DIR/install-$(date +%Y%m%d-%H%M%S).log"
LINGER_DIR="${MIGHTLING_LINGER_DIR:-/var/lib/systemd/linger}"

while [ $# -gt 0 ]; do
    case "$1" in
        --role)      ROLE="${2:-}"; shift 2 ;;
        --role=*)    ROLE="${1#*=}"; shift ;;
        --version)   VERSION="${2:-}"; shift 2 ;;
        --version=*) VERSION="${1#*=}"; shift ;;
        --no-advertise) ADVERTISE=0; shift ;;
        --from)      FROM="${2:-}"; shift 2 ;;
        --from=*)    FROM="${1#*=}"; shift ;;
        --no-host-setup) HOST_SETUP=0; shift ;;
        --no-model)  MODEL=0; shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           fail "unknown argument: $1 (see --help)" ;;
    esac
done

if [ -n "$FROM" ]; then
    [ -d "$FROM" ] || fail "--from: $FROM is not a directory."
    FROM="$(cd "$FROM" && pwd)"
    NEEDED="gzip awk"
else
    NEEDED="curl gzip awk"
fi
for tool in $NEEDED; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is needed and was not found."
done
if command -v sha256sum >/dev/null 2>&1; then
    sha256() { sha256sum "$1" | awk '{print $1}'; }
elif command -v shasum >/dev/null 2>&1; then
    sha256() { shasum -a 256 "$1" | awk '{print $1}'; }
else
    fail "sha256sum (or shasum) is needed to check the downloads and was not found."
fi

# -- which machine ---------------------------------------------------------------------------

case "$(uname -s)" in
    Linux)  os="unknown-linux-gnu" ;;
    Darwin) os="apple-darwin" ;;
    *)      fail "this script installs on Linux and macOS; on Windows use install.ps1." ;;
esac
case "$(uname -m)" in
    aarch64|arm64) arch="aarch64" ;;
    x86_64|amd64)  arch="x86_64" ;;
    *)             fail "unsupported processor: $(uname -m)" ;;
esac
TARGET="$arch-$os"

# A GB10 is an arm64 Linux machine whose GPU says so. Eight machines are GB10s (NVIDIA's DGX
# Spark and the Acer, ASUS, Dell, Gigabyte, HP, Lenovo and MSI boxes) and each names itself
# differently in DMI ("GX10" on an ASUS Ascent), so the GPU is asked, never the vendor: by
# nvidia-smi, or, where no driver answers yet, by its PCI id (10de:2e12, the same chip in all).
is_gb10() {
    [ "$TARGET" = "aarch64-unknown-linux-gnu" ] || return 1
    if command -v nvidia-smi >/dev/null 2>&1 \
        && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -q "GB10"; then
        return 0
    fi
    for device in "${MIGHTLING_PCI_DEVICES:-/sys/bus/pci/devices}"/*; do
        [ "$(cat "$device/vendor" 2>/dev/null)" = "0x10de" ] \
            && [ "$(cat "$device/device" 2>/dev/null)" = "0x2e12" ] && return 0
    done
    return 1
}

case "$ROLE" in
    "")          if is_gb10; then ROLE="node"; else ROLE="client"; fi ;;
    client)      ;;
    node|both)   ROLE="node" ;;
    *)           fail "--role is client or node, not '$ROLE'." ;;
esac
# The node is a GB10's model server, its containers and its host settings; a Mac can only use one.
if [ "$ROLE" = "node" ] && [ "$os" = "apple-darwin" ]; then
    fail "macOS cannot be a Mightling node (the node runs the model on a GB10, under Linux). Install the client here (--role client, the default) and point it at your GB10."
fi
if [ "$ROLE" = "node" ] && ! is_gb10; then
    say "⚠️  This machine is not a GB10. The node's model recipes and host-safety checks are written"
    say "   for one; installing the node anyway because --role node was given."
fi

WORK="$(mktemp -d)"
KEEPALIVE=""
cleanup() {
    rm -rf "$WORK"
    if [ -n "$KEEPALIVE" ]; then kill "$KEEPALIVE" 2>/dev/null || true; fi
}
trap cleanup EXIT

# -- root, asked for once --------------------------------------------------------------------

# A terminal to ask on: standard input, or the one the script runs in when it was piped into bash.
have_terminal() { [ -t 0 ] || { [ -r /dev/tty ] && (exec < /dev/tty) 2>/dev/null; }; }

USER_NAME="$(id -un)"
# Every root command of the node goes through "$SUDO": nothing as root, `sudo -n` otherwise, so
# that nothing after this point can stop to ask for a password.
SUDO=""
NODE_ROOT=0
if [ "$ROLE" = "node" ] && [ "$HOST_SETUP" = 1 ]; then
    NODE_ROOT=1
    if [ "$(id -u)" != 0 ]; then
        command -v sudo >/dev/null 2>&1 || fail "a node install changes the machine as root (host \
settings, the docker group, lingering) and sudo was not found. Run it as root, or install sudo."
        if sudo -n true 2>/dev/null; then
            :
        elif have_terminal; then
            say "🔑 A node install changes the machine outside your home folder (host settings, the docker"
            say "   group, lingering, advertising, system updates), so it needs sudo. Your password is"
            say "   asked for once, now; nothing else is asked after the update question below."
            if [ -t 0 ]; then sudo -v; else sudo -v < /dev/tty; fi \
                || fail "sudo did not accept the password; nothing was changed."
        else
            fail "a node install needs root, sudo wants a password, and there is no terminal to ask \
on, so nothing was done (this is what \`curl … | bash\` over a plain ssh does). Download the script \
and run it in a terminal, where sudo can ask once: \`curl -fsSL -o install.sh \
https://github.com/$REPO/releases/latest/download/install.sh && bash install.sh\` (over ssh: \`ssh -t\`)."
        fi
        SUDO="sudo -n"
        # sudo forgets the password after 15 minutes, and the model download takes longer.
        ( while kill -0 "$$" 2>/dev/null; do sudo -n -v 2>/dev/null || true; sleep 50; done ) >/dev/null 2>&1 &
        KEEPALIVE=$!
    fi
fi

# -- system updates, asked about once ----------------------------------------------------------

# Package upgrades as `apt-get full-upgrade` would install them: its simulation leaves out what apt
# holds back on its own (Ubuntu's phased updates), which `apt list --upgradable` would count.
UPGRADES=""          # every package
NVIDIA_UPGRADES=""   # NVIDIA's (DGX, CUDA, the driver, the kernels built for it)
FIRMWARE=""          # one "device: from -> to" per line
INSTALL_UPDATES=0
if [ "$NODE_ROOT" = 1 ] && command -v apt-get >/dev/null 2>&1; then
    say "🔎 Checking for system updates (apt, firmware) ..."
    $SUDO apt-get -o DPkg::Lock::Timeout=300 -qq update > "$WORK/apt-update.log" 2>&1 \
        || say "⚠️  apt-get update failed (see below); the list of updates may be out of date. $(tail -n 1 "$WORK/apt-update.log")"
    apt-get -s -o Debug::NoLocking=1 full-upgrade 2>/dev/null < /dev/null | awk '/^Inst / {print}' > "$WORK/upgrades" || true
    UPGRADES="$(awk '{print $2}' "$WORK/upgrades" | paste -sd ' ' -)"
    NVIDIA_UPGRADES="$(awk 'tolower($0) ~ /nvidia|dgx|cuda/ {print $2}' "$WORK/upgrades" | paste -sd ' ' -)"
    if command -v fwupdmgr >/dev/null 2>&1; then
        $SUDO fwupdmgr refresh --force > /dev/null 2>&1 < /dev/null || true
        # fwupdmgr prints its JSON pretty, one key a line: a device's keys at depth 3, a release's
        # at 5. Exit 2 means nothing to update.
        fwupdmgr get-updates --json --no-unreported-check < /dev/null 2>/dev/null > "$WORK/firmware.json" || true
        FIRMWARE="$(awk '
            function value(line) { sub(/^[^:]*: *"/, "", line); sub(/".*$/, "", line); return line }
            /"Name" *:/ && depth == 3 { name = value($0); current = "" }
            /"Version" *:/ && depth == 3 { current = value($0) }
            /"Version" *:/ && depth == 5 && name != "" && !(name in seen) {
                seen[name] = 1; print name ": " (current == "" ? "?" : current) " -> " value($0)
            }
            { line = $0; depth += gsub(/[{[]/, "", line) - gsub(/[]}]/, "", line) }
        ' "$WORK/firmware.json")"
    fi
    COUNT="$(printf '%s' "$UPGRADES" | wc -w | tr -d ' ')"
    FW_COUNT="$(printf '%s' "$FIRMWARE" | grep -c . || true)"
    if [ "$COUNT" -gt 0 ] || [ "$FW_COUNT" -gt 0 ]; then
        say "📦 System updates available: $COUNT package(s), $FW_COUNT firmware update(s)."
        if [ -n "$NVIDIA_UPGRADES" ]; then say "   NVIDIA (DGX, CUDA, driver): $NVIDIA_UPGRADES"; fi
        if [ "$FW_COUNT" -gt 0 ]; then printf '%s\n' "$FIRMWARE" | sed 's/^/   firmware: /'; fi
        if have_terminal; then
            printf '❓ Install them after Mightling (a reboot is needed afterwards; this script never reboots)? [y/N] '
            answer=""
            if [ -t 0 ]; then read -r answer || true; else read -r answer < /dev/tty || true; fi
            case "$answer" in y|Y|yes|Yes|YES) INSTALL_UPDATES=1 ;; esac
        fi
    else
        say "✅ No system updates."
    fi
fi

# -- the release -----------------------------------------------------------------------------

if [ -n "$FROM" ]; then
    # A bundle: the asset names are the files in the folder, and "fetching" one copies it.
    TAG="bundle"
    [ -f "$FROM/VERSION" ] && TAG="$(head -n 1 "$FROM/VERSION")"
    for file in "$FROM"/*; do
        if [ -f "$file" ]; then printf '%s\t%s\n' "$(basename "$file")" "$file"; fi
    done > "$WORK/assets.tsv"
    asset_url() { awk -F'\t' -v name="$1" '$1 == name {print $2; exit}' "$WORK/assets.tsv"; }
    fetch() {  # fetch <asset name>: copies it into $WORK, or fails
        local path; path="$(asset_url "$1")"
        [ -n "$path" ] || return 1
        cp "$path" "$WORK/$1"
    }
else
    # A token is used when GH_TOKEN or GITHUB_TOKEN is set; a logged-in gh's token only for a fork
    # named by MIGHTLING_RELEASE_REPO (the public repository needs none, and a gh login carries far more
    # access than reading a release). It is only ever sent to the API root, never to a download URL
    # the release names.
    TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
    if [ -z "$TOKEN" ] && [ -n "${MIGHTLING_RELEASE_REPO:-}" ] && command -v gh >/dev/null 2>&1; then
        TOKEN="$(gh auth token 2>/dev/null || true)"
    fi
    auth=()
    [ -n "$TOKEN" ] && auth=(-H "Authorization: Bearer $TOKEN")

    if [ -n "$VERSION" ]; then
        release_url="$API/repos/$REPO/releases/tags/v${VERSION#v}"
    else
        release_url="$API/repos/$REPO/releases/latest"
    fi
    if ! curl -fsSL ${auth[@]+"${auth[@]}"} -H "Accept: application/vnd.github+json" "$release_url" -o "$WORK/release.json"; then
        say "❌ Could not read $release_url" >&2
        if [ -z "$TOKEN" ]; then
            say "   Check the network and that $REPO has a published release. If the repository is" >&2
            say "   private, set GH_TOKEN (or GITHUB_TOKEN) to a token that can read it, or log in with" >&2
            say "   \`gh auth login\`, and run this again." >&2
        else
            say "   Check that the token can read $REPO and that the release exists (drafts are not listed)." >&2
        fi
        exit 1
    fi

    TAG="$(awk -F'"' '/^  "tag_name":/ {print $4; exit}' "$WORK/release.json")"
    [ -n "$TAG" ] || fail "the release at $release_url has no tag."

    # "name<TAB>API url" for every asset. GitHub prints an asset's own "url" before its "name"; the
    # uploader's "url" in between is a user URL and is not taken.
    awk -F'"' '
        /"url": "[^"]*\/releases\/assets\/[0-9]+"/ { url = $4 }
        /^      "name":/ && url != "" { print $4 "\t" url; url = "" }
    ' "$WORK/release.json" > "$WORK/assets.tsv"

    asset_url() { awk -F'\t' -v name="$1" '$1 == name {print $2; exit}' "$WORK/assets.tsv"; }

    fetch() {  # fetch <asset name>: downloads it into $WORK, or fails
        local url; url="$(asset_url "$1")"
        [ -n "$url" ] || return 1
        local header=()
        case "$url" in "$API"/*) header=(${auth[@]+"${auth[@]}"}) ;; esac
        curl -fsSL ${header[@]+"${header[@]}"} -H "Accept: application/octet-stream" "$url" -o "$WORK/$1"
    }
fi

say "🐧 Mightling $TAG for $TARGET, role: $ROLE"

SUMS="ling-$TARGET.sha256sums"
if ! fetch "$SUMS"; then
    fail "release $TAG has no binaries for $TARGET (no $SUMS). Published targets: $(awk -F'\t' '/sha256sums/ {sub(/^ling-/, "", $1); sub(/\.sha256sums$/, "", $1); printf "%s ", $1}' "$WORK/assets.tsv")"
fi

# -- the signature ---------------------------------------------------------------------------

# Whether a tag is SIGNED_SINCE or newer. A tag that is not a version is treated as new: a name
# nobody can read is no reason to skip the check (`ling update` decides the same way).
signing_required() {
    awk -v tag="${1#v}" -v since="$SIGNED_SINCE" 'BEGIN {
        if (tag !~ /^[0-9]+\.[0-9]+\.[0-9]+/) exit 0
        split(tag, a, /[.+-]/); split(since, b, ".")
        for (i = 1; i <= 3; i++) { if (a[i] + 0 > b[i] + 0) exit 0; if (a[i] + 0 < b[i] + 0) exit 1 }
        exit 0
    }'
}
signers() {  # the allowed-signers lines for the keys of a public-key file on stdin
    awk 'NF && $1 !~ /^#/ { print "mightling-release " $1 " " $2 }'
}

if [ -n "$(asset_url SHA256SUMS.sig)" ]; then
    command -v ssh-keygen >/dev/null 2>&1 || fail "ssh-keygen (OpenSSH 8.1 or newer) is needed to \
check the release's signature and was not found. On Debian and Ubuntu: sudo apt install openssh-client."
    fetch SHA256SUMS.sig || fail "could not download SHA256SUMS.sig."
    fetch SHA256SUMS || fail "release $TAG has SHA256SUMS.sig but no SHA256SUMS; nothing was installed."
    printf '%s\n' "$RELEASE_KEYS" | signers > "$WORK/allowed_signers"
    # A key rotation: the release names the new key, signed by a key trusted here under a namespace
    # of its own (specs/DREAMFERENCE_RELEASE_SIGNING.md §5).
    if [ -n "$(asset_url release-key-transition.pub)" ]; then
        fetch release-key-transition.pub && fetch release-key-transition.pub.sig \
            || fail "release $TAG names a new release key but its endorsement could not be downloaded."
        ssh-keygen -Y verify -f "$WORK/allowed_signers" -I mightling-release -n mightling-release-key \
            -s "$WORK/release-key-transition.pub.sig" < "$WORK/release-key-transition.pub" >/dev/null 2>&1 \
            || fail "release $TAG names a new release key that no trusted key has signed; nothing was installed."
        signers < "$WORK/release-key-transition.pub" >> "$WORK/allowed_signers"
    fi
    if ! ssh-keygen -Y verify -f "$WORK/allowed_signers" -I mightling-release -n mightling-release \
        -s "$WORK/SHA256SUMS.sig" < "$WORK/SHA256SUMS" > "$WORK/verify.out" 2>&1; then
        fail "release $TAG failed its signature check: SHA256SUMS is not signed by Mightling's \
release key ($(tail -n 1 "$WORK/verify.out")). Nothing was installed."
    fi
    # The per-target sums file is one of the files SHA256SUMS lists, which is what ties every
    # binary checked below to the signature.
    wanted="$(awk -v file="$SUMS" '{ f = $2; sub(/^\*/, "", f); if (f == file) { print $1; exit } }' "$WORK/SHA256SUMS")"
    [ -n "$wanted" ] || fail "SHA256SUMS has no checksum for $SUMS; nothing was installed."
    [ "$(sha256 "$WORK/$SUMS")" = "$wanted" ] \
        || fail "$SUMS does not match its checksum in SHA256SUMS; nothing was installed."
    say "🔏 Release $TAG is signed by Mightling's release key."
elif [ -n "$FROM" ] && [ "${TAG#this-}" != "$TAG" ]; then
    # `ling-admin node provision` bundles the build this node runs (VERSION `this-<digest>`): no
    # release job made it, so there is no release signature to check, only its checksums. A
    # bundle of a release (VERSION `vX.Y.Z`) carries that release's signature and is checked above.
    say "⚠️  Bundle $TAG is a build copied from another node, not a release; it is checked against its checksums only."
elif signing_required "$TAG"; then
    fail "release $TAG is not signed (it has no SHA256SUMS.sig); nothing was installed. Every release \
from $SIGNED_SINCE on is signed by Mightling's release key, so an unsigned one did not come from its release job."
else
    say "⚠️  Release $TAG predates signed releases ($SIGNED_SINCE); it is checked against its checksums only."
fi

# Required, then optional: releases before the web commands and the code index were Rust binaries
# do not carry them, which is how `ling update` treats them too.
REQUIRED="ling codex-code-mode-host"
OPTIONAL="ling-search ling-fetch ling-code"
INSTALLED=""
for name in $REQUIRED $OPTIONAL; do
    asset="$name-$TARGET.gz"
    if ! fetch "$asset"; then
        case " $REQUIRED " in *" $name "*) fail "release $TAG is missing $asset." ;; esac
        continue
    fi
    wanted="$(awk -v file="$asset" '{ f = $2; sub(/^\*/, "", f); if (f == file) { print $1; exit } }' "$WORK/$SUMS")"
    [ -n "$wanted" ] || fail "$SUMS has no checksum for $asset; nothing was installed."
    [ "$(sha256 "$WORK/$asset")" = "$wanted" ] \
        || fail "$asset does not match its checksum in $SUMS; nothing was installed."
    gzip -dc "$WORK/$asset" > "$WORK/$name"
    INSTALLED="$INSTALLED $name"
done

# The product was called Puffin before 1.5 (specs/DREAMFERENCE_RENAME_MIGHTLING.md §4). An old
# installation's folder becomes the new one, keeping the code index's tools in it, and the old
# command links go: the old names are not kept as aliases. `ling` moves the agent's home and the
# rest the first time it runs.
OLD_DIR="$(dirname "$INSTALL_DIR")/puffin"
if [ -d "$OLD_DIR" ] && [ ! -e "$INSTALL_DIR" ]; then
    mv "$OLD_DIR" "$INSTALL_DIR"
    for pair in puffin:ling puffin-search:ling-search puffin-fetch:ling-fetch puffin-code:ling-code; do
        old="${pair%%:*}"; new="${pair#*:}"
        [ -e "$INSTALL_DIR/bin/$old" ] && [ ! -e "$INSTALL_DIR/bin/$new" ] && mv "$INSTALL_DIR/bin/$old" "$INSTALL_DIR/bin/$new"
    done
    say "🐦 Puffin is now Mightling: moved $OLD_DIR to $INSTALL_DIR"
fi
for old in puffin puffin-search puffin-fetch puffin-code puffin-app puffin-admin; do
    [ -L "$LINK_DIR/$old" ] && rm -f "$LINK_DIR/$old"
done

# Everything is checked before anything is placed. Each file is renamed over the old one, so a
# running session keeps its binary and a new one never sees a half-written file.
mkdir -p "$INSTALL_DIR/bin" "$LINK_DIR"
link() {  # link <target> <name>: ~/.local/bin/<name> -> target, never over a real file
    if [ -e "$LINK_DIR/$2" ] && [ ! -L "$LINK_DIR/$2" ]; then
        say "⚠️  $LINK_DIR/$2 exists and is not a link; leaving it. Run $1 directly."
        return
    fi
    ln -sfn "$1" "$LINK_DIR/$2"
}
for name in $INSTALLED; do
    install -m 0755 "$WORK/$name" "$INSTALL_DIR/bin/.$name.new"
    mv -f "$INSTALL_DIR/bin/.$name.new" "$INSTALL_DIR/bin/$name"
    # Codex looks for its Code Mode host beside its own executable, so that one needs no link.
    [ "$name" = "codex-code-mode-host" ] || link "$INSTALL_DIR/bin/$name" "$name"
done
# What was installed, for `node provision`'s state probe and drift report.
printf '%s\n' "$TAG" > "$INSTALL_DIR/VERSION"
say "✅ Installed$INSTALLED in $INSTALL_DIR/bin"
step done "Mightling $TAG installed:$INSTALLED"

# -- the node --------------------------------------------------------------------------------

if [ "$ROLE" = "node" ]; then
    command -v python3 >/dev/null 2>&1 || fail "the node needs python3 (3.10 or newer) and it was not found."
    command -v docker >/dev/null 2>&1 \
        || say "⚠️  docker was not found. The model server runs in Docker; install it before \`ling-admin server start\`."

    wheel="$(awk -F'\t' '$1 ~ /^dreamference-.*\.whl$/ {print $1; exit}' "$WORK/assets.tsv")"
    [ -n "$wheel" ] || fail "release $TAG has no Python wheel, so the node cannot be installed from it."
    fetch "$wheel" || fail "could not download $wheel."
    # The wheel is checked like the binaries, against the release-wide SHA256SUMS (signed, and
    # already checked above, from 1.5 on); an older release has none, which is said rather than
    # skipped silently.
    if [ -f "$WORK/SHA256SUMS" ] || fetch SHA256SUMS; then
        wanted="$(awk -v file="$wheel" '{ f = $2; sub(/^\*/, "", f); if (f == file) { print $1; exit } }' "$WORK/SHA256SUMS")"
        [ -n "$wanted" ] || fail "SHA256SUMS has no checksum for $wheel; nothing was installed."
        [ "$(sha256 "$WORK/$wheel")" = "$wanted" ] \
            || fail "$wheel does not match its checksum in SHA256SUMS; nothing was installed."
    else
        say "⚠️  Release $TAG has no SHA256SUMS, so $wheel is installed without a checksum check."
    fi

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        say "🐍 Creating $VENV_DIR ..."
        if ! python3 -m venv "$VENV_DIR" > "$WORK/venv.log" 2>&1; then
            # DGX OS ships python3 without venv's ensurepip (python3-venv): measured on a fresh
            # GX10, 2026-10-08. It is installed here rather than left as a next step.
            rm -rf "$VENV_DIR"
            if [ "$NODE_ROOT" = 0 ] && [ "$(id -u)" != 0 ]; then
                sudo -n true 2>/dev/null && SUDO="sudo -n" \
                    || fail "python3 could not create a virtualenv: install python3-venv (sudo apt install python3-venv), then run this again."
            fi
            say "🐍 Installing python3-venv, which this system's python3 lacks ..."
            $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y python3-venv \
                >> "$WORK/venv.log" 2>&1 < /dev/null \
                || fail "could not install python3-venv: $(tail -n 2 "$WORK/venv.log" | tr '\n' ' ')"
            python3 -m venv "$VENV_DIR" >> "$WORK/venv.log" 2>&1 \
                || fail "python3 could not create a virtualenv even with python3-venv: $(tail -n 2 "$WORK/venv.log" | tr '\n' ' ')"
            step done "python3-venv installed (this system's python3 lacked it)"
        fi
    fi
    say "📦 Installing ling-admin and what it depends on (about 6 GB with PyTorch; a few minutes) ..."
    pip_index=()
    if [ -n "$FROM" ] && [ -d "$FROM/wheelhouse" ]; then
        # Every dependency is in the bundle: no package index is asked.
        pip_index=(--no-index --find-links "$FROM/wheelhouse")
    else
        "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
    fi
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade ${pip_index[@]+"${pip_index[@]}"} "$WORK/$wheel"
    link "$VENV_DIR/bin/ling-admin" ling-admin
    say "✅ Installed ling-admin $TAG in $VENV_DIR"
    step done "ling-admin $TAG installed in $VENV_DIR"

    # From here on a closed reader or a dropped ssh session does not stop the install halfway: the
    # rest runs to its summary (the long part is logged to a file anyway).
    trap '' HUP PIPE
    mkdir -p "$LOG_DIR"
    say "📝 The long part (model download, server start, system updates) is logged to $LOG"

    # Every `ling-admin` here has no terminal on its standard input, so none of them can ask a
    # question (the sandbox check of every run asks one at a terminal).
    admin() { "$VENV_DIR/bin/ling-admin" "$@" < /dev/null; }

    if [ "$NODE_ROOT" = 1 ]; then
        # The settings a model load is refused without, applied: each command is printed first,
        # and runs with `sudo -n` on the password given above.
        say ""
        if admin host setup --yes; then
            step done "host settings applied (swap, sysctls, earlyoom, sysstat, bubblewrap)"
        else
            step failed "host settings: \`ling-admin host check\` lists what is left, \`ling-admin host setup\` applies it"
        fi

        # Docker without sudo, which NVIDIA's first-boot wizard does not set up.
        if ! getent group docker > /dev/null 2>&1; then
            step failed "docker group: there is none, so Docker is not installed (DGX OS ships it); install Docker and the NVIDIA Container Toolkit, then run this again"
        elif id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx docker; then
            step done "docker group: $USER_NAME is in it"
        elif $SUDO usermod -aG docker "$USER_NAME" < /dev/null; then
            step done "docker group: $USER_NAME added (your next login has it; this script used \`sg docker\` meanwhile)"
        else
            step failed "docker group: \`sudo usermod -aG docker $USER_NAME\` failed"
        fi

        # Lingering: without it the user's systemd stops at logout, and with it every job sent
        # from another node and every Night Shift task.
        if [ -e "$LINGER_DIR/$USER_NAME" ]; then
            step done "lingering is on (jobs and Night Shift outlive a logout)"
        elif $SUDO loginctl enable-linger "$USER_NAME" < /dev/null; then
            step done "lingering turned on (jobs and Night Shift outlive a logout)"
        else
            step failed "lingering: \`sudo loginctl enable-linger $USER_NAME\` failed"
        fi
    else
        step off "host settings, docker group and lingering: --no-host-setup (\`sudo ling-admin node prepare\` applies them)"
    fi

    # A machine with the node half is a node: its id is written now, so `ling` here uses this
    # machine's own model server and never looks for another one on the network
    # (specs/DREAMFERENCE_MIGHTLING_NODE.md §6.1, §9).
    admin node id > /dev/null || true
    # Then it is offered to the local network: the model, web search and the web UI, to every
    # machine on it (the command says so). No question is asked; --no-advertise is the opt-out.
    if [ "$ADVERTISE" = 1 ]; then
        say ""
        if admin node enable --yes; then
            step done "advertised on the local network (\`ling-admin node disable\` stops it)"
        else
            step failed "advertising: \`ling-admin node enable\` says why"
        fi
    else
        step off "advertising: --no-advertise (\`ling-admin node enable\` offers this node later)"
    fi

    # The model: downloaded, then served. Docker is reached through `sg docker` when this login
    # predates the group (`node provision` does the same, FleetSession.with_docker_group).
    with_docker() {
        if id -nG | tr ' ' '\n' | grep -qx docker \
            || ! id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx docker \
            || ! command -v sg > /dev/null 2>&1; then
            admin "$@"
        else
            sg docker -c "$(printf '%q ' "$VENV_DIR/bin/ling-admin" "$@") < /dev/null"
        fi
    }
    if [ "$MODEL" = 1 ]; then
        say ""
        say "⬇️  Downloading the default model (tens of GB; this is the long part) ... log: $LOG"
        if with_docker model download >> "$LOG" 2>&1; then
            step done "default model downloaded"
            say "🚀 Starting the model server ... log: $LOG"
            if with_docker server start >> "$LOG" 2>&1; then
                step done "model server started (\`ling-admin status\`)"
            else
                step failed "model server: \`ling-admin server start\` failed, see $LOG"
            fi
        else
            step failed "default model: \`ling-admin model download\` failed, see $LOG"
            step failed "model server: not started, because the model is missing; \`ling-admin server start\` downloads and starts it"
        fi
    else
        step off "model: --no-model (\`ling-admin server start\` downloads and serves it)"
    fi
fi

# -- system updates, installed if asked --------------------------------------------------------

UPDATE_COMMANDS="sudo apt-get update && sudo apt-get full-upgrade"
FIRMWARE_COMMANDS="sudo fwupdmgr refresh && sudo fwupdmgr update"
if [ "$INSTALL_UPDATES" = 1 ]; then
    mkdir -p "$LOG_DIR"
    say ""
    say "📦 Installing the system updates ... log: $LOG"
    if [ -n "$UPGRADES" ]; then
        if $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 \
                -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -y full-upgrade \
                >> "$LOG" 2>&1 < /dev/null; then
            step done "system packages upgraded ($(printf '%s' "$UPGRADES" | wc -w | tr -d ' '))"
        else
            step failed "system packages: \`$UPDATE_COMMANDS\` failed, see $LOG"
        fi
    fi
    if [ -n "$FIRMWARE" ]; then
        if $SUDO fwupdmgr update -y --no-reboot-check >> "$LOG" 2>&1 < /dev/null; then
            step done "firmware staged; it is written during the next reboot"
        else
            step failed "firmware: \`$FIRMWARE_COMMANDS\` failed, see $LOG"
        fi
    fi
    REBOOT=1
elif [ -n "$UPGRADES" ] || [ -n "$FIRMWARE" ]; then
    detail=""
    if [ -n "$UPGRADES" ]; then
        detail="$(printf '%s' "$UPGRADES" | wc -w | tr -d ' ') package(s)"
        if [ -n "$NVIDIA_UPGRADES" ]; then detail="$detail, NVIDIA's: $NVIDIA_UPGRADES"; fi
        detail="$detail; install: $UPDATE_COMMANDS"
    fi
    if [ -n "$FIRMWARE" ]; then
        detail="${detail:+$detail; }$(printf '%s' "$FIRMWARE" | grep -c .) firmware update(s); install: $FIRMWARE_COMMANDS"
    fi
    step available "system updates, available, not installed: $detail; then reboot"
fi

# -- the summary -----------------------------------------------------------------------------

case ":$PATH:" in
    *":$LINK_DIR:"*) ;;
    *) say ""; say "⚠️  $LINK_DIR is not on your PATH. Add it:  export PATH=\"$LINK_DIR:\$PATH\"" ;;
esac

FAILED=0
say ""
if [ "$ROLE" = "node" ]; then
    say "━━ Mightling $TAG on $(uname -n 2>/dev/null || echo this machine) ━━"
    for entry in "${STEPS[@]}"; do
        state="${entry%%|*}"; text="${entry#*|}"
        case "$state" in
            done)      say "  ✅ $text" ;;
            failed)    say "  ❌ $text"; FAILED=1 ;;
            off)       say "  ⚪ $text" ;;
            available) say "  📦 $text" ;;
        esac
    done
    if [ "${REBOOT:-0}" = 1 ]; then
        say "  🔁 Reboot now to finish the updates: sudo reboot. The model server comes back by itself"
        say "     (Docker restarts it); the firmware is written during that reboot."
    fi
    say ""
    if [ "$FAILED" = 1 ]; then
        say "⚠️  Not everything succeeded: the lines with ❌ say what to run. Log: $LOG"
    elif [ "$ADVERTISE" = 1 ]; then
        say "🎉 Done. \`ling\` is the terminal agent here, and \`ling\` on your other computers finds this node."
    else
        say "🎉 Done. \`ling\` is the terminal agent."
    fi
else
    say "🎉 Done. \`ling\` needs a Mightling node to talk to: start one on a GB10, then run \`ling\`."
fi
exit "$FAILED"
