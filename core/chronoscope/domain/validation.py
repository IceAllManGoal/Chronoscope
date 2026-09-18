"""Валидация значений, общая для доменной модели.

Здесь живут только проверки, не зависящие от фреймворков (§50: domain не
зависит от FastAPI, SQLAlchemy, SQLite, HTTP и Windows).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from chronoscope.domain.errors import InvalidInputError

# Формат идентификатора сущности: префикс типа + ULID.
# Согласован с shared/schemas/event.schema.json и docs/EVENT_MODEL.md §2.1.
PREFIXED_ID_RE = re.compile(r"^[a-z][a-z0-9_]*_[0-9A-HJKMNP-TV-Z]{26}$")

# Имя коллектора/источника: не менее двух сегментов в lowercase dot notation.
COLLECTOR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

# Тип события: <domain>.<past-tense-action> (§18).
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

# Дискриминатор payload внутри коллектора.
PAYLOAD_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Тип сущности (§19).
ENTITY_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def require_utc(value: datetime, field: str) -> datetime:
    """§24: все timestamps хранятся в UTC.

    Наивное время отвергается, а не додумывается: молчаливая подстановка
    локальной зоны испортила бы исторические данные необратимо.
    """
    if value.tzinfo is None:
        raise InvalidInputError(f"{field}: timestamp должен быть timezone-aware (UTC), получено наивное значение")
    if value.utcoffset() != timedelta(0):
        raise InvalidInputError(f"{field}: timestamp должен быть в UTC, получено смещение {value.utcoffset()}")
    return value.astimezone(UTC)


def require_matches(value: str, pattern: re.Pattern[str], field: str) -> str:
    if not pattern.match(value):
        raise InvalidInputError(f"{field}: значение {value!r} не соответствует формату {pattern.pattern!r}")
    return value


def require_non_empty(value: str, field: str) -> str:
    if not value:
        raise InvalidInputError(f"{field}: значение не может быть пустым")
    return value
