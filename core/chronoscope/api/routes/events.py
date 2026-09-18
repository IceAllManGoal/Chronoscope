"""Эндпоинты запроса событий: GET /api/v1/events и /events/{event_id} (§31, §32)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi import status as http_status

from chronoscope.api.dependencies import get_get_event, get_list_events
from chronoscope.api.schemas import ErrorOut, EventListOut, EventOut
from chronoscope.application.queries.get_event import GetEvent
from chronoscope.application.queries.list_events import ListEvents
from chronoscope.domain.errors import InvalidInputError
from chronoscope.domain.validation import require_utc
from chronoscope.infrastructure.database.repositories import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    EventQuery,
)

router = APIRouter(tags=["events"])


def _as_utc(value: datetime | None, field: str) -> datetime | None:
    """Привести параметр времени к UTC.

    Для параметров запроса смещение допускается и пересчитывается в UTC: это
    преобразование без потери информации. Наивное время отвергается — его
    нельзя истолковать однозначно, а догадка испортила бы границу выборки.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        raise InvalidInputError(
            f"{field}: timestamp должен содержать timezone (например 2026-09-18T10:00:00Z)"
        )
    return require_utc(value.astimezone(UTC), field)


def _split_types(raw: str | None) -> tuple[str, ...]:
    """Разобрать список типов событий, переданный через запятую."""
    if raw is None:
        return ()
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not values:
        raise InvalidInputError("type: список типов событий пуст")
    return values


@router.get(
    "/events",
    response_model=EventListOut,
    responses={422: {"model": ErrorOut}},
    summary="Постраничный список событий",
)
def list_events(
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT, description="Размер страницы"),
    cursor: str | None = Query(None, description="Курсор следующей страницы из предыдущего ответа"),
    type: str | None = Query(None, description="Тип события; несколько значений — через запятую"),  # noqa: A002
    source: str | None = Query(None, description="Источник события, например windows.process"),
    actor_id: str | None = Query(None, description="Идентификатор сущности-актора"),
    subject_id: str | None = Query(None, description="Идентификатор сущности-субъекта"),
    from_: datetime | None = Query(None, alias="from", description="Начало периода, включительно"),
    to: datetime | None = Query(None, description="Конец периода, включительно"),
    use_case: ListEvents = Depends(get_list_events),
) -> EventListOut:
    query = EventQuery(
        limit=limit,
        cursor=cursor,
        types=_split_types(type),
        source=source,
        actor_id=actor_id,
        subject_id=subject_id,
        time_from=_as_utc(from_, "from"),
        time_to=_as_utc(to, "to"),
    )

    page = use_case.execute(query)

    return EventListOut(
        events=[EventOut.from_domain(event) for event in page.events],
        next_cursor=page.next_cursor,
        count=len(page.events),
    )


@router.get(
    "/events/{event_id}",
    response_model=EventOut,
    responses={404: {"model": ErrorOut}},
    summary="Одно событие по идентификатору",
)
def get_event(
    event_id: str = Path(description="Идентификатор события вида evt_<ULID>"),
    use_case: GetEvent = Depends(get_get_event),
) -> EventOut:
    event = use_case.execute(event_id)

    if event is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"событие {event_id!r} не найдено",
        )

    return EventOut.from_domain(event)
