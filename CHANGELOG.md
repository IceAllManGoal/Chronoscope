# Changelog

Все заметные изменения Chronoscope документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [Semantic Versioning](https://semver.org/lang/ru/). Правила версионирования до `1.0.0` описаны в §72 [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md).

## [Unreleased]

### Added

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
