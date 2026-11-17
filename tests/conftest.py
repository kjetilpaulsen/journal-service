from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine

from journal_service.crypto import keys
from journal_service.db.engine import create_db_engine, make_session_factory
from journal_service.db.migrate import upgrade_to_head
from journal_service.db.models import Key
from journal_service.journal import Journal
from journal_service.paths import AppPaths, resolve_paths
from journal_service.settings import load_settings

PASSWORD = "correct horse battery staple"
OSLO_WINTER = timezone(timedelta(hours=1))
# 00:30 local on 18 Nov is still 17 Nov in UTC: entry_date must follow local time.
JUST_AFTER_MIDNIGHT = datetime(2026, 11, 18, 0, 30, tzinfo=OSLO_WINTER)


@pytest.fixture
def xdg(tmp_path, monkeypatch) -> AppPaths:
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    paths = resolve_paths()
    paths.ensure()
    return paths


@pytest.fixture
def engine(xdg: AppPaths) -> Iterator[Engine]:
    engine = create_db_engine(load_settings(xdg).database_url)
    upgrade_to_head(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def journal(engine: Engine) -> Journal:
    return Journal(make_session_factory(engine), local_now=lambda: JUST_AFTER_MIDNIGHT)


@pytest.fixture(scope="session")
def keypair() -> keys.Keypair:
    # scrypt makes every wrap/unwrap take a second or two, so generate one keypair per run.
    return keys.generate_keypair(PASSWORD)


@pytest.fixture
def initialized(journal: Journal, engine: Engine, keypair: keys.Keypair) -> Journal:
    with make_session_factory(engine).begin() as session:
        session.add(
            Key(
                recipient=keypair.recipient,
                wrapped_identity=keypair.wrapped_identity,
                created_at=datetime.now(UTC),
            )
        )
    return journal
