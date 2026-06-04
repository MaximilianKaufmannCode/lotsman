# syntax=docker/dockerfile:1
# Lotsman registry-service — multistage build

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
WORKDIR /build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY shared/ /build/shared/
COPY services/registry-service/ /build/service/
WORKDIR /build/service
RUN uv pip install --system --no-cache /build/shared && \
    sed -i '/^\[tool\.uv\.sources\]/,/^$/d; /^lotsman-shared = { workspace = true }$/d' /build/service/pyproject.toml && \
    uv pip install --system --no-cache /build/service

FROM python:${PYTHON_VERSION}-slim AS runtime
# libmagic1 is required by python-magic for server-side MIME sniffing (US-9, Q7).
# Without it, the mime sniffer falls back to extension-based detection (less secure).
RUN apt-get update && apt-get install -y --no-install-recommends libmagic1 && \
    rm -rf /var/lib/apt/lists/*
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

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1
CMD ["uvicorn", "registry_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
