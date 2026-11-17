"""Request parsing and validation. Unknown fields are rejected to catch client bugs early."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from starlette.requests import Request

from journal_service.errors import PayloadTooLarge, Unauthorized, ValidationFailed
from journal_service.journal import EntryContent, NewEntry, make_content


async def json_object(request: Request) -> dict[str, Any]:
    limit: int = request.app.state.max_request_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise PayloadTooLarge(f"request body is larger than {limit} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise PayloadTooLarge(f"request body is larger than {limit} bytes")
    try:
        data = json.loads(body)
    except ValueError as exc:  # includes JSONDecodeError and UnicodeDecodeError
        raise ValidationFailed("request body must be a JSON object") from exc
    if not isinstance(data, dict):
        raise ValidationFailed("request body must be a JSON object")
    return data


def bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("missing session token; unlock with POST /session first")
    return token.strip()


def password(data: Mapping[str, Any]) -> str:
    _only(data, {"password"})
    value = data.get("password")
    if not isinstance(value, str) or not value:
        raise ValidationFailed("password must be a non-empty string")
    return value


def new_entry(data: Mapping[str, Any]) -> NewEntry:
    _only(data, {"body", "tags", "written_at", "entry_date"})
    written_at = _timestamp(data["written_at"]) if data.get("written_at") is not None else None
    entry_date = (
        _date(data["entry_date"], "entry_date") if data.get("entry_date") is not None else None
    )
    if written_at is not None and entry_date is not None:
        raise ValidationFailed("give either written_at or entry_date, not both")
    return NewEntry(content=_content(data), written_at=written_at, entry_date=entry_date)


def content(data: Mapping[str, Any]) -> EntryContent:
    _only(data, {"body", "tags"})
    return _content(data)


def list_query(query: Mapping[str, str]) -> tuple[date | None, date | None, bool]:
    """?from=YYYY-MM-DD&to=YYYY-MM-DD&content=true -> (start, end, include content)."""
    _only(query, {"from", "to", "content"})
    start = _date(query["from"], "from") if "from" in query else None
    end = _date(query["to"], "to") if "to" in query else None
    if start is not None and end is not None and start > end:
        raise ValidationFailed("'from' must not be after 'to'")
    content = query.get("content", "false").lower()
    if content not in ("true", "false"):
        raise ValidationFailed("content must be true or false")
    return start, end, content == "true"


def password_change(data: Mapping[str, Any]) -> tuple[str, str]:
    _only(data, {"current_password", "new_password"})
    current, new = data.get("current_password"), data.get("new_password")
    if not isinstance(current, str) or not current:
        raise ValidationFailed("current_password must be a non-empty string")
    if not isinstance(new, str):
        raise ValidationFailed("new_password must be a string")
    return current, new


def _only(data: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(f"unknown field(s): {', '.join(unknown)}")


def _content(data: Mapping[str, Any]) -> EntryContent:
    body = data.get("body")
    if not isinstance(body, str):
        raise ValidationFailed("body must be a string")
    tags = data.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValidationFailed("tags must be a list of strings")
    return make_content(body, tags)


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValidationFailed("written_at must be a string")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationFailed("written_at must be an ISO 8601 timestamp") from exc
    if moment.utcoffset() is None:
        raise ValidationFailed(
            "written_at must include a UTC offset, e.g. 2026-11-17T19:46:00+01:00"
        )
    return moment


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValidationFailed(f"{field} must be a string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationFailed(f"{field} must be a date (YYYY-MM-DD)") from exc
