# ADR-0005: Exchange Calendar integration as a notification channel

- **Status**: Proposed (ожидает acceptance) · revision 2 — расширен слоями отказоустойчивости (см. §9-§14)
- **Date**: 2026-05-08
- **Deciders**: architect (proposed), venawaziwoco83@gmail.com (pending)
- **Depends on**: ADR-0001, ADR-0002, ADR-0003, ADR-0004

## Context

Лоцман уже шлёт текстовые уведомления через 3 канала (Email/Telegram/Dion — `the admin-channels feature`). Запрос: добавить публикацию дедлайнов документов в **Microsoft Exchange Calendar** (on-prem EWS), чтобы события стояли в общем календаре отдела рядом с обычными встречами и срабатывали системные reminder'ы Outlook.

Решения, принятые в этом ADR, отвечают на 4 архитектурные развилки:

1. **Где живёт код**: новый сервис vs расширение `notification-service`?
2. **Как моделируется в БД**: новый тип в `provider_credentials` vs отдельная таблица?
3. **Как доставляется триггер**: pulling шедулер vs реактивный consumer outbox-событий registry-svc?
4. **Как изолировать драйвер**: жёсткая привязка к `exchangelib` vs Protocol-абстракция?

---

## Decision

### 1. Расширяем `notification-service`, **не плодим** `calendar-service`

Календарь концептуально — ещё один способ доставки уведомления. Все существующие механизмы (outbox, Fernet-шифрование креденшелов, Re-MFA, audit-events, `/admin/channels` UI, ARQ retry/DLQ) можно переиспользовать. Отдельный сервис добавил бы:
- Новый Postgres schema, новые роли, новые миграции
- Дублирование шифрования (новый ключ vs новая утечка)
- Новый Dockerfile, compose-сервис, healthcheck, метрики
- Новый internal-JWT key, новый аудитор-получатель
- Cross-service контракт там, где сейчас всё in-process

YAGNI. Расширение `notification-service` — минимально-инвазивное.

### 2. Календарь — 4-й тип канала в `notification.provider_credentials`

Миграция `notification 0003_add_exchange_calendar_channel.py`:

```sql
ALTER TABLE notification.provider_credentials
    DROP CONSTRAINT pc_channel_check;
ALTER TABLE notification.provider_credentials
    ADD CONSTRAINT pc_channel_check
        CHECK (channel IN ('email','telegram','dion','exchange_calendar'));

-- Новые таблицы (подробности ниже)
CREATE TABLE notification.calendar_subscriptions ( ... );
CREATE TABLE notification.calendar_event_mappings ( ... );
```

`config_enc` для exchange_calendar содержит JSON:
```json
{
  "ews_url": "https://mail.org.local/EWS/Exchange.asmx",
  "service_account_login": "DOMAIN\\lotsman-svc",
  "service_account_password": "********",
  "target_mailbox": "lotsman-deadlines@org.local",
  "auth_type": "NTLM",          // NTLM | Basic
  "verify_ssl": true,
  "default_notice_days": 14
}
```

**Шифрование переиспользует `CHANNEL_ENC_KEY`** (Fernet). Не плодим новых ключей — иначе расширяем blast-radius compromise. Single key для всех каналов уже принято в ADR-0004.

**Семантическая натяжка**: «канал» в коде сейчас означает «способ отправки сообщения». Календарь — это публикация состояния, не сообщение. Принимаю натяжку ради переиспользования инфры. Альтернатива (отдельная таблица `calendar_provider`) — рассматривалась, отклонена (см. §Alternatives).

### 3. Whitelist подписчиков — отдельная таблица в той же schema

```sql
CREATE TABLE notification.calendar_subscriptions (
    user_id      UUID         PRIMARY KEY,                -- references auth.users(id) (no FK; cross-schema)
    enabled      BOOLEAN      NOT NULL DEFAULT true,
    created_by   UUID         NOT NULL,                   -- admin user_id
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON notification.calendar_subscriptions TO notification_app;
```

Soft-disable на DELETE (`enabled=false` вместо удаления строки) — сохраняет audit-историю.

**В MVP** whitelist не влияет на содержимое календаря (все sharing-получатели Exchange видят одно и то же). Whitelist используется для:
- audit-trail «кто подписан»
- per-user opt-out (для будущей фичи per-user календарей)
- policy enforcement (если в будущем понадобится «не публиковать события документов с responsible_user, не входящим в whitelist» — место уже есть)

### 4. Mapping `document_id ↔ exchange_item_id` — composite-key таблица (N events на документ)

Каждый документ создаёт **N событий** в календаре (по одному на каждое значение `pre_notice_days` + одно due-day). Подробности в §9. Поэтому PK не может быть только `document_id`:

```sql
CREATE TABLE notification.calendar_event_mappings (
    document_id        UUID         NOT NULL,              -- references registry.documents(id) (no FK; cross-schema)
    notice_offset_days INT          NOT NULL,              -- 0 = due-day; 30/14/7/1 = pre-notice events
    exchange_item_id   TEXT         NOT NULL,              -- EWS ItemId (opaque base64)
    change_key         TEXT         NOT NULL,              -- EWS ChangeKey (concurrency token)
    external_marker    TEXT         NOT NULL,              -- "lotsman:doc:<uuid>:offset:<N>" — see §13
    last_synced_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    sync_state         TEXT         NOT NULL,
    last_error         TEXT,
    retry_count        INT          NOT NULL DEFAULT 0,
    PRIMARY KEY (document_id, notice_offset_days),
    CONSTRAINT cem_state_check CHECK (sync_state IN ('pending','synced','failed','dlq','deleted')),
    CONSTRAINT cem_offset_check CHECK (notice_offset_days >= 0)
);
CREATE INDEX cem_state_pending_idx
    ON notification.calendar_event_mappings (last_synced_at)
    WHERE sync_state IN ('pending','failed');
CREATE UNIQUE INDEX cem_external_marker_idx
    ON notification.calendar_event_mappings (external_marker);
GRANT SELECT, INSERT, UPDATE, DELETE ON notification.calendar_event_mappings TO notification_app;
```

Хранение `change_key` обязательно: EWS требует его для `UpdateItem` / `DeleteItem`, иначе `ErrorIrresolvableConflict`. `external_marker` — копия значения, записанного в Exchange `extended_property` (см. §13).

### 5. Триггер — реактивный consumer outbox-событий, **не** периодический шедулер

`registry-service` уже эмитит (verified в `services/registry-service/src/registry_service/domain/events.py`):
- `registry.document.created.v1`
- `registry.document.updated.v1` (содержит diff before/after)
- `registry.document.archived.v1`
- `registry.document.restored.v1` — пересоздаём событие при возврате документа из архива
- `registry.document.bulk_archived.v1` — массовое архивирование, payload содержит `[document_id...]` → fan-out на N задач

**Hard-delete события НЕ существует** в текущем registry — документы только архивируются (soft-delete). Если в будущем появится hard-delete, добавим обработчик; пока 5 событий выше покрывают весь lifecycle.

Все идут в `registry.outbox` → outbox-dispatcher → Redis Stream `registry.document`. Notification-service подписывается на этот stream через consumer-group `notification-calendar-sync` (новый, отдельный от существующих consumer-групп). Получив событие → enqueue ARQ-задачу `sync_calendar_event(document_id)`. Задача:

1. Загружает документ из registry-service через internal-JWT (read-only API уже есть).
2. Загружает mapping из `calendar_event_mappings`, если есть.
3. Решает операцию:
   - Нет mapping + expires_at задано + документ active → CreateItem
   - Есть mapping + expires_at задано + документ active → UpdateItem (с change_key)
   - Есть mapping + (expires_at=NULL ИЛИ документ archived) → DeleteItem
   - На `restored` событии: считаем как «active» — если нет mapping, CreateItem; если есть (mapping остался после archive) — UpdateItem
4. Выполняет EWS-вызов через `CalendarDriver` (см. §6).
5. Обновляет mapping (state, exchange_item_id, change_key, last_synced_at).
6. На любой ошибке — retry с exponential backoff (1m → 5m → 15m → 60m → 4h, max 10 attempts).

**Дополнительно** — daily reconciliation cron в 03:00 (через ARQ scheduled task): обходит `registry.documents` ↔ `calendar_event_mappings`, лечит дрифт (создаёт пропущенные, удаляет orphan'ы). Защита от потерянных событий outbox-стрима.

### 6. Driver-абстракция: `CalendarDriver` Protocol

```python
# notification-service/application/ports.py
class CalendarDriver(Protocol):
    async def upsert_event(self, *, mailbox: str, mapping: CalendarMapping | None,
                            event_data: CalendarEventData) -> CalendarSyncResult: ...
    async def delete_event(self, *, mailbox: str, mapping: CalendarMapping) -> None: ...
    async def test_connection(self, *, mailbox: str) -> CalendarTestResult: ...
```

В MVP — единственная реализация `EwsCalendarDriver` (через `exchangelib`). Под-Protocol можно потом ввести `MsGraphCalendarDriver` без правки use cases. Use case импортирует только Protocol.

### 7. Sharing в Exchange — **авто-выдача через EWS** (revision 3)

**Было (revision 1–2):** out of scope — sharing управлялось IT вручную через PowerShell.

**Теперь:** когда admin добавляет пользователя в whitelist через `/admin/calendar-subscriptions`, Лоцман
**автоматически** вызывает EWS `permission_set` и выставляет роль `Reviewer` на папку Calendar
целевого mailbox (`target_mailbox`). Пользователь затем в Outlook: «Другие календари → Открыть
общий календарь → ввести `lotsman-calendar@example.com`» — видит события без ошибки «У вас нет доступа».

#### Детали реализации

- **Новые колонки** в `notification.calendar_subscriptions` (миграция `0004`):
  - `share_status TEXT NOT NULL DEFAULT 'not_attempted'` — FSM:
    `pending → granted | failed | not_attempted | revoked`
  - `share_granted_at TIMESTAMPTZ` — timestamp успешной выдачи
  - `share_error TEXT` — sanitised сообщение об ошибке (без credentials)

- **Новый файл** `infrastructure/calendar/ews_share.py`:
  - `grant_calendar_share(*, ews_config, user_email)` — читает `permission_set`, добавляет
    `Permission(user=Mailbox(...), permission_level='Reviewer')`, сохраняет. Идемпотентен.
  - `revoke_calendar_share(*, ews_config, user_email)` — убирает пользователя из set. Идемпотентен.
  - `list_calendar_shares(*, ews_config)` — для диагностики.
  - Все три — синхронные (exchangelib sync); вызываются из `asyncio.to_thread()`.
  - Ошибки переводятся в `EwsShareError` с **safe message** (без credentials).

- **Share — best-effort, не блокирует подписку:**
  - POST/DELETE subscription возвращает 201/200 даже если EWS упал.
  - `share_status = 'failed'` + `share_error` в ответе — admin видит в UI.
  - Новый endpoint: `POST /api/v1/admin/calendar-subscriptions/{user_id}/retry-share`
    (re-MFA required) для повторной попытки.

- **Новые domain events** (в outbox):
  - `notification.calendar.share_granted.v1`
  - `notification.calendar.share_failed.v1`
  - `notification.calendar.share_not_attempted.v1`
  - `notification.calendar.share_revoked.v1`

#### Fallback (если Exchange policy блокирует self-permission-set)

Некоторые политики Exchange запрещают service-account выставлять разрешения на собственный mailbox
через EWS даже при наличии FullAccess (политика `OwnerOfMailboxFolders` или `SetChangePermission`).
В этом случае `share_error` содержит подсказку для оператора:

```
EWS permission denied — service account may lack 'FullAccess' or 'ChangePermission'
on the target mailbox calendar folder. IT fallback:
Add-MailboxFolderPermission -Identity <mailbox>:\Calendar -User <email> -AccessRights Reviewer
```

IT fallback через PowerShell остаётся рабочим вариантом и документирован в runbook.

### 8. Что меняется на хосте

- `.env`: ничего нового (используем существующий `CHANNEL_ENC_KEY`)
- `infra/compose.dev.yml`: ничего нового
- `services/notification-service/pyproject.toml`: добавляется `exchangelib>=5.5.0`, `icalendar>=5.0` (для §10)
- В runbook: новая секция §Exchange + ссылка из `/admin/channels` UI в эту секцию для super-admin

---

## Decision — Reliability layers (revision 2)

После принятия дизайна провели dedicated-обзор отказоустойчивости. Включены 6 дополнительных решений (§9-§14), повышающих надёжность доставки уведомлений в условиях полного отказа `notification-service`, потери Exchange, потери БД Лоцмана или вышедших из строя Outlook reminder'ов.

### 9. Серия событий вместо одного с reminder

Вместо одного события с reminder за `max(pre_notice_days)` создаём **N событий** на документ — по одному на каждое значение из `pre_notice_days` + одно due-day. Каждое — со своим reminder за 0 минут (срабатывает в момент события).

Пример для `pre_notice_days=[30,14,7,1]`, `expires_at=2026-08-15`:
- Event 1: 2026-07-16, subject «Лоцман: Лицензия истекает через 30 дней»
- Event 2: 2026-08-01, subject «...через 14 дней»
- Event 3: 2026-08-08, «...через 7 дней»
- Event 4: 2026-08-14, «...через 1 день»
- Event 5: 2026-08-15, «...истекает СЕГОДНЯ»

**Преимущества над single-event-with-reminder**:
- Если у пользователя выключены Outlook reminders в настройках — он всё равно ВИДИТ событие в day-view/week-view календаря в дни pre-notice
- Глаз ловит при scrolling календаря, без активного triggered notification
- Каждое событие имеет независимый reminder — не «всё или ничего»
- Подсчёт «N дней до» уже встроен в subject, не нужно считать в уме

**Недостатки**:
- N×Exchange-вызовов вместо 1 (для команды 4 человек, ~250 docs × 4 events = 1000 ops/backfill — приемлемо)
- N rows в `calendar_event_mappings` (terabytes? нет — мегабайты)
- UPDATE даты документа = N independent UpdateItem вызовов (выполняются параллельно через asyncio.gather)

**Reconciliation особенность**: за `notice_offset_days` колонкой видно «должны ли мы для этого документа иметь событие именно с этим offset». Если документ имеет `pre_notice_days=[30,14,7,1]`, а в mappings нашли только {0, 14, 7, 1} — создаём пропущенное `30`. Если в mappings есть {0, 60} — удаляем `60` (offset не из текущего списка типа документа).

### 10. ICS-feed как третий независимый канал доставки

Лоцман публикует endpoint:

```
GET /api/v1/calendar/feed/{token}.ics
```

— RFC 5545 iCalendar файл со ВСЕМИ active документами с `expires_at IS NOT NULL`. Любой календарный клиент (Outlook subscription, Google Calendar, Apple Calendar, Thunderbird) подписывается по URL и автоматически poll'ит обновления (Outlook — каждые 4 часа по умолчанию; Google — 8-24 часа).

Этот канал **полностью независим от Exchange**: даже если EWS лежит, Active Directory сломалась, service-account expired, ICS feed остаётся доступен через `nginx → web-bff → notification-service`. Это третий контур надёжности.

**Token model**:
- Один общий token на инстанс — хранится в `notification.provider_credentials` для нового channel-type `ics_feed` (или per-user — отложено в будущий ADR)
- Token = 32 байта url-safe (`secrets.token_urlsafe(32)`) — generates random, не угадывается
- Compromise: secret-в-URL не равен настоящему auth, но (a) нет write capability, (b) корпоративный VPN ограничивает reachability, (c) токен ротируется по запросу через `/admin/channels`

**Cache**:
- In-memory cache в notification-service на 5 минут (TTL)
- Invalidate при events `registry.document.*.v1` (через consumer, который и так подписан)
- При cache miss: SELECT all active docs → render via `icalendar` lib → cache → respond

**Содержимое ICS-event**: то же что и в Exchange events (subject, start, body с ссылкой), плюс iCal-специфические `UID` (= `external_marker` из §13 для cross-system консистентности) и `LAST-MODIFIED`. Возможны N-events-per-document как в §9, либо 1-event-with-VALARM. Решение: **1-event-with-VALARM** для ICS — серия в ICS избыточна т.к. ICS-клиенты обычно умеют показывать VALARM как nag-style notifications.

**UI**: на странице `/admin/channels` — 5-я карточка «ICS подписка»; пользователь копирует URL и подписывается в Outlook File → Account Settings → Internet Calendars → New.

### 11. Heartbeat / dead-man's switch event

ARQ scheduled task ежедневно в 00:00 создаёт/обновляет всегда-один-и-тот-же event с фиксированным `external_marker="lotsman:heartbeat"` и subject вида «Лоцман: Sync OK · last update 2026-05-08 00:00 MSK». Тело: ссылка на `/admin/channels/exchange_calendar` с инструкцией что делать если событие старше 24 часов.

**Use case**: super-admin или продвинутый Space-admin может в любой момент посмотреть в календарь и без открытия Лоцмана убедиться что синхронизация жива. Если heartbeat-событие отстаёт >24h — что-то сломалось, идём смотреть логи.

Дёшево (1 EWS-вызов в сутки), визуально, читаемо человеком.

### 12. Warm-up reconciliation при старте сервиса

В FastAPI lifespan `notification-service.main:lifespan`:

```python
async def lifespan(app):
    # ... existing init ...
    asyncio.create_task(warm_up_reconciliation())
    yield
```

`warm_up_reconciliation()`:
```sql
SELECT document_id FROM notification.calendar_event_mappings
WHERE sync_state IN ('pending','failed')
  AND last_synced_at < now() - interval '15 minutes'
LIMIT 1000;
```
→ для каждой строки enqueue `sync_calendar_event(document_id)` в ARQ.

**Почему не сразу обрабатываем**: чтобы не блокировать startup. ARQ-воркеры подхватывают параллельно с обычной нагрузкой.

**Защита от какого сценария**: notification-service упал во время обработки задачи → задача в ARQ-queue потерялась (Redis flush или Stream trim). Без warm-up такие записи висели бы до 03:00 reconciliation. С warm-up — они подхватываются через ≤1 минуту после старта.

### 13. Idempotency через extended_properties в Exchange events

Каждое событие, создаваемое в Exchange, получает custom `ExtendedProperty` с `PropertyId=0x0000` под нашим `PropertySetId=00020329-0000-0000-C000-000000000046` и `PropertyName="LotsmanMarker"`. Значение:

```
external_marker = f"lotsman:doc:{document_id}:offset:{notice_offset_days}"
```

(тот же текст что и в БД-колонке `external_marker` — копия для cross-source консистентности).

**Disaster recovery scenarios** где это спасает:

| Сценарий | Без extended_properties | С extended_properties |
|---|---|---|
| Полная потеря БД Лоцмана + restore из бэкапа недельной давности | Mappings пропали — все events в Exchange становятся «orphan'ами». Reconciliation создаст ДУБЛИКАТЫ. | `recover_mappings_from_exchange()` use case делает `FindItem` по нашему PropertySetId, парсит `external_marker`, восстанавливает строки в `calendar_event_mappings` |
| Случайный `TRUNCATE notification.calendar_event_mappings` | То же самое — дубликаты | Recovery из Exchange |
| Миграция данных, баг в Alembic-rollback | То же самое | Recovery из Exchange |
| Пользователь руками создал event в общем календаре | Будет считаться «нашим» при следующей попытке UPDATE | Не имеет нашего PropertySetId → reconciliation его игнорирует |

**Стоимость**: +1 строка в каждом CreateItem-вызове, никакой дополнительной инфраструктуры. Чистый upside.

### 14. Multi-channel warning banner на /admin/channels

Если включён ровно 1 канал (любой) — на странице `/admin/channels` показывается жёлтый info-баннер:

> ⚠ **Рекомендуется настроить минимум 2 канала уведомлений** — на случай отказа основного. Если, например, Email недоступен, Telegram продолжит доставлять напоминания. Один настроенный канал = единая точка отказа.

CTA: «Настроить ещё один канал» → scroll к карточкам.

Дисмиссится per session через sessionStorage (по аналогии с MIN_ADMINS баннером из ADR-0004 §6).

Это не technical hardening — это **operational hardening через UX**. Команда без баннера будет считать что одного канала достаточно. С баннером — поднимет вопрос на ретро.

### Сводная таблица слоёв надёжности (после revision 2)

| Слой | Что покрывает | Покрытие при отказе Лоцмана |
|---|---|---|
| L1: Outlook client cache + native reminder | Локальное уведомление на устройстве | ✅ Всегда (cached events) |
| L2: Exchange Server | События в mailbox с бэкапом IT | ✅ Если Exchange жив |
| L3: Outbox + Redis Stream | Догон накопленных registry-событий | ✅ После рестарта |
| L4: Daily reconciliation cron (03:00) | Лечение дрифта events ↔ mappings | ✅ В течение 24 ч |
| **L5: Series of N events (§9)** | Видимость дедлайна в календаре заранее, даже без reminder | ✅ Всегда (cached) |
| **L6: ICS-feed (§10)** | Третий независимый канал, не зависит от Exchange | ✅ Если nginx + notification-svc живы |
| **L7: Heartbeat event (§11)** | Видимая super-admin'у метрика «жив ли sync» | ✅ Видно в календаре пока heartbeat не устарел |
| **L8: Warm-up reconciliation (§12)** | Восстановление потерянных ARQ-задач | ✅ ≤1 мин после старта |
| **L9: extended_properties (§13)** | Disaster recovery после потери БД | ✅ Восстановление из Exchange |
| **L10: Multi-channel banner (§14)** | Оперативное «не клади все яйца в одну корзину» | ⏭ Превентивно (operational) |

---

## Consequences

### Positive

- **Минимальное вторжение**: 1 миграция (3 операции: ALTER + 2 CREATE TABLE), 1 dependency, 0 новых ключей шифрования, 0 новых сервисов.
- **Переиспользование infra**: ChannelCipher, audit-events, retry/DLQ, Re-MFA, /admin/channels UI — всё уже есть.
- **Driver-pattern** изолирует EWS-специфику; Microsoft Graph можно добавить в ADR-0006 без правки бизнес-логики.
- **Реактивный триггер** даёт ≤60s SLO без бизи-pulling-цикла шедулера.
- **Daily reconciliation** даёт self-healing на случай потерянных событий стрима.
- **Audit-completeness**: 8 новых event-типов покрывают все state-changes.

### Negative

- **Семантическая натяжка**: «channel» теперь обозначает и «отправку сообщения», и «синхронизацию календаря». Документируем; имена полей остаются прежние.
- **Зависимость от `exchangelib`** (зрелая, но 3rd-party): NTLM-auth требует системного `gssapi` или `pykerberos`, которые могут потребовать `apt install` в Dockerfile (`libsasl2-modules-gssapi-mit`). Документируем в Dockerfile.
- **Sharing-shift**: пользователь, выключенный из whitelist в Лоцмане, **продолжит** видеть события в общем календаре до тех пор, пока IT не уберёт его из sharing'а ящика. Документируем как limitation MVP.
- **EWS throttling** на больших тенантах (≥1000 документов): backfill потребует ≥2 минут. UI должен это показать.
- **`exchangelib` использует sync I/O** под капотом. Заворачиваем в `asyncio.to_thread()` чтобы не блокировать event loop notification-service.

### Neutral / Follow-ups

- **ADR-0006**: per-user calendars (опционально, при росте команды).
- **ADR-0007**: Microsoft Graph driver (для cloud Office 365 deployments).
- **ADR-0008**: customizable event templates через UI (Jinja2 from DB).

---

## Alternatives considered

### A. Отдельный сервис `calendar-service`

- **Pro**: чище семантически, изолирует Exchange-зависимость от других каналов, можно scale-out отдельно.
- **Con**: дубль инфры (см. §Decision/1). При 2–4 пользователях и редких событиях scale не нужен.
- **Why rejected**: YAGNI. Можно вынести в отдельный сервис позже, если нагрузка вырастет — переезд тривиален (use cases уже за Protocol).

### B. Отдельная таблица `notification.calendar_provider` (не расширять `provider_credentials`)

- **Pro**: чище семантически (provider_credentials = «отправка сообщений», calendar_provider = «синхронизация»).
- **Con**: дубль шифрования, audit, UI cards, миграция rename + два подобных endpoint'а в `/admin/channels`.
- **Why rejected**: разница чисто номенклатурная; натяжка с «channel» приемлема ради нулевой дубликации.

### C. Pulling-шедулер вместо outbox-consumer

- **Pro**: нет cross-service зависимости от registry's outbox.
- **Con**: либо медленный (cron каждые N минут — нарушаем 60s SLO), либо busy-poll (CPU). Daily reconciliation **дополняет** реактивный триггер, не заменяет его.
- **Why rejected**: SLO не достижим pulling-ом.

### D. Per-user calendars сразу (impersonation-based)

- **Pro**: подписку контролирует Лоцман на 100% (нет sharing-разрыва).
- **Con**: 1 EWS-вызов на пользователя на документ (×N). Service-account нужно `ApplicationImpersonation` для всех пользователей отдела (большая permission). Пользователь, недавно ушедший из whitelist, увидит свои события «висящими» пока их не удалит сам.
- **Why rejected (для MVP)**: shared calendar проще оперативно. ADR-0006 если запрос вырастет.

### E. EWS sharing automation (Лоцман сам выдаёт permission через EWS)

- **Pro**: Space-admin полностью самостоятелен.
- **Con**: EWS sharing операции требуют пермиссии `WriteFolders` + специфические kerberos delegation; могут не работать в hybrid-окружениях. Большой scope для MVP.
- **Why rejected (для MVP)**: handoff IT-команде через runbook — однократная операция при разворачивании.

---

## Implementation handoff

Следуем pattern'у `the admin-channels feature` — 4 фазы, 5 коммитов.

### Phase 0 — Documentation (этот ADR + sibling-доки + runbook)

**Owner**: Product + architect + Documentation
- ✅ ADR-0005 (этот файл).
- `docs/user-guide/admin-channels/exchange-calendar.md` — RU инструкция для Space-admin'а (как настроить).
- Готовится Stage 1 commit.

### Phase 1 — DB migration (notification 0003)

**Owner**: the data layer
- Миграция `0003_add_exchange_calendar_channel.py`:
  - `ALTER ... pc_channel_check` (добавить `'exchange_calendar'`)
  - `CREATE TABLE notification.calendar_subscriptions`
  - `CREATE TABLE notification.calendar_event_mappings` + индекс `cem_state_pending_idx`
  - `GRANT` на оба для `notification_app`
- SQLAlchemy модели в `db/models.py` (mirror migration).
- Verify: `alembic upgrade head` + downgrade + re-upgrade clean.

### Phase 2 — Backend (driver + use cases + consumer + API)

**Owner**: backend + the integrations layer (driver part)

Новые файлы:
- `notification-service/domain/calendar.py` — pydantic схемы `ExchangeCalendarConfig`, `CalendarEventData`, `CalendarMapping`, `CalendarSyncResult`. Валидаторы (https-only, NTLM/Basic, etc.).
- `notification-service/application/ports.py` — `CalendarDriver(Protocol)`.
- `notification-service/infrastructure/calendar/ews_driver.py` — `EwsCalendarDriver` через `exchangelib`. Все `exchangelib` вызовы обернуть в `asyncio.to_thread()`. Тип Account c IMPERSONATION на target_mailbox. Конструктор принимает уже-расшифрованный конфиг.
- `notification-service/application/use_cases/`:
  - `sync_calendar_event.py` — основной (триггерится consumer'ом + ARQ задачей)
  - `list_calendar_subscriptions.py`
  - `add_calendar_subscription.py`
  - `remove_calendar_subscription.py`
  - `test_calendar_channel.py` (probe event create+delete)
  - `backfill_calendar.py` (one-time после enable)
  - `reconcile_calendar.py` (daily 03:00)
- `notification-service/infrastructure/consumers/registry_document_consumer.py` — Redis Stream consumer для `registry.document.*` events; вызывает sync_calendar_event use case.
- `notification-service/api/v1/admin_channels.py` — расширить: добавить exchange_calendar в `_VALID_CHANNELS`, проброс в SetChannelConfig, `/test` endpoint поддерживает probe-flow.
- `notification-service/api/v1/admin_calendar_subscriptions.py` — новый router с CRUD на whitelist.

Расширения existing:
- `domain/channels.py` — добавить `ExchangeCalendarConfig` pydantic model и `SECRET_FIELDS["exchange_calendar"] = {"service_account_password"}`.
- `infrastructure/db/repositories.py` — `CalendarEventMappingsRepository`, `CalendarSubscriptionsRepository`.

BFF:
- `web-bff/api/v1/admin.py` — proxy для `/admin/channels/exchange_calendar/*` (UPDATE/PATCH/test) + новый proxy `/admin/calendar-subscriptions/*` с re-MFA gate.

ENV:
- Ничего нового. Документируем что `notification-service` Dockerfile должен иметь `libsasl2-modules-gssapi-mit` (или альтернативу) для NTLM, если on-prem требует.

Tests:
- 20+ unit-тестов на use cases (фейк-driver).
- 1 integration test на consumer (testcontainer-redis).
- Smoke с exchangelib-mock или wiremock-EWS.

### Phase 3 — UI

**Owner**: frontend + design

- `/admin/channels` — 4-я карточка «Exchange календарь». Иконка `Calendar` из lucide. Форма со всеми полями `ExchangeCalendarConfig`. Inline TOTP. Test button делает probe.
- Новая страница `/admin/calendar-subscriptions`:
  - Таблица: пользователь (email + ФИО) | роль | подписка ON/OFF | created_by | действие
  - Кнопка «Добавить подписчика» → модалка с searchable select из `auth.users`
  - Re-MFA на add/remove
  - i18n RU/EN
- В навигации админ-меню добавляется ссылка «Подписки на календарь» рядом с «Каналы уведомлений».

### Phase 4 — QA + security review + close

**Owner**: QA + security + review
- Integration test: enable channel → create document → event in mock EWS within 60s
- Security: credentials не в логах, retry не зацикливается на 401, channel auto-disable работает
- Code review: clean architecture, Protocol правильно изолирует
- Live e2e на staging Exchange (или mailpit-like EWS-stub если нет staging Exchange)
- Stage 4 commit + tag `v1.1.0`

---

## Migration safety analysis

**Что мы трогаем в существующей БД** (миграция `0003`):
1. `ALTER TABLE notification.provider_credentials DROP/ADD CONSTRAINT pc_channel_check` — non-destructive, существующие 3 канала продолжают валидировать.
2. `CREATE TABLE` — две новые таблицы, не конфликтуют ни с одной существующей.

**Что мы НЕ трогаем**:
- Существующие 3 канала (`email`, `telegram`, `dion`) — нулевые изменения в схеме / use cases / UI.
- Существующие миграции `0001`, `0002` — без правок.
- Существующие events: `notification.channel.{configured,disabled,tested,changed,rekeyed}.v1` — без правок.
- `auth-service`, `registry-service`, `audit-service` — без правок (registry уже эмитит нужные события).
- Все секреты в `.env` — без новых required переменных.

**Rollback-план**:
- `alembic downgrade -1` дропает 2 новые таблицы и сужает `pc_channel_check` обратно (требует, чтобы строк с `channel='exchange_calendar'` уже не было — иначе CHECK провалится).
- Для безопасного rollback: до `downgrade` вручную `DELETE FROM notification.provider_credentials WHERE channel='exchange_calendar'` и `TRUNCATE` обеих новых таблиц.

**Worst-case scenario** при сбое деплоя Phase 2:
- `notification-service` падает при старте → стек **остаётся работоспособен** для 3-х существующих каналов (auth, registry, web-bff не зависят от calendar-кода).
- Документы в registry создаются/изменяются нормально (registry-service не подписан на calendar-flow).
- Worst impact: события в Exchange не создаются, но никаких данных не теряется (registry.outbox продолжает копить события — consumer догонит после фикса).

---

## Quality checklist (перед Phase 1)

- [ ] Бизнес-владелец подтвердил все 4 решения из §Сводка
- [ ] IT-команда подтвердила, что готовы создать service-account с `ApplicationImpersonation` или прямым `FullAccess` на mailbox
- [ ] Решено, кто отвечает за initial sharing настройку (super-admin Лоцмана vs IT)
- [ ] Если Exchange staging доступен — выделено окно для smoke-теста; иначе — план с mock'ом (recommend `wiremock-EWS` или `python-exchangelib-mock`)
- [ ] Подтверждена разумность default `notice_days=14` (это минимально; для критичных типов документов в `document_types.notification_settings` будет больше)
- [ ] Подтверждено: один общий календарь — приемлемо для команды 2–4 человек

---

_Связано: ADR-0001 (стек), ADR-0002 (границы сервисов), ADR-0004 (channels architecture)._
