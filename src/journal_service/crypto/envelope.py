"""Encryption of entry payloads.

The plaintext of an entry is a small JSON document with everything that must stay secret
(body, tags) plus the entry's id and date. The id and date bind the ciphertext to its row:
a ciphertext copied onto another row fails to open. The JSON is padded with spaces (which
JSON ignores) to a multiple of PAD_BLOCK bytes, so the stored size reveals the length only
roughly. New secret fields can be added here without a database migration.
"""

import json
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import pyrage
from pyrage import x25519

from journal_service.crypto.keys import Identity
from journal_service.errors import CorruptEntry

PAYLOAD_VERSION = 1
PAD_BLOCK = 1024


@dataclass(frozen=True, slots=True)
class Payload:
    entry_id: UUID
    entry_date: date
    body: str
    tags: tuple[str, ...]


def seal(payload: Payload, recipient: str) -> bytes:
    document = {
        "v": PAYLOAD_VERSION,
        "id": str(payload.entry_id),
        "entry_date": payload.entry_date.isoformat(),
        "body": payload.body,
        "tags": list(payload.tags),
    }
    plaintext = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    plaintext += b" " * (-len(plaintext) % PAD_BLOCK)
    return pyrage.encrypt(plaintext, [x25519.Recipient.from_str(recipient)])


def unseal(ciphertext: bytes, identity: Identity, *, entry_id: UUID, entry_date: date) -> Payload:
    try:
        document = json.loads(pyrage.decrypt(ciphertext, [identity]))
    except (pyrage.DecryptError, ValueError) as exc:
        raise CorruptEntry(f"entry {entry_id} could not be decrypted") from exc

    if not isinstance(document, dict) or document.get("v") != PAYLOAD_VERSION:
        raise CorruptEntry(f"entry {entry_id}: unsupported payload format")
    if document.get("id") != str(entry_id) or document.get("entry_date") != entry_date.isoformat():
        raise CorruptEntry(f"entry {entry_id}: ciphertext belongs to a different entry")

    body, tags = document.get("body"), document.get("tags")
    if not isinstance(body, str) or not isinstance(tags, list):
        raise CorruptEntry(f"entry {entry_id}: malformed payload")
    return Payload(entry_id=entry_id, entry_date=entry_date, body=body, tags=tuple(tags))
