#!/usr/bin/env bash
# Deletes the job's signing keychain (scripts/macos/import_identity.sh) and the notarisation key
# (scripts/macos/notarize.sh), if they are there. Hosted runners are discarded after the job; this
# is for a runner that is not.
set -uo pipefail

if [[ -n "${MAC_SIGN_KEYCHAIN:-}" && -e "$MAC_SIGN_KEYCHAIN" ]]; then
    security delete-keychain "$MAC_SIGN_KEYCHAIN" || true
fi
rm -rf "${RUNNER_TEMP:?}/mightling-notary"
echo "Signing keychain and notarisation key removed."
