"""Эндпоинт приёма событий: POST /api/v1/ingest/raw-events (§31, §34, §60).

Поведение при частично невалидном пакете — осознанное решение, и его стоит
зафиксировать явно.

§60 требует, чтобы одно плохое событие не останавливало pipeline. Поэтому
пакет обрабатывается поэлементно: невалидные события попадают в ``rejected``
с указанием позиции и причины, валидные обрабатываются как обычно.

Статус ответа:

```text
200  хотя бы одно событие было принято в обработку (в том числе если все
     оказались дубликатами — это штатный результат at-least-once доставки)
422  ни одно событие пакета не прошло валидацию: сигнал клиенту, что он
     отправляет данные, которые Core не понимает вообще
```
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from chronoscope.api.dependencies import get_ingest_batch
from chronoscope.api.schemas import (
    ErrorOut,
    IngestBatchIn,
    IngestBatchOut,
    RawEventIn,
    RejectedEventOut,
)
from chronoscope.application.ingest.ingest_batch import IngestBatch
from chronoscope.domain.errors import (
    InvalidInputError,
    UnsupportedSchemaVersionError,
)
from chronoscope.domain.events.event_type import SUPPORTED_SCHEMA_VERSION
from chronoscope.domain.events.raw_event import RawEvent

router = APIRouter(tags=["ingest"])

CODE_INVALID_INPUT = "invalid_input"
CODE_UNSUPPORTED_SCHEMA = "unsupported_schema_version"

_MAX_REPORTED_REJECTIONS_IN_MESSAGE = 3


class _RejectedItem(Exception):
    """Внутренний сигнал: конкретное событие пакета не прошло валидацию."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _summarize_validation_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "payload"
    return f"{location}: {first.get('msg', 'не соответствует контракту')}"


def _to_domain(item: dict[str, Any]) -> RawEvent:
    """Проверить транспортную форму и собрать доменный объект.

    Структурные проверки выполняет Pydantic, смысловые — конструктор
    ``RawEvent`` (поддерживаемая версия схемы, форма идентификаторов, UTC).
    Оба вида отказов превращаются в одно понятие «событие отвергнуто», но с
    разными кодами, чтобы клиент понимал, что именно чинить.
    """
    try:
        model = RawEventIn.model_validate(item)
    except ValidationError as exc:
        raise _RejectedItem(code=CODE_INVALID_INPUT, message=_summarize_validation_error(exc)) from exc

    try:
        return RawEvent(
            schema_version=model.schema_version,
            raw_event_id=model.raw_event_id,
            collector=model.collector,
            collector_version=model.collector_version,
            observed_at=model.observed_at,
            source_timestamp=model.source_timestamp,
            host_id=model.host_id,
            boot_id=model.boot_id,
            payload_type=model.payload_type,
            payload=model.payload,
        )
    except UnsupportedSchemaVersionError as exc:
        raise _RejectedItem(code=CODE_UNSUPPORTED_SCHEMA, message=str(exc)) from exc
    except InvalidInputError as exc:
        raise _RejectedItem(code=CODE_INVALID_INPUT, message=str(exc)) from exc


@router.post(
    "/ingest/raw-events",
    response_model=IngestBatchOut,
    responses={422: {"model": ErrorOut}, 413: {"model": ErrorOut}},
    summary="Принять пакет сырых событий",
)
def ingest(
    payload: IngestBatchIn,
    use_case: IngestBatch = Depends(get_ingest_batch),
) -> IngestBatchOut:
    if payload.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            received=payload.schema_version,
            supported=SUPPORTED_SCHEMA_VERSION,
        )

    accepted: list[RawEvent] = []
    rejected: list[RejectedEventOut] = []

    for index, item in enumerate(payload.events):
        try:
            accepted.append(_to_domain(item))
        except _RejectedItem as rejection:
            rejected.append(
                RejectedEventOut(index=index, code=rejection.code, message=rejection.message)
            )

    if not accepted and rejected:
        reasons = "; ".join(f"[{item.index}] {item.message}" for item in rejected[:_MAX_REPORTED_REJECTIONS_IN_MESSAGE])
        raise InvalidInputError(
            f"ни одно из {len(payload.events)} событий пакета не прошло валидацию: {reasons}"
        )

    outcome = use_case.execute(accepted)

    return IngestBatchOut(
        accepted=outcome.accepted,
        duplicates=outcome.duplicates,
        normalization_failed=outcome.normalization_failed,
        rejected=rejected,
    )
