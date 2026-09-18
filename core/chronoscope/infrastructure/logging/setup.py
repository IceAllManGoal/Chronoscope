"""Structured logging (§35).

Требование спеки: оба runtime-компонента пишут структурированные логи, а не
строки вида «something failed». Формат — JSON: его читает и человек, и машина,
и он не ломается при добавлении новых полей.

Полноценный observability stack вокруг самого Chronoscope не нужен: достаточно
консоли и опционального ротируемого файла (§35).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Поля, которые logging добавляет сам. Они не дублируются в JSON.
_STANDARD_FIELDS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "taskName", "thread", "threadName",
    }
)

LOGGER_NAME = "chronoscope"
DEFAULT_COMPONENT = "core"

_LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
_LOG_FILE_BACKUPS = 3


class JsonFormatter(logging.Formatter):
    """Превращает LogRecord в одну JSON-строку.

    Поле ``event`` — короткий машинный идентификатор произошедшего
    (``collector_read_failed``, ``raw_event_stored``), а ``message`` —
    человекочитаемое пояснение. Такое разделение позволяет фильтровать логи
    без разбора текста.
    """

    def __init__(self, component: str = DEFAULT_COMPONENT) -> None:
        super().__init__()
        self._component = component

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "component": getattr(record, "component", self._component),
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_FIELDS or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    *,
    level: str = "INFO",
    component: str = DEFAULT_COMPONENT,
    log_file: Path | None = None,
) -> None:
    """Настроить логирование Core: консоль и, при указании, ротируемый файл."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = JsonFormatter(component=component)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=_LOG_FILE_MAX_BYTES,
            backupCount=_LOG_FILE_BACKUPS,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        logger.addHandler(rotating)


def get_logger(name: str | None = None) -> logging.Logger:
    """Логгер внутри иерархии Chronoscope."""
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **fields: Any,
) -> None:
    """Записать структурированное событие лога.

    ``event`` попадает в отдельное поле, а произвольные ``fields`` становятся
    полями JSON — так лог остаётся машиночитаемым без парсинга текста.
    """
    logger.log(level, message, extra={"event": event, **fields})
