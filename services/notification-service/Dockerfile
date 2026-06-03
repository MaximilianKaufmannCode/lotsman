# syntax=docker/dockerfile:1
# Lotsman notification-service — multistage build

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
WORKDIR /build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY shared/ /build/shared/
COPY services/notification-service/ /build/service/
WORKDIR /build/service
RUN uv pip install --system --no-cache /build/shared && \
    sed -i '/^\[tool\.uv\.sources\]/,/^$/d; /^lotsman-shared = { workspace = true }$/d' /build/service/pyproject.toml && \
    uv pip install --system --no-cache /build/service

FROM python:${PYTHON_VERSION}-slim AS runtime
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
CMD ["uvicorn", "notification_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
