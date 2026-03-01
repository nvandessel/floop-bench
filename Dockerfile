FROM python:3.12-bookworm

# System tools needed by SWE-bench repos
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl jq \
    && rm -rf /var/lib/apt/lists/*

# Floop CLI — Go binary from GitHub releases
ARG FLOOP_VERSION=0.10.0
ARG TARGETARCH=amd64
RUN curl -fsSL "https://github.com/nvandessel/floop/releases/download/v${FLOOP_VERSION}/floop-${FLOOP_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin floop \
    && chmod +x /usr/local/bin/floop

# Python tooling (uv for fast installs)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Project dependencies — install before copying code for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Agent + floop integration code
COPY agents/ /app/agents/
COPY floop_integration/ /app/floop_integration/

ENV PYTHONPATH=/app

WORKDIR /workspace
ENTRYPOINT ["uv", "run", "python", "-m", "agents.mini_swe_cli"]
