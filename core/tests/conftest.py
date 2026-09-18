"""Общие фикстуры и пути к контрактным артефактам.

Тесты Core проверяются против тех же файлов, что использует Agent:
``shared/schemas/`` и ``shared/fixtures/`` (§51, §58).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chronoscope.infrastructure.config.settings import (
    ENV_CONFIG_PATH,
    CoreSettings,
    load_settings,
)
from chronoscope.infrastructure.database.engine import create_database_engine
from chronoscope.infrastructure.database.repositories import (
    EventRepository,
    RawEventRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = Path(__file__).resolve().parents[1]
SHARED_DIR = REPO_ROOT / "shared"
SCHEMAS_DIR = SHARED_DIR / "schemas"
FIXTURES_DIR = SHARED_DIR / "fixtures"
ALEMBIC_INI = CORE_DIR / "alembic.ini"


def load_fixture(*parts: str) -> dict[str, Any]:
    """Загрузить фикстуру из shared/fixtures по частям пути."""
    path = FIXTURES_DIR.joinpath(*parts)
    if not path.is_file():
        raise FileNotFoundError(f"фикстура не найдена: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMAS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"схема не найдена: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def process_start_payload() -> dict[str, Any]:
    return load_fixture("windows", "process_start_001.json")


@pytest.fixture
def process_exit_payload() -> dict[str, Any]:
    return load_fixture("windows", "process_exit_001.json")


@pytest.fixture
def process_start_missing_path_payload() -> dict[str, Any]:
    return load_fixture("windows", "process_start_missing_path.json")


# ── База данных ──────────────────────────────────────────────────────


@pytest.fixture
def temp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CoreSettings:
    """Конфигурация с временной БД.

    Путь передаётся через тот же механизм, что и в реальной работе
    (``CHRONOSCOPE_CONFIG``), поэтому тесты проверяют и загрузку конфигурации,
    и миграции, и приложение — без обходных путей.
    """
    database_path = tmp_path / "chronoscope.db"
    config_file = tmp_path / "chronoscope.toml"
    config_file.write_text(
        f'[storage]\ndatabase_path = "{database_path.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_CONFIG_PATH, str(config_file))
    return load_settings()


@pytest.fixture
def engine(temp_settings: CoreSettings):  # noqa: ANN201 - тип Engine из SQLAlchemy
    database_engine = create_database_engine(temp_settings)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


@pytest.fixture
def migrated_engine(temp_settings: CoreSettings):  # noqa: ANN201
    """Engine с базой, к которой применены миграции Alembic."""
    from alembic import command
    from alembic.config import Config

    alembic_config = Config(str(ALEMBIC_INI))
    alembic_config.set_main_option("script_location", str(CORE_DIR / "migrations"))
    alembic_config.set_main_option("prepend_sys_path", str(CORE_DIR))
    command.upgrade(alembic_config, "head")

    database_engine = create_database_engine(temp_settings)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


@pytest.fixture
def raw_repository(migrated_engine) -> RawEventRepository:  # noqa: ANN001
    return RawEventRepository(migrated_engine)


@pytest.fixture
def event_repository(migrated_engine) -> EventRepository:  # noqa: ANN001
    return EventRepository(migrated_engine)
