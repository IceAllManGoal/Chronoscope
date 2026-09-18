"""Тесты идентификаторов (§14, §17, docs/EVENT_MODEL.md §2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from chronoscope.domain.errors import InvalidInputError
from chronoscope.domain.ids import (
    ALPHABET,
    ULID_LENGTH,
    is_prefixed_id,
    is_ulid,
    new_boot_id,
    new_event_id,
    new_host_id,
    new_raw_event_id,
    new_ulid,
    process_instance_id,
    require_prefixed_id,
    ulid_from,
    ulid_timestamp,
)
from tests.conftest import load_fixture

HOST_ID = "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9"
BOOT_ID = "boot_01K5R8Z9M4Q7T2V6X1B3D5F7HA"


class TestUlid:
    def test_length_and_alphabet(self) -> None:
        value = new_ulid()
        assert len(value) == ULID_LENGTH
        assert all(char in ALPHABET for char in value)
        assert is_ulid(value)

    def test_excludes_confusable_characters(self) -> None:
        """I, L, O и U не входят в алфавит Crockford.

        Это не придирка: идентификаторы читают глазами при разборе инцидентов,
        и ILOU — самые частые ошибки чтения.
        """
        for char in "ILOU":
            assert char not in ALPHABET

    def test_unique_across_calls(self) -> None:
        values = {new_ulid() for _ in range(500)}
        assert len(values) == 500

    def test_rejects_lowercase(self) -> None:
        assert not is_ulid(new_ulid().lower())

    def test_rejects_wrong_length(self) -> None:
        assert not is_ulid(new_ulid()[:25])
        assert not is_ulid(new_ulid() + "0")

    def test_rejects_confusable_character(self) -> None:
        # Классическая ошибка: L вместо 1 или вместо K.
        broken = new_ulid()[:25] + "L"
        assert not is_ulid(broken)

    def test_timestamp_round_trip(self) -> None:
        moment = datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)
        value = new_ulid(at=moment)
        assert ulid_timestamp(value) == moment

    def test_ulid_from_is_deterministic(self) -> None:
        first = ulid_from(timestamp_ms=1_787_000_000_000, random_bits=12345)
        second = ulid_from(timestamp_ms=1_787_000_000_000, random_bits=12345)
        assert first == second

    def test_ulid_from_rejects_out_of_range(self) -> None:
        with pytest.raises(InvalidInputError):
            ulid_from(timestamp_ms=-1, random_bits=0)
        with pytest.raises(InvalidInputError):
            ulid_from(timestamp_ms=0, random_bits=1 << 80)

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(InvalidInputError, match="timezone-aware"):
            new_ulid(at=datetime(2026, 9, 18, 10, 0, 0))

    def test_rejects_non_utc_datetime(self) -> None:
        shifted = datetime(2026, 9, 18, 15, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        with pytest.raises(InvalidInputError, match="UTC"):
            new_ulid(at=shifted)


class TestPrefixedIds:
    def test_generators_produce_correct_prefix(self) -> None:
        assert is_prefixed_id(new_raw_event_id(), "raw")
        assert is_prefixed_id(new_event_id(), "evt")
        assert is_prefixed_id(new_host_id(), "host")
        assert is_prefixed_id(new_boot_id(), "boot")

    def test_prefix_mismatch_rejected(self) -> None:
        assert not is_prefixed_id(new_raw_event_id(), "evt")

    def test_require_prefixed_id_message_mentions_expected_form(self) -> None:
        with pytest.raises(InvalidInputError, match="raw_<ULID>"):
            require_prefixed_id("evt_01K5R8Z9M4Q7T2V6X1B3D5F7H9", "raw", "raw_event_id")


class TestProcessInstanceId:
    """§14: PID — атрибут, process_instance_id — идентичность."""

    def test_is_deterministic(self) -> None:
        started = datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)
        first = process_instance_id(host_id=HOST_ID, boot_id=BOOT_ID, pid=9812, started_at=started)
        second = process_instance_id(host_id=HOST_ID, boot_id=BOOT_ID, pid=9812, started_at=started)
        assert first == second
        assert is_prefixed_id(first, "proc")

    def test_same_instance_from_start_and_exit_fixtures(self) -> None:
        """Главное требование: старт и выход одного процесса дают один id.

        Если это ломается, теряется связь «процесс запустился → завершился»,
        а вместе с ней и весь смысл process tree (§43).
        """
        start = load_fixture("windows", "process_start_001.json")
        exit_ = load_fixture("windows", "process_exit_001.json")

        def instance_id(raw: dict) -> str:
            payload = raw["payload"]
            return process_instance_id(
                host_id=raw["host_id"],
                boot_id=raw["boot_id"],
                pid=payload["pid"],
                started_at=datetime.fromisoformat(payload["process_started_at"]),
            )

        assert instance_id(start) == instance_id(exit_)

    def test_pid_reuse_after_exit_yields_different_id(self) -> None:
        """Переиспользование PID не должно склеивать два разных процесса."""
        first_start = datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)
        second_start = datetime(2026, 9, 18, 10, 50, 0, 0, tzinfo=UTC)

        first = process_instance_id(host_id=HOST_ID, boot_id=BOOT_ID, pid=9812, started_at=first_start)
        second = process_instance_id(host_id=HOST_ID, boot_id=BOOT_ID, pid=9812, started_at=second_start)
        assert first != second

    def test_different_boot_yields_different_id(self) -> None:
        started = datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)
        other_boot = "boot_01K5R8Z9M4Q7T2V6X1B3D5F7HB"

        first = process_instance_id(host_id=HOST_ID, boot_id=BOOT_ID, pid=9812, started_at=started)
        second = process_instance_id(host_id=HOST_ID, boot_id=other_boot, pid=9812, started_at=started)
        assert first != second

    def test_missing_boot_id_still_yields_valid_id(self) -> None:
        started = datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)
        value = process_instance_id(host_id=HOST_ID, boot_id=None, pid=9812, started_at=started)
        assert is_prefixed_id(value, "proc")

    def test_timestamp_part_encodes_process_start(self) -> None:
        """ULID упорядочен по времени старта процесса."""
        earlier = process_instance_id(
            host_id=HOST_ID, boot_id=BOOT_ID, pid=1,
            started_at=datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC),
        )
        later = process_instance_id(
            host_id=HOST_ID, boot_id=BOOT_ID, pid=2,
            started_at=datetime(2026, 9, 18, 11, 0, 0, tzinfo=UTC),
        )
        assert earlier < later

    def test_rejects_non_positive_pid(self) -> None:
        started = datetime(2026, 9, 18, 10, 42, 15, tzinfo=UTC)
        for bad_pid in (0, -1, True, "9812"):
            with pytest.raises(InvalidInputError):
                process_instance_id(host_id=HOST_ID, boot_id=BOOT_ID, pid=bad_pid, started_at=started)  # type: ignore[arg-type]


class TestFixtureIdsAreValid:
    """Идентификаторы в фикстурах должны быть валидны с точки зрения домена.

    Это ловит расхождение между shared/fixtures и реализацией раньше, чем
    оно проявится в интеграционном тесте.
    """

    @pytest.mark.parametrize(
        "path",
        [
            ("windows", "process_start_001.json"),
            ("windows", "process_exit_001.json"),
            ("windows", "process_start_missing_path.json"),
        ],
    )
    def test_raw_fixture_ids(self, path: tuple[str, str]) -> None:
        raw = load_fixture(*path)
        assert is_prefixed_id(raw["raw_event_id"], "raw")
        assert is_prefixed_id(raw["host_id"], "host")
        assert is_prefixed_id(raw["boot_id"], "boot")

    @pytest.mark.parametrize(
        "path",
        [("events", "process_started_001.json"), ("events", "process_exited_001.json")],
    )
    def test_event_fixture_ids(self, path: tuple[str, str]) -> None:
        event = load_fixture(*path)
        assert is_prefixed_id(event["id"], "evt")
        assert is_prefixed_id(event["raw_event_id"], "raw")
        assert is_prefixed_id(event["subject"]["id"], "proc")
        assert is_prefixed_id(event["actor"]["id"], "proc")
