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


class HistoryLimitExceededError(ChronoscopeError):
    """История одного экземпляра процесса длиннее, чем Core готов прочитать целиком.

    Это **не** «нет данных» и не отказ хранилища: событий у экземпляра больше,
    чем detail согласен прочитать. Ответ по части истории был бы вымыслом —
    detail сообщил бы «завершение не наблюдалось» о процессе, чьё завершение не
    поместилось в выборку.

    Такой отказ виден пользователю как ошибка, а не как пустая страница: молча
    усечённый detail невозможно отличить от честного (§77.1).
    """

    def __init__(self, *, process_instance_id: str, limit: int) -> None:
        self.process_instance_id = process_instance_id
        self.limit = limit
        super().__init__(
            f"история экземпляра процесса {process_instance_id!r} длиннее предела "
            f"{limit} событий: detail по неполной истории не строится"
        )


# ── Инфраструктура ───────────────────────────────────────────────────


class StorageError(ChronoscopeError):
    """Database failure: ошибка слоя хранения."""


class ConfigurationError(ChronoscopeError):
    """Некорректная конфигурация Core."""


class CollectorError(ChronoscopeError):
    """Ошибка коллектора. Относится к Agent, тип объявлен для полноты §60."""


class TransportError(ChronoscopeError):
    """Ошибка доставки событий между Agent и Core (§60)."""
