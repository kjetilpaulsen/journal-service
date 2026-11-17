"""Settings from the optional TOML file at $XDG_CONFIG_HOME/journal-service/config.toml.

Every key is optional. Unknown sections or keys are rejected so typos do not pass silently.

    [database]
    url = "sqlite:////path/to/journal.db"    # default: journal.db in the data dir

    [server]
    socket = "/path/to/api.sock"             # default: api.sock in the runtime dir

    [session]
    idle_timeout_minutes = 15                # lock after this long without requests
    max_lifetime_minutes = 480               # lock after this long regardless

    [logging]
    level = "INFO"
    stderr = true                            # also log to stderr (journald in the container)
    max_bytes = 5242880                      # rotate the log file at this size
    backup_count = 5

    [limits]
    max_request_bytes = 2097152              # largest accepted request body
"""

import tomllib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from journal_service.errors import SettingsError
from journal_service.paths import AppPaths

_SCHEMA: dict[str, dict[str, type]] = {
    "database": {"url": str},
    "server": {"socket": str},
    "session": {"idle_timeout_minutes": int, "max_lifetime_minutes": int},
    "logging": {"level": str, "stderr": bool, "max_bytes": int, "backup_count": int},
    "limits": {"max_request_bytes": int},
}

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    socket_path: Path
    session_idle_timeout: timedelta
    session_max_lifetime: timedelta
    log_level: str
    log_to_stderr: bool
    log_max_bytes: int
    log_backup_count: int
    max_request_bytes: int


def _read_file(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path}: {exc}") from exc

    for section, values in raw.items():
        keys = _SCHEMA.get(section)
        if keys is None or not isinstance(values, dict):
            raise SettingsError(f"{path}: unknown section [{section}]")
        for key, value in values.items():
            expected = keys.get(key)
            if expected is None:
                raise SettingsError(f"{path}: unknown key '{key}' in [{section}]")
            # Exact type check: bool is a subclass of int, but `true` is not a size.
            if type(value) is not expected:
                raise SettingsError(f"{path}: {section}.{key} must be {expected.__name__}")
    return raw


def _positive(value: int, name: str) -> int:
    if value <= 0:
        raise SettingsError(f"{name} must be positive")
    return value


def load_settings(paths: AppPaths) -> Settings:
    raw = _read_file(paths.config_file)

    def get[T](section: str, key: str, default: T) -> T:
        return raw.get(section, {}).get(key, default)  # type: ignore[return-value]

    level = get("logging", "level", "INFO").upper()
    if level not in _LOG_LEVELS:
        raise SettingsError(f"logging.level must be one of {', '.join(_LOG_LEVELS)}")

    socket = Path(get("server", "socket", str(paths.socket_file)))
    if not socket.is_absolute():
        raise SettingsError("server.socket must be an absolute path")

    idle = _positive(get("session", "idle_timeout_minutes", 15), "session.idle_timeout_minutes")
    lifetime = _positive(
        get("session", "max_lifetime_minutes", 480), "session.max_lifetime_minutes"
    )

    return Settings(
        database_url=get("database", "url", f"sqlite:///{paths.database_file}"),
        socket_path=socket,
        session_idle_timeout=timedelta(minutes=idle),
        session_max_lifetime=timedelta(minutes=max(idle, lifetime)),
        log_level=level,
        log_to_stderr=get("logging", "stderr", True),
        log_max_bytes=_positive(get("logging", "max_bytes", 5 * 1024 * 1024), "logging.max_bytes"),
        log_backup_count=max(0, get("logging", "backup_count", 5)),
        max_request_bytes=_positive(
            get("limits", "max_request_bytes", 2 * 1024 * 1024), "limits.max_request_bytes"
        ),
    )
