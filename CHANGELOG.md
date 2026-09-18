# Changelog

Все заметные изменения Chronoscope документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [Semantic Versioning](https://semver.org/lang/ru/). Правила версионирования до `1.0.0` описаны в §72 [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md).

## [Unreleased]

### Added

- **Chronoscope Core — первый работающий вертикальный срез** (§90 шаги 5–12):
  - доменный слой: `RawEvent`, `Event`, `EntityRef`, ULID-идентификаторы, детерминированный `process_instance_id`, таксономия ошибок §60;
  - хранилище: SQLite с WAL, `foreign_keys=ON`, `synchronous=NORMAL`, таблицы `raw_events` и `events`, индексы §27, Alembic-миграция;
  - репозитории с идемпотентной вставкой (§34), cursor-пагинацией (§32) и поиском экземпляра процесса;
  - `ProcessNormalizer` и реестр нормализаторов;
  - use cases приёма и запросов, API на FastAPI: health, status, ingest, events, event detail;
  - 252 теста: домен, идентификаторы, хранилище, миграции, нормализатор, контракт против `shared/schemas`, интеграционные сценарии.
- Конфигурация Core в TOML (§36) с запретом bind вне loopback на уровне настроек (ADR-0007).
- Структурированное JSON-логирование (§35).
- `core/README.md`, `core/chronoscope.example.toml`, `core/alembic.ini`.

### Changed

- `docs/EVENT_MODEL.md`: зафиксированы атрибуты `process_started_at` и `parent_resolved`, добавленные нормализатором; идентификаторы в примерах приведены к значениям, которые действительно выдаёт формула вывода.
- `shared/fixtures/events/*`: `subject.id`, `actor.id` и атрибуты приведены к фактическому выводу нормализатора — фикстуры стали проверяемым ожиданием, а не иллюстрацией.

- Каркас монорепозитория: структура каталогов, `.editorconfig`, `.gitignore`, `.gitkeep`-заготовки.
- `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`.
- `docs/ARCHITECTURE.md` — компоненты, слои Core, поток данных, инварианты.
- `docs/EVENT_MODEL.md` — модель `RawEvent` и `Event`, идентификаторы, модель времени, версионирование схем.
- ADR 0001–0010 в `docs/decisions/`, шаблон ADR и индекс.
- JSON-схемы контракта v1 в `shared/schemas/`: `raw-event`, `ingest-batch`, `event`.
- Фикстуры Windows process events в `shared/fixtures/`.
- `scripts/reset-dev-data.ps1`, `scripts/README.md`.

### Changed

- `docs/Chronoscope_PROJECT_SPEC.md` перенесён в `docs/PROJECT_SPEC.md` (§89).
- Каталог `backend/` переименован в `core/`: имя компонента (**Core**, §9 и §82) и путь к его коду теперь совпадают.
- В `SECURITY.md` указан резервный канал для сообщений об уязвимостях; снята пометка о невключённом Private Vulnerability Reporting.

## [0.0.1] — не выпущена

Первый работающий вертикальный срез: Windows process event проходит путь от операционной системы до query API. Состав версии — §73 [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md), критерии качества — §76.
