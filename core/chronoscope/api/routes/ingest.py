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

Код ошибки при полном отказе зависит от причины, и это не деталь оформления.
§52 требует отличать неподдерживаемую версию схемы (несовместимость версий
Agent и Core) от обычного дефекта данных (§60), поэтому пакет, отвергнутый
целиком **только** из-за версии схемы, отвечает ``unsupported_schema_version``,
а не обезличенным ``invalid_input``: иначе один и тот же дефект описывался бы
по-разному в зависимости от размера пакета.
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
    ChronoscopeError,
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
    """Внутренний сигнал: конкретное событие пакета не прошло валидацию.

    ``error`` хранит исходное исключение домена там, где оно несёт информацию,
    которую нельзя восстановить из текста: у ``UnsupportedSchemaVersionError``
    это полученная и поддерживаемая версии. Если пакет отвергнут целиком, наружу
    возвращается именно оно, а не пересказ в виде ``invalid_input``.
    """

    def __init__(self, *, code: str, message: str, error: ChronoscopeError | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.error = error


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
        raise _RejectedItem(
            code=CODE_UNSUPPORTED_SCHEMA, message=str(exc), error=exc
        ) from exc
    except InvalidInputError as exc:
        raise _RejectedItem(code=CODE_INVALID_INPUT, message=str(exc)) from exc


def _batch_error(rejected: list[tuple[int, _RejectedItem]], total: int) -> ChronoscopeError:
    """Собрать ошибку на весь пакет: ни одно событие не принято.

    Если единственная причина отказа — версия схемы, наружу уходит
    ``UnsupportedSchemaVersionError``: §52 требует отвергать такую версию явно,
    а §60 — различать категории отказов, и обезличенный ``invalid_input`` здесь
    терял бы ровно то различение, которого спека требует.

    Смешанные причины сводятся к ``invalid_input``: назвать одну из них значило
    бы соврать о содержимом пакета, а перечислять их в коде ошибки — превращать
    машинный код в текст.
    """
    if all(item.code == CODE_UNSUPPORTED_SCHEMA for _, item in rejected):
        versions: list[object] = []
        for _, item in rejected:
            received = getattr(item.error, "received", None)
            if received not in versions:
                versions.append(received)
        return UnsupportedSchemaVersionError(
            received=versions[0] if len(versions) == 1 else versions,
            supported=SUPPORTED_SCHEMA_VERSION,
        )

    reasons = "; ".join(
        f"[{index}] {item.message}"
        for index, item in rejected[:_MAX_REPORTED_REJECTIONS_IN_MESSAGE]
    )
    return InvalidInputError(f"ни одно из {total} событий пакета не прошло валидацию: {reasons}")


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
    rejected: list[tuple[int, _RejectedItem]] = []

    for index, item in enumerate(payload.events):
        try:
            accepted.append(_to_domain(item))
        except _RejectedItem as rejection:
            rejected.append((index, rejection))

    if not accepted and rejected:
        raise _batch_error(rejected, len(payload.events))

    outcome = use_case.execute(accepted)

    return IngestBatchOut(
        accepted=outcome.accepted,
        duplicates=outcome.duplicates,
        normalization_failed=outcome.normalization_failed,
        rejected=[
            RejectedEventOut(index=index, code=item.code, message=item.message)
            for index, item in rejected
        ],
    )
