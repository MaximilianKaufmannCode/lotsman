# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Settings for audit-service."""

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

    service_name: str = Field(default="audit-service")
    log_level: str = Field(default="info")

    database_url: str = Field(...)
    redis_url: str = Field(default="redis://localhost:6379/0")
    # Per-service internal JWT key (HS256, per ADR-0003 §10 / F-001, F-002)
    internal_jwt_key_audit: str = Field(
        ...,
        min_length=32,
        description=(
            "HS256 key for verifying internal JWTs addressed to audit-service. "
            "Generate: python -c \"import secrets; print(secrets.token_hex(32))\""
        ),
    )

    # Consumer group settings
    consumer_group: str = Field(default="audit-recorder")
    consumer_name: str = Field(default="audit-recorder-1")
    # Streams to consume — must match the `topic` column of each <service>.outbox
    # table in PG (source-of-truth used by dispatchers). Per ADR-0002 §C.
    #
    # FIXED 2026-05-22: defaults were plural ("auth.users", "auth.sessions") but
    # publishers write singular ("auth.user", "auth.session"). All auth.* and
    # several registry.* events for the lifetime of the service silently bypassed
    # audit.events.
    stream_keys: list[str] = Field(
        default=[
            "auth.user",
            "auth.session",
            "auth.invite",
            "auth.invitation",
            "registry.documents",
            "registry.assets",
            "registry.document_types",
            "registry.imports",
            "registry.preferences",
            "registry.exports",
            # Notification streams — added 2026-05-25 after Phase D fix of
            # notification.outbox double-prefix bug (notification.notification.*
            # → notification.*). 2-segment topic = "notification.<aggregate>".
            "notification.calendar",
            "notification.channel",
            "notification.email",
            "notification.deliveries",  # kept for backward-compat (currently unused)
            "notification.prefs",  # ADR-0011 C4 — per-user notification-preference changes
        ]
    )
    # How many messages to read per XREADGROUP call
    consumer_batch_size: int = Field(default=10)
    # Block timeout in milliseconds for XREADGROUP
    consumer_block_ms: int = Field(default=1000)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
