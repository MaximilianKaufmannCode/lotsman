# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Two-step xlsx import — Step 2: Confirm.

Loads the import session from Redis, applies user decisions for unknown columns
(create_new / map_to_existing / rename / skip), then bulk-inserts documents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from registry_service.application.dto import (
    ImportConfirmCommand,
    ImportConfirmDTO,
    ImportDecision,
    ImportRowError,
    UpdateCustomFieldSchemaCommand,
)
from registry_service.application.import_session_codec import loads_session
from registry_service.application.ports import (
    AssetRepository,
    Clock,
    DocumentRepository,
    DocumentTypeRepository,
    EventOutbox,
)
from registry_service.application.use_cases.update_custom_field_schema import (
    UpdateCustomFieldSchema,
)
from registry_service.domain.custom_fields import (
    CustomField,
    CustomFieldValidationError,
    FieldType,
    validate_values_against_schema,
)
from registry_service.domain.entities import Document
from registry_service.domain.errors import (
    ImportSessionExpiredError,
    ImportSessionNotFoundError,
    RequiredFieldMissingError,
)
from registry_service.domain.events import ImportCompleted


def _coerce_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


@dataclass(slots=True)
class ImportXlsxConfirm:
    """Use case: apply import decisions and insert documents."""

    doc_repo: DocumentRepository
    asset_repo: AssetRepository
    type_repo: DocumentTypeRepository
    outbox: EventOutbox
    clock: Clock
    redis_url: str

    async def execute(self, *, cmd: ImportConfirmCommand) -> ImportConfirmDTO:
        # Load session from Redis
        session_data = await self._load_session(cmd.import_session_id)

        headers: list[tuple[int, str]] = session_data["headers"]
        all_rows: list[tuple[Any, ...]] = session_data["rows"]
        known_col_map: list[tuple[str, str]] = session_data["known_columns"]
        unknown_headers: list[str] = session_data["unknown_headers"]

        # Validate decisions: each unknown header must have exactly one decision
        decisions_by_header: dict[str, ImportDecision] = {d.header: d for d in cmd.decisions}
        for uh in unknown_headers:
            if uh not in decisions_by_header:
                raise RequiredFieldMissingError(f"Missing decision for unknown header '{uh}'")
        for d in cmd.decisions:
            if d.header not in {h for h in unknown_headers}:
                raise RequiredFieldMissingError(
                    f"Decision for header '{d.header}' is not in the unknown columns list"
                )

        # Build column → target mapping
        # col_mapping: col_idx → ("standard", field) | ("custom", type, key) | ("skip",)
        col_mapping: dict[int, tuple[str, ...]] = {}

        # Known columns
        header_to_idx: dict[str, int] = {raw: idx for idx, raw in headers}
        for raw_header, matched_to in known_col_map:
            idx = header_to_idx.get(raw_header)
            if idx is None:
                continue
            if matched_to.startswith("custom:"):
                parts = matched_to.split(":", 2)
                col_mapping[idx] = ("custom", parts[1], parts[2])
            else:
                col_mapping[idx] = ("standard", matched_to)

        # Pre-load all document types and compute the set of types actually
        # present in the imported rows. Both `apply_to_all_types` decisions
        # and the auto-extend pass below need this BEFORE the decision loop.
        all_types = await self.type_repo.list_all()
        type_by_code: dict[str, Any] = {dt.code.casefold(): dt for dt in all_types}
        type_by_display: dict[str, Any] = {
            dt.display_name.casefold(): dt for dt in all_types
        }

        type_code_col_idx: int | None = None
        for raw_header, matched_to in known_col_map:
            if matched_to == "type_code":
                type_code_col_idx = header_to_idx.get(raw_header)
                break
        present_type_codes: list[str] = []
        if type_code_col_idx is not None:
            seen_codes: set[str] = set()
            for row in all_rows:
                if type_code_col_idx >= len(row):
                    continue
                v = row[type_code_col_idx]
                if _is_blank(v):
                    continue
                key = str(v).strip().casefold()
                dt = type_by_code.get(key) or type_by_display.get(key)
                if dt is not None and dt.code not in seen_codes:
                    seen_codes.add(dt.code)
                    present_type_codes.append(dt.code)

        # Apply decisions for unknown columns
        fields_added: list[dict[str, str]] = []
        schema_update_cache: UpdateCustomFieldSchema = UpdateCustomFieldSchema(
            repo=self.type_repo,
            outbox=self.outbox,
            clock=self.clock,
        )

        # Reject if two decisions try to create the same field key — would
        # silently overwrite each other otherwise (regardless of which type
        # is targeted, the column→field mapping is global per import).
        seen_keys: set[str] = set()
        for d in cmd.decisions:
            if d.action in ("create_new", "rename") and d.new_key:
                if d.new_key in seen_keys:
                    raise RequiredFieldMissingError(
                        f"Несколько колонок создают одинаковое поле '{d.new_key}'. "
                        "Задайте разные ключи."
                    )
                seen_keys.add(d.new_key)

        for decision in cmd.decisions:
            idx = header_to_idx.get(decision.header)
            if idx is None:
                continue

            if decision.action == "skip":
                col_mapping[idx] = ("skip",)
                continue

            if decision.action in ("create_new", "rename"):
                new_key = decision.new_key
                field_type_str = decision.field_type or "text"
                display_name = decision.display_name or decision.header

                if not new_key:
                    raise RequiredFieldMissingError(
                        f"Decision '{decision.action}' for '{decision.header}' "
                        "requires new_key"
                    )

                # Determine target types: either the user's single pick OR
                # every type that actually appears in the imported rows.
                if decision.apply_to_all_types:
                    target_types_list = list(present_type_codes)
                    if not target_types_list:
                        raise RequiredFieldMissingError(
                            f"apply_to_all_types is set for '{decision.header}' but "
                            "no recognised document types were found in the imported rows"
                        )
                else:
                    if not decision.target_type:
                        raise RequiredFieldMissingError(
                            f"Decision '{decision.action}' for '{decision.header}' "
                            "requires target_type when apply_to_all_types=false"
                        )
                    target_types_list = [decision.target_type]

                new_field = CustomField(
                    key=new_key,
                    display_name=display_name,
                    type=FieldType(field_type_str),
                    required=False,
                    options=None,
                )

                for tt in target_types_list:
                    dt = await self.type_repo.get_by_code(tt)
                    if dt is None:
                        raise RequiredFieldMissingError(
                            f"Target document type '{tt}' not found"
                        )
                    new_schema = [f for f in dt.custom_field_schema if f.key != new_key]
                    new_schema.append(new_field)
                    await schema_update_cache.execute(
                        cmd=UpdateCustomFieldSchemaCommand(
                            type_code=tt,
                            schema=new_schema,
                            actor_id=cmd.actor_id,
                            request_id=cmd.request_id,
                        )
                    )
                    fields_added.append({"type_code": tt, "field_key": new_key})

                # `custom_all` mapping = "store under new_key on whichever type
                # the row resolves to". Otherwise stick to the single-type form
                # so unrelated rows don't pick up the value.
                if decision.apply_to_all_types:
                    col_mapping[idx] = ("custom_all", new_key)
                else:
                    col_mapping[idx] = ("custom", target_types_list[0], new_key)

            elif decision.action == "map_to_existing":
                target_type = decision.target_type
                mapped_field = decision.mapped_to_field
                if not target_type or not mapped_field:
                    raise RequiredFieldMissingError(
                        f"Decision 'map_to_existing' for '{decision.header}' "
                        "requires target_type and mapped_to_field"
                    )
                col_mapping[idx] = ("custom", target_type, mapped_field)

        # Auto-extend known custom-field mappings across all present types.
        # When an Excel column matched an EXISTING custom field by display_name
        # (preview tagged it `custom:<type>:<key>`) but the import contains
        # rows of OTHER types too, a per-type binding silently drops values
        # for those other-type rows. Replicate the field on every present type
        # and switch the column to the wildcard form so values land regardless
        # of the row's type. This mirrors `apply_to_all_types` for unknown
        # columns but happens automatically for already-known fields.
        for idx, mapping in list(col_mapping.items()):
            if mapping[0] != "custom":
                continue
            bound_type, field_key = mapping[1], mapping[2]
            extra_types = [tc for tc in present_type_codes if tc != bound_type]
            if not extra_types:
                continue
            bound_dt = type_by_code.get(bound_type.casefold())
            if bound_dt is None:
                continue
            field_def = next(
                (f for f in bound_dt.custom_field_schema if f.key == field_key),
                None,
            )
            if field_def is None:
                continue
            for tc in extra_types:
                target_dt = await self.type_repo.get_by_code(tc)
                if target_dt is None:
                    continue
                if any(f.key == field_key for f in target_dt.custom_field_schema):
                    continue  # field already on this type
                new_schema = list(target_dt.custom_field_schema) + [field_def]
                await schema_update_cache.execute(
                    cmd=UpdateCustomFieldSchemaCommand(
                        type_code=tc,
                        schema=new_schema,
                        actor_id=cmd.actor_id,
                        request_id=cmd.request_id,
                    )
                )
                fields_added.append({"type_code": tc, "field_key": field_key})
            col_mapping[idx] = ("custom_all", field_key)

        # Schema may have changed (decisions + auto-extend added new fields).
        # Refresh the in-memory type_by_code/type_by_display maps so that
        # validate_values_against_schema sees the up-to-date schemas inside
        # _build_document — otherwise newly-added wildcard fields are not in
        # the stale schema and validate silently drops their values.
        all_types = await self.type_repo.list_all()
        type_by_code = {dt.code.casefold(): dt for dt in all_types}
        type_by_display = {dt.display_name.casefold(): dt for dt in all_types}

        # Process rows.
        # Corporate Excel registries commonly use merged cells for the
        # "Контрагент / Компания" column (one value spans multiple document
        # rows). openpyxl returns None for the inner cells, so forward-fill
        # the last seen asset name across rows.
        rows_imported = 0
        row_errors: list[ImportRowError] = []
        now = self.clock.now()
        last_asset_name: str | None = None

        for row_idx, row in enumerate(all_rows, start=2):
            try:
                standard, custom_by_type, custom_wildcard = self._extract_values(
                    row, col_mapping
                )

                asset_name_raw = standard.get("asset")
                asset_was_forward_filled = False
                if _is_blank(asset_name_raw):
                    if last_asset_name is None:
                        # Genuinely empty leading row — silent skip.
                        continue
                    standard["asset"] = last_asset_name
                    asset_was_forward_filled = True
                else:
                    last_asset_name = str(asset_name_raw).strip()

                # Separator-row heuristic: if the only thing tying this row to
                # the previous one is the forward-filled asset AND there is no
                # type_code, treat it as a visual divider (typical in
                # corporate registry xlsx between company groups). Reporting
                # these as errors is noise — silent-skip instead.
                if asset_was_forward_filled and _is_blank(standard.get("type_code")):
                    continue

                doc = await self._build_document(
                    standard=standard,
                    custom_by_type=custom_by_type,
                    custom_wildcard=custom_wildcard,
                    type_by_code=type_by_code,
                    type_by_display=type_by_display,
                    actor_id=cmd.actor_id,
                    now=now,
                )
                if doc is None:
                    continue
                await self.doc_repo.add(doc)
                rows_imported += 1
            except Exception as exc:  # noqa: BLE001
                row_errors.append(ImportRowError(row_index=row_idx, error=str(exc)))

        # Delete session from Redis
        await self._delete_session(cmd.import_session_id)

        # Emit audit event
        event = ImportCompleted(
            rows_imported=rows_imported,
            rows_failed=len(row_errors),
            fields_added=fields_added,
            actor_id=cmd.actor_id,
            request_id=cmd.request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)

        return ImportConfirmDTO(
            rows_imported=rows_imported,
            rows_failed=len(row_errors),
            fields_added=fields_added,
            errors=row_errors,
        )

    @staticmethod
    def _extract_values(
        row: tuple[Any, ...],
        col_mapping: dict[int, tuple[str, ...]],
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
        """Split a raw row into (standard fields, custom-per-type, custom-wildcard).

        Returns:
            standard: key → value for known standard fields.
            custom_by_type: type_code → {field_key → value} for fields scoped
                to a single document type.
            custom_wildcard: field_key → value for fields applied to whichever
                type the row resolves to (apply_to_all_types decisions).
        """
        standard: dict[str, Any] = {}
        custom_by_type: dict[str, dict[str, Any]] = {}
        custom_wildcard: dict[str, Any] = {}
        for col_idx, mapping in col_mapping.items():
            if col_idx >= len(row):
                continue
            val = row[col_idx]
            if _is_blank(val):
                continue
            if mapping[0] == "standard":
                standard[mapping[1]] = val
            elif mapping[0] == "custom":
                type_code = mapping[1]
                field_key = mapping[2]
                if type_code not in custom_by_type:
                    custom_by_type[type_code] = {}
                custom_by_type[type_code][field_key] = val
            elif mapping[0] == "custom_all":
                field_key = mapping[1]
                custom_wildcard[field_key] = val
            # "skip" → ignored
        return standard, custom_by_type, custom_wildcard

    async def _build_document(
        self,
        *,
        standard: dict[str, Any],
        custom_by_type: dict[str, dict[str, Any]],
        custom_wildcard: dict[str, Any],
        type_by_code: dict[str, Any],
        type_by_display: dict[str, Any],
        actor_id: uuid.UUID,
        now: Any,
    ) -> Document | None:
        """Build a Document entity from extracted row values.

        Raises ValueError with a human-readable message when the row cannot
        be imported because of missing or unresolvable required data —
        callers wrap the error and surface it per row.
        """
        # Asset is required (forward-fill is applied by the caller before us).
        asset_name = standard.get("asset")
        if _is_blank(asset_name):
            raise ValueError("Не указана компания (колонка «Компания» / «Контрагент»)")

        assets = await self.asset_repo.list_active(q=str(asset_name), limit=5)
        asset = next(
            (a for a in assets if a.name.casefold() == str(asset_name).casefold()),
            None,
        )
        if asset is None:
            from registry_service.domain.entities import Asset

            asset = Asset.create(name=str(asset_name))
            await self.asset_repo.add(asset)

        # Type — accept either code or display_name (case-insensitive).
        type_code_raw = standard.get("type_code")
        if _is_blank(type_code_raw):
            raise ValueError(
                "Не указан тип документа (колонка «Тип» / «Название документа»)"
            )

        type_lookup = str(type_code_raw).strip()
        needle = type_lookup.casefold()
        dt = type_by_code.get(needle) or type_by_display.get(needle)
        if dt is None:
            raise ValueError(f"Неизвестный тип документа: «{type_lookup}»")

        # Validate and merge custom field values for this type.
        # Wildcard (apply_to_all_types) values are overlaid first, then any
        # type-specific values from the same row override them.
        raw_custom = {**custom_wildcard, **custom_by_type.get(dt.code, {})}
        try:
            validated_custom = validate_values_against_schema(dt.custom_field_schema, raw_custom)
        except CustomFieldValidationError as exc:
            raise ValueError(str(exc)) from exc

        return Document.create(
            asset_id=asset.id,
            type_code=dt.code,
            number=(
                str(standard["doc_number"]).strip()
                if not _is_blank(standard.get("doc_number"))
                else None
            ),
            expiry_date=_coerce_date(standard.get("expires_at")),
            responsible_user_id=None,  # user IDs resolved by auth-svc — out of scope here
            notes=str(standard["notes"]).strip() if not _is_blank(standard.get("notes")) else None,
            created_by=actor_id,
            custom_field_values=validated_custom,
            now=now,
        )

    async def _load_session(self, session_id: str) -> dict[str, Any]:
        import redis.asyncio as aioredis

        async with aioredis.from_url(self.redis_url) as r:  # type: ignore[no-untyped-call]
            raw = await r.get(f"import:session:{session_id}")

        if raw is None:
            raise ImportSessionNotFoundError(f"Import session '{session_id}' not found")
        try:
            # gzipped msgpack; transparently dual-reads legacy pickle sessions
            # written by the previous version (see import_session_codec).
            data: dict[str, Any] = loads_session(raw)
            return data
        except Exception as exc:
            raise ImportSessionExpiredError(
                f"Import session '{session_id}' data is corrupt or expired"
            ) from exc

    async def _delete_session(self, session_id: str) -> None:
        import redis.asyncio as aioredis

        async with aioredis.from_url(self.redis_url) as r:  # type: ignore[no-untyped-call]
            await r.delete(f"import:session:{session_id}")
