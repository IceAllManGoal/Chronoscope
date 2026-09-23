"""Клиент локального API Core для CLI.

CLI — такой же клиент Core, как Agent, и обращается к нему по HTTP, а не к базе
напрямую. Причины две. Во-первых, инвариант 10 §81 запрещает обход API: база
принадлежит Core, и второй писатель в неё — это способ получить расхождение
представлений. Во-вторых, часть команд по смыслу требует живого Core: `status`
показывает его состояние, `doctor` проверяет доступность и применённую схему.

Транспорт — стандартная библиотека, а не httpx. §65 запрещает добавлять
зависимость ради небольшого удобства: здесь нужны GET, разбор JSON и таймаут,
то есть ровно то, что `urllib.request` умеет без дополнительных пакетов. httpx
остаётся dev-зависимостью для тестов и не попадает в runtime.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from chronoscope import __version__

API_PREFIX = "/api/v1"
DEFAULT_TIMEOUT_SECONDS = 5.0


class CliError(Exception):
    """Ошибка CLI, которую нужно показать пользователю без трассировки."""


class CoreUnavailable(CliError):
    """Core не отвечает: не запущен, другой порт или другой адрес."""


class CoreRejected(CliError):
    """Core ответил ошибкой из своего конверта (§60)."""

    def __init__(self, *, code: str, message: str, status: int) -> None:
        super().__init__(message)
        # message сохраняется отдельным атрибутом, а не берётся из аргументов
        # исключения: вызывающий код показывает код машинной ошибки и текст
        # рядом, а обращение к несуществующему атрибуту превратило бы понятное
        # сообщение в трассировку — то есть ровно в то, чего CLI не должен делать.
        self.message = message
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class CoreClient:
    """Минимальный клиент Core API."""

    base_url: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def health(self) -> Mapping[str, Any]:
        return self._get("/health")

    def status(self) -> Mapping[str, Any]:
        return self._get("/status")

    def events(self, **params: Any) -> Mapping[str, Any]:
        """Список событий с фильтрами и курсором (§31, §32)."""
        return self._get("/events", params)

    def event(self, event_id: str) -> Mapping[str, Any]:
        return self._get(f"/events/{urllib.parse.quote(event_id, safe='')}")

    def process(self, process_instance_id: str) -> Mapping[str, Any]:
        """Экземпляр процесса как сущность (§77.1)."""
        return self._get(f"/processes/{urllib.parse.quote(process_instance_id, safe='')}")

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        url = f"{self.base_url}{API_PREFIX}{path}"
        if params:
            # Пустые значения не отправляются: `type=` и отсутствие `type` для
            # API — разные вещи, а пустой параметр выглядел бы как «фильтр задан».
            cleaned = {key: value for key, value in params.items() if value is not None and value != ""}
            if cleaned:
                url = f"{url}?{urllib.parse.urlencode(cleaned, doseq=True)}"

        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": f"chronoscope-cli/{__version__}"})

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise self._rejected(error) from error
        except urllib.error.URLError as error:
            raise CoreUnavailable(self._unreachable_message(error.reason)) from error
        except socket.timeout as error:
            raise CoreUnavailable(self._unreachable_message("истёк таймаут ожидания ответа")) from error

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise CliError(f"Core ответил не JSON: {error}") from error

        if not isinstance(payload, dict):
            raise CliError("Core ответил JSON, но не объектом")

        return payload

    def _rejected(self, error: urllib.error.HTTPError) -> CliError:
        """Разобрать конверт ошибки Core (§60) и показать код вместе с текстом."""
        try:
            payload = json.loads(error.read().decode("utf-8"))
            detail = payload["error"]
            return CoreRejected(
                code=str(detail.get("code", "unknown")),
                message=str(detail.get("message", error.reason)),
                status=error.code,
            )
        except (json.JSONDecodeError, KeyError, AttributeError, UnicodeDecodeError):
            return CoreRejected(code="unknown", message=f"HTTP {error.code} {error.reason}", status=error.code)

    def _unreachable_message(self, reason: object) -> str:
        return (
            f"Core не отвечает на {self.base_url}: {reason}.\n"
            "Проверь, что он запущен: cd core && uv run python -m chronoscope"
        )


def resolve_base_url(explicit: str | None = None) -> str:
    """Определить адрес Core.

    Явный аргумент, иначе — тот же конфигурационный файл, что читает Core (§36).
    Отдельного ключа для CLI нет: два независимых описания одного адреса рано
    или поздно разошлись бы.
    """
    if explicit:
        return explicit.rstrip("/")

    # Импорт внутри функции: конфигурация нужна только при запуске команды, а не
    # при импорте модуля, — иначе CLI нельзя было бы импортировать без файла.
    from chronoscope.infrastructure.config.settings import load_settings

    settings = load_settings()
    return f"http://{settings.host}:{settings.port}"
