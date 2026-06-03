# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Settings for notification-service."""

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

    service_name: str = Field(default="notification-service")
    log_level: str = Field(default="info")

    database_url: str = Field(...)
    redis_url: str = Field(default="redis://localhost:6379/0")
    # Per-service internal JWT key (HS256, per ADR-0003 §10 / F-001, F-002)
    internal_jwt_key_notification: str = Field(
        ...,
        min_length=32,
        description=(
            "HS256 key for verifying internal JWTs addressed to notification-service. "
            "Generate: python -c \"import secrets; print(secrets.token_hex(32))\""
        ),
    )
    outbox_poll_interval_seconds: float = Field(default=1.0)

    # Fernet master key for channel config encryption (ADR-0004 §4 / US-16).
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # noqa: E501
    # The ChannelCipher reads this from os.environ directly at construction time
    # to ensure early-fail before the service finishes starting.
    # This field is declared here for documentation only — Settings validation
    # would catch a missing CHANNEL_ENC_KEY as well.
    channel_enc_key: str = Field(
        default="",
        description=(
            "Fernet key for encrypting channel credentials. "
            "If empty, notification-service refuses to start."
        ),
    )

    # SMTP settings (used by the integrations layer in the notifications feature)
    smtp_host: str = Field(default="localhost")
    smtp_port: int = Field(default=1025)

    # Registry service connection (for calendar sync gateway — ADR-0005 §5)
    registry_svc_url: str = Field(
        default="http://registry-svc:8000",
        description="Base URL for registry-service internal calls.",
    )
    internal_jwt_key_registry: str = Field(
        default="",
        description=(
            "HS256 key for signing internal JWTs addressed to registry-service. "
            "Must match INTERNAL_JWT_KEY_REGISTRY in registry-service and web-bff. "
            "If empty, registry_gateway is not wired (ICS feed and calendar sync disabled)."
        ),
    )

    # Auth service connection (for bulk user lookup during reminder dispatch — Phase A)
    auth_svc_url: str = Field(
        default="http://auth-svc:8000",
        description="Base URL for auth-service internal calls.",
    )
    internal_jwt_key_auth: str = Field(
        default="",
        description=(
            "HS256 key for signing internal JWTs addressed to auth-service. "
            "Must match INTERNAL_JWT_KEY_AUTH in auth-service. "
            "If empty, auth_gateway is not wired and reminder dispatch skips users."
        ),
    )

    # Web BFF base URL for deep links in calendar event bodies (ADR-0005 §9).
    web_bff_url: str = Field(
        default="https://lotsman.example.com",
        description="Base URL for deep links embedded in calendar event bodies.",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
