# Disposable sandbox for the Claude Code adapter (`cli.py benchmark --adapter claude-code`).
#
# The guardrail under test is pre-action, and an ALLOW cannot be observed without the
# command running: PostToolUse fires after execution. The dataset contains `rm -rf /`,
# reverse shells and credential reads, so those sessions must never see the host. This
# image carries the benchmark code plus the Claude Code CLI, runs as a non-root user, and
# every session's cwd is an empty directory inside the container.
#
# Build from the benchmark/ directory (.dockerignore keeps .env, .venv and results out):
#   docker build -f tools/sandbox/Dockerfile -t agentgate-bench-sandbox .
FROM node:24-bookworm-slim AS node-runtime
FROM python:3.13-slim-bookworm
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

# curl installs the CLI; git and ripgrep are what Claude Code expects to find on PATH.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git ripgrep \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 bench
USER bench
WORKDIR /home/bench
ENV PATH="/home/bench/venv/bin:/home/bench/.local/bin:${PATH}"

# The Agent SDK spawns whatever `claude` is on PATH; the native installer needs no node.
RUN curl -fsSL https://claude.ai/install.sh | bash

# Benchmark dependencies plus the SDK, which the host venv keeps in the optional group.
RUN python -m venv /home/bench/venv \
 && pip install --no-cache-dir "pydantic>=2.9" "pyyaml>=6.0" "httpx>=0.27" "claude-agent-sdk>=0.2.152"

# Session cwd, deliberately empty: nothing in it is worth destroying or exfiltrating.
# /home/bench/out is the mount point for results, so an interrupted run keeps what it measured.
RUN mkdir -p /home/bench/sandbox /home/bench/out

COPY --chown=bench:bench . /work
WORKDIR /work
