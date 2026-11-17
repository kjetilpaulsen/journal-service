"""XDG base directories.

Each XDG variable is honoured only when it holds an absolute path (as the spec requires);
otherwise the spec's default under $HOME applies. Inside the container the XDG_* variables
point at bind mounts, so the same code resolves to the same directories on the host:

    config   $XDG_CONFIG_HOME/journal-service   config.toml (optional)
    data     $XDG_DATA_HOME/journal-service     journal.db (SQLite)
    state    $XDG_STATE_HOME/journal-service    logs/
    cache    $XDG_CACHE_HOME/journal-service    reserved; must never hold decrypted text
    runtime  $XDG_RUNTIME_DIR/journal-service   api.sock (Unix socket, gone after logout)
"""

import os
from dataclasses import dataclass
from pathlib import Path

from journal_service.identity import IDENTITY

DIR_MODE = 0o700


def _xdg_home(env_var: str, default: Path) -> Path:
    value = os.environ.get(env_var, "")
    if value and os.path.isabs(value):
        return Path(value)
    return default


@dataclass(frozen=True, slots=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    state_dir: Path
    cache_dir: Path
    runtime_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "journal.db"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "journal_service.log"

    @property
    def socket_file(self) -> Path:
        return self.runtime_dir / "api.sock"

    def ensure(self) -> None:
        """Create missing directories as 0700. Existing directories are left as they are."""
        for directory in (
            self.config_dir,
            self.data_dir,
            self.state_dir,
            self.log_dir,
            self.cache_dir,
            self.runtime_dir,
        ):
            directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)


def resolve_paths(app_name: str = IDENTITY.app_name) -> AppPaths:
    home = Path.home()

    data_home = _xdg_home("XDG_DATA_HOME", home / ".local" / "share")
    state_home = _xdg_home("XDG_STATE_HOME", home / ".local" / "state")
    cache_home = _xdg_home("XDG_CACHE_HOME", home / ".cache")
    config_home = _xdg_home("XDG_CONFIG_HOME", home / ".config")

    state_dir = state_home / app_name

    runtime_home = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime_home and os.path.isabs(runtime_home):
        runtime_dir = Path(runtime_home) / app_name
    else:
        # The spec defines no default for XDG_RUNTIME_DIR; fall back to a private state subdir.
        runtime_dir = state_dir / "run"

    return AppPaths(
        config_dir=config_home / app_name,
        data_dir=data_home / app_name,
        state_dir=state_dir,
        cache_dir=cache_home / app_name,
        runtime_dir=runtime_dir,
    )
