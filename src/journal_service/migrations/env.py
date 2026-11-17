"""Alembic environment.

Used both by the service (which passes an open connection via `config.attributes`) and by
developers running `uv run alembic ...` (which builds an engine from journal-service's
settings, i.e. the database in the XDG data dir unless config.toml says otherwise).
"""

from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import Connection

from journal_service.db.engine import create_db_engine
from journal_service.db.models import Base
from journal_service.db.types import UTCDateTime
from journal_service.paths import resolve_paths
from journal_service.settings import load_settings

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_alembic_option("sqlalchemy.url")
    return str(url) if url else load_settings(resolve_paths()).database_url


def _render_item(type_: str, obj: Any, _ctx: AutogenContext) -> str | Literal[False]:
    # Autogenerate renders app types as their plain SQLAlchemy equivalent, so migration
    # files stay self-contained snapshots that never import application code.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


_COMMON: dict[str, Any] = {
    "target_metadata": target_metadata,
    # SQLite cannot ALTER most things; batch mode recreates tables instead. A batch migration
    # that rebuilds a table referenced by a foreign key needs `PRAGMA foreign_keys=OFF`
    # around it on SQLite.
    "render_as_batch": True,
    "render_item": _render_item,
}


def _run(connection: Connection) -> None:
    context.configure(connection=connection, compare_type=True, **_COMMON)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), literal_binds=True, **_COMMON)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_db_engine(_database_url())
    try:
        with engine.connect() as connection:
            _run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
