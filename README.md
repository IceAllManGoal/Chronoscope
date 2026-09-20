# Chronoscope

[![CI](https://github.com/IceAllManGoal/Chronoscope/actions/workflows/ci.yml/badge.svg)](https://github.com/IceAllManGoal/Chronoscope/actions/workflows/ci.yml)

**Локальная история компьютера, которую можно исследовать.**

Chronoscope — local-first система наблюдения за историей работы компьютера. Её задача — не показывать, что происходит прямо сейчас (для этого есть Task Manager), а сохранять контекст происходившего во времени, чтобы можно было вернуться к нужному моменту и ответить на вопросы:

- Что происходило на компьютере в 18:42?
- Какие процессы запускались перед зависанием?
- Что изменилось между двумя датами?
- Какой процесс породил другой процесс?
- Когда приложение впервые начало вести себя необычно?

Главный вопрос продукта: **«Что происходило с моей системой и что изменилось?»**

## Чем Chronoscope не является

Не антивирус. Не EDR/SIEM. Не средство удалённого администрирования. Не screen recorder. Не keylogger. Не AI-first продукт — AI не является основой и не требуется для работы.

## Статус

**0.0.1 — в разработке.** Это не MVP продукта, а первый полностью работающий архитектурный вертикальный срез:

```text
Windows
   ↓
ProcessCollector  (C# Agent)
   ↓
RawEvent
   ↓
Chronoscope Core  (Python)
   ↓
SQLite (raw events)
   ↓
ProcessNormalizer
   ↓
Event
   ↓
GET /api/v1/events
```

Целевой сценарий версии: запустить Core, запустить Agent, открыть и закрыть `notepad.exe`, получить через API события `process.started` и `process.exited`, пережить перезапуск Core без потери истории и безопасно обработать повторно отправленный `RawEvent`.

Состояние на сегодня:

| Часть | Состояние |
|---|---|
| Структура, документация, ADR | готово |
| JSON-схемы контракта и фикстуры | готово |
| **Chronoscope Core**: приём, нормализация, хранение, API | **готово**, 256 тестов |
| `Chronoscope Agent` и `ProcessCollector` | не начато — без него нет реального сбора из Windows |
| Frontend | не начато (вне 0.0.1) |

Проверено вживую: `POST /api/v1/ingest/raw-events` с фикстурой, выдача `process.started` и `process.exited` для одного и того же экземпляра процесса, отсутствие дубликатов при повторной отправке, сохранение истории после перезапуска Core (§75).

## Чем Chronoscope является

**Local-first** — данные остаются на компьютере, работа без аккаунта, подписки и обязательного интернет-соединения.

**Event-first** — ядро строится вокруг события, а не вокруг процесса, Windows или отдельного API.

**Raw-before-normalized** — сырое событие сохраняется до нормализации, поэтому историю можно переобработать, не собирая её заново.

**Privacy by default** — API слушает только loopback, потенциально чувствительные поля отключаемы, экспорт выполняется явно.

## Структура репозитория

```text
Chronoscope/
├── docs/          документация, спека и ADR
├── core/          Chronoscope Core (Python / FastAPI)
├── agent/         Chronoscope Agent (C# / .NET)
├── frontend/      будущий UI — вне 0.0.1
├── shared/        JSON-схемы контракта и фикстуры
├── scripts/       dev-скрипты
└── data/          локальные данные, в git не попадают
```

## Требования

```text
Windows
Git
Python 3.13+
.NET SDK 8+     — понадобится только для Agent
PowerShell
```

Docker не требуется: Chronoscope — локальный продукт, а Agent обязан быть Windows-native процессом.

## Документация

| Документ | О чём |
|---|---|
| [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) | Главный продуктовый и технический документ проекта |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Компоненты, слои, поток данных, структура репозитория |
| [`docs/EVENT_MODEL.md`](docs/EVENT_MODEL.md) | Модель `RawEvent` и `Event`, идентификаторы, время, версионирование схем |
| [`docs/decisions/`](docs/decisions/) | Architecture Decision Records |

## Быстрый старт

```powershell
cd core

# 1. Окружение и зависимости
uv sync --all-groups

# 2. Схема базы (Core не применяет миграции сам — см. инвариант 11 спеки)
uv run alembic upgrade head

# 3. Запуск
uv run python -m chronoscope
```

Core слушает `127.0.0.1:7342`. Проверка:

```powershell
curl http://127.0.0.1:7342/api/v1/health
```

Отправить тестовые события из фикстуры:

```powershell
curl.exe -X POST http://127.0.0.1:7342/api/v1/ingest/raw-events `
  -H "Content-Type: application/json" `
  --data-binary "@shared/fixtures/ingest/ingest_batch_001.json"
```

Получить события:

```powershell
curl "http://127.0.0.1:7342/api/v1/events?type=process.started"
```

| Эндпоинт | Назначение |
|---|---|
| `GET /api/v1/health` | Состояние Core и базы |
| `GET /api/v1/status` | Объёмы данных, размер базы |
| `POST /api/v1/ingest/raw-events` | Приём пакета сырых событий |
| `GET /api/v1/events` | Список событий с фильтрами и курсором |
| `GET /api/v1/events/{event_id}` | Одно событие |

Интерактивная документация API — `http://127.0.0.1:7342/docs`.

## Разработка

```powershell
cd core

uv run pytest        # 256 тестов: домен, хранилище, нормализатор, контракт, правила слоёв, интеграция
uv run pytest -q tests/test_api_integration.py
```

Правила участия описаны в [`CONTRIBUTING.md`](CONTRIBUTING.md), политика безопасности — в [`SECURITY.md`](SECURITY.md), команды разработки Core — в [`core/README.md`](core/README.md).

## Лицензия

[MIT](LICENSE) — см. [ADR-0009](docs/decisions/0009-mit-license.md).
