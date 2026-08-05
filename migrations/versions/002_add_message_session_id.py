"""Add session_id FK to messages table.

Revision ID: 002_message_session_id
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa

revision = '002_message_session_id'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'messages',
        sa.Column('session_id', sa.Uuid(), sa.ForeignKey('sessions.id'), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('messages', 'session_id')
