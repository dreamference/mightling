#!/bin/bash
# Builds ling-serve's image (docker/ling-serve/Dockerfile) from one ling-engine commit, on an aarch64
# machine that has the engine's build dependencies (the engine's development machine). No GPU is
# used: the build runs at the lowest priority beside whatever holds the GPU.
#
#   scripts/build_ling_serve_image.sh COMMIT [BINARY]
#   scripts/build_ling_serve_image.sh --check-image IMAGE     (the anonymity check alone, on any image)
#
# COMMIT is the full ling-engine commit. Without BINARY, the Release binary is built from a `git
# archive` of ENGINE_REPO at COMMIT with night 15's recipe (cmake Release with Ninja, `ninja
# ling-serve`); with BINARY, that already-built binary is used, and its build must have been that
# recipe at that commit (night 15's binary is b8e9451's; 1.7.2's is 8d112d0's, bdda3a2 plus the
# tokenizer fix). The image is tagged IMAGE:<first 12 of COMMIT>; publish it with `docker push`,
# then pin the pushed digest in QWEN38_LING_IMAGE (dreamference/hardware/model_matrix_registry.py)
# and the commit in QWEN38_LING_ENGINE_COMMIT.
#
# Nothing of the build machine may reach the public image. Compilers write their source and build
# paths into a binary (__FILE__ in folly's and glog's checks, DWARF from the RelWithDebInfo
# dependencies, nvcc's -lineinfo, the linker's RUNPATH), and a build under a home directory once
# published that directory, the owner's login name with it (the 8d112d0 and bdda3a2 images, both
# deleted). So:
# - the dependencies and the engine are built under neutral paths outside any home: DEPS
#   (/opt/ling-deps) and WORK (/opt/ling-engine/COMMIT); the script refuses a path under /home/;
# - every compile maps those paths away: the engine with -ffile-prefix-map/-fdebug-prefix-map
#   (WORK's source to /build, DEPS to /deps), the dependencies with the same flags (DEPS to /build)
#   given to getdeps through CFLAGS and CXXFLAGS. nvcc's device line info is not mapped, so it reads
#   the neutral WORK path;
# - the staged binary's RUNPATH loses every entry under DEPS or a home directory and gets
#   $ORIGIN/../lib/ling-serve instead (patchelf), and libglog.so.1's likewise;
# - after the build, the anonymity check runs `strings -a` over every file of the image's
#   filesystem and over its configuration (labels, history), and fails the build on any hit for
#   /home/, the build machine's login name and home directory name, every entry of the anonymity
#   denylist (~/.config/dreamference/anonymity-denylist.txt, required; never printed), "puffin" and
#   "gx10". A hit in a file that is byte-for-byte the pinned base image's, a distribution
#   package's file matching its dpkg md5sum, or Python bytecode compiled from such a file with the
#   same hit (copyright files naming other people, /etc/passwd's ubuntu user, NVIDIA's own build
#   paths, stdlib docstrings) is third-party content: listed, not fatal. On a hit
#   the check prints the file and the count, never the matched text.
#
# The dependencies (proxygen with folly, fizz, wangle, mvfst, glog, ...; ling-engine docs/V0.md),
# built once per machine, in a proxygen checkout at DEPS/src/proxygen:
#
#   M="-ffile-prefix-map=/opt/ling-deps=/build -fdebug-prefix-map=/opt/ling-deps=/build"
#   HOME=/opt/ling-deps/home CCACHE_DISABLE=1 CFLAGS="$M" CXXFLAGS="$M" nice -n 19 \
#     python3 build/fbcode_builder/getdeps.py build --no-tests --allow-system-packages \
#     --scratch-path /opt/ling-deps/scratch --install-prefix /opt/ling-deps/proxygen proxygen
#
# (/opt/ling-deps made by root and given to the build user; ccache off so that no object compiled
# under another path comes back from its cache.)
#
# Vision (for the next engine image, not 8d112d0's, which has none): a ling-serve built with vision
# starts with `--vision on` by default and then needs libpng16-16t64, libwebp7 and libjpeg-turbo8
# in the image (libnvjpeg optional), or it does not start. Add them to docker/ling-serve/Dockerfile's
# apt list with that engine, and check the image's ldd output again.
set -euo pipefail
ENGINE_REPO=${ENGINE_REPO:-$HOME/ling-engine}
DEPS=${DEPS:-/opt/ling-deps}
IMAGE=${IMAGE:-ghcr.io/dreamference/ling-serve}
DENYLIST=${DENYLIST:-$HOME/.config/dreamference/anonymity-denylist.txt}
HERE=$(cd "$(dirname "$0")/.." && pwd)
CUDA_BASE=$(sed -n 's/^ARG CUDA_BASE=//p' "$HERE/docker/ling-serve/Dockerfile")

# The anonymity check: every file of IMAGE's filesystem and its configuration, `strings -a`, against
# the patterns above. Returns non-zero on a hit outside third-party content. SCAN is a scratch
# directory (removed afterwards).
check_image() {
    local image=$1 scan=$2 pat files hits=0 third=0 f n rc src
    [ -s "$DENYLIST" ] || { echo "anonymity check: the denylist $DENYLIST is missing" >&2; return 1; }
    rm -rf "$scan"; mkdir -p "$scan/image" "$scan/base"
    # One PCRE, case-insensitive: the denylist's entries (Python-style regular expressions) and the
    # login and home names of this machine, each as a whole word as tests/test_anonymity.py matches
    # them (no letter or digit directly before or after), then the internal machine names and /home/
    # anywhere.
    pat=$( { { grep -v '^[[:space:]]*#' "$DENYLIST" | grep -v '^[[:space:]]*$'; id -un; basename "$HOME"; } \
             | sed 's/.*/(?<![A-Za-z0-9])(?:&)(?![A-Za-z0-9])/'
             printf '%s\n' puffin gx10 /home/; } | paste -sd'|')
    # A pattern grep cannot compile would make every count fail, which must not read as clean.
    rc=0; printf 'probe\n' | grep -q -i -P -e "$pat" || rc=$?
    [ "$rc" -le 1 ] || { echo "anonymity check: the patterns do not compile" >&2; return 1; }
    export_fs() {  # image dir
        local cid; cid=$(docker create "$1" true)
        docker export "$cid" | tar -x -C "$2" --no-same-owner --no-same-permissions 2>/dev/null || true
        docker rm "$cid" > /dev/null
        chmod -R u+rX "$2" 2>/dev/null || true
    }
    export_fs "$image" "$scan/image"
    export_fs "$CUDA_BASE" "$scan/base"
    # md5sums of the distribution packages installed in the image (path without its leading /).
    cat "$scan"/image/var/lib/dpkg/info/*.md5sums 2>/dev/null | awk '{print $2" "$1}' | sort -u > "$scan/dpkg.md5" || true
    files=$(cd "$scan/image" && find . -type f | sed 's|^\./||' | sort)
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        n=$(strings -a "$scan/image/$f" | grep -c -i -P -e "$pat" || true)
        [ "$n" -gt 0 ] || continue
        if [ -f "$scan/base/$f" ] && cmp -s "$scan/image/$f" "$scan/base/$f"; then
            echo "  third-party (base image): /$f ($n)" >&2; third=$((third + 1)); continue
        fi
        if grep -q -F -x "$f $(md5sum < "$scan/image/$f" | cut -d' ' -f1)" "$scan/dpkg.md5"; then
            echo "  third-party (package file, md5 verified): /$f ($n)" >&2; third=$((third + 1)); continue
        fi
        # Bytecode python3's postinst compiled from a package's source that has the same hit.
        src=$(sed -n 's|^\(.*\)/__pycache__/\([^.]*\)\.cpython-[0-9]*\.pyc$|\1/\2.py|p' <<< "$f")
        if [ -n "$src" ] && [ -f "$scan/image/$src" ] \
            && grep -q -F -x "$src $(md5sum < "$scan/image/$src" | cut -d' ' -f1)" "$scan/dpkg.md5" \
            && strings -a "$scan/image/$src" | grep -q -i -P -e "$pat"; then
            echo "  third-party (compiled from package file /$src): /$f ($n)" >&2; third=$((third + 1)); continue
        fi
        echo "  HIT: /$f ($n strings)" >&2; hits=$((hits + 1))
    done <<< "$files"
    n=$( { docker image inspect "$image"; docker history --no-trunc "$image"; } | grep -c -i -P -e "$pat" || true)
    [ "$n" -gt 0 ] && { echo "  HIT: the image's configuration or history ($n lines)" >&2; hits=$((hits + 1)); }
    echo "anonymity check of $image: $(wc -l <<< "$files") files, $hits with hits, $third third-party" >&2
    rm -rf "$scan"
    [ "$hits" -eq 0 ]
}

if [ "${1:-}" = "--check-image" ]; then
    check_image "${2:?usage: build_ling_serve_image.sh --check-image IMAGE}" "${SCAN:-/tmp/ling-serve-scan-$$}"
    exit
fi

COMMIT=${1:?usage: build_ling_serve_image.sh COMMIT [BINARY]}
BINARY=${2:-}
WORK=${WORK:-/opt/ling-engine/$COMMIT}
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "COMMIT must be the full 40-character commit" >&2; exit 2; }
for d in "$WORK" "$DEPS"; do
    case "$d/" in /home/*|"$HOME"/*) echo "$d is under a home directory: build under a neutral path (see above)" >&2; exit 2 ;; esac
done
mkdir -p "$WORK/context"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$WORK/build.log" >&2; }

if [ -z "$BINARY" ]; then
    if [ ! -d "$WORK/src" ]; then
        git -C "$ENGINE_REPO" fetch -q origin || true
        mkdir -p "$WORK/src"
        git -C "$ENGINE_REPO" archive "$COMMIT" | tar -x -C "$WORK/src"
        echo "$COMMIT" > "$WORK/src/.engine-commit"
    fi
    [ "$(cat "$WORK/src/.engine-commit")" = "$COMMIT" ] || { log "$WORK/src is not $COMMIT"; exit 1; }
    export PATH=/usr/local/cuda/bin:$PATH
    MAPS="-ffile-prefix-map=$WORK/src=/build -fdebug-prefix-map=$WORK/src=/build -ffile-prefix-map=$DEPS=/deps -fdebug-prefix-map=$DEPS=/deps"
    log "configure $COMMIT"
    cmake -S "$WORK/src" -B "$WORK/src/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DLING_PROXYGEN_PREFIX="$DEPS/proxygen" \
        -DLING_FBCODE_CMAKE="$DEPS/src/proxygen/build/fbcode_builder/CMake" \
        -DCMAKE_CXX_FLAGS="$MAPS" -DCMAKE_CUDA_FLAGS="-Xcompiler=${MAPS// /,}"
    log "build"
    nice -n 19 ninja -C "$WORK/src/build" -j 4 ling-serve
    BINARY=$WORK/src/build/ling-serve
fi
GLOG=$(ldd "$BINARY" | awk '/libglog/ {print $3}')
[ -n "$GLOG" ] && [ -f "$GLOG" ] || { log "libglog.so.1 not found by ldd (is $DEPS built?)"; exit 1; }
cp "$BINARY" "$WORK/context/ling-serve"
cp -L "$GLOG" "$WORK/context/libglog.so.1"
chmod u+w "$WORK/context/libglog.so.1"
# RUNPATH: the build machine's dependency and home directories out, the image's library directory in.
neutral_rpath() {  # file [first entry]
    local keep
    keep=$(patchelf --print-rpath "$1" | tr ':' '\n' | grep -v -e "^$DEPS" -e "^/home/" -e "^$HOME" -e '^$' | paste -sd: || true)
    keep=${2:-}${2:+${keep:+:}}$keep
    if [ -n "$keep" ]; then patchelf --set-rpath "$keep" "$1"; else patchelf --remove-rpath "$1"; fi
}
neutral_rpath "$WORK/context/ling-serve" '$ORIGIN/../lib/ling-serve'
neutral_rpath "$WORK/context/libglog.so.1"
SHA=$(sha256sum "$WORK/context/ling-serve" | cut -d' ' -f1)
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
log "anonymity check"
check_image "$TAG" "$WORK/scan" || { log "the image carries an identifier of the build machine: not publishable"; exit 1; }
log "built $TAG (ling-serve $SHA, commit $COMMIT)"
echo "$TAG"
