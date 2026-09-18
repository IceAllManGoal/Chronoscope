"""Идентификатор компьютера (§15).

Правило модели: ``host_id`` — **случайный локальный** идентификатор. Он не
должен быть hardware fingerprint, серийным номером, именем компьютера или
Microsoft account. Причины (§15 спеки):

- экспорт и импорт исторических записей между машинами;
- возможная поддержка нескольких машин;
- защита от случайного смешивания событий разных компьютеров.

Требование «случайный» здесь не косметическое: идентификатор, выведенный из
характеристик железа, превратил бы Chronoscope в инструмент идентификации
устройства, чего продукт делать не должен.
"""

from __future__ import annotations

from chronoscope.domain.ids import (
    PREFIX_HOST,
    is_prefixed_id,
    new_host_id,
    require_prefixed_id,
)

__all__ = ["is_host_id", "new_host_id", "require_host_id"]


def is_host_id(value: str) -> bool:
    return is_prefixed_id(value, PREFIX_HOST)


def require_host_id(value: str, field: str = "host_id") -> str:
    return require_prefixed_id(value, PREFIX_HOST, field)
