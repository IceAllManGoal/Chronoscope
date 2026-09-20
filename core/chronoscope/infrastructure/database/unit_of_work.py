"""Единица работы: одна транзакция на пакет событий (§34, ADR-0004).

ADR-0004 называет смягчением ограничения «один писатель в SQLite» батчевые
вставки и короткие транзакции. Приём при этом был устроен наоборот: каждое
событие писало сырую запись и нормализованное событие **двумя отдельными
коммитами**, то есть пакет из 50 событий давал 100 коммитов. Коммит в SQLite —
это не строчка в журнале, а сброс на диск, поэтому цена приёма определялась
числом событий, а не объёмом данных.

Здесь транзакция одна на пакет. Побочный эффект важнее ускорения: раньше
возможен был момент, когда сырое событие уже закоммичено, а нормализованного ещё
нет. Падение Core в этот момент оставляло запись, которую повторная доставка
Agent больше не нормализует — она видит ``raw_event_id`` и отвечает
``duplicate``. Теперь сырая запись и событие попадают в базу вместе или не
попадают вовсе, а незавершённый пакет Agent отправит снова (at-least-once, §34).

Граница транзакции — пакет, а не всё, что придёт: держать одну транзакцию на
десятки тысяч событий значило бы держать писателя занятым минутами и копить WAL.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy import Connection, Engine

from chronoscope.infrastructure.database.repositories import (
    EventRepository,
    RawEventRepository,
)


class SqliteUnitOfWork:
    """Транзакция пакета: репозитории, работающие внутри одного соединения.

    Реализует ``UnitOfWorkPort`` (§50). Репозитории, созданные от соединения,
    транзакцией не управляют — коммит или откат делает только этот класс.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None
        self.raw_events: RawEventRepository
        self.events: EventRepository

    def __enter__(self) -> "SqliteUnitOfWork":
        self._connection = self._engine.connect()
        self._transaction = self._connection.begin()
        self.raw_events = RawEventRepository(self._connection)
        self.events = EventRepository(self._connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._connection
        if connection is None:  # pragma: no cover - защита от неверного использования
            return

        try:
            if exc_type is None:
                self._transaction.commit()
            else:
                # Откат всего пакета, а не части: частично применённый пакет
                # выглядел бы как история, в которой чего-то не хватает, и
                # заметить это было бы нечем. Agent повторит отправку (§34).
                self._transaction.rollback()
        finally:
            connection.close()
            self._connection = None
