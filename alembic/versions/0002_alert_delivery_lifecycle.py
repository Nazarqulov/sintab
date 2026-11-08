"""alert delivery queue, mutes and lifecycle fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alerts', sa.Column('happened_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('alerts', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('alerts', sa.Column('longitude', sa.Float(), nullable=True))
    op.add_column('alerts', sa.Column('address', sa.String(length=255), nullable=True))
    op.add_column('alerts', sa.Column('samsara_url', sa.String(length=512), nullable=True))
    op.add_column('alerts', sa.Column('suppressed', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('alerts', sa.Column('assigned_to', sa.Integer(), nullable=True))
    op.add_column('alerts', sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'alerts_assigned_to_fkey', 'alerts', 'users', ['assigned_to'], ['id'], ondelete='SET NULL'
    )

    op.create_table('alert_deliveries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('alert_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('topic_id', sa.BigInteger(), nullable=True),
    sa.Column('message_id', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.Enum('pending', 'sent', 'failed', name='delivery_status'), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['alert_id'], ['alerts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alert_deliveries_alert_id'), 'alert_deliveries', ['alert_id'], unique=False)
    op.create_index(op.f('ix_alert_deliveries_status'), 'alert_deliveries', ['status'], unique=False)

    op.create_table('alert_mutes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('alert_type', sa.String(length=64), nullable=False),
    sa.Column('muted_until', sa.DateTime(timezone=True), nullable=False),
    sa.Column('muted_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['muted_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_alert_mutes_vehicle_type', 'alert_mutes', ['vehicle_id', 'alert_type'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_alert_mutes_vehicle_type', table_name='alert_mutes')
    op.drop_table('alert_mutes')
    op.drop_index(op.f('ix_alert_deliveries_status'), table_name='alert_deliveries')
    op.drop_index(op.f('ix_alert_deliveries_alert_id'), table_name='alert_deliveries')
    op.drop_table('alert_deliveries')
    sa.Enum(name='delivery_status').drop(op.get_bind(), checkfirst=True)

    op.drop_constraint('alerts_assigned_to_fkey', 'alerts', type_='foreignkey')
    for column in ('assigned_at', 'assigned_to', 'suppressed', 'samsara_url', 'address',
                   'longitude', 'latitude', 'happened_at'):
        op.drop_column('alerts', column)
