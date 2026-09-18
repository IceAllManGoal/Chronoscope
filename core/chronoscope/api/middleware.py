"""Ограничение размера запроса (§33, §66).

§33 перечисляет request size limits среди минимума для 0.0.1, а §66 — среди
правил безопасности. Ограничение сделано middleware, а не проверкой внутри
обработчика, чтобы оно действовало на все эндпоинты, включая будущие.

Если ``Content-Length`` отсутствует (chunked-запрос), запрос не отвергается по
размеру: полная проверка требовала бы буферизации тела, что для локального
loopback-API дало бы больше вреда, чем пользы. Валидация структуры и лимит
числа событий в пакете при этом продолжают действовать.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from chronoscope.api.errors import error_response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_bytes: int) -> None:  # noqa: ANN001 - тип ASGI-приложения
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError:
                return error_response(
                    status_code=400,
                    code="invalid_input",
                    message=f"заголовок Content-Length не является числом: {raw_length!r}",
                )

            if declared_length > self._max_bytes:
                return error_response(
                    status_code=413,
                    code="request_too_large",
                    message=(
                        f"размер запроса {declared_length} байт превышает лимит "
                        f"{self._max_bytes} байт (core.max_request_bytes)"
                    ),
                )

        return await call_next(request)
