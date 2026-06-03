# ADR-0006: Super-admin role + system control panel

- **Status**: Proposed (ожидает acceptance)
- **Date**: 2026-05-08
- **Deciders**: architect (proposed), venawaziwoco83@gmail.com (selected variants — см. §Decision)
- **Supersedes (partial)**: ADR-0004 §1 (контуры) и §2 (роли в приложении)

## Context

В ADR-0004 §2 было зафиксировано «**Новой роли `super-admin` в приложении НЕТ**» с мотивацией «снизить attack surface», предполагая что DevOps управляет инфраструктурой через SSH/CLI/Makefile, а в приложении нужна только бизнесовая роль `admin`.

На практике это решение оказалось **расхождением с изначальным видением продукта** (зафиксировано в обмене с product owner'ом 2026-05-08): задумывалось два разных типа администратора — **бизнесовый** (Space-admin) управляет реестром, пользователями, каналами; **платформенный** (Super-admin) отвечает за здоровье инфры, миграции, бэкапы, ротацию ключей, перезапуск сервисов. Разные обязанности, разные ответственности, разный audit-trail.

Кроме того, оригинальная ADR-0004 §3 «Bootstrap первого Space-admin'а через CLI» де-факто работала как способ создать **админа со всеми правами разом** — что концептуально соответствует super-admin, но без явного разделения. Это путало.

Этот ADR вводит формальное разделение и web-UI для платформенного администратора.

---

## Decision

### 1. Четвёртая роль `super_admin` в JWT (взаимоисключающая с admin/editor/viewer)

- Расширить enum ролей в `auth.users.role`: `'super_admin' | 'admin' | 'editor' | 'viewer'`.
- В JWT claim `role` появляется значение `super_admin`.
- **Взаимоисключение**: один пользователь — одна роль. Если нужно «и админ, и super-admin» — это два аккаунта (`super@org.local` и `admin@org.local`). Это сознательное решение в пользу чистого audit-trail и принципа «one hat at a time».

### 2. Зоны ответственности (чётко разделены)

| Роль | Где работает | Что может |
|---|---|---|
| `super_admin` | `/system/*` — новый раздел UI | Health-check сервисов, queues, audit (системный stream), бэкапы, миграции, ключи, логи, destructive ops (backup-trigger, restart-svc, alembic upgrade) с двойным подтверждением |
| `admin` (Space-admin) | `/admin/*` (как раньше) + основной реестр | Пользователи, каналы, типы документов, реестр |
| `editor` | Реестр | CRUD документов |
| `viewer` | Реестр | Read-only + экспорт |

**Разделение жёсткое**:
- `super_admin` НЕ имеет доступа к `/admin/users`, `/admin/channels`, реестру (нет смысла — это домен бизнес-данных, не его).
- `admin` НЕ имеет доступа к `/system/*` (не должен случайно перезапустить сервис или включить миграцию).
- В UI: пункты nav-меню для `/system/*` видны только super_admin'у; для `/admin/*` — только admin'у.

### 3. Bootstrap первого super-admin'а — отдельный CLI

Аналог `make admin-create`, но с другим подтипом:

```
make superadmin-create EMAIL=super@org.local FULL_NAME="Super Admin"
```

Под капотом — `auth_service.scripts.bootstrap_super_admin`, аналог `bootstrap_admin.py`, но создаёт user с `role='super_admin'`. OOB OTP в stdout, TTL 24h, идемпотентность та же.

**Защита**: `make admin-create` НЕ может создать super_admin (не передать `--super` флаг — отдельная команда). Это minimum-privilege по умолчанию.

«MIN_ADMINS» policy расширяется: должен быть **≥1 active admin** AND **≥1 active super_admin**. Backend блокирует deactivation последнего в любой группе.

### 4. Новый микросервис? — НЕТ. Расширение существующих

`super_admin` — новая роль в `auth-service` (миграция). Endpoints `/api/v1/system/*` живут в **двух местах** в зависимости от характера операции:

| Операция | Где живёт API |
|---|---|
| Health-check, queue-depth, последние audit-events, ключи (когда ротировались), миграции (alembic version) | `web-bff` напрямую (агрегатор; читает из существующих сервисов через internal-JWT) |
| Системные audit-events (фильтр) | `audit-service` — новый endpoint `/api/v1/audit/system?...` |
| Backup-trigger, restart-svc, migrate-trigger (destructive) | **Новый sidecar `lotsman-system-control`** (см. §6) — единственный с правом исполнять команды на хосте |

### 5. TOTP gate на ВСЕ state-changing операции (универсальное правило панели)

**Правило**: каждая мутирующая операция в `/system/*` требует свежий TOTP-код в теле запроса. Это **то же самое поведение**, что уже работает в `/admin/channels` (PUT/PATCH/test) и в профиле (смена пароля). Read-only GET'ы — БЕЗ TOTP.

| Тип операции | Защита |
|---|---|
| **Read-only GET** (health, queues, audit, keys, migrations, logs view) | Только role-check (`super_admin`). TOTP не требуется. |
| **Mutating PATCH/PUT/POST** (rotate-key trigger, change setting, update threshold, dismiss alert) | role-check + **inline TOTP в body** + backend `re_mfa_check` (атомарный, защищён от replay через `auth.totp_used_codes`). На REMFA_REQUIRED/REMFA_REPLAY — 401, UI очищает поле и фокусит. |
| **Destructive op** (backup-trigger, restart-svc, alembic upgrade, key-rotation execute) | role-check + inline TOTP + **typed-confirmation** (введите `registry-svc` чтобы подтвердить, как в GitHub при delete-repo). Двойной gate. |

Поток для destructive op (полный пример):

1. SPA: модальный диалог запрашивает (a) TOTP-код и (b) typed-confirmation (имя сервиса/команды).
2. SPA шлёт `POST /api/v1/system/restart-service` body `{service: "registry-svc", totp_code: "123456", confirmation: "registry-svc"}`.
3. BFF: validates `confirmation == service`. На несовпадение — 400 без call'а наверх.
4. BFF: `re_mfa_check(actor, totp_code)` — на 401 (REMFA_REPLAY) задерживает дальше.
5. BFF mints internal-JWT с `aud=system-control` (новый audience), `actor=super_admin_user_id`, TTL=60s.
6. BFF вызывает `lotsman-system-control` HTTP API (sidecar).
7. Sidecar validates JWT + checks команду по whitelist, выполняет через `subprocess` (или `docker.from_env()`), возвращает stdout/exit-code.
8. Audit-event `system.command.executed.v1` с payload `{actor, command, exit_code, duration_ms}` (БЕЗ stdout — может содержать чувствительное).

**TOTP code re-use**: каждый код одноразовый в течение его 30s window — backend записывает used codes в `auth.totp_used_codes` (composite PK уже существует с the admin-channels feature).

### 6. Sidecar `lotsman-system-control`

Минималистичный отдельный сервис:
- Python 3.12 + FastAPI + structlog. ~300 LOC.
- Mount: `/var/run/docker.sock` read-write (только этот сервис имеет!) + `/opt/lotsman` read-only (для git ops если понадобится).
- Auth: только internal-JWT с `aud=system-control` от web-bff, подписан per-service ключом `INTERNAL_JWT_KEY_SYSTEM_CONTROL` (новый env).
- API:
  - `POST /trigger/backup` — запускает скрипт бэкапа (whitelist'нутый pathname)
  - `POST /restart-service {service: "auth-svc"|"registry-svc"|...}` — `docker restart lotsman_<svc>` (whitelist сервисов)
  - `POST /migrate {service}` — `docker exec ... alembic upgrade head`
  - `GET /docker/ps` — read-only health
  - `GET /logs {service, tail: int<=500}` — `docker logs --tail`
- Whitelist команд hard-coded в коде sidecar'а (ни одного eval, никакого sh -c, никакой пользовательский ввод не шеллится).
- Только в network'е `lotsman-internal` (не expose'ed на хост-порт).

**Почему отдельный сервис, не endpoint в web-bff**: **blast radius**. Web-bff обрабатывает HTTP запросы публично (через nginx). Если в нём найдут уязвимость — атакующий не получает root на хосте. Sidecar маленький, узкий, его аудит на ~300 LOC можно сделать руками.

### 7. Schema changes (минимально-инвазивно)

Миграция `auth-service 0006_super_admin_role.py`:

```sql
ALTER TABLE auth.users DROP CONSTRAINT users_role_check;
ALTER TABLE auth.users ADD CONSTRAINT users_role_check
    CHECK (role IN ('super_admin','admin','editor','viewer'));
```

Никаких новых таблиц в auth. Существующие записи (4 system actors + admin@lotsman.local) не трогаются — они имеют валидные role-значения.

### 8. UI navigation

- Если `claims.role === 'super_admin'` → nav показывает **только** «Системная панель» (никаких /admin/*, реестра)
- Если `claims.role === 'admin'` → nav показывает «Реестр» + «Администрирование» (как сейчас) — `/system/*` скрыт
- Если `editor`/`viewer` → как сейчас
- Top-bar показывает badge с типом текущей роли крупным цветом: красный «SUPER-ADMIN», синий «ADMIN», серый «EDITOR/VIEWER»

### 9. ICS feed для super-admin (мониторинг через календарь)

Опционально (Tier B): super-admin может подписаться на feed `/api/v1/system/feed/{token}.ics` с системными событиями (deployment, key rotation, channel.auto_disabled). Reuse шаблона из ADR-0005 §10.

---

## Consequences

### Positive

- **Разделение обязанностей**: бизнес-админ не лазит в инфру; super-admin не лазит в бизнес-данные. Чище аудит-trail.
- **Явный bootstrap для super-admin**: `make superadmin-create` не путает с обычным admin'ом.
- **UI для super-admin**: визуально доступная панель здоровья + операций. Не нужно держать в голове CLI-команды.
- **Sidecar `system-control`**: blast-radius destructive ops изолирован. Compromise web-bff не даёт root.
- **MIN_SUPER_ADMINS**: предотвращает потерю доступа к платформе.

### Negative

- **Новая миграция auth.users.role CHECK** — non-destructive, но требует aware deployment.
- **Новый sidecar = +1 сервис в compose** — больше operational complexity.
- **Двойной admin для одного человека** (если хочется быть «и тем и тем») — нужно держать 2 аккаунта. Trade-off в пользу чистоты roles.
- **Новый INTERNAL_JWT_KEY_SYSTEM_CONTROL** — ещё один секрет для ротации в runbook.
- **ADR-0004 §1-§2 теперь устарели** — нужно обновить пользовательскую документацию.

### Neutral / Follow-ups

- Удалённое выполнение команд через `docker.sock` — есть вектор «sudo equivalent через Docker API». Это известный security pattern (Portainer, Watchtower работают так же); важно что (a) sidecar в private network, (b) JWT-аудитория узкая, (c) whitelist команд hard-coded.
- ADR-0007 (на будущее): миграция destructive ops с `docker.sock` на systemd unit с PolicyKit / sudo rules — для prod-deployment.

---

## Alternatives considered

### A. Флаг `is_super_admin BOOLEAN` поверх существующей роли admin

- **Pro**: один человек = и admin (видит реестр) и super (видит /system). Удобно для команды 4 человек.
- **Con**: ослабляет разделение. Кейс «случайно нажал backup-кнопку когда хотел редактировать пользователей» — реален. JWT с одним claim проще проверять, чем (`role`, `is_super_admin`).
- **Why rejected**: Product owner выбрал «one hat at a time» для чистоты audit-trail.

### B. Запуск destructive ops прямо в web-bff с mount'ом docker.sock

- **Pro**: один сервис, нет sidecar'а.
- **Con**: blast-radius. Web-bff публично-доступный (через nginx); compromise = root на хосте через docker.sock.
- **Why rejected**: безопасность.

### C. Запускать ops через SSH-вызов к хосту

- **Pro**: нет mount docker.sock в контейнер.
- **Con**: требует выдать сервису SSH-ключ + sudo rights — ещё хуже с безопасностью.
- **Why rejected**: SSH-эскалация = более широкий attack surface.

### D. Полностью внешний инструмент (Portainer / Komodo)

- **Pro**: готовое решение.
- **Con**: ещё один UI без интеграции с нашим auth/audit. Пользователь хочет именно in-product панель.
- **Why rejected**: отказ от единого UX.

---

## Implementation handoff

5 фаз, mirror'ит pattern admin-channels / exchange-calendar.

### Phase 0 — Documentation

- ✅ ADR-0006 (этот файл)
- `docs/adr/0004-two-tier-administration.md` — пометить §1-§2 как superseded ✅

### Phase 1 — Migrations + Bootstrap CLI

**Owner**: the data layer + backend
- `auth-service/alembic/versions/0006_super_admin_role.py` — extend role CHECK
- `auth_service/scripts/bootstrap_super_admin.py` + Makefile target `make superadmin-create`
- Расширить `check_min_admins.py` → `check_min_super_admins` (отдельный constraint per role)
- Update existing `UserResponse` to include `role: 'super_admin' | 'admin' | 'editor' | 'viewer'` enum

### Phase 2 — Sidecar `lotsman-system-control` + backend ops

**Owner**: backend + ops
- New service `services/system-control/` (FastAPI, 300 LOC)
- Dockerfile, compose.dev.yml entry, healthcheck
- New env `INTERNAL_JWT_KEY_SYSTEM_CONTROL`
- BFF endpoints `/api/v1/system/*`:
  - `GET /system/health` — агрегирует /healthz всех сервисов
  - `GET /system/queues` — Redis Stream depth + ARQ DLQ
  - `GET /system/migrations` — alembic version из каждого сервиса
  - `GET /system/keys` — last-rotation timestamps (нужно фиксировать в БД при ротации; новая таблица `auth.key_rotations` или env-readback)
  - `GET /system/logs?service=&tail=` — proxy в sidecar
  - `POST /system/backup-now` — proxy в sidecar (re-MFA + typed-confirmation)
  - `POST /system/restart-service` — proxy + safety
  - `POST /system/migrate` — proxy + safety
- audit-service: новый endpoint `/api/v1/audit/system` (фильтр по тегу `audit.system_*`)

### Phase 3 — UI

**Owner**: frontend + design
- Новый раздел `/system/*` (видим только super_admin):
  - `/system/health` — главная dashboard карточками
  - `/system/queues` — таблица streams + DLQ
  - `/system/audit` — фильтрованный audit
  - `/system/keys` — таблица ключей (✗ если >90 дней)
  - `/system/migrations` — alembic versions
  - `/system/logs` — log viewer per service
  - `/system/maintenance` — destructive ops с typed-confirmation
- Top-bar role badge с разными цветами
- Conditional rendering nav на основе `claims.role`

### Phase 4 — QA + security review

**Owner**: QA + security + review
- Особый focus на sidecar: pen-test whitelist'а команд, попытки injection
- e2e: super_admin не видит /admin/*; admin не видит /system/*
- MIN_SUPER_ADMINS guard срабатывает
- typed-confirmation предотвращает случайное нажатие
- Stage 4 commit + tag `v1.2.0`

---

## Migration safety analysis

**Что меняется в БД** (миграция `auth 0006`):
1. `ALTER TABLE auth.users DROP/ADD CONSTRAINT users_role_check` — non-destructive (расширяет allowed values).

**Что НЕ трогается**:
- Существующие пользователи (`admin@lotsman.local` остаётся `role='admin'`).
- Все existing `/admin/*` endpoints — поведение не меняется.
- ADR-0004 другие разделы (§3 bootstrap, §4 channels, §5 invite, §6 MIN_ADMINS) — остаются в силе.

**Rollback**:
- `alembic downgrade -1` сужает CHECK обратно. Pre-condition: `DELETE FROM auth.users WHERE role='super_admin'` (иначе CHECK fails). Документируется.

**Совместимость со старыми JWT**: пока никакого super_admin user'а нет — JWT-валидаторы продолжают видеть только `admin/editor/viewer`. После bootstrap первого super_admin'а — старые JWT остаются валидными (role enum расширяется, не сужается).

---

## Quality checklist (перед Phase 1)

- [ ] Product owner подтвердил: «role super_admin взаимоисключающая с admin» (selected — 2026-05-08)
- [ ] Подтверждено: destructive ops в UI с typed-confirmation (selected — 2026-05-08)
- [ ] Solution для exec команд: sidecar `lotsman-system-control` с `docker.sock` mount, узкий whitelist
- [ ] Bootstrap первого super-admin'а — через `make superadmin-create` (новая команда, отдельная от `make admin-create`)
- [ ] MIN_SUPER_ADMINS политика: ≥1 active super-admin (зеркало MIN_ADMINS)
- [ ] runbook обновлён с новой ролью (Phase 0c)

---

_Связано: ADR-0001 (стек), ADR-0002 (границы сервисов), ADR-0003 (auth), ADR-0004 (super-admin/space-admin contours — partial supersede), ADR-0005 (channels)._
