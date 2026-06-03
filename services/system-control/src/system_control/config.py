# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Settings for system-control sidecar."""

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

    service_name: str = Field(default="system-control")
    log_level: str = Field(default="info")

    # HS256 key for verifying internal JWTs addressed to this sidecar.
    # web-bff mints tokens with aud="system-control" signed by this key.
    # Generate: python -c "import secrets; print(secrets.token_hex(32))"
    internal_jwt_key_system_control: str = Field(
        ...,
        min_length=32,
        description=(
            "HS256 key for verifying internal JWTs with aud='system-control'. "
            "Must match INTERNAL_JWT_KEY_SYSTEM_CONTROL in web-bff. "
            "Generate: python -c \"import secrets; print(secrets.token_hex(32))\""
        ),
    )

    # Backup script path inside the container (mounted from host via :ro volume)
    backup_script_path: str = Field(default="/scripts/backup.sh")

    # Docker label prefix used to identify lotsman containers (optional filter)
    docker_label_prefix: str = Field(default="com.docker.compose.project=lotsman")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
