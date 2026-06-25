# notification-service

Schedules and dispatches Лоцман notifications: in-app feed, Email (SMTP), Telegram (Bot API) and Dion, plus calendar publishing — a shared Microsoft Exchange/Outlook calendar over EWS and a tokenized iCalendar (ICS) feed any calendar app can subscribe to.

The service is **event-driven**: it reacts to `registry.documents` and `auth.invite` Redis Streams. It does **not** drive the registry — but it is not fully self-contained either. To enrich a reminder it calls `registry-service` over HTTP (`get_document`, `get_document_type` via `HttpRegistryDocumentGateway`) and resolves recipient emails from `auth-service` (`lookup_users`, `list_active_users` via `HttpAuthGateway`). Scheduled sends run on an ARQ worker, which also handles retries.

Provider credentials (SMTP password, Telegram bot token, Dion API key, Exchange/EWS service account) live **only** in this service's schema, Fernet-encrypted at rest — keeping the security blast radius separate from registry data.

---

## Owns

**Postgres schema**: `notification` (app role: `notification_app`)

**Tables** (`db/models.py`):

| Table | Purpose |
|---|---|
| `delivery_attempts` | Scheduled / sent / failed reminder records |
| `message_templates` | Per-channel, per-locale templates (Jinja2 body, JSONB variables) |
| `provider_credentials` | Fernet-encrypted per-channel config (ADR-0004 §4) |
| `calendar_subscriptions` | Exchange-calendar whitelist + ICS feed token + EWS share FSM (ADR-0005 §3, §7) |
| `calendar_event_mappings` | Exchange ItemId/ChangeKey per document event (ADR-0005 §4, §9) |
| `user_notification_prefs` | Per-user master switch, email mode, category matrix (ADR-0011) |
| `user_notifications` | In-app feed rows (ADR-0011) |
| `outbox` / `outbox_dlq` | Transactional outbox + dead-letter queue ([outbox pattern](../../docs/db/outbox-pattern.md)) |
| `idempotency` | Provider-level dedup keys to prevent duplicate sends on retry |

Delivery scheduling and retry are **domain concepts**, not separate tables — a `delivery_attempt` carries its own `status`, `retry_count` and `scheduled_at`.

---

## Public surface

All `/api/v1` routes below require an internal JWT (ADR-0002). Admin channel/calendar mutations additionally require re-MFA, enforced at the BFF.

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/healthz` · `/readyz` · `/metrics` | — | Liveness, readiness (Postgres), Prometheus metrics |
| GET | `/api/v1/admin/channels` | admin | List channel status |
| GET·PUT·PATCH | `/api/v1/admin/channels/{channel}` · `/config` | admin | Read / set / toggle a channel config |
| POST | `/api/v1/admin/channels/{channel}/test`, `/exchange_calendar/test` | admin | Send a test message |
| GET·POST·DELETE | `/api/v1/admin/calendar-subscriptions` (+`/{user_id}/retry-share`, `/mark-granted`) | admin | Exchange subscribers + automatic EWS "Reviewer" grant/revoke (ADR-0005) |
| GET | `/api/v1/admin/notifications/history` | any actor | Paginated delivery-attempt history (filters: status, template, channel, document, user, date) |
| GET | `/api/v1/calendar/feed/{token}.ics` | token | Public ICS feed — the path token **is** the credential; one VEVENT + VALARM per document |
| POST | `/api/v1/internal/email/send`, `/email/test-self` | any actor | Transactional email send used by other services |
| GET·PUT | `/api/v1/me/notification-prefs` | self | Read / update the caller's preferences |
| GET·POST | `/api/v1/me/notifications` (+`/unread-count`, `/{id}/read`, `/read-all`) | self | In-app feed + read state |

OpenAPI: http://localhost:8003/api/docs (when running locally).

---

## Events published

Published via the transactional outbox to three 2-segment Redis Streams (`infrastructure/db/repositories.py`):

**`notification.channel`** — channel-config lifecycle (`domain/events.py`):
- `notification.channel.configured.v1` · `disabled.v1` · `tested.v1` · `changed.v1` · `rekeyed.v1`

**`notification.calendar`** — Exchange share + sync (ADR-0005):
- `notification.calendar.share_granted.v1` · `share_failed.v1` · `share_revoked.v1` · `share_not_attempted.v1`
- `notification.calendar.sync_succeeded.v1` · `sync_failed.v1`

**`notification.prefs`** — per-user preference changes (`api/v1/me_notifications.py`):
- `notification.prefs.updated.v1`

> There is **no** `notification.deliveries` stream and no `delivery.*` events. That topic name was a historical bug (events never reached `audit.events`) and is not used — see the note in `repositories.py`.

## Events consumed

All events use the canonical envelope from `lotsman_shared.envelope`. Three independent consumer groups:

| Stream | Consumer group | Reacts to | Effect |
|---|---|---|---|
| `registry.documents` | `notification-calendar-sync` | `document.created/updated/archived/restored/bulk_archived.v1` | Enqueue calendar sync (EWS + ICS); invalidate ICS cache |
| `registry.documents` | `notification-events` | same set | In-app feed + email notifications (ADR-0011) |
| `auth.invite` | `notification-invite-dispatcher` | `notification.invite.requested.v1` | Deliver one-time invite OTP by email |

The two `registry.documents` groups have independent cursors, so calendar sync and event notifications never interfere.

See [ADR-0002 §A and §E](../../docs/adr/0002-service-boundaries.md) (service boundaries / internal JWT) and [ADR-0011](../../docs/adr/0011-notifications-expansion.md) (notifications expansion).

---

## Email templates (2.4.0)

In 2.4.0 every email moved from plain text to a single branded HTML template, `render_notification_email` (`infrastructure/email_html.py`). Used by deadline reminders, lifecycle notifications (created / updated / assigned / archived) and the daily digest (`SendEventDigest`) — so all of them now share one look. **Only email is affected; Telegram and Dion are unchanged.**

What the template renders:

- **Status accent** — a left border coloured by urgency (`STATUS_ACCENT`), matching the SPA status tokens: 🔴 просрочено (`overdue`, red) · 🟠 скоро / сегодня (`soon`/`today`, amber) · 🟢 актуально (`ok`, green) · нейтральное событие (`info`, blue).
- **"С одного взгляда" details block** — Компания · Тип документа · № документа · Срок действия · Осталось / Просрочено · Ответственный. (The user-facing term is **Компания**; the code field is `asset_name`.)
- **CTA button** "Открыть документ" linking to the document in the SPA, plus a "Настроить уведомления" link in the footer.
- **Dark theme** via `prefers-color-scheme` (`_EMAIL_STYLE`), a hidden preheader, and inline CSS for Outlook/Gmail/mobile.
- A **plain-text mirror** delivered alongside every HTML email.

Human-readable Russian copy comes from `infrastructure/humanize.py`: `format_date_ru("2026-07-15") → "15 июля 2026, ср"` and `days_phrase(3) → "3 дня"` (correct `день`/`дня`/`дней` pluralisation).

Migration `0009_richer_email_templates` is **DATA-only and reversible**: it rewrites the 3 deadline email rows (`pre_notice` / `in_day` / `overdue`, locale `ru`) to a short intro — the details block is now built in code — and `downgrade()` restores the original copy. No schema change; Telegram/Dion rows untouched.

Tests: `tests/unit/test_humanize.py`, `tests/unit/test_email_html.py`.

---

## Local dev

Required environment variables:

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://notification_app:pw@localhost/lotsman` | Async SQLAlchemy DSN |
| `INTERNAL_JWT_SECRET` | `dev-secret-32-chars-minimum` | Shared HS256 key |
| `CHANNEL_ENC_KEY` | `<Fernet key>` | Required — the service refuses to boot without it (US-16) |
| `REDIS_URL` | `redis://localhost:6379/0` | ARQ workers + Streams consumers |
| `SMTP_HOST` | `localhost` | Dev: use Mailpit on port 1025 |
| `SMTP_PORT` | `1025` | |

Run standalone (Postgres, Redis, and Mailpit must be up):

```bash
cd services/notification-service
DATABASE_URL=postgresql+asyncpg://notification_app:pw@localhost/lotsman \
INTERNAL_JWT_SECRET=dev-secret \
SMTP_HOST=localhost SMTP_PORT=1025 \
uv run uvicorn notification_service.main:app --reload --port 8003
```

Or via Docker Compose:

```bash
docker compose -f infra/compose.dev.yml up notification-svc --build
```

Port mapping: `127.0.0.1:8003 → container:8000`. Dev email is captured by Mailpit at http://localhost:8025.

---

## Tests

```bash
uv run pytest services/notification-service/tests -q
```

---

## Directory layout

```
services/notification-service/
├── alembic/versions/        0001 … 0009 migrations
├── src/notification_service/
│   ├── domain/              Channel events, calendar, notification-prefs, errors
│   ├── application/
│   │   └── use_cases/       send_document_reminder, event_notifications, calendar sync, channels…
│   ├── infrastructure/
│   │   ├── consumers/       registry-document, event-notification, invite consumers
│   │   ├── calendar/        EWS driver + share helpers
│   │   ├── http/            registry_gateway, auth_gateway (httpx)
│   │   ├── redis/           invite OTP store
│   │   ├── db/              repositories, session factory
│   │   ├── outbox/          Outbox dispatcher ARQ worker
│   │   ├── email_html.py    render_notification_email + branded wrapper
│   │   ├── humanize.py      Russian dates + day pluralisation
│   │   ├── templating.py    Jinja2 template rendering
│   │   └── email_send.py    SMTP send + idempotency
│   ├── db/models.py         SQLAlchemy 2.x ORM models (top-level)
│   ├── api/v1/              FastAPI routers (channels, calendar, history, feed, internal, me)
│   ├── scripts/             rotate_channel_key
│   ├── config.py
│   └── main.py
├── tests/{unit,integration}/
├── Dockerfile
└── pyproject.toml
```

---

## Migrations

```bash
cd services/notification-service
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

The `alembic_version` table lives in the `notification` schema.

---

*Last updated: 2026-06-25 (2.4.0 — branded HTML email rework).*
