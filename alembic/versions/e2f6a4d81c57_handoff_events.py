"""handoff and abandon events

Revision ID: e2f6a4d81c57
Revises: d5a91b73e204
Create Date: 2026-10-24 09:15:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'e2f6a4d81c57'
down_revision: str | None = 'd5a91b73e204'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'handoff_events',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=40), nullable=False),
        sa.Column('signal_id', sa.String(length=48), nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('priority', sa.String(length=8), nullable=False),
        sa.Column('queue', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('intent', sa.String(length=48), nullable=True),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('bundle', sa.JSON(), nullable=False),
        sa.Column('trace_id', sa.String(length=40), nullable=True),
        sa.Column('pending_task_id', sa.String(length=40), nullable=True),
        sa.Column('claimed_by', sa.String(length=64), nullable=True),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        sa.Column('released_by', sa.String(length=64), nullable=True),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('closed_by', sa.String(length=64), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('resolution', sa.String(length=16), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('handoff_events', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_handoff_events_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_handoff_events_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_handoff_events_conversation_id'),
            ['conversation_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_handoff_events_signal_id'), ['signal_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_handoff_events_reason'), ['reason'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_handoff_events_status'), ['status'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_handoff_events_trace_id'), ['trace_id'], unique=False
        )
        batch_op.create_index(
            'ix_handoff_events_tenant_status', ['tenant_id', 'status'], unique=False
        )
        batch_op.create_index(
            'ix_handoff_events_conversation',
            ['conversation_id', 'created_at'],
            unique=False,
        )

    op.create_table(
        'abandon_events',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=40), nullable=False),
        sa.Column('intent', sa.String(length=48), nullable=True),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.Column('idle_minutes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('abandon_events', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_abandon_events_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_abandon_events_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_abandon_events_conversation_id'),
            ['conversation_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_abandon_events_intent'), ['intent'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_abandon_events_channel'), ['channel'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_abandon_events_reason'), ['reason'], unique=False
        )
        batch_op.create_index(
            'ix_abandon_events_tenant_created',
            ['tenant_id', 'created_at'],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table('abandon_events')
    op.drop_table('handoff_events')
