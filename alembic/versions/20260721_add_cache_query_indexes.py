"""Add indexes for cached and user-scoped read paths.

Revision ID: 20260721_cache_indexes
Revises: 6ab7d2e8f490
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260721_cache_indexes"
down_revision: Union[str, Sequence[str], None] = "6ab7d2e8f490"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_user_certifications_user_status", "user_certifications", ("user_id", "status")),
    ("ix_referral_codes_user_id", "referral_codes", ("user_id",)),
    ("ix_referral_relationships_referrer_id", "referral_relationships", ("referrer_id",)),
    ("ix_payments_user_created_at", "payments", ("user_id", "created_at")),
    ("ix_video_tasks_plan_id", "video_tasks", ("plan_id",)),
    ("ix_user_video_tasks_user_status_completed", "user_video_tasks", ("user_id", "status", "completed_at")),
    ("ix_user_video_tasks_user_video_task", "user_video_tasks", ("user_id", "video_task_id")),
    ("ix_user_plan_history_user_status_expiry", "user_plan_history", ("user_id", "status", "expires_at")),
    ("ix_withdrawal_accounts_user_primary", "withdrawal_accounts", ("user_id", "is_primary")),
    ("ix_upgrade_refunds_user_status_created", "upgrade_refunds", ("user_id", "status", "created_at")),
    ("ix_earnings_logs_user_created_at", "earnings_logs", ("user_id", "created_at")),
    ("ix_notifications_user_created_at", "notifications", ("user_id", "created_at")),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    existing_index_names = {
        index["name"]
        for table_name in table_names
        for index in inspector.get_indexes(table_name)
        if index["name"]
    }

    for index_name, table_name, columns in INDEX_SPECS:
        if table_name in table_names and index_name not in existing_index_names:
            op.create_index(index_name, table_name, list(columns))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    existing_index_names = {
        index["name"]
        for table_name in table_names
        for index in inspector.get_indexes(table_name)
        if index["name"]
    }

    for index_name, table_name, _ in reversed(INDEX_SPECS):
        if table_name in table_names and index_name in existing_index_names:
            op.drop_index(index_name, table_name=table_name)
