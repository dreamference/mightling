#!/bin/bash
# Peak memory against batch size for the chosen model (the indexer's share of the budget): the same
# 160-chunk probe at batch 1, 4 and 16.
D="$(dirname "$(readlink -f "$0")")"
m="${1:-ibm-granite/granite-embedding-107m-multilingual}"
for b in 1 4 16; do
  out="$D/emb/batch$b-probe__$(basename "$m")"
  BATCH=$b systemd-run --user --scope --quiet -p MemoryMax=4G -p MemorySwapMax=0 -- \
    nice -n 10 ionice -c 3 taskset -c "${CPUS:-15-18}" \
    bwrap --die-with-parent --unshare-net --ro-bind / / --dev /dev --proc /proc --bind "$D/emb" "$D/emb" \
    --setenv HF_HOME "$D/hf" --setenv HF_HUB_OFFLINE 1 --setenv CUDA_VISIBLE_DEVICES "" --setenv BATCH "$b" \
    "$D/venv/bin/python" "$D/embed.py" "$m" "$D/chunks/pdfium-ordered-512.json" "$D/questions.jsonl" "$out" 160 \
    2>&1 | grep -v -i warn | tail -1 | cut -c1-200
done
