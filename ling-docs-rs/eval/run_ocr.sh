#!/bin/bash
# The OCR engines on the scanned fixtures: four X925 cores, low priority, 3 GB cap, no network.
# Usage: [CPUS=5-8] run_ocr.sh <engine> …   (rapidocr-small, rapidocr-medium, tesseract)
D="$(dirname "$(readlink -f "$0")")"
for e in "$@"; do
  systemd-run --user --scope --quiet -p MemoryMax=3G -p MemorySwapMax=0 -- \
    nice -n 10 ionice -c 3 taskset -c "${CPUS:-5-8}" \
    bwrap --die-with-parent --unshare-net --ro-bind / / --dev /dev --proc /proc --bind "$D/results" "$D/results" \
    --tmpfs /var/tmp --setenv TMPDIR /var/tmp --setenv CUDA_VISIBLE_DEVICES "" \
    "$D/venv/bin/python" "$D/ocr_bench.py" "$e" 2>&1 | grep -v -i warn
done
