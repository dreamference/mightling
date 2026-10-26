#!/bin/bash
# Builds ling-serve's image (docker/ling-serve/Dockerfile) from one ling-engine commit, on an aarch64
# machine that has the engine's build dependencies (the engine's development machine). No GPU is
# used: the build runs at the lowest priority beside whatever holds the GPU.
#
#   scripts/build_ling_serve_image.sh COMMIT [BINARY]
#
# COMMIT is the full ling-engine commit. Without BINARY, the Release binary is built from a detached
# worktree of ENGINE_REPO at COMMIT with night 15's recipe (cmake Release with Ninja, `ninja
# ling-serve`); with BINARY, that already-built binary is used, and its build must have been that
# recipe at that commit (night 15's ~/ab-n15/ling-serve is 79c38c7's). The image is tagged
# IMAGE:<first 12 of COMMIT>; publish it with `docker push`, then pin the pushed digest in
# QWEN38_LING_IMAGE (dreamference/hardware/model_matrix_registry.py) and the commit in
# QWEN38_LING_ENGINE_COMMIT.
set -euo pipefail
COMMIT=${1:?usage: build_ling_serve_image.sh COMMIT [BINARY]}
BINARY=${2:-}
ENGINE_REPO=${ENGINE_REPO:-$HOME/ling-engine}
DEPS=${DEPS:-$HOME/ling-deps}
IMAGE=${IMAGE:-ghcr.io/dreamference/ling-serve}
WORK=${WORK:-$HOME/ling-serve-image/$COMMIT}
HERE=$(cd "$(dirname "$0")/.." && pwd)
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "COMMIT must be the full 40-character commit" >&2; exit 2; }
mkdir -p "$WORK/context"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$WORK/build.log" >&2; }

if [ -z "$BINARY" ]; then
    if [ ! -d "$WORK/src" ]; then
        git -C "$ENGINE_REPO" fetch -q origin || true
        git -C "$ENGINE_REPO" worktree add --detach "$WORK/src" "$COMMIT"
    fi
    [ "$(git -C "$WORK/src" rev-parse HEAD)" = "$COMMIT" ] || { log "worktree is not at $COMMIT"; exit 1; }
    [ -z "$(git -C "$WORK/src" status --porcelain)" ] || { log "worktree has changes"; exit 1; }
    export PATH=/usr/local/cuda/bin:$PATH
    log "configure $COMMIT"
    cmake -S "$WORK/src" -B "$WORK/src/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
    log "build"
    nice -n 19 ninja -C "$WORK/src/build" -j 4 ling-serve
    BINARY=$WORK/src/build/ling-serve
fi
cp "$BINARY" "$WORK/context/ling-serve"
SHA=$(sha256sum "$WORK/context/ling-serve" | cut -d' ' -f1)
GLOG=$(ldd "$WORK/context/ling-serve" | awk '/libglog/ {print $3}')
[ -n "$GLOG" ] && [ -f "$GLOG" ] || { log "libglog.so.1 not found by ldd (is $DEPS built?)"; exit 1; }
cp -L "$GLOG" "$WORK/context/libglog.so.1"
cp "$HERE/docker/ling-serve/Dockerfile" "$WORK/context/Dockerfile"
TAG="$IMAGE:${COMMIT:0:12}"
log "image $TAG from ling-serve $SHA"
nice -n 19 docker build --pull=false \
    --build-arg ENGINE_COMMIT="$COMMIT" --build-arg BINARY_SHA256="$SHA" \
    --build-arg CREATED="$(date -u +%FT%TZ)" -t "$TAG" "$WORK/context"
# The image must load the binary: every library resolves (libcuda.so.1 comes from the container
# toolkit at run time), and it prints its usage (exit 2 without --model) without touching the GPU.
if docker run --rm --entrypoint sh "$TAG" -c 'ldd /usr/local/bin/ling-serve | grep -v libcuda.so | grep -q "not found"'; then
    docker run --rm --entrypoint ldd "$TAG" /usr/local/bin/ling-serve | grep "not found" >&2
    log "a library is missing in the image"; exit 1
fi
usage=$(docker run --rm --gpus all "$TAG" ling-serve 2>&1 || true)
[[ "$usage" == "usage: ling-serve"* ]] || { log "ling-serve does not run in the image: $usage"; exit 1; }
log "built $TAG (ling-serve $SHA, commit $COMMIT)"
echo "$TAG"
