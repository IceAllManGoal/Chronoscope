"""ProcessDetail — экземпляр процесса как сущность во времени (§14, §20, §77.1).

Detail собирается из уже сохранённых событий, а не из отдельной таблицы
процессов: процессов в хранилище нет, есть события о них. Поэтому detail всегда
пересобирается из immutable Event, и расходиться с историей ему негде.

Главное свойство модели — **терпимость к неполной истории**. Коллектор на WMI
опрашивает систему раз в секунду (§8.4) и может пропустить любое из событий,
поэтому реальны все три случая:

```text
process.started ✅  process.exited ✅   наблюдалась вся жизнь
process.started ✅  process.exited ❌   процесс жив или выход пропущен
process.started ❌  process.exited ✅   запуск произошёл до начала наблюдения
```

Отсюда две пары полей, которые нельзя смешивать:

- ``started_at`` и ``observed_start`` — **разные вещи**. Время старта процесса
  может быть известно и без события запуска: событие завершения несёт
  ``process_started_at``, потому что Windows знает, когда процесс стартовал.
  Знать время и наблюдать событие — не одно и то же.
- ``exited_at`` и ``observed_exit`` — то же самое с другой стороны.
- ``duration`` выводится из двух времён, а не из двух наблюдений: длительность
  жизни процесса не зависит от того, видели ли мы его запуск.
- Незавершённый процесс **не** описывается как работающий: ``exited_at``
  остаётся пустым, ``observed_exit`` — ``false``, а поля «состояние» в модели
  нет вовсе. «Сейчас работает» — утверждение, которого Chronoscope делать не
  может, пока выход может быть пропущен (§77.1, пункт 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from chronoscope.domain.events.event import Event
from chronoscope.domain.events.event_type import (
    PAYLOAD_FIELD_PARENT_PID,
    PAYLOAD_FIELD_PID,
    PAYLOAD_FIELD_PROCESS_STARTED_AT,
    PROCESS_EXITED,
    PROCESS_STARTED,
)
from chronoscope.domain.timestamps import parse_utc

#: Имена атрибутов, которые читает detail, совпадают с именами полей payload:
#: нормализатор переносит ``pid``, ``parent_pid`` и ``process_started_at`` в
#: атрибуты под теми же именами. Поэтому константы берутся из контрактного
#: модуля, а не пишутся здесь литералами: строка, написанная дважды, разойдётся.


@dataclass(frozen=True, slots=True)
class ProcessParent:
    """Родитель экземпляра процесса (§20).

    ``resolved`` — не отдельный признак, а факт: идентичность родителя известна
    ровно тогда, когда нормализатор её установил и записал в ``actor`` события.
    То же решение зафиксировано в атрибуте ``parent_resolved``, но второго
    источника правды здесь нет намеренно: два признака одного решения рано или
    поздно разошлись бы.

    ``pid`` известен и без идентичности: он приходит от источника и остаётся
    полезным даже тогда, когда событие старта родителя не наблюдалось.
    """

    id: str | None = None
    name: str | None = None
    pid: int | None = None
    resolved: bool = False


@dataclass(frozen=True, slots=True)
class ProcessDetail:
    """Один экземпляр процесса: идентичность, жизнь и связь с родителем.

    ``parent`` присутствует всегда, даже когда родителя не наблюдали: его
    отсутствие — это пустые поля и ``resolved: false``, а не отсутствие объекта.
    Иначе клиенту пришлось бы различать два вида пустоты там, где разницы нет.
    """

    id: str
    name: str | None
    pid: int | None
    boot_id: str | None
    started_at: datetime | None
    exited_at: datetime | None
    duration: timedelta | None
    parent: ProcessParent
    observed_start: bool
    observed_exit: bool


def build_process_detail(process_instance_id: str, events: Sequence[Event]) -> ProcessDetail:
    """Собрать detail экземпляра из его событий.

    Порядок входа не важен: события сортируются здесь, а не берутся в том
    порядке, в котором их вернуло хранилище. Порядок выборки — деталь запроса, и
    detail не должен от неё зависеть.
    """
    ordered = sorted(events, key=lambda event: (event.timestamp, event.id))

    started_at = _started_at(ordered)
    exited_at = _exited_at(ordered)

    return ProcessDetail(
        id=process_instance_id,
        name=_first_name(ordered),
        pid=_first_attribute_int(ordered, PAYLOAD_FIELD_PID),
        boot_id=_first_boot_id(ordered),
        started_at=started_at,
        exited_at=exited_at,
        duration=_duration(started_at, exited_at),
        parent=_parent(ordered),
        observed_start=any(event.type == PROCESS_STARTED for event in ordered),
        observed_exit=any(event.type == PROCESS_EXITED for event in ordered),
    )


def _attribute_int(event: Event, name: str) -> int | None:
    """Целое из атрибута события.

    ``bool`` отсекается отдельно: он подкласс ``int``, и ``True`` на месте PID
    был бы молчаливым вымыслом вместо отсутствия значения.
    """
    value = event.attributes.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _first_attribute_int(events: Sequence[Event], name: str) -> int | None:
    for event in events:
        value = _attribute_int(event, name)
        if value is not None:
            return value
    return None


def _first_name(events: Sequence[Event]) -> str | None:
    for event in events:
        name = event.subject.name if event.subject else None
        if name:
            return name
    return None


def _first_boot_id(events: Sequence[Event]) -> str | None:
    for event in events:
        if event.boot_id is not None:
            return event.boot_id
    return None


def _started_at(events: Sequence[Event]) -> datetime | None:
    """Время старта экземпляра.

    Источников два, и второй важнее, чем кажется: событие завершения несёт
    ``process_started_at``, поэтому время старта известно даже тогда, когда
    событие запуска пропущено.

    Берётся самое раннее из известных значений: если времена разошлись, начало
    жизни процесса описывает более раннее, а не то, когда его заметили.
    """
    moments: list[datetime] = []

    for event in events:
        if event.type == PROCESS_STARTED:
            # У события запуска timestamp и есть время старта процесса:
            # нормализатор выводит его именно из ``process_started_at``.
            moments.append(event.timestamp)

        raw = event.attributes.get(PAYLOAD_FIELD_PROCESS_STARTED_AT)
        if isinstance(raw, str):
            moments.append(parse_utc(raw, PAYLOAD_FIELD_PROCESS_STARTED_AT))

    return min(moments) if moments else None


def _exited_at(events: Sequence[Event]) -> datetime | None:
    """Время завершения по наблюдавшимся событиям выхода.

    Если источник сообщил о завершении больше одного раза, жизнь процесса
    заканчивает более позднее наблюдение.
    """
    moments = [event.timestamp for event in events if event.type == PROCESS_EXITED]
    return max(moments) if moments else None


def _duration(started_at: datetime | None, exited_at: datetime | None) -> timedelta | None:
    """Длительность жизни процесса по двум временам.

    Отрицательная разница означает, что источник сообщил противоречащие друг
    другу времена. Тогда длительность не выводится вовсе: отрицательная
    длительность жизни — не «немного неточная» величина, а утверждение, которого
    в данных не было.
    """
    if started_at is None or exited_at is None:
        return None

    span = exited_at - started_at
    return span if span >= timedelta(0) else None


def _parent(events: Sequence[Event]) -> ProcessParent:
    """Родитель экземпляра по его же событиям.

    Идентичность берётся только у того события, чей ``parent_pid`` совпал с уже
    известным: если источник назвал разные родительские PID, выдать связь за
    установленную нельзя, а сообщить чужой идентификатор — тем более.

    Связь ищется по всем событиям экземпляра, а не только по событию запуска:
    родителя могло не быть в хранилище в момент нормализации запуска, но он
    появился там к моменту завершения. Выбирать при этом «более удачное»
    наблюдение — не догадка: оба наблюдения записаны в самих событиях.
    """
    parent_pid: int | None = None
    actor = None

    for event in events:
        pid = _attribute_int(event, PAYLOAD_FIELD_PARENT_PID)
        if pid is None:
            continue

        if parent_pid is None:
            parent_pid = pid

        if actor is None and pid == parent_pid and event.actor is not None:
            actor = event.actor

    return ProcessParent(
        id=actor.id if actor else None,
        name=actor.name if actor else None,
        pid=parent_pid,
        resolved=actor is not None,
    )
