"""Use case ``IngestRawEvent`` — приём одного сырого события (§9.1, §34, §60).

Порядок шагов принципиален и повторяет §11: **сначала raw, потом нормализация**.
Сырое событие сохраняется до любой интерпретации, поэтому ошибка нормализатора
не превращается в потерю данных.

Идемпотентность (§34). Если событие с таким ``raw_event_id`` уже сохранено,
нормализация не запускается вообще: иначе повторная доставка того же события
породила бы второе нормализованное событие с новым идентификатором, и timeline
заполнился бы дублями.

Транзакцией управляет не этот класс, а вызывающий: ``execute`` открывает
собственную единицу работы на одно событие, а ``execute_in`` работает внутри
чужой — той, которую открыл пакет. Логика приёма при этом одна и та же, и
расходиться двум её копиям негде.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from chronoscope.application.ports import UnitOfWorkFactory, UnitOfWorkPort
from chronoscope.domain.errors import InvalidInputError, NormalizationError
from chronoscope.domain.events.raw_event import RawEvent
from chronoscope.normalization.registry import NormalizationContext, NormalizerRegistry

#: Имя логгера внутри иерархии Chronoscope. Обработчики и JSON-формат настраивает
#: infrastructure (§35); здесь важно только попасть в ту же иерархию. Постоянная
#: часть имени повторена осознанно: импортировать её из infrastructure означало бы
#: вернуть зависимость application от слоя технологий ради одной строки (§50).
LOGGER_NAME = "chronoscope.ingest"

#: Ошибки, которые считаются сбоем нормализации, а не сбоем Core.
#: ``InvalidInputError`` попадает сюда потому, что некорректный payload
#: источника — это дефект данных, а не отказ системы (§60).
NORMALIZATION_FAILURES = (NormalizationError, InvalidInputError)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **fields: Any,
) -> None:
    """Записать структурированное событие лога.

    Используется стандартный API ``logging``, а не помощник из infrastructure:
    произвольные поля становятся полями JSON-записи, а это единственное, что
    должен знать application. Как именно они попадут в вывод — забота слоя,
    который настраивает обработчики (§35, §50).
    """
    logger.log(level, message, extra={"event": event, **fields})


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Результат приёма одного сырого события."""

    raw_event_id: str
    stored: bool
    normalized: bool
    event_id: str | None = None
    failure: str | None = None

    @property
    def duplicate(self) -> bool:
        return not self.stored


class IngestRawEvent:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        registry: NormalizerRegistry,
        clock: Callable[[], datetime] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._registry = registry
        self._clock = clock or (lambda: datetime.now(UTC))
        self._logger = logger or logging.getLogger(LOGGER_NAME)

    def execute(self, raw_event: RawEvent) -> IngestOutcome:
        """Принять одно событие в собственной транзакции."""
        with self._unit_of_work() as unit:
            return self.execute_in(unit, raw_event)

    def execute_in(self, unit: UnitOfWorkPort, raw_event: RawEvent) -> IngestOutcome:
        """Принять событие внутри чужой транзакции — так работает пакет (§34).

        Коммит здесь не делается: граница транзакции принадлежит тому, кто её
        открыл. Отсюда два следствия, и оба нужны. Первое: сырая запись и
        нормализованное событие оказываются в базе вместе, а не по отдельности —
        незавершённый пакет Agent отправит снова. Второе: ошибка нормализации
        внутри пакета не откатывает уже принятые события, потому что §60 требует,
        чтобы одно плохое событие не останавливало pipeline.
        """
        ingested_at = self._clock()

        try:
            stored = unit.raw_events.insert(raw_event, ingested_at=ingested_at)
        except Exception:
            log_event(
                self._logger,
                logging.ERROR,
                "raw_event_store_failed",
                "не удалось сохранить сырое событие",
                raw_event_id=raw_event.raw_event_id,
                collector=raw_event.collector,
            )
            raise

        if not stored:
            log_event(
                self._logger,
                logging.INFO,
                "raw_event_duplicate",
                "повторная доставка сырого события, дубликат не создан",
                raw_event_id=raw_event.raw_event_id,
                collector=raw_event.collector,
            )
            return IngestOutcome(raw_event_id=raw_event.raw_event_id, stored=False, normalized=False)

        context = NormalizationContext(find_process_instance=unit.events.find_process_instance)

        try:
            event = self._registry.normalize(raw_event, context)
        except NORMALIZATION_FAILURES as exc:
            # §60: raw event уже сохранён, пайплайн продолжает работу.
            log_event(
                self._logger,
                logging.WARNING,
                "normalization_failed",
                "нормализация не удалась, сырое событие сохранено",
                raw_event_id=raw_event.raw_event_id,
                collector=raw_event.collector,
                payload_type=raw_event.payload_type,
                error=str(exc),
            )
            return IngestOutcome(
                raw_event_id=raw_event.raw_event_id,
                stored=True,
                normalized=False,
                failure=str(exc),
            )

        event_stored = unit.events.insert(event, ingested_at=ingested_at)

        log_event(
            self._logger,
            logging.INFO,
            "event_stored",
            "нормализованное событие сохранено",
            raw_event_id=raw_event.raw_event_id,
            event_id=event.id,
            event_type=event.type,
            duplicate=not event_stored,
        )

        return IngestOutcome(
            raw_event_id=raw_event.raw_event_id,
            stored=True,
            normalized=True,
            event_id=event.id,
        )
