"""Нормализатор событий процесса для ``windows.process`` (§13, §14, §20).

Превращает payload ``process_start`` и ``process_exit`` в нормализованные
события ``process.started`` и ``process.exited``.

Две вещи, которые здесь происходят и требуют пояснения.

**Переименование полей.** В payload источник отдаёт ``path``, а контракт
нормализованного события (§13) называет это поле ``executable``. Нормализация
существует ровно для таких расхождений: raw-слой хранит терминологию
источника, Event — терминологию Chronoscope.

**Заполнение ``actor``.** §20 требует, чтобы у ``process.started`` актором был
родительский процесс. Родитель известен только как ``parent_pid``, а это
атрибут, а не идентичность (§14): чтобы получить ``process_instance_id``
родителя, нужно знать время его старта, которого в событии нет. Поэтому
родитель ищется среди уже сохранённых событий через порт
:class:`~chronoscope.normalization.registry.ProcessInstanceLookup`.

Если родителя найти не удалось, ``actor`` остаётся ``None``, а в атрибутах
появляется ``parent_resolved: false``. Это честнее подстановки догадки: в
сценарии 0.0.1 ``explorer.exe`` запускается при входе в систему, то есть
обычно **до** того, как Agent начал наблюдение, и его событие старта
отсутствует.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from chronoscope.domain.errors import NormalizationError
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.event_type import (
    ENTITY_PROCESS,
    PAYLOAD_FIELD_COMMAND_LINE,
    PAYLOAD_FIELD_EXIT_CODE,
    PAYLOAD_FIELD_EXITED_AT,
    PAYLOAD_FIELD_NAME,
    PAYLOAD_FIELD_PARENT_PID,
    PAYLOAD_FIELD_PATH,
    PAYLOAD_FIELD_PID,
    PAYLOAD_FIELD_PROCESS_STARTED_AT,
    PAYLOAD_FIELD_USER_SID,
    PROCESS_EXITED,
    PROCESS_STARTED,
)
from chronoscope.domain.events.raw_event import RawEvent
from chronoscope.domain.ids import new_event_id, process_instance_id
from chronoscope.domain.timestamps import format_utc
from chronoscope.normalization.registry import NormalizationContext

#: Атрибут, показывающий, удалось ли связать событие с родительским процессом.
ATTRIBUTE_PARENT_RESOLVED = "parent_resolved"


def normalize_process_start(raw_event: RawEvent, context: NormalizationContext) -> Event:
    """``process_start`` → ``process.started``."""
    return _normalize(raw_event, context, event_type=PROCESS_STARTED, is_exit=False)


def normalize_process_exit(raw_event: RawEvent, context: NormalizationContext) -> Event:
    """``process_exit`` → ``process.exited``."""
    return _normalize(raw_event, context, event_type=PROCESS_EXITED, is_exit=True)


def _require_pid(raw_event: RawEvent) -> int:
    pid = raw_event.payload_int(PAYLOAD_FIELD_PID)
    if pid is None:
        raise NormalizationError("payload.pid отсутствует: без PID событие процесса бессмысленно")
    return pid


def _process_started_at(raw_event: RawEvent) -> datetime:
    """Время старта экземпляра процесса.

    Нужно для вывода ``process_instance_id`` (§14). Порядок источников:
    явное поле payload, затем ``source_timestamp``, затем ``observed_at``.
    Последний вариант — наихудший: он даёт время наблюдения, а не старта, и
    поэтому для события ``process.started`` он приемлем, а для события выхода
    означал бы, что идентификатор выведен не из того времени.
    """
    explicit = raw_event.payload_timestamp(PAYLOAD_FIELD_PROCESS_STARTED_AT)
    if explicit is not None:
        return explicit
    return raw_event.best_timestamp


def _build_attributes(raw_event: RawEvent, *, is_exit: bool) -> dict[str, Any]:
    attributes: dict[str, Any] = {"pid": _require_pid(raw_event)}

    parent_pid = raw_event.payload_int(PAYLOAD_FIELD_PARENT_PID)
    if parent_pid is not None:
        attributes["parent_pid"] = parent_pid

    executable = raw_event.payload_str(PAYLOAD_FIELD_PATH)
    if executable is not None:
        attributes["executable"] = executable

    command_line = raw_event.payload_str(PAYLOAD_FIELD_COMMAND_LINE)
    if command_line is not None:
        attributes["command_line"] = command_line

    user_sid = raw_event.payload_str(PAYLOAD_FIELD_USER_SID)
    if user_sid is not None:
        attributes["user_sid"] = user_sid

    if is_exit:
        exit_code = raw_event.payload_int(PAYLOAD_FIELD_EXIT_CODE)
        if exit_code is not None:
            attributes["exit_code"] = exit_code

    started_at = raw_event.payload_timestamp(PAYLOAD_FIELD_PROCESS_STARTED_AT)
    if started_at is not None:
        attributes["process_started_at"] = format_utc(started_at)

    return attributes


def _resolve_parent(
    raw_event: RawEvent,
    context: NormalizationContext,
    attributes: dict[str, Any],
) -> EntityRef | None:
    parent_pid = attributes.get("parent_pid")
    if parent_pid is None:
        return None

    parent = context.find_process_instance(
        boot_id=raw_event.boot_id,
        pid=parent_pid,
        before=raw_event.best_timestamp,
    )

    # Флаг нужен пользователю: он различает «родителя не было» и «родитель был,
    # но его событие старта не наблюдалось». Без него пустой actor выглядел бы
    # как отсутствие данных.
    attributes[ATTRIBUTE_PARENT_RESOLVED] = parent is not None

    if parent is None:
        return None

    # Идентификатор и имя родителя берутся из его собственного события старта.
    # Имя не выдумывается: в событиях запуска потомка приходит только
    # parent_pid, а имя известно лишь потому, что старт родителя наблюдался.
    return EntityRef(ENTITY_PROCESS, parent.id, parent.name)


def _event_timestamp(raw_event: RawEvent, *, is_exit: bool, process_started_at: datetime) -> datetime:
    if not is_exit:
        return process_started_at

    exited_at = raw_event.payload_timestamp(PAYLOAD_FIELD_EXITED_AT)
    return exited_at or raw_event.best_timestamp


def _normalize(
    raw_event: RawEvent,
    context: NormalizationContext,
    *,
    event_type: str,
    is_exit: bool,
) -> Event:
    pid = _require_pid(raw_event)
    process_started_at = _process_started_at(raw_event)
    name = raw_event.payload_str(PAYLOAD_FIELD_NAME)

    subject = EntityRef(
        ENTITY_PROCESS,
        process_instance_id(
            host_id=raw_event.host_id,
            boot_id=raw_event.boot_id,
            pid=pid,
            started_at=process_started_at,
        ),
        name,
    )

    attributes = _build_attributes(raw_event, is_exit=is_exit)
    actor = _resolve_parent(raw_event, context, attributes)

    return Event(
        schema_version=raw_event.schema_version,
        id=new_event_id(),
        timestamp=_event_timestamp(raw_event, is_exit=is_exit, process_started_at=process_started_at),
        type=event_type,
        source=raw_event.collector,
        host_id=raw_event.host_id,
        attributes=attributes,
        observed_at=raw_event.observed_at,
        boot_id=raw_event.boot_id,
        actor=actor,
        subject=subject,
        raw_event_id=raw_event.raw_event_id,
    )
