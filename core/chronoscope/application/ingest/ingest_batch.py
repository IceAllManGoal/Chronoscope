"""Use case ``IngestBatch`` — приём пакета сырых событий (§31, §34, §60).

Пакет обрабатывается событие за событием, и сбой одного события не прерывает
остальные: §60 требует, чтобы одно плохое событие не останавливало pipeline.

Пакет пишется **одной транзакцией** (§34, ADR-0004). ADR-0004 называет
смягчением ограничения «один писатель в SQLite» батчевые вставки и короткие
транзакции, а приём был устроен наоборот: два коммита на каждое событие, то есть
сто коммитов на пакет из пятидесяти. Теперь коммит один.

У этого есть две стороны, и молчать о второй нельзя. Хорошая: сырая запись и
нормализованное событие попадают в базу вместе — раньше падение Core между ними
оставляло запись, которую повторная доставка уже не нормализует, потому что
видит ``raw_event_id`` и отвечает ``duplicate``. Плохая: отказ **хранилища**
(например, кончилось место) откатывает весь пакет, а не его часть. Это осознанный
выбор: частично применённый пакет выглядел бы как история, в которой чего-то не
хватает, и заметить это было бы нечем. Agent повторит отправку — доставка
at-least-once (§34), а идемпотентность не даст создать дубли.

Обрати внимание, что здесь нет ни слова о HTTP. Ошибки отдельных событий уже
отфильтрованы на уровне API (там проверяется транспортная схема), а сюда
попадают только валидные доменные объекты. Ошибки нормализации внутри
отражаются в счётчиках, а не выбрасываются наружу.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from chronoscope.application.ingest.ingest_raw_event import IngestRawEvent
from chronoscope.application.ports import UnitOfWorkFactory
from chronoscope.domain.events.raw_event import RawEvent


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """Итог приёма пакета."""

    accepted: int = 0
    duplicates: int = 0
    normalization_failed: int = 0
    event_ids: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.accepted + self.duplicates

    @property
    def nothing_stored(self) -> bool:
        """Ни одно событие из пакета не было сохранено впервые."""
        return self.accepted == 0


class IngestBatch:
    def __init__(self, ingest_raw_event: IngestRawEvent, unit_of_work: UnitOfWorkFactory) -> None:
        self._ingest_raw_event = ingest_raw_event
        self._unit_of_work = unit_of_work

    def execute(self, raw_events: Sequence[RawEvent]) -> BatchOutcome:
        accepted = 0
        duplicates = 0
        normalization_failed = 0
        event_ids: list[str] = []

        with self._unit_of_work() as unit:
            for raw_event in raw_events:
                outcome = self._ingest_raw_event.execute_in(unit, raw_event)

                if outcome.duplicate:
                    duplicates += 1
                    continue

                accepted += 1
                if not outcome.normalized:
                    normalization_failed += 1
                elif outcome.event_id is not None:
                    event_ids.append(outcome.event_id)

        return BatchOutcome(
            accepted=accepted,
            duplicates=duplicates,
            normalization_failed=normalization_failed,
            event_ids=tuple(event_ids),
        )
