"""
SQLAlchemy async ORM models for AgentCast.
All decisions from delta.md C3, C6, D7 applied:
- interviews table has NO questions/answers columns (use interview_messages)
- interview_messages has sequence_num INTEGER NOT NULL
- agents table has NO metadata column (dropped in P0 per D7)
"""
import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    String, Text, Integer, DateTime, ForeignKey,
    CheckConstraint, func, Uuid
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    display_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dashboard_token_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dashboard_token_issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    interviews: Mapped[List["Interview"]] = relationship(back_populates="agent")


class Interview(Base):
    __tablename__ = "interviews"

    interview_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("agents.agent_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="QUEUED",
        # Valid statuses: CREATED, QUEUED, IN_PROGRESS, COMPLETED, FAILED
    )
    topic: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    episode_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[Optional[str]] = mapped_column("metadata", Text, nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="interviews")
    messages: Mapped[List["InterviewMessage"]] = relationship(
        back_populates="interview", order_by="InterviewMessage.sequence_num"
    )
    transcript: Mapped[Optional["Transcript"]] = relationship(back_populates="interview")


class InterviewMessage(Base):
    __tablename__ = "interview_messages"
    __table_args__ = (
        CheckConstraint("sender IN ('HOST', 'AGENT')", name="chk_sender"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    interview_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("interviews.interview_id"), nullable=False
    )
    sender: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_num: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    interview: Mapped["Interview"] = relationship(back_populates="messages")


class Transcript(Base):
    __tablename__ = "transcripts"

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    interview_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("interviews.interview_id"), nullable=False, unique=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("agents.agent_id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)  # JSON stored as text
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    interview: Mapped["Interview"] = relationship(back_populates="transcript")
