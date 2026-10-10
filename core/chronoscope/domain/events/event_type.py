"""Типы событий, типы payload и имена источников.

Вынесено отдельным модулем (§49), потому что эти значения — часть контракта:
их используют и нормализаторы, и тесты, и документация. Строковые литералы,
разбросанные по коду, разошлись бы с shared/schemas и docs/EVENT_MODEL.md.
"""

from __future__ import annotations

SUPPORTED_SCHEMA_VERSION = 1
"""Версия схем транспортных объектов, поддерживаемая этой версией Core (§52)."""

# ── Имена источников (collector) ─────────────────────────────────────

COLLECTOR_WINDOWS_PROCESS = "windows.process"
COLLECTOR_WINDOWS_EVENTLOG = "windows.eventlog"

# ── Типы payload внутри windows.process ──────────────────────────────

PAYLOAD_PROCESS_START = "process_start"
PAYLOAD_PROCESS_EXIT = "process_exit"

# ── Типы payload внутри windows.eventlog ─────────────────────────────

PAYLOAD_EVENT_LOG_RECORD = "event_log_record"

# ── Типы нормализованных событий (§18) ───────────────────────────────

PROCESS_STARTED = "process.started"
PROCESS_EXITED = "process.exited"

SYSTEM_BOOTED = "system.booted"
SYSTEM_SHUTDOWN = "system.shutdown"

#: Единственный тип, который порождает второй источник в 0.0.4 (§77.2, пункт 7).
#:
#: Записи журнала различаются парой «provider + event_id», и соблазн выводить из
#: неё человеческие типы (`system.booted` для Kernel-General/12) велик. В 0.0.4
#: этого намеренно нет: таблица соответствий без собственных тестов и без
#: измерения выглядела бы как уверенное знание о системе, которого у Chronoscope
#: нет. Ошибка в ней не отличима от настоящего события.
SYSTEM_EVENT = "system.event"

INCIDENT_MARKED = "incident.marked"

KNOWN_EVENT_TYPES = frozenset(
    {
        PROCESS_STARTED,
        PROCESS_EXITED,
        SYSTEM_BOOTED,
        SYSTEM_SHUTDOWN,
        SYSTEM_EVENT,
        INCIDENT_MARKED,
    }
)
"""Типы, которые Core умеет порождать в 0.0.4.

Список не является ограничением контракта: он нужен для документации и
диагностики, а не для отказа в приёме события. ``system.booted`` и
``system.shutdown`` объявлены с 0.0.1, но по-прежнему не порождаются никем —
их первое реальное использование требует собственного среза с тестами.
"""

# ── Типы сущностей (§19) ─────────────────────────────────────────────

ENTITY_PROCESS = "process"

# ── Идентификаторы процессов в payload ───────────────────────────────

PAYLOAD_FIELD_PID = "pid"
PAYLOAD_FIELD_PARENT_PID = "parent_pid"
PAYLOAD_FIELD_NAME = "name"
PAYLOAD_FIELD_PATH = "path"
PAYLOAD_FIELD_COMMAND_LINE = "command_line"
PAYLOAD_FIELD_USER_SID = "user_sid"
PAYLOAD_FIELD_PROCESS_STARTED_AT = "process_started_at"
PAYLOAD_FIELD_EXITED_AT = "exited_at"
PAYLOAD_FIELD_EXIT_CODE = "exit_code"

# ── Поля payload записи журнала Windows (§8, §77.2) ──────────────────
#
# Имена совпадают с терминологией источника и с именами атрибутов события:
# переименовывать здесь нечего, и это единственный источник, где нормализация
# сводится к отбору полей, а не к переводу терминов.

PAYLOAD_FIELD_CHANNEL = "channel"
PAYLOAD_FIELD_PROVIDER = "provider"
PAYLOAD_FIELD_EVENT_ID = "event_id"
PAYLOAD_FIELD_RECORD_ID = "record_id"
PAYLOAD_FIELD_LEVEL = "level"
PAYLOAD_FIELD_TASK = "task"
PAYLOAD_FIELD_OPCODE = "opcode"
PAYLOAD_FIELD_MESSAGE = "message"
"""Форматированное сообщение записи; появляется в payload только при ``capture_message``.

Нормализатор его в событие **не переносит**: сообщение остаётся в сыром
событии, а timeline показывает знание Chronoscope, а не текст источника
(§37, §77.2 пункт 10).
"""
