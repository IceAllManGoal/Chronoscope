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
