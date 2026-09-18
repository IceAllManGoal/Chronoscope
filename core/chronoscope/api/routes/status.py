"""Эндпоинт состояния данных: GET /api/v1/status (§31, §57)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from chronoscope.api.dependencies import CoreContainer, get_container, get_get_core_status
from chronoscope.api.routes.health import check_database
from chronoscope.api.schemas import (
    EventCountsOut,
    RawEventCountsOut,
    StatusOut,
)
from chronoscope.application.queries.core_status import GetCoreStatus

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusOut, summary="Состояние Core и объёмы данных")
def status(
    container: CoreContainer = Depends(get_container),
    use_case: GetCoreStatus = Depends(get_get_core_status),
) -> StatusOut:
    snapshot = use_case.execute()

    return StatusOut(
        core="running",
        version=snapshot.version,
        database=check_database(container),
        database_path=snapshot.database_path,
        database_size_bytes=snapshot.database_size_bytes,
        events=EventCountsOut(total=snapshot.events_total, last_minute=snapshot.events_last_minute),
        raw_events=RawEventCountsOut(total=snapshot.raw_events_total),
        # §31 предполагает в ответе состояние Agent. В 0.0.1 Core его не
        # отслеживает, поэтому поле честно null, а не выдуманное «unknown».
        agent=None,
    )
