"""Unlock sessions: decrypted private keys held in memory for a limited time.

`POST /session` with the password returns a random bearer token. The unlocked keys stay in
this process only, until the session has been idle for `idle_timeout`, reaches
`max_lifetime`, is closed with `DELETE /session`, or the service restarts. Sessions are
looked up by a SHA-256 digest of the token; the token itself is not stored.
"""

import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from journal_service.errors import Unauthorized
from journal_service.journal import Keyring


@dataclass(slots=True)
class _Session:
    keyring: Keyring
    opened: float
    last_used: float


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class SessionStore:
    def __init__(
        self,
        *,
        idle_timeout: timedelta,
        max_lifetime: timedelta,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.idle_timeout = idle_timeout
        self.max_lifetime = max_lifetime
        self._clock = clock
        self._sessions: dict[bytes, _Session] = {}
        self._lock = threading.Lock()

    def open(self, keyring: Keyring) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._purge(now)
            self._sessions[_digest(token)] = _Session(keyring=keyring, opened=now, last_used=now)
        return token

    def keyring(self, token: str) -> Keyring:
        now = self._clock()
        with self._lock:
            self._purge(now)
            session = self._sessions.get(_digest(token))
            if session is None:
                raise Unauthorized("session expired or unknown; unlock again")
            session.last_used = now
            return session.keyring

    def close(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(_digest(token), None)

    def close_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return len(self._sessions)

    def _purge(self, now: float) -> None:
        idle, lifetime = self.idle_timeout.total_seconds(), self.max_lifetime.total_seconds()
        expired = [
            digest
            for digest, s in self._sessions.items()
            if now - s.last_used > idle or now - s.opened > lifetime
        ]
        for digest in expired:
            del self._sessions[digest]
