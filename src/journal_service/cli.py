"""Admin command line. Runs the same on the host (`uv run journal-service ...`) and in the
container (`podman exec -it journal-service journal-service ...`): both resolve the same XDG
directories.
"""

import argparse
import getpass
import http.client
import logging
import os
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from journal_service import __version__
from journal_service.errors import JournalError, SettingsError, ValidationFailed
from journal_service.identity import IDENTITY
from journal_service.journal import Journal
from journal_service.log_setup import configure_logging
from journal_service.paths import AppPaths, resolve_paths
from journal_service.settings import Settings, load_settings

log = logging.getLogger(__name__)

_SUN_PATH_MAX = 107  # bytes; AF_UNIX path limit on Linux, excluding the trailing NUL


@dataclass(frozen=True, slots=True)
class _Context:
    paths: AppPaths
    settings: Settings


def _context(*, console_logging: bool = False) -> _Context:
    paths = resolve_paths()
    paths.ensure()
    settings = load_settings(paths)
    configure_logging(settings, paths, console=console_logging)
    return _Context(paths=paths, settings=settings)


def _open_journal(ctx: _Context) -> Journal:
    from journal_service.db.engine import create_db_engine, make_session_factory
    from journal_service.db.migrate import upgrade_to_head

    engine = create_db_engine(ctx.settings.database_url)
    upgrade_to_head(engine)
    return Journal(make_session_factory(engine))


def _read_new_password(*, from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Repeat new password: ") != password:
            raise ValidationFailed("passwords do not match")
    return password


# -- commands -----------------------------------------------------------------------------


def _cmd_serve(_args: argparse.Namespace) -> int:
    import uvicorn

    from journal_service.api.app import create_app
    from journal_service.api.sessions import SessionStore

    ctx = _context(console_logging=True)
    journal = _open_journal(ctx)
    sessions = SessionStore(
        idle_timeout=ctx.settings.session_idle_timeout,
        max_lifetime=ctx.settings.session_max_lifetime,
    )
    app = create_app(journal, sessions, max_request_bytes=ctx.settings.max_request_bytes)

    socket_path = ctx.settings.socket_path
    sock = _bind_unix_socket(socket_path)
    log.info("journal-service %s listening on unix:%s", __version__, socket_path)
    if not journal.is_initialized():
        log.warning("no keypair yet: run `journal-service init` to set the password")

    # uvicorn shuts down gracefully on SIGTERM and then re-raises the signal. Turning that
    # second delivery into SystemExit lets the cleanup below run (`podman stop` sends SIGTERM).
    signal.signal(signal.SIGTERM, _exit_on_signal)
    try:
        uvicorn.run(
            app,
            fd=sock.fileno(),
            log_config=None,  # keep our handlers (file + stderr)
            server_header=False,
            timeout_graceful_shutdown=10,
        )
    finally:
        sessions.close_all()
        sock.close()
        socket_path.unlink(missing_ok=True)
        log.info("stopped")
    return 0


def _exit_on_signal(_signum: int, _frame: object) -> None:
    raise SystemExit(0)


def _cmd_init(args: argparse.Namespace) -> int:
    ctx = _context()
    journal = _open_journal(ctx)
    if journal.is_initialized():
        print("Already initialized. Use `journal-service passwd` to change the password.")
        return 1
    recipient = journal.initialize(_read_new_password(from_stdin=args.password_stdin))
    print(f"Initialized. New entries are encrypted to {recipient}")
    print("There is no password recovery: without the password the entries cannot be read.")
    return 0


def _cmd_passwd(_args: argparse.Namespace) -> int:
    ctx = _context()
    journal = _open_journal(ctx)
    old = getpass.getpass("Current password: ")
    journal.unlock(old)  # fail fast on a wrong password, before asking for the new one
    journal.change_password(old, _read_new_password(from_stdin=False))
    print("Password changed. Entries were not touched; unlock with the new password from now on.")
    return 0


def _cmd_migrate(_args: argparse.Namespace) -> int:
    from journal_service.db.engine import create_db_engine
    from journal_service.db.migrate import current_revision, upgrade_to_head

    ctx = _context()
    engine = create_db_engine(ctx.settings.database_url)
    upgrade_to_head(engine)
    print(f"database at revision {current_revision(engine)}")
    return 0


def _cmd_paths(_args: argparse.Namespace) -> int:
    paths = resolve_paths()
    settings = load_settings(paths)
    rows = [
        ("config dir", paths.config_dir),
        ("data dir", paths.data_dir),
        ("state dir", paths.state_dir),
        ("cache dir", paths.cache_dir),
        ("runtime dir", paths.runtime_dir),
        ("config file", paths.config_file),
        ("database", settings.database_url),
        ("log file", paths.log_file),
        ("socket", settings.socket_path),
    ]
    for label, value in rows:
        print(f"{label:<12} {value}")
    return 0


def _cmd_healthcheck(args: argparse.Namespace) -> int:
    settings = load_settings(resolve_paths())
    try:
        status, body = _unix_get(settings.socket_path, "/health", timeout=args.timeout)
    except OSError as exc:
        print(f"unhealthy: {settings.socket_path}: {exc}", file=sys.stderr)
        return 1
    if status != 200:
        print(f"unhealthy: HTTP {status}: {body}", file=sys.stderr)
        return 1
    print(body)
    return 0


# -- Unix socket helpers ------------------------------------------------------------------


def _bind_unix_socket(path: Path) -> socket.socket:
    """Bind a 0600 socket inside a 0700 directory, replacing a stale socket file."""
    if len(os.fsencode(path)) > _SUN_PATH_MAX:
        raise SettingsError(f"socket path is too long for AF_UNIX: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_socket():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(os.fspath(path))
        except ConnectionRefusedError:
            path.unlink()  # left behind by a process that is gone
        else:
            raise SettingsError(f"another journal-service is already listening on {path}")
        finally:
            probe.close()
    elif path.exists():
        raise SettingsError(f"{path} exists and is not a socket")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(os.fspath(path))
    os.chmod(path, 0o600)
    return sock


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(os.fspath(self._path))
        self.sock = sock


def _unix_get(path: Path, url: str, *, timeout: float) -> tuple[int, str]:
    connection = _UnixHTTPConnection(path, timeout)
    try:
        connection.request("GET", url)
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


# -- entry point --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=IDENTITY.app_name, description=IDENTITY.description)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("serve", help="run the API on the Unix socket").set_defaults(
        func=_cmd_serve
    )

    init = commands.add_parser("init", help="create the keypair and set the password")
    init.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin (scripts)"
    )
    init.set_defaults(func=_cmd_init)

    commands.add_parser("passwd", help="change the password").set_defaults(func=_cmd_passwd)
    commands.add_parser("migrate", help="apply database migrations").set_defaults(func=_cmd_migrate)
    commands.add_parser("paths", help="show resolved directories and files").set_defaults(
        func=_cmd_paths
    )

    health = commands.add_parser("healthcheck", help="exit 0 if the API answers")
    health.add_argument("--timeout", type=float, default=5.0)
    health.set_defaults(func=_cmd_healthcheck)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.umask(0o077)  # everything we create is private: database, logs, socket
    try:
        return args.func(args)
    except JournalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except EOFError:  # no terminal to prompt on, or input ended early
        print("error: aborted: no password entered", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
