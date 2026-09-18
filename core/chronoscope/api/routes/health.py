"""Эндпоинт состояния Core: GET /api/v1/health (§31)."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from chronoscope.api.dependencies import CoreContainer, get_container
from chronoscope.api.schemas import HealthOut
from chronoscope.infrastructure.database.engine import missing_tables

router = APIRouter(tags=["health"])


def check_database(container: CoreContainer) -> str:
    """Проверить, что БД доступна и схема применена.

    Ответ «database: ok» обязан означать выполненную проверку, а не факт
    успешного запуска процесса: иначе health-эндпоинт сообщал бы о здоровье
    системы, ничего о ней не зная.

    Различаются два разных отказа: недоступная база (``error``) и доступная
    база без применённых миграций (``schema_missing``). Во втором случае
    соединение работает и ``SELECT 1`` проходит, поэтому без отдельной
    проверки схемы Core отрапортовал бы «ok» при полностью нерабочем API.
    """
    try:
        with container.engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:  # noqa: BLE001 - любая ошибка доступа означает недоступную БД
        return "error"

    if missing_tables(container.engine):
        return "schema_missing"

    return "ok"


@router.get("/health", response_model=HealthOut, summary="Состояние Core и БД")
def health(container: CoreContainer = Depends(get_container)) -> HealthOut:
    database = check_database(container)
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=container.version,
        database=database,
    )
