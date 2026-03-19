"""interview_episode_path: add episode_path to interviews

Revision ID: 003
Revises: 002
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('interviews', sa.Column('episode_path', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('interviews', 'episode_path')
