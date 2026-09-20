# Chronoscope Core

Ядро Chronoscope: принимает сырые события от Agent, нормализует их, хранит и отдаёт через локальный API.

Полное описание проекта, архитектуры и модели событий — в корне репозитория:

- [`../docs/PROJECT_SPEC.md`](../docs/PROJECT_SPEC.md) — главный документ проекта;
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — компоненты, слои, поток данных;
- [`../docs/EVENT_MODEL.md`](../docs/EVENT_MODEL.md) — модель `RawEvent` и `Event`.

## Требования

```text
Python 3.13+
uv
```

## Команды разработки

Все команды выполняются из каталога `core/`.

```powershell
# Создать .venv и установить зависимости (включая dev-группу)
uv sync --all-groups

# Прогнать тесты
uv run pytest

# Применить миграции БД
uv run alembic upgrade head

# Запустить Core
uv run python -m chronoscope
```

## CLI

Пока Core запущен, историю можно смотреть из другого терминала:

```powershell
uv run chronoscope status                              # состояние Core, базы и объёмы данных
uv run chronoscope events                              # список событий
uv run chronoscope events --type process.started       # с фильтрами
uv run chronoscope events --limit 5 --cursor <курсор>  # следующая страница
uv run chronoscope event evt_01K5...                   # подробности одного события
uv run chronoscope doctor                              # диагностика
```

CLI — клиент локального API, а не второй доступ к базе: он обращается к Core по
HTTP и потому не нарушает инвариант 10 (§81). Адрес берётся из того же файла
конфигурации, что читает Core, и может быть переопределён ключом `--core-url`.

Вернуться к состоянию «Core недоступен» легко: `doctor` и `status` возвращают
код 2, если Core не отвечает, и подсказывают команду запуска.

## Страница

Пока Core запущен, история доступна и в браузере: **http://127.0.0.1:7342/ui/**
(корень `/` перенаправляет туда же). Это список событий с фильтрами, пагинацией и
панелью подробностей.

Файлы лежат в `chronoscope/web/` — обычные HTML, CSS и JavaScript, без шага
сборки, и отдаёт их сам Core. Решение и его издержки —
[ADR-0012](../docs/decisions/0012-web-page-served-by-core.md). Статика попадает в
wheel вместе с пакетом, поэтому находится независимо от того, как Core
установлен; если каталога нет, Core остаётся рабочим API, а `/ui` отвечает 404.

Страница — клиент того же API по тому же origin, поэтому CORS и прокси не нужны.

Те же команды — `uv sync --all-groups --locked`, `uv run alembic upgrade head` и `uv run pytest` — прогоняются в CI ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) на каждый pull request и на ветке `main`.

## Конфигурация

Значения по умолчанию заданы в коде, переопределяются TOML-файлом. Путь к файлу — переменная окружения `CHRONOSCOPE_CONFIG`, иначе `chronoscope.toml` в текущем каталоге. Пример — [`chronoscope.example.toml`](chronoscope.example.toml).

Bind на не-loopback адрес отвергается на уровне конфигурации: API обязан слушать только `127.0.0.1` (§33, [ADR-0007](../docs/decisions/0007-local-only-api.md)).

## Структура

```text
chronoscope/
├── api/               HTTP: валидация transport-схемы, вызов use case, ответ
├── application/       use cases: IngestRawEvent, ListEvents, GetEvent
├── domain/            события, сущности, идентификаторы, event types
├── normalization/     raw payload → Normalized Event
├── infrastructure/    конфигурация, SQLAlchemy, SQLite, логирование
├── analytics/         будущая аналитика, в 0.0.1 пусто
└── main.py            точка входа
```

Правила зависимостей между слоями — §50 спеки. Коротко: `domain/` не зависит от FastAPI, SQLAlchemy, SQLite, HTTP и Windows; `api/` не пишет SQL самостоятельно.
