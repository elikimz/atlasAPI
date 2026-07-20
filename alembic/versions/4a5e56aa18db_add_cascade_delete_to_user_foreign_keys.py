"""add cascade delete to user foreign keys

Revision ID: 4a5e56aa18db
Revises: notifications_table
Create Date: 2026-07-04 09:53:17.401428
"""

from typing import Sequence, Union

from alembic import op


revision: str = "4a5e56aa18db"
down_revision: Union[str, Sequence[str], None] = "notifications_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FOREIGN_KEYS = (
    ("evaluations", "evaluations_user_id_fkey", "user_id"),
    ("payments", "payments_user_id_fkey", "user_id"),
    ("referral_codes", "referral_codes_user_id_fkey", "user_id"),
    ("user_certifications", "user_certifications_user_id_fkey", "user_id"),
    ("user_video_tasks", "user_video_tasks_user_id_fkey", "user_id"),
    ("user_plan_history", "user_plan_history_user_id_fkey", "user_id"),
    ("withdrawal_accounts", "withdrawal_accounts_user_id_fkey", "user_id"),
    ("upgrade_refunds", "upgrade_refunds_user_id_fkey", "user_id"),
    ("earnings_logs", "earnings_logs_user_id_fkey", "user_id"),
    ("referral_relationships", "referral_relationships_user_id_fkey", "user_id"),
    ("referral_relationships", "referral_relationships_referrer_id_fkey", "referrer_id"),
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _replace_foreign_keys(ondelete: str | None) -> None:
    # SQLite cannot ALTER or rename constraints in place. This revision is a
    # PostgreSQL production migration; SQLite is used only for isolated tests,
    # where its original foreign keys remain intact and subsequent revisions can
    # still be verified end-to-end.
    if _is_sqlite():
        return

    for table_name, constraint_name, local_column in _FOREIGN_KEYS:
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table_name,
            "users",
            [local_column],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace_foreign_keys(ondelete="CASCADE")


def downgrade() -> None:
    _replace_foreign_keys(ondelete=None)
