"""Exceptions shared by the domain, the API and the CLI."""


class JournalError(Exception):
    """Base class for expected, user-facing errors."""


class SettingsError(JournalError):
    """The configuration file or environment is invalid."""


class ValidationFailed(JournalError):
    """Input does not satisfy the rules for entries, dates or passwords."""


class WeakPassword(ValidationFailed):
    pass


class PayloadTooLarge(JournalError):
    pass


class InvalidPassword(JournalError):
    pass


class Unauthorized(JournalError):
    """No valid unlock session for an operation that needs one."""


class NotInitialized(JournalError):
    """No keypair exists yet; run `journal-service init`."""


class AlreadyInitialized(JournalError):
    pass


class EntryNotFound(JournalError):
    pass


class CorruptEntry(JournalError):
    """A ciphertext failed to decrypt or does not belong to its row."""
