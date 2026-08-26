"""The API's error shape: ``{"error": {"code": ..., "message": ...}}``."""

from fastapi import FastAPI, Request
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
    async def handle(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, ApiError)
        return JSONResponse(
            {"error": {"code": error.code, "message": error.message}},
            status_code=error.status_code,
            headers=error.headers,
        )

    app.add_exception_handler(ApiError, handle)
