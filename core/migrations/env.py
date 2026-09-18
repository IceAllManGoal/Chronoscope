"""Окружение Alembic для Chronoscope Core.

Путь к БД берётся из конфигурации Chronoscope через тот же код, что и в
приложении (:func:`chronoscope.infrastructure.config.settings.load_settings`).
Так миграции физически не могут примениться к другой базе, чем та, с которой
работает Core, — а это ровно тот класс ошибок, который иначе обнаруживается
уже после потери данных.

``render_as_batch=True`` включает batch-режим для SQLite: без него многие
изменения схемы (например, изменение типа колонки или добавление ограничения)
в будущих миграциях стали бы невозможны, потому что SQLite не поддерживает
ALTER COLUMN.
"""

from __future__ import annotations

from alembic import context

from chronoscope.infrastructure.config.settings import load_settings
from chronoscope.infrastructure.database.engine import create_database_engine
from chronoscope.infrastructure.database.models import metadata

config = context.config
target_metadata = metadata


def _settings():  # noqa: ANN202 - тип возвращается из конфигурации
    return load_settings()


def run_migrations_offline() -> None:
    """Сгенерировать SQL без подключения к базе."""
    settings = _settings()
    context.configure(
        url=str(settings.database_path),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применить миграции к реальной базе."""
    settings = _settings()
    engine = create_database_engine(settings)

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
