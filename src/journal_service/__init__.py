"""journal-service: backend and database for the journal app."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("journal-service")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
