"""Таблицы Chronoscope в SQLite (§26, §27).

Схема соответствует §26 спеки с тремя осознанными дополнениями. §26 прямо
оговаривает, что это не окончательная схема и в раннем проекте важнее
гибкость, поэтому дополнения перечислены явно, а не сделаны молча.

Дополнения к §26:

1. ``events.ingested_at`` — §24 называет это поле третьим (после
   ``source_timestamp`` и ``observed_at``) и требующим отдельного хранения.
   Без него невозможно отличить задержку источника от задержки нормализации.
2. ``events.trace_id`` и ``events.tags_json`` — оба поля входят в контракт
   нормализованного события (§13), но отсутствуют в наброске таблицы §26.
   Без них объект контракта не сохраняется и не читается без потерь, а
   «round-trip» событие → БД → событие перестаёт быть тождественным.
3. ``tags_json`` хранится со значением по умолчанию ``'[]'``: в 0.0.1 теги не
   выставляются, но поле контракта существует.

Индексы взяты из §27 ровно в указанном объёме. Десятки индексов «на будущее»
не создаются: каждый занимает место и замедляет insert.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
)

metadata = MetaData()

raw_events = Table(
    "raw_events",
    metadata,
    Column("id", Text, primary_key=True),
    Column("schema_version", Integer, nullable=False),
    Column("collector", Text, nullable=False),
    Column("collector_version", Text, nullable=False),
    Column("observed_at", Text, nullable=False),
    Column("source_timestamp", Text, nullable=True),
    Column("host_id", Text, nullable=False),
    Column("boot_id", Text, nullable=True),
    Column("payload_type", Text, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("ingested_at", Text, nullable=False),
)

events = Table(
    "events",
    metadata,
    Column("id", Text, primary_key=True),
    Column("schema_version", Integer, nullable=False),
    Column("timestamp", Text, nullable=False),
    Column("observed_at", Text, nullable=True),
    Column("ingested_at", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("host_id", Text, nullable=False),
    Column("boot_id", Text, nullable=True),
    Column("actor_type", Text, nullable=True),
    Column("actor_id", Text, nullable=True),
    Column("actor_name", Text, nullable=True),
    Column("subject_type", Text, nullable=True),
    Column("subject_id", Text, nullable=True),
    Column("subject_name", Text, nullable=True),
    Column("attributes_json", Text, nullable=False),
    Column("raw_event_id", Text, ForeignKey("raw_events.id"), nullable=True),
    Column("trace_id", Text, nullable=True),
    Column("tags_json", Text, nullable=False, server_default="[]"),
)

# §27. Индекс (type, timestamp) обслуживает и фильтр по типу, и поиск
# родительского экземпляра процесса при нормализации.
Index("ix_events_timestamp", events.c.timestamp)
Index("ix_events_type_timestamp", events.c.type, events.c.timestamp)
Index("ix_events_actor_id_timestamp", events.c.actor_id, events.c.timestamp)
Index("ix_events_subject_id_timestamp", events.c.subject_id, events.c.timestamp)
Index("ix_raw_events_collector_source_timestamp", raw_events.c.collector, raw_events.c.source_timestamp)

#: Атрибут нормализованного события, по которому ищется родительский процесс.
#: Вынесено сюда, потому что этим путём пользуется и репозиторий, и тесты.
ATTRIBUTE_PID_PATH = "$.pid"
