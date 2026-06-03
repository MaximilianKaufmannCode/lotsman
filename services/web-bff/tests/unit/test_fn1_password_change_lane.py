# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Regression tests for F-N-1: /password/change lane-selection bug.

F-N-1: A normal logged-in user POSTing to /password/change with
  ``Authorization: Bearer <access JWT>`` (no ``enrollment_token`` body field)
  was incorrectly routed into the enrollment-ticket lane because
  ``_extract_enrollment_token`` treated the JWT as an opaque ticket.

Fix: route-local helper ``_extract_password_change_credential`` tries RS256
JWT decode FIRST; only falls back to opaque-ticket if decode fails.

Tests (T-FN1-a through T-FN1-d) cover all four discrimination paths.
The 422-no-echo test (T-FN1-e) closes ADR D3a.4 QA assertion.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import guards
# ---------------------------------------------------------------------------

try:
    from web_bff.api.deps import AccessClaims
    from web_bff.api.v1.auth import _extract_password_change_credential
    from web_bff.config import Settings

    _IMPORTABLE = True
except ImportError:
    _IMPORTABLE = False

pytestmark = pytest.mark.skipif(not _IMPORTABLE, reason="web_bff not importable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> Settings:
    """Create a minimal Settings instance with required keys (no RS256 key → dev mode)."""
    return Settings(
        internal_jwt_key_auth="a" * 32,
        internal_jwt_key_registry="b" * 32,
        internal_jwt_key_notification="c" * 32,
        internal_jwt_key_audit="d" * 32,
        jwt_public_key_path=None,  # dev mode: structural decode only
    )


def _make_access_jwt_str(role: str = "editor", user_id: str | None = None) -> str:
    """Mint a structurally valid access JWT (HS256/unsigned-struct accepted in dev mode)."""
    import jwt  # PyJWT

    uid = user_id or str(uuid.uuid4())
    payload = {
        "sub": uid,
        "role": role,
        "email": f"{role}@example.com",
        "sid": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "iat": int(time.time()),
        "nbf": int(time.time()),
        "exp": int(time.time()) + 900,
        "aud": "lotsman-spa",
        "iss": "lotsman-auth",
    }
    # Encode with HS256 — in test env (no RS256 public key) _verify_access_jwt
    # falls through to ``verify_signature: False`` path, so alg mismatch is OK.
    return jwt.encode(payload, key="test-external-jwt-secret", algorithm="HS256")


# ---------------------------------------------------------------------------
# T-FN1-a: Normal-path regression — access JWT → actor-JWT lane
# ---------------------------------------------------------------------------


def test_fn1a_normal_path_access_jwt_routes_to_jwt_lane() -> None:
    """T-FN1-a: ``Authorization: Bearer <valid access JWT>`` with no enrollment_token
    body field → _extract_password_change_credential returns (None, <AccessClaims>)
    i.e. the actor-JWT lane, NOT the ticket lane.

    This is the regression guard for F-N-1: the JWT must NOT be consumed as an
    opaque enrollment ticket.
    """
    settings = _make_settings()
    access_jwt = _make_access_jwt_str(role="editor")

    body: dict[str, Any] = {
        "current_password": "OldPassword!123",
        "new_password": "NewPassword!456",
        "re_mfa_token": "123456",
    }
    authorization = f"Bearer {access_jwt}"

    result = _extract_password_change_credential(body, authorization, settings)

    assert result is not None, "Expected a credential result, got None (would cause 401)"
    lane, cred_value = result
    assert lane is None, (
        f"T-FN1-a: Expected actor-JWT lane (lane=None) but got lane={lane!r}. "
        "The access JWT is being misrouted into the enrollment-ticket lane (F-N-1 regression)."
    )
    assert isinstance(cred_value, AccessClaims), (
        f"T-FN1-a: Expected AccessClaims but got {type(cred_value)}."
    )


# ---------------------------------------------------------------------------
# T-FN1-b: First-login still works — opaque non-JWT bearer → ticket lane
# ---------------------------------------------------------------------------


def test_fn1b_opaque_enrollment_token_in_auth_header_routes_to_ticket_lane() -> None:
    """T-FN1-b: ``Authorization: Bearer <opaque-non-JWT-string>`` (SPA first-login:
    ``applyToken(enrollment_token)`` sets the header, per ADR-0008 D7) → ticket lane.

    The string is NOT a valid JWT so _verify_access_jwt raises → fallback to ticket lane.
    """
    settings = _make_settings()

    # Opaque enrollment ticket — not a valid JWT structure.
    opaque_ticket = "opaque-enrollment-ticket-not-a-jwt"

    body: dict[str, Any] = {"new_password": "NewPassword!456"}
    authorization = f"Bearer {opaque_ticket}"

    result = _extract_password_change_credential(body, authorization, settings)

    assert result is not None, "Expected a credential result, got None (would cause 401)"
    lane, cred_value = result
    assert lane == "ticket", (
        f"T-FN1-b: Expected ticket lane for opaque bearer but got lane={lane!r}. "
        "SPA first-login (applyToken sets Authorization header) must still work."
    )
    assert cred_value == opaque_ticket, (
        f"T-FN1-b: Expected opaque_ticket value but got {cred_value!r}."
    )


# ---------------------------------------------------------------------------
# T-FN1-c: Body field wins — explicit enrollment_token in body → ticket lane
# ---------------------------------------------------------------------------


def test_fn1c_body_enrollment_token_wins_regardless_of_auth_header() -> None:
    """T-FN1-c: Body ``enrollment_token`` non-empty → ticket lane, regardless of
    Authorization header value (body field is PRIMARY contract per ADR-0008 D3).
    """
    settings = _make_settings()
    access_jwt = _make_access_jwt_str(role="admin")

    body: dict[str, Any] = {
        "enrollment_token": "body-explicit-ticket-value",
        "new_password": "NewPassword!456",
    }
    authorization = f"Bearer {access_jwt}"

    result = _extract_password_change_credential(body, authorization, settings)

    assert result is not None, "Expected a credential result, got None"
    lane, cred_value = result
    assert lane == "ticket", (
        f"T-FN1-c: Expected ticket lane when body has enrollment_token, got lane={lane!r}."
    )
    assert cred_value == "body-explicit-ticket-value", (
        f"T-FN1-c: Expected body enrollment_token value but got {cred_value!r}."
    )


def test_fn1c_body_enrollment_token_wins_even_without_auth_header() -> None:
    """T-FN1-c variant: body enrollment_token with no Authorization header → ticket lane."""
    settings = _make_settings()

    body: dict[str, Any] = {
        "enrollment_token": "  body-ticket-with-whitespace  ",
        "new_password": "NewPassword!456",
    }

    result = _extract_password_change_credential(body, authorization=None, settings=settings)

    assert result is not None
    lane, cred_value = result
    assert lane == "ticket"
    # strip() applied
    assert cred_value == "body-ticket-with-whitespace"


# ---------------------------------------------------------------------------
# T-FN1-d: Neither credential present → None (caller raises 401)
# ---------------------------------------------------------------------------


def test_fn1d_no_credential_returns_none() -> None:
    """T-FN1-d: No enrollment_token in body AND no Authorization header →
    ``_extract_password_change_credential`` returns None so the handler raises 401.
    """
    settings = _make_settings()

    body: dict[str, Any] = {
        "current_password": "OldPassword!123",
        "new_password": "NewPassword!456",
    }

    result = _extract_password_change_credential(body, authorization=None, settings=settings)

    assert result is None, (
        f"T-FN1-d: Expected None (→ 401) when no credential present, got {result!r}."
    )


def test_fn1d_bearer_missing_token_value_returns_none() -> None:
    """T-FN1-d variant: ``Authorization: Bearer `` (empty bearer value) → None."""
    settings = _make_settings()
    body: dict[str, Any] = {"new_password": "NewPassword!456"}

    result = _extract_password_change_credential(body, authorization="Bearer ", settings=settings)

    assert result is None, f"T-FN1-d: Expected None for empty Bearer value, got {result!r}."


def test_fn1d_non_bearer_scheme_returns_none() -> None:
    """T-FN1-d variant: ``Authorization: Basic ...`` → None."""
    settings = _make_settings()
    body: dict[str, Any] = {"new_password": "NewPassword!456"}

    result = _extract_password_change_credential(
        body, authorization="Basic dXNlcjpwYXNz", settings=settings
    )

    assert result is None


# ---------------------------------------------------------------------------
# Handler source-level structural assertions
# ---------------------------------------------------------------------------


def test_fn1_handler_uses_new_route_local_helper() -> None:
    """The change_password handler must use _extract_password_change_credential
    (route-local, F-N-1 fix) — not _extract_enrollment_token directly.
    """
    import inspect

    try:
        import web_bff.api.v1.auth as bff_auth_module
    except ImportError as e:
        pytest.skip(f"web_bff.api.v1.auth not importable: {e}")

    handler = getattr(bff_auth_module, "change_password", None)
    assert handler is not None, "change_password handler not found"
    source = inspect.getsource(handler)

    assert "_extract_password_change_credential" in source, (
        "F-N-1: change_password must use _extract_password_change_credential "
        "for lane selection — not _extract_enrollment_token (which lacks JWT-decode step)."
    )
    # _extract_enrollment_token must NOT appear in the handler body either:
    assert "_extract_enrollment_token" not in source, (
        "F-N-1: _extract_enrollment_token must NOT be called inside change_password. "
        "The route-local helper _extract_password_change_credential replaces it."
    )


def test_fn1_handler_still_calls_change_password_with_ticket_for_ticket_lane() -> None:
    """The change_password handler must still call change_password_with_ticket for tickets."""
    import inspect

    try:
        import web_bff.api.v1.auth as bff_auth_module
    except ImportError as e:
        pytest.skip(f"web_bff.api.v1.auth not importable: {e}")

    handler = getattr(bff_auth_module, "change_password", None)
    assert handler is not None
    source = inspect.getsource(handler)

    assert "change_password_with_ticket" in source, (
        "Ticket lane must still call auth_client.change_password_with_ticket "
        "(first-login / enrollment flow)."
    )
    assert "auth_client.change_password(" in source, (
        "Actor-JWT lane must call auth_client.change_password (normal profile change)."
    )


# ---------------------------------------------------------------------------
# T-FN1-e: 422-no-echo — confirm coverage via auth-service schema assertions
# (ADR D3a.4 QA assertion — auth-service test_adr0008_security_gate.py already
#  carries the full T6e tests; this test asserts the schema-level guard is present)
# ---------------------------------------------------------------------------


def test_fn1e_422_no_echo_hide_input_configured_in_auth_service_schemas() -> None:
    """T-FN1-e (ADR D3a.4): ConfirmTotpEnrollmentEnrollmentRequest must have
    ``model_config = ConfigDict(hide_input_in_errors=True)`` at the class level
    to prevent enrollment_token from being echoed in 422 responses.

    Full endpoint-level 422-no-echo coverage lives in:
      services/auth-service/tests/unit/use_cases/test_adr0008_security_gate.py
      (tests test_t6e_422_on_enroll_does_not_echo_enrollment_token and
       test_t6e_422_on_confirm_does_not_echo_enrollment_token)

    This BFF-suite test asserts the static schema guard so regressions are
    caught without needing a live database or Redis connection.
    """
    try:
        from auth_service.api.schemas import (  # type: ignore[import]
            ChangePasswordEnrollmentRequest,
            ConfirmTotpEnrollmentEnrollmentRequest,
            EnrollTotpEnrollmentRequest,
        )
    except ImportError:
        pytest.skip("auth_service.api.schemas not importable from web-bff test suite")

    for schema_cls in (
        EnrollTotpEnrollmentRequest,
        ConfirmTotpEnrollmentEnrollmentRequest,
        ChangePasswordEnrollmentRequest,
    ):
        config = getattr(schema_cls, "model_config", {})
        assert config.get("hide_input_in_errors") is True, (
            f"T-FN1-e (ADR D3a.4 / F-N-2): {schema_cls.__name__}.model_config must have "
            "hide_input_in_errors=True at top level (NOT inside json_schema_extra). "
            "This prevents enrollment_token from being echoed in 422 validation error responses."
        )
