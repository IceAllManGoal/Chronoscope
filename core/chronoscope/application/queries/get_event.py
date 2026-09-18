"""Use case ``GetEvent`` — одно событие по идентификатору (§31).

Возвращает ``None`` вместо исключения: отсутствие события — не ошибка
пайплайна, а нормальный результат запроса, и решение о том, что это 404,
принимает транспортный слой.
"""

from __future__ import annotations

from chronoscope.domain.events.event import Event
from chronoscope.infrastructure.database.repositories import EventRepository


class GetEvent:
    def __init__(self, event_repository: EventRepository) -> None:
        self._event_repository = event_repository

    def execute(self, event_id: str) -> Event | None:
        return self._event_repository.get(event_id)
