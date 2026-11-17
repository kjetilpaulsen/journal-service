import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from journal_service.db.models import Base
from journal_service.errors import (
    AlreadyInitialized,
    EntryNotFound,
    InvalidPassword,
    NotInitialized,
    ValidationFailed,
    WeakPassword,
)
from journal_service.journal import Journal, NewEntry, make_content

from .conftest import PASSWORD


def _new(body: str = "Today I set up journal-service.", **kwargs) -> NewEntry:
    return NewEntry(content=make_content(body, kwargs.pop("tags", ())), **kwargs)


def test_migrations_match_models(engine):
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_writing_before_init_is_refused(journal: Journal):
    with pytest.raises(NotInitialized):
        journal.create_entry(_new())


def test_init_once(initialized: Journal):
    assert initialized.is_initialized()
    with pytest.raises(AlreadyInitialized):
        initialized.initialize(PASSWORD)


def test_create_and_list_need_no_password(initialized: Journal):
    meta = initialized.create_entry(_new(tags=["setup"]))
    # 00:30 in Oslo on 18 Nov: the journal day is the local date, the offset is kept.
    assert meta.entry_date == date(2026, 11, 18)
    assert meta.written_at == datetime(2026, 11, 18, 0, 30, tzinfo=timezone(timedelta(hours=1)))
    assert meta.written_at.utcoffset() == timedelta(hours=1)
    assert [m.id for m in initialized.list_entries()] == [meta.id]


def test_read_needs_the_keyring(initialized: Journal):
    meta = initialized.create_entry(_new("secret text", tags=["a", "A", " b "]))
    keyring = initialized.unlock(PASSWORD)
    read_meta, content = initialized.read_entry(meta.id, keyring)
    assert read_meta == meta
    assert content.body == "secret text"
    assert content.tags == ("a", "b")  # trimmed, de-duplicated case-insensitively


def test_wrong_password_does_not_unlock(initialized: Journal):
    with pytest.raises(InvalidPassword):
        initialized.unlock("wrong password here")


def test_plaintext_never_reaches_the_database_file(initialized: Journal, engine, xdg):
    initialized.create_entry(_new("a very distinctive sentence", tags=["distinctive-tag"]))
    engine.dispose()
    # Search the database and any WAL/SHM files next to it.
    raw = b"".join(p.read_bytes() for p in xdg.data_dir.iterdir() if p.is_file())
    assert b"distinctive" not in raw


def test_date_only_entry_for_imports(initialized: Journal):
    meta = initialized.create_entry(_new(entry_date=date(2019, 3, 4)))
    assert meta.entry_date == date(2019, 3, 4)
    assert meta.written_at is None


def test_explicit_written_at_keeps_its_offset(initialized: Journal):
    moment = datetime(2026, 7, 1, 21, 15, tzinfo=timezone(timedelta(hours=2)))
    meta = initialized.create_entry(_new(written_at=moment))
    assert meta.written_at == moment
    assert meta.written_at.utcoffset() == timedelta(hours=2)
    assert initialized.list_entries()[0].written_at.utcoffset() == timedelta(hours=2)


def test_written_at_and_entry_date_are_exclusive(initialized: Journal):
    with pytest.raises(ValidationFailed):
        initialized.create_entry(_new(written_at=datetime.now(UTC), entry_date=date(2026, 1, 1)))


def test_list_is_chronological_and_filterable(initialized: Journal):
    for day in (date(2026, 3, 1), date(2026, 1, 1), date(2026, 2, 1)):
        initialized.create_entry(_new(entry_date=day))
    assert [m.entry_date.month for m in initialized.list_entries()] == [1, 2, 3]
    window = initialized.list_entries(start=date(2026, 1, 15), end=date(2026, 2, 15))
    assert [m.entry_date for m in window] == [date(2026, 2, 1)]


def test_update_and_delete(initialized: Journal):
    meta = initialized.create_entry(_new("first draft"))
    updated = initialized.update_entry(meta.id, make_content("second draft", ["edited"]))
    assert updated.updated_at is not None
    _, content = initialized.read_entry(meta.id, initialized.unlock(PASSWORD))
    assert (content.body, content.tags) == ("second draft", ("edited",))
    initialized.delete_entry(meta.id)
    with pytest.raises(EntryNotFound):
        initialized.delete_entry(meta.id)


def test_change_password_keeps_entries_readable(initialized: Journal):
    meta = initialized.create_entry(_new("written before the change"))
    new_password = "an even better passphrase"
    with pytest.raises(WeakPassword):
        initialized.change_password(PASSWORD, "short")
    initialized.change_password(PASSWORD, new_password)
    with pytest.raises(InvalidPassword):
        initialized.unlock(PASSWORD)
    _, content = initialized.read_entry(meta.id, initialized.unlock(new_password))
    assert content.body == "written before the change"


def test_empty_body_is_rejected():
    with pytest.raises(ValidationFailed):
        make_content("   \n")


def test_raw_rows_are_portable(initialized: Journal, xdg):
    """What a migration to another database would copy: plain values and age blobs."""
    initialized.create_entry(_new())
    with closing(sqlite3.connect(xdg.database_file)) as db:
        entry_date, ciphertext = db.execute("SELECT entry_date, ciphertext FROM entries").fetchone()
        (wrapped,) = db.execute("SELECT wrapped_identity FROM keys").fetchone()
    assert entry_date == "2026-11-18"
    assert ciphertext.startswith(b"age-encryption.org/v1\n")
    assert wrapped.startswith(b"age-encryption.org/v1\n")
