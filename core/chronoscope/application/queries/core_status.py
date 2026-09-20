"""Use case ``GetCoreStatus`` — состояние Core и объёмы данных (§31, §57).

§31 показывает в ответе ``/status`` также состояние Agent. В 0.0.1 оно не
отслеживается: Agent ещё не существует, а придумывать поле «agent.connected»
со значением «неизвестно» означало бы показывать пользователю данные, которых
у Core нет. Поэтому ответ содержит только то, что Core действительно знает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from chronoscope.application.ports import EventRepositoryPort, RawEventRepositoryPort

#: Файлы, которые SQLite создаёт рядом с основной базой.
SIDECAR_SUFFIXES = ("", "-wal", "-shm")

STATUS_WINDOW = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class CoreStatus:
    version: str
    events_total: int
    events_last_minute: int
    raw_events_total: int
    database_path: str
    database_size_bytes: int


def database_size_bytes(database_path: Path) -> int:
    """Суммарный размер базы вместе со служебными файлами SQLite.

    Считать только основной файл было бы неверно: в режиме WAL существенная
    часть данных лежит в ``-wal``, и «размер базы» оказался бы заниженным
    ровно тогда, когда запись идёт активно.
    """
    total = 0
    for suffix in SIDECAR_SUFFIXES:
        candidate = Path(f"{database_path}{suffix}")
        if candidate.is_file():
            total += candidate.stat().st_size
    return total


class GetCoreStatus:
    """Состояние Core и объёмы данных.

    Принимает путь к базе, а не объект настроек: use case нужно одно значение, а
    не весь ``CoreSettings``. Пока сюда передавался объект конфигурации, слой
    application зависел от модуля настроек инфраструктуры ради одного поля —
    и вместе с ним от всего, что этот модуль тянет за собой (§50).
    """

    def __init__(
        self,
        *,
        raw_repository: RawEventRepositoryPort,
        event_repository: EventRepositoryPort,
        database_path: Path,
        version: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._raw_repository = raw_repository
        self._event_repository = event_repository
        self._database_path = database_path
        self._version = version
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self) -> CoreStatus:
        moment = self._clock()

        return CoreStatus(
            version=self._version,
            events_total=self._event_repository.count(),
            events_last_minute=self._event_repository.count_since(moment - STATUS_WINDOW),
            raw_events_total=self._raw_repository.count(),
            database_path=str(self._database_path),
            database_size_bytes=database_size_bytes(self._database_path),
        )
