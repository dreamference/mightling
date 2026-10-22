#!/bin/sh
# Mightling's desktop app, unpacked from a zip or run from an `out/Mightling-linux-<arch>/` folder:
# writes the AppArmor profile its Chromium sandbox needs, for the binary beside this script.
#
# Ubuntu 24.04 and later refuse a user namespace to programs without a profile
# (kernel.apparmor_restrict_unprivileged_userns=1), and the app ships no SUID `chrome-sandbox`, so
# without the profile it dies at start with "No usable sandbox!". The .deb's postinst writes the
# same profile for /usr/lib/mightling/Mightling (linux/postinst); nothing of ours installs an
# unpacked build, so this script, copied beside the binary by forge.config.js's postPackage hook,
# does it once, with sudo, after printing the commands it runs.
#
# Usage: ./install-sandbox-profile.sh            write and load the profile (sudo asks once)
#        ./install-sandbox-profile.sh --print    print the commands and the profile, change nothing
set -e

PROFILE_NAME=mightling-unpacked
PROFILE="/etc/apparmor.d/$PROFILE_NAME"
# Read only, never written; the two variables exist so the repository's tests can point them at a
# scratch folder (tests/test_desktop.py).
PROFILES_DIR=${MIGHTLING_APPARMOR_DIR:-/etc/apparmor.d}
SYSCTL=${MIGHTLING_USERNS_SYSCTL:-/proc/sys/kernel/apparmor_restrict_unprivileged_userns}
# Setuid sandbox helpers Chromium browsers install as root. Without an administrator, Chromium
# runs an unpacked build (a binary the user owns) with one named by CHROME_DEVEL_SANDBOX; `ling app`
# does that by itself (specs/DREAMFERENCE_MIGHTLING_NO_SUDO.md). The variable is for the tests.
HELPERS=${MIGHTLING_SANDBOX_HELPERS-/opt/google/chrome/chrome-sandbox /opt/microsoft/msedge/msedge-sandbox /opt/brave.com/brave/chrome-sandbox}

DIR=$(cd "$(dirname "$0")" && pwd -P)
BINARY="$DIR/Mightling"

if [ ! -x "$BINARY" ]; then
    echo "Mightling: no Mightling binary beside this script (in $DIR)." >&2
    exit 1
fi
case "$BINARY" in
    *'"'*) echo "Mightling: an AppArmor profile cannot name a path with a double quote in it: $BINARY" >&2; exit 1 ;;
esac

if [ "$(cat "$SYSCTL" 2>/dev/null)" != 1 ]; then
    echo "Nothing to do: this system does not restrict user namespaces, so the app's sandbox needs no profile."
    exit 0
fi

# A profile naming this binary already (the .deb's, `ling-admin desktop install`'s, or this
# script's from an earlier run) is enough; a second one with the same attachment would conflict.
# A file that names it without `userns,` grants nothing (DesktopInstaller.covering_profile, the same test).
COVERED=$(grep -F -l -s -- "\"$BINARY\"" "$PROFILES_DIR"/* 2>/dev/null | xargs -r -d '\n' grep -F -l -s -- "userns," 2>/dev/null | head -n 1 || true)
if [ -n "$COVERED" ]; then
    echo "Nothing to do: $COVERED already lets $BINARY create its sandbox."
    exit 0
fi

profile() {
    cat <<EOF
# Written by Mightling's install-sandbox-profile.sh. It lets the desktop app unpacked at
# $DIR create the user namespace that Chromium's sandbox needs, which Ubuntu otherwise
# refuses to programs without a profile (kernel.apparmor_restrict_unprivileged_userns=1). Same shape
# as the .deb's profile and Ubuntu's own profiles for sandboxing programs. One unpacked copy at a
# time: running the script from another folder moves the profile there.
abi <abi/4.0>,
include <tunables/global>

profile $PROFILE_NAME "$BINARY" flags=(unconfined) {
  userns,

  # Site-specific additions and overrides. See local/README for details.
  include if exists <local/$PROFILE_NAME>
}
EOF
}

echo "Mightling's sandbox needs an AppArmor profile for $BINARY. The commands, as root:"
echo "  sudo tee $PROFILE    (the profile below)"
echo "  sudo apparmor_parser -r -T -W $PROFILE"
for HELPER in $HELPERS; do
    if [ -f "$HELPER" ] && [ -u "$HELPER" ] && [ "$(stat -c %u "$HELPER" 2>/dev/null)" = 0 ]; then
        echo "Without an administrator: ling app, or  CHROME_DEVEL_SANDBOX=$HELPER $BINARY  uses that browser's setuid sandbox helper instead."
        break
    fi
done
if [ "$1" = "--print" ]; then
    echo
    profile
    exit 0
fi

if ! command -v sudo >/dev/null 2>&1 || ! command -v apparmor_parser >/dev/null 2>&1; then
    echo "Mightling: sudo or apparmor_parser is missing; write the profile yourself (--print shows it)." >&2
    exit 1
fi
echo "sudo will ask for your password."
profile | sudo tee "$PROFILE" >/dev/null
sudo apparmor_parser -r -T -W "$PROFILE"
echo "Done: start the app with $BINARY"
