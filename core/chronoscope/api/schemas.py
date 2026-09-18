"""Transport-схемы API (§31, §52).

Здесь описана форма данных на границе HTTP. Она **зеркалит**
``shared/schemas/*.schema.json`` — тот самый контракт, против которого
тестируется и Agent (§51). Дублирование неизбежно (Pydantic нужен FastAPI для
документации и валидации), поэтому расхождение ловится контрактным тестом:
``tests/test_contract_schemas.py`` проверяет, что обе реализации одинаково
принимают и отвергают одни и те же фикстуры.

Проверка на этом уровне — только структурная. Семантику (поддерживаемую версию
схемы, форму идентификатора с нужным префиксом, UTC) проверяет домен, поэтому
``schema_version`` объявлен обычным целым, а не ``Literal[1]``: иначе
неподдерживаемая версия выглядела бы как обычная ошибка валидации, и §52
(«явно отклонять неподдерживаемую схему») остался бы невыполненным.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from chronoscope.domain.events.entity_ref import MAX_NAME_LENGTH, EntityRef
from chronoscope.domain.events.event import Event
from chronoscope.domain.ids import (
    PREFIX_BOOT,
    PREFIX_EVENT,
    PREFIX_HOST,
    PREFIX_PROCESS_INSTANCE,
    PREFIX_RAW_EVENT,
    prefixed_id_pattern,
)
from chronoscope.domain.timestamps import format_utc
from chronoscope.domain.validation import (
    COLLECTOR_NAME_RE,
    EVENT_TYPE_RE,
    PAYLOAD_TYPE_RE,
)

#: Совпадает с maxItems в shared/schemas/ingest-batch.schema.json.
MAX_BATCH_EVENTS = 1000

RawEventId = Annotated[str, Field(pattern=prefixed_id_pattern(PREFIX_RAW_EVENT))]
EventId = Annotated[str, Field(pattern=prefixed_id_pattern(PREFIX_EVENT))]
HostId = Annotated[str, Field(pattern=prefixed_id_pattern(PREFIX_HOST))]
BootId = Annotated[str, Field(pattern=prefixed_id_pattern(PREFIX_BOOT))]
ProcessInstanceId = Annotated[str, Field(pattern=prefixed_id_pattern(PREFIX_PROCESS_INSTANCE))]
CollectorName = Annotated[str, Field(pattern=COLLECTOR_NAME_RE.pattern, max_length=128)]
EventTypeName = Annotated[str, Field(pattern=EVENT_TYPE_RE.pattern, max_length=128)]
PayloadTypeName = Annotated[str, Field(pattern=PAYLOAD_TYPE_RE.pattern, max_length=64)]


# ── Входящие данные ──────────────────────────────────────────────────


class RawEventIn(BaseModel):
    """Сырое событие в форме shared/schemas/raw-event.schema.json (§12)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    raw_event_id: RawEventId
    collector: CollectorName
    collector_version: str = Field(min_length=1, max_length=64)
    observed_at: datetime
    source_timestamp: datetime | None = None
    host_id: HostId
    boot_id: BootId | None = None
    payload_type: PayloadTypeName
    payload: dict[str, Any]


class IngestBatchIn(BaseModel):
    """Envelope POST /api/v1/ingest/raw-events (§31).

    События принимаются как словари, а не как ``list[RawEventIn]``: так ошибка
    в одном событии не отвергает весь пакет. §60 требует, чтобы одно плохое
    событие не останавливало pipeline, а валидация силами Pydantic на уровне
    списка вернула бы единый отказ на весь батч.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    events: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_BATCH_EVENTS)


# ── Ответы ───────────────────────────────────────────────────────────


class EntityRefOut(BaseModel):
    type: str
    id: str
    name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)

    @classmethod
    def from_domain(cls, ref: EntityRef) -> "EntityRefOut":
        return cls(type=ref.type, id=ref.id, name=ref.name)


class EventOut(BaseModel):
    """Нормализованное событие в форме shared/schemas/event.schema.json (§13)."""

    model_config = ConfigDict(frozen=True)

    schema_version: int
    id: EventId
    timestamp: datetime
    observed_at: datetime | None
    type: EventTypeName
    source: CollectorName
    host_id: HostId
    boot_id: BootId | None
    actor: EntityRefOut | None
    subject: EntityRefOut | None
    attributes: dict[str, Any]
    raw_event_id: RawEventId | None
    trace_id: str | None
    tags: list[str]

    @field_serializer("timestamp", "observed_at", when_used="json")
    def _serialize_timestamp(self, value: datetime | None) -> str | None:
        """Отдавать время в той же форме, что требует схема: суффикс ``Z``.

        Полагаться на сериализатор Pydantic по умолчанию нельзя: он вправе
        выдать смещение ``+00:00``, которое ``pattern`` в event.schema.json
        отвергает.
        """
        return format_utc(value) if value is not None else None

    @classmethod
    def from_domain(cls, event: Event) -> "EventOut":
        return cls(
            schema_version=event.schema_version,
            id=event.id,
            timestamp=event.timestamp,
            observed_at=event.observed_at,
            type=event.type,
            source=event.source,
            host_id=event.host_id,
            boot_id=event.boot_id,
            actor=EntityRefOut.from_domain(event.actor) if event.actor else None,
            subject=EntityRefOut.from_domain(event.subject) if event.subject else None,
            attributes=dict(event.attributes),
            raw_event_id=event.raw_event_id,
            trace_id=event.trace_id,
            tags=list(event.tags),
        )


class EventListOut(BaseModel):
    """Ответ GET /api/v1/events (§31, §32)."""

    model_config = ConfigDict(frozen=True)

    events: list[EventOut]
    next_cursor: str | None = Field(
        default=None,
        description="Курсор следующей страницы. null означает, что события закончились.",
    )
    count: int


class RejectedEventOut(BaseModel):
    """Событие пакета, которое не удалось принять."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(description="Позиция события в массиве events исходного запроса")
    code: str
    message: str


class IngestBatchOut(BaseModel):
    """Отчёт о приёме пакета.

    Дубликаты не являются ошибкой: §34 требует идемпотентности при модели
    at-least-once, поэтому повторная доставка — нормальная ситуация, о которой
    нужно лишь сообщить.
    """

    model_config = ConfigDict(frozen=True)

    accepted: int = Field(description="Событий сохранено впервые")
    duplicates: int = Field(description="Событий пропущено: raw_event_id уже известен")
    normalization_failed: int = Field(description="Событий сохранено, но не нормализовано")
    rejected: list[RejectedEventOut] = Field(
        default_factory=list,
        description="События, отвергнутые валидацией. Не влияют на остальные события пакета.",
    )


class HealthOut(BaseModel):
    """Ответ GET /api/v1/health (§31)."""

    model_config = ConfigDict(frozen=True)

    status: str
    version: str
    database: str


class EventCountsOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int
    last_minute: int


class RawEventCountsOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int


class AgentStatusOut(BaseModel):
    """Состояние Agent. В 0.0.1 никогда не заполняется — см. статус-эндпоинт."""

    model_config = ConfigDict(frozen=True)

    connected: bool
    last_seen_at: datetime | None = None


class StatusOut(BaseModel):
    """Ответ GET /api/v1/status (§31)."""

    model_config = ConfigDict(frozen=True)

    core: str
    version: str
    database: str
    database_path: str
    database_size_bytes: int
    events: EventCountsOut
    raw_events: RawEventCountsOut
    agent: AgentStatusOut | None = Field(
        default=None,
        description=(
            "Всегда null в 0.0.1: Agent ещё не существует, и Core не отслеживает "
            "его подключение. Поле присутствует, чтобы клиент видел его отсутствие, "
            "а не догадывался о нём."
        ),
    )


class ErrorDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class ErrorOut(BaseModel):
    """Единый формат ошибки (§60)."""

    model_config = ConfigDict(frozen=True)

    error: ErrorDetail
