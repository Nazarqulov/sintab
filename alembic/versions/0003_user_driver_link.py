"""link users to samsara drivers

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('driver_id', sa.Integer(), nullable=True))
    op.create_foreign_key('users_driver_id_fkey', 'users', 'drivers', ['driver_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('users_driver_id_fkey', 'users', type_='foreignkey')
    op.drop_column('users', 'driver_id')
