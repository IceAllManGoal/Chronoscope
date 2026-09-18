"""Реестр нормализаторов (§49).

Нормализатор — чистая функция «сырое событие → нормализованное событие».
Единственное, что ему нужно от остальной системы, — возможность найти
экземпляр процесса по PID: родитель известен событию только как ``parent_pid``,
а идентичностью является ``process_instance_id``, выводимый из времени старта
родителя, которого в событии нет (§14).

Этот доступ оформлен как порт (:class:`ProcessInstanceLookup`), а не как
прямая зависимость от репозитория: нормализатор можно тестировать без базы
данных, подставив тривиальную реализацию. Так же обеспечивается требование
§50 — нормализация остаётся свободной от инфраструктуры.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from chronoscope.domain.errors import NormalizationError
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.raw_event import RawEvent


class ProcessInstanceLookup(Protocol):
    """Поиск экземпляра процесса среди уже сохранённых событий.

    Возвращается ссылка на сущность, а не строка с идентификатором: имя
    родителя уже сохранено в его собственном событии старта, и отдавать его
    вместе с идентификатором дешевле, чем заставлять вызывающий код идти за
    ним вторым запросом.
    """

    def __call__(self, *, boot_id: str | None, pid: int, before: datetime) -> EntityRef | None: ...


def no_process_lookup(*, boot_id: str | None, pid: int, before: datetime) -> EntityRef | None:
    """Реализация-заглушка: ничего не находит.

    Используется там, где искать негде (изолированные тесты нормализатора) и
    означает не «родителя нет», а «искать не пытались»: в этом случае
    ``actor`` остаётся ``None``.
    """
    return None


@dataclass(frozen=True, slots=True)
class NormalizationContext:
    """Всё, что нормализатору нужно из внешнего мира."""

    find_process_instance: ProcessInstanceLookup = no_process_lookup


NormalizerHandler = Callable[[RawEvent, NormalizationContext], Event]


class NormalizerRegistry:
    """Сопоставление «коллектор + тип payload» → обработчик."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], NormalizerHandler] = {}

    def register(self, *, collector: str, payload_type: str, handler: NormalizerHandler) -> None:
        key = (collector, payload_type)
        if key in self._handlers:
            raise NormalizationError(
                f"нормализатор для {collector}/{payload_type} уже зарегистрирован"
            )
        self._handlers[key] = handler

    def resolve(self, collector: str, payload_type: str) -> NormalizerHandler | None:
        return self._handlers.get((collector, payload_type))

    def knows(self, collector: str, payload_type: str) -> bool:
        return (collector, payload_type) in self._handlers

    @property
    def registered(self) -> frozenset[tuple[str, str]]:
        return frozenset(self._handlers)

    def normalize(self, raw_event: RawEvent, context: NormalizationContext) -> Event:
        """Преобразовать сырое событие в нормализованное.

        Неизвестная пара коллектор/payload — это не сбой pipeline, а сигнал о
        том, что Agent новее Core. Сырое событие уже сохранено (§60), поэтому
        исключение здесь приводит лишь к тому, что событие не попадёт в
        timeline — но не потеряется.
        """
        handler = self.resolve(raw_event.collector, raw_event.payload_type)
        if handler is None:
            raise NormalizationError(
                f"нет нормализатора для collector={raw_event.collector!r} "
                f"payload_type={raw_event.payload_type!r}"
            )
        return handler(raw_event, context)


def default_registry() -> NormalizerRegistry:
    """Реестр со всеми нормализаторами, поддерживаемыми этой версией Core."""
    from chronoscope.domain.events.event_type import (
        COLLECTOR_WINDOWS_PROCESS,
        PAYLOAD_PROCESS_EXIT,
        PAYLOAD_PROCESS_START,
    )
    from chronoscope.normalization.windows.process_normalizer import (
        normalize_process_exit,
        normalize_process_start,
    )

    registry = NormalizerRegistry()
    registry.register(
        collector=COLLECTOR_WINDOWS_PROCESS,
        payload_type=PAYLOAD_PROCESS_START,
        handler=normalize_process_start,
    )
    registry.register(
        collector=COLLECTOR_WINDOWS_PROCESS,
        payload_type=PAYLOAD_PROCESS_EXIT,
        handler=normalize_process_exit,
    )
    return registry
