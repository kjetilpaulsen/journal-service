"""Programmatic Alembic: the migrations ship inside the package, so the container can run them."""

import logging

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

log = logging.getLogger(__name__)

SCRIPT_LOCATION = "journal_service:migrations"


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    return config


def upgrade_to_head(engine: Engine) -> None:
    before = current_revision(engine)
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    after = current_revision(engine)
    if after != before:
        log.info("database migrated from revision %s to %s", before or "(empty)", after)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()
