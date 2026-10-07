#!/bin/bash
# Chunk one engine's PDF text at one size and score BM25 on it: run_one_chunks.sh <engine> <tokens>
D="$(dirname "$(readlink -f "$0")")"
export HF_HOME="$D/hf" HF_HUB_OFFLINE=1
nice -n 15 "$D/venv/bin/python" "$D/build_chunks.py" "$1" "$2" 2>&1 | grep -v -i warn
"$D/venv/bin/python" "$D/evaluate.py" "$D/chunks/$1-$2.json" | cut -c1-330
