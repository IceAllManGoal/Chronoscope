"""Правила зависимостей между слоями (§50), проверяемые механически.

ADR-0008 честно называет слабое место: «границы слоёв поддерживаются дисциплиной:
компилятор не помешает ``api/`` напрямую обратиться к SQLAlchemy». Ревью — не
компилятор, и правило, на котором держится вся ценность слоёв, до сих пор не
проверялось ничем.

Проверяются три вещи:

1. ``domain/`` и ``normalization/`` не зависят от FastAPI, SQLAlchemy, SQLite, HTTP
   и Windows — именно поэтому поиск родителя оформлен портом
   ``ProcessInstanceLookup``, а не обращением к репозиторию;
2. ``api/`` не пишет SQL самостоятельно: §50 говорит это прямо, а первым шагом к
   «самостоятельно» всегда оказывается импорт ``sqlalchemy`` в обработчике;
3. ``application/`` и ``api/`` не зависят от ``infrastructure`` — кроме
   композиционного корня, который для того и существует, чтобы соединять
   инфраструктуру с use cases. Пока `application/` импортировал
   `infrastructure.database.repositories`, направление зависимости было обратным,
   и подменить хранение можно было только вместе с правкой use case.

Импорты читаются через ``ast``, а не импортом модулей: импорт поймал бы только
то, что падает в рантайме, и пропустил бы ``import`` внутри функции или под
``TYPE_CHECKING``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "chronoscope"

#: Слои, которые §50 обязывает держать в стороне от технологий.
TECHNOLOGY_FREE_LAYERS = ("domain", "normalization")

#: Слои, которые не должны знать о слое технологий вообще.
INFRASTRUCTURE_FREE_LAYERS = ("application", "api")

#: Единственное место в api/, которому инфраструктура видна: композиционный
#: корень. Он собирает адаптеры и передаёт их use cases — без него соединять
#: было бы негде, и «слои» остались бы только на бумаге.
COMPOSITION_ROOT = "api/dependencies.py"

#: Запрещённые зависимости и то, чем они являются по §50.
FORBIDDEN_ROOTS = {
    "fastapi": "HTTP-фреймворк",
    "starlette": "HTTP-фреймворк",
    "uvicorn": "HTTP-сервер",
    "httpx": "HTTP-клиент",
    "requests": "HTTP-клиент",
    "http": "HTTP",
    "sqlalchemy": "слой хранения",
    "alembic": "миграции",
    "sqlite3": "конкретная СУБД",
    "pydantic": "валидация транспорта",
    "win32api": "Windows API",
    "win32con": "Windows API",
    "win32file": "Windows API",
    "win32security": "Windows API",
    "pywintypes": "Windows API",
    "wmi": "Windows API",
}

#: Работа с хранилищем, которую слой API не имеет права вести сам (§50).
STORAGE_ROOTS = {
    "sqlalchemy": "SQLAlchemy",
    "sqlite3": "SQLite",
    "alembic": "миграции",
}

INFRASTRUCTURE_PREFIX = "chronoscope.infrastructure"


def _imported_modules(source: str) -> list[str]:
    """Полные имена модулей, импортированных исходником."""
    tree = ast.parse(source)
    modules: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level != 0 — относительный импорт внутри пакета, он безопасен.
            if node.level == 0 and node.module:
                modules.append(node.module)

    return modules


def _imported_roots(source: str) -> set[str]:
    """Верхнеуровневые имена модулей, импортированных исходником."""
    return {module.split(".")[0] for module in _imported_modules(source)}


def _infrastructure_imports(source: str) -> list[str]:
    """Импорты слоя инфраструктуры из исходника."""
    return [
        module
        for module in _imported_modules(source)
        if module == INFRASTRUCTURE_PREFIX or module.startswith(f"{INFRASTRUCTURE_PREFIX}.")
    ]


def _layer_files(layer: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


# ── Проверка самих проверок ──────────────────────────────────────────
#
# Правило, которое не может сработать, — это не правило, а комментарий.
# Детекторы проверяются на заведомо нарушающем исходнике: так «зелёный» тест
# отличается от теста, который ничего не проверял.


def test_detector_finds_technology_import() -> None:
    assert _imported_roots("import sqlalchemy\n") & set(FORBIDDEN_ROOTS) == {"sqlalchemy"}
    assert _imported_roots("from sqlalchemy.orm import Session\n") & set(FORBIDDEN_ROOTS) == {"sqlalchemy"}
    assert _imported_roots("from win32api import GetTickCount\n") & set(FORBIDDEN_ROOTS) == {"win32api"}


def test_detector_finds_infrastructure_import() -> None:
    assert _infrastructure_imports(
        "from chronoscope.infrastructure.database.repositories import EventRepository\n"
    ) == ["chronoscope.infrastructure.database.repositories"]
    assert _infrastructure_imports(
        "from chronoscope.infrastructure import logging\n"
    ) == ["chronoscope.infrastructure"]
    assert _infrastructure_imports("from chronoscope.application.ports import EventQuery\n") == []


def test_detector_is_not_fooled_by_relative_import() -> None:
    """Относительный импорт внутри слоя — не зависимость от инфраструктуры."""
    assert _imported_modules("from . import ports\n") == []


# ── Правила ──────────────────────────────────────────────────────────


def test_technology_free_layers_import_nothing_forbidden() -> None:
    """§50: domain/ и normalization/ не знают о фреймворках, СУБД и Windows."""
    checked = 0
    offenders: list[str] = []

    for layer in TECHNOLOGY_FREE_LAYERS:
        for path in _layer_files(layer):
            checked += 1
            source = path.read_text(encoding="utf-8")
            forbidden = sorted(_imported_roots(source) & set(FORBIDDEN_ROOTS))
            for root in forbidden:
                offenders.append(
                    f"{path.relative_to(PACKAGE_ROOT).as_posix()} -> {root} "
                    f"({FORBIDDEN_ROOTS[root]})"
                )

    # Страховка от теста, который прошёл потому, что ничего не нашёл: ошибка в
    # пути к пакету сделала бы проверку ниже молча зелёной.
    assert checked > 0, "не найдено ни одного файла — тест ничего не проверял"
    assert offenders == [], "§50 нарушен:\n" + "\n".join(offenders)


def test_api_does_not_talk_to_storage() -> None:
    """§50: ни API, ни application не обращаются к хранилищу сами.

    Композиционный корень — исключение по той же причине, что и в правиле об
    инфраструктуре: он собирает адаптеры, и создание движка базы — часть сборки.
    Обработчики, схемы и use cases этого права не имеют.
    """
    checked = 0
    offenders: list[str] = []

    for layer in ("application", *INFRASTRUCTURE_FREE_LAYERS):
        for path in _layer_files(layer):
            relative = path.relative_to(PACKAGE_ROOT).as_posix()
            if relative == COMPOSITION_ROOT:
                continue

            checked += 1
            for root in sorted(_imported_roots(path.read_text(encoding="utf-8")) & set(STORAGE_ROOTS)):
                offenders.append(f"{relative} -> {root} ({STORAGE_ROOTS[root]})")

    assert checked > 0, "не найдено ни одного файла — тест ничего не проверял"
    assert offenders == [], (
        "§50 нарушен: обращение к хранилищу в обход репозиториев\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("layer", INFRASTRUCTURE_FREE_LAYERS)
def test_layer_does_not_import_infrastructure(layer: str) -> None:
    """§50: application/ и api/ не зависят от слоя технологий, кроме композиционного корня."""
    checked = 0
    offenders: list[str] = []

    for path in _layer_files(layer):
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        if relative == COMPOSITION_ROOT:
            continue

        checked += 1
        for module in _infrastructure_imports(path.read_text(encoding="utf-8")):
            offenders.append(f"{relative} -> {module}")

    assert checked > 0, f"не найдено ни одного файла в {layer}/ — тест ничего не проверял"
    assert offenders == [], (
        f"§50 нарушен: {layer}/ зависит от infrastructure\n" + "\n".join(offenders)
    )
