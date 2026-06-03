# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Structured logging configuration for Лоцман services.

Uses structlog with JSON renderer for production and coloured console output
for local development (detected via LOG_FORMAT env var).

Standard log fields:
    ts          - ISO-8601 UTC timestamp
    level       - log level string
    service     - the service name (set once at startup via configure_logging)
    request_id  - from ASGI context var (bound by RequestIdMiddleware)
    actor_user_id - from ASGI context var (bound by auth deps)
    event       - the log message

Usage::

    from lotsman_shared.logging import configure_logging
    configure_logging(service="registry-service", level="info")

    import structlog
    log = structlog.get_logger()
    log.info("document_created", document_id=str(doc.id))
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# F-009: Sensitive field redaction processor
# ---------------------------------------------------------------------------

# Case-insensitive regex that matches log field keys containing sensitive data.
# ``enrollment_token`` is covered by the ``token`` alternative.
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|authorization|cookie|set-cookie"
    r"|x-internal-token|totp.*code|otp|refresh)",
    re.IGNORECASE,
)

_REDACTED = "***REDACTED***"
_CYCLE_SENTINEL = "<cycle>"

# ADR-0008 rev. 3 D3a.3 / MF-3: fixed depth cap for recursion.
# Top-level event_dict = depth 0; children are depth 1, etc.
# At the depth cap the entire still-nested subtree is replaced with _REDACTED
# (elide-by-redacting) so sensitive keys buried past the cap can never leak.
MAX_REDACT_DEPTH = 8


def _redact_value(
    value: Any,
    depth: int,
    visited: set[int],
) -> Any:
    """Recursively redact sensitive keys inside nested dicts/lists.

    Parameters
    ----------
    value:
        The current value being examined.
    depth:
        Current recursion depth (0 = top-level event_dict).
    visited:
        Set of ``id()``s of container objects on the current descent path.
        Used for cycle detection (D3a.3 / MF-3).
    """
    if isinstance(value, dict):
        obj_id = id(value)
        if obj_id in visited:
            # Cycle detected — replace with sentinel, do not recurse (D3a.3).
            return _CYCLE_SENTINEL
        if depth >= MAX_REDACT_DEPTH:
            # Depth cap reached — elide-by-redacting (D3a.3 bound behaviour).
            return _REDACTED
        visited.add(obj_id)
        try:
            result: dict[str, Any] = {}
            for k, v in value.items():
                if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k):
                    result[k] = _REDACTED
                else:
                    result[k] = _redact_value(v, depth + 1, visited)
            return result
        finally:
            visited.discard(obj_id)
    elif isinstance(value, list):
        obj_id = id(value)
        if obj_id in visited:
            return _CYCLE_SENTINEL
        if depth >= MAX_REDACT_DEPTH:
            return _REDACTED
        visited.add(obj_id)
        try:
            return [_redact_value(item, depth + 1, visited) for item in value]
        finally:
            visited.discard(obj_id)
    return value


def redact_sensitive_fields(
    logger: Any,
    method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor that scrubs values of sensitive keys at any depth.

    ADR-0008 rev. 3 D3a.3 / MF-3: recursively redacts nested dict/list values.

    Runs BEFORE JSONRenderer so sensitive values never reach the output stream.
    Keys are matched case-insensitively against the pattern:
        password | passwd | secret | token | authorization | cookie |
        set-cookie | x-internal-token | totp.*code | otp | refresh

    ``enrollment_token`` is covered by the ``token`` sub-pattern.

    Recursion is bounded by ``MAX_REDACT_DEPTH = 8`` (top-level = depth 0) and
    protected against cycles via a ``visited`` set of container ``id()``s.
    At the depth cap, the still-nested subtree is replaced with ``***REDACTED***``
    (elide-by-redacting — the safe default to prevent leakage past the cap).

    Closes F-009 (CWE-532 — sensitive data in log files).
    """
    visited: set[int] = set()
    for key in list(event_dict.keys()):
        if _SENSITIVE_KEY_RE.search(key):
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key], depth=1, visited=visited)
    return event_dict


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def configure_logging(service: str, level: str = "info") -> None:
    """Configure structlog for the given service.

    Must be called once at application startup (in ``create_app()`` or
    before the first log call).

    Args:
        service: Service name injected into every log record, e.g. ``'registry-service'``.
        level: Python log level string: ``'debug'``, ``'info'``, ``'warning'``, ``'error'``.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Configure stdlib logging root handler so uvicorn/sqlalchemy logs are captured.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # Note: add_logger_name is omitted — it requires stdlib Logger, not PrintLogger.
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # F-009: redact sensitive fields BEFORE JSONRenderer sees them.
        redact_sensitive_fields,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Inject the service name into every log record automatically.
    structlog.contextvars.bind_contextvars(service=service)
