"""add destination_number to payments

Revision ID: 20260728_add_destination_number
Revises: 20260721_cache_indexes
Create Date: 2026-07-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260728_add_destination_number'
down_revision = '20260721_cache_indexes'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('payments', sa.Column('destination_number', sa.String(), nullable=True))

def downgrade():
    op.drop_column('payments', 'destination_number')
