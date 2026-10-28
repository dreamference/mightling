# ling-serve's image: the model server Mightling runs for qwen3.8-27b-nvfp4-dflash2 (engine `ling`).
#
# Built by scripts/build_ling_serve_image.sh, which builds the Release binary from the pinned
# ling-engine commit first (night 15's cmake recipe) and stages it here with the one library it
# links from outside the distribution (glog, from the engine's dependency build). Everything else it
# links is Ubuntu 24.04's, as on the machine it was built on. CUDA comes from the base image's
# runtime (libcudart 13.0.96, the toolkit the binary was compiled with) and the driver from the
# NVIDIA container toolkit at `docker run --gpus all`.
#
# An engine with vision (not bdda3a2) links libpng, libwebp and libjpeg-turbo (image input, CMake's
# IMAGE_LIBS), so the image carries libpng16-16t64, libwebp7 and libjpeg-turbo8: with `--vision on`,
# its default, it does not start without them. libnvjpeg is optional (the engine opens it at run
# time and falls back to libjpeg-turbo) and is not installed.
#
# python3 is here for the model gate, which runs its stdlib-only proxy in the engine's image
# (ModelGate.run_command), so the gate needs no image of its own.

# nvidia/cuda:13.0.2-base-ubuntu24.04, pinned by digest.
ARG CUDA_BASE=nvidia/cuda@sha256:2ab6381d970b211fb93853796dc6707eb8a72575a375c422b17cf4d8b2641701
FROM ${CUDA_BASE}

ARG ENGINE_COMMIT
ARG BINARY_SHA256
ARG CREATED

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libevent-2.1-7t64 \
        libgflags2.2 \
        libicu74 \
        libjpeg-turbo8 \
        liblz4-1 \
        liblzma5 \
        libpcre2-8-0 \
        libpng16-16t64 \
        libsnappy1v5 \
        libsodium23 \
        libssl3t64 \
        libunwind8 \
        libwebp7 \
        libzstd1 \
        python3 \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY libglog.so.1 /usr/local/lib/ling-serve/libglog.so.1
COPY ling-serve /usr/local/bin/ling-serve
ENV LD_LIBRARY_PATH=/usr/local/lib/ling-serve

LABEL org.opencontainers.image.title="ling-serve" \
      org.opencontainers.image.description="ling-engine's HTTP server for Qwen3.8-27B NVFP4 with the DFlash2 drafter (Mightling)" \
      org.opencontainers.image.source="https://github.com/dreamference/mightling" \
      org.opencontainers.image.vendor="Dreamference" \
      org.opencontainers.image.revision="${ENGINE_COMMIT}" \
      org.opencontainers.image.created="${CREATED}" \
      ai.dreamference.ling-engine.commit="${ENGINE_COMMIT}" \
      ai.dreamference.ling-serve.sha256="${BINARY_SHA256}"

# No entry point: the launch names the program (`ling-serve ...` after the image, as SGLang's
# launch names `python3 -m sglang.launch_server`), and the model gate names `python3`.
CMD ["ling-serve"]
