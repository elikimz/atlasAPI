"""Backfill the explicitly confirmed LV1 membership commission rate."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_lv1_membership_rate"
down_revision = "20261015_commission_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE plans SET membership_commission_rate = 0.10 "
            "WHERE LOWER(name) = 'lv1' AND membership_commission_rate IS NULL"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE plans SET membership_commission_rate = NULL "
            "WHERE LOWER(name) = 'lv1' AND membership_commission_rate = 0.10"
        )
    )
