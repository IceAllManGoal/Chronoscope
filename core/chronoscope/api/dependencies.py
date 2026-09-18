"""Композиционный корень Core и зависимости FastAPI.

Все зависимости собираются в одном месте — :class:`CoreContainer`. Это
единственная точка, где инфраструктура соединяется с use cases, поэтому
подменить любое звено (например, подставить временную БД в тестах или
собственный clock) можно здесь, не трогая ни один обработчик.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterator

from fastapi import Depends, Request
from sqlalchemy import Engine

from chronoscope import __version__
from chronoscope.application.ingest.ingest_batch import IngestBatch
from chronoscope.application.ingest.ingest_raw_event import IngestRawEvent
from chronoscope.application.queries.core_status import GetCoreStatus
from chronoscope.application.queries.get_event import GetEvent
from chronoscope.application.queries.list_events import ListEvents
from chronoscope.infrastructure.config.settings import CoreSettings
from chronoscope.infrastructure.database.engine import create_database_engine
from chronoscope.infrastructure.database.repositories import (
    EventRepository,
    RawEventRepository,
)
from chronoscope.normalization.registry import NormalizerRegistry, default_registry

CONTAINER_STATE_KEY = "chronoscope_container"


@dataclass(slots=True)
class CoreContainer:
    settings: CoreSettings
    engine: Engine
    registry: NormalizerRegistry
    raw_repository: RawEventRepository
    event_repository: EventRepository
    ingest_raw_event: IngestRawEvent
    ingest_batch: IngestBatch
    list_events: ListEvents
    get_event: GetEvent
    get_core_status: GetCoreStatus
    version: str

    @classmethod
    def build(
        cls,
        settings: CoreSettings,
        *,
        version: str = __version__,
        registry: NormalizerRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> "CoreContainer":
        engine = create_database_engine(settings)
        raw_repository = RawEventRepository(engine)
        event_repository = EventRepository(engine)
        normalizer_registry = registry or default_registry()

        ingest_raw_event = IngestRawEvent(
            raw_repository=raw_repository,
            event_repository=event_repository,
            registry=normalizer_registry,
            clock=clock,
        )

        return cls(
            settings=settings,
            engine=engine,
            registry=normalizer_registry,
            raw_repository=raw_repository,
            event_repository=event_repository,
            ingest_raw_event=ingest_raw_event,
            # Один и тот же экземпляр use case: пакетная обработка не должна
            # пересобирать зависимости заново, иначе настройки и логирование
            # разъедутся с одиночным приёмом.
            ingest_batch=IngestBatch(ingest_raw_event),
            list_events=ListEvents(event_repository),
            get_event=GetEvent(event_repository),
            get_core_status=GetCoreStatus(
                raw_repository=raw_repository,
                event_repository=event_repository,
                settings=settings,
                version=version,
                clock=clock,
            ),
            version=version,
        )

    def close(self) -> None:
        self.engine.dispose()


def build_container(app, settings: CoreSettings, **kwargs) -> CoreContainer:  # noqa: ANN001
    container = CoreContainer.build(settings, **kwargs)
    setattr(app.state, CONTAINER_STATE_KEY, container)
    return container


def get_container(request: Request) -> CoreContainer:
    return getattr(request.app.state, CONTAINER_STATE_KEY)


def get_settings(container: CoreContainer = Depends(get_container)) -> CoreSettings:
    return container.settings


def get_ingest_batch(container: CoreContainer = Depends(get_container)) -> IngestBatch:
    return container.ingest_batch


def get_list_events(container: CoreContainer = Depends(get_container)) -> ListEvents:
    return container.list_events


def get_get_event(container: CoreContainer = Depends(get_container)) -> GetEvent:
    return container.get_event


def get_get_core_status(container: CoreContainer = Depends(get_container)) -> GetCoreStatus:
    return container.get_core_status


def iter_dependencies(container: CoreContainer) -> Iterator[object]:
    """Помощник для тестов: перечислить собранные зависимости."""
    yield container.raw_repository
    yield container.event_repository
    yield container.ingest_raw_event
    yield container.ingest_batch
    yield container.list_events
    yield container.get_event
    yield container.get_core_status
