"""Add member-specific state for shared notifications.

Revision ID: 20261028_user_notice_state
Revises: 20261027_progress_unique
"""
from alembic import op
import sqlalchemy as sa

revision = "20261028_user_notice_state"
down_revision = "20261027_progress_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notification_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("notification_id", sa.Integer(), sa.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "notification_id", name="uq_user_notification_state_pair"),
    )
    op.create_index("ix_user_notification_states_user", "user_notification_states", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_notification_states_user", table_name="user_notification_states")
    op.drop_table("user_notification_states")
