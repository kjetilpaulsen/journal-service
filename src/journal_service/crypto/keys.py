"""Keypair generation and password wrapping of the private key."""

from dataclasses import dataclass

import pyrage
from pyrage import passphrase, x25519

from journal_service.errors import InvalidPassword, WeakPassword

MIN_PASSWORD_LENGTH = 12

type Identity = x25519.Identity


@dataclass(frozen=True, slots=True)
class Keypair:
    recipient: str  # "age1..."; public, used to encrypt new entries
    wrapped_identity: bytes  # age file holding the private key, encrypted with the password


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def generate_keypair(password: str) -> Keypair:
    check_password_policy(password)
    identity = x25519.Identity.generate()
    return Keypair(
        recipient=str(identity.to_public()),
        wrapped_identity=wrap_identity(identity, password),
    )


def wrap_identity(identity: Identity, password: str) -> bytes:
    # The trailing newline makes the decrypted content a valid age identity file.
    return passphrase.encrypt(f"{identity}\n".encode(), password)


def unwrap_identity(wrapped: bytes, password: str) -> Identity:
    try:
        secret = passphrase.decrypt(wrapped, password)
    except pyrage.DecryptError as exc:
        raise InvalidPassword("wrong password") from exc
    return x25519.Identity.from_str(secret.decode().strip())
