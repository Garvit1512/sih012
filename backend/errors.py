"""Consistent error envelope: {"error": {"code", "message", "details"}}. No stack traces leave the server."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("sih.backend")


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


def _resp(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details or {}}})


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return _resp(e.status, e.code, e.message, e.details)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        errs = [{"loc": [str(x) for x in err["loc"]], "message": err["msg"], "type": err["type"]} for err in e.errors()][:20]
        malformed = any(err["type"] in ("json_invalid", "model_attributes_type") for err in e.errors())
        return _resp(422, "MALFORMED_JSON" if malformed else "INVALID_REQUEST",
                     "The request body is not valid JSON." if malformed else "The request did not match the expected schema.",
                     {"errors": errs})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(e.status_code, "HTTP_ERROR")
        return _resp(e.status_code, code, str(e.detail))

    @app.exception_handler(Exception)
    async def _any(_: Request, e: Exception):
        log.exception("unhandled error")
        return _resp(500, "INTERNAL_ERROR", "Unexpected server error. See the server log.")
