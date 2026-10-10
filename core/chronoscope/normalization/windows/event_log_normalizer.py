"""Нормализатор записей журнала Windows для ``windows.eventlog`` (§8, §77.2).

Превращает payload ``event_log_record`` в нормализованное событие ``system.event``.

Что здесь осознанно **не** делается — и почему это важнее того, что делается.

**Тип события не выводится из пары «provider + event_id».** Соблазн велик:
запись ``Microsoft-Windows-Kernel-General`` с идентификатором 12 — это загрузка
системы, и её хочется назвать ``system.booted``. §77.2 (пункт 7 и примечание к
нему) прямо запрещает это в 0.0.4, и запрет содержательный: таблица
соответствий, не покрытая собственными тестами и не сверенная с наблюдением,
ошибалась бы молча — а ошибка в ней выглядела бы как знание Chronoscope о
системе. В 0.0.4 у второго источника один тип: ``system.event``.

**Актора и субъекта нет.** Запись журнала не описывает действие над сущностью
Chronoscope: у неё нет ни процесса-инициатора, ни объекта, у которого есть
идентичность. Подставить сюда канал журнала значило бы выдумать тип сущности,
которого §19 не знает, — и он попал бы в модель, где идентичность выводится
детерминированно.

**Сообщение не переносится в событие.** Форматированное сообщение записи
попадает в payload только при включённом ``capture_message`` и остаётся в сыром
событии: сырой слой хранит свидетельство источника, timeline — знание
Chronoscope. Переносить туда текст, который может содержать пути, имена
пользователей и аргументы, — отдельное решение о расширении показа, а не
следствие флага сбора (§37, §77.2 пункт 10).
"""

from __future__ import annotations

from typing import Any

from chronoscope.domain.errors import NormalizationError
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.event_type import (
    PAYLOAD_FIELD_CHANNEL,
    PAYLOAD_FIELD_EVENT_ID,
    PAYLOAD_FIELD_LEVEL,
    PAYLOAD_FIELD_OPCODE,
    PAYLOAD_FIELD_PROVIDER,
    PAYLOAD_FIELD_RECORD_ID,
    PAYLOAD_FIELD_TASK,
    SYSTEM_EVENT,
)
from chronoscope.domain.events.raw_event import RawEvent
from chronoscope.domain.ids import new_event_id
from chronoscope.normalization.registry import NormalizationContext

#: Числовые поля payload, которые переносятся в атрибуты как есть.
_INT_FIELDS = (
    PAYLOAD_FIELD_EVENT_ID,
    PAYLOAD_FIELD_RECORD_ID,
    PAYLOAD_FIELD_LEVEL,
    PAYLOAD_FIELD_TASK,
    PAYLOAD_FIELD_OPCODE,
)


def normalize_event_log_record(raw_event: RawEvent, _context: NormalizationContext) -> Event:
    """``event_log_record`` → ``system.event``.

    Внешний мир нормализатору не нужен: запись журнала самодостаточна, и порт
    поиска экземпляра процесса (единственное, что есть в контексте) здесь не
    используется. Параметр остаётся потому, что этого требует форма обработчика
    реестра: у всех нормализаторов одна сигнатура, и вызывающий код не должен
    знать, каким из них что нужно.
    """
    return Event(
        schema_version=raw_event.schema_version,
        id=new_event_id(),

        # Время события — время записи в журнале (§24), а не момент наблюдения:
        # source_timestamp и есть время появления записи, observed_at — когда её
        # увидел Agent. Best_timestamp выбирает именно эту пару в правильном
        # порядке и откатывается на observed_at только если источник времени не
        # сообщил.
        timestamp=raw_event.best_timestamp,

        type=SYSTEM_EVENT,
        source=raw_event.collector,
        host_id=raw_event.host_id,
        attributes=_build_attributes(raw_event),
        observed_at=raw_event.observed_at,
        boot_id=raw_event.boot_id,
        actor=None,
        subject=None,
        raw_event_id=raw_event.raw_event_id,
    )


def _require_channel(raw_event: RawEvent) -> str:
    """Канал записи обязателен.

    Без канала запись неотличима от записи другого журнала с тем же поставщиком
    и тем же идентификатором события, а такие пары встречаются: одно и то же
    событие попадает и в ``System``, и в ``Application``. Отказ здесь честнее
    события, которое выглядит полным, но не говорит, откуда оно.
    """
    channel = raw_event.payload_str(PAYLOAD_FIELD_CHANNEL)
    if channel is None:
        raise NormalizationError(
            "payload.channel отсутствует: без канала запись журнала неотличима от записи другого журнала"
        )
    return channel


def _build_attributes(raw_event: RawEvent) -> dict[str, Any]:
    """Собрать атрибуты по явному списку полей (§77.2, пункт 7).

    Список разрешающий и совпадает с тем, что собирает Agent. Это не
    дублирование, а страховка: если контракт события окажется шире контракта
    сбора, в timeline появится поле, которого в payload нет, и отличить
    «источник не сообщил» от «Chronoscope добавил» будет нельзя.

    Отсутствующее значение остаётся отсутствующим. Ноль в ``event_id`` выглядел
    бы как настоящее событие с идентификатором 0, а ноль в ``level`` — как
    событие уровня 0, которого в терминах Windows не существует.
    """
    attributes: dict[str, Any] = {PAYLOAD_FIELD_CHANNEL: _require_channel(raw_event)}

    provider = raw_event.payload_str(PAYLOAD_FIELD_PROVIDER)
    if provider is not None:
        attributes[PAYLOAD_FIELD_PROVIDER] = provider

    for field in _INT_FIELDS:
        value = raw_event.payload_int(field)
        if value is not None:
            attributes[field] = value

    return attributes
