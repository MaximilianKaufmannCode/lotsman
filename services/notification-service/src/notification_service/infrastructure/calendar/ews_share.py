# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""EWS calendar folder sharing helpers.

Grants / revokes Reviewer permission on the shared target_mailbox calendar
folder for individual users.  Used when admin adds/removes a user from the
calendar subscription whitelist (ADR-0005 §7).

Design notes
------------
- All functions are **synchronous** (exchangelib has no native async).  Callers
  must wrap them in ``asyncio.to_thread()``.
- Account construction is extracted into ``_build_ews_account()`` and reuses
  exactly the same Configuration/Credentials/DELEGATE flow as
  ``test_channel._send_via_ews_sync`` and ``EwsCalendarDriver._make_account``.
  We do NOT duplicate the Configuration setup.
- Errors are re-raised as ``EwsShareError`` with a sanitised message (no
  credentials, no raw exchangelib repr that might embed the password).
- All functions are idempotent from the caller's perspective:
    - ``grant_calendar_share``: if permission already exists at Reviewer level,
      this is a no-op (we read the permission set first).
    - ``revoke_calendar_share``: if the user is not in the permission set,
      this is a no-op (not an error).
    - ``list_calendar_shares``: pure read, always safe to call.

SECURITY:
  - ews_config is the *decrypted* dict — must NEVER be logged or included in
    exception messages.
  - Error messages include only the EWS error class name + a short human-
    readable description.  No username, no password, no token.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Typed exception (safe — no credentials)
# ---------------------------------------------------------------------------


class EwsShareError(Exception):
    """EWS calendar share operation failed.

    The message is always safe for logging and storage in share_error column —
    it contains only EWS error class names, not credential values.
    """


# ---------------------------------------------------------------------------
# Internal helpers (synchronous — run via asyncio.to_thread)
# ---------------------------------------------------------------------------

_REVIEWER_LEVEL = "Reviewer"


def _build_ews_account(ews_config: dict[str, Any]) -> Any:
    """Build an exchangelib Account for the target_mailbox.

    Uses IMPERSONATION access by default. Permissions API in EWS requires
    the request to come from the mailbox owner identity — DELEGATE access
    silently returns ErrorInternalServerError on permission-set updates
    even when the service-account has Owner rights on the calendar
    folder. With IMPERSONATION the service-account «becomes» the mailbox
    for the duration of the call. Requires the
    `ApplicationImpersonation` RBAC role on the service-account in
    Exchange (one-time PowerShell from IT — see runbook §12).

    Falls back to DELEGATE if the channel config explicitly opts out
    (`ews_access_type=DELEGATE`), e.g. for migration / debug purposes.
    """
    from exchangelib import (  # type: ignore[import-untyped]
        BASIC,
        DELEGATE,
        IMPERSONATION,
        NTLM,
        Account,
        Configuration,
        Credentials,
    )
    from exchangelib.protocol import (  # type: ignore[import-untyped]
        BaseProtocol,
        NoVerifyHTTPAdapter,
    )

    if not ews_config.get("verify_ssl", True):
        BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter

    auth_type = (
        NTLM
        if str(ews_config.get("auth_type", "NTLM")).upper() == "NTLM"
        else BASIC
    )
    creds = Credentials(
        username=str(ews_config["service_account_login"]),
        password=str(ews_config["service_account_password"]),
    )
    cfg = Configuration(
        service_endpoint=str(ews_config["ews_url"]),
        credentials=creds,
        auth_type=auth_type,
    )
    access_type = (
        DELEGATE
        if str(ews_config.get("ews_access_type", "IMPERSONATION")).upper() == "DELEGATE"
        else IMPERSONATION
    )
    return Account(
        primary_smtp_address=str(ews_config["target_mailbox"]),
        config=cfg,
        autodiscover=False,
        access_type=access_type,
    )


def _safe_error(exc: Exception) -> str:
    """Return a safe string representation of an EWS exception.

    We deliberately include only the exception class name, not the exception
    message, because exchangelib may echo credentials in some error paths.
    A short human-readable hint is appended based on the class name.
    """
    cls = type(exc).__name__
    hints: dict[str, str] = {
        "UnauthorizedError": (
            "EWS authentication failed — check service_account_login and password."
        ),
        "ErrorAccessDenied": (
            "EWS permission denied — service account may lack 'FullAccess' or "
            "'ChangePermission' on the target mailbox calendar folder."
        ),
        "ErrorFolderNotFound": "EWS calendar folder not found on target mailbox.",
        "ErrorMailboxStoreUnavailable": (
            "EWS mailbox store is temporarily unavailable — retry later."
        ),
        "ErrorInvalidOperation": (
            "EWS reported an invalid operation — the Exchange policy may block "
            "self-permission-set. Use IT fallback: "
            "Add-MailboxFolderPermission -Identity <mailbox>:\\Calendar "
            "-User <email> -AccessRights Reviewer"
        ),
        "ErrorInternalServerError": (
            "Exchange отверг изменение прав (Internal Server Error). "
            "Чаще всего это означает что service-account не имеет права "
            "ChangePermission на calendar folder target_mailbox. "
            "IT-fallback: PowerShell `Add-MailboxFolderPermission -Identity "
            "<mailbox>:\\Календарь -User <email> -AccessRights Reviewer` (или "
            "`-Identity <mailbox>:\\Calendar` если англ. локаль)."
        ),
    }
    hint = hints.get(cls, f"EWS error: {cls}")
    return hint


# ---------------------------------------------------------------------------
# Public API (synchronous — wrap in asyncio.to_thread from async callers)
# ---------------------------------------------------------------------------


def grant_calendar_share(*, ews_config: dict[str, Any], user_email: str) -> None:
    """Grant Reviewer permission on the target_mailbox calendar folder for user_email.

    Uses exchangelib ``Folder.permission_set`` (read → modify → save).
    The service account must have FullAccess (or ChangePermission) on the
    target_mailbox — the same right used by EwsCalendarDriver for CalendarItem
    create/update, which the existing live test already proved works.

    Idempotent: if user_email already has >= Reviewer permission, this call is a
    no-op and does not raise.

    Raises:
        EwsShareError: on any EWS-side failure (access denied, mailbox offline,
            Exchange policy blocking self-permission-set, etc.).  Message is safe
            for storage in share_error column (no credentials).
        RuntimeError: if exchangelib is not installed.
    """
    try:
        from exchangelib import (  # type: ignore[import-untyped]
            Mailbox,
        )
        # exchangelib 5.x: Calendar folder uses CalendarPermission (NOT
        # generic Permission — that's for mail folders). PermissionSet has
        # a separate `calendar_permissions` field for calendar folders.
        # Verified via inspect against installed 5.6.0.
        from exchangelib.properties import (  # type: ignore[import-untyped]
            CalendarPermission,
            PermissionSet,
            UserId,
        )
    except ImportError as exc:
        raise RuntimeError(
            "exchangelib is not installed — cannot grant EWS calendar share."
        ) from exc

    try:
        account = _build_ews_account(ews_config)
        calendar = account.calendar

        # Read existing permission set.  exchangelib returns a PermissionSet object
        # (iterable of Permission items).  We may need to call .refresh() to load it.
        try:
            perm_set = calendar.permission_set
        except Exception:
            calendar.refresh()
            perm_set = calendar.permission_set

        # Calendar folder uses calendar_permissions (separate from .permissions
        # which is for mail folders). exchangelib may return None for either
        # perm_set or its inner field when no explicit permissions exist yet.
        #
        # IMPORTANT: filter out built-in Exchange entries (Default, Anonymous)
        # AND entries with read_items='TimeOnly' — exchangelib's validator
        # rejects 'TimeOnly' on save (only accepts FullDetails/None) even though
        # Exchange itself returns it. Sending these back would fail with
        # ValueError. They're managed server-side regardless.
        _VALID_READ_ITEMS = {"FullDetails", "None"}
        existing_perms_list: list[Any] = []
        if perm_set is not None and perm_set.calendar_permissions is not None:
            for p in perm_set.calendar_permissions:
                uid = getattr(p, "user_id", None)
                if uid is not None and getattr(uid, "distinguished_user", None):
                    # Skip Default/Anonymous — managed by Exchange
                    continue
                if getattr(p, "read_items", None) not in _VALID_READ_ITEMS:
                    # Skip entries with values exchangelib won't accept on round-trip
                    continue
                existing_perms_list.append(p)

        # Check whether user already has a permission entry (idempotent).
        normalized_email = user_email.strip().lower()
        for perm in existing_perms_list:
            uid = getattr(perm, "user_id", None)
            addr = getattr(uid, "primary_smtp_address", None) if uid else None
            if addr and str(addr).lower() == normalized_email:
                log.info(
                    "ews_share.grant.already_present",
                    user_email=user_email,
                    target_mailbox=ews_config.get("target_mailbox", ""),
                )
                return

        # Build a new CalendarPermission entry at Reviewer level.
        # Critical: when CalendarPermissionLevel="Reviewer" is sent, Exchange
        # also expects matching individual flags. exchangelib's defaults are
        # all-None which serializes as <ReadItems>None</ReadItems> etc — and
        # Exchange rejects that as conflicting with Reviewer (which implies
        # ReadItems=FullDetails). Spell out the Reviewer preset explicitly.
        new_perm = CalendarPermission(
            user_id=UserId(primary_smtp_address=user_email),
            calendar_permission_level=_REVIEWER_LEVEL,
            read_items="FullDetails",
            edit_items="None",
            delete_items="None",
            can_create_items=False,
            can_create_subfolders=False,
            is_folder_owner=False,
            is_folder_visible=True,
            is_folder_contact=False,
        )

        # NOTE on Default/Anonymous: we previously tried re-injecting them
        # into the new permission set (Exchange complains if missing in some
        # versions) but the corp Exchange instance still returns generic
        # ErrorInternalServerError on UpdateFolder, regardless of whether
        # they're present. After extensive SOAP tracing the conclusion is
        # that EWS PermissionSet writes are unreliable on this server even
        # with correct payload — admin should grant via PowerShell
        # `Add-MailboxFolderPermission` and use the «Mark as granted»
        # button (see manual_grant endpoint).

        # Append to existing set and write back. Preserve other (non-calendar)
        # permissions if any.
        existing_perms_list.append(new_perm)
        existing_other = (
            list(perm_set.permissions)
            if perm_set is not None and perm_set.permissions is not None
            else []
        )
        new_perm_set = PermissionSet(
            permissions=existing_other,
            calendar_permissions=existing_perms_list,
        )
        calendar.permission_set = new_perm_set
        # Critical: scope the UpdateFolder to permission_set only.
        # exchangelib's bare .save() will also include DisplayName and
        # FolderClass in <SetFolderField/>, which Exchange flat-out
        # rejects for system folders → generic ErrorInternalServerError
        # with no useful detail. The SOAP request was traced and confirmed.
        calendar.save(update_fields=["permission_set"])

        log.info(
            "ews_share.grant.succeeded",
            user_email=user_email,
            target_mailbox=ews_config.get("target_mailbox", ""),
        )
    except EwsShareError:
        raise
    except Exception as exc:
        safe_msg = _safe_error(exc)
        # TEMP traceback for diagnosis — remove after fix verified
        log.warning(
            "ews_share.grant.failed",
            user_email=user_email,
            error_class=type(exc).__name__,
            safe_msg=safe_msg,
            exc_repr=repr(exc)[:300],
        )
        raise EwsShareError(safe_msg) from exc


def revoke_calendar_share(*, ews_config: dict[str, Any], user_email: str) -> None:
    """Remove user_email from the target_mailbox calendar folder permission set.

    Idempotent: if user_email is not in the permission set, this call is a no-op
    and does not raise.

    Raises:
        EwsShareError: on any EWS-side failure.  Message is safe for storage.
        RuntimeError: if exchangelib is not installed.
    """
    try:
        try:
            from exchangelib.folders.base import (  # type: ignore[import-untyped]
                Permission,
                PermissionSet,
            )
        except ImportError:
            from exchangelib import (  # type: ignore[import-untyped]  # noqa: F401
                Permission,
                PermissionSet,
            )
        _ = Permission  # noqa: F841 — imported for type checking below
    except ImportError as exc:
        raise RuntimeError(
            "exchangelib is not installed — cannot revoke EWS calendar share."
        ) from exc

    try:
        account = _build_ews_account(ews_config)
        calendar = account.calendar

        try:
            perm_set = calendar.permission_set
        except Exception:
            calendar.refresh()
            perm_set = calendar.permission_set

        if perm_set is None or perm_set.calendar_permissions is None:
            # No calendar permissions at all — user_email is definitely not present.
            return

        normalized_email = user_email.strip().lower()
        filtered: list[Any] = []
        found = False
        for perm in perm_set.calendar_permissions:
            uid = getattr(perm, "user_id", None)
            addr = getattr(uid, "primary_smtp_address", None) if uid else None
            if addr and str(addr).lower() == normalized_email:
                found = True
            else:
                filtered.append(perm)

        if not found:
            log.info(
                "ews_share.revoke.not_present",
                user_email=user_email,
                target_mailbox=ews_config.get("target_mailbox", ""),
            )
            return

        existing_other = (
            list(perm_set.permissions) if perm_set.permissions is not None else []
        )
        calendar.permission_set = PermissionSet(
            permissions=existing_other,
            calendar_permissions=filtered,
        )
        # Same scoping as grant — see grant_calendar_share for rationale.
        calendar.save(update_fields=["permission_set"])

        log.info(
            "ews_share.revoke.succeeded",
            user_email=user_email,
            target_mailbox=ews_config.get("target_mailbox", ""),
        )
    except EwsShareError:
        raise
    except Exception as exc:
        safe_msg = _safe_error(exc)
        log.warning(
            "ews_share.revoke.failed",
            user_email=user_email,
            error_class=type(exc).__name__,
            safe_msg=safe_msg,
        )
        raise EwsShareError(safe_msg) from exc


def list_calendar_shares(*, ews_config: dict[str, Any]) -> list[dict[str, str]]:
    """List all current sharing permissions on the target_mailbox calendar folder.

    Returns a list of dicts: [{email, permission_level}, ...].
    Built-in permissions (Default, Anonymous) are included.

    Used for diagnostics and UI status checks.

    Raises:
        EwsShareError: on EWS-side failure.
        RuntimeError: if exchangelib is not installed.
    """
    try:
        pass  # exchangelib import validated in _build_ews_account
    except ImportError as exc:
        raise RuntimeError(
            "exchangelib is not installed — cannot list EWS calendar shares."
        ) from exc

    try:
        account = _build_ews_account(ews_config)
        calendar = account.calendar

        try:
            perm_set = calendar.permission_set
        except Exception:
            calendar.refresh()
            perm_set = calendar.permission_set

        if perm_set is None:
            return []

        result: list[dict[str, str]] = []
        for perm in perm_set.permissions:
            user = getattr(perm, "user", None)
            email = (
                str(getattr(user, "email_address", ""))
                if user is not None
                else ""
            )
            level = str(getattr(perm, "permission_level", ""))
            result.append({"email": email, "permission_level": level})
        return result
    except EwsShareError:
        raise
    except Exception as exc:
        safe_msg = _safe_error(exc)
        log.warning(
            "ews_share.list.failed",
            error_class=type(exc).__name__,
            safe_msg=safe_msg,
        )
        raise EwsShareError(safe_msg) from exc
