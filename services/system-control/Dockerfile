# syntax=docker/dockerfile:1
# Lotsman system-control sidecar — multistage build
# Final image: python:3.12-slim, non-root user 1001 with docker group membership
#
# IMPORTANT: The container must run as a user that has access to
# /var/run/docker.sock.  Compose sets user: "1001:<docker_gid>" where
# docker_gid is the GID of /var/run/docker.sock on the host.
# The image pre-creates group docker (GID overrideable at runtime via compose).

ARG PYTHON_VERSION=3.12

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY shared/ /build/shared/
COPY services/system-control/ /build/service/

WORKDIR /build/service
RUN uv pip install --system --no-cache /build/shared && \
    sed -i '/^\[tool\.uv\.sources\]/,/^$/d; /^lotsman-shared = { workspace = true }$/d' /build/service/pyproject.toml && \
    uv pip install --system --no-cache /build/service

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

# Create non-root user matching compose uid=1001.
# The 'docker' group is created here so compose can map it to the host GID via
# the `user: "1001:<host_docker_gid>"` directive — the GID is overridden at
# runtime; the group name is irrelevant after that.
RUN groupadd --gid 1001 lotsman && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /sbin/nologin lotsman

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build/service/src /app/src
COPY --from=builder /build/shared/src /app/shared_src

ENV PYTHONPATH=/app/src:/app/shared_src
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER 1001:1001

EXPOSE 8000

CMD ["uvicorn", "system_control.main:app", "--host", "0.0.0.0", "--port", "8000"]
