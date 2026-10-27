"""Guard per-user task and certification state against duplicate rows.

Revision ID: 20261027_progress_unique
Revises: 20261016_lv1_membership_rate
"""
from alembic import op
import sqlalchemy as sa

revision = "20261027_progress_unique"
down_revision = "20261016_lv1_membership_rate"
branch_labels = None
depends_on = None


def _fail_on_duplicates(table: str, left: str, right: str) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        f"SELECT {left}, {right}, COUNT(*) AS n FROM {table} "
        f"GROUP BY {left}, {right} HAVING COUNT(*) > 1"
    )).fetchall()
    if rows:
        conflicts = ", ".join(f"({row[0]}, {row[1]}) x{row[2]}" for row in rows[:20])
        raise RuntimeError(
            f"Cannot add unique progress constraint: duplicate rows in {table}: {conflicts}. "
            "Resolve duplicates through an explicitly approved data-reconciliation process; "
            "this migration will not delete or rewrite records."
        )


def upgrade() -> None:
    _fail_on_duplicates("user_video_tasks", "user_id", "video_task_id")
    _fail_on_duplicates("user_certifications", "user_id", "certification_id")
    op.add_column("user_video_tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("uq_user_video_task_pair", "user_video_tasks", ["user_id", "video_task_id"], unique=True)
    op.create_index("uq_user_certification_pair", "user_certifications", ["user_id", "certification_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_user_certification_pair", table_name="user_certifications")
    op.drop_index("uq_user_video_task_pair", table_name="user_video_tasks")
    op.drop_column("user_video_tasks", "started_at")
