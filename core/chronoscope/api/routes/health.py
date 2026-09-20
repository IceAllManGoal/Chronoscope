"""Эндпоинт состояния Core: GET /api/v1/health (§31)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from chronoscope.api.dependencies import CoreContainer, get_container
from chronoscope.api.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut, summary="Состояние Core и БД")
def health(container: CoreContainer = Depends(get_container)) -> HealthOut:
    database = container.database_state()
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=container.version,
        database=database,
    )
