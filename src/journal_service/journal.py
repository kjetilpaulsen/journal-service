"""Journal domain logic: the one place where the database and the crypto meet.

Writing and listing never touch a secret: new entries are encrypted to the public key.
Reading needs a keyring, which only `unlock(password)` produces.
"""

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from journal_service.crypto import envelope, keys
from journal_service.db.models import Entry, Key
from journal_service.errors import (
    AlreadyInitialized,
    EntryNotFound,
    NotInitialized,
    Unauthorized,
    ValidationFailed,
)

log = logging.getLogger(__name__)

MAX_TAGS = 32
MAX_TAG_LENGTH = 64

type Keyring = Mapping[int, keys.Identity]  # key id -> unlocked identity


@dataclass(frozen=True, slots=True)
class EntryContent:
    """The secret part of an entry."""

    body: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntryMeta:
    """The plaintext part of an entry: what you can see without the password."""

    id: UUID
    entry_date: date
    written_at: datetime | None  # in the writer's own UTC offset; None for date-only entries
    created_at: datetime
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewEntry:
    content: EntryContent
    written_at: datetime | None = None  # timezone-aware; defaults to "now" in local time
    entry_date: date | None = None  # date-only entries (e.g. imports); excludes written_at


def make_content(body: str, tags: Iterable[str] = ()) -> EntryContent:
    """Validate and normalise entry content: non-empty body; trimmed, de-duplicated tags."""
    if not body.strip():
        raise ValidationFailed("body must not be empty")
    normalised: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = raw.strip()
        if not tag:
            raise ValidationFailed("tags must not be empty")
        if len(tag) > MAX_TAG_LENGTH:
            raise ValidationFailed(f"tags must be at most {MAX_TAG_LENGTH} characters")
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            normalised.append(tag)
    if len(normalised) > MAX_TAGS:
        raise ValidationFailed(f"at most {MAX_TAGS} tags per entry")
    return EntryContent(body=body, tags=tuple(normalised))


def _offset_minutes(moment: datetime) -> int:
    offset = moment.utcoffset()
    if offset is None:
        raise ValidationFailed("written_at must include a UTC offset")
    seconds = int(offset.total_seconds())
    if seconds % 60:
        raise ValidationFailed("the UTC offset of written_at must be whole minutes")
    return seconds // 60


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _local_now() -> datetime:
    # In the container, Podman's `Timezone=local` makes this the host's local time.
    return datetime.now().astimezone().replace(microsecond=0)


class Journal:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        utc_now: Callable[[], datetime] = _utc_now,
        local_now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._sessions = session_factory
        self._utc_now = utc_now
        self._local_now = local_now

    # -- keys -------------------------------------------------------------------------

    def is_initialized(self) -> bool:
        with self._sessions() as session:
            return bool(session.scalar(select(func.count()).select_from(Key)))

    def initialize(self, password: str) -> str:
        """Create the keypair, protected by `password`. Returns the public recipient."""
        if self.is_initialized():
            raise AlreadyInitialized("already initialized; use `journal-service passwd`")
        keypair = keys.generate_keypair(password)
        with self._sessions.begin() as session:
            if session.scalar(select(func.count()).select_from(Key)):
                raise AlreadyInitialized("already initialized; use `journal-service passwd`")
            session.add(
                Key(
                    recipient=keypair.recipient,
                    wrapped_identity=keypair.wrapped_identity,
                    created_at=self._utc_now(),
                )
            )
        log.info("keypair created, recipient %s", keypair.recipient)
        return keypair.recipient

    def change_password(self, old_password: str, new_password: str) -> None:
        """Re-wrap every private key with the new password. Entries are not touched."""
        keys.check_password_policy(new_password)
        with self._sessions.begin() as session:
            rows = session.scalars(select(Key)).all()
            if not rows:
                raise NotInitialized("no keypair yet; run `journal-service init`")
            for row in rows:
                identity = keys.unwrap_identity(row.wrapped_identity, old_password)
                row.wrapped_identity = keys.wrap_identity(identity, new_password)
        log.info("password changed for %d key(s)", len(rows))

    def unlock(self, password: str) -> Keyring:
        with self._sessions() as session:
            rows = session.execute(select(Key.id, Key.wrapped_identity)).all()
        if not rows:
            raise NotInitialized("no keypair yet; run `journal-service init`")
        return {key_id: keys.unwrap_identity(blob, password) for key_id, blob in rows}

    # -- entries ----------------------------------------------------------------------

    def create_entry(self, new: NewEntry) -> EntryMeta:
        if new.entry_date is not None and new.written_at is not None:
            raise ValidationFailed("give either written_at or entry_date, not both")

        written_at: datetime | None
        offset: int | None
        if new.entry_date is not None:
            entry_date, written_at, offset = new.entry_date, None, None
        else:
            local = (new.written_at or self._local_now()).replace(microsecond=0)
            offset = _offset_minutes(local)
            entry_date, written_at = local.date(), local.astimezone(UTC)

        entry_id = uuid7()
        payload = envelope.Payload(entry_id, entry_date, new.content.body, new.content.tags)
        with self._sessions.begin() as session:
            key = self._active_key(session)
            entry = Entry(
                id=entry_id,
                entry_date=entry_date,
                written_at=written_at,
                utc_offset_minutes=offset,
                key_id=key.id,
                ciphertext=envelope.seal(payload, key.recipient),
                created_at=self._utc_now(),
            )
            session.add(entry)
            meta = _meta(entry)
        log.info("entry %s created for %s", entry_id, entry_date)
        return meta

    def list_entries(
        self, *, start: date | None = None, end: date | None = None
    ) -> list[EntryMeta]:
        query = select(
            Entry.id,
            Entry.entry_date,
            Entry.written_at,
            Entry.utc_offset_minutes,
            Entry.created_at,
            Entry.updated_at,
        )
        if start is not None:
            query = query.where(Entry.entry_date >= start)
        if end is not None:
            query = query.where(Entry.entry_date <= end)
        # Explicit NULLS FIRST: SQLite and PostgreSQL disagree on the default.
        query = query.order_by(Entry.entry_date, Entry.written_at.asc().nulls_first(), Entry.id)
        with self._sessions() as session:
            return [
                EntryMeta(
                    id=row.id,
                    entry_date=row.entry_date,
                    written_at=_local_time(row.written_at, row.utc_offset_minutes),
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in session.execute(query)
            ]

    def read_entry(self, entry_id: UUID, keyring: Keyring) -> tuple[EntryMeta, EntryContent]:
        with self._sessions() as session:
            entry = session.get(Entry, entry_id)
            if entry is None:
                raise EntryNotFound(f"no entry {entry_id}")
            identity = keyring.get(entry.key_id)
            if identity is None:
                raise Unauthorized(
                    "this session does not hold the key for that entry; unlock again"
                )
            payload = envelope.unseal(
                entry.ciphertext, identity, entry_id=entry.id, entry_date=entry.entry_date
            )
            return _meta(entry), EntryContent(body=payload.body, tags=payload.tags)

    def update_entry(self, entry_id: UUID, content: EntryContent) -> EntryMeta:
        with self._sessions.begin() as session:
            entry = session.get(Entry, entry_id)
            if entry is None:
                raise EntryNotFound(f"no entry {entry_id}")
            key = self._active_key(session)
            payload = envelope.Payload(entry.id, entry.entry_date, content.body, content.tags)
            entry.ciphertext = envelope.seal(payload, key.recipient)
            entry.key_id = key.id
            entry.updated_at = self._utc_now()
            meta = _meta(entry)
        log.info("entry %s updated", entry_id)
        return meta

    def delete_entry(self, entry_id: UUID) -> None:
        with self._sessions.begin() as session:
            entry = session.get(Entry, entry_id)
            if entry is None:
                raise EntryNotFound(f"no entry {entry_id}")
            session.delete(entry)
        log.info("entry %s deleted", entry_id)

    # -- helpers ----------------------------------------------------------------------

    @staticmethod
    def _active_key(session: Session) -> Key:
        key = session.scalars(select(Key).order_by(Key.id.desc()).limit(1)).first()
        if key is None:
            raise NotInitialized("no keypair yet; run `journal-service init`")
        return key


def _local_time(written_at: datetime | None, offset_minutes: int | None) -> datetime | None:
    if written_at is None or offset_minutes is None:
        return None
    return written_at.astimezone(timezone(timedelta(minutes=offset_minutes)))


def _meta(entry: Entry) -> EntryMeta:
    return EntryMeta(
        id=entry.id,
        entry_date=entry.entry_date,
        written_at=_local_time(entry.written_at, entry.utc_offset_minutes),
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )
