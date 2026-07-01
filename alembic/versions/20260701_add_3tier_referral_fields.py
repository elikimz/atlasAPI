"""Add 3-tier referral fields and first purchase flag

Revision ID: 3tier_referral_update
Revises: 2400f32b4530
Create Date: 2026-07-01 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3tier_referral_update'
down_revision: Union[str, Sequence[str], None] = '2400f32b4530'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add has_purchased_first_package to users
    op.add_column('users', sa.Column('has_purchased_first_package', sa.Boolean(), server_default='0', nullable=False))
    
    # Add 3-tier fields to referral_codes
    op.add_column('referral_codes', sa.Column('tier_a_invite_earnings', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('referral_codes', sa.Column('tier_b_invite_earnings', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('referral_codes', sa.Column('tier_c_invite_earnings', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('referral_codes', sa.Column('tier_a_task_rebate', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('referral_codes', sa.Column('tier_b_task_rebate', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('referral_codes', sa.Column('tier_c_task_rebate', sa.Float(), server_default='0.0', nullable=False))

def downgrade() -> None:
    op.drop_column('referral_codes', 'tier_c_task_rebate')
    op.drop_column('referral_codes', 'tier_b_task_rebate')
    op.drop_column('referral_codes', 'tier_a_task_rebate')
    op.drop_column('referral_codes', 'tier_c_invite_earnings')
    op.drop_column('referral_codes', 'tier_b_invite_earnings')
    op.drop_column('referral_codes', 'tier_a_invite_earnings')
    op.drop_column('users', 'has_purchased_first_package')
