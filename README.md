<div align="center">

# 🧭 Лоцман

**Реестр договоров, лицензий и сроков — который сам помнит о дедлайнах.**

![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg) ![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg) ![React 19](https://img.shields.io/badge/React-19-61dafb.svg) ![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791.svg) ![self-hosted](https://img.shields.io/badge/self--hosted-on--premise-2D3F50.svg)

![Лоцман — главный экран: реестр документов с цветными маркерами сроков, фильтрами по колонкам и экспортом в Excel](docs/assets/lotsman-registry.png)

</div>

> 🇬🇧 **English (TL;DR).** Лоцман ("Lotsman") is a **self-hosted** registry for partner-company documents (contracts, licenses, audit reports) that **tracks renewal deadlines and reminds the right people automatically** — via Email, Telegram, and Dion, and by **publishing every deadline straight into calendars**: a shared Microsoft Exchange/Outlook calendar and a personal iCalendar (ICS) subscription that works with any calendar app. It replaces a fragile shared Excel file with a multi-user, **fully audited**, **MFA-protected** app that runs entirely on your own server. The UI and user guide are in Russian. Licensed under **MPL-2.0**.

---

## Зачем он нужен

Реестр договоров и лицензий обычно живёт в Excel — и это больно: о сроках вспоминают, когда уже просрочено; файл одновременно правят несколько человек и теряют версии; кто что менял — непонятно; данные расползаются по почте.

**Лоцман убирает эту боль.** Он хранит документы по контрагентам, **сам напоминает** ответственным о приближении сроков — за N дней, в день срока и повторно при просрочке, — фиксирует **каждое** изменение и работает **на вашем сервере**. Источник правды — база данных, а не файл.

## Почему не «просто Excel» (и не облако)

| Боль с Excel-реестром | Как в Лоцмане |
|---|---|
| О сроках вспоминают постфактум | Автонапоминания: Email · Telegram · Dion |
| Дедлайнов не видно в рабочем календаре | Сроки сами попадают в календарь: общий Exchange + персональная ICS-подписка (Outlook · Google · Apple) |
| «Кто это поменял и когда?» | Полный аудит: кто, когда, было → стало |
| Версии файла, конфликты правок | Многопользовательская БД — один источник правды |
| Нет разграничения доступа | Роли admin / editor / viewer + обязательный 2FA |
| Данные в чужом облаке | Self-hosted: данные не покидают ваш контур |

## Что умеет

- 📋 **Привычно, как Excel.** Виртуализированная таблица: закреплённые шапки, цветные маркеры сроков (🔴 просрочено · 🟡 скоро · 🟢 ок), правка по двойному клику, быстрый поиск. Учиться нечему.
- ⏰ **Сам следит за сроками.** Напоминания pre-notice / в день / повтор при просрочке, настраиваются на тип документа; уходят ответственным и подписчикам.
- 📨 **Доставка туда, где люди.** Email (SMTP/EWS), Telegram, Dion.
- 📅 **Сроки прямо в календаре.** Дедлайны публикуются в общий календарь Microsoft Exchange (события встают рядом со встречами, срабатывают штатные напоминания Outlook) — или раздаются персональной ICS-ссылкой: её можно подписать в любом календаре (Outlook · Google · Apple), лента обновляется сама и работает, даже если Exchange недоступен.
- 🔐 **Безопасность по умолчанию.** argon2id, обязательный TOTP (2FA) для всех ролей, короткоживущие токены, RBAC.
- 🧾 **Ничего не теряется.** Append-only журнал аудита — каждое изменение зафиксировано.
- 📤 **Экспорт в .xlsx.** Выгрузка текущего (отфильтрованного) вида одним кликом.

## Кому подойдёт

Небольшим командам (≈2–10 человек) — юристам, комплаенсу, АХО, закупкам — которые ведут реестр договоров / лицензий / аудиторских отчётов по контрагентам и хотят уйти от Excel и ручных напоминаний, **не отдавая данные в облако**.

## Быстрый старт (5 минут)

Нужны **Docker 26+** (Compose v2), **uv**, **pnpm**.

```bash
git clone https://github.com/MaximilianKaufmannCode/lotsman.git
cd lotsman
cp .env.example .env      # заполните значения — всё описано внутри
make dev
```

`make dev` соберёт образы, поднимет контейнеры, применит миграции и зальёт демо-данные. Затем откройте:

| URL | Что |
|---|---|
| http://localhost:5173 | Веб-интерфейс |
| http://localhost:8000 | API (web-bff) |
| http://localhost:8025 | Mailpit — ловит всю почту в dev |

Прод-развёртывание (Nginx + TLS, генерация секретов) — **[docs/deployment/](docs/deployment/README.md)**.

## Архитектура

Пять backend-сервисов на FastAPI + шлюз **web-bff**, SPA на React 19, PostgreSQL и Redis — всё в Docker Compose, on-premise. Чистая архитектура внутри каждого сервиса, звёздная топология через BFF, транзакционный outbox → Redis Streams → аудит.

![Архитектура Лоцмана](docs/architecture/lotsman-logic.png)

Подробнее: [docs/architecture/](docs/architecture/README.md) · [ADR](docs/adr/README.md).

## Стек

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · PostgreSQL 16 · Redis 7 · ARQ · React 19 · TypeScript · Vite · TanStack · Tailwind · shadcn/ui.

## Документация

| | |
|---|---|
| 🚀 Развёртывание | [docs/deployment/](docs/deployment/README.md) |
| 🏗 Архитектура · ADR | [docs/architecture/](docs/architecture/README.md) · [docs/adr/](docs/adr/README.md) |
| 🔌 API | [docs/api/](docs/api/) |
| 🗄 База данных | [docs/db/](docs/db/README.md) |
| 📖 Руководство пользователя | [docs/user-guide/](docs/user-guide/) |
| 🔒 Безопасность | [SECURITY.md](SECURITY.md) |
| 🤝 Участие | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 📝 История изменений | [CHANGELOG.md](CHANGELOG.md) |

## Безопасность и приватность

Self-hosted: данные и секреты остаются в вашем контуре. Обязательный TOTP для всех ролей, argon2id, RBAC, единая точка проверки доступа (web-bff), append-only аудит. О найденных уязвимостях сообщайте приватно — см. [SECURITY.md](SECURITY.md).

## Участие и лицензия

PR и issue приветствуются — см. [CONTRIBUTING.md](CONTRIBUTING.md) и [Code of Conduct](CODE_OF_CONDUCT.md).
Лицензия — **[Mozilla Public License 2.0](LICENSE)**.
