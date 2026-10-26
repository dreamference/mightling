#!/usr/bin/env bash
# Signs the macOS client binaries with the Developer ID and the hardened runtime
# (docs/dev/macos-signing.md):
#
#   scripts/macos/sign_binaries.sh <bin dir>
#
# Every binary scripts/package_mightling.sh packages, before it packages them, so the archives and
# ling-<target>.sha256sums are of the signed files. Entitlements: none, except
# codex-code-mode-host, which runs V8 with its JIT on (codex-code-mode-runtime's default) and
# so needs com.apple.security.cs.allow-jit (V8 maps its code space MAP_JIT on every Mac, Intel
# included). Needs MAC_SIGN_IDENTITY, MAC_SIGN_KEYCHAIN (import_identity.sh) and APPLE_TEAM_ID.
set -euo pipefail

bin="${1:?bin directory}"
: "${MAC_SIGN_IDENTITY:?}" "${MAC_SIGN_KEYCHAIN:?}" "${APPLE_TEAM_ID:?}"
here="$(cd "$(dirname "$0")" && pwd)"
# A Developer ID signature of this team, nothing else, is what each binary must satisfy.
requirement="anchor apple generic and certificate leaf[subject.OU] = \"$APPLE_TEAM_ID\""

signed=0
for name in ling codex-code-mode-host ling-search ling-fetch ling-code ling-docs ling-signal; do
    file="$bin/$name"
    [[ -f "$file" ]] || continue
    args=(--force --sign "$MAC_SIGN_IDENTITY" --keychain "$MAC_SIGN_KEYCHAIN" --timestamp --options runtime)
    if [[ "$name" == codex-code-mode-host ]]; then
        args+=(--entitlements "$here/entitlements/codex-code-mode-host.plist")
    fi
    codesign "${args[@]}" "$file"
    codesign --verify --strict --verbose=2 -R="$requirement" "$file"
    codesign -d --entitlements - "$file" 2> /dev/null || true
    signed=$((signed + 1))
done
for name in ling codex-code-mode-host; do
    [[ -f "$bin/$name" ]] || { echo "::error::$bin/$name is missing"; exit 1; }
done
echo "Signed $signed binaries in $bin"
