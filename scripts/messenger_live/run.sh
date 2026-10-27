#!/usr/bin/env bash
# The live messenger suite in one command (docs/dev/messengers.md): Telegram and Signal against
# the real messengers. Arguments go to `run.py run` (--only telegram|signal, --notify, …).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
exec python3 -I "$here/run.py" run "$@"
