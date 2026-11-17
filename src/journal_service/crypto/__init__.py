"""Encryption at rest, built on the age format (https://age-encryption.org/v1).

Envelope scheme:

* One X25519 keypair per journal. The public half (the "recipient", ``age1...``) is stored
  in plaintext and every entry is encrypted to it, so writing never needs the password.
* The private half (the "identity") is stored as an age file encrypted with the password
  (age's scrypt mode), so reading needs the password.
* Changing the password re-encrypts only the identity; entries are untouched.

Everything stored is a standard age file, so the data stays readable with the `age` CLI
even without this code (see README, "Recovery without journal-service").
"""
