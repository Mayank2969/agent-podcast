"""Add dashboard_token_issued_at column for token expiry tracking

Revision ID: 007
Revises: 006
Create Date: 2026-03-20
"""
from alembic import op
import sqlalchemy as sa

revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade():
    # Use inspector to check if the column already exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('agents')]
    
    if 'dashboard_token_issued_at' not in columns:
        op.add_column(
            'agents',
            sa.Column(
                'dashboard_token_issued_at',
                sa.DateTime(timezone=True),
                nullable=True,
                comment='Timestamp when dashboard token was issued (for 1-hour expiry)'
            )
        )


def downgrade():
    op.drop_column('agents', 'dashboard_token_issued_at')
