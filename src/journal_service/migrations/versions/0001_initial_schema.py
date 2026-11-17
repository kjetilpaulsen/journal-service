"""initial schema: keys and entries

Revision ID: 0001
Revises:
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.Text(), nullable=False),
        sa.Column("wrapped_identity", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_keys")),
        sa.UniqueConstraint("recipient", name=op.f("uq_keys_recipient")),
    )
    op.create_table(
        "entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("utc_offset_minutes", sa.Integer(), nullable=True),
        sa.Column("key_id", sa.Integer(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(written_at IS NULL) = (utc_offset_minutes IS NULL)",
            name=op.f("ck_entries_written_at_has_offset"),
        ),
        sa.ForeignKeyConstraint(["key_id"], ["keys.id"], name=op.f("fk_entries_key_id_keys")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entries")),
    )
    op.create_index(op.f("ix_entries_entry_date"), "entries", ["entry_date"])


def downgrade() -> None:
    op.drop_index(op.f("ix_entries_entry_date"), table_name="entries")
    op.drop_table("entries")
    op.drop_table("keys")
