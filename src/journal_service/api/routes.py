"""Endpoints.

    GET    /health          liveness, and whether a keypair exists          no password
    POST   /session         {"password"} -> {"token", ...}: unlock            password
    DELETE /session         lock (forget the unlocked keys)                   token
    GET    /entries         ?from=YYYY-MM-DD&to=YYYY-MM-DD: metadata list     no password
    POST   /entries         {"body", "tags"?, "written_at"? | "entry_date"?}  no password
    GET    /entries/{id}    decrypted entry                                   token
    PUT    /entries/{id}    {"body", "tags"?}: replace content                token
    DELETE /entries/{id}    delete                                            token

Blocking work (SQLite, scrypt, age) runs in a worker thread so the event loop stays free.
"""

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from journal_service import __version__
from journal_service.api import parsing
from journal_service.api.sessions import SessionStore
from journal_service.journal import EntryContent, EntryMeta, Journal, Keyring


def _journal(request: Request) -> Journal:
    return request.app.state.journal


def _sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


def _keyring(request: Request) -> Keyring:
    return _sessions(request).keyring(parsing.bearer_token(request))


def _meta_json(meta: EntryMeta) -> dict[str, Any]:
    return {
        "id": str(meta.id),
        "entry_date": meta.entry_date.isoformat(),
        "written_at": meta.written_at.isoformat() if meta.written_at else None,
        "created_at": meta.created_at.isoformat(),
        "updated_at": meta.updated_at.isoformat() if meta.updated_at else None,
    }


def _entry_json(meta: EntryMeta, content: EntryContent) -> dict[str, Any]:
    return {**_meta_json(meta), "body": content.body, "tags": list(content.tags)}


async def health(request: Request) -> Response:
    initialized = await run_in_threadpool(_journal(request).is_initialized)
    return JSONResponse({"status": "ok", "initialized": initialized, "version": __version__})


async def open_session(request: Request) -> Response:
    password = parsing.password(await parsing.json_object(request))
    keyring = await run_in_threadpool(_journal(request).unlock, password)
    sessions = _sessions(request)
    return JSONResponse(
        {
            "token": sessions.open(keyring),
            "idle_timeout_seconds": int(sessions.idle_timeout.total_seconds()),
            "max_lifetime_seconds": int(sessions.max_lifetime.total_seconds()),
        },
        status_code=201,
    )


async def close_session(request: Request) -> Response:
    _sessions(request).close(parsing.bearer_token(request))
    return Response(status_code=204)


async def list_entries(request: Request) -> Response:
    start, end = parsing.date_range(request.query_params)
    metas = await run_in_threadpool(_journal(request).list_entries, start=start, end=end)
    return JSONResponse({"entries": [_meta_json(meta) for meta in metas]})


async def create_entry(request: Request) -> Response:
    new = parsing.new_entry(await parsing.json_object(request))
    meta = await run_in_threadpool(_journal(request).create_entry, new)
    return JSONResponse(
        _meta_json(meta), status_code=201, headers={"Location": f"/entries/{meta.id}"}
    )


async def read_entry(request: Request) -> Response:
    keyring = _keyring(request)
    entry_id = request.path_params["entry_id"]
    meta, content = await run_in_threadpool(_journal(request).read_entry, entry_id, keyring)
    return JSONResponse(_entry_json(meta, content))


async def update_entry(request: Request) -> Response:
    _keyring(request)  # editing requires an unlocked session
    content = parsing.content(await parsing.json_object(request))
    entry_id = request.path_params["entry_id"]
    meta = await run_in_threadpool(_journal(request).update_entry, entry_id, content)
    return JSONResponse(_meta_json(meta))


async def delete_entry(request: Request) -> Response:
    _keyring(request)  # deleting requires an unlocked session
    await run_in_threadpool(_journal(request).delete_entry, request.path_params["entry_id"])
    return Response(status_code=204)


ROUTES = [
    Route("/health", health, methods=["GET"]),
    Route("/session", open_session, methods=["POST"]),
    Route("/session", close_session, methods=["DELETE"]),
    Route("/entries", list_entries, methods=["GET"]),
    Route("/entries", create_entry, methods=["POST"]),
    Route("/entries/{entry_id:uuid}", read_entry, methods=["GET"]),
    Route("/entries/{entry_id:uuid}", update_entry, methods=["PUT"]),
    Route("/entries/{entry_id:uuid}", delete_entry, methods=["DELETE"]),
]
