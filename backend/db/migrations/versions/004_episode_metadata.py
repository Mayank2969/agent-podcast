"""Add title, metadata to interviews; display_name to agents

Revision ID: 004
Revises: 003
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('interviews', sa.Column('title', sa.Text(), nullable=True))
    op.add_column('interviews', sa.Column('metadata', sa.Text(), nullable=True))
    op.add_column('agents', sa.Column('display_name', sa.Text(), nullable=True))

def downgrade():
    op.drop_column('interviews', 'title')
    op.drop_column('interviews', 'metadata')
    op.drop_column('agents', 'display_name')
