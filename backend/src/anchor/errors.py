"""The API's one error shape: ``{"error": {"code": ..., "message": ...}}``."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def install(app: FastAPI) -> None:
    async def handle_api_error(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, ApiError)
        return _response(error)

    async def handle_validation_error(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)
        return _response(invalid_input(error))

    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)


def invalid_input(error: RequestValidationError) -> ApiError:
    """The first validation failure, worded for a person: "Password: at least 8 characters"."""
    first = next(iter(error.errors()), None)
    if first is None:
        return ApiError(422, "invalid_input", "The request is not valid.")
    field = str(first["loc"][-1]) if first.get("loc") else "input"
    message = str(first.get("msg", "is not valid")).removeprefix("Value error, ")
    return ApiError(422, "invalid_input", f"{field.capitalize()}: {message}.")


def _response(error: ApiError) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message}},
        status_code=error.status_code,
        headers=error.headers,
    )
