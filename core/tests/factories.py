"""Фабрики тестовых объектов.

Вынесены из тестов, чтобы разные уровни (домен, репозитории, нормализатор, API)
строили объекты одинаково. Иначе тесты начнут расходиться в деталях вроде
``observed_at``, и падение одного из них перестанет что-либо означать для
остальных.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.event_type import (
    COLLECTOR_WINDOWS_PROCESS,
    PAYLOAD_PROCESS_START,
    PROCESS_EXITED,
    PROCESS_STARTED,
)
from chronoscope.domain.events.raw_event import RawEvent
from chronoscope.domain.ids import process_instance_id

HOST_ID = "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9"
BOOT_ID = "boot_01K5R8Z9M4Q7T2V6X1B3D5F7HA"
RAW_ID = "raw_01K5R8Z9M5P8W3X7Y2C4E6G8J0"
RAW_ID_EXIT = "raw_01K5R8Z9M6N9V4Z8A3D5F7H9K1"
EVENT_ID = "evt_01K5R8Z9M8R1X6Y0Z5D7F9H1K3"
PROC_ID = "proc_01K5R8Z9M1C2D3E4F5G6H7J8K9"
PARENT_PROC_ID = "proc_01K5R8Z9M0A1B2C3D4E5F6G7H8"

NOTEPAD_PID = 9812
EXPLORER_PID = 4312

OBSERVED_AT = datetime(2026, 9, 18, 10, 42, 15, 281_000, tzinfo=UTC)
SOURCE_TIMESTAMP = datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)
EXIT_TIMESTAMP = datetime(2026, 9, 18, 10, 44, 3, 114_000, tzinfo=UTC)
EXIT_OBSERVED_AT = datetime(2026, 9, 18, 10, 44, 3, 170_000, tzinfo=UTC)


def make_raw_event(**overrides: Any) -> RawEvent:
    defaults: dict[str, Any] = {
        "schema_version": 1,
        "raw_event_id": RAW_ID,
        "collector": COLLECTOR_WINDOWS_PROCESS,
        "collector_version": "0.0.1",
        "observed_at": OBSERVED_AT,
        "source_timestamp": SOURCE_TIMESTAMP,
        "host_id": HOST_ID,
        "boot_id": BOOT_ID,
        "payload_type": PAYLOAD_PROCESS_START,
        "payload": {"pid": NOTEPAD_PID, "name": "notepad.exe"},
    }
    defaults.update(overrides)
    return RawEvent(**defaults)


def make_event(**overrides: Any) -> Event:
    defaults: dict[str, Any] = {
        "schema_version": 1,
        "id": EVENT_ID,
        "timestamp": SOURCE_TIMESTAMP,
        "type": PROCESS_STARTED,
        "source": COLLECTOR_WINDOWS_PROCESS,
        "host_id": HOST_ID,
        "attributes": {"pid": NOTEPAD_PID},
        "observed_at": OBSERVED_AT,
        "boot_id": BOOT_ID,
        "subject": EntityRef("process", PROC_ID, "notepad.exe"),
        "raw_event_id": RAW_ID,
    }
    defaults.update(overrides)
    return Event(**defaults)


def make_exit_event(**overrides: Any) -> Event:
    defaults: dict[str, Any] = {
        "id": "evt_01K5R8Z9M9S2Y7Z1A6E8G0J2K4",
        "timestamp": EXIT_TIMESTAMP,
        "observed_at": EXIT_OBSERVED_AT,
        "type": PROCESS_EXITED,
        "raw_event_id": RAW_ID_EXIT,
        "attributes": {"pid": NOTEPAD_PID, "exit_code": 0},
    }
    defaults.update(overrides)
    return make_event(**defaults)


def process_ref(pid: int, *, host_id: str = HOST_ID, boot_id: str | None = BOOT_ID, name: str | None = None) -> EntityRef:
    """Ссылка на процесс с идентичностью, выведенной из времени старта."""
    return EntityRef(
        "process",
        process_instance_id(host_id=host_id, boot_id=boot_id, pid=pid, started_at=SOURCE_TIMESTAMP),
        name,
    )


def raw_event_from_contract(data: dict[str, Any]) -> RawEvent:
    """Собрать доменный RawEvent из словаря в форме shared/schemas/raw-event."""
    return RawEvent(
        schema_version=data["schema_version"],
        raw_event_id=data["raw_event_id"],
        collector=data["collector"],
        collector_version=data["collector_version"],
        observed_at=datetime.fromisoformat(data["observed_at"]),
        source_timestamp=(
            datetime.fromisoformat(data["source_timestamp"]) if data.get("source_timestamp") else None
        ),
        host_id=data["host_id"],
        boot_id=data.get("boot_id"),
        payload_type=data["payload_type"],
        payload=data["payload"],
    )


def event_from_contract(data: dict[str, Any]) -> Event:
    """Собрать доменный Event из словаря в форме shared/schemas/event."""
    return Event(
        schema_version=data["schema_version"],
        id=data["id"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
        type=data["type"],
        source=data["source"],
        host_id=data["host_id"],
        attributes=data["attributes"],
        observed_at=datetime.fromisoformat(data["observed_at"]) if data.get("observed_at") else None,
        boot_id=data.get("boot_id"),
        actor=EntityRef(**data["actor"]) if data.get("actor") else None,
        subject=EntityRef(**data["subject"]) if data.get("subject") else None,
        raw_event_id=data.get("raw_event_id"),
        trace_id=data.get("trace_id"),
        tags=tuple(data.get("tags") or ()),
    )
