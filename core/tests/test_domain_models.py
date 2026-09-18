"""Тесты доменной модели RawEvent и Event."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from chronoscope.domain.errors import (
    InvalidInputError,
    UnsupportedSchemaVersionError,
)
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.event_type import PROCESS_STARTED
from chronoscope.domain.events.raw_event import RawEvent
from tests.conftest import load_fixture
from tests.factories import (
    BOOT_ID,
    EVENT_ID,
    HOST_ID,
    PROC_ID,
    RAW_ID,
    make_event,
    make_raw_event,
)

class TestRawEventValidation:
    def test_accepts_valid_event(self) -> None:
        raw = make_raw_event()
        assert raw.raw_event_id == RAW_ID
        assert raw.boot_id == BOOT_ID

    def test_rejects_unsupported_schema_version(self) -> None:
        """§52: неподдерживаемая версия отвергается явно и отдельным типом ошибки."""
        with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
            make_raw_event(schema_version=99)
        assert excinfo.value.received == 99
        assert excinfo.value.supported == 1

    @pytest.mark.parametrize("version", [0, 2, "1", None])
    def test_rejects_any_other_version(self, version: Any) -> None:
        with pytest.raises(UnsupportedSchemaVersionError):
            make_raw_event(schema_version=version)

    def test_rejects_wrong_id_prefix(self) -> None:
        with pytest.raises(InvalidInputError, match="raw_<ULID>"):
            make_raw_event(raw_event_id=EVENT_ID)

    def test_rejects_malformed_host_id(self) -> None:
        with pytest.raises(InvalidInputError):
            make_raw_event(host_id="desktop-abc")

    def test_rejects_uppercase_collector(self) -> None:
        with pytest.raises(InvalidInputError, match="collector"):
            make_raw_event(collector="Windows.Process")

    def test_rejects_empty_collector_version(self) -> None:
        with pytest.raises(InvalidInputError):
            make_raw_event(collector_version="")

    def test_rejects_naive_observed_at(self) -> None:
        with pytest.raises(InvalidInputError, match="timezone-aware"):
            make_raw_event(observed_at=datetime(2026, 9, 18, 10, 42, 15))

    def test_rejects_non_utc_observed_at(self) -> None:
        with pytest.raises(InvalidInputError, match="UTC"):
            make_raw_event(observed_at=datetime(2026, 9, 18, 15, 42, 15, tzinfo=timezone(timedelta(hours=5))))

    def test_rejects_string_payload(self) -> None:
        with pytest.raises(InvalidInputError, match="payload"):
            make_raw_event(payload="not-an-object")

    def test_rejects_list_payload(self) -> None:
        with pytest.raises(InvalidInputError, match="payload"):
            make_raw_event(payload=[1, 2, 3])

    def test_allows_missing_source_timestamp_and_boot_id(self) -> None:
        raw = make_raw_event(source_timestamp=None, boot_id=None)
        assert raw.source_timestamp is None
        assert raw.boot_id is None

    def test_rejects_bad_payload_type(self) -> None:
        with pytest.raises(InvalidInputError, match="payload_type"):
            make_raw_event(payload_type="ProcessStart")


class TestRawEventHelpers:
    def test_best_timestamp_prefers_source_timestamp(self) -> None:
        raw = make_raw_event()
        assert raw.best_timestamp == raw.source_timestamp

    def test_best_timestamp_falls_back_to_observed_at(self) -> None:
        raw = make_raw_event(source_timestamp=None)
        assert raw.best_timestamp == raw.observed_at

    def test_payload_str(self) -> None:
        raw = make_raw_event(payload={"name": "notepad.exe"})
        assert raw.payload_str("name") == "notepad.exe"
        assert raw.payload_str("path") is None

    def test_payload_str_rejects_wrong_type(self) -> None:
        raw = make_raw_event(payload={"name": 42})
        with pytest.raises(InvalidInputError, match="ожидалась строка"):
            raw.payload_str("name")

    def test_payload_int_rejects_bool(self) -> None:
        """В Python True — это int, и молчаливое принятие его как PID скрыло бы ошибку."""
        raw = make_raw_event(payload={"pid": True})
        with pytest.raises(InvalidInputError, match="целое число"):
            raw.payload_int("pid")

    def test_payload_int(self) -> None:
        raw = make_raw_event(payload={"pid": 9812, "parent_pid": None})
        assert raw.payload_int("pid") == 9812
        assert raw.payload_int("parent_pid") is None

    def test_payload_timestamp_parses_z_suffix(self) -> None:
        raw = make_raw_event(payload={"process_started_at": "2026-09-18T10:42:15.220Z"})
        assert raw.payload_timestamp("process_started_at") == datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)

    def test_payload_timestamp_rejects_offset(self) -> None:
        raw = make_raw_event(payload={"process_started_at": "2026-09-18T15:42:15+05:00"})
        with pytest.raises(InvalidInputError, match="UTC"):
            raw.payload_timestamp("process_started_at")

    def test_payload_timestamp_rejects_garbage(self) -> None:
        raw = make_raw_event(payload={"process_started_at": "вчера"})
        with pytest.raises(InvalidInputError, match="разобрать"):
            raw.payload_timestamp("process_started_at")


class TestEventValidation:
    def test_accepts_valid_event(self) -> None:
        event = make_event()
        assert event.type == PROCESS_STARTED
        assert event.subject is not None
        assert event.subject.name == "notepad.exe"

    def test_rejects_wrong_id_prefix(self) -> None:
        with pytest.raises(InvalidInputError, match="evt_<ULID>"):
            make_event(id=RAW_ID)

    def test_rejects_malformed_event_type(self) -> None:
        for bad_type in ["process started", "Process.Started", "processstarted", "process."]:
            with pytest.raises(InvalidInputError, match="type"):
                make_event(type=bad_type)

    def test_rejects_unsupported_schema_version(self) -> None:
        with pytest.raises(UnsupportedSchemaVersionError):
            make_event(schema_version=2)

    def test_rejects_naive_timestamp(self) -> None:
        with pytest.raises(InvalidInputError, match="timezone-aware"):
            make_event(timestamp=datetime(2026, 9, 18, 10, 42, 15))

    def test_allows_null_observed_at_and_raw_event_id(self) -> None:
        """Синтетические события вроде incident.marked не имеют raw-источника (§42)."""
        event = make_event(observed_at=None, raw_event_id=None, actor=None)
        assert event.observed_at is None
        assert event.raw_event_id is None

    def test_rejects_overlong_tag(self) -> None:
        with pytest.raises(InvalidInputError, match="tags"):
            make_event(tags=("x" * 65,))

    def test_rejects_empty_tag(self) -> None:
        with pytest.raises(InvalidInputError, match="tags"):
            make_event(tags=("",))

    def test_rejects_string_attributes(self) -> None:
        with pytest.raises(InvalidInputError, match="attributes"):
            make_event(attributes="nope")


class TestEntityRef:
    def test_accepts_nameless_entity(self) -> None:
        ref = EntityRef("process", PROC_ID)
        assert ref.name is None
        assert ref.to_dict() == {"type": "process", "id": PROC_ID}

    def test_rejects_malformed_id(self) -> None:
        with pytest.raises(InvalidInputError, match="entity.id"):
            EntityRef("process", "9812")

    def test_rejects_overlong_name(self) -> None:
        with pytest.raises(InvalidInputError, match="entity.name"):
            EntityRef("process", PROC_ID, "x" * 261)


class TestEventSerialization:
    def test_to_dict_matches_contract_shape(self) -> None:
        event = make_event(tags=("manual",))
        data = event.to_dict()
        assert set(data) == {
            "schema_version", "id", "timestamp", "observed_at", "type", "source",
            "host_id", "boot_id", "actor", "subject", "attributes", "raw_event_id",
            "trace_id", "tags",
        }
        assert data["timestamp"] == "2026-09-18T10:42:15.220Z"
        assert data["actor"] is None
        assert data["subject"] == {"type": "process", "id": PROC_ID, "name": "notepad.exe"}
        assert data["tags"] == ["manual"]

    def test_to_dict_uses_z_suffix_not_offset(self) -> None:
        """Форма должна совпадать со схемой: суффикс Z, а не +00:00."""
        data = make_event().to_dict()
        assert str(data["timestamp"]).endswith("Z")
        assert "+00:00" not in str(data["timestamp"])


class TestFixturesParseIntoDomain:
    """Фикстуры должны разбираться доменом без правок — иначе контракт разошёлся."""

    @pytest.mark.parametrize(
        "path",
        [
            ("windows", "process_start_001.json"),
            ("windows", "process_exit_001.json"),
            ("windows", "process_start_missing_path.json"),
        ],
    )
    def test_raw_fixture(self, path: tuple[str, str]) -> None:
        data = load_fixture(*path)
        raw = RawEvent(
            schema_version=data["schema_version"],
            raw_event_id=data["raw_event_id"],
            collector=data["collector"],
            collector_version=data["collector_version"],
            observed_at=datetime.fromisoformat(data["observed_at"]),
            source_timestamp=datetime.fromisoformat(data["source_timestamp"]),
            host_id=data["host_id"],
            boot_id=data["boot_id"],
            payload_type=data["payload_type"],
            payload=data["payload"],
        )
        assert raw.payload_type == data["payload_type"]

    @pytest.mark.parametrize(
        "path",
        [("events", "process_started_001.json"), ("events", "process_exited_001.json")],
    )
    def test_event_fixture(self, path: tuple[str, str]) -> None:
        data = load_fixture(*path)
        event = Event(
            schema_version=data["schema_version"],
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            type=data["type"],
            source=data["source"],
            host_id=data["host_id"],
            attributes=data["attributes"],
            observed_at=datetime.fromisoformat(data["observed_at"]),
            boot_id=data["boot_id"],
            actor=EntityRef(**data["actor"]) if data["actor"] else None,
            subject=EntityRef(**data["subject"]) if data["subject"] else None,
            raw_event_id=data["raw_event_id"],
            trace_id=data["trace_id"],
            tags=tuple(data["tags"]),
        )
        assert event.to_dict()["subject"] == data["subject"]
