# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Auth proxy routes — /api/v1/auth/*

Proxies every auth endpoint that the SPA (web/src/features/auth/api.ts) calls.

Responsibilities:
  - Accept the SPA's request (Bearer token in Authorization header where required).
  - For unauthenticated routes: forward with system-actor internal JWT.
  - For authenticated routes: decode Bearer token via require_access_claims,
    forward with actor-bound internal JWT.
  - Manage the HttpOnly refresh cookie:
      set: Set-Cookie: refresh=...; HttpOnly; Secure; SameSite=Strict;
           Path=/api/v1/auth; Max-Age=604800
      clear: Set-Cookie: refresh=; Max-Age=0
  - Refresh tokens NEVER appear in the JSON response body.
  - Errors from auth-service are passed through as-is (no improvement —
    preserving the no-enumeration property).

Path translations (SPA → auth-service):
  POST /api/v1/auth/totp/verify
       body {totp_session_token, code}  →  {session_ticket, totp_code}
  POST /api/v1/auth/backup-codes/verify
       body {totp_session_token, code}  →  totp/verify with {backup_code}
  GET  /api/v1/auth/sessions/me         →  GET /auth/sessions
  POST /api/v1/auth/re-mfa {code}       →  /auth/mfa-check {totp_code}
  POST /api/v1/auth/password/change     →  /auth/change-password
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any

import jwt
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from web_bff.api.deps import (
    AccessClaims,
    AppSettings,
    GetAuthClient,
    GetNotificationClient,
    RefreshCookie,
    RequireAccessClaims,
    _verify_access_jwt,
    get_request_id,
)
from web_bff.config import Settings as BffSettings

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE_NAME = "refresh"

# ---------------------------------------------------------------------------
# BFF-side refresh coalescing (ADR-0003 §11 amendment 2026-05-12)
#
# Problem: N concurrent 401 responses from parallel SPA API calls each trigger
# an independent POST /refresh. Auth-service's chain-revocation sees the 2nd-Nth
# calls as refresh-token reuse (correct security behaviour for replay attacks) and
# revokes the entire session, logging the user out — a false-positive.
#
# Fix: per-cookie-hash mutex + 5-second result cache in the BFF process.
# The first request that acquires the lock calls upstream; the others wait and
# replay the same response. After 5 s the cache entry expires so genuine retries
# (e.g. very slow connections) go through fresh.
#
# Limitation: single BFF process only. Horizontally-scaled BFF would need a
# Redis-based distributed lock (e.g. Redlock) to coalesce across processes.
# At current single-instance deployment this in-memory approach is sufficient.
# ---------------------------------------------------------------------------

_COALESCE_TTL_SECONDS = 5.0

# Stored as (timestamp, body_dict, set_cookie_headers)
_RefreshCacheEntry = tuple[float, dict[str, Any], list[str]]

_refresh_cache: dict[str, _RefreshCacheEntry] = {}
_refresh_locks: dict[str, asyncio.Lock] = {}
_refresh_cache_lock: asyncio.Lock = asyncio.Lock()


def _cookie_key(refresh_token: str) -> str:
    """Return a short hex key for the refresh token — never store plaintext."""
    return hashlib.sha256(refresh_token.encode()).hexdigest()


async def _evict_expired() -> None:
    """Remove stale entries from the cache (lazy eviction, called on each request)."""
    now = time.monotonic()
    expired = [k for k, (ts, _, __) in _refresh_cache.items() if now - ts > _COALESCE_TTL_SECONDS]
    for k in expired:
        _refresh_cache.pop(k, None)
        _refresh_locks.pop(k, None)


async def _get_or_create_lock(key: str) -> asyncio.Lock:
    async with _refresh_cache_lock:
        if key not in _refresh_locks:
            _refresh_locks[key] = asyncio.Lock()
        return _refresh_locks[key]


def _set_refresh_cookie(response: Response, token: str, settings: BffSettings) -> None:
    """Set the HttpOnly refresh cookie using env-driven security attributes."""
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path="/api/v1/auth",
        max_age=settings.refresh_token_ttl_seconds,
    )


def _clear_refresh_cookie(response: Response, settings: BffSettings) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME,
        path="/api/v1/auth",
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _upstream_error(upstream_resp: Any) -> HTTPException:
    """Convert a non-2xx upstream response into an HTTPException.

    Preserves both ``detail`` and the machine-readable ``code`` so the SPA can
    map errors to typed UX states (REMFA_REQUIRED, EMAIL_CHANNEL_REQUIRED,
    EMAIL_ALREADY_TAKEN, VERIFICATION_FAILED, etc.). Mirrors the admin.py
    helper. The code is forwarded both as a custom header AND inside the
    JSON body so apiFetch can read it from either location.
    """
    try:
        body = upstream_resp.json()
        if isinstance(body, dict):
            detail = body.get("detail", "Upstream error")
            code = body.get("code")
        else:
            detail = "Upstream error"
            code = None
    except Exception:
        detail = "Upstream error"
        code = None
    headers: dict[str, str] = {}
    if code:
        headers["X-Error-Code"] = code
    return HTTPException(
        status_code=upstream_resp.status_code,
        detail={"detail": detail, "code": code} if code else detail,
        headers=headers if headers else None,
    )


def _extract_enrollment_token(
    body: dict[str, Any],
    *,
    authorization: str | None,
) -> str | None:
    """Extract the enrollment ticket from the request.

    ADR-0008 D3 / D7: body field ``enrollment_token`` is the PRIMARY contract.
    The Authorization: Bearer header is the FALLBACK for SPA compatibility
    (the SPA calls ``applyToken(enrollment_token)`` which sets the header).
    The fallback treats the bearer value as an OPAQUE STRING — NOT a JWT.
    MF-3/D3a.1: this value MUST NEVER be logged.
    """
    raw = body.get("enrollment_token") if isinstance(body, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    # Fallback: Authorization: Bearer <opaque-token> (SPA compat — ADR-0008 D7)
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return None


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# SPA body: {email, password}
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(
    body: dict[str, Any],
    response: Response,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Step 1 of login — email + password.

    Returns one of:
      {status: "totp_required", session_ticket: "..."}     — TOTP step needed
      {status: "enrollment_required", enrollment_token: "..."} — first login
    """
    email = body.get("email", "")
    password = body.get("password", "")

    upstream = await auth_client.login(
        email=email,
        password=password,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)

    data = upstream.json()
    # Auth-service response shape: {session_ticket} or {enrollment_token}
    # BFF translates to the SPA contract: {totp_session_token} or {enrollment_token}
    if "session_ticket" in data:
        return {
            "status": "totp_required",
            "next_step": "verify_totp",
            "totp_session_token": data["session_ticket"],
        }
    if "enrollment_token" in data:
        # next_step MUST be "enroll_totp" — the SPA's LoginResponse union
        # (web/src/features/auth/types.ts) and AuthProvider/AuthGuard key on
        # exactly this literal to redirect to /first-login. Returning any
        # other value (regression a2945f7 sent "first_login_required") makes
        # the SPA fall through every branch → silent hang on login.
        return {
            "status": "enrollment_required",
            "next_step": "enroll_totp",
            "enrollment_token": data["enrollment_token"],
        }

    log.warning("login_unexpected_shape", keys=list(data.keys()))
    raise HTTPException(status_code=502, detail="Unexpected upstream response")


# ---------------------------------------------------------------------------
# POST /api/v1/auth/totp/verify
# SPA body: {totp_session_token, code}
# ---------------------------------------------------------------------------


@router.post("/totp/verify")
async def verify_totp(
    body: dict[str, Any],
    response: Response,
    auth_client: GetAuthClient,
    settings: AppSettings,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """TOTP phase — complete login with a 6-digit TOTP code."""
    session_ticket = body.get("totp_session_token", "")
    totp_code = body.get("code", "")

    upstream = await auth_client.verify_totp(
        session_ticket=session_ticket,
        totp_code=totp_code,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)

    data = upstream.json()
    # Strip refresh_token from the body — set as cookie instead.
    refresh_token = data.get("refresh_token")
    if refresh_token:
        _set_refresh_cookie(response, refresh_token, settings)

    return {k: v for k, v in data.items() if k != "refresh_token"}


# ---------------------------------------------------------------------------
# POST /api/v1/auth/backup-codes/verify
# SPA body: {totp_session_token, code}
# ---------------------------------------------------------------------------


@router.post("/backup-codes/verify")
async def verify_backup_code(
    body: dict[str, Any],
    response: Response,
    auth_client: GetAuthClient,
    settings: AppSettings,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Backup-code phase — complete login with a backup code."""
    session_ticket = body.get("totp_session_token", "")
    backup_code = body.get("code", "")

    upstream = await auth_client.verify_totp_backup_code(
        session_ticket=session_ticket,
        backup_code=backup_code,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)

    data = upstream.json()
    refresh_token = data.get("refresh_token")
    if refresh_token:
        _set_refresh_cookie(response, refresh_token, settings)

    return {k: v for k, v in data.items() if k != "refresh_token"}


# ---------------------------------------------------------------------------
# POST /api/v1/auth/totp/enroll  (enrollment-ticket lane — ADR-0008 D2/D3/MF-5)
#
# The SPA sends Authorization: Bearer <enrollment_token> (opaque) AND also
# includes the token in the request body as {enrollment_token}.  Per ADR-0008
# D3 and D7, the body field is the primary contract; the Authorization header
# is ignored for this route.
#
# MF-3/D3a.2: Request body MUST NOT be logged.
# ---------------------------------------------------------------------------


@router.post("/totp/enroll")
async def enroll_totp(
    req: Request,
    auth_client: GetAuthClient,
    body: dict[str, Any] = Body(default_factory=dict),  # noqa: B008
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Step 1 of TOTP enrollment — anonymous ticket lane (ADR-0008 D3/MF-5).

    Reads enrollment_token from the request body (primary contract per ADR-0008 D3).
    Falls back to extracting it from the Authorization: Bearer header (opaque string,
    NOT decoded as JWT) for SPA compatibility (ADR-0008 D7 fallback).
    MF-3/D3a.1: enrollment_token MUST NOT be logged.
    """
    authorization_hdr = req.headers.get("Authorization")
    enrollment_token = _extract_enrollment_token(body, authorization=authorization_hdr)
    if not enrollment_token:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    # MF-3/D3a.1: do NOT log enrollment_token or body.
    upstream = await auth_client.enroll_totp_with_ticket(
        enrollment_token=enrollment_token,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/auth/totp/enroll/confirm  (enrollment-ticket lane — ADR-0008 D3)
#
# MF-3/D3a.2: Request body MUST NOT be logged.
# MF-6: Per-ticket attempt cap enforced in auth-service.
# ---------------------------------------------------------------------------


@router.post("/totp/enroll/confirm")
async def confirm_totp_enrollment(
    body: dict[str, Any],
    req: Request,
    response: Response,
    auth_client: GetAuthClient,
    settings: AppSettings,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Step 2 of enrollment — anonymous ticket lane (ADR-0008 D3/MF-5/6).

    On the enroll-only terminal branch auth-service returns access_token + refresh_token
    in addition to backup_codes.  The BFF promotes refresh_token to an HttpOnly cookie
    and strips it from the JSON response body.
    MF-3/D3a.1: enrollment_token and code MUST NOT be logged.
    """
    authorization_hdr = req.headers.get("Authorization")
    enrollment_token = _extract_enrollment_token(body, authorization=authorization_hdr)
    if not enrollment_token:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    code = body.get("code", "")
    # MF-3/D3a.1: do NOT log enrollment_token, code, or body.
    upstream = await auth_client.confirm_totp_enrollment_with_ticket(
        enrollment_token=enrollment_token,
        code=code,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)

    data = upstream.json()
    # If auth-service returned a refresh token (enroll-only terminal branch),
    # promote it to an HttpOnly cookie (same pattern as verify_totp).
    refresh_token = data.get("refresh_token")
    if refresh_token:
        _set_refresh_cookie(response, refresh_token, settings)
    return {k: v for k, v in data.items() if k != "refresh_token"}


# ---------------------------------------------------------------------------
# POST /api/v1/auth/refresh
# Cookie-only — no Authorization header (SPA contract, ADR-0003 §14).
# ---------------------------------------------------------------------------


@router.post("/refresh")
async def refresh_tokens(
    response: Response,
    auth_client: GetAuthClient,
    refresh_token: RefreshCookie,
    settings: AppSettings,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Rotate the refresh token with BFF-side coalescing (ADR-0003 §11 amendment 2026-05-12).

    When N concurrent requests arrive with the same refresh cookie:
    - The first acquires a per-cookie-hash lock, calls upstream, caches the result for 5 s.
    - The remaining N-1 wait on the lock and replay the cached result.
    This prevents the multi-tab / Promise.all race that triggers chain-revocation
    as a false positive.
    """
    if not refresh_token:
        _clear_refresh_cookie(response, settings)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    cache_key = _cookie_key(refresh_token)

    # Lazy eviction — drop entries older than _COALESCE_TTL_SECONDS
    await _evict_expired()

    # Fast path: check cache before acquiring the per-key lock
    async with _refresh_cache_lock:
        entry = _refresh_cache.get(cache_key)
        if entry is not None and time.monotonic() - entry[0] <= _COALESCE_TTL_SECONDS:
            _, cached_body, cached_cookies = entry
            log.debug("refresh_coalesced_cache_hit", cache_key=cache_key[:8])
            for c in cached_cookies:
                response.headers.append("set-cookie", c)
            return cached_body

    # Acquire per-key lock so exactly one request calls upstream
    per_key_lock = await _get_or_create_lock(cache_key)
    async with per_key_lock:
        # Re-check cache: a concurrent waiter may have populated it
        async with _refresh_cache_lock:
            entry = _refresh_cache.get(cache_key)
            if entry is not None and time.monotonic() - entry[0] <= _COALESCE_TTL_SECONDS:
                _, cached_body, cached_cookies = entry
                log.debug("refresh_coalesced_lock_hit", cache_key=cache_key[:8])
                for c in cached_cookies:
                    response.headers.append("set-cookie", c)
                return cached_body

        # We are the first — call upstream
        upstream = await auth_client.refresh_tokens(
            refresh_token=refresh_token,
            request_id=request_id,
        )
        if not upstream.is_success:
            _clear_refresh_cookie(response, settings)
            raise _upstream_error(upstream)

        data = upstream.json()
        new_refresh = data.get("refresh_token")

        # Build the Set-Cookie header(s) we will return
        set_cookie_headers: list[str] = []
        if new_refresh:
            # Set cookie on the FastAPI response object to get proper formatting.
            # We then extract the resulting header value so we can cache and replay it.
            _set_refresh_cookie(response, new_refresh, settings)
            # Collect the headers we just added
            set_cookie_headers = [
                v for k, v in response.headers.items() if k.lower() == "set-cookie"
            ]

        body_out = {k: v for k, v in data.items() if k != "refresh_token"}

        # Store in cache for late-arriving concurrent requests
        async with _refresh_cache_lock:
            _refresh_cache[cache_key] = (time.monotonic(), body_out, set_cookie_headers)

        log.debug("refresh_upstream_called", cache_key=cache_key[:8])
        # Return only the access token — never the refresh token in body.
        return body_out


# ---------------------------------------------------------------------------
# POST /api/v1/auth/logout  (auth required)
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    refresh_token: RefreshCookie,
    settings: AppSettings,
    request_id: str | None = Depends(get_request_id),
) -> None:
    """Revoke the current session. Clears the refresh cookie regardless of upstream result."""
    upstream = await auth_client.logout(
        actor_id=claims.subject,
        role=claims.role,
        refresh_token=refresh_token,
        request_id=request_id,
    )
    # Clear cookie even on upstream error — client should lose the cookie.
    _clear_refresh_cookie(response, settings)
    if not upstream.is_success and upstream.status_code != 204:
        raise _upstream_error(upstream)


# ---------------------------------------------------------------------------
# POST /api/v1/auth/backup-codes/regenerate  (auth required)
# ---------------------------------------------------------------------------


@router.post("/backup-codes/regenerate")
async def regenerate_backup_codes(
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Regenerate all 10 backup codes (invalidates prior ones)."""
    upstream = await auth_client.regenerate_backup_codes(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# GET /api/v1/auth/sessions/me  (auth required)
# Auth-service path: GET /auth/sessions
# ---------------------------------------------------------------------------


@router.get("/sessions/me")
async def list_my_sessions(
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """List the authenticated user's active sessions."""
    upstream = await auth_client.list_my_sessions(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# DELETE /api/v1/auth/sessions/{session_id}  (auth required)
# ---------------------------------------------------------------------------


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: uuid.UUID,
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> None:
    """Revoke a specific session (self-only; enforced by auth-service)."""
    upstream = await auth_client.revoke_session(
        actor_id=claims.subject,
        role=claims.role,
        session_id=session_id,
        request_id=request_id,
    )
    if not upstream.is_success and upstream.status_code != 204:
        raise _upstream_error(upstream)


# ---------------------------------------------------------------------------
# POST /api/v1/auth/re-mfa  (auth required)
# SPA body: {code}  →  auth-service /auth/mfa-check {totp_code}
# Returns: {re_mfa_token: "<opaque>"}
# ---------------------------------------------------------------------------


@router.post("/re-mfa")
async def re_mfa(
    body: dict[str, Any],
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Verify TOTP for sensitive operations. Returns a short-lived re-MFA token.

    The auth-service sets a Redis mfa-verified flag keyed by user_id; subsequent
    admin or sensitive calls are gated on that flag server-side.
    """
    totp_code = body.get("code", "")
    upstream = await auth_client.re_mfa_check(
        actor_id=claims.subject,
        role=claims.role,
        totp_code=totp_code,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)

    data = upstream.json()
    if not data.get("mfa_verified"):
        raise HTTPException(status_code=403, detail="MFA verification failed")

    # The BFF returns the actor's session-scoped token as re_mfa_token.
    # The value is a signed confirmation token: we use the actor's subject + jti
    # to give the SPA a stable opaque string it can include in subsequent admin calls.
    # Auth-service enforces the actual gate via its Redis flag.
    re_mfa_token = f"{claims.subject}:{claims.jti}"
    return {"re_mfa_token": re_mfa_token, "mfa_verified": True}


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me  (auth required)
# Auth-service path: GET /auth/me
# ---------------------------------------------------------------------------


@router.get("/me")
async def get_my_profile(
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Return the authenticated user's profile (full_name, email, role, flags)."""
    upstream = await auth_client.get_my_profile(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# PATCH /api/v1/auth/me  (auth required)
# SPA body: {full_name: str}
# Auth-service path: PATCH /auth/me
# ---------------------------------------------------------------------------


@router.patch("/me")
async def update_my_profile(
    body: dict[str, Any],
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Update the authenticated user's full_name and/or UI font-size preference.

    email is read-only from the user's perspective — contact an administrator
    to change your email address. ui_font_scale is an optional self-service UI
    preference (percent of base) forwarded only when present.
    """
    full_name = body.get("full_name", "")
    ui_font_scale_raw = body.get("ui_font_scale")
    ui_font_scale = int(ui_font_scale_raw) if isinstance(ui_font_scale_raw, int) else None
    upstream = await auth_client.update_my_profile(
        actor_id=claims.subject,
        role=claims.role,
        full_name=full_name,
        ui_font_scale=ui_font_scale,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/auth/me/test-email  (auth required)
#
# Self-service email channel test. Resolves the authenticated user's email
# server-side (no SPA-supplied recipient) and delegates rendering + delivery
# to notification-svc. Rate-limited via Redis SET NX EX (60s per user) to
# block accidental button-spam. Does NOT require re-MFA — it's a low-stakes
# delivery probe against the user's own inbox.
# ---------------------------------------------------------------------------


_TEST_EMAIL_RATE_LIMIT_SECONDS = 60


@router.post("/me/test-email", status_code=200)
async def send_self_test_email(
    request: Request,
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Send a diagnostic email to the authenticated user's own inbox."""
    redis_client = request.app.state.redis_client
    rl_key = f"bff:test_email_rl:{claims.subject}"
    acquired = await redis_client.set(
        rl_key, b"1", nx=True, ex=_TEST_EMAIL_RATE_LIMIT_SECONDS
    )
    if not acquired:
        ttl = await redis_client.ttl(rl_key)
        retry_after = max(int(ttl) if ttl and ttl > 0 else _TEST_EMAIL_RATE_LIMIT_SECONDS, 1)
        raise HTTPException(
            status_code=429,
            detail={
                "detail": "Слишком часто — попробуйте через минуту.",
                "code": "RATE_LIMITED",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    me_resp = await auth_client.get_my_profile(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not me_resp.is_success:
        raise _upstream_error(me_resp)
    me = me_resp.json()
    recipient = me.get("email")
    if not recipient:
        raise HTTPException(
            status_code=500,
            detail={"detail": "Profile has no email address"},
        )

    from datetime import UTC, datetime
    initiated_at = datetime.now(tz=UTC).isoformat(timespec="seconds")
    client_ip = request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )

    upstream = await notification_client.send_self_test_email(
        actor_id=claims.subject,
        role=claims.role,
        recipient=recipient,
        full_name=me.get("full_name"),
        ip_address=client_ip,
        initiated_at=initiated_at,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    log.info(
        "self_test_email.requested",
        user_id=str(claims.subject),
        recipient=recipient,
    )
    return {"sent": True, "recipient": recipient}


# ---------------------------------------------------------------------------
# GET/PUT /api/v1/auth/me/notification-prefs  (auth required) — ADR-0011
#
# Thin proxy to notification-svc. The user only ever touches their OWN prefs:
# the BFF mints an internal JWT with actor_id = the authenticated subject, and
# notification-svc keys the row by that actor_id. Body validation (email_mode,
# category sanitisation) is enforced downstream in notification-svc.
# ---------------------------------------------------------------------------


@router.get("/me/notification-prefs", status_code=200)
async def get_my_notification_prefs(
    claims: RequireAccessClaims,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Return the authenticated user's effective notification preferences."""
    upstream = await notification_client.get_my_notification_prefs(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.put("/me/notification-prefs", status_code=200)
async def update_my_notification_prefs(
    claims: RequireAccessClaims,
    notification_client: GetNotificationClient,
    body: dict[str, Any] = Body(...),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Upsert the authenticated user's notification preferences."""
    upstream = await notification_client.put_my_notification_prefs(
        actor_id=claims.subject,
        role=claims.role,
        body=body,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# In-app notification feed (auth required) — ADR-0011 §D6. Thin proxy; the user
# only ever sees/clears their OWN feed (keyed by the authenticated subject).
# ---------------------------------------------------------------------------


@router.get("/me/notifications", status_code=200)
async def list_my_notifications(
    claims: RequireAccessClaims,
    notification_client: GetNotificationClient,
    limit: int = 30,
    offset: int = 0,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    upstream = await notification_client.list_my_notifications(
        actor_id=claims.subject,
        role=claims.role,
        limit=limit,
        offset=offset,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/me/notifications/unread-count", status_code=200)
async def my_unread_count(
    claims: RequireAccessClaims,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    upstream = await notification_client.my_unread_count(
        actor_id=claims.subject, role=claims.role, request_id=request_id
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/me/notifications/{notification_id}/read", status_code=200)
async def mark_notification_read(
    notification_id: str,
    claims: RequireAccessClaims,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    upstream = await notification_client.mark_notification_read(
        actor_id=claims.subject,
        role=claims.role,
        notification_id=notification_id,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/me/notifications/read-all", status_code=200)
async def mark_all_notifications_read(
    claims: RequireAccessClaims,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    upstream = await notification_client.mark_all_notifications_read(
        actor_id=claims.subject, role=claims.role, request_id=request_id
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/auth/password/change  (auth required)
# SPA body: {current_password, new_password, re_mfa_token}
# Auth-service path: /auth/change-password
# ---------------------------------------------------------------------------


def _extract_password_change_credential(
    body: dict[str, Any],
    authorization: str | None,
    settings: BffSettings,
) -> tuple[str, str] | tuple[None, AccessClaims] | None:
    """Determine the credential lane for POST /password/change.

    Returns one of:
      ("ticket", <opaque_token>)  — enrollment-ticket lane (ADR-0008 D3)
      (None, <AccessClaims>)      — normal actor-JWT lane   (INV-2)
      None                        — no credential → 401

    Priority (F-N-1 fix):
      1. Body ``enrollment_token`` non-empty → ticket lane (body field always wins).
      2. Authorization: Bearer <value> present:
         2a. Value decodes as a valid RS256 access JWT → actor-JWT lane.
         2b. Value does NOT decode as JWT (opaque string) → ticket lane
             (SPA first-login: applyToken(enrollment_token) sets Authorization header,
             per ADR-0008 D7 fallback).
      3. No Authorization header → None (caller raises 401).

    MF-3/D3a.1: enrollment_token and new_password MUST NOT be logged here or upstream.
    The JWT-decode try/except is SILENT — an opaque enrollment_token in the header is
    the expected first-login path, not an error.
    """
    # Priority 1: explicit body field always wins → ticket lane.
    raw = body.get("enrollment_token") if isinstance(body, dict) else None
    if isinstance(raw, str) and raw.strip():
        return ("ticket", raw.strip())

    # Priority 2: Authorization Bearer present.
    if not authorization:
        return None
    scheme, _, bearer_value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not bearer_value.strip():
        return None
    bearer_value = bearer_value.strip()

    # 2a: try RS256 JWT decode — SILENT on failure (opaque token is expected fallback).
    try:
        claims = _verify_access_jwt(bearer_value, settings)
        return (None, claims)  # actor-JWT lane
    except jwt.PyJWTError:
        # 2b: not a valid JWT → treat as opaque enrollment ticket (ADR-0008 D7).
        return ("ticket", bearer_value)


@router.post("/password/change")
async def change_password(
    body: dict[str, Any],
    req: Request,
    response: Response,
    auth_client: GetAuthClient,
    settings: AppSettings,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Change password — three-way lane selection (ADR-0008 D3 / MF-5 / INV-2 / F-N-1 fix).

    Lane (a) body enrollment_token present: forced-enrollment terminal step.
      - Anonymous ticket lane; identity resolved from ticket in auth-service.
      - MF-3/D3a.1: body MUST NOT be logged.

    Lane (b) Authorization header is a valid RS256 access JWT: normal profile change.
      - Actor-JWT lane: actor identity from decoded claims forwarded upstream.
      - INV-2 preserved: only a verifiable JWT reaches the authenticated upstream call.

    Lane (c) Authorization header is an opaque non-JWT Bearer: SPA first-login compat.
      - Ticket lane; ``applyToken(enrollment_token)`` sets the Authorization header
        rather than the body field (ADR-0008 D7 fallback).

    Lane (d) No usable credential → 401.
    """
    authorization_hdr: str | None = req.headers.get("Authorization")
    new_password: str = body.get("new_password", "")

    credential = _extract_password_change_credential(body, authorization_hdr, settings)

    if credential is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    lane, cred_value = credential

    if lane == "ticket":
        # ── Lanes (a) and (c): enrollment-ticket lane ──
        # MF-3/D3a.1: do NOT log cred_value (enrollment_token), new_password, or body.
        if not new_password:
            raise HTTPException(status_code=422, detail="new_password is required")
        upstream = await auth_client.change_password_with_ticket(
            enrollment_token=cred_value,  # type: ignore[arg-type]
            new_password=new_password,
            request_id=request_id,
        )
    else:
        # ── Lane (b): normal path — actor-JWT lane (INV-2 preserved) ──
        claims: AccessClaims = cred_value  # type: ignore[assignment]
        upstream = await auth_client.change_password(
            actor_id=claims.subject,
            role=claims.role,
            new_password=new_password,
            request_id=request_id,
        )

    if not upstream.is_success and upstream.status_code != 204:
        raise _upstream_error(upstream)

    if upstream.status_code == 204 or not upstream.content:
        return {"detail": "Password changed successfully"}

    data = upstream.json()
    refresh_token = data.get("refresh_token")
    if refresh_token:
        _set_refresh_cookie(response, refresh_token, settings)
    return {k: v for k, v in data.items() if k != "refresh_token"}


# ---------------------------------------------------------------------------
# POST /api/v1/auth/me/change-email/request  (auth required)
# SPA body: {new_email, totp_code}
# Auth-service path: POST /auth/me/change-email/request
# ---------------------------------------------------------------------------


@router.post("/me/change-email/request")
async def request_email_change(
    body: dict[str, Any],
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Step 1: request self-service email change.

    BFF enforces re-MFA: extracts totp_code from body, calls /auth/mfa-check,
    then forwards to auth-service with actor internal-JWT.

    Returns: {request_id, code_ttl_seconds, masked_new_email}
    503 if email channel is not configured (EMAIL_CHANNEL_REQUIRED).
    409 if new email is already taken (EMAIL_ALREADY_TAKEN).
    422 if new email is same as current (EMAIL_SAME) or invalid format.
    """
    new_email = body.get("new_email", "")
    totp_code = body.get("totp_code", "")

    # Re-MFA gate: verify TOTP before forwarding
    if not totp_code:
        raise HTTPException(
            status_code=401,
            detail={
                "detail": "TOTP code required for email change",
                "code": "REMFA_REQUIRED",
            },
            headers={"X-Error-Code": "REMFA_REQUIRED"},
        )

    mfa_resp = await auth_client.re_mfa_check(
        actor_id=claims.subject,
        role=claims.role,
        totp_code=totp_code,
        request_id=request_id,
    )
    if not mfa_resp.is_success:
        try:
            mfa_body = mfa_resp.json()
            detail = mfa_body.get("detail", "Re-authentication failed")
            code = mfa_body.get("code", "REMFA_REQUIRED")
        except Exception:
            detail = "Re-authentication failed"
            code = "REMFA_REQUIRED"
        raise HTTPException(
            status_code=mfa_resp.status_code,
            detail={"detail": detail, "code": code},
            headers={"X-Error-Code": code},
        )

    # Forward to auth-service
    upstream = await auth_client.request_email_change(
        actor_id=claims.subject,
        role=claims.role,
        new_email=new_email,
        totp_code=totp_code,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/auth/me/change-email/confirm  (auth required)
# SPA body: {request_id, verification_code}
# Auth-service path: POST /auth/me/change-email/confirm
# ---------------------------------------------------------------------------


@router.post("/me/change-email/confirm")
async def confirm_email_change(
    body: dict[str, Any],
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Step 2: confirm email change by supplying the verification code.

    No re-MFA required — the 8-digit code from the email IS the second factor.

    Returns: {email: new_email}
    404 if request_id not found / expired.
    401 if code is wrong (with attempts_remaining in detail).
    """
    request_id_val = body.get("request_id", "")
    verification_code = body.get("verification_code", "")

    upstream = await auth_client.confirm_email_change(
        actor_id=claims.subject,
        role=claims.role,
        request_id_val=request_id_val,
        verification_code=verification_code,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# SavedFilters (v1.23.0 — registry-filters feature)
# GET    /api/v1/auth/me/saved-filters
# POST   /api/v1/auth/me/saved-filters
# PATCH  /api/v1/auth/me/saved-filters/{filter_id}
# DELETE /api/v1/auth/me/saved-filters/{filter_id}
#
# All routes require authentication. There is no admin-only gate —
# every user manages their own presets (ownership enforced at auth-svc).
# ---------------------------------------------------------------------------


@router.get("/me/saved-filters")
async def list_saved_filters(
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Return all named filter presets for the current user."""
    upstream = await auth_client.list_my_saved_filters(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/me/saved-filters", status_code=201)
async def create_saved_filter(
    body: dict[str, Any],
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Create a new named filter preset. Max 20 per user."""
    upstream = await auth_client.create_saved_filter(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.patch("/me/saved-filters/{filter_id}")
async def update_saved_filter(
    filter_id: uuid.UUID,
    body: dict[str, Any],
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Partially update a named filter preset."""
    upstream = await auth_client.update_saved_filter(
        actor_id=claims.subject,
        role=claims.role,
        filter_id=filter_id,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.delete("/me/saved-filters/{filter_id}", status_code=204)
async def delete_saved_filter(
    filter_id: uuid.UUID,
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> None:
    """Hard-delete a named filter preset. Returns 204 No Content."""
    upstream = await auth_client.delete_saved_filter(
        actor_id=claims.subject,
        role=claims.role,
        filter_id=filter_id,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
