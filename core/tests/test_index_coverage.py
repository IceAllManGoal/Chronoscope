"""Покрытие сортировки списка событий индексами (§27, §32).

§32 требует стабильный порядок ``timestamp DESC, id DESC``. Проверить это можно
двумя способами: посмотреть на состав индексов или на план запроса. Первое
слабее — индекс с нужным составом ещё не значит, что SQLite им воспользуется,
поэтому проверяется план **того самого** запроса, которым пользуется репозиторий:
``build_list_statement`` — общая точка для репозитория и этого теста, а не копия
запроса в тесте, которая рано или поздно разошлась бы с оригиналом.

Если сортировка не покрыта, SQLite сообщает об этом прямо:
``USE TEMP B-TREE FOR LAST TERM OF ORDER BY``. Именно это и было до изменения,
и потому тест умеет падать, а не просто зеленеть.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from chronoscope.application.ports import EventQuery
from chronoscope.infrastructure.database.repositories import build_list_statement

EXPECTED_INDEX_COLUMNS = {
    "ix_events_timestamp": ["timestamp", "id"],
    "ix_events_type_timestamp": ["type", "timestamp", "id"],
    "ix_events_actor_id_timestamp": ["actor_id", "timestamp", "id"],
    "ix_events_subject_id_timestamp": ["subject_id", "timestamp", "id"],
}

CURSOR = "eyJ2IjoxLCJ0IjoiMjAyNi0wOS0yMFQwOTowNzozOC42ODUxMjNaIiwiaSI6ImV2dF8wMU0yWjE0RFhCNjJSNTlOOVpXQTREWFZHMSJ9"

QUERIES = {
    "страница без фильтров": EventQuery(),
    "фильтр по типу": EventQuery(types=("process.started",)),
    "фильтр по актору": EventQuery(actor_id="proc_01M2Z14CB6MJPWF61TWVAZRWYV"),
    "фильтр по субъекту": EventQuery(subject_id="proc_01M2Z14CHXGNZ00H6AG7CDNHK1"),
    "страница по курсору": EventQuery(cursor=CURSOR),
}


def query_plan(engine: Engine, query: EventQuery) -> list[str]:
    """План выполнения того запроса, который строит репозиторий."""
    statement = build_list_statement(query)
    sql = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))

    with engine.connect() as connection:
        rows = connection.execute(sa.text("EXPLAIN QUERY PLAN " + sql)).fetchall()

    # plan-строка лежит в последней колонке; формат EXPLAIN стабилен по числу колонок,
    # но брать её по индексу 3 значило бы зависеть от версии SQLite.
    return [str(row[-1]) for row in rows]


@pytest.mark.parametrize("name", list(QUERIES))
def test_sorting_is_covered_by_index(migrated_engine, name: str) -> None:  # noqa: ANN001
    """В плане не должно быть временного B-tree: сортировка обязана быть покрыта."""
    plan = query_plan(migrated_engine, QUERIES[name])

    assert plan, "план пуст — проверка ничего не проверила"
    assert not any("TEMP B-TREE" in line for line in plan), (
        f"{name}: сортировка не покрыта индексом, SQLite строит временное дерево:\n"
        + "\n".join(plan)
    )
    assert any("USING INDEX" in line or "USING COVERING INDEX" in line for line in plan), (
        f"{name}: план не использует индекс вовсе:\n" + "\n".join(plan)
    )


def test_event_indexes_include_ordering_key(migrated_engine) -> None:  # noqa: ANN001
    """Состав индексов совпадает с сортировкой, а не только имена."""
    inspector = sa.inspect(migrated_engine)

    for name, expected in EXPECTED_INDEX_COLUMNS.items():
        index = next(
            (item for item in inspector.get_indexes("events") if item["name"] == name),
            None,
        )
        assert index is not None, f"индекс {name} не найден"
        assert index["column_names"] == expected, (
            f"{name}: состав {index['column_names']} не совпадает с сортировкой {expected}"
        )


def test_migration_keeps_index_names(migrated_engine) -> None:  # noqa: ANN001
    """Миграция пересоздаёт индексы под теми же именами (§27, ruleset)."""
    inspector = sa.inspect(migrated_engine)
    assert {index["name"] for index in inspector.get_indexes("events")} == set(EXPECTED_INDEX_COLUMNS)
