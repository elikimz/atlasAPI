"""add cascade delete to user foreign keys

Revision ID: 4a5e56aa18db
Revises: notifications_table
Create Date: 2026-07-04 09:53:17.401428

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a5e56aa18db'
down_revision: Union[str, Sequence[str], None] = 'notifications_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. evaluations
    op.drop_constraint('evaluations_user_id_fkey', 'evaluations', type_='foreignkey')
    op.create_foreign_key('evaluations_user_id_fkey', 'evaluations', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 2. payments
    op.drop_constraint('payments_user_id_fkey', 'payments', type_='foreignkey')
    op.create_foreign_key('payments_user_id_fkey', 'payments', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 3. referral_codes
    op.drop_constraint('referral_codes_user_id_fkey', 'referral_codes', type_='foreignkey')
    op.create_foreign_key('referral_codes_user_id_fkey', 'referral_codes', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 4. user_certifications
    op.drop_constraint('user_certifications_user_id_fkey', 'user_certifications', type_='foreignkey')
    op.create_foreign_key('user_certifications_user_id_fkey', 'user_certifications', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 5. user_video_tasks
    op.drop_constraint('user_video_tasks_user_id_fkey', 'user_video_tasks', type_='foreignkey')
    op.create_foreign_key('user_video_tasks_user_id_fkey', 'user_video_tasks', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 6. user_plan_history
    op.drop_constraint('user_plan_history_user_id_fkey', 'user_plan_history', type_='foreignkey')
    op.create_foreign_key('user_plan_history_user_id_fkey', 'user_plan_history', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 7. withdrawal_accounts
    op.drop_constraint('withdrawal_accounts_user_id_fkey', 'withdrawal_accounts', type_='foreignkey')
    op.create_foreign_key('withdrawal_accounts_user_id_fkey', 'withdrawal_accounts', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 8. upgrade_refunds
    op.drop_constraint('upgrade_refunds_user_id_fkey', 'upgrade_refunds', type_='foreignkey')
    op.create_foreign_key('upgrade_refunds_user_id_fkey', 'upgrade_refunds', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 9. earnings_logs
    op.drop_constraint('earnings_logs_user_id_fkey', 'earnings_logs', type_='foreignkey')
    op.create_foreign_key('earnings_logs_user_id_fkey', 'earnings_logs', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # 10. referral_relationships
    op.drop_constraint('referral_relationships_user_id_fkey', 'referral_relationships', type_='foreignkey')
    op.create_foreign_key('referral_relationships_user_id_fkey', 'referral_relationships', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    
    op.drop_constraint('referral_relationships_referrer_id_fkey', 'referral_relationships', type_='foreignkey')
    op.create_foreign_key('referral_relationships_referrer_id_fkey', 'referral_relationships', 'users', ['referrer_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    """Downgrade schema."""
    # 1. evaluations
    op.drop_constraint('evaluations_user_id_fkey', 'evaluations', type_='foreignkey')
    op.create_foreign_key('evaluations_user_id_fkey', 'evaluations', 'users', ['user_id'], ['id'])

    # 2. payments
    op.drop_constraint('payments_user_id_fkey', 'payments', type_='foreignkey')
    op.create_foreign_key('payments_user_id_fkey', 'payments', 'users', ['user_id'], ['id'])

    # 3. referral_codes
    op.drop_constraint('referral_codes_user_id_fkey', 'referral_codes', type_='foreignkey')
    op.create_foreign_key('referral_codes_user_id_fkey', 'referral_codes', 'users', ['user_id'], ['id'])

    # 4. user_certifications
    op.drop_constraint('user_certifications_user_id_fkey', 'user_certifications', type_='foreignkey')
    op.create_foreign_key('user_certifications_user_id_fkey', 'user_certifications', 'users', ['user_id'], ['id'])

    # 5. user_video_tasks
    op.drop_constraint('user_video_tasks_user_id_fkey', 'user_video_tasks', type_='foreignkey')
    op.create_foreign_key('user_video_tasks_user_id_fkey', 'user_video_tasks', 'users', ['user_id'], ['id'])

    # 6. user_plan_history
    op.drop_constraint('user_plan_history_user_id_fkey', 'user_plan_history', type_='foreignkey')
    op.create_foreign_key('user_plan_history_user_id_fkey', 'user_plan_history', 'users', ['user_id'], ['id'])

    # 7. withdrawal_accounts
    op.drop_constraint('withdrawal_accounts_user_id_fkey', 'withdrawal_accounts', type_='foreignkey')
    op.create_foreign_key('withdrawal_accounts_user_id_fkey', 'withdrawal_accounts', 'users', ['user_id'], ['id'])

    # 8. upgrade_refunds
    op.drop_constraint('upgrade_refunds_user_id_fkey', 'upgrade_refunds', type_='foreignkey')
    op.create_foreign_key('upgrade_refunds_user_id_fkey', 'upgrade_refunds', 'users', ['user_id'], ['id'])

    # 9. earnings_logs
    op.drop_constraint('earnings_logs_user_id_fkey', 'earnings_logs', type_='foreignkey')
    op.create_foreign_key('earnings_logs_user_id_fkey', 'earnings_logs', 'users', ['user_id'], ['id'])

    # 10. referral_relationships
    op.drop_constraint('referral_relationships_user_id_fkey', 'referral_relationships', type_='foreignkey')
    op.create_foreign_key('referral_relationships_user_id_fkey', 'referral_relationships', 'users', ['user_id'], ['id'])
    
    op.drop_constraint('referral_relationships_referrer_id_fkey', 'referral_relationships', type_='foreignkey')
    op.create_foreign_key('referral_relationships_referrer_id_fkey', 'referral_relationships', 'users', ['referrer_id'], ['id'])
