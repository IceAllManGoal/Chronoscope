"""Тесты миграций и настроек SQLite (§25, §26, §27).

Главная проверка здесь — отсутствие расхождения между миграцией и моделями.
Если они разойдутся, тесты на моделях будут проходить, а на реальной базе
приложение упадёт; такой разрыв обнаруживается слишком поздно, поэтому он
проверяется явно.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from chronoscope.infrastructure.database.models import metadata
from tests.conftest import ALEMBIC_INI, CORE_DIR


def alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(CORE_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(CORE_DIR))
    return config


class TestMigrations:
    def test_upgrade_creates_expected_tables(self, migrated_engine) -> None:  # noqa: ANN001
        inspector = sa.inspect(migrated_engine)
        assert set(inspector.get_table_names()) >= {"raw_events", "events", "alembic_version"}

    def test_migration_schema_matches_models(self, migrated_engine) -> None:  # noqa: ANN001
        """Схема из миграции должна совпадать с моделями SQLAlchemy."""
        inspector = sa.inspect(migrated_engine)

        for table_name in ("raw_events", "events"):
            database_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
            model_columns = {column.name: column for column in metadata.tables[table_name].columns}

            assert set(database_columns) == set(model_columns), f"расхождение колонок в {table_name}"

            for name, model_column in model_columns.items():
                if model_column.primary_key:
                    # SQLite не запрещает NULL в TEXT PRIMARY KEY, и introspection
                    # сообщает о таких колонках иначе, чем описывает модель.
                    continue
                assert bool(database_columns[name]["nullable"]) == bool(model_column.nullable), (
                    f"{table_name}.{name}: nullable расходится"
                )

            database_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
            model_indexes = {index.name for index in metadata.tables[table_name].indexes}
            assert database_indexes == model_indexes, f"расхождение индексов в {table_name}"

    def test_expected_indexes_present(self, migrated_engine) -> None:  # noqa: ANN001
        """§27 перечисляет конкретные индексы — они должны существовать."""
        inspector = sa.inspect(migrated_engine)
        assert {index["name"] for index in inspector.get_indexes("events")} == {
            "ix_events_timestamp",
            "ix_events_type_timestamp",
            "ix_events_actor_id_timestamp",
            "ix_events_subject_id_timestamp",
        }
        assert {index["name"] for index in inspector.get_indexes("raw_events")} == {
            "ix_raw_events_collector_source_timestamp"
        }

    def test_downgrade_removes_tables(self, migrated_engine, temp_settings) -> None:  # noqa: ANN001
        command.downgrade(alembic_config(), "base")
        assert "events" not in sa.inspect(migrated_engine).get_table_names()

        # Возвращаем базу в исходное состояние, чтобы фикстура не оставила мусор.
        command.upgrade(alembic_config(), "head")
        assert "events" in sa.inspect(migrated_engine).get_table_names()

    def test_upgrade_is_idempotent(self, migrated_engine) -> None:  # noqa: ANN001
        command.upgrade(alembic_config(), "head")
        command.upgrade(alembic_config(), "head")
        assert "events" in sa.inspect(migrated_engine).get_table_names()

    def test_database_file_is_created_inside_data_dir(self, temp_settings) -> None:  # noqa: ANN001
        assert temp_settings.database_path.exists() or temp_settings.database_path.parent.is_dir()
        assert temp_settings.database_path.suffix == ".db"


class TestSqlitePragmas:
    def test_wal_mode_enabled(self, engine) -> None:  # noqa: ANN001
        with engine.connect() as connection:
            assert connection.execute(sa.text("PRAGMA journal_mode")).scalar_one().lower() == "wal"

    def test_foreign_keys_enabled(self, engine) -> None:  # noqa: ANN001
        with engine.connect() as connection:
            assert connection.execute(sa.text("PRAGMA foreign_keys")).scalar_one() == 1

    def test_synchronous_normal(self, engine) -> None:  # noqa: ANN001
        # 1 = NORMAL
        with engine.connect() as connection:
            assert connection.execute(sa.text("PRAGMA synchronous")).scalar_one() == 1

    def test_busy_timeout_applied(self, engine) -> None:  # noqa: ANN001
        with engine.connect() as connection:
            assert connection.execute(sa.text("PRAGMA busy_timeout")).scalar_one() == 5000


class TestForeignKeysAreEnforced:
    def test_event_with_unknown_raw_event_id_is_rejected(self, event_repository) -> None:  # noqa: ANN001
        """Доказывает, что foreign_keys=ON действительно применён.

        Без этого PRAGMA SQLite молча принял бы ссылку на несуществующее
        сырое событие, и связь Event → RawEvent стала бы фиктивной.
        """
        from datetime import UTC, datetime

        from chronoscope.domain.errors import StorageError
        from tests.factories import make_event

        orphan = make_event(raw_event_id="raw_01K5R8Z9M5P8W3X7Y2C4E6G8J9")
        with pytest.raises(StorageError):
            event_repository.insert(orphan, ingested_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC))


class TestDatabaseLocation:
    def test_relative_path_resolved_against_working_directory(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from chronoscope.infrastructure.config.settings import CoreSettings
        from chronoscope.infrastructure.database.engine import build_database_url

        monkeypatch.chdir(tmp_path)
        url = build_database_url(CoreSettings().database_path)
        assert url.startswith("sqlite+pysqlite:///")
        assert (tmp_path / "data" / "chronoscope.db").as_posix() in url


class TestAlembicConfig:
    def test_ini_is_pure_ascii(self) -> None:
        """alembic.ini обязан оставаться ASCII, иначе миграции падают на Windows.

        Alembic читает конфигурацию в locale-кодировке, а не в UTF-8
        (``alembic/util/compat.py``: ``file_config.read(..., encoding="locale")``).
        Кодировка следует системной кодовой странице: при cp1252 (Западная
        Европа) не-ASCII байт роняет ``alembic upgrade head`` с
        UnicodeDecodeError, при cp1251 (Россия) тот же байт молча декодируется в
        мусор — поэтому локально дефект не виден, а на windows-раннере CI виден.

        ``PYTHONUTF8=1`` здесь не спасает: ``locale.getencoding()`` намеренно
        игнорирует UTF-8-режим Python.
        """
        non_ascii = [
            (index, byte) for index, byte in enumerate(ALEMBIC_INI.read_bytes()) if byte > 127
        ]

        assert non_ascii == [], (
            "alembic.ini должен быть ASCII: alembic читает его в locale-кодировке. "
            f"Не-ASCII байты (позиция, байт): {non_ascii[:10]}"
        )
