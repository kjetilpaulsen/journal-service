"""Application factory and error mapping."""

import logging

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from journal_service.api.routes import ROUTES
from journal_service.api.sessions import SessionStore
from journal_service.errors import (
    AlreadyInitialized,
    CorruptEntry,
    EntryNotFound,
    InvalidPassword,
    JournalError,
    NotInitialized,
    PayloadTooLarge,
    Unauthorized,
    ValidationFailed,
)
from journal_service.journal import Journal

log = logging.getLogger(__name__)

_STATUS: dict[type[JournalError], int] = {
    ValidationFailed: 422,
    PayloadTooLarge: 413,
    InvalidPassword: 401,
    Unauthorized: 401,
    EntryNotFound: 404,
    NotInitialized: 409,
    AlreadyInitialized: 409,
    CorruptEntry: 500,
}


def _status_for(exc: JournalError) -> int:
    for cls in type(exc).__mro__:
        if cls in _STATUS:
            return _STATUS[cls]
    return 500


async def _journal_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, JournalError)
    status = _status_for(exc)
    if isinstance(exc, InvalidPassword):
        log.warning("unlock failed: wrong password")
    elif status >= 500:
        log.error("%s %s failed: %s", request.method, request.url.path, exc)
    return JSONResponse({"error": str(exc)}, status_code=status)


async def _http_error(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)


async def _internal_error(_request: Request, _exc: Exception) -> Response:
    return JSONResponse({"error": "internal server error"}, status_code=500)


def create_app(journal: Journal, sessions: SessionStore, *, max_request_bytes: int) -> Starlette:
    app = Starlette(
        routes=ROUTES,
        exception_handlers={
            JournalError: _journal_error,
            HTTPException: _http_error,
            500: _internal_error,
        },
    )
    app.state.journal = journal
    app.state.sessions = sessions
    # Enforced in parsing.json_object (Starlette's own limit answers in plain text, not JSON).
    app.state.max_request_bytes = max_request_bytes
    return app
