# Руководство по развёртыванию

Лоцман рассчитан на работу **on-premise / в закрытой сети**, за обратным прокси
с TLS. Это руководство описывает развёртывание на одном хосте через Docker
Compose — этого достаточно для небольших команд, на которые ориентирован продукт.

Образы запускаются **по тегу** из заранее собранного релиза (без сборки на хосте):
`infra/compose.prod.yml` ссылается на `lotsman/<сервис>:${LOTSMAN_VERSION}` и не
содержит ни одной секции `build:`.

> Для локальной разработки используйте `make dev` — см.
> [README](../../README.md#быстрый-старт-5-минут).

> **Реальная среда эксплуатации — podman, а не docker.** PreProd/прод работают на
> rootful **podman 5.4.2 + podman-compose**, с host-networking и загрузочной
> персистентностью через systemd. Команды ниже даны для `docker compose`; под
> podman замените рантайм на `podman compose` (флаги совпадают). Эксплуатационный
> слой описан в [`preprod-runbook.md`](preprod-runbook.md) и
> [`infra/systemd/README.md`](../../infra/systemd/README.md) — обязательно прочтите
> их перед прод-развёртыванием.

## 1. Требования

- Linux-хост с **Docker 26+** и Compose v2, **либо** **podman 5.x +
  podman-compose** (фактическая среда эксплуатации — см. врезку выше).
- DNS-имя, указывающее на хост (например, `lotsman.example.com`), и TLS-сертификат
  для него (Let's Encrypt или корпоративный УЦ).
- Исходящий доступ к вашему SMTP/EWS-серверу и, если используются, к Telegram Bot
  API и Dion API.

## 2. Получить код

```bash
git clone https://github.com/MaximilianKaufmannCode/lotsman.git
cd lotsman
```

## 3. Сгенерировать секреты

**Секреты не коммитятся.** Генерируйте их на хосте. Канонический источник команд
с пояснениями по каждому ключу и каденции ротации —
[`infra/secrets-dev/README.md`](../../infra/secrets-dev/README.md).

Минимальный набор для прод-стека:

**RS256-пара** для подписи внешних (пользовательских) access-токенов — auth-service
подписывает, остальные сервисы проверяют:

```bash
mkdir -p infra/secrets
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out infra/secrets/jwt-private.pem
openssl pkey -pubout -in infra/secrets/jwt-private.pem -out infra/secrets/jwt-public.pem
```

**Случайные значения** для остальных секретов:

```bash
openssl rand -hex 32   # INTERNAL_JWT_SECRET (внутренний JWT web-bff <-> бэкенды)
openssl rand -hex 32   # GRAFANA_ADMIN_PASSWORD (если используете observability-оверлей)
# По одному паролю на каждую служебную роль Postgres:
openssl rand -hex 32   # AUTH_PG_PASSWORD
openssl rand -hex 32   # REGISTRY_PG_PASSWORD
openssl rand -hex 32   # NOTIFICATION_PG_PASSWORD
openssl rand -hex 32   # AUDIT_PG_PASSWORD
openssl rand -hex 32   # POSTGRES_PASSWORD (суперпользователь контейнера postgres)
```

**Fernet-ключи** (две разные пары, ключи **обязаны различаться**):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # TOTP_ENC_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CHANNEL_ENC_KEY
```

`CHANNEL_ENC_KEY` шифрует конфиги каналов (SMTP/Telegram/Dion) в БД;
notification-service **не стартует** без него. `TOTP_ENC_KEY` шифрует TOTP-секреты
пользователей. Их совпадение смешало бы два независимых домена шифрования — не
допускается.

> Пароли служебных ролей Postgres нужно задать вручную после старта контейнера
> (роли создаются без пароля скриптом `01-schemas-and-roles.sql`). Процедура —
> в [`infra/secrets-dev/README.md` §4](../../infra/secrets-dev/README.md).

## 4. Настроить `.env`

```bash
cp .env.example .env
```

Откройте `.env` и задайте как минимум:

| Переменная | Назначение |
|---|---|
| `LOTSMAN_VERSION` | semver-тег релиза; подставляется во все `image:` (см. шаг 5) |
| `POSTGRES_PASSWORD` | пароль суперпользователя контейнера postgres (шаг 3) |
| `AUTH_PG_PASSWORD`, `REGISTRY_PG_PASSWORD`, `NOTIFICATION_PG_PASSWORD`, `AUDIT_PG_PASSWORD` | пароли служебных ролей; синхронны с `*_DATABASE_URL` (шаг 3) |
| `INTERNAL_JWT_SECRET` | секрет внутреннего JWT между сервисами (шаг 3) |
| `TOTP_ENC_KEY` | Fernet-ключ шифрования TOTP-секретов (шаг 3) |
| `CHANNEL_ENC_KEY` | Fernet-ключ шифрования конфигов каналов; обязателен (шаг 3) |
| `GRAFANA_ADMIN_PASSWORD` | пароль admin Grafana, если поднимаете observability (шаг 8) |
| `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` | пути к ключам **внутри контейнера** (по умолчанию `/run/secrets/jwt_private_key` / `/run/secrets/jwt_public_key` — см. шаг 5) |
| Учётные данные SMTP / Telegram / Dion | для каналов, которые включаете |

Каждая переменная из `.env.example` снабжена комментарием. Исключение —
`WEB_BFF_URL` (публичный URL для deep-link в письмах и календарных событиях): это
**compose-переменная**, заданная только у `notification-svc` со значением по
умолчанию `https://lotsman.example.com`. Чтобы переопределить её, экспортируйте
`WEB_BFF_URL` в окружении перед `up` или допишите строку в `.env`.

## 5. Запустить стек и применить миграции

**Образы должны быть собраны заранее** (CI или `make build`) и опубликованы под
тегом `lotsman/<сервис>:${LOTSMAN_VERSION}`. `compose.prod.yml` ничего не собирает.
Перед запуском **обязательно** задайте `LOTSMAN_VERSION` — без него теги
резолвятся в `lotsman/<сервис>:` и `up`/`pull` падает.

```bash
LOTSMAN_VERSION=<тег> docker compose -f infra/compose.prod.yml --env-file .env up -d

# Миграции по всем четырём сервисам:
for s in auth registry notification audit; do
  docker compose -f infra/compose.prod.yml run --rm ${s}-svc alembic upgrade head
done
```

> **Digest-pinning (рекомендуется для прода).** После успешной сборки замените
> `:${LOTSMAN_VERSION}` на `@sha256:<digest>` в `compose.prod.yml` — см. комментарий
> в шапке файла и операционный runbook.

> **make-цели.** `make up` (требует заданного `LOTSMAN_VERSION`) и `make build`
> инкапсулируют те же команды для прод-compose. Цели `make migrate` / `make seed`
> / `make admin-create` / `make superadmin-create` нацелены на **dev-compose** —
> для прод-стека пользуйтесь сырыми командами `docker compose -f
> infra/compose.prod.yml …`, как показано выше и в шаге 7.

### Доставка JWT-ключей в контейнеры — pre-launch gap

`.env.example` по умолчанию указывает на ключи как на Docker secrets
(`/run/secrets/jwt_private_key`, `/run/secrets/jwt_public_key`). Однако
`compose.prod.yml` в этом репозитории **не содержит** ни top-level блока
`secrets:`, ни bind-mount этих `.pem` в сервисы — то есть файлы, сгенерированные в
`infra/secrets/`, сами по себе никуда не монтируются. Перед запуском в проде
доставку ключей нужно настроить явно (Docker secrets либо bind-mount по путям из
`.env`), синхронно с `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH`. Команды
генерации и пояснения — в
[`infra/secrets-dev/README.md` §1](../../infra/secrets-dev/README.md).

## 6. Обратный прокси и TLS

`compose.prod.yml` уже включает сервис `nginx` (`nginx:1.27-alpine`, порты 80/443) —
это единственный ingress; бэкенд-сервисы наружу не публикуются. Он завершает TLS,
отдаёт SPA и проксирует `/api/*` на `web-bff`. Конфигурация монтируется из
[`infra/nginx/`](../../infra/nginx/): `nginx.conf`, `conf.d/`, а сертификаты — из
[`infra/nginx/certs/`](../../infra/nginx/certs/) в `/etc/nginx/certs`.

Положите сертификат и ключ как `infra/nginx/certs/lotsman.crt` и
`infra/nginx/certs/lotsman.key` (имена заданы в `conf.d/lotsman.conf`).

> Если вы предпочитаете завершать TLS на **своём внешнем прокси** (как на PreProd —
> там ingress’ом служит host nginx), отключите встроенный сервис `nginx` и
> проксируйте на `web-bff`. Не смешивайте оба сценария.

## 7. Первый администратор

Первого администратора создают bootstrap-цели — bootstrap-скрипт **не** описан в
README `auth-service` (там только эндпоинты и события):

```bash
# Space-admin:
docker compose -f infra/compose.prod.yml exec -T auth-svc \
  python -m auth_service.scripts.bootstrap_admin --email admin@org.local --full-name "Иван Петров"

# Super-admin:
docker compose -f infra/compose.prod.yml exec -T auth-svc \
  python -m auth_service.scripts.bootstrap_super_admin --email super@org.local --full-name "Super Admin"
```

В dev те же действия доступны как `make admin-create EMAIL=… FULL_NAME="…"` и
`make superadmin-create …` (они используют dev-compose). Модель ролей —
[ADR-0004](../adr/0004-two-tier-administration.md) (двухуровневое
администрирование); обмен enrollment-тикетом при первом входе —
[ADR-0008](../adr/0008-first-login-enrollment-ticket-exchange.md). При первом входе
пользователь завершает обязательную привязку TOTP; MFA обязательна для всех ролей.

## 8. Наблюдаемость (опционально)

```bash
docker compose -f infra/compose.observability.yml --env-file .env up -d
```

Поднимает Prometheus, Grafana, Loki и Promtail (то же — `make obs-up`). Из `.env`
оверлей читает единственную переменную — `GRAFANA_ADMIN_PASSWORD` (по умолчанию
`changeme`). **Смените её перед запуском** и ограничьте доступ к этим инструментам
только операторами.

## 9. Резервные копии

- **PostgreSQL.** В репозитории есть готовый валидируемый бэкап:
  [`scripts/backup-pg.sh`](../../scripts/backup-pg.sh) +
  [`infra/cron.d/lotsman-backup`](../../infra/cron.d/lotsman-backup) (ежедневно в
  03:00, хранение 14 дней). Скрипт проверяет каждый дамп (gzip, минимальный размер,
  заголовок кластера, наличие всех схем и `_app`-ролей, объём данных), атомарно
  публикует `latest-good.sql.gz`, пишет метрику node_exporter
  (`lotsman_pg_backup_success`) и **падает громко** при сбое — «тихий» пустой
  бэкап невозможен. Восстановление — `scripts/restore-pg.sh` (живая цель требует
  явного флага). Подробности и acceptance-проверки — в
  [`preprod-runbook.md`](preprod-runbook.md). Журнал аудита — append-only и
  партиционирован по месяцам; в дамп включаются все схемы.
- **Секреты.** Отдельно и безопасно бэкапьте JWT-пару и прочие ключи; потеря
  приватного ключа аннулирует все выданные токены.

## 10. Загрузочная и crash-персистентность (прод)

Compose сам по себе не переживает перезагрузку хоста: на PreProd именно отсутствие
загрузочной персистентности однажды привело к тому, что после ребута `/api/*`
несколько дней отвечал 502, пока nginx отдавал статичный SPA. Реально работающий
прод-механизм — systemd-юниты поверх podman:

- [`infra/systemd/README.md`](../../infra/systemd/README.md) — `lotsman.service`
  (старт пода на boot), `lotsman-watchdog.timer` (само-восстановление раз в 2 мин),
  установка юнитов и acceptance-gate.
- [`preprod-runbook.md`](preprod-runbook.md) — топология хоста, типовые операции,
  процедура бэкапа/восстановления, отложенные follow-up’ы.

> **Проверка живости.** Healthcheck’и в compose обращаются к `localhost:8000` —
> это совпадает с внутренним портом сервисов в `compose.prod.yml`, поэтому здесь
> они корректны. На PreProd (host-networking, порты 8001–8080) тот же probe даёт
> ложный `unhealthy` — там критерий живости один: ответ **web-bff на
> `:8080/healthz`** (см. follow-up #3 в runbook).

## Обновление

```bash
git pull
# Соберите/опубликуйте образы под новым тегом (CI или `make build`), затем:
LOTSMAN_VERSION=<новый_тег> docker compose -f infra/compose.prod.yml --env-file .env up -d
for s in auth registry notification audit; do
  docker compose -f infra/compose.prod.yml run --rm ${s}-svc alembic upgrade head
done
```

Перед обновлением через MAJOR-версию изучите `CHANGELOG.md` на предмет ломающих
изменений.

---

_Last updated: 2026-06-25_
