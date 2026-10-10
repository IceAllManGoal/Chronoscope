"""Тесты нормализатора записей журнала Windows (§8, §77.2).

Ключевые проверки — не «поля переложились», а обещания версии:

1. **Один тип события.** Никакая пара «provider + event_id» не превращается в
   человеческий тип: в 0.0.4 у второго источника один тип — ``system.event``.
2. **Атрибуты не шире payload.** Чего источник не сообщил, того в событии нет:
   ноль вместо отсутствующего идентификатора выглядел бы как настоящее событие
   с идентификатором 0.
3. **Сообщение остаётся в сыром событии.** Даже когда оно собрано
   (``capture_message``), в timeline оно не переезжает: сырой слой хранит
   свидетельство источника, timeline — знание Chronoscope.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from chronoscope.domain.errors import NormalizationError
from chronoscope.domain.events.event_type import (
    COLLECTOR_WINDOWS_EVENTLOG,
    KNOWN_EVENT_TYPES,
    PAYLOAD_EVENT_LOG_RECORD,
    PAYLOAD_FIELD_CHANNEL,
    PAYLOAD_FIELD_MESSAGE,
    SYSTEM_EVENT,
)
from chronoscope.normalization.registry import (
    NormalizationContext,
    default_registry,
)
from chronoscope.normalization.windows.event_log_normalizer import normalize_event_log_record
from tests.conftest import load_fixture
from tests.factories import raw_event_from_contract

RECORD_OBSERVED_AT = datetime(2026, 10, 9, 18, 20, 4, 180_000, tzinfo=UTC)
RECORD_TIME = datetime(2026, 10, 9, 18, 20, 4, 117_000, tzinfo=UTC)


def record_raw(payload: dict[str, Any] | None = None):  # noqa: ANN201 - доменный RawEvent
    raw = raw_event_from_contract(load_fixture("windows", "event_log_record_001.json"))
    if payload is None:
        return raw
    return replace(raw, payload=payload)


def normalize(raw) -> Any:  # noqa: ANN001
    return normalize_event_log_record(raw, NormalizationContext())


class TestNormalization:
    def test_maps_to_system_event(self, event_log_record_payload: dict[str, Any]) -> None:
        raw = record_raw()
        event = normalize(raw)

        assert event.type == SYSTEM_EVENT
        assert event.source == COLLECTOR_WINDOWS_EVENTLOG
        assert event.raw_event_id == raw.raw_event_id
        assert event.schema_version == raw.schema_version
        assert event.observed_at == RECORD_OBSERVED_AT
        assert event.host_id == raw.host_id
        assert event.boot_id == raw.boot_id

    def test_attributes_repeat_payload_field_for_field(self) -> None:
        """Нормализация здесь — отбор полей, а не перевод терминов."""
        raw = record_raw()
        event = normalize(raw)

        assert event.attributes == dict(raw.payload)

    def test_timestamp_is_record_time_not_observation_time(self) -> None:
        """§24: время события — время записи в журнале, а не момент наблюдения."""
        event = normalize(record_raw())

        assert event.timestamp == RECORD_TIME
        assert event.timestamp != event.observed_at

    def test_timestamp_falls_back_to_observed_at(self) -> None:
        """Если журнал не сообщил времени записи, остаётся время наблюдения."""
        raw = replace(record_raw(), source_timestamp=None)

        assert normalize(raw).timestamp == RECORD_OBSERVED_AT

    def test_has_no_actor_and_no_subject(self) -> None:
        """Запись журнала не описывает действие над сущностью Chronoscope."""
        event = normalize(record_raw())

        assert event.actor is None
        assert event.subject is None

    def test_record_id_is_not_an_identity(self) -> None:
        """Две записи с одним ``record_id`` дают два разных события.

        ``record_id`` — данные источника: он уникален внутри канала и только
        внутри него, а дедупликация в 0.0.4 идёт по ``raw_event_id`` (§34).
        """
        first = normalize(record_raw())
        second = normalize(record_raw())

        assert first.attributes["record_id"] == second.attributes["record_id"]
        assert first.id != second.id


class TestMissingDataStaysMissing:
    def test_absent_fields_are_not_invented(self) -> None:
        """Чего не сообщили — того нет; ноль вместо неизвестного не подставляется."""
        event = normalize(record_raw({PAYLOAD_FIELD_CHANNEL: "Application"}))

        assert event.attributes == {PAYLOAD_FIELD_CHANNEL: "Application"}

    def test_partial_payload_keeps_only_what_arrived(self) -> None:
        event = normalize(
            record_raw(
                {
                    PAYLOAD_FIELD_CHANNEL: "System",
                    "provider": "Service Control Manager",
                    "event_id": 7036,
                }
            )
        )

        assert event.attributes == {
            PAYLOAD_FIELD_CHANNEL: "System",
            "provider": "Service Control Manager",
            "event_id": 7036,
        }

    def test_missing_channel_is_rejected(self) -> None:
        """Без канала запись неотличима от записи другого журнала."""
        raw = record_raw({"provider": "Service Control Manager", "event_id": 7036})

        with pytest.raises(NormalizationError) as failure:
            normalize(raw)

        assert "channel" in str(failure.value)

    def test_zero_is_kept_as_zero(self) -> None:
        """Ноль, который источник сообщил, — это ноль, а не отсутствие."""
        event = normalize(record_raw({PAYLOAD_FIELD_CHANNEL: "System", "task": 0}))

        assert event.attributes["task"] == 0

    def test_wrong_type_is_rejected(self) -> None:
        """Строка там, где ожидалось число, — ошибка источника, а не молчание."""
        raw = record_raw({PAYLOAD_FIELD_CHANNEL: "System", "event_id": "12"})

        with pytest.raises(Exception) as failure:
            normalize(raw)

        assert "event_id" in str(failure.value)


class TestPrivacy:
    def test_message_is_not_carried_into_the_event(self) -> None:
        """Сообщение остаётся в сыром событии, даже когда оно собрано."""
        raw = record_raw(
            {
                PAYLOAD_FIELD_CHANNEL: "System",
                "event_id": 7036,
                PAYLOAD_FIELD_MESSAGE: "Служба запущена: C:\\Users\\someone\\app.exe",
            }
        )

        event = normalize(raw)

        assert PAYLOAD_FIELD_MESSAGE not in event.attributes
        assert "someone" not in str(event.attributes)

    def test_unknown_payload_fields_are_not_copied(self) -> None:
        """Данные события, XML и SID источника в timeline не попадают."""
        raw = record_raw(
            {
                PAYLOAD_FIELD_CHANNEL: "System",
                "event_id": 7036,
                "xml": "<Event/>",
                "event_data": {"param1": "C:\\Users\\someone\\app.exe"},
                "user_sid": "S-1-5-21-1000000000-2000000000-3000000000-1001",
            }
        )

        event = normalize(raw)

        assert event.attributes == {PAYLOAD_FIELD_CHANNEL: "System", "event_id": 7036}


class TestRegistry:
    def test_default_registry_knows_the_pair(self) -> None:
        assert default_registry().knows(COLLECTOR_WINDOWS_EVENTLOG, PAYLOAD_EVENT_LOG_RECORD)

    def test_unknown_payload_type_of_the_new_collector_is_still_rejected(self) -> None:
        """Незарегистрированная пара не нормализуется: сырое событие остаётся, событие — нет.

        Так выглядит Agent новее Core: он присылает payload, которого ядро не
        знает, и это не сбой pipeline (§60).
        """
        raw = replace(record_raw(), payload_type="event_log_channel_changed")

        with pytest.raises(NormalizationError) as failure:
            default_registry().normalize(raw, NormalizationContext())

        assert "event_log_channel_changed" in str(failure.value)

    def test_system_event_is_a_known_type(self) -> None:
        assert SYSTEM_EVENT in KNOWN_EVENT_TYPES
