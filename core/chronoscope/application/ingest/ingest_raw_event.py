"""Use case ``IngestRawEvent`` — приём одного сырого события (§9.1, §34, §60).

Порядок шагов принципиален и повторяет §11: **сначала raw, потом нормализация**.
Сырое событие сохраняется до любой интерпретации, поэтому ошибка нормализатора
не превращается в потерю данных.

Идемпотентность (§34). Если событие с таким ``raw_event_id`` уже сохранено,
нормализация не запускается вообще: иначе повторная доставка того же события
породила бы второе нормализованное событие с новым идентификатором, и timeline
заполнился бы дублями.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from chronoscope.domain.errors import InvalidInputError, NormalizationError
from chronoscope.domain.events.raw_event import RawEvent
from chronoscope.infrastructure.database.repositories import (
    EventRepository,
    RawEventRepository,
)
from chronoscope.infrastructure.logging.setup import get_logger, log_event
from chronoscope.normalization.registry import NormalizationContext, NormalizerRegistry

#: Ошибки, которые считаются сбоем нормализации, а не сбоем Core.
#: ``InvalidInputError`` попадает сюда потому, что некорректный payload
#: источника — это дефект данных, а не отказ системы (§60).
NORMALIZATION_FAILURES = (NormalizationError, InvalidInputError)


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
        raw_repository: RawEventRepository,
        event_repository: EventRepository,
        registry: NormalizerRegistry,
        clock: Callable[[], datetime] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._raw_repository = raw_repository
        self._event_repository = event_repository
        self._registry = registry
        self._clock = clock or (lambda: datetime.now(UTC))
        self._logger = logger or get_logger("ingest")

    def execute(self, raw_event: RawEvent) -> IngestOutcome:
        ingested_at = self._clock()

        try:
            stored = self._raw_repository.insert(raw_event, ingested_at=ingested_at)
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

        context = NormalizationContext(find_process_instance=self._event_repository.find_process_instance)

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

        event_stored = self._event_repository.insert(event, ingested_at=ingested_at)

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
