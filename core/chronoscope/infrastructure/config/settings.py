"""Конфигурация Core (§36).

Формат — TOML. Главное правило §36: настройки не хранятся в коде. Значения по
умолчанию в коде есть, но они лишь запасной вариант, а не способ конфигурации.

Файл может содержать секции Agent (``[agent]``, ``[collectors.*]``,
``[privacy]``) — §36 предполагает единый конфигурационный файл проекта. Core
читает только свои секции и игнорирует остальные, но **отвергает неизвестные
ключи внутри своих секций**: опечатка в имени параметра иначе выглядела бы как
«настройка не применилась».
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chronoscope.domain.errors import ConfigurationError

ENV_CONFIG_PATH = "CHRONOSCOPE_CONFIG"
DEFAULT_CONFIG_NAME = "chronoscope.toml"

#: Значения, на которых Core вправе слушать (§33, ADR-0007).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7342
_DEFAULT_DATABASE_PATH = "./data/chronoscope.db"
_DEFAULT_BUSY_TIMEOUT_MS = 5000
_DEFAULT_MAX_REQUEST_BYTES = 5 * 1024 * 1024
_DEFAULT_LOG_LEVEL = "INFO"

_KNOWN_SECTIONS = {"core", "storage"}
_KNOWN_KEYS: dict[str, set[str]] = {
    "core": {"host", "port", "log_level", "max_request_bytes"},
    "storage": {"database_path", "busy_timeout_ms"},
}


@dataclass(frozen=True, slots=True)
class CoreSettings:
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    database_path: Path = Path(_DEFAULT_DATABASE_PATH)
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS
    max_request_bytes: int = _DEFAULT_MAX_REQUEST_BYTES
    log_level: str = _DEFAULT_LOG_LEVEL

    def __post_init__(self) -> None:
        # ADR-0007 / §33: bind только на loopback. Проверка живёт здесь, а не
        # в коде запуска сервера, чтобы небезопасная конфигурация не могла
        # возникнуть вообще — ни через файл, ни через переменную окружения.
        if self.host not in LOOPBACK_HOSTS:
            raise ConfigurationError(
                f"core.host={self.host!r} не является loopback-адресом. "
                f"API обязан слушать только {sorted(LOOPBACK_HOSTS)} (§33, ADR-0007)"
            )
        if not 1 <= self.port <= 65535:
            raise ConfigurationError(f"core.port={self.port} вне диапазона 1..65535")
        if self.busy_timeout_ms <= 0:
            raise ConfigurationError("storage.busy_timeout_ms должен быть положительным")
        if self.max_request_bytes <= 0:
            raise ConfigurationError("core.max_request_bytes должен быть положительным")
        if not self.database_path.name:
            raise ConfigurationError("storage.database_path не может быть пустым")


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"не удалось разобрать {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"не удалось прочитать {path}: {exc}") from exc


def _reject_unknown_keys(section: str, values: dict[str, Any]) -> None:
    unknown = sorted(set(values) - _KNOWN_KEYS[section])
    if unknown:
        raise ConfigurationError(
            f"неизвестные ключи в секции [{section}]: {', '.join(unknown)}. "
            f"Известные: {', '.join(sorted(_KNOWN_KEYS[section]))}"
        )


def resolve_config_path(explicit: Path | None = None) -> Path | None:
    """Определить, откуда читать конфигурацию.

    Приоритет: явный аргумент, затем переменная окружения ``CHRONOSCOPE_CONFIG``,
    затем ``chronoscope.toml`` в текущем каталоге. Если файла нет — работаем на
    значениях по умолчанию.
    """
    if explicit is not None:
        return explicit
    from_env = os.environ.get(ENV_CONFIG_PATH)
    if from_env:
        return Path(from_env)
    default = Path(DEFAULT_CONFIG_NAME)
    return default if default.is_file() else None


def load_settings(config_path: Path | None = None) -> CoreSettings:
    """Загрузить настройки Core, наложив файл поверх значений по умолчанию."""
    path = resolve_config_path(config_path)
    if path is None:
        return CoreSettings()

    if not path.is_file():
        raise ConfigurationError(f"конфигурационный файл не найден: {path}")

    document = _read_toml(path)

    for section in document:
        if section not in _KNOWN_SECTIONS:
            # Секции Agent и privacy относятся к другому компоненту (§36).
            continue
        if not isinstance(document[section], dict):
            raise ConfigurationError(f"секция [{section}] должна быть таблицей")

    values: dict[str, Any] = {}
    for section in _KNOWN_SECTIONS:
        section_values = document.get(section) or {}
        _reject_unknown_keys(section, section_values)
        values.update(section_values)

    if "database_path" in values:
        values["database_path"] = Path(values["database_path"])

    return CoreSettings(**values)
