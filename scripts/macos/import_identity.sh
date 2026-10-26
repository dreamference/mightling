#!/usr/bin/env bash
# Imports the Developer ID Application identity into a keychain of its own, for this job only
# (docs/dev/macos-signing.md):
#
#   scripts/macos/import_identity.sh
#
# Reads APPLE_DEVELOPER_ID_P12 (the .p12, base64), APPLE_DEVELOPER_ID_P12_PASSWORD and
# APPLE_TEAM_ID. Writes MAC_SIGN_IDENTITY (the identity's SHA-1, which is what codesign is given,
# so the certificate's name is never on a command line) and MAC_SIGN_KEYCHAIN to $GITHUB_ENV.
# The certificate's name is masked in the log: it is the enrolled account's, owner data.
# scripts/macos/remove_identity.sh deletes the keychain at the end of the job.
set -euo pipefail

: "${APPLE_DEVELOPER_ID_P12:?}" "${APPLE_DEVELOPER_ID_P12_PASSWORD:?}" "${APPLE_TEAM_ID:?}"
temp="${RUNNER_TEMP:?}"
keychain="$temp/mightling-signing.keychain-db"
p12="$temp/mightling-developer-id.p12"
# A random password for a keychain that lives as long as the job.
keychain_password="$(openssl rand -hex 24)"

( umask 077 && printf '%s' "$APPLE_DEVELOPER_ID_P12" | base64 --decode > "$p12" )
security create-keychain -p "$keychain_password" "$keychain"
# No lock while the job runs (the default locks after 5 minutes, mid-notarisation).
security set-keychain-settings "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
security import "$p12" -k "$keychain" -P "$APPLE_DEVELOPER_ID_P12_PASSWORD" -f pkcs12 \
    -T /usr/bin/codesign -T /usr/bin/security > /dev/null
rm -f "$p12"
# codesign may use the key without a dialog.
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$keychain_password" "$keychain" > /dev/null
# The job's keychain first in the search list, the existing ones kept (codesign finds Apple's
# intermediates in the system keychains).
# (A read loop: the scripts stay valid for the bash 3.2 macOS ships.)
existing=()
while IFS= read -r entry; do
    existing+=("$(sed -e 's/^ *"//' -e 's/"$//' <<< "$entry")")
done < <(security list-keychains -d user)
security list-keychains -d user -s "$keychain" "${existing[@]}"

# The one Developer ID Application identity of the team, by its hash.
line="$(security find-identity -v -p codesigning "$keychain" |
    grep -F "\"Developer ID Application: " | grep -F "($APPLE_TEAM_ID)\"" | head -1 || true)"
if [[ -z "$line" ]]; then
    echo "::error::APPLE_DEVELOPER_ID_P12 holds no valid Developer ID Application identity of team APPLE_TEAM_ID (an Apple Development or Distribution certificate does not notarise; an expired one is not listed)."
    exit 1
fi
hash="$(awk '{print $2}' <<< "$line")"
name="$(sed -E 's/^[^"]*"(.*)"$/\1/' <<< "$line")"
echo "::add-mask::$name"
echo "::add-mask::${name#Developer ID Application: }"
echo "MAC_SIGN_IDENTITY=$hash" >> "$GITHUB_ENV"
echo "MAC_SIGN_KEYCHAIN=$keychain" >> "$GITHUB_ENV"
echo "Developer ID Application identity imported (SHA-1 $hash)."
