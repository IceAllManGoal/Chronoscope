"""Идентификаторы Chronoscope: ULID, префиксы и детерминированные идентичности.

Реализация ULID написана вручную, а не взята зависимостью, по двум причинам:

1. §65 требует не добавлять dependency ради небольшого удобства, а формат ULID
   прост и стабилен: 48 бит времени + 80 бит случайности в base32 Crockford.
2. Нужен **детерминированный** конструктор (см. :func:`process_instance_id`),
   которого готовые библиотеки не предоставляют. Без него идентичность
   экземпляра процесса нельзя воспроизвести при повторной обработке одних и
   тех же raw-данных, а §14 и docs/EVENT_MODEL.md этого требуют.

Соответствие спецификации: §17 (сортируемые идентификаторы, категории),
docs/EVENT_MODEL.md §2.1 (форма записи).
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

from chronoscope.domain.errors import InvalidInputError
from chronoscope.domain.validation import require_utc

# Алфавит Crockford base32: без I, L, O и U — их слишком легко спутать
# с 1, 0 и между собой при чтении идентификаторов глазами.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ALPHABET_INDEX = {char: index for index, char in enumerate(ALPHABET)}

_TIME_CHARS = 10
_RANDOM_CHARS = 16
ULID_LENGTH = _TIME_CHARS + _RANDOM_CHARS

# Класс символов и шаблон ULID. Используются и для валидации, и в тестах,
# которые сверяют их с pattern из shared/schemas/*.schema.json.
ULID_CHAR_CLASS = f"[{ALPHABET}]"
ULID_PATTERN = f"^{ULID_CHAR_CLASS}{{{ULID_LENGTH}}}$"

_MAX_TIMESTAMP_MS = (1 << 48) - 1
_MAX_RANDOM_BITS = (1 << 80) - 1
_RANDOM_BYTES = 10  # 80 бит


def _encode(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        chars.append(ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _decode(text: str) -> int:
    value = 0
    for char in text:
        value = (value << 5) | _ALPHABET_INDEX[char]
    return value


def _to_ms(moment: datetime) -> int:
    require_utc(moment, "timestamp")
    milliseconds = int(round(moment.timestamp() * 1000))
    if not 0 <= milliseconds <= _MAX_TIMESTAMP_MS:
        raise InvalidInputError(f"timestamp вне диапазона ULID: {moment.isoformat()}")
    return milliseconds


def is_ulid(value: str) -> bool:
    """Проверить, что строка — ULID в канонической форме (заглавные, 26 символов)."""
    if not isinstance(value, str) or len(value) != ULID_LENGTH:
        return False
    return all(char in _ALPHABET_INDEX for char in value)


def ulid_from(*, timestamp_ms: int, random_bits: int) -> str:
    """Собрать ULID из готовых частей.

    Именно эта функция делает идентичность воспроизводимой: одни и те же
    входные данные всегда дают один и тот же идентификатор.
    """
    if not 0 <= timestamp_ms <= _MAX_TIMESTAMP_MS:
        raise InvalidInputError(f"timestamp_ms вне диапазона ULID: {timestamp_ms}")
    if not 0 <= random_bits <= _MAX_RANDOM_BITS:
        raise InvalidInputError(f"random_bits вне диапазона ULID: {random_bits}")
    return _encode(timestamp_ms, _TIME_CHARS) + _encode(random_bits, _RANDOM_CHARS)


def new_ulid(*, at: datetime | None = None) -> str:
    """Новый ULID для текущего момента (или для указанного времени)."""
    return ulid_from(
        timestamp_ms=_to_ms(at or datetime.now(UTC)),
        random_bits=int.from_bytes(os.urandom(_RANDOM_BYTES), "big"),
    )


def require_ulid(value: str, field: str) -> str:
    if not is_ulid(value):
        raise InvalidInputError(f"{field}: {value!r} не является ULID в канонической форме")
    return value


def ulid_timestamp_ms(ulid: str) -> int:
    require_ulid(ulid, "ulid")
    return _decode(ulid[:_TIME_CHARS])


def ulid_timestamp(ulid: str) -> datetime:
    return datetime.fromtimestamp(ulid_timestamp_ms(ulid) / 1000, UTC)


# ── Идентификаторы с префиксом (§17) ─────────────────────────────────
#
# Префиксы используемых в 0.0.1 категорий. Список не закрыт: §17 перечисляет
# также incident_id и correlation_id, которые появятся позже (не в 0.0.1).

PREFIX_RAW_EVENT = "raw"
PREFIX_EVENT = "evt"
PREFIX_HOST = "host"
PREFIX_BOOT = "boot"
PREFIX_PROCESS_INSTANCE = "proc"
PREFIX_RELATIONSHIP = "rel"


def new_id(prefix: str) -> str:
    """Новый идентификатор вида ``<prefix>_<ULID>``."""
    if not prefix or not prefix.isascii() or not prefix.islower() or not prefix.isidentifier():
        raise InvalidInputError(f"некорректный префикс идентификатора: {prefix!r}")
    return f"{prefix}_{new_ulid()}"


def is_prefixed_id(value: str, prefix: str) -> bool:
    head, separator, body = value.partition("_")
    return separator == "_" and head == prefix and is_ulid(body)


def require_prefixed_id(value: str, prefix: str, field: str) -> str:
    if not is_prefixed_id(value, prefix):
        raise InvalidInputError(
            f"{field}: {value!r} должно иметь форму {prefix}_<ULID> "
            f"(26 заглавных символов Crockford base32)"
        )
    return value


def new_raw_event_id() -> str:
    return new_id(PREFIX_RAW_EVENT)


def new_event_id() -> str:
    return new_id(PREFIX_EVENT)


def new_host_id() -> str:
    return new_id(PREFIX_HOST)


def new_boot_id() -> str:
    return new_id(PREFIX_BOOT)


def process_instance_id(
    *,
    host_id: str,
    boot_id: str | None,
    pid: int,
    started_at: datetime,
) -> str:
    """Детерминированная идентичность экземпляра процесса (§14).

    Концептуально ``host_id + boot_id + pid + process_start_timestamp``,
    записанная как ULID: время старта процесса задаёт временную часть, а
    хэш от остальных компонентов — случайную.

    Почему именно так:

    - **детерминированность.** Событие старта и событие выхода одного и того
      же процесса обязаны дать одинаковый идентификатор, иначе связь между
      ними теряется, а вместе с ней — весь смысл process tree (§43);
    - **уникальность.** В пределах одной загрузки PID уникален в каждый момент
      времени, а переиспользование PID после завершения процесса меняет
      время старта, поэтому идентификаторы не совпадут;
    - **упорядоченность.** ULID сортируется по времени, а временная часть —
      реальное время старта процесса.

    ``boot_id`` может быть ``None``: тогда в хэш попадает пустая строка, и
    гарантия уникальности слабеет ровно настолько, насколько источник не
    смог сообщить boot session.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise InvalidInputError(f"pid должен быть положительным целым, получено {pid!r}")

    material = f"{host_id}|{boot_id or ''}|{pid}".encode("utf-8")
    digest = hashlib.sha256(material).digest()

    return (
        f"{PREFIX_PROCESS_INSTANCE}_"
        + ulid_from(
            timestamp_ms=_to_ms(started_at),
            random_bits=int.from_bytes(digest[:_RANDOM_BYTES], "big"),
        )
    )
