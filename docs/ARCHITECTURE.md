# Архитектура Chronoscope

> Дополняет [`PROJECT_SPEC.md`](PROJECT_SPEC.md) и не заменяет его. Спека — главный документ; здесь собрано то, что нужно при чтении кода: компоненты, слои, поток данных и принятые решения по структуре.

## 1. Компоненты

| Компонент | Роль | Технологии | Статус |
|---|---|---|---|
| **Chronoscope Agent** | Наблюдает за Windows и доставляет сырые события в Core | C# / .NET | 0.0.1, не начат |
| **Chronoscope Core** | Принимает, нормализует, хранит и отдаёт события | Python, FastAPI, SQLAlchemy, Alembic, SQLite | 0.0.1, реализован |
| **Chronoscope UI** | Объясняет историю пользователю | TypeScript, React, Vite | вне 0.0.1 |

Разделение зафиксировано в [ADR-0002](decisions/0002-agent-core-separation.md).

## 2. Поток данных

```text
Windows / Data Sources
        ↓
Collector                 наблюдает
        ↓
RawEvent                  сырое событие в универсальной оболочке
        ↓
Agent Buffer              bounded channel (§34)
        ↓
Core Ingest API           валидация + идемпотентность
        ↓
Raw Store                 SQLite: raw_events
        ↓
Normalizer                структурирует
        ↓
Event                     нормализованная сущность
        ↓
Event Store               SQLite: events
        ↓
Query API                 GET /api/v1/events
```

Ключевой принцип: **сырое событие сохраняется до нормализации** (§11, [ADR-0005](decisions/0005-preserve-raw-events.md)). Поэтому историю можно переобработать новой версией нормализатора, не собирая данные заново.

Архитектурная формула проекта:

> Collectors observe. Normalizers structure. Storage persists. Analytics interprets. UI explains.

## 3. Почему Agent и Core разделены

- **Изоляция платформы.** Agent знает про Windows, Core знает про Chronoscope. Появление `Chronoscope.Agent.Linux` или `.MacOS` не потребует переписывать Core.
- **Изоляция прав.** Некоторым коллекторам понадобятся elevated privileges. Core не обязан работать с такими же правами (§67).
- **Независимое развитие.** Agent оптимизируется под стабильный сбор, Core — под хранение, API и аналитику.
- **Воспроизводимость.** Сохранённые raw events можно прогнать через новую версию Core без повторного сбора.

## 4. Слои Core и правила зависимостей

```text
core/chronoscope/
├── api/               HTTP: валидация transport-схемы, вызов use case, сериализация ответа
├── application/       use cases: IngestRawEvent, ListEvents, GetEvent
├── domain/            события, сущности, идентификаторы, event types
├── normalization/     raw payload → Normalized Event
├── infrastructure/    конфигурация, SQLAlchemy, SQLite, логирование
├── analytics/         будущая аналитика (в 0.0.1 — только README)
└── main.py            точка входа
```

Правила (§50):

- `domain/` **не зависит** от FastAPI, SQLAlchemy, SQLite, HTTP и Windows.
- `application/` реализует use cases и координирует repositories, normalizers и транзакции.
- `infrastructure/` реализует конкретные технологии: SQLite, файловая система, конфигурация, логирование.
- `api/` принимает HTTP, валидирует transport schema, вызывает use case и сериализует ответ. **API не пишет SQL самостоятельно.**

Направление зависимостей:

```text
API → Use Case → Repository
```

Строгая валидация на входе в API: некорректный payload отвергается явно, а не разбирается «как получится» (§52, §60).

### Порт для нормализатора

Нормализатор — чистая функция «сырое событие → нормализованное событие», но одному ему всё же нужен доступ к уже сохранённым данным. Причина конкретная: у события запуска родитель известен только как `parent_pid`, а идентичностью является `process_instance_id`, выводимый из времени старта родителя, которого в событии нет. Значит, родителя приходится искать среди сохранённых событий.

Этот доступ оформлен портом `ProcessInstanceLookup` в `normalization/registry.py`, а не прямой зависимостью от репозитория. Благодаря этому нормализатор тестируется без базы данных, а направление зависимостей остаётся правильным: `normalization/` не знает про SQLAlchemy и SQLite, реализацию порта предоставляет слой хранения.

Если родитель не найден (типичный случай: `explorer.exe` стартовал до начала наблюдения), `actor` остаётся пустым, а в атрибутах появляется `parent_resolved: false`. Подставлять догадку вместо факта модель не позволяет.

## 5. Структура репозитория

```text
Chronoscope/
├── README.md
├── LICENSE                     MIT (ADR-0009)
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── .editorconfig
├── .gitignore
│
├── docs/
│   ├── PROJECT_SPEC.md         главный документ проекта
│   ├── ARCHITECTURE.md         этот файл
│   ├── EVENT_MODEL.md          модель RawEvent и Event
│   └── decisions/              ADR
│
├── core/                       Chronoscope Core
│   ├── chronoscope/
│   ├── migrations/             Alembic
│   ├── tests/
│   └── pyproject.toml
│
├── agent/                      Chronoscope Agent — в 0.0.1 каталоги без кода
│   ├── Chronoscope.Agent.sln   ещё не создан
│   ├── src/
│   │   ├── Chronoscope.Agent/
│   │   └── Chronoscope.Agent.Collectors.Windows/
│   └── tests/
│       └── Chronoscope.Agent.Tests/
│
├── frontend/                   вне 0.0.1
├── shared/
│   ├── schemas/                JSON-схемы контракта v1
│   └── fixtures/               тестовые события
├── scripts/
└── data/                       локальные данные, в git не попадают
```

### Решения по структуре

**Два проекта Agent, а не четыре.** §48 предлагает `Agent`, `Abstractions`, `Transport` и `Collectors.Windows`. В 0.0.1 фиксируется только одна граница — платформенная, потому что она архитектурно несущая: Windows-зависимый код должен быть отделён от платформенно-нейтрального. `Abstractions` и `Transport` при одном коллекторе и одном транспорте выделяются в проекты, когда появится второй коллектор или второй транспорт (§70, §80).

**`docker-compose.yml` отсутствует.** См. [ADR-0010](decisions/0010-no-containerization-in-0.0.1.md).

**Дерево выше — целевая раскладка, а не снимок.** Фактически созданы `docs/`, `core/`, `shared/`, `scripts/reset-dev-data.ps1` и файлы в корне. `agent/` существует только как каталоги (`.gitkeep`), `scripts/dev.ps1` и `scripts/test.ps1` не созданы, `docs/PRIVACY.md` и `docs/DEVELOPMENT.md` отсутствуют. Список расхождений ведётся в §48 [`PROJECT_SPEC.md`](PROJECT_SPEC.md) — критерий качества §76 требует, чтобы спека соответствовала реальной архитектуре.

## 6. Доставка, backpressure и идемпотентность

```text
Collector
   ↓
Bounded Channel       ограниченная очередь, чтобы не расти в памяти без предела
   ↓
Batch Sender
   ↓
Core
```

Модель доставки (§34):

> **at-least-once delivery + идемпотентный ingest**

Agent может повторить отправку после сбоя. Core не создаёт дубликат, потому что `raw_event_id` стабилен, а ingest идемпотентен по этому ключу. В 0.0.1 достаточно in-memory buffer в Agent.

## 7. Хранение

```text
raw_events     сырые события, до нормализации
events         нормализованные события
```

Обе таблицы в SQLite, доступ только через слой репозиториев ([ADR-0004](decisions/0004-sqlite-first.md)). Схема изменяется **только** через Alembic-миграции (§81, инвариант 11).

Индексы в ранних версиях (§27): `events(timestamp)`, `events(type, timestamp)`, `events(actor_id, timestamp)`, `events(subject_id, timestamp)`, `raw_events(collector, source_timestamp)`. Десятки индексов «на будущее» не создаются: каждый занимает место и замедляет insert.

Cold storage (Parquet + DuckDB), retention-политики и агрегаты — вне 0.0.1 (§30, §39).

## 8. API

Локальный и версионированный, префикс `/api/v1` (§31):

```http
GET  /api/v1/health
GET  /api/v1/status
POST /api/v1/ingest/raw-events
GET  /api/v1/events
GET  /api/v1/events/{event_id}
```

Ограничения безопасности (§33, §66, [ADR-0007](decisions/0007-local-only-api.md)):

- bind **только** на `127.0.0.1`, никогда на `0.0.0.0`;
- настраиваемый порт, лимит размера запроса, строгая валидация входных данных;
- нет endpoint'ов для произвольного SQL, выполнения кода и shell;
- путь к БД приходит только из конфигурации, никогда от HTTP-клиента;
- чувствительные данные минимизируются в логах.

Пагинация — cursor-based с курсором из `timestamp` и `event_id`, порядок `ORDER BY timestamp DESC, id DESC` (§32). Глубокий `OFFSET` как долгосрочная стратегия не используется.

## 9. Архитектурные инварианты

Не нарушаются без отдельного ADR (§81):

1. Core не зависит от Windows API.
2. Agent не содержит продуктовой аналитики.
3. Raw events сохраняются до потери source-specific информации.
4. Normalized Event имеет версию схемы.
5. PID не является идентичностью process instance.
6. Исторические events immutable.
7. API по умолчанию local-only.
8. Cloud не является обязательной зависимостью.
9. Analytics строится поверх событий, а не вместо них.
10. UI не читает SQLite напрямую.
11. Database schema изменяется только через migrations.
12. Collectors не знают о UI.
13. Один сломанный event не останавливает pipeline.
14. Chronoscope должен быть полезен без AI.

## 10. Что за пределами 0.0.1

Кроме перечисленного в §74 спеки, в архитектуре пока отсутствуют и появятся позже:

- correlation engine и модель relationships (§22, §23);
- enrichment процесса: publisher, digital signature, file hash (§21);
- retention и холодное хранилище (§30, §39);
- CLI (§57);
- plugin SDK (§46);
- privacy filter и redaction между коллектором и envelope (§38);
- аутентификация между Agent и Core: локальный shared secret (§33).
