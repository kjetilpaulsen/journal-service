from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from journal_service.api.app import create_app
from journal_service.api.sessions import SessionStore
from journal_service.errors import Unauthorized
from journal_service.journal import Journal

from .conftest import PASSWORD


def _client(journal: Journal, **store_kwargs) -> TestClient:
    sessions = SessionStore(
        idle_timeout=store_kwargs.pop("idle", timedelta(minutes=15)),
        max_lifetime=store_kwargs.pop("lifetime", timedelta(hours=8)),
        **store_kwargs,
    )
    return TestClient(create_app(journal, sessions, max_request_bytes=64 * 1024))


@pytest.fixture
def client(initialized: Journal) -> TestClient:
    return _client(initialized)


def _unlock(client: TestClient) -> dict[str, str]:
    response = client.post("/session", json={"password": PASSWORD})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_health_reports_initialization(journal: Journal):
    client = _client(journal)
    assert client.get("/health").json()["initialized"] is False
    assert client.post("/entries", json={"body": "x"}).status_code == 409
    journal.initialize(PASSWORD)
    assert client.get("/health").json()["initialized"] is True


def test_full_flow(client: TestClient):
    created = client.post("/entries", json={"body": "hello", "tags": ["first"]})
    assert created.status_code == 201
    entry = created.json()
    assert created.headers["location"] == f"/entries/{entry['id']}"
    assert entry["entry_date"] == "2026-11-18"
    assert entry["written_at"] == "2026-11-18T00:30:00+01:00"
    assert "body" not in entry

    listed = client.get("/entries").json()["entries"]
    assert [e["id"] for e in listed] == [entry["id"]]
    assert "body" not in listed[0]

    url = f"/entries/{entry['id']}"
    assert client.get(url).status_code == 401  # no session

    headers = _unlock(client)
    body = client.get(url, headers=headers).json()
    assert (body["body"], body["tags"]) == ("hello", ["first"])

    assert client.put(url, json={"body": "edited"}, headers=headers).status_code == 200
    assert client.get(url, headers=headers).json()["body"] == "edited"

    assert client.delete("/session", headers=headers).status_code == 204
    assert client.get(url, headers=headers).status_code == 401  # locked again
    assert client.delete(url).status_code == 401  # deleting needs a session too

    headers = _unlock(client)
    assert client.delete(url, headers=headers).status_code == 204
    assert client.get(url, headers=headers).status_code == 404


def test_wrong_password(client: TestClient):
    response = client.post("/session", json={"password": "definitely wrong"})
    assert response.status_code == 401
    assert response.json() == {"error": "wrong password"}


def test_date_only_entry_and_range_filter(client: TestClient):
    client.post("/entries", json={"body": "old", "entry_date": "2019-03-04"})
    client.post("/entries", json={"body": "new", "written_at": "2026-11-17T19:46:00+01:00"})
    entries = client.get("/entries", params={"to": "2020-01-01"}).json()["entries"]
    assert [(e["entry_date"], e["written_at"]) for e in entries] == [("2019-03-04", None)]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"body": ""},
        {"body": 42},
        {"body": "x", "tags": "not-a-list"},
        {"body": "x", "mood": "great"},  # unknown field
        {"body": "x", "written_at": "2026-11-17T19:46:00"},  # no offset: ambiguous
        {"body": "x", "written_at": "2026-11-17T19:46:00Z", "entry_date": "2026-11-17"},
        {"body": "x", "entry_date": "17.11.2026"},
    ],
)
def test_invalid_entries_are_rejected(client: TestClient, payload):
    response = client.post("/entries", json=payload)
    assert response.status_code == 422
    assert "error" in response.json()


def test_errors_are_json(client: TestClient):
    assert client.post("/entries", content=b"{not json").status_code == 422
    assert client.get("/entries", params={"since": "2026-01-01"}).status_code == 422
    not_found = client.get("/nope")
    assert not_found.status_code == 404 and "error" in not_found.json()
    assert client.patch("/entries").status_code == 405
    assert client.get("/entries/not-a-uuid").status_code == 404


def test_request_size_is_limited(client: TestClient):
    response = client.post("/entries", json={"body": "x" * (65 * 1024)})
    assert response.status_code == 413
    assert "error" in response.json()

    def chunked():  # no Content-Length: the limit must hold while streaming too
        for _ in range(70):
            yield b"x" * 1024

    assert client.post("/entries", content=chunked()).status_code == 413


def test_sessions_expire_when_idle(initialized: Journal):
    now = [0.0]
    client = _client(initialized, idle=timedelta(minutes=15), clock=lambda: now[0])
    entry_id = client.post("/entries", json={"body": "x"}).json()["id"]
    headers = _unlock(client)
    now[0] += 14 * 60
    assert client.get(f"/entries/{entry_id}", headers=headers).status_code == 200
    now[0] += 14 * 60  # still alive: the previous request reset the idle timer
    assert client.get(f"/entries/{entry_id}", headers=headers).status_code == 200
    now[0] += 16 * 60
    assert client.get(f"/entries/{entry_id}", headers=headers).status_code == 401


def test_sessions_have_a_maximum_lifetime():
    now = [0.0]
    store = SessionStore(
        idle_timeout=timedelta(minutes=15), max_lifetime=timedelta(hours=1), clock=lambda: now[0]
    )
    token = store.open({})
    for _ in range(5):  # keep it busy, but past the max lifetime it must expire anyway
        now[0] += 14 * 60
        try:
            store.keyring(token)
        except Unauthorized:
            break
    else:
        pytest.fail("session outlived max_lifetime")
    assert now[0] > 3600


def test_list_with_content_needs_a_session(client: TestClient):
    client.post("/entries", json={"body": "searchable text", "tags": ["work"]})
    client.post("/entries", json={"body": "older", "entry_date": "2019-03-04"})
    assert client.get("/entries", params={"content": "true"}).status_code == 401

    headers = _unlock(client)
    entries = client.get("/entries", params={"content": "true"}, headers=headers).json()["entries"]
    assert [(e["entry_date"], e["body"], e["tags"]) for e in entries] == [
        ("2019-03-04", "older", []),
        ("2026-11-18", "searchable text", ["work"]),
    ]
    filtered = client.get(
        "/entries", params={"content": "true", "from": "2020-01-01"}, headers=headers
    ).json()["entries"]
    assert [e["body"] for e in filtered] == ["searchable text"]
    assert client.get("/entries", params={"content": "maybe"}).status_code == 422


def test_change_password_over_the_api(client: TestClient):
    def change(current: str, new: str | None = None) -> int:
        body = {"current_password": current} | ({"new_password": new} if new is not None else {})
        return client.post("/password", json=body).status_code

    new = "a brand new passphrase"
    assert change("wrong one", new) == 401
    assert change(PASSWORD, "short") == 422
    assert change(PASSWORD) == 422
    assert change(PASSWORD, new) == 204
    assert client.post("/session", json={"password": PASSWORD}).status_code == 401
    assert client.post("/session", json={"password": new}).status_code == 201
