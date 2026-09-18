"""Идентификатор загрузки операционной системы (§16).

``boot_id`` присваивается каждой загрузке ОС. Он нужен, чтобы:

- различать одинаковые PID после перезагрузки;
- группировать события по system session;
- анализировать startup sequence;
- сравнивать boots между собой.

``boot_id`` может отсутствовать (``None``), если источник не позволяет
определить boot session. Нормализатор в таком случае **не выдумывает**
значение: подставленный идентификатор склеил бы события разных загрузок и
испортил бы историю, а отсутствие значения — честно отражает ограничение
источника.
"""

from __future__ import annotations

from chronoscope.domain.ids import (
    PREFIX_BOOT,
    is_prefixed_id,
    new_boot_id,
    require_prefixed_id,
)

__all__ = ["is_boot_id", "new_boot_id", "require_boot_id"]


def is_boot_id(value: str) -> bool:
    return is_prefixed_id(value, PREFIX_BOOT)


def require_boot_id(value: str, field: str = "boot_id") -> str:
    return require_prefixed_id(value, PREFIX_BOOT, field)
