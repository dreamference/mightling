#!/usr/bin/env bash
# Decides whether a macOS build is signed with the Developer ID (docs/dev/macos-signing.md):
#
#   scripts/macos/signing_gate.sh
#
# Writes sign=true or sign=false to $GITHUB_OUTPUT. Signing needs both: the caller asked for it
# (SIGN=true, which release.yml passes and which puts the job in the `release` environment, where
# the secrets live), and all six Apple secrets are set. None set: the unsigned preview, exactly as
# before the Developer ID. Some but not all set: an error, since a half-configured identity would
# otherwise ship an unsigned build without anyone noticing.
set -euo pipefail

names=(
    APPLE_DEVELOPER_ID_P12
    APPLE_DEVELOPER_ID_P12_PASSWORD
    APPLE_TEAM_ID
    APPLE_API_KEY_ID
    APPLE_API_ISSUER_ID
    APPLE_API_KEY_P8
)
present=()
missing=()
for name in "${names[@]}"; do
    if [[ -n "${!name:-}" ]]; then present+=("$name"); else missing+=("$name"); fi
done

output="${GITHUB_OUTPUT:-/dev/stdout}"
if [[ "${SIGN:-false}" != "true" ]]; then
    echo "Unsigned preview: signing was not asked for (only release.yml asks)."
    echo "sign=false" >> "$output"
    exit 0
fi
if [[ ${#present[@]} -eq 0 ]]; then
    echo "::warning::Signing was asked for, but no Apple secret is set in the release environment: building the unsigned preview (docs/dev/macos-signing.md)."
    echo "sign=false" >> "$output"
    exit 0
fi
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "::error::Some Apple secrets are set and some are not; missing: ${missing[*]} (docs/dev/macos-signing.md)."
    exit 1
fi
echo "Signing with the Developer ID, notarising with the App Store Connect API key."
echo "sign=true" >> "$output"
