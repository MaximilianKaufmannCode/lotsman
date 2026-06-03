# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Settings for registry-service."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = Field(default="registry-service")
    log_level: str = Field(default="info")

    database_url: str = Field(
        ...,
        description="Async SQLAlchemy DSN for registry schema.",
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Per-service internal JWT key (HS256, per ADR-0003 §10 / F-001, F-002)
    internal_jwt_key_registry: str = Field(
        ...,
        min_length=32,
        description=(
            "HS256 key for verifying internal JWTs addressed to registry-service. "
            'Generate: python -c "import secrets; print(secrets.token_hex(32))"'
        ),
    )

    # Internal JWT key for minting tokens to audit-service
    internal_jwt_key_audit: str = Field(
        ...,
        min_length=32,
        description="HS256 key for minting JWTs addressed to audit-service.",
    )

    outbox_poll_interval_seconds: float = Field(default=1.0)

    # Downstream service URLs
    audit_svc_url: str = Field(default="http://audit-svc:8000")

    # File storage volume roots
    attachments_volume_root: str = Field(
        default="/vol/attachments",
        description="Absolute path to the attachments volume. Outside nginx web root.",
    )
    exports_volume_root: str = Field(
        default="/vol/exports",
        description="Absolute path to the xlsx exports volume.",
    )

    # HMAC key for signed URLs (attachment + export download)
    signed_url_key: str = Field(
        default="dev-insecure-key-replace-in-production",
        description=(
            "HMAC-SHA256 key for signed attachment and export URLs. "
            "In production, inject via Docker secrets."
        ),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
