"""Начальная схема Chronoscope (§26, §27).

Миграция написана явным DDL, а не через ``metadata.create_all``: миграция
обязана быть воспроизводимой и читаемой сама по себе, независимо от того,
как модель выглядит в текущей версии кода. Иначе изменение модели задним
числом меняло бы смысл уже применённой миграции.

Соответствие моделям проверяется тестом ``tests/test_migrations.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("collector", sa.Text(), nullable=False),
        sa.Column("collector_version", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("source_timestamp", sa.Text(), nullable=True),
        sa.Column("host_id", sa.Text(), nullable=False),
        sa.Column("boot_id", sa.Text(), nullable=True),
        sa.Column("payload_type", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Text(), nullable=False),
        sa.Column("boot_id", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("actor_name", sa.Text(), nullable=True),
        sa.Column("subject_type", sa.Text(), nullable=True),
        sa.Column("subject_id", sa.Text(), nullable=True),
        sa.Column("subject_name", sa.Text(), nullable=True),
        sa.Column("attributes_json", sa.Text(), nullable=False),
        sa.Column("raw_event_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_events.id"]),
    )

    op.create_index("ix_events_timestamp", "events", ["timestamp"])
    op.create_index("ix_events_type_timestamp", "events", ["type", "timestamp"])
    op.create_index("ix_events_actor_id_timestamp", "events", ["actor_id", "timestamp"])
    op.create_index("ix_events_subject_id_timestamp", "events", ["subject_id", "timestamp"])
    op.create_index(
        "ix_raw_events_collector_source_timestamp",
        "raw_events",
        ["collector", "source_timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_events_collector_source_timestamp", table_name="raw_events")
    op.drop_index("ix_events_subject_id_timestamp", table_name="events")
    op.drop_index("ix_events_actor_id_timestamp", table_name="events")
    op.drop_index("ix_events_type_timestamp", table_name="events")
    op.drop_index("ix_events_timestamp", table_name="events")
    op.drop_table("events")
    op.drop_table("raw_events")
