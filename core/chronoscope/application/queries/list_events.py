"""Use case ``ListEvents`` — постраничный список событий (§31, §32).

Тонкая обёртка над репозиторием, и это осознанно: use case существует как
точка входа приложения, чтобы API зависел от намерения («показать события»), а
не от репозитория напрямую (§50). Когда появятся правила доступа, фильтрация
или обогащение выдачи, они добавятся здесь, не затрагивая ни API, ни хранение.
"""

from __future__ import annotations

from chronoscope.infrastructure.database.repositories import (
    EventPage,
    EventQuery,
    EventRepository,
)


class ListEvents:
    def __init__(self, event_repository: EventRepository) -> None:
        self._event_repository = event_repository

    def execute(self, query: EventQuery) -> EventPage:
        return self._event_repository.list(query)
