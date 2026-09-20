"""CLI Chronoscope (§57).

Отдельный пакет, а не часть `api/`: CLI — клиент API, а не его обработчик. Он
обращается к Core по HTTP и не знает ни про базу, ни про use cases, поэтому
границы слоёв §50 не затрагиваются.
"""

from chronoscope.cli.main import build_parser, main

__all__ = ["build_parser", "main"]
