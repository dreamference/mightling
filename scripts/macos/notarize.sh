#!/usr/bin/env bash
# Submits one file to Apple's notary service and waits for the verdict (docs/dev/macos-signing.md):
#
#   scripts/macos/notarize.sh <file.zip|file.dmg>
#
# Authenticates with the App Store Connect API key (APPLE_API_KEY_ID, APPLE_API_ISSUER_ID,
# APPLE_API_KEY_P8 base64), not an Apple ID and app-specific password. Exits 0 only when the
# submission is Accepted; otherwise prints the notary's log, which names each file it refused and
# why, and exits 1. Stapling is the caller's (`xcrun stapler staple`): a bare binary cannot be
# stapled, a .app and a .dmg can.
set -euo pipefail

file="${1:?file to notarise}"
: "${APPLE_API_KEY_ID:?}" "${APPLE_API_ISSUER_ID:?}" "${APPLE_API_KEY_P8:?}"
dir="${RUNNER_TEMP:?}/mightling-notary"
key="$dir/AuthKey_$APPLE_API_KEY_ID.p8"
if [[ ! -s "$key" ]]; then
    ( umask 077 && mkdir -p "$dir" && printf '%s' "$APPLE_API_KEY_P8" | base64 --decode > "$key" )
fi
auth=(--key "$key" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER_ID")

echo "Notarising $(basename "$file") ($(du -h "$file" | cut -f1))"
result="$dir/submit-$(basename "$file").json"
# --wait returns once Apple has decided; usually minutes, the timeout is for a slow day.
xcrun notarytool submit "$file" "${auth[@]}" --wait --timeout 2h --output-format json > "$result" || true
cat "$result"
echo
# jq: on the runner images, and in macOS itself since 15.
id="$(jq -r '.id // empty' "$result" 2> /dev/null || true)"
status="$(jq -r '.status // empty' "$result" 2> /dev/null || true)"
if [[ "$status" != "Accepted" ]]; then
    echo "::error::Notarisation of $(basename "$file") ended with status '${status:-none}'."
    if [[ -n "$id" ]]; then
        xcrun notarytool log "$id" "${auth[@]}" || true
    fi
    exit 1
fi
# Accepted can still carry warnings; the log lists them.
xcrun notarytool log "$id" "${auth[@]}" || true
echo "Notarised: $(basename "$file") (submission $id)"
