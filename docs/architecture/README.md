# Архитектурные диаграммы Лоцман

Три **D2 + ELK** диаграммы в стиле **TALA** (см. `TALA_User_Manual.pdf` от Terrastruct). Каждая отвечает на отдельный вопрос — не пытайтесь понять всё с одной картинки.

| # | Диаграмма | Отвечает на вопрос | Аудитория | Время чтения |
|---|---|---|---|---|
| 1 | [`lotsman-logic.svg`](./lotsman-logic.svg) | **Из чего собрана система?** Какие у неё контейнеры, кто с кем связан? | архитектор, новый разработчик, devops | 2–3 мин |
| 2 | [`lotsman-traffic.svg`](./lotsman-traffic.svg) | **Как HTTPS-запрос проходит через систему?** Где TLS, где JWT, где RBAC, где REJECT? | security, network engineer | 3–4 мин |
| 3 | [`lotsman-data.svg`](./lotsman-data.svg) | **Где живут данные? Как событие из write попадает в audit / notification?** | DBA, backend-разработчик, аудитор | 3–4 мин |

## Принципы дизайна (TALA / Tufte / Gestalt)

1. **Один аспект — одна диаграмма.** Не смешиваем «что есть» / «как идёт трафик» / «как живут данные».
2. **Hub vs node.** Опорные узлы (Nginx, web-bff, hub-сервисы, storage) получают `shadow + bold + radius:10`. Остальные — без тени. Глаз сразу находит «якоря».
3. **Edge-classes семантичны** (8 классов в `lotsman-logic`, 7 в `traffic`, 7 в `data`). Каждый цвет/толщина = одна категория связи. Чёрно-серое не используем — глазу нечего различать.
4. **Embedded legend.** Каждый файл несёт свою легенду на канвасе (TALA §6.3) — не нужно искать «что значит фиолетовый».
5. **Tooltip ≫ inline label.** Узел = короткое имя + 1-строчный label. Детали (TTL, поля БД, retry-policy) — в `tooltip:`. Скачайте SVG, откройте в браузере, наведите мышь — всё увидите.
6. **Explicit width/height для hub-узлов** (TALA §6.1 / §3.12). ELK без подсказок размеров делает несимметричный layout.
7. **Эмодзи в заголовках кластеров** — быстрый визуальный якорь для скана глазом.
8. **Animated edges** на критических асинхронных потоках (`async-event`, `consume`) — драматизирует «движение».

## Файлы

```
docs/architecture/
├── README.md                      ← этот файл
├── lotsman-logic.d2 / .svg / .png    ← #1 Logical components
├── lotsman-traffic.d2 / .svg / .png  ← #2 Network traffic flow
└── lotsman-data.d2 / .svg / .png     ← #3 Data ownership + flow
```

## Команды рендера

```bash
# d2 уже установлен в ~/.local/bin (если нет, см. ниже)
export PATH="$HOME/.local/bin:$PATH"

cd docs/architecture
for f in lotsman-logic lotsman-traffic lotsman-data; do
  d2 -l elk -t 4 --pad 60 "$f.d2" "$f.svg"   # источник правды (vector + tooltips)
  d2 -l elk -t 4 --pad 60 "$f.d2" "$f.png"   # для презентаций
done

# Live-preview во время правок (открывает браузер, авто-рефреш)
d2 -l elk -t 4 lotsman-logic.d2 --watch
```

### Установка D2 (без sudo)

```bash
curl -fsSL https://d2lang.com/install.sh | sh -s -- --prefix="$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"
d2 --version   # проверка
```

### Тема

`-t 4` — Origami (TALA-friendly: тёплые сглаженные тона, хорошо различимые с цветными edge-classes). Для чёрно-белой печати используйте `-t 200` (Terminal grayscale).

### Layout engine

ELK (open-source). Платный TALA дал бы +15% качества, но не критично — наши схемы сделаны под ELK с подсказками `width`/`height`/`direction` в кластерах.

## Что НЕ показано (намеренно)

| Категория | Почему | Где искать |
|---|---|---|
| Конкретные библиотеки | загрязняет картинку, есть в tooltip | `pyproject.toml` каждого сервиса · ADR-0001 §Стек |
| Структура кода (domain/application/...) | это L4 (Code) — за рамками контейнерных диаграмм | `services/<svc>/src/<svc>/` · `the project conventions` §3 |
| Все таблицы Postgres | ушло в `tooltip:` на схему | миграции `services/<svc>/alembic/versions/` |
| Все 27 use cases registry-service | излишний шум | `requirements/registry-crud.md` (US-1..US-26) |
| Полная политика безопасности | это политика, не структура | ADR-0003 §Decision · the security review |

## История

- **2026-05-07 v3** — три TALA-стиле диаграммы (`logic` / `traffic` / `data`) с edge-classes и легендой
- **2026-05-07 v2** — три C4 диаграммы (Context / Containers / EventFlow); удалены — слишком академично
- **2026-05-06 v1** — монолитная `lotsman-components.d2` (30+ узлов, перегружена)

---

_Связано: ADR-0001 (стек), ADR-0002 (границы сервисов), ADR-0003 (auth)._
