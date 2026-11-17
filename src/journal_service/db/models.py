"""Tables.

Only what must be readable without the password is a column. Everything secret (body, tags,
and any field added later) lives inside `entries.ciphertext`.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Text,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from journal_service.db.types import UTCDateTime

# Explicit constraint names keep Alembic migrations deterministic (and SQLite batch mode able
# to drop or alter constraints later).
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Key(Base):
    """An age X25519 keypair. New entries are encrypted to the newest key."""

    __tablename__ = "keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient: Mapped[str] = mapped_column(Text, unique=True)
    wrapped_identity: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        # A time of writing is only meaningful together with the writer's UTC offset.
        CheckConstraint(
            "(written_at IS NULL) = (utc_offset_minutes IS NULL)",
            name="written_at_has_offset",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # UUIDv7: time-ordered
    # The journal day in the writer's local time. Always set, also for date-only imports.
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    # When the entry was written (UTC) and the writer's offset; NULL when only the date is known.
    written_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    utc_offset_minutes: Mapped[int | None] = mapped_column(Integer)
    key_id: Mapped[int] = mapped_column(ForeignKey("keys.id"))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)  # row inserted
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # last edit
