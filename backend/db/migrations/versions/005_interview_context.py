"""Add context column to interviews for pull-mode agent context

Revision ID: 005
Revises: 004
Create Date: 2026-03-15
"""
from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('interviews', sa.Column('context', sa.Text(), nullable=True))

def downgrade():
    op.drop_column('interviews', 'context')
