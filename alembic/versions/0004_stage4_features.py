"""stage 4: notifications, digest/escalation, documents, internal chat

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('notify_level', sa.String(length=16), server_default='off', nullable=False))
    op.add_column('users', sa.Column('quiet_start', sa.SmallInteger(), nullable=True))
    op.add_column('users', sa.Column('quiet_end', sa.SmallInteger(), nullable=True))

    op.add_column('vehicles', sa.Column('fuel_percent', sa.Float(), nullable=True))
    op.add_column('vehicles', sa.Column('fuel_updated_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('groups', sa.Column('language', sa.String(length=8), server_default='uz', nullable=False))
    op.add_column('groups', sa.Column('is_escalation', sa.Boolean(), server_default=sa.false(), nullable=False))

    op.add_column('alerts', sa.Column('samsara_resolved', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('alerts', sa.Column('resolution_reason', sa.String(length=32), nullable=True))
    op.add_column('alerts', sa.Column('resolution_note', sa.Text(), nullable=True))

    op.add_column('alert_deliveries', sa.Column('kind', sa.String(length=16), server_default='route', nullable=False))
    # PG12+: ADD VALUE tranzaksiya ichida ishlaydi (yangi qiymat shu tranzaksiyada ishlatilmaydi).
    op.execute("ALTER TYPE delivery_status ADD VALUE IF NOT EXISTS 'deferred'")
    op.execute("ALTER TYPE delivery_status ADD VALUE IF NOT EXISTS 'digested'")

    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_type', sa.String(length=16), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('doc_type', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('file_id', sa.String(length=255), nullable=False),
    sa.Column('file_unique_id', sa.String(length=64), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=True),
    sa.Column('mime_type', sa.String(length=128), nullable=True),
    sa.Column('file_size', sa.BigInteger(), nullable=True),
    sa.Column('expires_at', sa.Date(), nullable=True),
    sa.Column('uploaded_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_documents_owner', 'documents', ['owner_type', 'owner_id'], unique=False)
    op.create_index(op.f('ix_documents_doc_type'), 'documents', ['doc_type'], unique=False)
    op.create_index(op.f('ix_documents_expires_at'), 'documents', ['expires_at'], unique=False)

    op.create_table('chat_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sender_id', sa.Integer(), nullable=True),
    sa.Column('recipient_id', sa.Integer(), nullable=True),
    sa.Column('content_type', sa.String(length=32), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('file_id', sa.String(length=255), nullable=True),
    sa.Column('file_name', sa.String(length=255), nullable=True),
    sa.Column('delivered_message_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['recipient_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chat_messages_pair', 'chat_messages', ['sender_id', 'recipient_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_chat_messages_created_at'), 'chat_messages', ['created_at'], unique=False)

    op.create_table('notification_marks',
    sa.Column('key', sa.String(length=160), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('notification_marks')
    op.drop_index(op.f('ix_chat_messages_created_at'), table_name='chat_messages')
    op.drop_index('ix_chat_messages_pair', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index(op.f('ix_documents_expires_at'), table_name='documents')
    op.drop_index(op.f('ix_documents_doc_type'), table_name='documents')
    op.drop_index('ix_documents_owner', table_name='documents')
    op.drop_table('documents')

    # Postgres enum'dan qiymat o'chirib bo'lmaydi — turni qayta yaratamiz.
    op.execute("UPDATE alert_deliveries SET status = 'sent' WHERE status IN ('deferred', 'digested')")
    op.execute("ALTER TABLE alert_deliveries ALTER COLUMN status TYPE text")
    op.execute("DROP TYPE delivery_status")
    op.execute("CREATE TYPE delivery_status AS ENUM ('pending', 'sent', 'failed')")
    op.execute(
        "ALTER TABLE alert_deliveries ALTER COLUMN status TYPE delivery_status USING status::delivery_status"
    )
    op.drop_column('alert_deliveries', 'kind')

    for column in ('resolution_note', 'resolution_reason', 'samsara_resolved'):
        op.drop_column('alerts', column)
    op.drop_column('groups', 'is_escalation')
    op.drop_column('vehicles', 'fuel_updated_at')
    op.drop_column('vehicles', 'fuel_percent')
    op.drop_column('groups', 'language')
    for column in ('quiet_end', 'quiet_start', 'notify_level'):
        op.drop_column('users', column)
