"""security events

Revision ID: f3a7c9d21b84
Revises: c4f1b8d2e306
Create Date: 2026-10-24 09:10:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'f3a7c9d21b84'
down_revision: str | None = 'c4f1b8d2e306'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'security_events',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=True),
        sa.Column('conversation_id', sa.String(length=40), nullable=True),
        sa.Column('trace_id', sa.String(length=40), nullable=True),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('rule_id', sa.String(length=64), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('excerpt', sa.String(length=240), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('security_events', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_security_events_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_security_events_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_security_events_conversation_id'),
            ['conversation_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_security_events_trace_id'), ['trace_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_security_events_category'), ['category'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_security_events_created_at'),
            ['created_at'],
            unique=False,
        )
        batch_op.create_index(
            'ix_security_events_tenant_created',
            ['tenant_id', 'created_at'],
            unique=False,
        )
        batch_op.create_index(
            'ix_security_events_tenant_category',
            ['tenant_id', 'category'],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table('security_events')
