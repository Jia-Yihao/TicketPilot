"""proactive rule versions, changes and notification rule version

Revision ID: a1c7e9d4b820
Revises: b4e8d2f6a1c9
Create Date: 2026-10-26 16:20:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a1c7e9d4b820'
down_revision: str | None = 'b4e8d2f6a1c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('proactive_rules', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'version', sa.Integer(), nullable=False, server_default='1'
            )
        )

    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'rule_version', sa.Integer(), nullable=False, server_default='1'
            )
        )

    op.create_table(
        'proactive_rule_versions',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=48), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('template', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('requires_confirmation', sa.Boolean(), nullable=False),
        sa.Column('cooldown_minutes', sa.Integer(), nullable=False),
        sa.Column('change_id', sa.String(length=40), nullable=False),
        sa.Column('submitted_by', sa.String(length=64), nullable=False),
        sa.Column('approved_by', sa.String(length=64), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id',
            'event_type',
            'version',
            name='uq_proactive_rule_versions_number',
        ),
    )
    with op.batch_alter_table('proactive_rule_versions', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_proactive_rule_versions_tenant_id'),
            ['tenant_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_proactive_rule_versions_event_type'),
            ['event_type'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_proactive_rule_versions_change_id'),
            ['change_id'],
            unique=False,
        )

    op.create_table(
        'proactive_rule_changes',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=48), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('pending_key', sa.String(length=48), nullable=True),
        sa.Column('base_version', sa.Integer(), nullable=False),
        sa.Column('target_version', sa.Integer(), nullable=True),
        sa.Column('template', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('requires_confirmation', sa.Boolean(), nullable=False),
        sa.Column('cooldown_minutes', sa.Integer(), nullable=False),
        sa.Column('old_template', sa.Text(), nullable=True),
        sa.Column('old_enabled', sa.Boolean(), nullable=True),
        sa.Column('old_cooldown_minutes', sa.Integer(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('submitted_by', sa.String(length=64), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=False),
        sa.Column('decided_by', sa.String(length=64), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decision_note', sa.Text(), nullable=False),
        sa.Column('applied_version', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id',
            'pending_key',
            name='uq_proactive_rule_changes_pending',
        ),
    )
    with op.batch_alter_table('proactive_rule_changes', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_proactive_rule_changes_tenant_id'),
            ['tenant_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_proactive_rule_changes_event_type'),
            ['event_type'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_proactive_rule_changes_status'),
            ['status'],
            unique=False,
        )

    # Existing rules become version 1 so a notification produced before the
    # first governed edit can still resolve the words it used.
    op.execute(
        sa.text(
            """
            INSERT INTO proactive_rule_versions (
                id, tenant_id, event_type, version, template, enabled,
                requires_confirmation, cooldown_minutes, change_id,
                submitted_by, approved_by, reason, created_at
            )
            SELECT
                'prv_' || substr(id, 6, 30), tenant_id, event_type, 1, template,
                enabled, requires_confirmation, cooldown_minutes, 'seed',
                'system', 'system', 'seed', CURRENT_TIMESTAMP
            FROM proactive_rules
            WHERE NOT EXISTS (
                SELECT 1 FROM proactive_rule_versions v
                WHERE v.tenant_id = proactive_rules.tenant_id
                  AND v.event_type = proactive_rules.event_type
                  AND v.version = 1
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_table('proactive_rule_changes')
    op.drop_table('proactive_rule_versions')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_column('rule_version')
    with op.batch_alter_table('proactive_rules', schema=None) as batch_op:
        batch_op.drop_column('version')
