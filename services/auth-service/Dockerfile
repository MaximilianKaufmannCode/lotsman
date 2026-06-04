# syntax=docker/dockerfile:1
# Lotsman auth-service — multistage build
# Final image: python:3.12-slim, non-root user 1001

ARG PYTHON_VERSION=3.12

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /build

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy shared kernel + service sources (needed for workspace install)
COPY shared/ /build/shared/
COPY services/auth-service/ /build/service/

# Install dependencies into an isolated prefix
WORKDIR /build/service
RUN uv pip install --system --no-cache /build/shared && \
    sed -i '/^\[tool\.uv\.sources\]/,/^$/d; /^lotsman-shared = { workspace = true }$/d' /build/service/pyproject.toml && \
    uv pip install --system --no-cache /build/service

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

# Create non-root user matching compose uid=1001
RUN groupadd --gid 1001 lotsman && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /sbin/nologin lotsman

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source (needed because we use src layout with hatchling — package is installed)
COPY --from=builder /build/service/src /app/src
COPY --from=builder /build/shared/src /app/shared_src

ENV PYTHONPATH=/app/src:/app/shared_src
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER 1001:1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["uvicorn", "auth_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
