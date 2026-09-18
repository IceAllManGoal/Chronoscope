"""Иерархия ошибок Chronoscope Core.

Соответствует §60 PROJECT_SPEC: Core обязан различать invalid input,
unsupported schema version, duplicate event, database failure,
normalizer failure, collector failure и transport failure.

Правило §60: одно плохое событие не должно останавливать pipeline. Поэтому
ошибки нормализации и валидации отдельного события не должны приводить к
падению обработки всего батча.
"""

from __future__ import annotations


class ChronoscopeError(Exception):
    """Базовый класс для всех ошибок Core."""


# ── Входные данные ───────────────────────────────────────────────────


class InvalidInputError(ChronoscopeError):
    """Payload не соответствует контракту: форма, обязательные поля, форматы."""


class UnsupportedSchemaVersionError(ChronoscopeError):
    """§52: неподдерживаемая версия схемы.

    Отдельный тип, потому что §52 требует явного отказа вместо молчаливого
    неверного разбора, и потому что такая ошибка диагностируется иначе,
    чем обычный невалидный payload: она означает несовместимость версий
    Agent и Core, а не дефект данных.
    """

    def __init__(self, *, received: object, supported: object) -> None:
        self.received = received
        self.supported = supported
        super().__init__(
            f"неподдерживаемая schema_version: получено {received!r}, "
            f"поддерживается {supported!r}"
        )


# ── Обработка событий ────────────────────────────────────────────────


class NormalizationError(ChronoscopeError):
    """§60: нормализатор не понял payload.

    Обрабатывается мягко: raw event уже сохранён, событие не превращается в
    Event, факт фиксируется в логах.
    """


class DuplicateEventError(ChronoscopeError):
    """Событие с таким идентификатором уже сохранено.

    Не является сбоем: §34 требует идемпотентности при at-least-once доставке.
    """


# ── Инфраструктура ───────────────────────────────────────────────────


class StorageError(ChronoscopeError):
    """Database failure: ошибка слоя хранения."""


class ConfigurationError(ChronoscopeError):
    """Некорректная конфигурация Core."""


class CollectorError(ChronoscopeError):
    """Ошибка коллектора. Относится к Agent, тип объявлен для полноты §60."""


class TransportError(ChronoscopeError):
    """Ошибка доставки событий между Agent и Core (§60)."""
