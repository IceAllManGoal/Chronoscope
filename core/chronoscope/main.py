"""Точка входа Chronoscope Core (§49, §63).

Создание приложения вынесено в :func:`create_app`, чтобы тесты поднимали
настоящее приложение с временной базой, а не собирали обработчики вручную.
Иначе интеграционный тест проверял бы не то, что запускается у пользователя.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI

from chronoscope import __version__
from chronoscope.api.dependencies import CoreContainer, build_container
from chronoscope.api.errors import register_exception_handlers
from chronoscope.api.middleware import RequestSizeLimitMiddleware
from chronoscope.api.routes import events, health, ingest, status
from chronoscope.infrastructure.config.settings import CoreSettings, load_settings
from chronoscope.infrastructure.database.engine import missing_tables
from chronoscope.infrastructure.logging.setup import configure_logging, get_logger, log_event

API_PREFIX = "/api/v1"
TITLE = "Chronoscope Core API"
DESCRIPTION = (
    "Локальный API Chronoscope Core: приём сырых событий, их нормализация, "
    "хранение и выдача. Слушает только loopback (§33, ADR-0007)."
)


def create_app(settings: CoreSettings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    configure_logging(level=resolved_settings.log_level, component="core")
    logger = get_logger("main")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container: CoreContainer = app.state.chronoscope_container

        absent = missing_tables(container.engine)
        if absent:
            # Не поднимаем исключение: Core остаётся доступным для health и
            # диагностики, а пользователь получает точную инструкцию вместо
            # невнятной ошибки «no such table» при первом запросе.
            log_event(
                logger,
                logging.CRITICAL,
                "schema_incomplete",
                "схема базы не применена: выполни 'uv run alembic upgrade head'",
                missing_tables=sorted(absent),
                database=str(resolved_settings.database_path),
            )

        log_event(
            logger,
            logging.INFO,
            "core_started",
            "Chronoscope Core запущен",
            version=__version__,
            host=resolved_settings.host,
            port=resolved_settings.port,
            database=str(resolved_settings.database_path),
        )

        try:
            yield
        finally:
            container.close()
            log_event(logger, logging.INFO, "core_stopped", "Chronoscope Core остановлен")

    app = FastAPI(
        title=TITLE,
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    build_container(app, resolved_settings)

    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=resolved_settings.max_request_bytes)
    register_exception_handlers(app)

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(status.router, prefix=API_PREFIX)
    app.include_router(ingest.router, prefix=API_PREFIX)
    app.include_router(events.router, prefix=API_PREFIX)

    return app


def run() -> None:
    """Запустить Core так, как это описано в §63.

    ``log_config=None`` не даёт uvicorn переопределить настроенное
    структурированное логирование: §35 требует одного формата на компонент.
    """
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
