from datetime import timedelta
from pathlib import Path

import pytest

from journal_service.errors import SettingsError
from journal_service.paths import resolve_paths
from journal_service.settings import load_settings


def test_xdg_defaults_follow_home(monkeypatch, tmp_path):
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    paths = resolve_paths()
    assert paths.config_dir == tmp_path / ".config" / "journal-service"
    assert paths.data_dir == tmp_path / ".local" / "share" / "journal-service"
    assert paths.state_dir == tmp_path / ".local" / "state" / "journal-service"
    assert paths.cache_dir == tmp_path / ".cache" / "journal-service"
    assert paths.socket_file == Path("/run/user/1000/journal-service/api.sock")
    assert paths.log_file == paths.state_dir / "logs" / "journal_service.log"


def test_relative_xdg_values_are_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "relative/data")  # invalid per the spec
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    paths = resolve_paths()
    assert paths.data_dir == tmp_path / ".local" / "share" / "journal-service"
    assert paths.runtime_dir == paths.state_dir / "run"


def test_ensure_creates_private_dirs(xdg):
    for directory in (xdg.config_dir, xdg.data_dir, xdg.state_dir, xdg.cache_dir, xdg.log_dir):
        assert directory.is_dir()
        assert directory.stat().st_mode & 0o777 == 0o700


def test_settings_defaults(xdg):
    settings = load_settings(xdg)
    assert settings.database_url == f"sqlite:///{xdg.database_file}"
    assert settings.socket_path == xdg.socket_file
    assert settings.session_idle_timeout == timedelta(minutes=15)


def test_settings_file_overrides(xdg):
    xdg.config_file.write_text('[session]\nidle_timeout_minutes = 5\n[logging]\nlevel = "debug"\n')
    settings = load_settings(xdg)
    assert settings.session_idle_timeout == timedelta(minutes=5)
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    "content",
    [
        "[sesion]\nidle_timeout_minutes = 5\n",  # typo in section
        "[session]\nidle_timeout = 5\n",  # unknown key
        "[session]\nidle_timeout_minutes = true\n",  # bool is not an int here
        "[session]\nidle_timeout_minutes = 0\n",
        '[server]\nsocket = "relative.sock"\n',
        "not toml at all [",
    ],
)
def test_settings_file_rejects_mistakes(xdg, content):
    xdg.config_file.write_text(content)
    with pytest.raises(SettingsError):
        load_settings(xdg)
