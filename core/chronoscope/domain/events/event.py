"""Event — нормализованное событие, центральная сущность Chronoscope (§13).

Событие исторически неизменно (§21). Дополнительное знание — enrichment,
correlation, результаты аналитики — хранится **отдельно** и не переписывает
``Event``. Поэтому здесь нет ни полей обогащения, ни ссылок на аналитику.

``actor`` — сущность, инициировавшая действие; ``subject`` — сущность, над
которой действие произошло (§20). Оба могут отсутствовать: честное ``None``
лучше подставленной догадки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from chronoscope.domain.errors import (
    InvalidInputError,
    UnsupportedSchemaVersionError,
)
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event_type import SUPPORTED_SCHEMA_VERSION
from chronoscope.domain.hosts.boot_id import require_boot_id
from chronoscope.domain.hosts.host_id import require_host_id
from chronoscope.domain.ids import (
    PREFIX_EVENT,
    PREFIX_RAW_EVENT,
    require_prefixed_id,
)
from chronoscope.domain.timestamps import format_utc
from chronoscope.domain.validation import (
    COLLECTOR_NAME_RE,
    EVENT_TYPE_RE,
    require_matches,
    require_utc,
)

MAX_TAG_LENGTH = 64
MAX_TRACE_ID_LENGTH = 128


@dataclass(frozen=True, slots=True)
class Event:
    schema_version: int
    id: str
    timestamp: datetime
    type: str
    source: str
    host_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    observed_at: datetime | None = None
    boot_id: str | None = None
    actor: EntityRef | None = None
    subject: EntityRef | None = None
    raw_event_id: str | None = None
    trace_id: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != SUPPORTED_SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                received=self.schema_version,
                supported=SUPPORTED_SCHEMA_VERSION,
            )

        require_prefixed_id(self.id, PREFIX_EVENT, "id")
        require_matches(self.type, EVENT_TYPE_RE, "type")
        require_matches(self.source, COLLECTOR_NAME_RE, "source")

        require_utc(self.timestamp, "timestamp")
        if self.observed_at is not None:
            require_utc(self.observed_at, "observed_at")

        require_host_id(self.host_id)
        if self.boot_id is not None:
            require_boot_id(self.boot_id)

        if self.raw_event_id is not None:
            require_prefixed_id(self.raw_event_id, PREFIX_RAW_EVENT, "raw_event_id")

        if self.trace_id is not None and len(self.trace_id) > MAX_TRACE_ID_LENGTH:
            raise InvalidInputError(f"trace_id: длина превышает {MAX_TRACE_ID_LENGTH} символов")

        if isinstance(self.attributes, (str, bytes)) or not isinstance(self.attributes, Mapping):
            raise InvalidInputError("attributes: должен быть JSON-объектом")

        for tag in self.tags:
            if not isinstance(tag, str) or not tag:
                raise InvalidInputError("tags: каждый тег должен быть непустой строкой")
            if len(tag) > MAX_TAG_LENGTH:
                raise InvalidInputError(f"tags: длина тега превышает {MAX_TAG_LENGTH} символов")

    @property
    def is_process_event(self) -> bool:
        return self.type.startswith("process.")

    def to_dict(self) -> dict[str, Any]:
        """Представление, совпадающее с shared/schemas/event.schema.json.

        Существует здесь, а не в API-слое, потому что это форма самого
        доменного объекта, а не форма конкретного транспорта.
        """
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "timestamp": format_utc(self.timestamp),
            "observed_at": format_utc(self.observed_at) if self.observed_at else None,
            "type": self.type,
            "source": self.source,
            "host_id": self.host_id,
            "boot_id": self.boot_id,
            "actor": self.actor.to_dict() if self.actor else None,
            "subject": self.subject.to_dict() if self.subject else None,
            "attributes": dict(self.attributes),
            "raw_event_id": self.raw_event_id,
            "trace_id": self.trace_id,
            "tags": list(self.tags),
        }
