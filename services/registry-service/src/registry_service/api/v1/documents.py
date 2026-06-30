# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Document CRUD endpoints.

GET  /documents                         — list + filter + sort (US-1, US-2, US-3)
GET  /documents/distinct-values         — top-N distinct values for a column (v1.24.0)
POST /documents                         — create (US-5)
GET  /documents/{id}                    — detail (US-8)
PATCH /documents/{id}                   — inline edit single field (US-4)
DELETE /documents/{id}                  — soft-delete/archive (US-6)
POST /documents/{id}/restore            — restore (US-7, admin-only)
POST /documents/bulk-archive            — bulk archive (US-23)
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status

from registry_service.api.deps import (
    CurrentActor,
    DbSession,
    Pagination,
    RequireAdmin,
    RequireEditor,
    get_clock,
    get_request_id,
)
from registry_service.api.schemas import (
    AttachmentResponse,
    BulkArchiveRequest,
    BulkArchiveResponse,
    DistinctValueItemResponse,
    DistinctValuesResponse,
    DocumentCreateRequest,
    DocumentPatchRequest,
    DocumentResponse,
)
from registry_service.application.dto import (
    BulkArchiveCommand,
    CreateDocumentCommand,
    ListDistinctValuesQuery,
    ListDocumentsQuery,
    PatchDocumentCommand,
)
from registry_service.application.use_cases.archive_document import ArchiveDocument
from registry_service.application.use_cases.bulk_archive_documents import BulkArchiveDocuments
from registry_service.application.use_cases.create_document import CreateDocument
from registry_service.application.use_cases.get_document_detail import GetDocumentDetail
from registry_service.application.use_cases.inline_edit_document import InlineEditDocument
from registry_service.application.use_cases.list_distinct_values import ListDistinctValues
from registry_service.application.use_cases.list_documents import ListDocuments
from registry_service.application.use_cases.restore_document import RestoreDocument
from registry_service.domain.errors import DateFieldDistinctNotSupported, UnknownDistinctField
from registry_service.infrastructure.db.repositories import (
    SqlAssetRepository,
    SqlAttachmentRepository,
    SqlDocumentRepository,
    SqlDocumentTypeRepository,
    SqlEventOutbox,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# ---------------------------------------------------------------------------
# Custom-field key validation helpers
# ---------------------------------------------------------------------------

# Keys must be lowercase snake_case identifiers to avoid injection risks.
_CF_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Cache key for per-request valid CF keys set (stored in request.state).
_CF_KEYS_STATE_ATTR = "_valid_cf_keys"

# Redis cache TTL (seconds) for distinct-values results.
_DISTINCT_VALUES_CACHE_TTL = 300  # 5 minutes


async def _load_valid_cf_keys(session: Any, request: Request) -> frozenset[str]:
    """Fetch all valid custom-field keys from document_types once per request.

    Result is cached in request.state so multiple cf_* params in one request
    only trigger a single SQL query.

    Uses a raw SQL query over all document types to extract keys from the
    JSONB custom_field_schema array (each element has a 'key' property).
    """
    cached: frozenset[str] | None = getattr(request.state, _CF_KEYS_STATE_ATTR, None)
    if cached is not None:
        return cached

    from sqlalchemy import text

    result = await session.execute(
        text(
            "SELECT DISTINCT (elem->>'key') "
            "FROM registry.document_types, "
            "jsonb_array_elements(custom_field_schema) AS elem "
            "WHERE elem->>'key' IS NOT NULL"
        )
    )
    keys: frozenset[str] = frozenset(str(row[0]) for row in result.all() if row[0])
    request.state._valid_cf_keys = keys  # noqa: SLF001
    return keys


_CF_RANGE_SUFFIX_RE = re.compile(r"^(?P<key>[a-z][a-z0-9_]{0,63})_(?P<suffix>from|to|is_null)$")


async def _parse_cf_params(
    raw_params: dict[str, Any],
    *,
    session: Any,
    request: Request,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Extract cf_<key>=<value> AND cf_<key>_(from|to|is_null) params.

    Schema-driven validation (v1.24.0+):
      - Keys are checked against regex ^[a-z][a-z0-9_]{0,63}$.
      - Keys are then checked against valid_cf_keys loaded from DB (once per request).
      - Unknown cf_* keys are silently dropped with a structured INFO log.

    Returns:
      (equality_dict, ranges_dict) where:
        equality_dict[key] = value          — cf_<key>=<value>  (containment)
        ranges_dict[key]   = {from?, to?, is_null?}  — cf_<key>_from/_to/_is_null
                              (v1.24.17 — schema-driven date range filtering)

    No cf_* params in query string → returns ({}, {}) without hitting the DB.
    """
    cf_candidates = {k: v for k, v in raw_params.items() if k.startswith("cf_")}
    if not cf_candidates:
        return {}, {}

    valid_cf_keys = await _load_valid_cf_keys(session, request)
    equality: dict[str, str] = {}
    ranges: dict[str, dict[str, Any]] = {}

    for k, v in cf_candidates.items():
        raw_field = k[3:]  # strip 'cf_' prefix
        # First check for range-suffix forms.
        m = _CF_RANGE_SUFFIX_RE.match(raw_field)
        if m:
            field_key = m.group("key")
            suffix = m.group("suffix")
            if field_key not in valid_cf_keys:
                log.info(
                    "documents.list.unknown_cf_key_ignored",
                    key=field_key,
                    reason="not_in_schema",
                    request_id=get_request_id(request),
                )
                continue
            if not v or not isinstance(v, str):
                continue
            bucket = ranges.setdefault(field_key, {})
            if suffix == "is_null":
                bucket["is_null"] = v.lower() == "true"
            else:
                bucket[suffix] = v
            continue

        # Plain equality form (legacy single value containment).
        if not _CF_KEY_RE.match(raw_field):
            log.info(
                "documents.list.unknown_cf_key_ignored",
                key=raw_field,
                reason="regex_mismatch",
                request_id=get_request_id(request),
            )
            continue
        if raw_field not in valid_cf_keys:
            log.info(
                "documents.list.unknown_cf_key_ignored",
                key=raw_field,
                reason="not_in_schema",
                request_id=get_request_id(request),
            )
            continue
        if v and isinstance(v, str):
            equality[raw_field] = v

    return equality, ranges


# ---------------------------------------------------------------------------
# GET /documents
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    pagination: Pagination,
    # --- legacy single-value params (kept for backward compat) ---
    q: str | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    type_code: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    dir: str | None = Query(default=None, pattern="^(asc|desc)$"),
    # v1.25.5 — urgency status filter: accept repeated `?status=soon&status=overdue`
    # for multi-select. FastAPI parses repeated query params into list[str]
    # automatically; legacy single-value `?status=soon` still works (parses
    # as ["soon"]). Empty list ⇒ filter not applied.
    status: list[str] = Query(default=[]),
    include_archived: bool = Query(default=False),
    # --- new multi-value params ---
    asset_ids: list[uuid.UUID] = Query(default=[]),
    type_codes: list[str] = Query(default=[]),
    responsible_user_ids: list[uuid.UUID] = Query(default=[]),
    responsible_is_null: bool | None = Query(default=None),
    expiry_from: date | None = Query(default=None),
    expiry_to: date | None = Query(default=None),
    expiry_is_null: bool | None = Query(default=None, alias="expiry_null"),
    updated_from: datetime | None = Query(default=None),
    updated_to: datetime | None = Query(default=None),
    doc_status: list[str] = Query(default=[]),
    asset_status: list[str] = Query(default=[]),
    inn: str | None = Query(default=None),
    # v1.25.6 — column-funnel «— Не задано» for № документа.
    number_is_null: bool | None = Query(default=None),
    # v1.24.9 — фильтр по конкретным датам (multi-select из воронки колонки
    # «Действ. до»). Принимает ISO-даты или сентинел __NULL__ для бессрочных.
    expiry_dates: list[str] = Query(default=[]),
) -> list[DocumentResponse]:
    # Parse cf_* custom-field params from raw query string (schema-driven, v1.24.0+).
    # Equality form: cf_<key>=value
    # Range form (v1.24.17): cf_<key>_from=date / cf_<key>_to=date / cf_<key>_is_null=true
    custom_fields, custom_field_ranges = await _parse_cf_params(
        dict(request.query_params),
        session=session,
        request=request,
    )

    # v1.24.1 — accept `asset_activity` as legacy alias for `asset_status`.
    # The frontend shipped in v1.23.0 / v1.24.0 sends `asset_activity` in the URL;
    # backend canonical name is `asset_status` (matches DB column registry.assets.status).
    # Union both into the canonical list so neither client breaks.
    if not asset_status:
        legacy_vals = request.query_params.getlist("asset_activity")
        if legacy_vals:
            # Support both CSV and repeated forms (?asset_activity=a,b and ?asset_activity=a&asset_activity=b)
            asset_status = [
                v.strip()
                for raw in legacy_vals
                for v in (raw.split(",") if "," in raw else [raw])
                if v.strip()
            ]

    clock = get_clock()
    repo = SqlDocumentRepository(session)
    use_case = ListDocuments(repo=repo, clock=clock)
    dtos = await use_case.execute(
        query=ListDocumentsQuery(
            asset_id=asset_id,
            type_code=type_code,
            asset_ids=asset_ids,
            type_codes=type_codes,
            responsible_user_ids=responsible_user_ids,
            responsible_is_null=responsible_is_null,
            expiry_from=expiry_from,
            expiry_to=expiry_to,
            expiry_is_null=expiry_is_null,
            updated_from=updated_from,
            updated_to=updated_to,
            doc_status=doc_status,
            asset_status=asset_status,
            inn=inn,
            number_is_null=number_is_null,
            expiry_dates=expiry_dates,
            custom_fields=custom_fields,
            custom_field_ranges=custom_field_ranges,
            q=q,
            sort=sort,
            dir=dir,
            status=status,
            offset=pagination.offset,
            limit=pagination.limit,
            include_archived=include_archived,
        )
    )
    # Resolve asset_name + type_display_name in batch (avoid N+1)
    from sqlalchemy import select

    from registry_service.db.models import Asset as AssetModel
    from registry_service.db.models import DocumentType as DocumentTypeModel

    asset_ids_set = {d.asset_id for d in dtos}
    type_codes_set = {d.type_code for d in dtos}
    asset_map: dict[uuid.UUID, str] = {}
    type_map: dict[str, str] = {}
    if asset_ids_set:
        asset_rows = (
            await session.execute(
                select(AssetModel.id, AssetModel.name).where(
                    AssetModel.id.in_(asset_ids_set)
                )
            )
        ).all()
        asset_map = {r[0]: r[1] for r in asset_rows}
    if type_codes_set:
        type_rows = (
            await session.execute(
                select(DocumentTypeModel.code, DocumentTypeModel.display_name).where(
                    DocumentTypeModel.code.in_(type_codes_set)
                )
            )
        ).all()
        type_map = {r[0]: r[1] for r in type_rows}

    out: list[DocumentResponse] = []
    for dto in dtos:
        d = vars(dto).copy()
        d["asset_name"] = asset_map.get(dto.asset_id)
        d["type_display_name"] = type_map.get(dto.type_code)
        d["responsible_user_name"] = None  # auth-svc lookup deferred
        out.append(DocumentResponse(**d))
    return out


# ---------------------------------------------------------------------------
# GET /documents/distinct-values  (must come BEFORE /{id} to avoid routing conflict)
# ---------------------------------------------------------------------------


@router.get("/distinct-values", response_model=DistinctValuesResponse)
async def get_distinct_values(
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    field: str = Query(..., description="Field name: system (number, asset_name, type_code) or cf_<key>"),
    q: str | None = Query(default=None, description="Optional case-insensitive substring filter"),
    limit: int = Query(default=100, ge=1, le=500, description="Max distinct values to return (default 100, max 500)"),
) -> DistinctValuesResponse:
    """Return top-N distinct values for a filterable column.

    Used by FE column-filter autocomplete (US-3).
    Results are cached in Redis with TTL 300s. On Redis failure, falls back to DB.
    """
    # --- Attempt Redis cache lookup ---
    cache_key = _distinct_values_cache_key(field=field, q=q, limit=limit)
    cached_payload = await _redis_get(request, cache_key)
    if cached_payload is not None:
        return DistinctValuesResponse.model_validate_json(cached_payload)

    # --- Execute use case ---
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)
    try:
        result = await use_case.execute(
            query=ListDistinctValuesQuery(field=field, q=q, limit=limit)
        )
    except DateFieldDistinctNotSupported as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except UnknownDistinctField as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    response = DistinctValuesResponse(
        field=result.field,
        values=[
            DistinctValueItemResponse(value=item.value, count=item.count)
            for item in result.values
        ],
        total_distinct=result.total_distinct,
        truncated=result.truncated,
        null_count=result.null_count,
    )

    # --- Store in Redis (best-effort — never fail the response on cache write error) ---
    await _redis_set(request, cache_key, response.model_dump_json(), ttl=_DISTINCT_VALUES_CACHE_TTL)

    return response


# ---------------------------------------------------------------------------
# Redis cache helpers (best-effort: fallback to DB on any error)
# ---------------------------------------------------------------------------


def _distinct_values_cache_key(*, field: str, q: str | None, limit: int) -> str:
    """Deterministic Redis key for a distinct-values query.

    Format: dv:<field>:<sha256_of_q_or_empty>:<limit>
    q is lowercased and hashed so special characters don't break key structure.
    field is already validated/whitelisted before reaching here.
    """
    q_normalized = (q or "").lower()
    q_hash = hashlib.sha256(q_normalized.encode()).hexdigest()[:12]
    return f"dv:{field}:{q_hash}:{limit}"


async def _redis_get(request: Request, key: str) -> bytes | None:
    """Attempt to GET a value from Redis. Returns None on any failure."""
    try:
        import redis.asyncio as aioredis

        settings = request.app.state.settings
        async with aioredis.from_url(settings.redis_url) as r:  # type: ignore[no-untyped-call]
            return await r.get(key)  # type: ignore[no-any-return]
    except Exception:
        log.debug("distinct_values_redis_get_failed", key=key)
        return None


async def _redis_set(request: Request, key: str, value: str, *, ttl: int) -> None:
    """Attempt to SET a value in Redis. Silently ignores any failure."""
    try:
        import redis.asyncio as aioredis

        settings = request.app.state.settings
        async with aioredis.from_url(settings.redis_url) as r:  # type: ignore[no-untyped-call]
            await r.set(key, value, ex=ttl)
    except Exception:
        log.debug("distinct_values_redis_set_failed", key=key)


# ---------------------------------------------------------------------------
# POST /documents/bulk-archive  (must come BEFORE /{id} to avoid routing conflict)
# ---------------------------------------------------------------------------


@router.post("/bulk-archive", response_model=BulkArchiveResponse)
async def bulk_archive_documents(
    body: BulkArchiveRequest,
    session: DbSession,
    editor: RequireEditor,
    request: Request,
) -> BulkArchiveResponse:
    async with session.begin():
        repo = SqlDocumentRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = BulkArchiveDocuments(repo=repo, outbox=outbox, clock=clock)
        result = await use_case.execute(
            cmd=BulkArchiveCommand(
                ids=body.ids,
                actor_id=editor.actor_id,
                request_id=get_request_id(request),
            )
        )
    return BulkArchiveResponse(archived=result.archived, skipped=result.skipped)


# ---------------------------------------------------------------------------
# POST /documents
# ---------------------------------------------------------------------------


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    body: DocumentCreateRequest,
    session: DbSession,
    editor: RequireEditor,
    request: Request,
) -> DocumentResponse:
    async with session.begin():
        doc_repo = SqlDocumentRepository(session)
        asset_repo = SqlAssetRepository(session)
        type_repo = SqlDocumentTypeRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = CreateDocument(
            doc_repo=doc_repo,
            asset_repo=asset_repo,
            type_repo=type_repo,
            outbox=outbox,
            clock=clock,
        )
        dto = await use_case.execute(
            cmd=CreateDocumentCommand(
                asset_id=body.asset_id,
                type_code=body.type_code,
                number=body.number,
                issue_date=body.issue_date,
                expiry_date=body.expiry_date,
                responsible_user_id=body.responsible_user_id,
                notes=body.notes,
                actor_id=editor.actor_id,
                request_id=get_request_id(request),
                custom_field_values=body.custom_field_values,
            )
        )
    return DocumentResponse(**vars(dto))


# ---------------------------------------------------------------------------
# GET /documents/{document_id}
# ---------------------------------------------------------------------------


@router.get("/{document_id}", response_model=dict[str, Any])
async def get_document(
    document_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
) -> dict[str, Any]:
    doc_repo = SqlDocumentRepository(session)
    att_repo = SqlAttachmentRepository(session)
    clock = get_clock()
    use_case = GetDocumentDetail(doc_repo=doc_repo, attachment_repo=att_repo, clock=clock)
    doc_dto, att_dtos = await use_case.execute(document_id=document_id)

    # Resolve asset_name + type_display_name (parity with the list endpoint) so
    # every consumer of the detail endpoint — notifications, calendar sync, the
    # SPA detail view — can show the company and the human document type without
    # a second call. Both are optional fields on DocumentResponse (default None),
    # so this only fills them in; it does not change the response contract.
    from sqlalchemy import select as _select

    from registry_service.db.models import Asset as _AssetModel
    from registry_service.db.models import DocumentType as _DocumentTypeModel

    d = vars(doc_dto).copy()
    d["asset_name"] = (
        await session.execute(
            _select(_AssetModel.name).where(_AssetModel.id == doc_dto.asset_id)
        )
    ).scalar_one_or_none()
    d["type_display_name"] = (
        await session.execute(
            _select(_DocumentTypeModel.display_name).where(
                _DocumentTypeModel.code == doc_dto.type_code
            )
        )
    ).scalar_one_or_none()
    return {
        "document": DocumentResponse(**d).model_dump(),
        "attachments": [AttachmentResponse(**vars(a)).model_dump() for a in att_dtos],
    }


# ---------------------------------------------------------------------------
# PATCH /documents/{document_id}
# ---------------------------------------------------------------------------


@router.patch("/{document_id}", response_model=DocumentResponse)
async def patch_document(
    document_id: uuid.UUID,
    body: DocumentPatchRequest,
    session: DbSession,
    editor: RequireEditor,
    request: Request,
) -> DocumentResponse:
    """Apply a partial-object patch to a document.

    SPA sends ``{number?, issue_date?, expiry_date?, responsible_user_id?, notes?}``
    with only the fields the user changed. We iterate over ``model_fields_set``
    and invoke ``InlineEditDocument`` once per field so every change emits its
    own ``DocumentUpdated`` audit event (one event per field — preserves US-4
    audit-granularity). All updates run inside a single transaction — partial
    failure rolls the whole batch back.
    """
    # v1.25.0 — wire DocumentTypeRepository so type_code change can prune
    # orphan custom_field_values against the new type's schema.
    from registry_service.infrastructure.db.repositories import (
        SqlDocumentTypeRepository,
    )

    async with session.begin():
        repo = SqlDocumentRepository(session)
        outbox = SqlEventOutbox(session)
        type_repo = SqlDocumentTypeRepository(session)
        clock = get_clock()
        use_case = InlineEditDocument(
            repo=repo, outbox=outbox, clock=clock, type_repo=type_repo
        )

        # v1.25.0 — Iteration order matters: apply type_code BEFORE
        # custom_field_values so user-supplied cf values are validated against
        # the NEW type's schema. asset_id / number / dates can run in any order.
        _ORDER_PRIORITY = {"type_code": 0, "asset_id": 1, "custom_field_values": 99}
        ordered_fields = sorted(
            body.model_fields_set,
            key=lambda f: _ORDER_PRIORITY.get(f, 50),
        )

        dto = None
        for field_name in ordered_fields:
            dto = await use_case.execute(
                cmd=PatchDocumentCommand(
                    document_id=document_id,
                    field=field_name,
                    value=getattr(body, field_name),
                    actor_id=editor.actor_id,
                    request_id=get_request_id(request),
                )
            )
        assert dto is not None  # _at_least_one_field validator guarantees this
    return DocumentResponse(**vars(dto))


# ---------------------------------------------------------------------------
# DELETE /documents/{document_id}  (soft-delete / archive)
# ---------------------------------------------------------------------------


@router.delete("/{document_id}", status_code=status.HTTP_200_OK)
async def archive_document(
    document_id: uuid.UUID,
    session: DbSession,
    editor: RequireEditor,
    request: Request,
) -> dict[str, str]:
    async with session.begin():
        repo = SqlDocumentRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = ArchiveDocument(repo=repo, outbox=outbox, clock=clock)
        await use_case.execute(
            document_id=document_id,
            actor_id=editor.actor_id,
            request_id=get_request_id(request),
        )
    return {"detail": "Document archived"}


# ---------------------------------------------------------------------------
# POST /documents/{document_id}/restore  (admin-only, US-7)
# ---------------------------------------------------------------------------


@router.post("/{document_id}/restore", status_code=status.HTTP_200_OK)
async def restore_document(
    document_id: uuid.UUID,
    session: DbSession,
    admin: RequireAdmin,
    request: Request,
) -> dict[str, str]:
    async with session.begin():
        repo = SqlDocumentRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = RestoreDocument(repo=repo, outbox=outbox, clock=clock)
        await use_case.execute(
            document_id=document_id,
            actor_id=admin.actor_id,
            request_id=get_request_id(request),
        )
    return {"detail": "Document restored"}
