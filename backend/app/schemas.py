from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import PaperDecision, ProtocolType, RunStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AuthCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AdminUserOut(ORMModel):
    id: int
    username: str
    created_at: datetime
    password_changed_at: datetime


class AuthStatusOut(BaseModel):
    setup_required: bool
    authenticated: bool
    user: AdminUserOut | None = None
    session_expires_at: datetime | None = None


class TopicBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=2)
    description: str | None = None
    relevance_prompt: str | None = None
    enabled: bool = True
    max_results: int = Field(default=10, ge=1, le=100)
    lookback_days: int = Field(default=4, ge=1, le=30)
    analyze_pdf: bool = False
    include_cross_list: bool = False
    llm_profile_id: int | None = None


class TopicCreate(TopicBase):
    pass


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    query: str | None = Field(default=None, min_length=2)
    description: str | None = None
    relevance_prompt: str | None = None
    enabled: bool | None = None
    max_results: int | None = Field(default=None, ge=1, le=100)
    lookback_days: int | None = Field(default=None, ge=1, le=30)
    analyze_pdf: bool | None = None
    include_cross_list: bool | None = None
    llm_profile_id: int | None = None


class TopicOut(ORMModel):
    id: int
    name: str
    query: str
    description: str | None
    relevance_prompt: str | None
    enabled: bool
    max_results: int
    lookback_days: int
    analyze_pdf: bool
    include_cross_list: bool
    llm_profile_id: int | None
    created_at: datetime
    updated_at: datetime


class TopicPreviewRequest(BaseModel):
    query: str = Field(min_length=2)
    max_results: int = Field(default=5, ge=1, le=10)
    lookback_days: int = Field(default=7, ge=1, le=30)
    include_cross_list: bool = False


class TopicQuerySuggestionRequest(BaseModel):
    research_focus: str = Field(min_length=2, max_length=2000)
    categories: list[str] = Field(default_factory=lambda: ["cs.AI"], max_length=8)
    llm_profile_id: int | None = None

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, value: list[str]) -> list[str]:
        allowed = {
            "cs.AI",
            "cs.CL",
            "cs.CV",
            "cs.HC",
            "cs.IR",
            "cs.LG",
            "cs.MA",
            "cs.RO",
            "eess.IV",
            "stat.ML",
        }
        normalized = list(dict.fromkeys(category.strip() for category in value if category.strip()))
        invalid = [category for category in normalized if category not in allowed]
        if invalid:
            raise ValueError(f"Unsupported arXiv categories: {', '.join(invalid)}")
        return normalized


class TopicQuerySuggestionOut(BaseModel):
    name: str
    query: str
    relevance_prompt: str
    keywords: list[str]


class LLMProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(default="custom", min_length=1, max_length=60)
    protocol: ProtocolType = ProtocolType.openai_compatible
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    is_default: bool = False
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=3000, ge=128, le=128_000)
    supports_json_mode: bool = True
    extra_body: dict[str, Any] = Field(default_factory=dict)


class LLMProfileCreate(LLMProfileBase):
    api_key: str | None = Field(default=None, max_length=1000)


class LLMProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    provider: str | None = Field(default=None, min_length=1, max_length=60)
    protocol: ProtocolType | None = None
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    is_default: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=128, le=128_000)
    supports_json_mode: bool | None = None
    extra_body: dict[str, Any] | None = None


class LLMProfileOut(ORMModel):
    id: int
    name: str
    provider: str
    protocol: ProtocolType
    base_url: str
    model: str
    enabled: bool
    is_default: bool
    temperature: float
    max_tokens: int
    supports_json_mode: bool
    extra_body: dict[str, Any]
    has_api_key: bool
    created_at: datetime
    updated_at: datetime


class ModelDiscoveryRequest(BaseModel):
    profile_id: int | None = None
    protocol: ProtocolType = ProtocolType.openai_compatible
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)


class ModelOption(BaseModel):
    id: str
    label: str


class ModelDiscoveryOut(BaseModel):
    models: list[ModelOption]


class ProviderPreset(BaseModel):
    id: str
    label: str
    region: str
    protocol: ProtocolType
    base_url: str
    model_hint: str
    api_key_url: str


class ScheduleSettingsUpdate(BaseModel):
    enabled: bool
    timezone: str = Field(min_length=1, max_length=80)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    digest_language: str = Field(default="zh-CN", min_length=2, max_length=20)
    public_base_url: str = Field(min_length=8, max_length=500)


class ScheduleSettingsOut(ScheduleSettingsUpdate):
    updated_at: datetime


class EmailSettingsUpdate(BaseModel):
    enabled: bool
    smtp_host: str = Field(default="", max_length=300)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=300)
    password: str | None = Field(default=None, max_length=1000)
    from_email: str = Field(default="", max_length=300)
    from_name: str = Field(default="arXiv Research Digest", max_length=200)
    recipients: list[EmailStr] = Field(default_factory=list)
    security: str = Field(default="starttls", pattern="^(starttls|ssl|plain)$")
    subject_prefix: str = Field(default="[arXiv Daily]", max_length=120)

    @field_validator("recipients")
    @classmethod
    def unique_recipients(cls, value: list[EmailStr]) -> list[EmailStr]:
        return list(dict.fromkeys(value))


class EmailSettingsOut(BaseModel):
    enabled: bool
    smtp_host: str
    smtp_port: int
    username: str | None
    from_email: str
    from_name: str
    recipients: list[str]
    security: str
    subject_prefix: str
    has_password: bool
    updated_at: datetime


class PaperUpdate(BaseModel):
    is_read: bool | None = None
    is_starred: bool | None = None
    decision: PaperDecision | None = None
    personal_notes: str | None = Field(default=None, max_length=20_000)
    user_tags: list[str] | None = None


class PaperBulkActionRequest(BaseModel):
    paper_ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal[
        "mark_read",
        "mark_unread",
        "star",
        "unstar",
        "relevant",
        "maybe",
        "irrelevant",
        "unreviewed",
        "delete",
    ]

    @field_validator("paper_ids")
    @classmethod
    def unique_paper_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class AnalysisOut(ORMModel):
    id: int
    topic_id: int | None
    llm_profile_id: int | None
    provider: str
    model: str
    language: str
    source_mode: str
    paper_version: int
    prompt_version: str
    status: str
    summary: str | None
    research_question: str | None
    contributions: list
    methodology: str | None
    experiments: str | None
    limitations: list
    reading_advice: str | None
    relevance_reason: str | None
    keywords: list
    novelty_score: float | None
    rigor_score: float | None
    relevance_score: float | None
    model_routing: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class ReanalyzeRequest(BaseModel):
    topic_id: int | None = None
    llm_profile_id: int | None = None
    source_mode: str | None = Field(default=None, pattern="^(abstract|pdf)$")


class BulkReanalyzeRequest(BaseModel):
    paper_ids: list[int] = Field(min_length=1, max_length=100)
    llm_profile_id: int | None = None
    source_mode: Literal["abstract", "pdf"] | None = None

    @field_validator("paper_ids")
    @classmethod
    def unique_analysis_paper_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class ManualRunRequest(BaseModel):
    topic_ids: list[int] | None = None
    send_email: bool = True


class RunLogOut(ORMModel):
    id: int
    job_type: str
    trigger: str
    status: RunStatus
    progress_stage: str
    progress_current: int
    progress_total: int
    progress_percent: int
    started_at: datetime
    finished_at: datetime | None
    topics_processed: int
    papers_found: int
    papers_new: int
    analyses_completed: int
    analyses_failed: int
    emails_sent: int
    message: str | None
    error_details: dict | None
