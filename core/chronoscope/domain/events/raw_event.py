"""RawEvent — универсальная оболочка вокруг source-specific payload (§12).

``RawEvent`` — это ещё не знание Chronoscope, а сохранённое свидетельство.
Он фиксирует то, что сообщил источник, не добавляя интерпретации.

Оболочка — **строгий** контракт между Agent и Core, а ``payload`` — точка
расширения и схемой намеренно не ограничен. Обоснование разделения —
docs/EVENT_MODEL.md §11.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from chronoscope.domain.errors import (
    InvalidInputError,
    UnsupportedSchemaVersionError,
)
from chronoscope.domain.events.event_type import SUPPORTED_SCHEMA_VERSION
from chronoscope.domain.hosts.boot_id import require_boot_id
from chronoscope.domain.hosts.host_id import require_host_id
from chronoscope.domain.ids import PREFIX_RAW_EVENT, require_prefixed_id
from chronoscope.domain.validation import (
    COLLECTOR_NAME_RE,
    PAYLOAD_TYPE_RE,
    require_matches,
    require_non_empty,
    require_utc,
)


@dataclass(frozen=True, slots=True)
class RawEvent:
    schema_version: int
    raw_event_id: str
    collector: str
    collector_version: str
    observed_at: datetime
    host_id: str
    payload_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    source_timestamp: datetime | None = None
    boot_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SUPPORTED_SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                received=self.schema_version,
                supported=SUPPORTED_SCHEMA_VERSION,
            )

        require_prefixed_id(self.raw_event_id, PREFIX_RAW_EVENT, "raw_event_id")
        require_matches(self.collector, COLLECTOR_NAME_RE, "collector")
        require_non_empty(self.collector_version, "collector_version")
        require_matches(self.payload_type, PAYLOAD_TYPE_RE, "payload_type")

        require_utc(self.observed_at, "observed_at")
        if self.source_timestamp is not None:
            require_utc(self.source_timestamp, "source_timestamp")

        require_host_id(self.host_id)
        if self.boot_id is not None:
            require_boot_id(self.boot_id)

        if isinstance(self.payload, (str, bytes)) or not isinstance(self.payload, Mapping):
            raise InvalidInputError("payload: должен быть JSON-объектом")

    @property
    def best_timestamp(self) -> datetime:
        """Наиболее точное доступное время события (§24).

        ``source_timestamp`` — время источника, и именно им следует
        пользоваться, когда он есть. ``observed_at`` — момент, когда событие
        увидел Chronoscope, поэтому он всегда не раньше реального времени
        события и является лишь запасным вариантом.
        """
        return self.source_timestamp or self.observed_at

    def payload_str(self, key: str) -> str | None:
        """Строковое поле payload или ``None``, если поле отсутствует."""
        value = self.payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidInputError(f"payload.{key}: ожидалась строка, получено {type(value).__name__}")
        return value

    def payload_int(self, key: str) -> int | None:
        """Целочисленное поле payload или ``None``, если поле отсутствует.

        ``bool`` отвергается явно: в Python ``True`` является ``int``, и
        молчаливое принятие его как PID скрыло бы ошибку источника.
        """
        value = self.payload.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidInputError(f"payload.{key}: ожидалось целое число, получено {type(value).__name__}")
        return value

    def payload_timestamp(self, key: str) -> datetime | None:
        """Поле payload с временем в формате RFC 3339 UTC."""
        value = self.payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidInputError(f"payload.{key}: ожидалась строка с timestamp")
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise InvalidInputError(f"payload.{key}: не удалось разобрать timestamp {value!r}") from exc
        return require_utc(moment, f"payload.{key}")
