#!/bin/bash
# Embed one chunk set with one model: 4 threads pinned to four Cortex-X925 cores, low priority, memory-capped,
# no network (the model is already in the scratch cache). Usage: [CPUS=5-8] run_embed.sh <model> <chunk-tag>
D="$(dirname "$(readlink -f "$0")")"
model="$1"; tag="$2"
out="$D/emb/${tag}__$(basename "$model")"
mkdir -p "$D/emb"
systemd-run --user --scope --quiet -p MemoryMax=4G -p MemorySwapMax=0 -- \
  nice -n 10 ionice -c 3 taskset -c "${CPUS:-15-18}" \
  bwrap --die-with-parent --unshare-net --ro-bind / / --dev /dev --proc /proc --bind "$D/emb" "$D/emb" \
  --setenv HF_HOME "$D/hf" --setenv HF_HUB_OFFLINE 1 --setenv CUDA_VISIBLE_DEVICES "" \
  "$D/venv/bin/python" "$D/embed.py" "$model" "$D/chunks/$tag.json" "$D/questions.jsonl" "$out"
