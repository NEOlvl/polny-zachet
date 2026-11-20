"""users и progress

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("tg_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("tg_id"),
    )
    op.create_table(
        "progress",
        sa.Column("tg_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("item_id", sa.String(24), nullable=False),
        sa.Column("done_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("tg_id", "item_id"),
    )


def downgrade() -> None:
    op.drop_table("progress")
    op.drop_table("users")
