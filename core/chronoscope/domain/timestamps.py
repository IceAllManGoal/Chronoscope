"""Форматирование и разбор времени (§24).

§24 требует хранить все timestamps в UTC. Здесь собраны две разные формы
записи, и различие между ними принципиально:

- :func:`format_utc` — **отображающая** форма для контракта и API. Миллисекунды,
  когда точность кратна миллисекунде, иначе микросекунды. Совпадает по стилю
  с примерами §12 и §13 и с фикстурами в ``shared/fixtures``.
- :func:`format_utc_fixed` — **хранилищная** форма фиксированной ширины.
  Нужна, потому что timestamps в SQLite хранятся как TEXT (§26), а
  лексикографический порядок строк совпадает с хронологическим только при
  одинаковой длине дробной части. Без выравнивания ``…15.220Z`` оказалось бы
  «меньше» ``…15Z``, хотя 15.220 секунды позже 15.000: символ ``.`` идёт
  раньше ``Z``. Это тихая ошибка сортировки, поэтому ширина фиксирована.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chronoscope.domain.errors import InvalidInputError
from chronoscope.domain.validation import require_utc

_STORAGE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def format_utc(moment: datetime) -> str:
    """Отображающая форма: RFC 3339 UTC с суффиксом ``Z``."""
    require_utc(moment, "timestamp")
    moment = moment.astimezone(UTC)

    if moment.microsecond == 0:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    if moment.microsecond % 1000 == 0:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
    return moment.strftime(_STORAGE_FORMAT)


def format_utc_fixed(moment: datetime) -> str:
    """Хранилищная форма фиксированной ширины: всегда микросекунды.

    Именно эта форма попадает в колонки ``*_at`` таблиц и участвует в
    ``ORDER BY`` и в сравнениях курсора.
    """
    require_utc(moment, "timestamp")
    return moment.astimezone(UTC).strftime(_STORAGE_FORMAT)


def parse_utc(value: str, field: str = "timestamp") -> datetime:
    """Разобрать timestamp из хранилища или из запроса."""
    if not isinstance(value, str):
        raise InvalidInputError(f"{field}: ожидалась строка с timestamp")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputError(f"{field}: не удалось разобрать timestamp {value!r}") from exc
    return require_utc(moment, field)
