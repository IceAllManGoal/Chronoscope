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

**0.0.2 — выпущена.** 0.0.1 доказала, что вертикальный срез работает целиком; 0.0.2 сделала поток событий видимым — простой CLI и базовая локальная страница, обе части §77 «See». Это по-прежнему не MVP продукта, а работающий вертикальный срез:

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
   ↓
CLI `chronoscope …`  и  страница http://127.0.0.1:7342/ui/   — появились в 0.0.2
```

Целевой сценарий 0.0.1 (§75), пройденный вживую: запустить Core, запустить Agent, открыть и закрыть `notepad.exe`, получить через API события `process.started` и `process.exited`, пережить перезапуск Core без потери истории и безопасно обработать повторно отправленный `RawEvent`.

Состояние на сегодня:

| Часть | Состояние |
|---|---|
| Структура, документация, ADR | готово |
| JSON-схемы контракта и фикстуры | готово |
| **Chronoscope Core**: приём, нормализация, хранение, API | **готово**, 301 тест |
| **Chronoscope Agent**: `ProcessCollector`, доставка в Core | **готово**, 97 тестов. Ограничение: полнота наблюдения не гарантирована — [`agent/README.md`](agent/README.md) |
| CLI и локальная страница — 0.0.2 «See» (§77) | готово: `uv run chronoscope …` и http://127.0.0.1:7342/ui/ |
| Frontend на стеке §55 | не начато: минимальный интерфейс отдаёт сам Core ([ADR-0012](docs/decisions/0012-web-page-served-by-core.md)), стек понадобится к 0.1.0 «Timeline» |

Сценарий §75 пройден целиком и проверен вживую, с настоящим Agent: запуск Core и `/health`, запуск Agent, запуск и закрытие `notepad.exe`, выдача `process.started` и `process.exited` **для одного и того же экземпляра** процесса (`subject.id` совпал), перезапуск Core без потери истории, повторная отправка `RawEvent` без дубликата. В 0.0.2 тот же путь пройден снова, уже вместе с интерфейсами: Agent наблюдал запуск и завершение `notepad.exe`, `subject.id` совпал, а CLI и страница показали те же события.

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
├── frontend/      будущий UI на стеке §55 — к 0.1.0; первый интерфейс отдаёт Core
├── shared/        JSON-схемы контракта и фикстуры
├── scripts/       dev-скрипты
└── data/          локальные данные, в git не попадают
```

## Требования

```text
Windows
Git
Python 3.13+
.NET SDK 8+     — для Agent
PowerShell
```

Docker не требуется: Chronoscope — локальный продукт, а Agent обязан быть Windows-native процессом.

## Документация

| Документ | О чём |
|---|---|
| [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) | Главный продуктовый и технический документ проекта |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Компоненты, слои, поток данных, структура репозитория |
| [`docs/EVENT_MODEL.md`](docs/EVENT_MODEL.md) | Модель `RawEvent` и `Event`, идентификаторы, время, версионирование схем |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | Что собирается, где хранится, что **не** защищено |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Окружение, тесты, CI, правила слоёв, выпуск версии |
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

Посмотреть историю в браузере: **http://127.0.0.1:7342/ui/** — базовая страница со
списком событий, фильтрами и панелью подробностей. Её отдаёт сам Core, поэтому
Node и сборка не нужны ([ADR-0012](docs/decisions/0012-web-page-served-by-core.md)).

Запустить Agent — второй терминал (§63). Он читает тот же файл конфигурации, что и
Core, и берёт адрес из его секции `[core]`:

```powershell
dotnet run --project agent/src/Chronoscope.Agent.Host
```

Точка входа — `Chronoscope.Agent.Host`, а не `Chronoscope.Agent`: композиционный
корень вынесен в отдельный проект, см. [ADR-0011](docs/decisions/0011-agent-project-structure.md).

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

## CLI

То же самое, но читаемо, из третьего терминала (пока Core запущен):

```powershell
cd core
uv run chronoscope status                          # состояние Core, базы и объёмы данных
uv run chronoscope events                          # список событий
uv run chronoscope events --type process.started   # с фильтрами
uv run chronoscope event evt_01K5...               # подробности одного события
uv run chronoscope doctor                          # диагностика
```

CLI обращается к Core по HTTP, а не к базе напрямую, и берёт адрес из того же
файла конфигурации, что и Core (§36).

## Разработка

```powershell
pwsh scripts/dev.ps1     # поднять Core и Agent: окружение, миграции, два окна (§63)
pwsh scripts/test.ps1    # тесты Core и Agent одной командой (§58)
```

Те же команды вручную, без скриптов:

```powershell
cd core
uv run pytest        # 301 тест: домен, хранилище, нормализатор, контракт, правила слоёв, приём, CLI, страница, скрипты, интеграция

cd ../agent
dotnet test Chronoscope.Agent.sln   # 97 тестов: ULID, контракт, конфигурация, буфер, отправка, маппинг процессов
```

Оба набора прогоняются в CI на каждый pull request. Правила участия описаны в [`CONTRIBUTING.md`](CONTRIBUTING.md), политика безопасности — в [`SECURITY.md`](SECURITY.md), процесс разработки — в [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md), команды разработки Core — в [`core/README.md`](core/README.md), Agent — в [`agent/README.md`](agent/README.md).

## Лицензия

[MIT](LICENSE) — см. [ADR-0009](docs/decisions/0009-mit-license.md).
