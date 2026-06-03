# ADR-0004: Two-tier administration (Super-admin / Space-admin)

- **Status**: Accepted (sections §1 «Контуры» и §2 «Роли» **superseded by ADR-0006** на 2026-05-08)
- **Date**: 2026-05-07
- **Superseded by (partial)**: ADR-0006 (Super-admin role + system panel) — отменяет решение «нет роли super_admin в JWT» и вводит четвёртую роль с web-UI
- **Deciders**: architect (proposed), venawaziwoco83@gmail.com (accepted)
- **Depends on**: ADR-0001 (Tech Stack), ADR-0002 (Service Boundaries), ADR-0003 (Authentication & Session Lifecycle)

## Context

Лоцман развёртывается **«один инстанс = один реестр = одна команда»**. Multi-tenancy в коде не нужно. Тем не менее, есть две естественно разные роли с радикально разной ответственностью:

1. **Кто-то управляет хостом** — устанавливает обновления, ротирует ключи, бэкапит БД, восстанавливает после сбоев, настраивает наблюдаемость. Этому актору нужен SSH к серверу. Никаких бизнес-операций он не делает (не создаёт документы, не приглашает editors).
2. **Кто-то управляет содержимым реестра** — добавляет / убирает сотрудников отдела, раздаёт роли, настраивает каналы уведомлений, заводит типы документов, разруливает забытые пароли. Этому актору НЕ нужен SSH; нужен веб-UI.

Текущая реализация (после `the auth feature`) даёт только второй контур: роль `admin` в JWT-claim, доступ через `/api/v1/admin/*`. Управление хостом подразумевалось «снаружи» (Makefile + .env + psql), но не было документировано.

Кроме того, сейчас:
- Channel credentials (SMTP, Telegram, Dion) лежат **в `.env` хоста** → каждое изменение требует доступа к серверу;
- Bootstrap первого admin'а **не описан** → `make seed-admin` отсутствует, на чистом инстансе невозможно начать работу;
- Приглашение пользователя — **ручной OOB OTP**, который admin лично передаёт. Нет авто-доставки через канал;
- Отсутствует политика **«≥2 admin'а»** → потеря единственного admin'а блокирует команду.

Этот ADR формализует двухконтурную модель и ставит контракты для всех четырёх пробелов.

---

## Decision

### 1. Контуры

| Контур | Кто (актор) | Доступ | Ответственность | Аудит |
|---|---|---|---|---|
| **Super-admin (Платформа)** | Владелец железа / DevOps | SSH к хосту, `make` команды, `psql` через `docker exec`, Grafana/Prometheus | Deploy · upgrade · backup/restore · ротация ключей (RS256, Fernet, internal-JWT) · OS-level secrets · мониторинг · инцидент-response | OS audit (sudo, ssh logs) + system-actor UUID в `audit.events` для bootstrap-операций |
| **Space-admin (Приложение)** | Руководитель отдела (1–2 человека) | Web-UI на `/admin/*` с обязательным TOTP + re-MFA на destructive | Приглашать/убирать сотрудников · роли editor/viewer · каналы уведомлений · типы документов · audit-log сотрудников · lockout/unlock | Каждое действие → событие `auth.*.v1` или `notification.*.v1` в `audit.events` |

**Контуры разделены физически.** Super-admin не открывает `/admin/*` через браузер (это дополнительная attack surface при наличии физического доступа). Space-admin не имеет SSH (ему не нужно).

### 2. Роли в приложении остаются прежними (1+3, не 1+4)

| Роль (JWT claim `role`) | Назначение |
|---|---|
| `admin` (Space-admin) | Полный доступ к `/admin/*`, может всё |
| `editor` | CRUD документов |
| `viewer` | Read-only + экспорт |

**Новой роли `super-admin` в приложении НЕТ.** Super-admin — это OS-уровневый актор, не аутентифицирующийся через JWT. Он работает напрямую с инфраструктурой и БД.

### 3. Bootstrap первого Space-admin'а — CLI команда

```
make admin-create EMAIL=admin@org.local FULL_NAME="Иван Петров"
```

Под капотом:
```python
docker compose exec auth-svc python -m auth_service.scripts.bootstrap_admin \
    --email <EMAIL> --full-name <NAME>
```

Поведение:
1. Создаёт `auth.users` row: role=`admin`, is_active=true, **must_change_password=true**, totp_secret_enc=NULL.
2. Генерирует одноразовый OOB OTP (24h TTL в Redis под ключом `bootstrap:otp:<email>`).
3. Печатает OTP в **stdout** (а не в логи).
4. Эмитит событие `auth.user.bootstrapped.v1` с actor_id=`SYSTEM_MIGRATOR` (известный UUIDv7 из `lotsman_shared.actors`).
5. На next login user проходит forced first-login flow: смена пароля + TOTP enrollment.

Команда **идемпотентна**: повторный вызов с тем же email возвращает existing user с обновлённым OOB OTP (старый инвалидируется), но НЕ удаляет/перезаписывает существующие credentials, если user уже active с TOTP.

**Защита**: команда работает только если у user нет TOTP secret (т.е. он либо не существует, либо ещё не завершил first-login). Это предотвращает re-bootstrap кем угодно с SSH с целью подменить пароль активного admin'а.

### 4. Channel credentials живут в БД (не в `.env`)

#### Где хранится

Существующая таблица `notification.provider_credentials` расширяется (миграция `0002_add_channel_columns.py`):

```sql
ALTER TABLE notification.provider_credentials
    ADD COLUMN channel TEXT NOT NULL,
    ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN config_enc BYTEA NOT NULL,           -- Fernet-encrypted JSON
    ADD COLUMN created_by UUID NOT NULL,
    ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD CONSTRAINT pc_channel_check CHECK (channel IN ('email','telegram','dion')),
    ADD CONSTRAINT pc_channel_unique UNIQUE (channel);  -- один канал на инстанс
```

Schema invariant: **один канал на инстанс** (один SMTP-аккаунт, один Telegram bot, один Dion endpoint). Per-user предпочтения (мой chat_id, мой email) хранятся отдельно в `auth.users` или `auth.user_preferences` (новая таблица в Phase 4 — пока опускаем, default = email из профиля).

#### Шифрование

`config_enc` — это `bytes` от `Fernet.encrypt(json.dumps(channel_config).encode())`. Master key — env-переменная `CHANNEL_ENC_KEY` (новая), генерируется через `Fernet.generate_key()` при bootstrap-стенде. **Не переиспользуем** `TOTP_ENC_KEY`, чтобы blast-radius разнести: компрометация одного ключа не разворачивает оба.

При rotate `CHANNEL_ENC_KEY` (раз в 90 дней — то же что для JWT) — runbook описывает шаги re-encrypt.

#### Что внутри `config`

| Channel | Поля JSON-конфига |
|---|---|
| `email` | `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `from_address`, `from_name` |
| `telegram` | `bot_token`, `default_parse_mode` (MarkdownV2/HTML) |
| `dion` | `api_base`, `api_token`, `workspace_id` (если требуется) |

#### Что меняется на хосте

- `.env` теряет: `SMTP_HOST/PORT`, `TELEGRAM_BOT_TOKEN`, `DION_API_BASE/TOKEN`. Только пустые placeholder'ы для **bootstrap-defaults** (admin может пропустить и настроить через UI).
- `.env` приобретает: `CHANNEL_ENC_KEY` (≥32 хеф-байт).
- `notification-service` на startup: читает `provider_credentials` из БД, расшифровывает в memory, инициализирует SMTP/Telegram/Dion клиенты. На UPDATE (через events `notification.channel.changed.v1`) — re-init без рестарта (hot-reload).

### 5. Auto-invite через канал (с fallback)

#### Новый use case `invite_user`

```
POST /api/v1/admin/users
Body: {
  "email": "...",
  "full_name": "...",
  "role": "editor",
  "delivery": "auto" | "show-otp"   // выбирает space-admin
}
```

`delivery=auto`:
1. Создать user (как сейчас) + сгенерить OOB OTP.
2. Вычислить **default-канал** (приоритет: enabled email → telegram → dion). Если нет включённых каналов → 409 + сообщение «настройте канал в /admin/channels или используйте delivery=show-otp».
3. Эмитить событие `notification.invite.requested.v1` с payload {to: email, otp, role, login_url, ttl_minutes=10}.
4. Notification-dispatcher reagирует: рендерит шаблон `invite_user.{channel}` (Jinja2 в `notification.message_templates`) → отправляет.
5. Возвращает space-admin'у в response: `{user_id, channel_used: "email", invitation_id}`. Никакого OTP в response — он пошёл по каналу.

`delivery=show-otp`:
1. Создать user + OOB OTP.
2. Вернуть в response `{user_id, otp, otp_ttl_minutes: 10}`. Space-admin показывает в UI ОДИН РАЗ в модалке.

Default в UI: `auto` если ≥1 канал enabled, иначе `show-otp`.

#### Идемпотентность приглашения

Если space-admin отправил повторный invite на того же email **до** завершения first-login:
- Старый OOB OTP инвалидируется (DEL ключ в Redis).
- Новый OTP генерируется и отправляется.
- audit-event `auth.invitation.resent.v1`.

Если user уже active с TOTP — приглашение блокируется (это бы перезаписало TOTP secret). Space-admin использует password reset / TOTP reset вместо invite.

### 6. Политика «≥2 admin'а»

Hard-rule в backend и UI:

- **Backend** запрещает: `DEACTIVATE`/`DELETE` последнего active admin (HTTP 409 + payload `{"detail": "Должен оставаться минимум 1 активный admin", "code": "MIN_ADMINS"}`). Для деактивации — сначала повысить editor → admin.
- **Backend** запрещает: смену роли `admin → editor` если это последний admin.
- **UI**: жёлтый баннер на `/admin/users` если active admin'ов <2 — «Рекомендуется иметь минимум 2 admin'а на случай отпуска / потери TOTP».

Это рекомендация (не блокирует работу с одним admin'ом), но предупреждает.

---

## Consequences

### Positive

- **Чёткое разделение privilege**: super-admin никогда не видит бизнес-данные через UI; space-admin не может тронуть инфру.
- **Space-admin самостоятелен**: настраивает каналы без помощи DevOps.
- **UX onboarding**: новый сотрудник получает приглашение на email/Telegram, а не «сходи к Ивану за бумажкой».
- **Resilience**: «≥2 admin'а» убирает SPOF.
- **Audit-completeness**: каждое изменение канала эмитит событие, super-admin видит в Grafana/Loki через лог-стрим, space-admin — в audit-log UI.
- **Без новых ролей и таблиц**: используем существующую `notification.provider_credentials`, расширенную миграцией.

### Negative

- **Новая security-поверхность**: space-admin может подменить bot token → утечка уведомлений через чужой канал. Mitigation: audit-event на любое изменение конфига канала + rate-limit на изменения (не чаще 5 в час).
- **Migration `0002_add_channel_columns.py` на live БД**: non-destructive (только ADD COLUMN + CHECK + UNIQUE), но требует aware deployment.
- **Новый env `CHANNEL_ENC_KEY`** на хосте, забыть = получить «не могу расшифровать» при первом запуске. Mitigation: bootstrap-проверка на startup `notification-service`, с явной ошибкой и инструкцией.

### Neutral / Follow-ups

- **ADR-0005**: per-user preferences (свой Telegram chat_id) — сейчас явно опускаем, default = email из `auth.users`.
- **ADR-0006** (на ваш рост): Co-admin роль — отдельный ADR когда команда вырастет до 6+. Сейчас YAGNI.
- **ADR-0007** (когда введём): Vault / KMS взамен Fernet с env-managed master-key.

---

## Alternatives considered

### A. Один контур (роль `super-admin` в JWT)

- **Pro**: единый интерфейс, одно место для всех админ-операций.
- **Con**: super-admin живёт в той же auth-плоскости что и пользователи. Компрометация web-bff → доступ к bootstrap. Удваивает attack surface. Лишний код для операций, которые SSH/CLI делают за 30 секунд.
- **Why rejected**: для on-prem с ≤4 пользователей — гипертрофия. SSH владеет хостом физически, дополнительная аутентификация в приложении ничего не добавляет.

### B. Channel credentials остаются в `.env`

- **Pro**: проще, нет миграции БД, нет шифрования, нет UI.
- **Con**: каждое изменение канала = SSH + edit `.env` + restart container. Space-admin не самостоятелен. Если у инстанса 3 канала и 2 раза в год меняется bot token — 6 SSH-сессий в год без необходимости.
- **Why rejected**: преимущество DB-storage окупает migration cost за 1-й цикл изменения.

### C. Co-admin роль сразу

- **Pro**: подготовлено к росту команды.
- **Con**: пока команда 4 человека — overkill. Лишние тесты, документация, edge-cases (admin не может deactivate co-admin? — правила).
- **Why rejected (сейчас)**: YAGNI. Введём отдельным ADR-0006, когда обозначится потребность.

### D. Self-service password reset через email-link

- **Pro**: классический UX.
- **Con**: phishing-vector, требует надёжного email канала, противоречит whitelist'у. Для 4 человек проще «admin сделает».
- **Why rejected**: уже зафиксировано в ADR-0003.

---

## Implementation handoff

### Phase 0 — Documentation (этот ADR + два sibling-документа). **СЕЙЧАС.**

### Phase 1 — Bootstrap CLI

**Owner**: backend
- `services/auth-service/src/auth_service/scripts/bootstrap_admin.py` — argparse-based CLI
- `Makefile` — цель `admin-create`
- Подъём из storage существующего admin не требуется — у нас уже есть `admin@lotsman.local` (UUID `47ba0fdc-…-bf1e3`); CLI работает с любого пустого state.

### Phase 2 — Channel storage in DB

**Owner**: the data layer + backend
- Миграция `notification 0002_add_channel_columns.py` — non-destructive ALTER TABLE.
- `services/notification-service/src/notification_service/infrastructure/channel_crypto.py` — Fernet wrapper.
- Use cases: `set_channel_config`, `get_channels`, `test_channel`, `disable_channel`. Все admin-only с re-MFA.
- BFF proxy `/api/v1/admin/channels`.
- Auth-service: новый use case `invite_user` (расширяет существующий `create_user`), эмитит событие в notification.

### Phase 3 — UI

**Owner**: frontend
- `/admin/channels` — 3 карточки с CRUD.
- `/admin/users` — radio-button delivery + warning «≥2 admin».
- i18n RU/EN.

### Phase 4 — Verify + commit

**Owner**: QA + security
- Integration test: bootstrap → invite → first-login через канал.
- Security review: блокировки на «выключить последний канал», audit-event coverage, no token-leak в logs.

---

_Последнее обновление: 2026-05-07. Утверждение пользователем (5/5 default-recommendations)._
