"""Logging to a rotating file in the state dir, plus stderr (journald when containerised).

Never log entry bodies, tags, passwords or session tokens. Ids and dates are fine: they are
stored unencrypted anyway.
"""

import logging
from logging.handlers import RotatingFileHandler

from journal_service.paths import AppPaths
from journal_service.settings import Settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(settings: Settings, paths: AppPaths, *, console: bool = True) -> None:
    """Log to the file always; to stderr too if enabled and `console` (off for admin commands,
    whose terminal output is meant for the person running them)."""
    paths.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            paths.log_file,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    ]
    if console and settings.log_to_stderr:
        handlers.append(logging.StreamHandler())

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(settings.log_level)
    # Alembic narrates every startup at INFO; db.migrate logs the revisions it applies instead.
    logging.getLogger("alembic").setLevel(max(logging.WARNING, root.level))
    logging.captureWarnings(True)
