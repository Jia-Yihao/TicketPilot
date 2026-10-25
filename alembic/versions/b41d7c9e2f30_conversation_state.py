"""conversation state

Revision ID: b41d7c9e2f30
Revises: 7d3a9c1e5b42
Create Date: 2026-10-23 21:05:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b41d7c9e2f30'
down_revision: str | None = '7d3a9c1e5b42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'conversations',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('slots', sa.JSON(), nullable=False),
        sa.Column('turn_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_conversations_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_conversations_user_id'), ['user_id'], unique=False
        )

    op.create_table(
        'conversation_messages',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('conversation_id', sa.String(length=40), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('intent', sa.String(length=32), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('conversation_messages', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_conversation_messages_conversation_id'),
            ['conversation_id'],
            unique=False,
        )
        batch_op.create_index(
            'ix_conversation_messages_conversation_created',
            ['conversation_id', 'created_at'],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table('conversation_messages')
    op.drop_table('conversations')
