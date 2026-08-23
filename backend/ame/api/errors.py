from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ame.observability import get_logger
from ame.security.secrets import mask_secret

log = get_logger("ame.api.errors")

_SECRET_HINTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "refresh",
    "private_key",
)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            lowered = str(key).lower()
            if lowered in {"token_encrypted", "refresh_encrypted"}:
                continue
            if any(hint in lowered for hint in _SECRET_HINTS):
                out[key] = mask_secret(inner) if isinstance(inner, str) else "****"
            else:
                out[key] = redact_secrets(inner)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def error_body(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": redact_secrets(details or {}),
        }
    }


class APIError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, details=exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=redact_secrets(detail))
        message = detail if isinstance(detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("http_error", message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "validation_error",
                "Request validation failed",
                details={"errors": exc.errors()},
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_api_error", error=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "An unexpected error occurred"),
        )
