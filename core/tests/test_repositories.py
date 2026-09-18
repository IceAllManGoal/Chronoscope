"""Тесты репозиториев (§26, §32, §34).

Проверяется не «SQL работает», а поведение, обещанное спекой:
идемпотентность ingest, порядок выдачи, отсутствие потерь и дублей на границе
страниц и корректность поиска экземпляра процесса.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chronoscope.domain.errors import InvalidInputError
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.event_type import (
    PROCESS_EXITED,
    PROCESS_STARTED,
)
from chronoscope.domain.ids import new_event_id
from chronoscope.infrastructure.database.repositories import (
    MAX_PAGE_LIMIT,
    EventQuery,
    decode_cursor,
    encode_cursor,
)
from tests.factories import (
    BOOT_ID,
    EVENT_ID,
    HOST_ID,
    NOTEPAD_PID,
    PROC_ID,
    RAW_ID,
    make_event,
    make_exit_event,
    make_raw_event,
)

INGESTED_AT = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
LATER_BOOT_ID = "boot_01K5R8Z9M4Q7T2V6X1B3D5F7HB"


@pytest.fixture
def stored_raw(raw_repository):  # noqa: ANN001
    """Сырое событие, уже лежащее в базе: нужно для ссылки по внешнему ключу."""
    raw = make_raw_event()
    assert raw_repository.insert(raw, ingested_at=INGESTED_AT) is True
    return raw


def sequenced_event(*, index: int, timestamp: datetime, raw_event_id: str | None = None) -> Event:
    return make_event(
        id=new_event_id(),
        timestamp=timestamp,
        observed_at=timestamp,
        raw_event_id=raw_event_id,
        attributes={"pid": 1000 + index, "index": index},
    )


class TestRawEventRepository:
    def test_insert_returns_true_for_new_event(self, raw_repository) -> None:
        assert raw_repository.insert(make_raw_event(), ingested_at=INGESTED_AT) is True

    def test_duplicate_insert_returns_false(self, raw_repository) -> None:
        """§34: at-least-once доставка не создаёт дубликат."""
        raw = make_raw_event()
        assert raw_repository.insert(raw, ingested_at=INGESTED_AT) is True
        assert raw_repository.insert(raw, ingested_at=INGESTED_AT) is False

    def test_duplicate_does_not_overwrite_original(self, raw_repository) -> None:
        """Повторная доставка не должна менять уже сохранённое свидетельство."""
        original = make_raw_event(payload={"pid": 9812, "name": "notepad.exe"})
        raw_repository.insert(original, ingested_at=INGESTED_AT)

        conflicting = make_raw_event(payload={"pid": 9999, "name": "подмена.exe"})
        assert raw_repository.insert(conflicting, ingested_at=INGESTED_AT) is False

        stored = raw_repository.get(RAW_ID)
        assert stored is not None
        assert stored.payload == {"pid": 9812, "name": "notepad.exe"}

    def test_get_round_trip(self, raw_repository) -> None:
        original = make_raw_event()
        raw_repository.insert(original, ingested_at=INGESTED_AT)

        stored = raw_repository.get(RAW_ID)
        assert stored is not None
        assert stored == original

    def test_get_missing_returns_none(self, raw_repository) -> None:
        assert raw_repository.get("raw_01K5R8Z9M5P8W3X7Y2C4E6G8J9") is None

    def test_count(self, raw_repository) -> None:
        assert raw_repository.count() == 0
        raw_repository.insert(make_raw_event(), ingested_at=INGESTED_AT)
        assert raw_repository.count() == 1


class TestEventRepositoryBasics:
    def test_insert_and_get_round_trip(self, event_repository, stored_raw) -> None:
        event = make_event(
            actor=EntityRef("process", "proc_01K5R8Z9M0A1B2C3D4E5F6G7H8", "explorer.exe"),
            tags=("manual",),
            trace_id="trace-1",
        )
        assert event_repository.insert(event, ingested_at=INGESTED_AT) is True

        stored = event_repository.get(EVENT_ID)
        assert stored is not None
        assert stored == event

    def test_get_missing_returns_none(self, event_repository) -> None:
        assert event_repository.get(EVENT_ID) is None

    def test_duplicate_insert_returns_false(self, event_repository, stored_raw) -> None:
        event = make_event()
        assert event_repository.insert(event, ingested_at=INGESTED_AT) is True
        assert event_repository.insert(event, ingested_at=INGESTED_AT) is False

    def test_null_actor_and_subject_are_preserved(self, event_repository) -> None:
        event = make_event(actor=None, subject=None, raw_event_id=None)
        event_repository.insert(event, ingested_at=INGESTED_AT)

        stored = event_repository.get(EVENT_ID)
        assert stored is not None
        assert stored.actor is None
        assert stored.subject is None

    def test_count_and_count_since(self, event_repository) -> None:
        base = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        for index in range(3):
            event_repository.insert(
                sequenced_event(index=index, timestamp=base + timedelta(minutes=index)),
                ingested_at=INGESTED_AT,
            )

        assert event_repository.count() == 3
        assert event_repository.count_since(base + timedelta(minutes=1)) == 2
        assert event_repository.count_since(base + timedelta(minutes=5)) == 0


class TestEventOrdering:
    def test_newest_first(self, event_repository) -> None:
        base = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        for index in range(3):
            event_repository.insert(
                sequenced_event(index=index, timestamp=base + timedelta(minutes=index)),
                ingested_at=INGESTED_AT,
            )

        page = event_repository.list(EventQuery())
        assert [event.attributes["index"] for event in page.events] == [2, 1, 0]

    def test_same_timestamp_ordered_by_id_desc(self, event_repository) -> None:
        """§32: при равном времени порядок задаёт id, иначе страницы «поплывут»."""
        moment = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        events = [sequenced_event(index=index, timestamp=moment) for index in range(5)]
        for event in events:
            event_repository.insert(event, ingested_at=INGESTED_AT)

        page = event_repository.list(EventQuery())
        ids = [event.id for event in page.events]
        assert ids == sorted(ids, reverse=True)


class TestPagination:
    def _seed(self, event_repository, count: int, *, same_timestamp: bool = False) -> list[Event]:
        base = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        events = [
            sequenced_event(
                index=index,
                timestamp=base if same_timestamp else base + timedelta(seconds=index),
            )
            for index in range(count)
        ]
        for event in events:
            event_repository.insert(event, ingested_at=INGESTED_AT)
        return events

    @pytest.mark.parametrize("same_timestamp", [False, True])
    def test_walks_all_events_exactly_once(self, event_repository, same_timestamp: bool) -> None:
        """Курсор не теряет и не дублирует события на границах страниц.

        Случай одинаковых timestamps — самый опасный: наивная пагинация только
        по времени либо пропускает события, либо зацикливается.
        """
        seeded = self._seed(event_repository, 7, same_timestamp=same_timestamp)

        collected: list[str] = []
        cursor: str | None = None
        for _ in range(20):  # предохранитель от зацикливания
            page = event_repository.list(EventQuery(limit=2, cursor=cursor))
            collected.extend(event.id for event in page.events)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert len(collected) == len(set(collected)), "события продублированы"
        assert set(collected) == {event.id for event in seeded}
        assert len(collected) == len(seeded)

    def test_last_page_has_no_cursor(self, event_repository) -> None:
        self._seed(event_repository, 2)
        page = event_repository.list(EventQuery(limit=10))
        assert len(page.events) == 2
        assert page.next_cursor is None

    def test_full_page_reports_cursor(self, event_repository) -> None:
        self._seed(event_repository, 3)
        page = event_repository.list(EventQuery(limit=2))
        assert len(page.events) == 2
        assert page.next_cursor is not None

    def test_cursor_round_trip(self) -> None:
        moment = datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)
        cursor = encode_cursor(moment, EVENT_ID)
        assert decode_cursor(cursor) == (moment, EVENT_ID)

    def test_cursor_is_opaque(self) -> None:
        """Клиент не должен полагаться на внутренний формат курсора (§32)."""
        cursor = encode_cursor(datetime(2026, 9, 18, 10, 0, tzinfo=UTC), EVENT_ID)
        assert "|" not in cursor
        assert "2026" not in cursor

    @pytest.mark.parametrize(
        "bad_cursor",
        ["", "не-base64!", "!!!", "YWJj", "eyJ2IjogOTl9"],
    )
    def test_invalid_cursor_is_rejected(self, event_repository, bad_cursor: str) -> None:
        with pytest.raises(InvalidInputError):
            event_repository.list(EventQuery(cursor=bad_cursor))


class TestEventFilters:
    def _seed(self, event_repository) -> None:
        base = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        event_repository.insert(
            make_event(id=new_event_id(), timestamp=base, raw_event_id=None, subject=EntityRef("process", PROC_ID, "notepad.exe")),
            ingested_at=INGESTED_AT,
        )
        event_repository.insert(
            make_exit_event(id=new_event_id(), timestamp=base + timedelta(minutes=1), raw_event_id=None),
            ingested_at=INGESTED_AT,
        )
        event_repository.insert(
            make_event(
                id=new_event_id(),
                timestamp=base + timedelta(minutes=2),
                raw_event_id=None,
                source="plugin.steam",
                subject=EntityRef("process", "proc_01K5R8Z9M2D3E4F5G6H7J8K9M0", "steam.exe"),
            ),
            ingested_at=INGESTED_AT,
        )

    def test_filter_by_type(self, event_repository) -> None:
        self._seed(event_repository)
        page = event_repository.list(EventQuery(types=(PROCESS_STARTED,)))
        assert len(page.events) == 2
        assert all(event.type == PROCESS_STARTED for event in page.events)

    def test_filter_by_multiple_types(self, event_repository) -> None:
        self._seed(event_repository)
        page = event_repository.list(EventQuery(types=(PROCESS_STARTED, PROCESS_EXITED)))
        assert len(page.events) == 3

    def test_filter_by_source(self, event_repository) -> None:
        self._seed(event_repository)
        page = event_repository.list(EventQuery(source="plugin.steam"))
        assert len(page.events) == 1
        assert page.events[0].source == "plugin.steam"

    def test_filter_by_subject(self, event_repository) -> None:
        self._seed(event_repository)
        page = event_repository.list(EventQuery(subject_id=PROC_ID))
        assert len(page.events) == 2
        assert all(event.subject is not None and event.subject.id == PROC_ID for event in page.events)

    def test_filter_by_actor(self, event_repository, stored_raw) -> None:
        event = make_event(
            id=new_event_id(),
            actor=EntityRef("process", "proc_01K5R8Z9M0A1B2C3D4E5F6G7H8", "explorer.exe"),
        )
        event_repository.insert(event, ingested_at=INGESTED_AT)

        page = event_repository.list(EventQuery(actor_id="proc_01K5R8Z9M0A1B2C3D4E5F6G7H8"))
        assert len(page.events) == 1

    def test_filter_by_time_range(self, event_repository) -> None:
        self._seed(event_repository)
        base = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)

        page = event_repository.list(
            EventQuery(time_from=base + timedelta(minutes=1), time_to=base + timedelta(minutes=2))
        )
        assert len(page.events) == 2

    def test_time_range_is_inclusive(self, event_repository) -> None:
        self._seed(event_repository)
        base = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        page = event_repository.list(EventQuery(time_from=base, time_to=base))
        assert len(page.events) == 1

    def test_filters_combine(self, event_repository) -> None:
        self._seed(event_repository)
        page = event_repository.list(EventQuery(types=(PROCESS_STARTED,), source="plugin.steam"))
        assert len(page.events) == 1


class TestEventQueryValidation:
    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(InvalidInputError, match="положительным"):
            EventQuery(limit=0)

    def test_limit_has_upper_bound(self) -> None:
        with pytest.raises(InvalidInputError, match="превышать"):
            EventQuery(limit=MAX_PAGE_LIMIT + 1)

    def test_from_after_to_is_rejected(self) -> None:
        base = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        with pytest.raises(InvalidInputError, match="позже"):
            EventQuery(time_from=base + timedelta(hours=1), time_to=base)


class TestProcessInstanceLookup:
    """Поиск родителя для заполнения actor при нормализации (§14, §20)."""

    def _store_started(self, event_repository, *, pid: int, boot_id: str, timestamp: datetime, instance_id: str) -> None:
        event_repository.insert(
            make_event(
                id=new_event_id(),
                timestamp=timestamp,
                raw_event_id=None,
                boot_id=boot_id,
                subject=EntityRef("process", instance_id, f"pid{pid}.exe"),
                attributes={"pid": pid},
            ),
            ingested_at=INGESTED_AT,
        )

    def test_finds_parent_in_same_boot_session(self, event_repository) -> None:
        parent_instance = "proc_01K5R8Z9M0A1B2C3D4E5F6G7H8"
        started = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        self._store_started(
            event_repository, pid=4312, boot_id=BOOT_ID, timestamp=started, instance_id=parent_instance
        )

        found = event_repository.find_process_instance(
            boot_id=BOOT_ID, pid=4312, before=started + timedelta(minutes=5)
        )
        assert found is not None
        assert found.id == parent_instance
        # Имя берётся из события старта родителя: в событиях запуска потомка
        # приходит только parent_pid, поэтому иначе имя было бы неизвестно.
        assert found.name == "pid4312.exe"
        assert found.type == "process"

    def test_returns_none_without_boot_id(self, event_repository) -> None:
        """Без boot session PID может совпасть с процессом прошлой загрузки.

        Подставлять такую «связь» нельзя: это был бы вымысел, а не факт.
        """
        started = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        self._store_started(
            event_repository, pid=4312, boot_id=BOOT_ID, timestamp=started,
            instance_id="proc_01K5R8Z9M0A1B2C3D4E5F6G7H8",
        )

        assert event_repository.find_process_instance(boot_id=None, pid=4312, before=started) is None

    def test_ignores_events_from_another_boot(self, event_repository) -> None:
        started = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        self._store_started(
            event_repository, pid=4312, boot_id=LATER_BOOT_ID, timestamp=started,
            instance_id="proc_01K5R8Z9M0A1B2C3D4E5F6G7H8",
        )

        assert event_repository.find_process_instance(boot_id=BOOT_ID, pid=4312, before=started) is None

    def test_respects_before_bound(self, event_repository) -> None:
        """Родитель не может быть найден по событию, которое произошло позже."""
        started = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        self._store_started(
            event_repository, pid=4312, boot_id=BOOT_ID, timestamp=started,
            instance_id="proc_01K5R8Z9M0A1B2C3D4E5F6G7H8",
        )

        found = event_repository.find_process_instance(
            boot_id=BOOT_ID, pid=4312, before=started - timedelta(seconds=1)
        )
        assert found is None

    def test_ignores_other_pid(self, event_repository) -> None:
        started = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        self._store_started(
            event_repository, pid=4312, boot_id=BOOT_ID, timestamp=started,
            instance_id="proc_01K5R8Z9M0A1B2C3D4E5F6G7H8",
        )

        assert event_repository.find_process_instance(boot_id=BOOT_ID, pid=9999, before=started) is None

    def test_picks_most_recent_instance_for_reused_pid(self, event_repository) -> None:
        """При переиспользовании PID берётся ближайший предшествующий экземпляр."""
        base = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
        older = "proc_01K5R8Z9M0A1B2C3D4E5F6G7H8"
        newer = "proc_01K5R8Z9M1C2D3E4F5G6H7J8K9"

        self._store_started(event_repository, pid=4312, boot_id=BOOT_ID, timestamp=base, instance_id=older)
        self._store_started(
            event_repository, pid=4312, boot_id=BOOT_ID, timestamp=base + timedelta(minutes=10), instance_id=newer
        )

        found = event_repository.find_process_instance(
            boot_id=BOOT_ID, pid=4312, before=base + timedelta(minutes=20)
        )
        assert found is not None
        assert found.id == newer
