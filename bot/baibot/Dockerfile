# syntax=docker/dockerfile:1
# etkecc/baibot v2026.11.18.0；只针对这一个源码提交应用酒馆补丁。
FROM docker.io/rust:1.100.0-slim-trixie AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates cmake git libsqlite3-dev libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git init --quiet \
    && git fetch --quiet --depth=1 https://github.com/etkecc/baibot.git a5944371b6c4a952b350eb9d561202934735a475 \
    && git checkout --quiet --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = a5944371b6c4a952b350eb9d561202934735a475
COPY strict-room-mentions.patch /tmp/tavern.patch
RUN git apply --check /tmp/tavern.patch && git apply /tmp/tavern.patch

ENV BAIBOT_VERSION=2026.11.18.0-tavern.3
ENV CARGO_HOME=/cargo CARGO_TARGET_DIR=/target
RUN --mount=type=cache,target=/cargo,sharing=locked \
    --mount=type=cache,target=/target,sharing=locked \
    cargo build --locked --release && cp /target/release/baibot /baibot

FROM docker.io/debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates sqlite3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /baibot /app/baibot
COPY --from=build /app/LICENSE /usr/share/doc/tavern-baibot/LICENSE
USER 65532:65532
ENTRYPOINT ["/app/baibot"]
