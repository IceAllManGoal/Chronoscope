"""Границы транзакции при приёме (§34, ADR-0004, §60).

Проверяется не «стало быстрее», а поведение, которое изменилось вместе с
границей транзакции:

- пакет применяется целиком или не применяется вовсе;
- ошибка нормализации внутри пакета не откатывает уже принятые события (§60);
- идемпотентность внутри одного пакета работает так же, как между пакетами (§34);
- чтение внутри транзакции видит то, что записано в ней же.

Проверки сформулированы через наблюдаемое состояние базы, а не через подсчёт
вызовов commit: считать коммиты значило бы проверять реализацию, а не обещание.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chronoscope.application.ingest.ingest_batch import IngestBatch
from chronoscope.application.ingest.ingest_raw_event import IngestRawEvent
from chronoscope.application.ports import EventQuery
from chronoscope.domain.errors import NormalizationError
from chronoscope.normalization.registry import NormalizerRegistry, default_registry
from chronoscope.infrastructure.database.unit_of_work import SqliteUnitOfWork
from tests.factories import BOOT_ID, make_raw_event

INGESTED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class ExplodingRegistry(NormalizerRegistry):
    """Реестр, который падает на выбранном по счёту событии.

    Сбой здесь — не ошибка нормализации (та ловится и пакет не прерывает), а
    отказ, который обязан привести к откату всей транзакции: так же выглядит
    падение Core или отказ хранилища. Нормализация делегируется обычному реестру,
    чтобы падение оставалось единственным отличием от рабочего пути.
    """

    def __init__(self, explode_on: int) -> None:
        super().__init__()
        self._inner = default_registry()
        self._explode_on = explode_on
        self._calls = 0

    def normalize(self, raw_event, context):  # noqa: ANN001, ANN201
        self._calls += 1
        if self._calls == self._explode_on:
            raise RuntimeError("имитация отказа во время приёма пакета")
        return self._inner.normalize(raw_event, context)


class FailingNormalizerRegistry(NormalizerRegistry):
    """Реестр, который отвечает ошибкой нормализации на выбранном событии (§60)."""

    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self._inner = default_registry()
        self._fail_on = fail_on
        self._calls = 0

    def normalize(self, raw_event, context):  # noqa: ANN001, ANN201
        self._calls += 1
        if self._calls == self._fail_on:
            raise NormalizationError("имитация дефекта данных")
        return self._inner.normalize(raw_event, context)


@pytest.fixture
def unit_of_work(migrated_engine):  # noqa: ANN001, ANN201
    return lambda: SqliteUnitOfWork(migrated_engine)


def build_batch(unit_of_work, registry: NormalizerRegistry) -> IngestBatch:  # noqa: ANN001
    ingest = IngestRawEvent(unit_of_work=unit_of_work, registry=registry, clock=lambda: INGESTED_AT)
    return IngestBatch(ingest, unit_of_work)


def raw_event(index: int, *, pid: int = 9812, parent_pid: int | None = None, hour: int = 11, minute: int = 0):
    """Сырое событие запуска процесса с заданными PID и временем."""
    payload = {"pid": pid, "name": f"proc{pid}.exe"}
    if parent_pid is not None:
        payload["parent_pid"] = parent_pid
    moment = datetime(2026, 9, 20, hour, minute, index % 60, tzinfo=UTC)
    payload["process_started_at"] = moment.isoformat().replace("+00:00", "Z")

    return make_raw_event(
        raw_event_id=f"raw_01K5R8Z9M5P8W3X7Y2C4E6G8J{index}",
        payload=payload,
        observed_at=moment,
        source_timestamp=moment,
    )


class TestBatchTransaction:
    def test_failure_mid_batch_rolls_back_everything(
        self, unit_of_work, raw_repository, event_repository  # noqa: ANN001
    ) -> None:
        """Отказ на середине пакета не оставляет применённой части.

        Это и есть то, ради чего граница транзакции перенесена на пакет: раньше
        первое событие было уже закоммичено, а сырая запись без нормализованного
        события оставалась в базе навсегда — повторная доставка видит
        ``raw_event_id`` и отвечает ``duplicate``, не нормализуя.
        """
        events = [raw_event(index) for index in range(3)]
        batch = build_batch(unit_of_work, ExplodingRegistry(explode_on=2))

        with pytest.raises(RuntimeError, match="имитация отказа"):
            batch.execute(events)

        assert raw_repository.count() == 0, "сырые события первого события остались в базе"
        assert event_repository.count() == 0

    def test_normalization_failure_does_not_roll_back_batch(
        self, unit_of_work, raw_repository, event_repository  # noqa: ANN001
    ) -> None:
        """§60: одно плохое событие не останавливает pipeline и не отменяет остальные."""
        events = [raw_event(index) for index in range(3)]
        batch = build_batch(unit_of_work, FailingNormalizerRegistry(fail_on=2))

        outcome = batch.execute(events)

        assert outcome.accepted == 3, "пакет должен был принять все три события"
        assert outcome.normalization_failed == 1
        assert outcome.event_ids is not None and len(outcome.event_ids) == 2
        # Сырое событие сохраняется до нормализации (§11), поэтому дефект данных
        # не превращается в потерю: в базе три сырых записи и два события.
        assert raw_repository.count() == 3
        assert event_repository.count() == 2

    def test_duplicate_inside_one_batch_is_counted_once(
        self, unit_of_work, raw_repository, event_repository  # noqa: ANN001
    ) -> None:
        """§34: повтор внутри одного пакета — тот же дубликат, что и между пакетами."""
        duplicate = raw_event(0)
        other = raw_event(1, pid=9813)
        batch = build_batch(unit_of_work, default_registry())

        outcome = batch.execute([duplicate, duplicate, other])

        assert outcome.accepted == 2
        assert outcome.duplicates == 1
        assert raw_repository.count() == 2
        assert event_repository.count() == 2

    def test_parent_from_same_batch_is_visible(
        self, unit_of_work, event_repository  # noqa: ANN001
    ) -> None:
        """Родитель, принятый в этом же пакете, уже виден нормализатору.

        Пакет — одна транзакция, и чтение внутри неё видит незакоммиченные
        строки. Для истории это означает, что связь ``parent.exe → child.exe``
        не теряется, когда оба события пришли одним пакетом.
        """
        parent = raw_event(0, pid=5000, hour=11, minute=0)
        child = raw_event(1, pid=5001, parent_pid=5000, hour=11, minute=5)
        assert parent.boot_id == BOOT_ID, "без boot_id поиск родителя не выполняется вовсе"

        batch = build_batch(unit_of_work, default_registry())
        outcome = batch.execute([parent, child])

        assert outcome.accepted == 2

        stored = event_repository.list(EventQuery(limit=10)).events
        child_event = next(event for event in stored if event.subject and event.subject.name == "proc5001.exe")
        parent_event = next(event for event in stored if event.subject and event.subject.name == "proc5000.exe")

        assert child_event.actor is not None, "родитель из того же пакета не найден"
        assert child_event.actor.name == "proc5000.exe"
        assert child_event.actor.id == parent_event.subject.id, (
            "актор должен ссылаться на экземпляр родителя, а не на PID"
        )
