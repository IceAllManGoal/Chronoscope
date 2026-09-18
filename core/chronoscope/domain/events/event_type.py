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

# ── Типы payload внутри windows.process ──────────────────────────────

PAYLOAD_PROCESS_START = "process_start"
PAYLOAD_PROCESS_EXIT = "process_exit"

# ── Типы нормализованных событий (§18) ───────────────────────────────

PROCESS_STARTED = "process.started"
PROCESS_EXITED = "process.exited"

SYSTEM_BOOTED = "system.booted"
SYSTEM_SHUTDOWN = "system.shutdown"

INCIDENT_MARKED = "incident.marked"

KNOWN_EVENT_TYPES = frozenset(
    {
        PROCESS_STARTED,
        PROCESS_EXITED,
        SYSTEM_BOOTED,
        SYSTEM_SHUTDOWN,
        INCIDENT_MARKED,
    }
)
"""Типы, которые Core умеет порождать в 0.0.1.

Список не является ограничением контракта: он нужен для документации и
диагностики, а не для отказа в приёме события.
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
