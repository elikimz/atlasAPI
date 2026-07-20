"""Materialize schema previously created by runtime startup code.

The historical application mutated the database on every API start.  This
revision moves those existing model tables and columns into the Alembic chain
so a fresh deployment reaches a usable schema before the server starts.

Revision ID: 6ab7d2e8f490
Revises: 5f2a1c9d7e31
Create Date: 2026-07-20
"""

from __future__ import annotations

import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.models import Base


revision: str = "6ab7d2e8f490"
down_revision: Union[str, Sequence[str], None] = "5f2a1c9d7e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_copy(
    column: sa.Column,
    *,
    nullable: bool | None = None,
    include_foreign_keys: bool = True,
) -> sa.Column:
    """Create a detached column suitable for Alembic's add_column operation."""
    foreign_keys = [
        sa.ForeignKey(
            foreign_key.target_fullname,
            ondelete=foreign_key.ondelete,
            onupdate=foreign_key.onupdate,
        )
        for foreign_key in column.foreign_keys
    ] if include_foreign_keys else []
    return sa.Column(
        column.name,
        column.type,
        *foreign_keys,
        nullable=column.nullable if nullable is None else nullable,
        server_default=column.server_default,
    )


def _add_missing_columns(bind: sa.Connection) -> set[tuple[str, str]]:
    """Add model columns absent from legacy, startup-mutated databases."""
    inspector = sa.inspect(bind)
    added: set[tuple[str, str]] = set()
    for table in Base.metadata.sorted_tables:
        if table.name not in set(inspector.get_table_names()):
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue

            # Existing OTP-era users have no password credential. Add the two
            # required fields nullable, backfill below, then tighten them on
            # databases that support ALTER COLUMN.
            nullable = True if (table.name, column.name) in {
                ("users", "username"),
                ("users", "password_hash"),
            } else None
            op.add_column(
                table.name,
                _column_copy(
                    column,
                    nullable=nullable,
                    include_foreign_keys=bind.dialect.name != "sqlite",
                ),
            )
            added.add((table.name, column.name))
    return added


def _ensure_indexes(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    index_names = {
        index["name"]
        for table_name in inspector.get_table_names()
        for index in inspector.get_indexes(table_name)
        if index["name"]
    }
    for table in Base.metadata.sorted_tables:
        if table.name not in set(inspector.get_table_names()):
            continue
        for index in table.indexes:
            if index.name and index.name not in index_names:
                op.create_index(index.name, table.name, [column.name for column in index.columns], unique=index.unique)
                index_names.add(index.name)


def _backfill_legacy_users(bind: sa.Connection, added: set[tuple[str, str]]) -> None:
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "username" in user_columns:
        # ID-derived values are deterministic and collision-resistant, unlike
        # using email prefixes. Existing populated usernames remain untouched.
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text("UPDATE users SET username = 'legacy-user-' || id::text WHERE username IS NULL OR username = ''"))
        else:
            bind.execute(sa.text("UPDATE users SET username = 'legacy-user-' || CAST(id AS TEXT) WHERE username IS NULL OR username = ''"))

    if "password_hash" in user_columns:
        # Do not manufacture a usable password for former OTP-only accounts.
        # This deterministic unusable bcrypt-shaped marker forces an explicit
        # password reset / administrator-assisted recovery before login.
        disabled_hash = "$2b$12$" + hashlib.sha256(b"atlas-disabled-legacy-password").hexdigest()[:53]
        bind.execute(
            sa.text("UPDATE users SET password_hash = :disabled_hash WHERE password_hash IS NULL OR password_hash = ''"),
            {"disabled_hash": disabled_hash},
        )

    if bind.dialect.name != "sqlite":
        if ("users", "username") in added:
            op.alter_column("users", "username", nullable=False)
        if ("users", "password_hash") in added:
            op.alter_column("users", "password_hash", nullable=False)


def upgrade() -> None:
    bind = op.get_bind()

    # Creates only tables absent from the old migrations (plans, config,
    # provider payments, account tables, and other runtime-only structures).
    Base.metadata.create_all(bind=bind, checkfirst=True)
    added = _add_missing_columns(bind)
    _backfill_legacy_users(bind, added)
    _ensure_indexes(bind)


def downgrade() -> None:
    # This is a one-way consolidation of historically implicit startup schema
    # changes. Dropping tables or fields here could destroy production data.
    # Subsequent revisions must provide explicit, reversible domain changes.
    pass
