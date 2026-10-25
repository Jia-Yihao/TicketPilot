"""customer memory and proactive notifications

Revision ID: c3b8e5a71d29
Revises: a9d4c2f7b135
Create Date: 2026-10-24 12:05:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c3b8e5a71d29'
down_revision: str | None = 'a9d4c2f7b135'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'customer_profiles',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('preferred_name', sa.String(length=40), nullable=True),
        sa.Column('locale', sa.String(length=16), nullable=False),
        sa.Column('preferences', sa.JSON(), nullable=False),
        sa.Column('history_summary', sa.Text(), nullable=False),
        sa.Column('proactive_opt_in', sa.Boolean(), nullable=False),
        sa.Column('muted_event_types', sa.JSON(), nullable=False),
        sa.Column('max_notifications_per_day', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'user_id', name='uq_customer_profiles_owner'
        ),
    )
    with op.batch_alter_table('customer_profiles', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_customer_profiles_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_customer_profiles_user_id'), ['user_id'], unique=False
        )

    op.create_table(
        'proactive_rules',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=48), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('template', sa.Text(), nullable=False),
        sa.Column('requires_confirmation', sa.Boolean(), nullable=False),
        sa.Column('cooldown_minutes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'event_type', name='uq_proactive_rules_tenant_event'
        ),
    )
    with op.batch_alter_table('proactive_rules', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_proactive_rules_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_proactive_rules_event_type'),
            ['event_type'],
            unique=False,
        )

    op.create_table(
        'notifications',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=48), nullable=False),
        sa.Column('event_key', sa.String(length=120), nullable=False),
        sa.Column('rule_id', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id',
            'user_id',
            'event_type',
            'event_key',
            name='uq_notifications_event_key',
        ),
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_notifications_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_notifications_event_type'), ['event_type'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_notifications_rule_id'), ['rule_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_notifications_status'), ['status'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False
        )
        batch_op.create_index(
            'ix_notifications_user_created',
            ['tenant_id', 'user_id', 'created_at'],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table('notifications')
    op.drop_table('proactive_rules')
    op.drop_table('customer_profiles')
