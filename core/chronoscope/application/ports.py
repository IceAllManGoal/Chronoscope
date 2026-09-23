"""Порты application-слоя (§50).

§50 описывает поток ``API → Use Case → Repository``. Repository здесь — понятие
application, а не конкретная технология: «сохранить событие» и «отдать страницу
событий» — это то, что умеет делать приложение, а то, что под этим лежит SQLite,
знать ему незачем.

Пока `application/` импортировал `infrastructure.database.repositories`, само
направление зависимости было обратным: слой хранения оказывался обязательным
условием существования use case. Подменить хранение (например, на память в
тесте) можно было только вместе с правкой use case, а «слои по §50» держались
на том, что никто не заметил исключения.

Здесь лежат и протоколы, и формы данных, которыми порт обменивается: запрос
списка и его страница. Держать их вместе приходится из-за направления импортов —
если бы формы переехали в `queries/`, модули ссылались бы друг на друга.

Протоколы структурные (`Protocol`), а не базовые классы: реализация в
`infrastructure/` не обязана знать о порте и наследоваться от него — достаточно
совпадения методов. Это позволяет проверять use cases подставными реализациями
без общего предка, а сам порт остаётся описанием, а не иерархией.

Кодирование курсора пагинации здесь **не** объявлено: курсор — непрозрачная
строка для клиента (§32), и его формат принадлежит слою, который знает порядок
сортировки. Use case получает и отдаёт её как есть.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Callable, Protocol

from chronoscope.domain.errors import InvalidInputError
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.raw_event import RawEvent

DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class EventQuery:
    """Фильтры GET /api/v1/events (§31)."""

    limit: int = DEFAULT_PAGE_LIMIT
    cursor: str | None = None
    types: tuple[str, ...] = ()
    source: str | None = None
    actor_id: str | None = None
    subject_id: str | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise InvalidInputError("limit: должен быть положительным")
        if self.limit > MAX_PAGE_LIMIT:
            raise InvalidInputError(f"limit: не может превышать {MAX_PAGE_LIMIT}")
        if self.time_from is not None and self.time_to is not None and self.time_from > self.time_to:
            raise InvalidInputError("from: не может быть позже to")


@dataclass(frozen=True, slots=True)
class EventPage:
    events: tuple[Event, ...] = ()
    next_cursor: str | None = None


class RawEventRepositoryPort(Protocol):
    """Хранилище сырых событий (§29, §34)."""

    def insert(self, raw_event: RawEvent, *, ingested_at: datetime) -> bool:
        """Сохранить сырое событие; ``False`` — такое уже было (повторная доставка)."""
        ...

    def get(self, raw_event_id: str) -> RawEvent | None: ...

    def count(self) -> int: ...


class EventRepositoryPort(Protocol):
    """Хранилище нормализованных событий (§13, §26)."""

    def insert(self, event: Event, *, ingested_at: datetime) -> bool:
        """Сохранить событие; ``False`` — событие с таким идентификатором уже есть."""
        ...

    def get(self, event_id: str) -> Event | None: ...

    def list(self, query: EventQuery) -> EventPage: ...

    def find_instance_events(self, process_instance_id: str, *, limit: int) -> tuple[Event, ...]:
        """События одного экземпляра процесса, от ранних к поздним (§14).

        Экземпляр процесса — не строка в таблице процессов, а набор событий с
        общим ``subject_id``: процессов в хранилище нет, есть события о них.
        Поэтому запрос выглядит именно так, и отдельной таблицы ``processes``
        для detail не заводится.

        ``limit`` обязателен и принадлежит вызывающему: у чтения detail должен
        быть предел, а какой он — решает приложение, а не хранилище. Порядок
        выборки фиксирован, потому что detail собирается от начала жизни
        процесса к её концу.
        """
        ...

    def count(self) -> int: ...

    def count_since(self, moment: datetime) -> int: ...

    def find_process_instance(
        self,
        *,
        boot_id: str | None,
        pid: int,
        before: datetime,
    ) -> EntityRef | None:
        """Найти экземпляр процесса по PID в пределах boot session (§14, §20)."""
        ...


class UnitOfWorkPort(Protocol):
    """Единица работы: границы транзакции, которыми владеет приложение (§34).

    Репозитории, полученные отсюда, транзакцией не управляют: коммит делает тот,
    кто открыл единицу работы. Поэтому пакет событий пишется одной транзакцией —
    одна на пакет вместо двух коммитов на каждое событие (ADR-0004).
    """

    raw_events: RawEventRepositoryPort
    events: EventRepositoryPort

    def __enter__(self) -> "UnitOfWorkPort": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


#: Как use case получает единицу работы: реализацию передаёт слой хранения,
#: приложение о ней не знает.
UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
