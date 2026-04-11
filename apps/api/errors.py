from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def _format_validation_details(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for error in errors:
        location = ".".join(str(part) for part in error["loc"])
        details.append(
            {
                "field": location,
                "message": error["msg"],
                "type": error["type"],
            }
        )
    return details


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Invalid request payload.",
                "details": _format_validation_details(exc.errors()),
            }
        },
    )


async def api_error_exception_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )
