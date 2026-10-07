#!/bin/bash
# Chunk the corpus once: chunk_one.sh <engine> <tokens> [<tokenizer>]
D="$(dirname "$(readlink -f "$0")")"
export HF_HOME="$D/hf" HF_HUB_OFFLINE=1
nice -n 15 ionice -c 3 "$D/venv/bin/python" "$D/build_chunks.py" "$@" 2>&1 | grep -v -i warn
