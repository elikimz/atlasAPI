"""Add configurable membership commission policy and award ledger."""
from alembic import op
import sqlalchemy as sa

revision = "20261015_commission_policy"
down_revision = "20260915_plan_financials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("membership_level", sa.Integer(), nullable=True))
    op.add_column("plans", sa.Column("membership_commission_rate", sa.Float(), nullable=True))
    op.create_table(
        "commission_awards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("downline_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("upline_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("eligible_amount", sa.Float(), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_commission_awards_id", "commission_awards", ["id"])
    op.create_index("ix_commission_awards_upline_created", "commission_awards", ["upline_user_id", "created_at"])
    op.create_index("uq_commission_award_source_recipient_tier", "commission_awards", ["source_type", "source_id", "upline_user_id", "tier"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_commission_award_source_recipient_tier", table_name="commission_awards")
    op.drop_index("ix_commission_awards_upline_created", table_name="commission_awards")
    op.drop_index("ix_commission_awards_id", table_name="commission_awards")
    op.drop_table("commission_awards")
    op.drop_column("plans", "membership_commission_rate")
    op.drop_column("plans", "membership_level")
