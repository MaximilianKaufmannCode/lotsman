# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""BFF registry proxy — /api/v1/{assets,documents,document-types,exports,attachments}/*

Responsibilities:
  - Accept the SPA's request (Bearer JWT in Authorization header).
  - Verify external JWT via require_access_claims.
  - Forward to registry-service with actor-bound internal JWT.
  - Apply role gate at the BFF level (fast 403 before any downstream call).
  - Translate response: pass through status code + body unchanged.
  - Multipart attachment upload: validate Content-Length at proxy level
    (defense-in-depth: registry-service also validates).

Auth rules (enforced here; registry-service enforces them again internally):
  GET  /assets, /documents, /document-types, /exports   — any authenticated user
  POST /assets, /document-types                          — admin only
  PATCH /assets/*, /document-types/*                    — admin only
  POST /documents, PATCH /documents/*                   — editor or admin
  DELETE /documents/*, POST /documents/*/restore         — editor (archive) / admin (restore)
  POST /documents/bulk-archive                           — editor or admin
  POST /documents/*/attachments                          — editor or admin
  DELETE /attachments/*                                  — editor or admin
  POST /exports                                          — any authenticated user
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse

from web_bff.api.deps import (
    GetAuditClient,
    GetAuthClient,
    RequireAccessClaims,
    get_request_id,
)
from web_bff.infrastructure.clients.registry_client import RegistryClient

log = structlog.get_logger(__name__)

router = APIRouter(tags=["registry"])

_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MiB — mirrors registry-service Q1


def get_registry_client(request: Request) -> RegistryClient:
    return request.app.state.registry_client  # type: ignore[no-any-return]


def _upstream_error(upstream_resp: Any) -> HTTPException:
    try:
        detail = upstream_resp.json().get("detail", "Upstream error")
    except Exception:
        detail = "Upstream error"
    return HTTPException(status_code=upstream_resp.status_code, detail=detail)


def _require_role(claims: Any, *allowed_roles: str) -> None:
    if claims.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Forbidden")


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


@router.get("/assets")
async def list_assets(
    request: Request,
    claims: RequireAccessClaims,
    q: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    upstream = await client.list_assets(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        q=q,
        offset=offset,
        limit=limit,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/assets", status_code=status.HTTP_201_CREATED)
async def create_asset(
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "admin")
    client = get_registry_client(request)
    upstream = await client.create_asset(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.patch("/assets/{asset_id}")
async def update_asset(
    asset_id: uuid.UUID,
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "admin")
    client = get_registry_client(request)
    upstream = await client.update_asset(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        asset_id=asset_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.patch("/assets/{asset_id}/archive")
async def archive_asset(
    asset_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "admin")
    client = get_registry_client(request)
    upstream = await client.archive_asset(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        asset_id=asset_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.patch("/assets/{asset_id}/status")
async def patch_asset_status(
    asset_id: uuid.UUID,
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Change the lifecycle status of an asset (active / liquidating / archived).

    Access: admin or editor.
    The registry-service enforces the valid values via domain validation.
    """
    _require_role(claims, "admin", "editor")
    status_val = body.get("status", "")
    if not isinstance(status_val, str) or status_val not in {"active", "liquidating", "archived"}:
        raise HTTPException(
            status_code=422,
            detail="status must be one of: active, liquidating, archived",
        )
    client = get_registry_client(request)
    upstream = await client.patch_asset_status(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        asset_id=asset_id,
        status=status_val,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/assets/{asset_id}/history")
async def get_asset_history(
    asset_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    limit: int = Query(default=50, ge=1, le=200),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    upstream = await client.get_asset_history(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        asset_id=asset_id,
        limit=limit,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# Document types
# ---------------------------------------------------------------------------


@router.get("/document-types")
async def list_document_types(
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    upstream = await client.list_document_types(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/document-types", status_code=status.HTTP_201_CREATED)
async def create_document_type(
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "admin")
    client = get_registry_client(request)
    upstream = await client.create_document_type(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.patch("/document-types/{code}")
async def update_document_type(
    code: str,
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "admin")
    client = get_registry_client(request)
    upstream = await client.update_document_type(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        code=code,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@router.get("/documents/distinct-values")
async def get_distinct_values(
    request: Request,
    claims: RequireAccessClaims,
    field: str = Query(...),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Proxy GET /documents/distinct-values — column-filter autocomplete (v1.24.0).

    Requires viewer role or above. Forwards to registry-service which handles
    Redis caching and schema-driven field validation.
    Must be declared BEFORE /documents/{document_id} to avoid FastAPI routing
    treating 'distinct-values' as a UUID path parameter.
    """
    client = get_registry_client(request)
    upstream = await client.list_distinct_values(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        field=field,
        q=q,
        limit=limit,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/documents")
async def list_documents(
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    # v1.24.10 — Forward query params preserving REPEATED values via multi_items().
    # `dict(request.query_params)` collapsed repeated keys to their LAST value
    # (e.g. ?asset_ids=a&asset_ids=b → {'asset_ids': 'b'}), что молча ломало
    # все multi-select фильтры (asset_ids, type_codes, doc_status, expiry_dates,
    # asset_status, responsible_user_ids). httpx с list[tuple] корректно
    # сериализует обратно в repeated query-params для registry-svc.
    forwarded_params = list(request.query_params.multi_items())
    upstream = await client.list_documents(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        params=forwarded_params,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/documents/bulk-archive")
async def bulk_archive_documents(
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "editor", "admin")
    client = get_registry_client(request)
    upstream = await client.bulk_archive_documents(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def create_document(
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "editor", "admin")
    client = get_registry_client(request)
    upstream = await client.create_document(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/documents/{document_id}")
async def get_document(
    document_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    upstream = await client.get_document(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        document_id=document_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.patch("/documents/{document_id}")
async def patch_document(
    document_id: uuid.UUID,
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "editor", "admin")
    client = get_registry_client(request)
    upstream = await client.patch_document(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        document_id=document_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.delete("/documents/{document_id}")
async def archive_document(
    document_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "editor", "admin")
    client = get_registry_client(request)
    upstream = await client.archive_document(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        document_id=document_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/documents/{document_id}/restore")
async def restore_document(
    document_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "admin")
    client = get_registry_client(request)
    upstream = await client.restore_document(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        document_id=document_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/documents/{document_id}/history")
async def get_document_history(
    document_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    auth_client: GetAuthClient,
    limit: int = Query(default=50, ge=1, le=200),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Document-history view for SPA's "История изменений" tab.

    Two-stage enrichment:
      1. registry-svc resolves asset_id / type_code / attachment.filename
         (registry owns these tables).
      2. web-bff (here) resolves actor_id + before/after UUIDs of
         responsible_user_id to ФИО via auth-service's internal lookup
         (web-bff has INTERNAL_JWT_KEY_AUTH; registry-svc does not, so this
         stage cannot live there without compose changes).

    SPA does the final i18n formatting of dates, field labels and event-type
    verb phrases — backend stays string-template-free per ADR-0001 §4.1.
    """
    client = get_registry_client(request)
    upstream = await client.get_document_history(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        document_id=document_id,
        limit=limit,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    events: list[dict[str, Any]] = upstream.json()

    # Collect all user UUIDs needing name resolution.
    user_ids: set[uuid.UUID] = set()
    for ev in events:
        actor_raw = ev.get("actor_id")
        if actor_raw:
            try:
                user_ids.add(uuid.UUID(str(actor_raw)))
            except (ValueError, TypeError):
                pass
        if ev.get("field") == "responsible_user_id":
            for v in (ev.get("before"), ev.get("after")):
                if v is None:
                    continue
                try:
                    user_ids.add(uuid.UUID(str(v)))
                except (ValueError, TypeError):
                    pass

    # Bulk-lookup ФИО. Graceful: on auth-svc failure, leave actor_name placeholder.
    users: dict[str, dict[str, Any]] = {}
    if user_ids:
        try:
            resp = await auth_client.lookup_users(
                actor_id=claims.subject,
                role=claims.role,
                request_id=request_id,
                user_ids=list(user_ids),
            )
            if resp.is_success:
                users = resp.json()
            else:
                log.warning("history_user_lookup_non_2xx", status=resp.status_code)
        except Exception:
            log.exception("history_user_lookup_failed", count=len(user_ids))

    def _fio(uuid_str: str | None) -> str | None:
        if not uuid_str:
            return None
        u = users.get(uuid_str)
        if u and u.get("full_name"):
            return u["full_name"]
        return f"Удалённый пользователь ({str(uuid_str)[:8]})"

    for ev in events:
        ev["actor_name"] = _fio(str(ev.get("actor_id")) if ev.get("actor_id") else None)
        if ev.get("field") == "responsible_user_id":
            before = ev.get("before")
            after = ev.get("after")
            if before:
                ev["before_display"] = _fio(str(before))
            if after:
                ev["after_display"] = _fio(str(after))

    return events


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@router.post("/documents/{document_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    document_id: uuid.UUID,
    file: UploadFile,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "editor", "admin")

    # Defense-in-depth: validate Content-Length at proxy level
    data = await file.read()
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="Файл превышает допустимый размер 25 МиБ")

    client = get_registry_client(request)
    upstream = await client.upload_attachment(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        document_id=document_id,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        data=data,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/documents/{document_id}/attachments")
async def list_attachments(
    document_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """List attachments for a document (US-9 — DocumentDetailDrawer needs this).

    Read-only: any authenticated role (viewer/editor/admin) can see the list
    for a document they're allowed to view. Auth-svc / registry-svc enforce
    row-level access via standard claims forwarding; no additional gate here.
    """
    client = get_registry_client(request)
    upstream = await client.list_attachments(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        document_id=document_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    # Follow redirects disabled — we re-redirect to the signed URL
    upstream = await client.download_attachment(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        attachment_id=attachment_id,
    )
    if upstream.status_code in (301, 302, 307, 308):
        return RedirectResponse(
            url=upstream.headers.get("location", ""),
            status_code=302,
        )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> None:
    _require_role(claims, "editor", "admin")
    client = get_registry_client(request)
    upstream = await client.delete_attachment(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        attachment_id=attachment_id,
    )
    if not upstream.is_success and upstream.status_code != 204:
        raise _upstream_error(upstream)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


@router.post("/exports", status_code=status.HTTP_202_ACCEPTED)
async def request_export(
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    upstream = await client.request_export(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# SPA hits /exports/xlsx (legacy path) — alias to /exports.
@router.post("/exports/xlsx", status_code=status.HTTP_202_ACCEPTED)
async def request_export_xlsx(
    body: dict[str, Any],
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """SPA-compat alias for /exports.

    `web/src/features/registry/api.ts::requestExportJob` POSTs to
    `/api/v1/exports/xlsx`, while the canonical BFF and registry-svc
    paths are `/exports`. Identical body shape and behaviour.
    """
    client = get_registry_client(request)
    upstream = await client.request_export(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# Audit-events query (used by SPA's getDocumentHistory / getAssetHistory).
# SPA hits `/api/v1/events?entity_type=…&entity_id=…&limit=…`; the canonical
# audit-svc path is `/api/v1/audit/events`. This proxy bridges them.
# ---------------------------------------------------------------------------


@router.get("/events")
async def list_events(
    request: Request,
    claims: RequireAccessClaims,
    audit_client: GetAuditClient,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Proxy audit-events query — forwards to audit-svc /api/v1/audit/events.

    SPA contract: `getDocumentHistory(documentId)` → GET /events?
    entity_type=document&entity_id=<uuid>&limit=50  (analogously for asset).
    Any authenticated user can query: row-level access is enforced upstream
    (audit-svc rejects unauthenticated calls; entity-level visibility is
    governed by the same boundary as viewing the document itself, which
    requires being authenticated to the registry).
    """
    params: dict[str, Any] = {"limit": limit}
    if entity_type:
        params["entity_type"] = entity_type
    if entity_id:
        params["entity_id"] = entity_id
    if event_type:
        params["event_type"] = event_type
    if actor:
        params["actor"] = actor
    if from_:
        params["from"] = from_.isoformat()
    if to:
        params["to"] = to.isoformat()

    upstream = await audit_client.get(
        "/api/v1/audit/events",
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        params=params,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/exports/{job_id}")
async def get_export_job(
    job_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    upstream = await client.get_export_job(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        job_id=job_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/exports/{job_id}/download")
async def download_export(
    job_id: uuid.UUID,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    client = get_registry_client(request)
    upstream = await client.download_export(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        job_id=job_id,
    )
    if upstream.status_code in (301, 302, 307, 308):
        return RedirectResponse(
            url=upstream.headers.get("location", ""),
            status_code=302,
        )
    if upstream.status_code == 410:
        raise HTTPException(
            status_code=410,
            detail="Файл экспорта истёк. Создайте новый экспорт.",
        )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ─── /api/v1/imports/xlsx ───────────────────────────────────────────────


@router.post("/imports/xlsx")
async def import_xlsx_proxy(
    file: UploadFile,
    request: Request,
    claims: RequireAccessClaims,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Proxy bulk Excel registry import to registry-svc (admin-only)."""
    _require_role(claims, "admin")
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (>25 MiB)")

    client = get_registry_client(request)
    upstream = await client.import_xlsx(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        filename=file.filename or "import.xlsx",
        data=data,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# Tenant preferences (column order)
# Read: any authenticated user. Write: admin only (RBAC).
# ---------------------------------------------------------------------------


@router.get("/preferences/column-order")
async def get_column_order(
    claims: RequireAccessClaims,
    registry_client: RegistryClient = Depends(get_registry_client),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    upstream = await registry_client.get_column_order(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.put("/admin/preferences/column-order")
async def put_column_order(
    body: dict[str, Any],
    claims: RequireAccessClaims,
    registry_client: RegistryClient = Depends(get_registry_client),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "admin")
    order = body.get("order")
    if not isinstance(order, list):
        raise HTTPException(status_code=400, detail="`order` must be a list of column ids")
    pinned_raw = body.get("pinned_column_id")
    pinned: str | None = (
        str(pinned_raw) if isinstance(pinned_raw, str) and pinned_raw.strip() else None
    )
    upstream = await registry_client.update_column_order(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        order=[str(x) for x in order],
        pinned_column_id=pinned,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/preferences/column-labels")
async def get_column_labels(
    claims: RequireAccessClaims,
    registry_client: RegistryClient = Depends(get_registry_client),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    upstream = await registry_client.get_column_labels(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.put("/admin/preferences/column-labels")
async def put_column_labels(
    body: dict[str, Any],
    claims: RequireAccessClaims,
    registry_client: RegistryClient = Depends(get_registry_client),
    request_id: str | None = Depends(get_request_id),
) -> Any:
    _require_role(claims, "admin")
    labels = body.get("labels")
    if not isinstance(labels, dict):
        raise HTTPException(status_code=400, detail="`labels` must be an object")
    cleaned: dict[str, str] = {}
    for k, v in labels.items():
        if isinstance(k, str) and isinstance(v, str):
            cleaned[k] = v
    upstream = await registry_client.update_column_labels(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        labels=cleaned,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()
