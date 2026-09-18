"""Единый формат ошибок API (§60).

§60 требует, чтобы Core различал категории отказов, а не сообщал «что-то
пошло не так». Здесь эти категории превращаются в HTTP-статусы и машинные
коды, по которым клиент может принимать решения.

Соответствие категорий §60 и кодов:

```text
invalid input                422  invalid_input
unsupported schema version   422  unsupported_schema_version
database failure             500  storage_failure
not found                    404  not_found
request too large            413  request_too_large
```

Дубликат события намеренно **не** является ошибкой: §34 требует
идемпотентности при at-least-once доставке, поэтому повторная отправка —
нормальная ситуация, и о ней сообщается счётчиком в успешном ответе.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from chronoscope.api.schemas import ErrorDetail, ErrorOut
from chronoscope.domain.errors import (
    ChronoscopeError,
    InvalidInputError,
    StorageError,
    UnsupportedSchemaVersionError,
)
from chronoscope.infrastructure.logging.setup import get_logger, log_event

STATUS_BY_EXCEPTION: dict[type[Exception], tuple[int, str]] = {
    UnsupportedSchemaVersionError: (422, "unsupported_schema_version"),
    InvalidInputError: (422, "invalid_input"),
    StorageError: (500, "storage_failure"),
}

_STATUS_CODES = {
    404: "not_found",
    405: "method_not_allowed",
    413: "request_too_large",
    422: "invalid_input",
    500: "internal_error",
}

_MAX_REPORTED_DETAILS = 10


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
) -> JSONResponse:
    body = ErrorOut(
        error=ErrorDetail(code=code, message=message, details=details or []),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    logger = get_logger("api")

    @app.exception_handler(UnsupportedSchemaVersionError)
    async def _unsupported_schema(_request: Request, exc: UnsupportedSchemaVersionError) -> JSONResponse:
        log_event(
            logger,
            logging.WARNING,
            "unsupported_schema_version",
            "отвергнута неподдерживаемая версия схемы",
            received=exc.received,
            supported=exc.supported,
        )
        status_code, code = STATUS_BY_EXCEPTION[UnsupportedSchemaVersionError]
        return error_response(status_code=status_code, code=code, message=str(exc))

    @app.exception_handler(InvalidInputError)
    async def _invalid_input(_request: Request, exc: InvalidInputError) -> JSONResponse:
        status_code, code = STATUS_BY_EXCEPTION[InvalidInputError]
        return error_response(status_code=status_code, code=code, message=str(exc))

    @app.exception_handler(StorageError)
    async def _storage_failure(_request: Request, exc: StorageError) -> JSONResponse:
        log_event(logger, logging.ERROR, "storage_failure", str(exc))
        status_code, code = STATUS_BY_EXCEPTION[StorageError]
        return error_response(status_code=status_code, code=code, message=str(exc))

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "location": ".".join(str(part) for part in error.get("loc", ())),
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
            }
            for error in exc.errors()[:_MAX_REPORTED_DETAILS]
        ]
        return error_response(
            status_code=422,
            code="invalid_input",
            message="тело или параметры запроса не соответствуют контракту",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=_STATUS_CODES.get(exc.status_code, "http_error"),
            message=str(exc.detail),
        )

    @app.exception_handler(ChronoscopeError)
    async def _chronoscope_error(_request: Request, exc: ChronoscopeError) -> JSONResponse:
        log_event(logger, logging.ERROR, "unhandled_chronoscope_error", str(exc), error_type=type(exc).__name__)
        return error_response(status_code=500, code="internal_error", message=str(exc))
