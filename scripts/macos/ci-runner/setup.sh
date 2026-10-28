#!/bin/bash
# Makes a Mac the self-hosted GitHub Actions runner for Mightling's macOS client build
# (build-clients.yml routes aarch64-apple-darwin to it when the MACOS_RUNNER variable is set).
# One command per machine, idempotent, so moving the runner to another Mac is this script again:
#
#   1. From a checkout, as the Mac's administrator, no sudo: copy this folder over and register the
#      runner while the registration token is fresh (an hour).
#        scp -r scripts/macos/ci-runner mac:/tmp/
#        token="$(gh api -X POST repos/dreamference/mightling/actions/runners/registration-token --jq .token)"
#        ssh mac "bash /tmp/ci-runner/setup.sh --stage --token $token --name mac-mini"
#   2. On the Mac, the one step that needs the administrator's password:
#        sudo bash /Users/Shared/mac-runner/ci-runner/setup.sh
#
# Step 2 creates a hidden non-administrator account (ghrunner, a random password nobody keeps),
# moves the staged runner into its home, installs the job hooks (job-started.sh refuses every event
# but push, workflow_dispatch and schedule, every other repository, ref and workflow) and the
# runner's environment root-owned, puts Python 3.12 in the runner's tool cache (setup-python cannot
# install one without sudo) and rustup in its home, and runs the runner as a LaunchDaemon, at boot,
# with nobody logged in. `sudo bash setup.sh --remove` stops and removes it (unregister it on GitHub
# too: Settings, Actions, Runners). Labels: self-hosted, macOS, ARM64, mac-mini, the value of
# MACOS_RUNNER, so a replacement Mac with the same labels takes over without a workflow change.
set -euo pipefail

REPO_URL=https://github.com/dreamference/mightling
RUNNER_VERSION=2.338.2
RUNNER_SHA256_ARM64=25a065af409ffa317ab4d5a3b614dd15fc9c40f2172bc1b35a1b1258e919a388
RUNNER_SHA256_X64=109fff505a34014a7e8d1acb850c33d404811102f572b8fb9788e2fff013cc49
PYTHON_VERSION=3.12.16
PYTHON_BUILD=20261026
PYTHON_SHA256_ARM64=fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e
PYTHON_SHA256_X64=a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3
STAGE=/Users/Shared/mac-runner
USER_NAME=ghrunner
HOME_DIR=/Users/$USER_NAME
RUNNER_DIR=$HOME_DIR/actions-runner
HOOKS_DIR="/Library/Application Support/mightling-ci"
LOCK_DIR=/Users/Shared/ci-lock
LABEL=actions.runner.dreamference-mightling.mac
PLIST=/Library/LaunchDaemons/$LABEL.plist

mode=install token="" name="$(scutil --get LocalHostName 2>/dev/null || hostname -s)" jobs=""
labels="self-hosted,macOS,ARM64,mac-mini"
while (($#)); do
    case "$1" in
        --stage) mode=stage ;;
        --remove) mode=remove ;;
        --token) token="$2"; shift ;;
        --name) name="$2"; shift ;;
        --labels) labels="$2"; shift ;;
        --jobs) jobs="$2"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

case "$(uname -m)" in
    arm64) arch=arm64 runner_sha=$RUNNER_SHA256_ARM64 python_arch=aarch64 python_sha=$PYTHON_SHA256_ARM64 ;;
    x86_64) arch=x64 runner_sha=$RUNNER_SHA256_X64 python_arch=x86_64 python_sha=$PYTHON_SHA256_X64 ;;
    *) echo "unsupported machine $(uname -m)" >&2; exit 1 ;;
esac

fetch() { # url sha256 destination
    curl -fsSL -o "$3" "$1"
    echo "$2  $3" | shasum -a 256 -c - >/dev/null || { echo "checksum mismatch: $1" >&2; rm -f "$3"; exit 1; }
}

if [[ $mode == stage ]]; then
    [[ $EUID -ne 0 ]] || { echo "--stage runs as the administrator, without sudo" >&2; exit 1; }
    [[ -n "$token" ]] || { echo "--stage needs --token (a registration token, valid for an hour)" >&2; exit 1; }
    mkdir -p "$STAGE" && chmod 700 "$STAGE"
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [[ "$here" == "$STAGE/ci-runner" ]] || { rm -rf "$STAGE/ci-runner"; cp -R "$here" "$STAGE/ci-runner"; }
    if [[ -f "$STAGE/actions-runner/.runner" ]]; then echo "already staged: $STAGE/actions-runner"; exit 0; fi
    rm -rf "$STAGE/actions-runner" && mkdir -p "$STAGE/actions-runner" && cd "$STAGE/actions-runner"
    fetch "https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/actions-runner-osx-$arch-$RUNNER_VERSION.tar.gz" "$runner_sha" runner.tgz
    tar xzf runner.tgz && rm runner.tgz
    ./config.sh --unattended --url "$REPO_URL" --token "$token" --name "$name" --labels "$labels" --work _work --replace
    echo "staged; now, on this Mac: sudo bash $STAGE/ci-runner/setup.sh"
    exit 0
fi

[[ $EUID -eq 0 ]] || { echo "run with sudo (or --stage as the administrator first)" >&2; exit 1; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $mode == remove ]]; then
    launchctl bootout system "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    if id "$USER_NAME" >/dev/null 2>&1; then sysadminctl -deleteUser "$USER_NAME" 2>&1 | grep -v -i password || true; fi
    rm -rf "$HOME_DIR" "$HOOKS_DIR"
    echo "removed; unregister the runner on GitHub (Settings, Actions, Runners) if it is still listed"
    exit 0
fi

xcode-select -p >/dev/null 2>&1 || { echo "no command line tools: run xcode-select --install first" >&2; exit 1; }
[[ -f "$here/job-started.sh" && -f "$here/job-completed.sh" ]] || { echo "job hooks missing beside $0" >&2; exit 1; }

if ! id "$USER_NAME" >/dev/null 2>&1; then
    sysadminctl -addUser "$USER_NAME" -fullName "GitHub Actions runner" -shell /bin/bash -home "$HOME_DIR" \
        -password "$(openssl rand -base64 30)" 2>&1 | grep -v -i password || true
    createhomedir -c -u "$USER_NAME" >/dev/null
    dscl . -create "/Users/$USER_NAME" IsHidden 1
fi
if dseditgroup -o checkmember -m "$USER_NAME" admin >/dev/null 2>&1; then
    echo "$USER_NAME is an administrator; refusing" >&2
    exit 1
fi

if [[ ! -f "$RUNNER_DIR/.runner" ]]; then
    [[ -f "$STAGE/actions-runner/.runner" ]] || { echo "no staged runner: run --stage first" >&2; exit 1; }
    rm -rf "$RUNNER_DIR"
    mv "$STAGE/actions-runner" "$RUNNER_DIR"
fi
cp "$RUNNER_DIR/bin/runsvc.sh" "$RUNNER_DIR/runsvc.sh"
chown -R "$USER_NAME:staff" "$HOME_DIR"
as_runner() { sudo -u "$USER_NAME" -H env HOME="$HOME_DIR" "$@"; }

# Python for actions/setup-python, which looks in the tool cache first.
python_dir="$RUNNER_DIR/_work/_tool/Python/$PYTHON_VERSION"
if [[ ! -f "$python_dir/$arch.complete" ]]; then
    as_runner mkdir -p "$python_dir"
    tmp="$(mktemp -d)"
    fetch "https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_BUILD/cpython-$PYTHON_VERSION%2B$PYTHON_BUILD-$python_arch-apple-darwin-install_only.tar.gz" "$python_sha" "$tmp/python.tgz"
    tar xzf "$tmp/python.tgz" -C "$tmp" && rm -rf "${python_dir:?}/$arch" && mv "$tmp/python" "$python_dir/$arch"
    ln -sf python3 "$python_dir/$arch/bin/python"
    touch "$python_dir/$arch.complete"
    rm -rf "$tmp"
    chown -R "$USER_NAME:staff" "$RUNNER_DIR/_work"
fi
# rustup only: codex-rs/rust-toolchain.toml pins the toolchain, which rustup fetches on first use.
if [[ ! -x "$HOME_DIR/.cargo/bin/rustup" ]]; then
    curl -fsSL https://sh.rustup.rs -o /tmp/rustup-init.sh
    as_runner sh /tmp/rustup-init.sh -y --default-toolchain none --profile minimal --no-modify-path
    rm -f /tmp/rustup-init.sh
fi

# Parallel rustc processes: one per 3 GB of memory, at most one per core (8 GB: 2).
if [[ -z "$jobs" ]]; then
    memory_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
    jobs=$(( memory_gb / 3 )); cores=$(sysctl -n hw.ncpu)
    (( jobs <= cores )) || jobs=$cores
    (( jobs >= 1 )) || jobs=1
fi

# The hooks and the runner's environment: root's, read-only to the runner account.
mkdir -p "$HOOKS_DIR"
install -o root -g wheel -m 755 "$here/job-started.sh" "$HOOKS_DIR/job-started.sh"
install -o root -g wheel -m 755 "$here/job-completed.sh" "$HOOKS_DIR/job-completed.sh"
cat > "$RUNNER_DIR/.env" <<ENV
ACTIONS_RUNNER_HOOK_JOB_STARTED=$HOOKS_DIR/job-started.sh
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=$HOOKS_DIR/job-completed.sh
CARGO_BUILD_JOBS=$jobs
LANG=en_US.UTF-8
ENV
echo "$HOME_DIR/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" > "$RUNNER_DIR/.path"
chown root:wheel "$RUNNER_DIR/.env" "$RUNNER_DIR/.path"
chmod 644 "$RUNNER_DIR/.env" "$RUNNER_DIR/.path"

# The lock CI shares with hand builds: anyone in staff may take it.
mkdir -p "$LOCK_DIR"
chown root:staff "$LOCK_DIR"
chmod 775 "$LOCK_DIR"

as_runner mkdir -p "$HOME_DIR/Library/Logs/$LABEL"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>UserName</key><string>$USER_NAME</string>
  <key>GroupName</key><string>staff</string>
  <key>ProgramArguments</key><array><string>$RUNNER_DIR/runsvc.sh</string></array>
  <key>WorkingDirectory</key><string>$RUNNER_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME_DIR</string>
    <key>USER</key><string>$USER_NAME</string>
    <key>ACTIONS_RUNNER_SVC</key><string>1</string>
    <key>ACTIONS_RUNNER_HOOK_JOB_STARTED</key><string>$HOOKS_DIR/job-started.sh</string>
    <key>ACTIONS_RUNNER_HOOK_JOB_COMPLETED</key><string>$HOOKS_DIR/job-completed.sh</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>SessionCreate</key><true/>
  <key>StandardOutPath</key><string>$HOME_DIR/Library/Logs/$LABEL/stdout.log</string>
  <key>StandardErrorPath</key><string>$HOME_DIR/Library/Logs/$LABEL/stderr.log</string>
</dict>
</plist>
PLIST
chown root:wheel "$PLIST"
chmod 644 "$PLIST"
launchctl bootout system "$PLIST" 2>/dev/null || true
launchctl bootstrap system "$PLIST"
# A runner that sleeps queues every job for a day.
pmset -a sleep 0 disksleep 0 >/dev/null
sleep 5
launchctl print "system/$LABEL" | grep -E "^\s*(state|pid) =" || true
echo "done: $LABEL runs as $USER_NAME (labels $labels, CARGO_BUILD_JOBS=$jobs)"
