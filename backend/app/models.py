from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Store UTC consistently and restore timezone information for SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ProtocolType(enum.StrEnum):
    openai_compatible = "openai_compatible"
    anthropic = "anthropic"


class PaperDecision(enum.StrEnum):
    unreviewed = "unreviewed"
    relevant = "relevant"
    irrelevant = "irrelevant"
    maybe = "maybe"


class AnalysisStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class ChatMessageStatus(enum.StrEnum):
    streaming = "streaming"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class RunStatus(enum.StrEnum):
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    username: Mapped[str] = mapped_column(String(64))
    username_normalized: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_created", "user_id", "created_at"),
        Index("ix_auth_sessions_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(96))
    created_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())

    user: Mapped[AdminUser] = relationship(back_populates="sessions")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    max_results: Mapped[int] = mapped_column(Integer, default=10)
    lookback_days: Mapped[int] = mapped_column(Integer, default=4)
    analyze_pdf: Mapped[bool] = mapped_column(Boolean, default=False)
    include_cross_list: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    llm_profile: Mapped[LLMProfile | None] = relationship(back_populates="topics")
    paper_links: Mapped[list[TopicPaper]] = relationship(
        back_populates="topic", cascade="all, delete-orphan"
    )


class LLMProfile(Base):
    __tablename__ = "llm_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    provider: Mapped[str] = mapped_column(String(60), default="custom")
    protocol: Mapped[ProtocolType] = mapped_column(String(40))
    base_url: Mapped[str] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(200))
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, default=3000)
    supports_json_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    topics: Mapped[list[Topic]] = relationship(back_populates="llm_profile")
    analyses: Mapped[list[Analysis]] = relationship(back_populates="llm_profile")


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(primary_key=True)
    arxiv_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    primary_category: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    abs_url: Mapped[str] = mapped_column(String(500))
    pdf_url: Mapped[str] = mapped_column(String(500))
    doi: Mapped[str | None] = mapped_column(String(200), nullable=True)
    journal_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    decision: Mapped[PaperDecision] = mapped_column(
        String(30), default=PaperDecision.unreviewed, index=True
    )
    personal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_tags: Mapped[list] = mapped_column(JSON, default=list)

    topic_links: Mapped[list[TopicPaper]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    analyses: Mapped[list[Analysis]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list[PaperChatSession]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )


class TopicPaper(Base):
    __tablename__ = "topic_papers"
    __table_args__ = (
        UniqueConstraint("topic_id", "paper_id", name="uq_topic_paper"),
        Index("ix_topic_papers_discovered", "discovered_at"),
    )

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    topic: Mapped[Topic] = relationship(back_populates="paper_links")
    paper: Mapped[Paper] = relationship(back_populates="topic_links")


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (
        Index("ix_analyses_paper_created", "paper_id", "created_at"),
        Index("ix_analyses_topic_created", "topic_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_logs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(20), default="zh-CN")
    source_mode: Mapped[str] = mapped_column(String(20), default="abstract")
    paper_version: Mapped[int] = mapped_column(Integer, default=1)
    prompt_version: Mapped[str] = mapped_column(String(30), default="v1")
    status: Mapped[AnalysisStatus] = mapped_column(
        String(30), default=AnalysisStatus.pending, index=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    contributions: Mapped[list] = mapped_column(JSON, default=list)
    methodology: Mapped[str | None] = mapped_column(Text, nullable=True)
    experiments: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    reading_advice: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    novelty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rigor_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    paper: Mapped[Paper] = relationship(back_populates="analyses")
    topic: Mapped[Topic | None] = relationship()
    llm_profile: Mapped[LLMProfile | None] = relationship(back_populates="analyses")

    @property
    def model_routing(self) -> dict | None:
        if not isinstance(self.raw_response, dict):
            return None
        routing = self.raw_response.get("_model_routing")
        return routing if isinstance(routing, dict) else None


class PaperChatSession(Base):
    __tablename__ = "paper_chat_sessions"
    __table_args__ = (Index("ix_paper_chat_sessions_paper_updated", "paper_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    preferred_llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    paper: Mapped[Paper] = relationship(back_populates="chat_sessions")
    messages: Mapped[list[PaperChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class PaperChatMessage(Base):
    __tablename__ = "paper_chat_messages"
    __table_args__ = (Index("ix_paper_chat_messages_session_created", "session_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("paper_chat_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ChatMessageStatus] = mapped_column(
        String(30), default=ChatMessageStatus.completed, index=True
    )
    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_routing: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    session: Mapped[PaperChatSession] = relationship(back_populates="messages")


class ScheduleSettings(Base):
    __tablename__ = "schedule_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai")
    hour: Mapped[int] = mapped_column(Integer, default=13)
    minute: Mapped[int] = mapped_column(Integer, default=0)
    digest_language: Mapped[str] = mapped_column(String(20), default="zh-CN")
    public_base_url: Mapped[str] = mapped_column(String(500), default="http://localhost:8000")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class EmailSettings(Base):
    __tablename__ = "email_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_host: Mapped[str] = mapped_column(String(300), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    username: Mapped[str | None] = mapped_column(String(300), nullable=True)
    encrypted_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_email: Mapped[str] = mapped_column(String(300), default="")
    from_name: Mapped[str] = mapped_column(String(200), default="arXiv Research Digest")
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    security: Mapped[str] = mapped_column(String(20), default="starttls")
    subject_prefix: Mapped[str] = mapped_column(String(120), default="[arXiv Daily]")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class RunLog(Base):
    __tablename__ = "run_logs"
    __table_args__ = (
        Index(
            "uq_running_job_type",
            "job_type",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_run_logs_started", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), default="daily")
    trigger: Mapped[str] = mapped_column(String(30), default="scheduled")
    status: Mapped[RunStatus] = mapped_column(String(30), default=RunStatus.running)
    progress_stage: Mapped[str] = mapped_column(String(30), default="starting")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    topics_processed: Mapped[int] = mapped_column(Integer, default=0)
    papers_found: Mapped[int] = mapped_column(Integer, default=0)
    papers_new: Mapped[int] = mapped_column(Integer, default=0)
    analyses_completed: Mapped[int] = mapped_column(Integer, default=0)
    analyses_failed: Mapped[int] = mapped_column(Integer, default=0)
    emails_sent: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
