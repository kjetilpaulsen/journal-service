from datetime import date
from uuid import uuid7

import pyrage
import pytest

from journal_service.crypto import envelope, keys
from journal_service.errors import CorruptEntry, InvalidPassword, WeakPassword

from .conftest import PASSWORD


@pytest.fixture(scope="module")
def identity(keypair) -> keys.Identity:
    return keys.unwrap_identity(keypair.wrapped_identity, PASSWORD)


def test_wrapped_identity_is_a_standard_age_file(keypair):
    assert keypair.recipient.startswith("age1")
    assert keypair.wrapped_identity.startswith(b"age-encryption.org/v1\n")
    assert b"AGE-SECRET-KEY" not in keypair.wrapped_identity


def test_wrong_password_is_rejected(keypair):
    with pytest.raises(InvalidPassword):
        keys.unwrap_identity(keypair.wrapped_identity, "not the password")


def test_short_password_is_rejected():
    with pytest.raises(WeakPassword):
        keys.generate_keypair("short")


def test_seal_roundtrip(keypair, identity):
    payload = envelope.Payload(uuid7(), date(2026, 11, 17), "Dear diary, æøå 🙂", ("work",))
    sealed = envelope.seal(payload, keypair.recipient)
    assert b"Dear diary" not in sealed
    assert (
        envelope.unseal(sealed, identity, entry_id=payload.entry_id, entry_date=payload.entry_date)
        == payload
    )


def test_padding_hides_exact_length(keypair, identity):
    # Compare plaintext sizes: the age header itself varies (rage adds random "grease").
    sizes = set()
    for body in ("a", "a" * 500):
        sealed = envelope.seal(
            envelope.Payload(uuid7(), date(2026, 1, 1), body, ()), keypair.recipient
        )
        sizes.add(len(pyrage.decrypt(sealed, [identity])))
    assert sizes == {envelope.PAD_BLOCK}


def test_ciphertext_is_bound_to_its_row(keypair, identity):
    payload = envelope.Payload(uuid7(), date(2026, 11, 17), "text", ())
    sealed = envelope.seal(payload, keypair.recipient)
    with pytest.raises(CorruptEntry):
        envelope.unseal(sealed, identity, entry_id=uuid7(), entry_date=payload.entry_date)
    with pytest.raises(CorruptEntry):
        envelope.unseal(sealed, identity, entry_id=payload.entry_id, entry_date=date(2026, 1, 1))


def test_garbage_ciphertext_is_reported_as_corrupt(identity):
    with pytest.raises(CorruptEntry):
        envelope.unseal(b"not age", identity, entry_id=uuid7(), entry_date=date(2026, 1, 1))
