"""channel messages

Revision ID: a9d4c2f7b135
Revises: f3a7c9d21b84
Create Date: 2026-10-24 11:20:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a9d4c2f7b135'
down_revision: str | None = 'f3a7c9d21b84'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'channel_messages',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('channel_message_id', sa.String(length=160), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=40), nullable=True),
        sa.Column('direction', sa.String(length=8), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('request_digest', sa.String(length=64), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('response_payload', sa.JSON(), nullable=True),
        sa.Column('rendered_text', sa.Text(), nullable=True),
        sa.Column('last_error', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'channel',
            'channel_message_id',
            name='uq_channel_messages_delivery',
        ),
    )
    with op.batch_alter_table('channel_messages', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_channel_messages_channel'), ['channel'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_channel_messages_tenant_id'),
            ['tenant_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_channel_messages_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_channel_messages_conversation_id'),
            ['conversation_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_channel_messages_status'), ['status'], unique=False
        )
        batch_op.create_index(
            'ix_channel_messages_tenant_created',
            ['tenant_id', 'created_at'],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table('channel_messages')
