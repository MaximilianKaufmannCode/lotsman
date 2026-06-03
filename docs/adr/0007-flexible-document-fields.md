# ADR-0007: Flexible custom fields per document type

- **Status**: Proposed (ожидает acceptance)
- **Date**: 2026-05-08
- **Deciders**: architect (proposed), venawaziwoco83@gmail.com (selected variants — см. §Decision)

## Context

Сейчас `registry.documents` имеет фиксированный набор полей (asset_id, type_code, doc_number, expires_at, responsible_user_id, status, notes). Когда space-admin импортирует xlsx с дополнительными колонками — «Сумма контракта», «Орган выдавший лицензию», «Срок гарантии» — эти данные теряются: импортер их игнорирует, потому что не знает куда положить.

Также при создании нового типа документа admin не может ассоциировать с ним type-specific поля без изменения схемы БД и UI.

Запрос: дать space-admin'у возможность (a) определить **per-type** дополнительные поля через web-UI, (b) при импорте xlsx с новыми колонками увидеть **preview** и решить — создать как новые поля, переименовать, сопоставить с существующими, или пропустить.

---

## Decision

### 1. Custom fields живут в JSONB-схеме на уровне типа документа

`document_types.custom_field_schema JSONB DEFAULT '[]'` — массив объектов:
```json
[
  {"key": "contract_sum", "display_name": "Сумма контракта", "type": "number", "required": false, "options": null},
  {"key": "issuer", "display_name": "Орган выдавший", "type": "text", "required": true, "options": null},
  {"key": "warranty_until", "display_name": "Срок гарантии", "type": "date", "required": false, "options": null},
  {"key": "criticality", "display_name": "Критичность", "type": "enum", "required": true, "options": ["Низкая","Средняя","Высокая"]}
]
```

`documents.custom_field_values JSONB DEFAULT '{}'` — словарь `{key: value}`:
```json
{"contract_sum": 1500000, "issuer": "Минцифры РФ", "criticality": "Высокая"}
```

GIN-индекс на `documents.custom_field_values` для будущего поиска по кастомным полям.

### 2. Per-type, не глобально (но с возможностью «всегда показывать»)

Schema привязана к `document_type` — для «Лицензии» один набор, для «Договора» другой. Если space-admin хочет поле во всех типах — он добавляет одинаковое (по `key` + `display_name`) в каждый тип. Глобальная альтернатива (см. §Alternatives) отвергнута.

### 3. 4 типа полей: Text, Number, Date, Enum

| `type` | Storage | UI input | Validation backend |
|---|---|---|---|
| `text` | JSON string | `<input type="text">` (multiline if length>120) | `min_length`/`max_length` (опц.) |
| `number` | JSON number | `<input type="number">` | `min`/`max` (опц.); only finite numbers, NaN/Inf reject |
| `date` | JSON string ISO `YYYY-MM-DD` | `<input type="date">` | parsable ISO date |
| `enum` | JSON string из `options` | `<select>` | значение должно быть в `options` |

`required: bool` — backend отказывает в save документа если поле помечено required и значение отсутствует.

### 4. 2-step xlsx import: preview → confirm

Изменение существующего use case `import_xlsx`:

**Шаг 1 — preview** (`POST /api/v1/admin/import/preview`):
- Backend парсит xlsx: первая строка = headers; для каждого header'а сопоставляет:
  - **Existing standard column** (контрагент, тип, № документа, дата) → matched
  - **Existing custom field** (сравнение по `display_name` case-insensitive trim) → matched
  - **Unknown** → suggested type (text default; numeric если все cells число; date если все cells parseable date) + first 3 sample values
- Возвращает `{rows_total, known_columns: [...], unknown_columns: [{header, suggested_type, samples}]}`

**Шаг 2 — confirm** (`POST /api/v1/admin/import/confirm`):
- Body: `{import_session_id, decisions: [{header, action: "create_new"|"map_to_existing"|"skip"|"rename", new_key?, target_type?, mapped_to_field?}]}`
- Backend:
  - Для `create_new` — добавляет новое поле в `custom_field_schema` указанного `target_type`, эмитит `registry.document_type.field_added.v1`
  - Для `rename` — добавляет с новым display_name
  - Для `map_to_existing` — реиспользует существующее
  - Для `skip` — игнорирует столбец
  - Затем выполняет реальный insert документов (с `custom_field_values` для добавленных столбцов)
  - Эмитит `registry.import.completed.v1`

Import-session живёт в Redis 30 минут с ключом `import:session:{uuid}`. Содержит распарсенный xlsx (gzip-compressed pickle).

### 5. Динамические колонки в реестре

`registry.documents` table в UI:
- Стандартные колонки (контрагент, тип, № документа, дата, статус) — всегда видны
- Кастомные колонки — добавляются динамически из `document_type.custom_field_schema` отображаемого среза. Если выборка содержит несколько типов — показываются все объединённые поля; ячейки для документов другого типа = пустые.
- Per-user (или per-session) toggle visibility через "Колонки" dropdown (уже существует в registry-page).

### 6. Validation на сохранении документа

`save_document` use case (create/update) — после стандартной валидации:
- Загрузить `custom_field_schema` для `document.type_code`
- Для каждого поля schema: проверить тип/required/enum-membership/min-max
- На fail → `ValidationError` с `loc=["custom_field_values", "<key>"]` чтобы UI подсветил проблемный input

### 7. Audit events

| Event type | Emit on | Payload |
|---|---|---|
| `registry.document_type.fields_updated.v1` | Admin меняет схему через `/admin/document-types/{id}/fields` | `{type_code, schema_before, schema_after}` |
| `registry.document_type.field_added.v1` | Field добавлен через import-confirm | `{type_code, field_key, source: "import"}` |
| `registry.import.preview.v1` | Preview вызван | `{rows_total, unknown_count}` |
| `registry.import.completed.v1` | Confirm выполнен | `{rows_imported, rows_failed, fields_added}` |

### 8. Что меняется в существующих моделях

- `Document` entity — поле `custom_field_values: dict[str, Any]` (default empty)
- `DocumentType` entity — поле `custom_field_schema: list[CustomField]` (default empty list)
- Существующие документы получают `custom_field_values={}` (миграция non-destructive — DEFAULT в SQL)
- Существующие типы получают `custom_field_schema=[]`

---

## Consequences

### Positive
- **Гибкость без code-deploys**: admin сам расширяет модель данных под бизнес-нужды.
- **Импорт «как есть»**: xlsx с новыми колонками не теряется, admin видит что добавляется.
- **Не ломает существующее**: defaults `[]` / `{}` означают backward-compat. Существующие документы и типы продолжают работать.
- **JSONB + GIN**: PostgreSQL умеет эффективно искать по любому ключу/значению.
- **4 типа полей** покрывают 95% реальных кейсов; для редких case'ов всегда есть fallback в `text`.

### Negative
- **JSONB ≠ типобезопасность на уровне БД**: типы соблюдаются только в backend validation. Если кто-то напрямую UPDATE через psql — может насыпать мусор. Mitigation: триггер можно добавить в будущем, в MVP — accept.
- **Динамические колонки в UI**: TanStack Table придётся пересобирать columns array при смене фильтра по типу. Перерендеры — приемлемо для табличек ≤500 строк.
- **Import-session в Redis**: занимает память (gzip xlsx ≤ 5 МБ × ≤ 5 sessions = ≤ 25 МБ). TTL 30 мин ограничивает.
- **Backward-compat в API**: эндпоинт `POST /api/v1/admin/import` остаётся, но теперь фактически работает как `import/preview` + автоматический `import/confirm` (если все columns known). Если приходят unknown columns → 409 + URL для preview-flow.

### Neutral / Follow-ups
- **ADR-0008** (когда будет нужно): глобальные кастомные поля (как в §Alternatives B).
- Поиск по кастомным полям в строке поиска — отдельная фича; в этой только storage + UI display.
- Экспорт xlsx — должен включать кастомные колонки. Делается в Phase 2 как часть `export_xlsx` use case.

---

## Alternatives considered

### A. Per-type custom fields через отдельную таблицу `document_type_fields(type_code, key, ...)`
- **Pro**: типобезопасно, чище схема.
- **Con**: каждое чтение документа = JOIN на 2 таблицы; миграция данных при изменении схемы поля.
- **Why rejected**: JSONB на 4 человек overkill-free, performance impact исчезающе мал на 250 документах.

### B. Глобальные custom fields для всех документов
- **Pro**: проще админу — один раз настроил, везде видно.
- **Con**: бесполезные пустые ячейки для типов где это поле не валидно. UI шум.
- **Why rejected**: per-type выбран пользователем (Stage 0 confirmation).

### C. Auto-create колонок без подтверждения
- **Pro**: zero-clicks импорт.
- **Con**: «Сума» vs «Сумма» = два разных поля. Засоряет схему опечатками. Trust-кризис когда admin видит 17 кастомных полей вместо 4.
- **Why rejected**: пользователь выбрал preview+confirm.

### D. Strict (отказывать импорт если есть unknown columns)
- **Pro**: гарантированно чистая схема.
- **Con**: admin вручную заводит 4 поля → пробует импорт → получает «не подходит» → правит → repeat. UX-кошмар.
- **Why rejected**: пользователь выбрал preview+confirm.

---

## Implementation handoff (4 фазы, mirror admin-channels/exchange-calendar pattern)

### Phase 0 — Documentation (этот ADR + requirements + commit)
- ✅ ADR-0007
- Stage 0 commit (docs-only, БЕЗ bump per the project conventions правила pure-docs).

### Phase 1 — DB migration
**Owner**: the data layer
- Migration `registry 0005_custom_fields.py`:
  - `ALTER TABLE registry.document_types ADD COLUMN custom_field_schema JSONB NOT NULL DEFAULT '[]'`
  - `ALTER TABLE registry.documents ADD COLUMN custom_field_values JSONB NOT NULL DEFAULT '{}'`
  - `CREATE INDEX documents_custom_fields_gin_idx ON registry.documents USING GIN (custom_field_values)`
  - Reversibility verified (upgrade + downgrade + re-upgrade clean).
- SQLAlchemy models extended.

### Phase 2 — Backend
**Owner**: backend + the integrations layer (для import preview)
- Domain: `CustomField` value object + `FieldType` enum.
- Use cases:
  - `update_custom_field_schema` (admin only, re-MFA-gated as «sensitive admin op» per pattern).
  - `validate_custom_field_values` helper, called from `create_document`, `update_document`.
  - `import_xlsx_preview` (returns import_session_id + columns analysis).
  - `import_xlsx_confirm` (consumes session_id + decisions).
- Updated `import_xlsx` flow: попытка straight-import; если есть unknown columns → 409 с `{"detail":"Use /admin/import/preview","code":"UNKNOWN_COLUMNS","preview_url":"..."}`.
- API endpoints (registry-svc):
  - `GET  /api/v1/admin/document-types/{id}/custom-fields` — read schema
  - `PUT  /api/v1/admin/document-types/{id}/custom-fields` — replace schema (re-MFA)
  - `POST /api/v1/admin/import/preview` — multipart xlsx upload
  - `POST /api/v1/admin/import/confirm` — JSON body with decisions (re-MFA)
- BFF proxy для всех 4-х endpoints.
- Validation: `custom_field_values` validated против `custom_field_schema` при save; на fail — 422 с typed errors.
- Audit events (4 типа выше).

### Phase 3 — UI
**Owner**: frontend + design
- Новая страница `/admin/document-types/{id}/fields`:
  - Список полей с inline-editor (display_name, type, required, options для enum)
  - Drag-handle для порядка
  - Add field / Remove field (с warning «существующие документы потеряют значения этого поля»)
  - re-MFA при сохранении
- `DocumentForm` (создание/редактирование): динамически рендерит inputs на основе custom_field_schema выбранного типа документа.
- `RegistryPage`: динамические колонки из объединённой схемы видимых типов; toggle visibility per column.
- `ImportXlsxDialog` 2-шаговый:
  - Шаг 1: upload файла → preview-table с known/unknown columns
  - Шаг 2: для каждого unknown — radio (Создать как новое поле / Сопоставить с существующим / Пропустить) + при «создать» — выбор типа + edit display_name
  - Шаг 3: confirm с re-MFA → import progress

### Phase 4 — QA + close
**Owner**: QA + review
- Tests: unit-валидация всех 4-х типов; integration import preview→confirm; edge case enum + missing options; race на concurrent schema edits.
- Bump 1.2.1 → **1.3.0** (MINOR per the project conventions).
- CHANGELOG entry.
- Tag **v1.3.0**.

---

## Migration safety analysis

**Что меняется в существующей БД** (миграция `registry 0005`):
1. `ALTER TABLE document_types ADD COLUMN ... DEFAULT '[]'` — не блокирует существующие строки (DEFAULT присваивается lazily Postgres'ом, без table rewrite).
2. `ALTER TABLE documents ADD COLUMN ... DEFAULT '{}'` — то же.
3. `CREATE INDEX ... USING GIN` — на пустых JSONB строится мгновенно.

**Что НЕ трогается**:
- Существующие документы (~8 штук в dev) — получают `custom_field_values = '{}'`, продолжают работать.
- Существующие типы документов — получают `custom_field_schema = '[]'`, ничего не меняется в их поведении.
- Все существующие API endpoints без unknown columns — продолжают принимать импорт без preview-flow.
- Notification/calendar — без изменений (events не зависят от custom fields).

**Rollback**:
- `alembic downgrade -1` — `DROP COLUMN` × 2 + `DROP INDEX`. Если документы накопили `custom_field_values` — данные теряются (необратимо). Pre-condition: бэкап.

**Worst-case scenario** при сбое деплоя Phase 2:
- registry-svc не стартует → весь стек частично не работает (registry, реестр UI). Notification продолжает обрабатывать events предыдущих документов.
- Документы не теряются (миграция к этому моменту уже применена, DEFAULT заполнил пустыми значениями).

---

## Quality checklist (перед Phase 1)

- [x] Per-type vs global → **per-type** (selected)
- [x] Auto-create vs preview-confirm → **preview+confirm** (selected)
- [x] Field types → **Text + Number + Date + Enum** (selected)
- [ ] Backward-compat: существующий `POST /admin/import` поведение при unknown columns → 409 с redirect на preview-flow
- [ ] re-MFA gate на ВСЕ mutating endpoints (per universal rule из ADR-0006 §5)
- [ ] Audit-event coverage для всех 4 event types

---

_Связано: ADR-0001 (стек), ADR-0002 (границы сервисов)._
