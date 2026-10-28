#!/bin/bash
# The macOS CI runner's hook after every job (failed and cancelled ones too): release the Mac's lock if CI holds it.
lock=/Users/Shared/ci-lock/mac.lock
holder="$(cat "$lock" 2>/dev/null || true)"
if [[ "$holder" == ci\ * ]]; then rm -f "$lock"; echo "Mac lock released"; fi
exit 0
