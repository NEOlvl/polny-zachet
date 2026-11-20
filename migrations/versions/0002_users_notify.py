"""users.notify: согласие на уведомления о новых пунктах

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # существующие пользователи получают notify = true
    op.add_column("users", sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("notify")
