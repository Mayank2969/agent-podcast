"""Initial schema: agents, interviews, interview_messages, transcripts

Revision ID: 001
Revises:
Create Date: 2026-03-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create agents table
    op.create_table(
        "agents",
        sa.Column("agent_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.UniqueConstraint("public_key", name="uq_agents_public_key"),
    )

    # Create interviews table
    op.create_table(
        "interviews",
        sa.Column(
            "interview_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.String(64),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUEUED"),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_interviews_agent_id", "interviews", ["agent_id"])
    op.create_index("ix_interviews_status", "interviews", ["status"])

    # Create interview_messages table
    op.create_table(
        "interview_messages",
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "interview_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interviews.interview_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence_num", sa.Integer(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("sender IN ('HOST', 'AGENT')", name="chk_sender"),
    )
    op.create_index(
        "ix_interview_messages_interview_id",
        "interview_messages",
        ["interview_id"],
    )
    op.create_index(
        "ix_interview_messages_sequence",
        "interview_messages",
        ["interview_id", "sequence_num"],
    )

    # Create transcripts table
    op.create_table(
        "transcripts",
        sa.Column(
            "transcript_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "interview_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interviews.interview_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "agent_id",
            sa.String(64),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("interview_id", name="uq_transcripts_interview_id"),
    )
    op.create_index("ix_transcripts_agent_id", "transcripts", ["agent_id"])


def downgrade() -> None:
    op.drop_table("transcripts")
    op.drop_table("interview_messages")
    op.drop_table("interviews")
    op.drop_table("agents")
