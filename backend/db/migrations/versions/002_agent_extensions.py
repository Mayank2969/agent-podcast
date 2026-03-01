"""agent_extensions: add callback_url and github_repo_url

Revision ID: 002
Revises: 001
Create Date: 2026-03-02
"""
from alembic import op
import sqlalchemy as sa

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agents', sa.Column('callback_url', sa.Text(), nullable=True))
    op.add_column('interviews', sa.Column('github_repo_url', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('agents', 'callback_url')
    op.drop_column('interviews', 'github_repo_url')
