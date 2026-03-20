"""Add dashboard_token column to agents for authentication

Revision ID: 006
Revises: 005
Create Date: 2026-03-20
"""
from alembic import op
import sqlalchemy as sa

revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade():
    # Use inspector to check if the column already exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('agents')]
    
    if 'dashboard_token_hash' not in columns:
        op.add_column('agents', sa.Column('dashboard_token_hash', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('agents', 'dashboard_token_hash')
