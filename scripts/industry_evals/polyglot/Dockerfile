# The Aider Polyglot toolchains for `ling` (specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §10.2).
# Modelled on aider's benchmark/Dockerfile (Apache-2.0): the same base and toolchains, without aider
# itself, plus a warm-up of every exercise's dependencies so the runs need no network. Built by
# `setup_cache.py polyglot` with the exercises (pinned) in its context under polyglot-benchmark/.
FROM buildpack-deps:jammy

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common cmake libboost-all-dev ca-certificates-java openjdk-21-jdk libtbb-dev \
    && add-apt-repository ppa:deadsnakes/ppa && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-dev python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && python3 -m pip install --no-cache-dir pytest==8.4.1

# Go, for the machine's architecture (aarch64 on the GB10).
RUN ARCH=$(uname -m) && case "$ARCH" in x86_64) GOARCH=amd64 ;; aarch64) GOARCH=arm64 ;; *) exit 1 ;; esac \
    && curl -fsSL "https://golang.org/dl/go1.21.5.linux-$GOARCH.tar.gz" -o /tmp/go.tgz \
    && tar -C /usr/local -xzf /tmp/go.tgz && rm /tmp/go.tgz
ENV PATH="/usr/local/go/bin:/root/.cargo/bin:${PATH}"

RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /npm-install && cd /npm-install && npm init -y >/dev/null \
    && npm install jest @babel/core@7.25.2 @exercism/babel-preset-javascript@0.2.1 \
        @exercism/eslint-config-javascript@0.6.0 @types/jest@29.5.12 @types/node@20.12.12 \
        babel-jest@29.6.4 core-js@3.37.1 eslint@8.49.0

COPY test-js.sh test-cpp.sh /usr/local/bin/
COPY polyglot-benchmark /polyglot
# Every exercise's dependencies, fetched now so a run needs no network: Gradle and the Java test
# libraries, the Rust crates, the Go modules. A failure here fails the build, listing the exercise.
RUN set -e; \
    for d in /polyglot/java/exercises/practice/*/; do (cd "$d" && ./gradlew --quiet dependencies >/dev/null) || { echo "java warm-up failed: $d"; exit 1; }; done; \
    for d in /polyglot/rust/exercises/practice/*/; do (cd "$d" && cargo fetch --quiet) || { echo "rust warm-up failed: $d"; exit 1; }; done; \
    for d in /polyglot/go/exercises/practice/*/; do (cd "$d" && go mod download) || { echo "go warm-up failed: $d"; exit 1; }; done
WORKDIR /work
