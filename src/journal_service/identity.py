"""Static identity of the application."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppIdentity:
    app_name: str  # used for the XDG directory names
    description: str


IDENTITY = AppIdentity(
    app_name="journal-service",
    description="Backend and database for the journal app",
)
