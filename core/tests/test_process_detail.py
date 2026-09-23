"""ProcessDetail: экземпляр процесса как сущность во времени (§14, §77.1).

Проверяются четыре случая, ради которых модель сделана терпимой к неполной
истории: полная жизнь, только запуск, **только завершение** и неизвестный
идентификатор. Последний из первых трёх — не экзотика: событие завершения несёт
``process_started_at``, поэтому время старта процесса может быть известно без
наблюдавшегося события запуска, и смешивать эти два факта нельзя.

Отдельно проверяется то, где detail обязан промолчать: противоречащие времена,
неизвестный родитель и нечисловой PID дают пустые поля, а не правдоподобные
значения.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from chronoscope.application.queries.get_process_detail import (
    PROCESS_EVENTS_LIMIT,
    GetProcessDetail,
)
from chronoscope.application.queries.process_detail import (
    ProcessDetail,
    build_process_detail,
)
from chronoscope.domain.errors import HistoryLimitExceededError
from chronoscope.domain.events.event import Event
from tests.factories import (
    BOOT_ID,
    EXIT_TIMESTAMP,
    NOTEPAD_PID,
    PROC_ID,
    SOURCE_TIMESTAMP,
    make_event,
    make_exit_event,
    process_ref,
)

#: Строка, которую нормализатор записывает в атрибут события.
STARTED_AT_ATTRIBUTE = "2026-09-18T10:42:15.220Z"

#: Родительский процесс, чья идентичность установлена нормализатором.
PARENT = process_ref(4312, name="explorer.exe")

LIFETIME = EXIT_TIMESTAMP - SOURCE_TIMESTAMP


class StubEventRepository:
    """Подставное хранилище.

    Порт структурный: наследоваться от него не нужно, достаточно совпадения
    методов (§50). Поэтому use case проверяется без базы — и проверяется именно
    то, что он делает с полученными событиями.
    """

    def __init__(self, events: tuple[Event, ...] = ()) -> None:
        self._events = events
        self.asked: list[tuple[str, int]] = []

    def find_instance_events(self, instance_id: str, *, limit: int) -> tuple[Event, ...]:
        self.asked.append((instance_id, limit))
        return self._events[:limit]


def start_event(**overrides) -> Event:  # noqa: ANN003
    attributes = {"pid": NOTEPAD_PID, "process_started_at": STARTED_AT_ATTRIBUTE}
    attributes.update(overrides.pop("attributes", {}))
    return make_event(attributes=attributes, **overrides)


def exit_event(**overrides) -> Event:  # noqa: ANN003
    """Событие завершения — с временем старта, как его отдаёт источник.

    Коллектор запоминает время старта процесса и подставляет его в событие
    выхода, поэтому ``process_started_at`` есть и здесь.
    """
    attributes = {"pid": NOTEPAD_PID, "process_started_at": STARTED_AT_ATTRIBUTE, "exit_code": 0}
    attributes.update(overrides.pop("attributes", {}))
    return make_exit_event(attributes=attributes, **overrides)


def detail_of(*events: Event) -> ProcessDetail:
    detail = GetProcessDetail(StubEventRepository(tuple(events))).execute(PROC_ID)

    assert detail is not None, "detail не собран"
    return detail


class TestLifecycle:
    """Три состояния истории и то, что каждое из них означает."""

    def test_full_lifecycle(self) -> None:
        detail = detail_of(start_event(), exit_event())

        assert detail.observed_start is True
        assert detail.observed_exit is True
        assert detail.started_at == SOURCE_TIMESTAMP
        assert detail.exited_at == EXIT_TIMESTAMP
        assert detail.duration == LIFETIME

    def test_start_without_exit(self) -> None:
        """Выход пропущен или процесс ещё жив — и модель не утверждает второго."""
        detail = detail_of(start_event())

        assert detail.observed_start is True
        assert detail.observed_exit is False
        assert detail.started_at == SOURCE_TIMESTAMP
        assert detail.exited_at is None
        assert detail.duration is None

    def test_exit_without_observed_start(self) -> None:
        """Время старта известно, а событие запуска — нет.

        Это два разных факта, и они не смешиваются: ``started_at`` заполнен,
        ``observed_start`` — ``false``. Длительность при этом считается: она
        выводится из времён, а не из наблюдений.
        """
        detail = detail_of(exit_event())

        assert detail.observed_start is False
        assert detail.observed_exit is True
        assert detail.started_at == SOURCE_TIMESTAMP
        assert detail.exited_at == EXIT_TIMESTAMP
        assert detail.duration == LIFETIME

    def test_identity_comes_from_events(self) -> None:
        detail = detail_of(start_event(), exit_event())

        assert detail.id == PROC_ID
        assert detail.name == "notepad.exe"
        assert detail.pid == NOTEPAD_PID
        assert detail.boot_id == BOOT_ID

    def test_unknown_instance_is_not_an_error(self) -> None:
        """Об экземпляре ничего не известно — вернуть нечего, а не выдумать."""
        assert GetProcessDetail(StubEventRepository()).execute(PROC_ID) is None

    def test_query_goes_by_instance_not_by_pid(self) -> None:
        """Запрос идёт по экземпляру: PID недостаточен, он переиспользуется (§14)."""
        repository = StubEventRepository((start_event(),))

        GetProcessDetail(repository).execute(PROC_ID)

        assert repository.asked == [(PROC_ID, PROCESS_EVENTS_LIMIT + 1)]


class TestTolerance:
    """Неполные и противоречивые данные не превращаются в правдоподобный ответ."""

    def test_order_of_events_does_not_change_detail(self) -> None:
        """Порядок выборки — деталь запроса, detail от него не зависит."""
        straight = detail_of(start_event(), exit_event())
        reversed_order = detail_of(exit_event(), start_event())

        assert straight == reversed_order

    def test_contradicting_times_do_not_produce_duration(self) -> None:
        """Отрицательная длительность жизни — величина, которой в данных не было.

        Источник сообщил старт позже завершения. Оба времени сохраняются как
        есть, а длительность не выводится: «минус сто секунд жизни» — не
        неточность, а утверждение, которого никто не делал.
        """
        later_start = datetime(2026, 9, 18, 10, 50, 0, tzinfo=UTC)
        detail = detail_of(
            start_event(
                timestamp=later_start,
                attributes={"process_started_at": "2026-09-18T10:50:00.000Z"},
            ),
            # Событие завершения без времени старта: иначе самое раннее из времён
            # снова оказалось бы раньше завершения, и случай перестал бы быть
            # противоречивым.
            make_exit_event(attributes={"pid": NOTEPAD_PID}),
        )

        assert detail.started_at == later_start
        assert detail.exited_at == EXIT_TIMESTAMP
        assert detail.duration is None

    def test_pid_that_is_not_a_number_is_not_used(self) -> None:
        """``True`` вместо PID — не PID: подставить его значило бы выдумать число."""
        detail = detail_of(start_event(attributes={"pid": True}))

        assert detail.pid is None

    def test_duration_is_zero_for_instant_process(self) -> None:
        """Мгновенный процесс имеет нулевую длительность, а не пустое поле."""
        moment = datetime(2026, 9, 18, 11, 0, 0, tzinfo=UTC)
        detail = detail_of(
            start_event(timestamp=moment, attributes={"process_started_at": "2026-09-18T11:00:00.000Z"}),
            exit_event(timestamp=moment, attributes={"process_started_at": "2026-09-18T11:00:00.000Z"}),
        )

        assert detail.duration == timedelta(0)

    def test_model_has_no_state_field(self) -> None:
        """Модель не отвечает на вопрос «работает ли процесс сейчас» (§77.1, пункт 9).

        Пока событие выхода может быть пропущено, любой ответ на него был бы
        догадкой. Поля состояния нет намеренно, и этот тест сторожит именно это
        решение: добавить поле можно только осознанно — вместе с правкой здесь.
        """
        assert {field.name for field in fields(ProcessDetail)} == {
            "id",
            "name",
            "pid",
            "boot_id",
            "started_at",
            "exited_at",
            "duration",
            "parent",
            "observed_start",
            "observed_exit",
        }


class TestParent:
    def test_identity_when_resolved(self) -> None:
        detail = detail_of(start_event(actor=PARENT, attributes={"parent_pid": 4312}))

        assert detail.parent.resolved is True
        assert detail.parent.id == PARENT.id
        assert detail.parent.name == "explorer.exe"
        assert detail.parent.pid == 4312

    def test_pid_remains_when_identity_is_unknown(self) -> None:
        """Родителя не наблюдали: PID известен, идентичность — нет, и это разные вещи."""
        detail = detail_of(start_event(attributes={"parent_pid": 4312, "parent_resolved": False}))

        assert detail.parent.resolved is False
        assert detail.parent.id is None
        assert detail.parent.name is None
        assert detail.parent.pid == 4312

    def test_identity_resolved_in_later_event_is_used(self) -> None:
        """Связь ищется по всем событиям экземпляра, а не только по запуску.

        При нормализации запуска родителя могло ещё не быть в хранилище, а к
        моменту завершения он появился. Это не догадка: оба наблюдения записаны
        в событиях одного экземпляра.
        """
        detail = detail_of(
            start_event(attributes={"parent_pid": 4312, "parent_resolved": False}),
            exit_event(actor=PARENT, attributes={"parent_pid": 4312}),
        )

        assert detail.parent.resolved is True
        assert detail.parent.id == PARENT.id
        assert detail.parent.pid == 4312

    def test_foreign_parent_identity_is_not_used(self) -> None:
        """Если источник назвал разные родительские PID, связь не выдаётся за установленную."""
        detail = detail_of(
            start_event(attributes={"parent_pid": 4312}),
            exit_event(actor=PARENT, attributes={"parent_pid": 9999}),
        )

        assert detail.parent.pid == 4312
        assert detail.parent.resolved is False
        assert detail.parent.id is None


class TestHistoryLimit:
    """Предел чтения: отказ вместо молча усечённого detail."""

    @staticmethod
    def events(count: int) -> tuple[Event, ...]:
        """События одного экземпляра: важно их количество, а не смысл каждого."""
        return tuple(start_event(id=f"evt_{index:026d}", raw_event_id=None) for index in range(count))

    def test_history_within_limit_is_read(self) -> None:
        detail = detail_of(*self.events(PROCESS_EVENTS_LIMIT))

        assert detail.observed_start is True

    def test_history_longer_than_limit_is_refused(self) -> None:
        """Усечённая история сообщила бы о процессе то, чего в данных нет."""
        with pytest.raises(HistoryLimitExceededError) as error:
            detail_of(*self.events(PROCESS_EVENTS_LIMIT + 1))

        assert error.value.limit == PROCESS_EVENTS_LIMIT
        assert error.value.process_instance_id == PROC_ID

    def test_builder_has_no_limit_of_its_own(self) -> None:
        """Предел — правило use case, а не модели: сборка detail из готовых событий проста."""
        detail = build_process_detail(PROC_ID, (start_event(), exit_event()))

        assert detail.duration == LIFETIME
