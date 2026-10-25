"""outbox messages

Revision ID: c4f1b8d2e306
Revises: b7c4e19a2f60
Create Date: 2026-10-24 14:20:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c4f1b8d2e306'
down_revision: str | None = 'b7c4e19a2f60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'outbox_messages',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('aggregate_type', sa.String(length=48), nullable=False),
        sa.Column('aggregate_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('dedupe_key', sa.String(length=160), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.DateTime(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedupe_key', name='uq_outbox_messages_dedupe_key'),
    )
    with op.batch_alter_table('outbox_messages', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_outbox_messages_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_outbox_messages_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_outbox_messages_aggregate_id'), ['aggregate_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_outbox_messages_event_type'), ['event_type'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_outbox_messages_status'), ['status'], unique=False
        )
        batch_op.create_index(
            'ix_outbox_messages_due', ['status', 'available_at'], unique=False
        )


def downgrade() -> None:
    op.drop_table('outbox_messages')
