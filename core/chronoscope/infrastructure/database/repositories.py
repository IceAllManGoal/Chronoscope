"""Репозитории raw_events и events (§26, §32, §49).

Единственное место, где Core обращается к SQL. Слой API не пишет запросы
самостоятельно (§50), а use cases работают через эти репозитории.

Идемпотентность (§34). Обе вставки используют ``ON CONFLICT DO NOTHING`` и
сообщают вызывающему коду, была ли строка добавлена. Это и есть механизм
at-least-once доставки без дублей: Agent вправе повторить отправку, Core
распознаёт повтор по стабильному ``raw_event_id`` и не создаёт вторую запись.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, and_, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from chronoscope.domain.errors import InvalidInputError, StorageError
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.event_type import PROCESS_STARTED
from chronoscope.domain.events.raw_event import RawEvent
from chronoscope.domain.timestamps import format_utc_fixed, parse_utc
from chronoscope.infrastructure.database.models import (
    ATTRIBUTE_PID_PATH,
    events,
    raw_events,
)

DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 1000

_CURSOR_VERSION = 1


# ── Курсор пагинации (§32) ───────────────────────────────────────────


def encode_cursor(timestamp: datetime, event_id: str) -> str:
    """Упаковать позицию ``(timestamp, event_id)`` в непрозрачный курсор.

    §32 требует курсор, включающий timestamp и event_id. Кодирование в base64
    делает его непрозрачным для клиента: тогда порядок сортировки можно
    изменить, не ломая уже выданные курсоры, и клиент не начнёт полагаться на
    внутренний формат.
    """
    payload = json.dumps(
        {"v": _CURSOR_VERSION, "t": format_utc_fixed(timestamp), "i": event_id},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Разобрать курсор. Некорректный курсор — ошибка входа, а не тихий сброс."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidInputError("cursor: значение не является корректным курсором") from exc

    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise InvalidInputError("cursor: неподдерживаемая версия курсора")

    timestamp_raw = payload.get("t")
    event_id = payload.get("i")
    if not isinstance(timestamp_raw, str) or not isinstance(event_id, str) or not event_id:
        raise InvalidInputError("cursor: значение не является корректным курсором")

    return parse_utc(timestamp_raw, "cursor.t"), event_id


# ── Запросы и страницы ───────────────────────────────────────────────


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


# ── Преобразование строк БД в доменные объекты ───────────────────────


def _entity_ref(entity_type: str | None, entity_id: str | None, name: str | None) -> EntityRef | None:
    if entity_id is None:
        return None
    return EntityRef(type=entity_type or "unknown", id=entity_id, name=name)


def _row_to_event(row: Any) -> Event:
    tags_raw = row.tags_json or "[]"
    tags = tuple(json.loads(tags_raw)) if tags_raw else ()

    return Event(
        schema_version=row.schema_version,
        id=row.id,
        timestamp=parse_utc(row.timestamp),
        type=row.type,
        source=row.source,
        host_id=row.host_id,
        attributes=json.loads(row.attributes_json),
        observed_at=parse_utc(row.observed_at) if row.observed_at else None,
        boot_id=row.boot_id,
        actor=_entity_ref(row.actor_type, row.actor_id, row.actor_name),
        subject=_entity_ref(row.subject_type, row.subject_id, row.subject_name),
        raw_event_id=row.raw_event_id,
        trace_id=row.trace_id,
        tags=tags,
    )


def _row_to_raw_event(row: Any) -> RawEvent:
    return RawEvent(
        schema_version=row.schema_version,
        raw_event_id=row.id,
        collector=row.collector,
        collector_version=row.collector_version,
        observed_at=parse_utc(row.observed_at),
        source_timestamp=parse_utc(row.source_timestamp) if row.source_timestamp else None,
        host_id=row.host_id,
        boot_id=row.boot_id,
        payload_type=row.payload_type,
        payload=json.loads(row.payload_json),
    )


class RawEventRepository:
    """Хранилище сырых событий (§29)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert(self, raw_event: RawEvent, *, ingested_at: datetime) -> bool:
        """Сохранить сырое событие.

        Возвращает ``True``, если событие добавлено, и ``False``, если событие
        с таким ``raw_event_id`` уже существует — то есть при повторной
        доставке (§34).
        """
        statement = (
            sqlite_insert(raw_events)
            .values(
                id=raw_event.raw_event_id,
                schema_version=raw_event.schema_version,
                collector=raw_event.collector,
                collector_version=raw_event.collector_version,
                observed_at=format_utc_fixed(raw_event.observed_at),
                source_timestamp=(
                    format_utc_fixed(raw_event.source_timestamp) if raw_event.source_timestamp else None
                ),
                host_id=raw_event.host_id,
                boot_id=raw_event.boot_id,
                payload_type=raw_event.payload_type,
                payload_json=json.dumps(dict(raw_event.payload), ensure_ascii=False, separators=(",", ":")),
                ingested_at=format_utc_fixed(ingested_at),
            )
            .on_conflict_do_nothing(index_elements=[raw_events.c.id])
        )

        try:
            with self._engine.begin() as connection:
                result = connection.execute(statement)
                return result.rowcount == 1
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось сохранить raw event: {exc}") from exc

    def get(self, raw_event_id: str) -> RawEvent | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(raw_events).where(raw_events.c.id == raw_event_id)
                ).one_or_none()
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось прочитать raw event: {exc}") from exc

        return _row_to_raw_event(row) if row is not None else None

    def count(self) -> int:
        try:
            with self._engine.connect() as connection:
                return int(connection.execute(select(func.count()).select_from(raw_events)).scalar_one())
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось посчитать raw events: {exc}") from exc


class EventRepository:
    """Хранилище нормализованных событий (§13, §26)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert(self, event: Event, *, ingested_at: datetime) -> bool:
        """Сохранить нормализованное событие.

        ``ON CONFLICT DO NOTHING`` здесь — страховка, а не основной механизм:
        идентификаторы событий выдаёт Core, поэтому дубликат означал бы ошибку
        в вызывающем коде. Молча затёрть чужую запись всё равно нельзя:
        событие исторически неизменно (§21).
        """
        actor = event.actor
        subject = event.subject

        statement = (
            sqlite_insert(events)
            .values(
                id=event.id,
                schema_version=event.schema_version,
                timestamp=format_utc_fixed(event.timestamp),
                observed_at=format_utc_fixed(event.observed_at) if event.observed_at else None,
                ingested_at=format_utc_fixed(ingested_at),
                type=event.type,
                source=event.source,
                host_id=event.host_id,
                boot_id=event.boot_id,
                actor_type=actor.type if actor else None,
                actor_id=actor.id if actor else None,
                actor_name=actor.name if actor else None,
                subject_type=subject.type if subject else None,
                subject_id=subject.id if subject else None,
                subject_name=subject.name if subject else None,
                attributes_json=json.dumps(dict(event.attributes), ensure_ascii=False, separators=(",", ":")),
                raw_event_id=event.raw_event_id,
                trace_id=event.trace_id,
                tags_json=json.dumps(list(event.tags), ensure_ascii=False),
            )
            .on_conflict_do_nothing(index_elements=[events.c.id])
        )

        try:
            with self._engine.begin() as connection:
                result = connection.execute(statement)
                return result.rowcount == 1
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось сохранить event: {exc}") from exc

    def get(self, event_id: str) -> Event | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(select(events).where(events.c.id == event_id)).one_or_none()
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось прочитать event: {exc}") from exc

        return _row_to_event(row) if row is not None else None

    def list(self, query: EventQuery) -> EventPage:
        """Постраничный список событий (§31, §32).

        Порядок — ``timestamp DESC, id DESC``. Курсор содержит обе компоненты,
        поэтому события с одинаковым временем не теряются на границе страницы и
        не дублируются: глубокий ``OFFSET`` для этого непригоден (§32).
        """
        conditions = self._build_conditions(query)

        if query.cursor is not None:
            cursor_timestamp, cursor_id = decode_cursor(query.cursor)
            cursor_timestamp_raw = format_utc_fixed(cursor_timestamp)
            conditions.append(
                or_(
                    events.c.timestamp < cursor_timestamp_raw,
                    and_(events.c.timestamp == cursor_timestamp_raw, events.c.id < cursor_id),
                )
            )

        statement = (
            select(events)
            .where(and_(*conditions) if conditions else True)  # type: ignore[arg-type]
            .order_by(events.c.timestamp.desc(), events.c.id.desc())
            .limit(query.limit + 1)
        )

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(statement).all()
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось прочитать список events: {exc}") from exc

        has_more = len(rows) > query.limit
        page_rows = rows[: query.limit]
        page_events = tuple(_row_to_event(row) for row in page_rows)

        next_cursor = None
        if has_more and page_events:
            last = page_events[-1]
            next_cursor = encode_cursor(last.timestamp, last.id)

        return EventPage(events=page_events, next_cursor=next_cursor)

    @staticmethod
    def _build_conditions(query: EventQuery) -> list[Any]:
        conditions: list[Any] = []
        if query.types:
            conditions.append(events.c.type.in_(query.types))
        if query.source is not None:
            conditions.append(events.c.source == query.source)
        if query.actor_id is not None:
            conditions.append(events.c.actor_id == query.actor_id)
        if query.subject_id is not None:
            conditions.append(events.c.subject_id == query.subject_id)
        if query.time_from is not None:
            conditions.append(events.c.timestamp >= format_utc_fixed(query.time_from))
        if query.time_to is not None:
            conditions.append(events.c.timestamp <= format_utc_fixed(query.time_to))
        return conditions

    def count(self) -> int:
        try:
            with self._engine.connect() as connection:
                return int(connection.execute(select(func.count()).select_from(events)).scalar_one())
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось посчитать events: {exc}") from exc

    def count_since(self, moment: datetime) -> int:
        try:
            with self._engine.connect() as connection:
                return int(
                    connection.execute(
                        select(func.count())
                        .select_from(events)
                        .where(events.c.timestamp >= format_utc_fixed(moment))
                    ).scalar_one()
                )
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось посчитать недавние events: {exc}") from exc

    def find_process_instance(
        self,
        *,
        boot_id: str | None,
        pid: int,
        before: datetime,
    ) -> str | None:
        """Найти экземпляр процесса по PID в пределах boot session (§14, §20).

        Нужен нормализатору, чтобы заполнить ``actor`` события
        ``process.started``: родитель известен по PID, но его идентичность —
        это ``process_instance_id``, который выводится из времени старта
        родителя. Времени старта у нас нет, поэтому родителя приходится искать
        среди уже сохранённых событий.

        Если ``boot_id`` неизвестен, поиск **не выполняется**. PID без boot
        session может совпасть с процессом предыдущей загрузки, и тогда
        подставленный ``actor`` был бы не связью, а вымыслом.
        """
        if boot_id is None:
            return None

        pid_value = func.json_extract(events.c.attributes_json, ATTRIBUTE_PID_PATH)

        statement = (
            select(events.c.subject_id)
            .where(
                and_(
                    events.c.type == PROCESS_STARTED,
                    events.c.boot_id == boot_id,
                    events.c.subject_id.is_not(None),
                    events.c.timestamp <= format_utc_fixed(before),
                    pid_value == pid,
                )
            )
            .order_by(events.c.timestamp.desc(), events.c.id.desc())
            .limit(1)
        )

        try:
            with self._engine.connect() as connection:
                return connection.execute(statement).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise StorageError(f"не удалось найти экземпляр процесса: {exc}") from exc
