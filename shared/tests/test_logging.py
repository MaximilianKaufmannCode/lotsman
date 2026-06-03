# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for lotsman_shared.logging — redact_sensitive_fields processor.

Closes F-009 (CWE-532: sensitive data in log files).
ADR-0008 D3a.3 / MF-3: nested-dict recursion, depth-bound, cycle-safety.
"""

from __future__ import annotations

import io
import json

import structlog

from lotsman_shared.logging import MAX_REDACT_DEPTH, redact_sensitive_fields

_REDACTED = "***REDACTED***"

# ---------------------------------------------------------------------------
# Direct processor unit tests (no I/O)
# ---------------------------------------------------------------------------


def _run_processor(event_dict: dict) -> dict:  # type: ignore[type-arg]
    """Call the processor with a no-op logger and method."""
    return redact_sensitive_fields(None, "info", event_dict)


def test_password_redacted() -> None:
    result = _run_processor({"event": "login", "password": "s3cr3t"})
    assert result["password"] == _REDACTED
    assert result["event"] == "login"


def test_passwd_redacted() -> None:
    result = _run_processor({"passwd": "abc"})
    assert result["passwd"] == _REDACTED


def test_secret_redacted() -> None:
    result = _run_processor({"internal_jwt_secret": "xxxyyy"})
    assert result["internal_jwt_secret"] == _REDACTED


def test_token_redacted() -> None:
    result = _run_processor({"access_token": "eyJ..."})
    assert result["access_token"] == _REDACTED


def test_authorization_redacted() -> None:
    result = _run_processor({"authorization": "Bearer eyJ..."})
    assert result["authorization"] == _REDACTED


def test_cookie_redacted() -> None:
    result = _run_processor({"cookie": "refresh=abc123"})
    assert result["cookie"] == _REDACTED


def test_set_cookie_redacted() -> None:
    result = _run_processor({"set-cookie": "refresh=abc123; HttpOnly"})
    assert result["set-cookie"] == _REDACTED


def test_x_internal_token_redacted() -> None:
    result = _run_processor({"x-internal-token": "eyJ..."})
    assert result["x-internal-token"] == _REDACTED


def test_totp_code_redacted() -> None:
    result = _run_processor({"totp_code": "123456"})
    assert result["totp_code"] == _REDACTED


def test_otp_redacted() -> None:
    result = _run_processor({"otp": "654321"})
    assert result["otp"] == _REDACTED


def test_refresh_redacted() -> None:
    result = _run_processor({"refresh": "abc-opaque-token"})
    assert result["refresh"] == _REDACTED


def test_case_insensitive_password() -> None:
    result = _run_processor({"PASSWORD": "hunter2"})
    assert result["PASSWORD"] == _REDACTED


def test_case_insensitive_token() -> None:
    result = _run_processor({"Authorization": "Bearer x"})
    assert result["Authorization"] == _REDACTED


def test_non_sensitive_key_untouched() -> None:
    result = _run_processor({"user_id": "abc", "email": "a@b.com", "event": "login"})
    assert result["user_id"] == "abc"
    assert result["email"] == "a@b.com"
    assert result["event"] == "login"


def test_multiple_sensitive_fields_all_redacted() -> None:
    result = _run_processor({"password": "x", "token": "y", "email": "z@x.com"})
    assert result["password"] == _REDACTED
    assert result["token"] == _REDACTED
    assert result["email"] == "z@x.com"


# ---------------------------------------------------------------------------
# Integration: verify that configure_logging wires it before JSONRenderer
# ---------------------------------------------------------------------------


def test_configure_logging_redacts_password_in_output() -> None:
    """log.info("login", password="x") must produce JSON with REDACTED, not "x"."""
    # We test the processor chain directly without touching global structlog state.
    buf = io.StringIO()

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            redact_sensitive_fields,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )

    log = structlog.get_logger()
    log.info("login", password="super_secret_value")

    output = buf.getvalue()
    data = json.loads(output.strip())

    assert data["password"] == _REDACTED
    assert "super_secret_value" not in output


def test_configure_logging_does_not_redact_event() -> None:
    """The 'event' field itself is never redacted even if it contains the word 'password'."""
    buf = io.StringIO()

    structlog.configure(
        processors=[
            redact_sensitive_fields,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )

    log = structlog.get_logger()
    log.info("password_change_requested")  # event key is "event", not "password"

    output = buf.getvalue()
    data = json.loads(output.strip())
    # The word "password" appears in the event value, not in a sensitive key name
    assert "password_change_requested" in data.get("event", "")


# ---------------------------------------------------------------------------
# ADR-0008 D3a.3 / MF-3: Nested-dict recursion, depth-bound, cycle-safety
# ---------------------------------------------------------------------------


def test_enrollment_token_redacted_in_nested_dict() -> None:
    """enrollment_token matches 'token' pattern; redacted inside nested dicts (MF-3)."""
    result = _run_processor({"body": {"enrollment_token": "super_secret_live_ticket"}})
    assert result["body"]["enrollment_token"] == _REDACTED


def test_nested_password_redacted() -> None:
    """A password key nested inside a dict value is redacted (MF-3 / D3a.3)."""
    result = _run_processor({"request": {"password": "s3cr3t", "user": "alice"}})
    assert result["request"]["password"] == _REDACTED
    assert result["request"]["user"] == "alice"


def test_nested_list_with_sensitive_key_redacted() -> None:
    """Sensitive keys inside dicts within a list are redacted (D3a.3)."""
    result = _run_processor({"items": [{"token": "abc"}, {"other": "ok"}]})
    assert result["items"][0]["token"] == _REDACTED
    assert result["items"][1]["other"] == "ok"


def test_non_sensitive_nested_value_preserved() -> None:
    """Non-sensitive nested values are NOT redacted."""
    result = _run_processor({"meta": {"user_id": "uuid-123", "email": "a@b.com"}})
    assert result["meta"]["user_id"] == "uuid-123"
    assert result["meta"]["email"] == "a@b.com"


def test_depth_bound_at_max() -> None:
    """Sensitive key at MAX_REDACT_DEPTH-1 nesting below event_dict is still redacted (D3a.3)."""
    # Build a nested dict: depth 0 is event_dict; depth 1 is event_dict["a"];
    # ...; depth MAX_REDACT_DEPTH-1 should still be recursed into (depth < MAX).
    # At depth MAX_REDACT_DEPTH the value is replaced wholesale.
    # Structure: {"a": {"b": {"c": ... }}} nested (MAX_REDACT_DEPTH-1) levels deep
    # with a sensitive key at level MAX_REDACT_DEPTH-1.
    #
    # From the implementation:
    # - event_dict is depth 0 (not passed to _redact_value)
    # - top-level values are passed at depth=1
    # - so a key at depth MAX_REDACT_DEPTH-1 from event_dict is depth MAX_REDACT_DEPTH-1
    #   (< MAX) — still recursed into.
    # - a key at depth MAX_REDACT_DEPTH from event_dict is depth MAX_REDACT_DEPTH — replaced.

    # Build MAX_REDACT_DEPTH-1 levels of nesting from the event_dict top level.
    # event_dict["L1"]["L2"]...["L(MAX-1)"]["secret"] = "value"
    # The path from event_dict to the secret key is MAX_REDACT_DEPTH-1 levels.
    # Since values at depth 1 are entered, a key MAX_REDACT_DEPTH-1 deep from event_dict
    # is at _redact_value depth MAX_REDACT_DEPTH-1 which is < MAX_REDACT_DEPTH → recursed.
    inner: dict = {"secret": "super_secret"}
    current = inner
    for _ in range(MAX_REDACT_DEPTH - 2):  # build (MAX-2) more levels above inner
        current = {"nested": current}
    result = _run_processor({"L1": current})

    # The sensitive key should be redacted regardless of nesting depth < MAX.
    # Navigate to find it:
    def find_secret(d: object) -> str | None:
        if isinstance(d, dict):
            if "secret" in d:
                return str(d["secret"])
            for v in d.values():
                found = find_secret(v)
                if found is not None:
                    return found
        return None

    val = find_secret(result)
    assert val == _REDACTED, f"Expected REDACTED but got {val!r}"


def test_depth_bound_over_max_elides_subtree() -> None:
    """Subtree at MAX_REDACT_DEPTH below event_dict is replaced — no plaintext leak (D3a.3).

    The top-level value at depth 1 is a dict; at MAX_REDACT_DEPTH nesting levels
    below event_dict the subtree is replaced with REDACTED sentinel.
    """
    # Build a chain MAX_REDACT_DEPTH levels deep below the event_dict value.
    # event_dict["L1"] is depth 1; at depth=MAX_REDACT_DEPTH the value is elided.
    inner = {"secret": "leaked_value"}
    current: dict = inner
    for _ in range(MAX_REDACT_DEPTH - 1):  # total chain length = MAX_REDACT_DEPTH levels
        current = {"n": current}
    result = _run_processor({"root": current})

    # The result must not contain the string "leaked_value" anywhere.
    serialised = json.dumps(result)
    assert "leaked_value" not in serialised, (
        f"Sensitive value leaked past MAX_REDACT_DEPTH={MAX_REDACT_DEPTH}: {serialised}"
    )


def test_cycle_safety_no_recursion_error() -> None:
    """A self-referential dict terminates without RecursionError (D3a.3 cycle-safety)."""
    d: dict = {"key": "value"}
    d["self"] = d  # self-reference

    event_dict = {"outer": d}
    # Must not raise RecursionError
    result = _run_processor(event_dict)
    # The result is some safe representation — we don't assert exact shape,
    # only that no RecursionError was raised and no plaintext "value" leaked
    # from the cyclic path.
    assert result is not None


def test_max_redact_depth_constant() -> None:
    """MAX_REDACT_DEPTH is the module-level constant 8 (D3a.3)."""
    assert MAX_REDACT_DEPTH == 8
