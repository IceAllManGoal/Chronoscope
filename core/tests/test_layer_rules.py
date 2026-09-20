"""Правила зависимостей между слоями (§50), проверяемые механически.

ADR-0008 честно называет слабое место: «границы слоёв поддерживаются дисциплиной:
компилятор не помешает ``api/`` напрямую обратиться к SQLAlchemy». Ревью — не
компилятор, и правило, на котором держится вся ценность слоёв, до сих пор не
проверялось ничем.

§50 требует, чтобы ``domain/`` не зависел от FastAPI, SQLAlchemy, SQLite, HTTP и
Windows, а ``normalization/`` оставался свободен от инфраструктуры — именно
поэтому поиск родителя оформлен портом ``ProcessInstanceLookup``, а не
обращением к репозиторию.

Импорты читаются через ``ast``, а не импортом модулей: импорт поймал бы только
то, что падает в рантайме, и пропустил бы ``import`` внутри функции или под
``TYPE_CHECKING``.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "chronoscope"

#: Слои, которые §50 обязывает держать в стороне от технологий.
TECHNOLOGY_FREE_LAYERS = ("domain", "normalization")

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


def _imported_roots(path: Path) -> set[str]:
    """Верхнеуровневые имена модулей, импортированных файлом."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level != 0 — относительный импорт внутри пакета, он безопасен.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])

    return roots


def test_technology_free_layers_import_nothing_forbidden() -> None:
    """§50: domain/ и normalization/ не знают о фреймворках, СУБД и Windows."""
    checked = 0
    offenders: list[str] = []

    for layer in TECHNOLOGY_FREE_LAYERS:
        for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
            checked += 1
            forbidden = sorted(_imported_roots(path) & set(FORBIDDEN_ROOTS))
            for root in forbidden:
                offenders.append(
                    f"{path.relative_to(PACKAGE_ROOT).as_posix()} -> {root} "
                    f"({FORBIDDEN_ROOTS[root]})"
                )

    # Страховка от теста, который прошёл потому, что ничего не нашёл: ошибка в
    # пути к пакету сделала бы проверку ниже молча зелёной.
    assert checked > 0, "не найдено ни одного файла — тест ничего не проверял"
    assert offenders == [], "§50 нарушен:\n" + "\n".join(offenders)
