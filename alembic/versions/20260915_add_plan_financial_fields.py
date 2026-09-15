"""Add admin-editable plan financial fields.

Revision ID: 20260915_plan_financials
Revises: 20260728_add_destination_number
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_plan_financials"
down_revision = "20260728_add_destination_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("daily_earnings", sa.Float(), nullable=False, server_default="0"))
    op.add_column("plans", sa.Column("total_return", sa.Float(), nullable=False, server_default="0"))
    op.add_column("plans", sa.Column("profit", sa.Float(), nullable=False, server_default="0"))

    # Preserve the values previously shown by the client for existing plans.
    plans = sa.table(
        "plans",
        sa.column("name", sa.String()),
        sa.column("daily_earnings", sa.Float()),
        sa.column("total_return", sa.Float()),
        sa.column("profit", sa.Float()),
    )
    legacy_values = {
        "Intern": (0.7, 2.1, 2.1),
        "LV1": (0.7, 42.0, 22.0),
        "LV2": (1.7, 102.0, 52.0),
        "LV3": (3.5, 210.0, 110.0),
        "LV4": (5.0, 300.0, 150.0),
    }
    for name, (daily, total, profit) in legacy_values.items():
        op.execute(
            plans.update()
            .where(plans.c.name == name)
            .values(daily_earnings=daily, total_return=total, profit=profit)
        )


def downgrade() -> None:
    op.drop_column("plans", "profit")
    op.drop_column("plans", "total_return")
    op.drop_column("plans", "daily_earnings")
